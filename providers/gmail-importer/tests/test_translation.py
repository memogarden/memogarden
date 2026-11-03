"""Tests for Gmail API message -> Email item translation.

Fixtures mirror the Gmail API v1 message resource shape (format=full).
Shared helpers/fixtures live in conftest.py.
"""

import base64

from conftest import b64url, simple_message


class TestTranslation:
    def test_basic_field_mapping(self, importer):
        result = importer.translate_gmail_to_email(simple_message())
        assert result is not None
        assert result["_type"] == "Email"
        assert result["fidelity"] == "full"
        data = result["data"]
        assert data["rfc_message_id"] == "<msg-1@example.com>"
        assert data["from_address"] == "Alice <alice@example.com>"
        assert data["to_addresses"] == ["bob@example.com", "carol@example.com"]
        assert data["cc_addresses"] == ["dora@example.com"]
        assert data["title"] == "Hello world"
        assert data["description"] == "Plain body here"
        assert data["content_type"] == "text/plain"
        assert data["in_reply_to"] == "msg-0@example.com"
        assert data["references"] == ["msg-0@example.com", "msg-0a@example.com"]

    def test_sent_at_from_internal_date(self, importer):
        from datetime import datetime, timezone

        result = importer.translate_gmail_to_email(simple_message())
        expected = datetime.fromtimestamp(1077930003, tz=timezone.utc).isoformat()
        assert result["canonical_at"] == expected
        assert result["data"]["sent_at"] == expected

    def test_attachments_metadata_only(self, importer):
        result = importer.translate_gmail_to_email(simple_message())
        data = result["data"]
        assert data["has_attachments"] is True
        assert data["attachment_count"] == 1  # inline image excluded
        assert data["attachment_filenames"] == ["invoice.pdf"]
        # no binary material in the item; the attachment's Gmail id is kept
        # in metadata for later binary fetch, but inline files are absent
        assert "ANGtdJx1" not in str(result["data"])
        assert "ANGtdJx2" not in str(result)

    def test_gmail_fields_in_metadata(self, importer):
        result = importer.translate_gmail_to_email(simple_message())
        metadata = result["metadata"]
        assert metadata["provider"] == "google"
        assert metadata["source"] == "gmail_api"
        assert metadata["gmail_message_id"] == "18abc123"
        assert metadata["gmail_thread_id"] == "18abc123"
        assert "INBOX" in metadata["gmail_labels"]
        # html preserved for a plain-text-primary message
        assert "<p>HTML body</p>" in metadata["html_body"]

    def test_missing_message_id_skipped(self, importer):
        msg = simple_message()
        msg["payload"]["headers"] = [
            h for h in msg["payload"]["headers"] if h["name"] != "Message-ID"
        ]
        assert importer.translate_gmail_to_email(msg) is None

    def test_html_only_message(self, importer):
        msg = simple_message()
        msg["payload"]["parts"] = [
            part for part in msg["payload"]["parts"] if part["mimeType"] != "text/plain"
        ]
        result = importer.translate_gmail_to_email(msg)
        assert result["data"]["content_type"] == "text/html"
        assert result["data"]["description"] == "<p>HTML body</p>"

    def test_legacy_charset_decoded(self, importer):
        """ISO-8859-1 body decodes via its declared charset, not utf-8."""
        import base64 as b64

        raw = "caf\u00e9 na\u00efve".encode("iso-8859-1")
        msg = simple_message()
        msg["payload"]["parts"] = [
            {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}
                ],
                "body": {"data": b64.urlsafe_b64encode(raw).decode().rstrip("=")},
            }
        ]
        result = importer.translate_gmail_to_email(msg)
        assert result["data"]["description"] == "caf\u00e9 na\u00efve"


class TestFetchOrchestration:
    def test_oldest_first_slice_errors_and_skips(self, importer, monkeypatch):
        """fetch_emails reverses the finder's newest-first ids, slices to the
        limit, counts fetch errors, and skips untranslatable messages."""
        from gmail_importer import importer as importer_module
        from gmail_importer.gmail_api import GmailApiError

        class FakeApi:
            def __init__(self):
                self.fetched = []

            def get_message(self, message_id):
                self.fetched.append(message_id)
                if message_id == "boom":
                    raise GmailApiError("404")
                msg = simple_message()
                msg["id"] = message_id
                if message_id == "untranslatable":
                    del msg["payload"]["headers"]
                return msg

        api = FakeApi()
        monkeypatch.setattr(importer, "_build_api_client", lambda: api)
        # Finder contract: ids are NEWEST-first. "18abc123" is the oldest.
        monkeypatch.setattr(
            importer_module,
            "find_oldest_message_ids",
            lambda api_, target, verbose: ["newest", "18abc123", "untranslatable", "boom"],
        )

        importer._limit = 3
        importer._verbose = False
        emails = list(importer.fetch_emails())

        # reversed + sliced: newest dropped entirely, boom counted as error,
        # untranslatable skipped, 18abc123 yielded oldest-first.
        assert api.fetched == ["boom", "untranslatable", "18abc123"]
        assert [e["data"]["rfc_message_id"] for e in emails] == ["<msg-1@example.com>"]
        assert importer.stats["errors"] == 1
        assert importer.stats["skipped"] == 1


class TestSoilRoundtrip:
    def test_import_dedup_and_retrieval(self, importer, monkeypatch):
        """End-to-end through the base pipeline: write, dedup, read back."""
        first = importer.translate_gmail_to_email(simple_message())
        parent = dict(first)
        parent["data"] = {
            **first["data"],
            "rfc_message_id": "<msg-0@example.com>",
            "title": "Thread root",
            "in_reply_to": None,
            "references": None,
        }
        monkeypatch.setattr(
            importer, "fetch_emails", lambda since=None: iter([parent, first])
        )

        stats = importer.import_all(verbose=False)
        assert stats["imported"] == 2
        assert stats["relations_created"] == 1  # first replies_to parent

        # Re-import: everything deduplicated (stats are cumulative)
        monkeypatch.setattr(
            importer, "fetch_emails", lambda since=None: iter([parent, first])
        )
        stats2 = importer.import_all(verbose=False)
        assert stats2["imported"] == 2  # unchanged from first run
        assert stats2["skipped"] == 2
        assert stats2["relations_created"] == 1  # no duplicate relations

        with importer.soil:
            items = importer.soil.list_items(_type="Email", limit=10)
        assert len(items) == 2
        by_subject = {item.data["title"]: item for item in items}
        assert by_subject["Hello world"].data["attachment_filenames"] == ["invoice.pdf"]
        assert by_subject["Hello world"].metadata["gmail_thread_id"] == "18abc123"


class TestRelaxedValidation:
    def test_missing_subject_imports_with_empty_title(self, importer):
        """Emails with no Subject header are valid — empty title, not skipped."""
        msg = simple_message()
        msg["payload"]["headers"] = [
            h for h in msg["payload"]["headers"] if h["name"] != "Subject"
        ]
        result = importer.translate_gmail_to_email(msg)
        assert result is not None
        assert result["data"]["title"] == ""

    def test_bodyless_attachment_only_imports(self, importer):
        """Attachment-only emails (no body parts) import with empty body."""
        msg = simple_message()
        parts = msg["payload"]["parts"]
        msg["payload"]["parts"] = [p for p in parts if p.get("filename")]
        result = importer.translate_gmail_to_email(msg)
        assert result is not None
        assert result["data"]["description"] == ""
        assert result["data"]["content_type"] == "text/plain"
        assert result["data"]["has_attachments"] is True


class TestRelaxedValidation:
    def test_missing_subject_imports_with_empty_title(self, importer):
        """Emails with no Subject header are valid — empty title, not skipped."""
        msg = simple_message()
        msg["payload"]["headers"] = [
            h for h in msg["payload"]["headers"] if h["name"] != "Subject"
        ]
        result = importer.translate_gmail_to_email(msg)
        assert result is not None
        assert result["data"]["title"] == ""

    def test_missing_to_imports(self, importer):
        """Bcc-only sent mail carries no To header — still ingested."""
        msg = simple_message()
        msg["payload"]["headers"] = [
            h for h in msg["payload"]["headers"] if h["name"] != "To"
        ]
        result = importer.translate_gmail_to_email(msg)
        assert result is not None
        assert result["data"]["to_addresses"] == []

    def test_bodyless_attachment_only_imports(self, importer):
        """Attachment-only emails (no body parts) import with empty body."""
        msg = simple_message()
        parts = msg["payload"]["parts"]
        msg["payload"]["parts"] = [p for p in parts if p.get("filename")]
        result = importer.translate_gmail_to_email(msg)
        assert result is not None
        assert result["data"]["description"] == ""
        assert result["data"]["content_type"] == "text/plain"
        assert result["data"]["has_attachments"] is True
