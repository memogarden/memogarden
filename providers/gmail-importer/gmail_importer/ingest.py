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
import re
import secrets
import sys
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local/share/memogarden"
REPO_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail_importer.ingest",
        description="Ingest Gmail into Soil (attachment metadata only).",
    )
    parser.add_argument("--account", required=True, help="Gmail address (HACM lookup key)")
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
    """Create HACM store (if needed) and seed google:oauth-client credentials."""
    from datetime import datetime, timezone

    from hacm import CredentialStore
    from hacm.core import Credential

    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase_file = args.passphrase_file or (hacm_path.parent / "passphrase")

    if not passphrase_file.exists():
        hacm_path.parent.mkdir(parents=True, exist_ok=True)
        passphrase = secrets.token_urlsafe(24)
        passphrase_file.write_text(passphrase + "\n")
        passphrase_file.chmod(0o600)
        print(f"Generated HACM passphrase: {passphrase_file}")
    else:
        print(f"Using existing HACM passphrase: {passphrase_file}")

    # Client credentials live in gmail-oauth/store_client.py (extracted by
    # regex so the secret is not duplicated into this file).
    store_client_src = (REPO_ROOT / "gmail-oauth" / "store_client.py").read_text()
    client_id_match = re.search(r'CLIENT_ID\s*=\s*"([^"]+)"', store_client_src)
    client_secret_match = re.search(r'CLIENT_SECRET\s*=\s*"([^"]+)"', store_client_src)
    if not (client_id_match and client_secret_match):
        sys.exit("Could not extract CLIENT_ID/CLIENT_SECRET from gmail-oauth/store_client.py")

    redirect_uris = re.search(r'"redirect_uris":\s*\[([^\]]*)\]', store_client_src)
    uris = re.findall(r'"([^"]+)"', redirect_uris.group(1)) if redirect_uris else [
        "http://localhost:8080/callback"
    ]

    material = base64.b64encode(
        json.dumps(
            {
                "client_id": client_id_match.group(1),
                "client_secret": client_secret_match.group(1),
            }
        ).encode()
    ).decode()

    now = datetime.now(timezone.utc)
    cred = Credential(
        id="google:oauth-client",
        provider="google",
        account_id="gmail-scraper-app",
        type="oauth_client",
        material=material,
        metadata={
            "redirect_uris": uris,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        created_at=now,
        updated_at=now,
    )
    store = CredentialStore(str(hacm_path), passphrase=passphrase_file.read_text().strip())
    store.put("google:oauth-client", cred)
    print(f"OAuth client credentials stored: {hacm_path}")
    print(f"  Client ID: {client_id_match.group(1)}")
    print(f"  Redirect URIs: {uris}")
    return 0


def cmd_auth_login(args) -> int:
    """Run the loopback OAuth consent flow and store the refresh token."""
    gmail_oauth = _load_gmail_oauth()
    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase = _read_passphrase(args)

    client = gmail_oauth.GmailOAuthClient(str(hacm_path), passphrase=passphrase)

    registered = "http://localhost:8080/callback"  # as registered in Google Cloud Console
    url = _auth_url(client, registered)
    print("\nVisit this URL in a browser to authorize:")
    print(f"\n  {url}\n")

    server = gmail_oauth.SimpleCallbackServer(port=8080)
    print(f"Waiting for callback on {registered} ({args.timeout}s timeout)...")
    result = server.wait_for_callback(timeout=args.timeout)

    tokens = client.exchange_code_for_tokens(
        result["code"], redirect_uri=registered, state=result.get("state")
    )
    client.store_tokens(args.account, tokens)
    print(f"\nOAuth complete. Refresh token stored for {args.account}.")
    print(f"Scopes: {tokens.get('scope', '')}")
    return 0


def _auth_url(client, redirect_uri: str) -> str:
    """Build the authorization URL with an explicit redirect URI."""
    import urllib.parse

    from gmail_oauth import GOOGLE_AUTH_URI, GOOGLE_SCOPES

    client_id, _ = client.get_client_credentials()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(GOOGLE_SCOPES),
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "state": client._generate_state(),
    }
    return f"{GOOGLE_AUTH_URI}?{urllib.parse.urlencode(params)}"


def cmd_oldest(args) -> int:
    from system.soil import Soil

    from gmail_importer.importer import GmailImporter

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    hacm_path = args.hacm or (args.data_dir / "hacm/credentials.enc")
    passphrase_file = args.passphrase_file or (hacm_path.parent / "passphrase")
    passphrase = (
        passphrase_file.read_text().strip() if passphrase_file.exists() else None
    )

    soil = Soil(args.data_dir / "soil.db")
    with soil:
        # Context covers construction: the base class builds a dedup index.
        # import_oldest re-enters the context itself.
        soil.init_schema()  # idempotent; mirrors initialize_system in system.core
        importer = GmailImporter(
            soil=soil,
            account_id=args.account,
            hacm_path=str(hacm_path),
            passphrase=passphrase,
        )
    stats = importer.import_oldest(limit=args.limit, verbose=not args.quiet)
    return 0 if stats.get("errors", 0) == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.add_argument(
        "command",
        nargs="?",
        default="oldest",
        choices=["oldest", "auth-setup", "auth-login"],
        help="What to do (default: oldest)",
    )
    args = parser.parse_args(argv)
    return {"auth-setup": cmd_auth_setup, "auth-login": cmd_auth_login, "oldest": cmd_oldest}[
        args.command
    ](args)


if __name__ == "__main__":
    sys.exit(main())
