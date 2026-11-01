"""Tests for Gmail API message -> Email item translation.

Fixtures mirror the Gmail API v1 message resource shape (format=full).
"""

import base64

import pytest
from system.soil import Soil

from gmail_importer.importer import GmailImporter


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.fixture
def importer(tmp_path) -> GmailImporter:
    # HACM/path values are irrelevant for pure translation tests, but the
    # base class builds a dedup index from Soil, so a real tmp db is used.
    # Soil requires context-manager use even for schema init and reads;
    # context is held only for construction, import_all manages its own.
    soil = Soil(tmp_path / "soil.db")
    with soil:
        soil.init_schema()  # idempotent (IF NOT EXISTS)
        imp = GmailImporter(
            soil=soil,
            account_id="t@example.com",
            hacm_path="/tmp/x.enc",
        )
    yield imp


def simple_message() -> dict:
    return {
        "id": "18abc123",
        "threadId": "18abc123",
        "labelIds": ["INBOX", "CATEGORY_PERSONAL"],
        "internalDate": "1077930003000",  # 2004-02-28T03:00:03Z-ish epoch ms
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Message-ID", "value": "<msg-1@example.com>"},
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "bob@example.com, carol@example.com"},
                {"name": "Cc", "value": "dora@example.com"},
                {"name": "Subject", "value": "Hello world"},
                {"name": "Date", "value": "Sat, 28 Feb 2004 03:00:03 +0000"},
                {"name": "In-Reply-To", "value": "<msg-0@example.com>"},
                {"name": "References", "value": "<msg-0@example.com> <msg-0a@example.com>"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64url("Plain body here")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": b64url("<p>HTML body</p>")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice.pdf",
                    "headers": [
                        {"name": "Content-Disposition", "value": "attachment; filename=\"invoice.pdf\""}
                    ],
                    "body": {"attachmentId": "ANGtdJx1", "size": 12345},
                },
                {
                    "mimeType": "image/png",
                    "filename": "inline-logo.png",
                    "headers": [
                        {"name": "Content-Disposition", "value": "inline; filename=\"inline-logo.png\""}
                    ],
                    "body": {"attachmentId": "ANGtdJx2", "size": 999},
                },
            ],
        },
    }


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
        # no binary material anywhere in the item
        assert "ANGtdJx1" not in str(result)

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
