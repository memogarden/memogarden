"""CLI: ingest the N oldest Gmail messages into MemoGarden Soil.

Subcommands:
    oldest        Ingest the N oldest messages (default).
    auth-setup    Create the HACM store and seed Google OAuth client credentials.
    auth-login    Run the OAuth consent flow and store the refresh token.

Requires (after auth-setup + auth-login):
    - HACM store with client credentials under `google:oauth-client`
      and a refresh token under `google:{account}`.
    - A passphrase file next to the HACM store if the store is
      passphrase-protected.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import secrets
import sys
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local/share/memogarden"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail_importer.ingest",
        description="Ingest Gmail into Soil (attachment metadata only).",
    )
    parser.add_argument("--account", default=None, help="Gmail address (HACM lookup key; required for oldest and auth-login)")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"MemoGarden data dir (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--hacm",
        type=Path,
        default=None,
        help="HACM store path (default: <data-dir>/hacm/credentials.enc)",
    )
    parser.add_argument(
        "--passphrase-file",
        type=Path,
        default=None,
        help="File containing the HACM passphrase (default: <hacm dir>/passphrase)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of oldest messages (default: 10)")
    parser.add_argument("--timeout", type=int, default=3600, help="auth-login callback wait seconds (default: 3600)")
    parser.add_argument(
        "--client-secrets",
        default=None,
        metavar="FILE",
        help="auth-setup: JSON file with client_id/client_secret (flat or GCP download format)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="auth-setup: overwrite existing client credentials (prompt for values)",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="auth-login: also create the provider account row via the API (RFC-012); the token itself never leaves this machine",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="auth-login --register: operator nickname for the account (e.g. 'work')",
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8600",
        help="auth-login --register: MemoGarden API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="auth-login --register: API key (default: $MEMOGARDEN_API_KEY)",
    )
    parser.add_argument("--dry-run", action="store_true", help="incremental: report what would be ingested, store nothing")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    return parser


def _load_gmail_oauth():
    """Load the gmail_oauth module from the repo's gmail-oauth directory."""
    import sys

    from gmail_importer.importer import GmailImporter

    GmailImporter._load_oauth_client()  # ensures sys.modules["gmail_oauth"]
    return sys.modules["gmail_oauth"]


def _read_passphrase(args) -> str:
    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase_file = args.passphrase_file or (hacm_path.parent / "passphrase")
    if not passphrase_file.exists():
        sys.exit(f"Passphrase file not found: {passphrase_file}")
    return passphrase_file.read_text().strip()


def cmd_auth_setup(args) -> int:
    """Create HACM store (if needed) and seed google:oauth-client credentials.

    Client credentials are NEVER taken from the repository: they come from
    --client-secrets FILE (flat {"client_id", "client_secret"} JSON or a
    Google Cloud Console download) or an interactive prompt, and are stored
    only in the encrypted HACM store. This is also the rotation path — run
    it again with a new secret after resetting it in Google Cloud Console.
    """
    from datetime import datetime, timezone
    from getpass import getpass

    from hacm import CredentialStore
    from hacm.core import Credential

    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase_file = args.passphrase_file or (hacm_path.parent / "passphrase")

    if not passphrase_file.exists():
        import os

        hacm_path.parent.mkdir(parents=True, exist_ok=True)
        passphrase = secrets.token_urlsafe(24)
        fd = os.open(passphrase_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(passphrase + "\n")
        print(f"Generated HACM passphrase: {passphrase_file}")
    else:
        print(f"Using existing HACM passphrase: {passphrase_file}")

    store = CredentialStore(str(hacm_path), passphrase=passphrase_file.read_text().strip())

    existing = store.get("google:oauth-client")
    if existing is not None and not args.client_secrets and not args.force:
        print("OAuth client credentials already present in HACM (google:oauth-client).")
        print("Nothing to do. To replace them (e.g. after rotating the secret in")
        print("Google Cloud Console), pass --client-secrets FILE or --force to prompt.")
        return 0

    if args.client_secrets:
        try:
            doc = json.loads(Path(args.client_secrets).read_text())
            # Accept either a flat JSON or the GCP Console download shape.
            blob = doc.get("installed") or doc.get("web") or doc
            client_id = blob["client_id"]
            client_secret = blob["client_secret"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            sys.exit(f"Could not read client credentials from {args.client_secrets}: {e}")
    else:
        client_id = input("OAuth client ID: ").strip()
        client_secret = getpass("OAuth client secret: ")

    material = base64.b64encode(
        json.dumps({"client_id": client_id, "client_secret": client_secret}).encode()
    ).decode()

    now = datetime.now(timezone.utc)
    cred = Credential(
        id="google:oauth-client",
        provider="google",
        account_id="gmail-scraper-app",
        type="oauth_client",
        material=material,
        metadata={
            "redirect_uris": ["http://localhost:8080/callback"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        created_at=now,
        updated_at=now,
    )
    store.put("google:oauth-client", cred)
    print(f"OAuth client credentials stored: {hacm_path}")
    print(f"  Client ID: {client_id}")
    print("  Secret is stored only in the encrypted HACM store.")
    if args.client_secrets:
        print(f"  NOTE: {args.client_secrets} still contains the secret — delete it.")
    return 0


def cmd_auth_login(args) -> int:
    """Run the loopback OAuth consent flow, verify the mailbox identity,
    store the refresh token, and (with --register) create the account row."""
    if not args.account:
        sys.exit("--account is required for auth-login")
    gmail_oauth = _load_gmail_oauth()
    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase = _read_passphrase(args)

    client = gmail_oauth.GmailOAuthClient(str(hacm_path), passphrase=passphrase)

    registered = "http://localhost:8080/callback"  # as registered in Google Cloud Console
    url, state = _auth_url(client, registered)
    print("\nVisit this URL in a browser to authorize:")
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

    # RFC-012 INV-3: link-time subject verification. The token must belong
    # to the mailbox it will be stored under — nothing is persisted until
    # this passes, so a typo'd --account can never poison sync state.
    from gmail_importer.gmail_api import GmailApiClient

    profile = GmailApiClient(tokens["access_token"]).get_profile()
    verified = (profile.get("emailAddress") or "").strip().lower()
    if verified != args.account.strip().lower():
        raise SystemExit(
            f"OAuth verification failed: the credential authenticated as "
            f"'{verified or '<unknown>'}', not '{args.account}'. Nothing was "
            "stored — re-run with the correct --account."
        )

    client.store_tokens(verified, tokens)
    print(f"\nOAuth complete. Refresh token stored for {verified}.")
    print(f"Scopes: {tokens.get('scope', '')}")

    if args.register:
        _register_account_row(verified, args)
    return 0


def _register_account_row(subject: str, args) -> None:
    """POST the provider_accounts row for the verified subject (RFC-012 §6.3).

    The API receives the credential REFERENCE only — the token itself stays
    in the local HACM store (INV-2: tokens never transit the API). A 409
    means the account row already exists; the freshly consented token
    refreshed the HACM entry in place, which is exactly a re-link.
    """
    import os

    import requests

    api_key = args.api_key or os.environ.get("MEMOGARDEN_API_KEY")
    if not api_key:
        sys.exit(
            "--register needs an API key: pass --api-key or export MEMOGARDEN_API_KEY"
        )
    response = requests.post(
        f"{args.api_url.rstrip('/')}/api/v1/providers/gmail/accounts",
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
    print(f"Account registered: gmail/{account['id']} ({account['subject']}).")
    print("Its first sync ingests the full mailbox through the hourly heartbeat; a fresh")
    print("account also runs immediately if you trigger it (POST .../accounts/<id>/run).")


def _auth_url(client, redirect_uri: str) -> tuple[str, str]:
    """Build the authorization URL; returns (url, state) for callback validation."""
    import urllib.parse

    from gmail_oauth import GOOGLE_AUTH_URI, GOOGLE_SCOPES

    client_id, _ = client.get_client_credentials()
    state = client._generate_state()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(GOOGLE_SCOPES),
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URI}?{urllib.parse.urlencode(params)}", state


def _make_importer(args, sync_cache=False):
    """Shared construction for the ingestion commands."""
    from system.soil import Soil

    from gmail_importer.importer import GmailImporter

    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase_file = args.passphrase_file or (hacm_path.parent / "passphrase")
    passphrase = (
        passphrase_file.read_text().strip() if passphrase_file.exists() else None
    )

    soil = Soil(args.data_dir / "soil.db")
    with soil:
        # Context covers construction: the base class builds a dedup index.
        soil.init_schema()  # idempotent; mirrors initialize_system in system.core
        importer = GmailImporter(
            soil=soil,
            account_id=args.account,
            hacm_path=str(hacm_path),
            passphrase=passphrase,
            search_cache_path=(
                # RFC-012 §4.6: per-subject cache file
                str(args.data_dir / f"gmail-search-cache--{args.account}.json")
                if sync_cache
                else None
            ),
            excluded_ids_path=str(args.data_dir / "gmail-excluded-ids.json"),
        )
    return importer, hacm_path, passphrase_file, passphrase


def cmd_oldest(args) -> int:
    if not args.account:
        sys.exit("--account is required for oldest")
    from system.soil import Soil

    from gmail_importer.gmail_api import GmailApiError
    from gmail_importer.importer import GmailImporter

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    importer, hacm_path, passphrase_file, passphrase = _make_importer(
        args, sync_cache=args.progressive
    )
    try:
        if args.progressive:
            stats = importer.import_next(limit=args.limit, verbose=not args.quiet)
        else:
            stats = importer.import_oldest(limit=args.limit, verbose=not args.quiet)
    except GmailApiError as e:
        print(f"Ingestion failed: {e}", file=sys.stderr)
        return 1
    return 0 if stats.get("errors", 0) == 0 else 1


def cmd_next(args) -> int:
    args.progressive = True
    return cmd_oldest(args)


def cmd_incremental(args) -> int:
    """historyId sync of NEW mail since the stored historyId (seeds on first run)."""
    if not args.account:
        sys.exit("--account is required for incremental")
    from system.soil import Soil

    from gmail_importer.gmail_api import GmailApiError
    from gmail_importer.importer import GmailImporter

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase_file = args.passphrase_file or (hacm_path.parent / "passphrase")
    passphrase = (
        passphrase_file.read_text().strip() if passphrase_file.exists() else None
    )

    soil = Soil(args.data_dir / "soil.db")
    with soil:
        soil.init_schema()
        importer = GmailImporter(
            soil=soil,
            account_id=args.account,
            hacm_path=str(hacm_path),
            passphrase=passphrase,
            sync_state_path=str(args.data_dir / "gmail-sync-state.json"),
            excluded_ids_path=str(args.data_dir / "gmail-excluded-ids.json"),
        )
    try:
        stats = importer.import_incremental(limit=args.limit, verbose=not args.quiet)
    except GmailApiError as e:
        print(f"Incremental sync failed: {e}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            f"Incremental: processed {stats.get('processed', 0)}, "
            f"imported {stats.get('imported', 0)}, skipped {stats.get('skipped', 0)}, "
            f"errors {stats.get('errors', 0)}"
        )
    return 0 if stats.get("errors", 0) == 0 else 1


def cmd_verify(args) -> int:
    """Re-hash attachment files against their facts; report missing/mutated/orphaned.

    Local-only: no Gmail account or network access needed.
    """
    from system.soil import Soil

    from gmail_importer.verify import verify_attachments

    soil = Soil(args.data_dir / "soil.db")
    report = verify_attachments(soil, args.data_dir / "attachments")
    print(report.summary())
    return 0 if report.problems == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.add_argument(
        "--progressive",
        action="store_true",
        help="oldest: use progressive cursor mode (same as the next command)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="oldest",
        choices=["oldest", "next", "incremental", "verify", "auth-setup", "auth-login"],
        help=(
            "What to do (default: oldest). oldest = fixed oldest-N window; "
            "next = progressive (ingest the next N oldest not yet in Soil); "
            "incremental = historyId sync of NEW mail since the stored "
            "historyId; verify = re-hash stored attachment files against "
            "the facts (mutation detection). NB: attachment binaries are "
            "extracted by the provider's sync (RFC-012 §4.9, 2026-11-20) — "
            "the standalone attachments pass was retired"
        ),
    )
    args = parser.parse_args(argv)
    if args.command == "next":
        args.progressive = True
        args.command = "oldest"
    return {
        "auth-setup": cmd_auth_setup,
        "auth-login": cmd_auth_login,
        "oldest": cmd_oldest,
        "next": cmd_next,
        "incremental": cmd_incremental,
        "verify": cmd_verify,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
