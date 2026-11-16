"""CLI for the gcal provider: OAuth linking, account registration, discovery.

Mirrors gmail_importer.ingest's flow (loopback consent → code exchange →
link-time subject verification → HACM store → optional account row), with
CALENDAR-based subject verification: the account's own primary calendar
must appear in calendarList with id == --account (no Gmail scope needed
for the check, RFC-014 §5).

Requires calendar.readonly consent: the scope set comes from
gmail_oauth.GOOGLE_SCOPES (widened per RFC-014 §5), so the consent screen
covers gmail + calendar together — one identity, one re-consent
(RFC-012 §3/§10). Re-linking refreshes the shared google:{subject} HACM
entry in place; revoking it kills gmail + gcal together (documented
footgun).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _default_data_dir() -> Path:
    return Path(os.environ.get("MEMOGARDEN_DATA_DIR",
                               "~/.local/share/memogarden")).expanduser()


def _load_gmail_oauth():
    """Load the gmail-oauth machinery via gmail_importer's dash-name loader
    (reference, don't copy). Calling this seeds sys.modules["gmail_oauth"],
    so a plain `import gmail_oauth` works afterwards (gmail ingest
    precedent) — the loader returns the CLASS, not the module."""
    from gmail_importer.provider import GmailImporter

    return GmailImporter._load_oauth_client()


def _read_passphrase(args) -> str | None:
    path = args.passphrase_file or (_default_data_dir() / "hacm/passphrase")
    p = Path(path).expanduser()
    return p.read_text().strip() if p.exists() else None


# ----------------------------------------------------------------------
# auth-login
# ----------------------------------------------------------------------

def cmd_auth_login(args) -> int:
    """Loopback OAuth consent → verify via calendarList → HACM → --register."""
    if not args.account:
        sys.exit("--account is required for auth-login")
    oauth_cls = _load_gmail_oauth()
    import gmail_oauth  # seeded by the loader (sys.modules)
    hacm_path = args.hacm or (_default_data_dir() / "hacm/credentials.enc")
    passphrase = _read_passphrase(args)

    client = oauth_cls(str(Path(hacm_path).expanduser()),
                       passphrase=passphrase)

    from gmail_importer.ingest import _auth_url

    registered = "http://localhost:8080/callback"  # as registered in Google Cloud Console
    url, state = _auth_url(client, registered)
    print("\nVisit this URL in a browser to authorize (gmail + calendar scopes):")
    print(f"\n  {url}\n")

    server = gmail_oauth.SimpleCallbackServer(port=8080)
    print(f"Waiting for callback on {registered} ({args.timeout}s timeout)...")
    result = server.wait_for_callback(timeout=args.timeout)

    if result.get("state") != state:
        raise SystemExit(
            "OAuth state mismatch — the callback did not match the authorization "
            "request (possible CSRF). Aborting without storing anything."
        )

    tokens = client.exchange_code_for_tokens(result["code"], redirect_uri=registered)

    # RFC-012 INV-3 (calendar edition): the credential must belong to the
    # account it will be stored under. Nothing is persisted until this
    # passes — a typo'd --account can never poison sync state.
    from gcal_importer.calendar_api import GoogleCalendarClient

    entries = GoogleCalendarClient(tokens["access_token"]).calendar_list()
    verified = next(
        (e["id"] for e in entries if e.get("id", "").strip().lower() == args.account.strip().lower()
         and e.get("primary")),
        None,
    )
    if verified is None:
        raise SystemExit(
            "OAuth verification failed: the credential's calendarList has no "
            f"primary calendar for '{args.account}'. Nothing was stored — "
            "re-run with the correct --account."
        )

    client.store_tokens(verified, tokens)
    print(f"\nOAuth complete. Refresh token stored for {verified}.")
    print(f"Scopes: {tokens.get('scope', '')}")
    print("WARNING: this token is shared by the gmail provider (google:"
          f"{verified}); revoking it disconnects both.")

    if args.register:
        _register_account_row(verified, args)
    return 0


def _register_account_row(subject: str, args) -> None:
    """POST the provider_accounts row for the verified subject (RFC-012 §6.3).

    The API receives the credential REFERENCE only — the token itself
    stays in the local HACM store (INV-2). 409 = re-link (the freshly
    consented token refreshed the HACM entry in place).
    """
    import requests

    api_key = args.api_key or os.environ.get("MEMOGARDEN_API_KEY")
    if not api_key:
        sys.exit("--register needs an API key: pass --api-key or export MEMOGARDEN_API_KEY")
    response = requests.post(
        f"{args.api_url.rstrip('/')}/api/v1/providers/gcal/accounts",
        json={
            "platform": "google",
            "subject": subject,
            "label": args.label,
            "credential_ref": f"google:{subject}",
        },
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    if response.status_code == 409:
        print(f"Account already registered (google:{subject}) — token refreshed in place.")
        return
    if response.status_code != 201:
        sys.exit(
            f"Account registration failed ({response.status_code}): "
            f"{response.text[:300]}"
        )
    account = response.json()
    print(f"Account registered: gcal/{account['id']} ({account['subject']}).")
    print("Its first sync seeds full calendar history through the hourly heartbeat.")


# ----------------------------------------------------------------------
# list-calendars
# ----------------------------------------------------------------------

def cmd_list_calendars(args) -> int:
    """Read-only probe: what would this account ingest under the current
    selection rules? (owner-role default; calendar_ids allowlist overrides)"""
    oauth_cls = _load_gmail_oauth()
    hacm_path = args.hacm or (_default_data_dir() / "hacm/credentials.enc")
    passphrase = _read_passphrase(args)
    client = oauth_cls(str(Path(hacm_path).expanduser()),
                       passphrase=passphrase)
    tokens = client.refresh_access_token(args.account)

    from gcal_importer.calendar_api import GoogleCalendarClient

    entries = GoogleCalendarClient(tokens["access_token"]).calendar_list()
    allow = {c.strip() for c in (args.calendar_ids or "").split(",") if c.strip()}
    for entry in sorted(entries, key=lambda e: (e.get("accessRole") != "owner", e.get("id", ""))):
        selected = (
            entry.get("id") in allow if allow else entry.get("accessRole") == "owner"
        )
        marker = "*" if selected else " "
        print(
            f"{marker} {entry.get('accessRole', '?'):>14}  {entry.get('id', '?')}"
            f"  ({entry.get('summary', '')})"
        )
    print("\n(*) would ingest under the current rules; allowlist via the"
          " provider/account config key `calendar_ids`.")
    return 0


# ----------------------------------------------------------------------
# argparse
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gcal_importer.ingest",
        description="gcal provider CLI (RFC-014): auth, registration, discovery",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hacm", help="HACM store path (default under MEMOGARDEN_DATA_DIR)")
    common.add_argument("--passphrase-file", dest="passphrase_file",
                        help="HACM passphrase file (default under MEMOGARDEN_DATA_DIR)")

    login = sub.add_parser("auth-login", parents=[common],
                           help="OAuth consent → verify → HACM → optional register")
    login.add_argument("--account", required=True, help="Google account (email)")
    login.add_argument("--register", action="store_true",
                       help="POST the provider_accounts row after verification")
    login.add_argument("--label", default=None, help="Account label for the registry")
    login.add_argument("--api-url", default=os.environ.get("MEMOGARDEN_API_URL",
                                                           "http://127.0.0.1:8600"))
    login.add_argument("--api-key", default=None, help="API key (else MEMOGARDEN_API_KEY)")
    login.add_argument("--timeout", type=int, default=300,
                       help="Callback wait timeout in seconds")
    login.set_defaults(func=cmd_auth_login)

    calendars = sub.add_parser("list-calendars", parents=[common],
                               help="List calendars and what would ingest")
    calendars.add_argument("--account", required=True, help="Google account (email)")
    calendars.add_argument("--calendar-ids", dest="calendar_ids", default="",
                           help="Comma-separated allowlist to preview")
    calendars.set_defaults(func=cmd_list_calendars)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
