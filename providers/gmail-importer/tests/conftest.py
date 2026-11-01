"""Shared test helpers and fixtures."""

import base64

import pytest
from system.soil import Soil

from gmail_importer.importer import GmailImporter


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def b64url_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def simple_message() -> dict:
    return {
        "id": "18abc123",
        "threadId": "18abc123",
        "labelIds": ["INBOX", "CATEGORY_PERSONAL"],
        "internalDate": "1077930003000",  # 2004-02-28T01:00:03Z
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


@pytest.fixture
def importer(tmp_path) -> GmailImporter:
    # HACM/path values are irrelevant for pure translation tests, but the
    # base class builds a dedup index from Soil, so a real tmp db is used.
    # Context is held only for construction; import_all manages its own.
    soil = Soil(tmp_path / "soil.db")
    with soil:
        soil.init_schema()  # idempotent (IF NOT EXISTS)
        imp = GmailImporter(
            soil=soil,
            account_id="t@example.com",
            hacm_path="/tmp/x.enc",
        )
    yield imp
