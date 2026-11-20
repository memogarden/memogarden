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


def store_yielded(soil, items) -> list[str]:
    """Simulate the runtime side of the yield contract (RFC-012 §4.9).

    Since 2026-11-20 the extractor only PREPARES attachment dicts; the
    runtime creates the facts (with scope policy applied) and the
    provider's create_relations hook links contained_by. Extractor tests
    call this to persist a drained pass, exactly as the runtime would
    (minus the scope stamp, which is runtime policy territory).
    """
    from datetime import datetime, timezone

    from system.soil.fact import Fact, generate_soil_uuid
    from system.soil.relation import SystemRelation

    now = datetime.now(timezone.utc).isoformat()
    epoch_days = (datetime.now(timezone.utc) - datetime(2020, 1, 1, tzinfo=timezone.utc)).days
    stored: list[str] = []
    with soil:
        for item in items:
            fact = Fact(
                uuid=generate_soil_uuid(),
                _type=item["type"],
                realized_at=item.get("realized_at", now),
                canonical_at=item.get("canonical_at", now),
                data=item["data"],
                metadata=item.get("metadata") or {},
                integrity_hash=None,
                fidelity="full",
            )
            uuid = soil.create_fact(fact)
            parent = (item.get("metadata") or {}).get("email_uuid")
            if parent:
                soil.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="contained_by",
                    source=uuid,
                    source_type="item",
                    target=parent,
                    target_type="item",
                    created_at=epoch_days,
                    evidence=None,
                    metadata=None,
                ))
            stored.append(uuid)
    return stored
