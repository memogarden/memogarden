"""Gmail Email Importer for MemoGarden Soil.

Fetches messages from the Gmail API and translates them to the same
provider-agnostic Email item shape as the mbox importer (strict RFC 5322
data / provider metadata separation), reusing the mbox email_parser
helpers so both importers stay consistent.

Attachments are captured as metadata only (has_attachments, count,
filenames) — no binaries — matching the decision in
plan/email-implementation-plan.md (Q2).
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Iterator, Optional

from system.soil import Soil
from mbox_importer.import_mbox import EmailImporter
from mbox_importer.email_parser import (
    decode_header_value,
    normalize_message_id,
    parse_addresses,
    parse_references,
    strip_angle_brackets,
    validate_email_data,
)

from gmail_importer.gmail_api import (
    GmailApiClient,
    GmailApiError,
    find_oldest_message_ids,
)

logger = logging.getLogger(__name__)

_MAX_METADATA_HTML_BYTES = 10000  # same truncation as mbox importer


class GmailImporter(EmailImporter):
    """Import emails from the Gmail API.

    Uses HACM for OAuth credentials: a refresh token stored under
    `google:{account_id}` and client credentials under `google:oauth-client`.
    """

    def __init__(
        self,
        soil: Soil,
        account_id: str,
        hacm_path: str,
        passphrase: Optional[str] = None,
        batch_size: int = 50,
    ):
        super().__init__(soil, batch_size)
        self.account_id = account_id
        self.hacm_path = hacm_path
        self.passphrase = passphrase
        self._limit: Optional[int] = None

    # ------------------------------------------------------------------
    # OAuth plumbing
    # ------------------------------------------------------------------

    @staticmethod
    def _load_oauth_client():
        """Load GmailOAuthClient from the gmail-oauth source directory.

        The gmail-oauth directory ships as `gmail-oauth/` (dash, not a valid
        package name), so it is loaded by path rather than installed.
        """
        import importlib.util
        import sys
        from pathlib import Path

        try:
            import gmail_oauth  # type: ignore

            return gmail_oauth.GmailOAuthClient
        except ImportError:
            pass

        oauth_dir = Path(__file__).resolve().parents[3] / "gmail-oauth"
        init_py = oauth_dir / "__init__.py"
        if not init_py.exists():
            raise ImportError(
                f"gmail-oauth source not found at {oauth_dir}; expected it "
                "next to providers/ in the repository root"
            )
        spec = importlib.util.spec_from_file_location(
            "gmail_oauth", init_py, submodule_search_locations=[str(oauth_dir)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["gmail_oauth"] = module
        spec.loader.exec_module(module)
        return module.GmailOAuthClient

    def _build_api_client(self) -> GmailApiClient:
        """Refresh the access token via HACM-stored credentials."""
        oauth_cls = self._load_oauth_client()
        oauth = oauth_cls(self.hacm_path, passphrase=self.passphrase)
        tokens = oauth.refresh_access_token(self.account_id)
        return GmailApiClient(tokens["access_token"])

    # ------------------------------------------------------------------
    # Fetch + translate
    # ------------------------------------------------------------------

    def fetch_emails(
        self, since: Optional[datetime] = None, verbose: bool = True
    ) -> Iterator[dict]:
        """Fetch the oldest emails from Gmail.

        Args:
            since: Unused for the oldest-N strategy (kept for the
                EmailImporter interface; date-range sync lands with the
                background scheduler work).
            verbose: Print search progress.

        Yields:
            Email dicts in the same shape as mbox_message_to_email_item.
        """
        api = self._build_api_client()
        # The base pipeline calls fetch_emails() with no args, so --quiet
        # flows through instance state set by import_oldest().
        verbose = getattr(self, "_verbose", verbose)
        ids = find_oldest_message_ids(
            api, target=self._limit or 10, verbose=verbose
        )
        # API returns newest-first; the oldest are at the tail.
        oldest_ids = list(reversed(ids))
        if self._limit:
            oldest_ids = oldest_ids[: self._limit]

        realized_at = datetime.now(timezone.utc).isoformat()
        for message_id in oldest_ids:
            try:
                message = api.get_message(message_id)
            except GmailApiError as e:
                self.stats["errors"] += 1
                logger.warning("Failed to fetch message %s: %s", message_id, e)
                continue
            email_dict = self.translate_gmail_to_email(
                message, realized_at=realized_at
            )
            if email_dict:
                yield email_dict
            else:
                self.stats["skipped"] += 1

    def import_oldest(self, limit: int = 10, verbose: bool = True) -> dict:
        """Import the `limit` oldest messages (dedup + threading via base class).

        Re-running with the same limit after a successful import is a no-op
        (the same oldest messages deduplicate); growth requires raising limit.
        """
        self._limit = limit
        self._verbose = verbose
        try:
            return self.import_all(verbose=verbose)
        finally:
            self._limit = None

    def translate_gmail_to_email(
        self, gmail_msg: dict, realized_at: Optional[str] = None
    ) -> Optional[dict]:
        """Translate a Gmail API message resource to an Email item dict.

        Mirrors mbox_message_to_email_item: RFC 5322 fields into data,
        Gmail-specific fields into metadata, attachments as metadata only.
        Returns None for messages that cannot be deduplicated (no
        Message-ID), matching the mbox importer.
        """
        if realized_at is None:
            realized_at = datetime.now(timezone.utc).isoformat()

        headers: dict[str, str] = {}
        for header in gmail_msg.get("payload", {}).get("headers", []):
            name = header["name"].lower()
            if name not in headers:
                headers[name] = header["value"]

        raw_message_id = headers.get("message-id", "")
        if not raw_message_id:
            return None  # cannot deduplicate; same rule as mbox path
        normalized_message_id = normalize_message_id(raw_message_id)

        sent_at = self._internal_date_to_iso(gmail_msg.get("internalDate"))
        if not sent_at:
            from mbox_importer.email_parser import parse_date

            sent_at = parse_date(headers.get("date", "")) or realized_at

        plain_text, html_body, attachment_filenames = self._extract_bodies(
            gmail_msg.get("payload", {})
        )

        if plain_text:
            body = plain_text
            content_type = "text/plain"
        elif html_body:
            body = html_body
            content_type = "text/html"
        else:
            body = ""
            content_type = "text/plain"

        references = parse_references(headers["references"]) if headers.get("references") else None
        in_reply_to = (
            strip_angle_brackets(headers["in-reply-to"])
            if headers.get("in-reply-to")
            else None
        )

        metadata = {
            "provider": "google",
            "source": "gmail_api",
            "gmail_message_id": gmail_msg.get("id"),
        }
        if gmail_msg.get("threadId"):
            metadata["gmail_thread_id"] = gmail_msg["threadId"]
        if gmail_msg.get("labelIds"):
            metadata["gmail_labels"] = gmail_msg["labelIds"]
        if html_body and content_type == "text/plain":
            # Store HTML in metadata when plain text is the primary body
            metadata["html_body"] = html_body[:_MAX_METADATA_HTML_BYTES]

        data = {
            "rfc_message_id": normalized_message_id,
            "from_address": decode_header_value(headers.get("from", "")),
            "to_addresses": parse_addresses(headers.get("to", "")),
            "cc_addresses": parse_addresses(headers["cc"]) if headers.get("cc") else None,
            "bcc_addresses": parse_addresses(headers["bcc"]) if headers.get("bcc") else None,
            "sent_at": sent_at,
            "received_at": None,
            "references": references,
            "in_reply_to": in_reply_to,
            "title": decode_header_value(headers.get("subject", "")),
            "description": body,
            "content_type": content_type,
            "has_attachments": bool(attachment_filenames),
            "attachment_count": len(attachment_filenames),
            "attachment_filenames": attachment_filenames,
        }

        try:
            validate_email_data(data)
        except ValueError as e:
            logger.warning("Skipping invalid Gmail message %s: %s", gmail_msg.get("id"), e)
            return None

        return {
            "_type": "Email",
            "realized_at": realized_at,
            "canonical_at": sent_at,
            "fidelity": "full",
            "data": data,
            "metadata": metadata,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _internal_date_to_iso(internal_date: Optional[str]) -> Optional[str]:
        """Gmail internalDate (ms since epoch, string) -> ISO 8601 UTC."""
        if not internal_date:
            return None
        try:
            seconds = int(internal_date) / 1000
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()

    @staticmethod
    def _walk_parts(payload: dict) -> Iterator[dict]:
        """Depth-first walk of a MIME payload tree."""
        yield payload
        for part in payload.get("parts", []) or []:
            yield from GmailImporter._walk_parts(part)

    @staticmethod
    def _part_headers(part: dict) -> dict[str, str]:
        return {
            h["name"].lower(): h["value"] for h in part.get("headers", [])
        }

    def _extract_bodies(self, payload: dict) -> tuple[Optional[str], Optional[str], list[str]]:
        """Extract (plain_text, html_body, attachment_filenames).

        Attachment = part with a filename AND Content-Disposition: attachment
        (same rule as the mbox importer's parser; note the Gmail path counts
        only NAMED attachments, and inline files are never attachments).
        Bodies are base64url decoded using the part's declared charset —
        the oldest mail is the most likely to use legacy charsets, and Soil
        facts are immutable, so a wrong decode would persist forever.
        """
        plain_text: Optional[str] = None
        html_body: Optional[str] = None
        attachment_filenames: list[str] = []

        for part in self._walk_parts(payload):
            part_headers = self._part_headers(part)
            disposition = part_headers.get("content-disposition", "")
            filename = part.get("filename") or ""

            if filename and "attachment" in disposition.lower():
                attachment_filenames.append(decode_header_value(filename))
                continue
            if filename:
                # Inline files (Content-Disposition: inline) are not
                # attachments per the mbox importer's definition.
                continue

            mime_type = part.get("mimeType", "")
            if mime_type not in ("text/plain", "text/html"):
                continue
            body_data = part.get("body", {}).get("data")
            if not body_data:
                continue
            text = self._decode_body(body_data, part_headers.get("content-type", ""))
            if text is None:
                continue
            if mime_type == "text/plain" and plain_text is None:
                plain_text = text
            elif mime_type == "text/html" and html_body is None:
                html_body = text

        return plain_text, html_body, attachment_filenames

    @staticmethod
    def _decode_body(body_data: str, content_type_header: str) -> Optional[str]:
        """Decode a base64url body using its declared charset, utf-8 fallback."""
        import re as _re

        try:
            raw = base64.urlsafe_b64decode(body_data + "=" * (-len(body_data) % 4))
        except Exception:
            return None
        charset_match = _re.search(
            r'charset=["\']?([A-Za-z0-9_\-.]+)', content_type_header
        )
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")
