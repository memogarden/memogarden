"""Tests for content-addressed attachment storage and extraction.

RFC-012 §4.9 (2026-11-20): the extractor no longer writes facts — it
PREPARES runtime dicts (yield contract); the runtime creates the facts
(scope-stamped) and the provider's create_relations links contained_by.
Persistence-side assertions here go through conftest.store_yielded, the
test-double of that runtime behavior.
"""

from datetime import datetime, timezone

import pytest
from system.soil import create_email_item

from conftest import b64url_bytes, simple_message, store_yielded
from gmail_importer.attachments import AttachmentExtractor, AttachmentStore

PDF_BYTES = b"%PDF-1.4 fake invoice content"


@pytest.fixture
def store(tmp_path) -> AttachmentStore:
    return AttachmentStore(tmp_path / "attachments")


@pytest.fixture
def importer_with_email(importer):
    """`importer` fixture (conftest) plus one ingested Email fact in its Soil."""
    email_dict = importer.translate_gmail_to_email(simple_message())
    with importer.soil:
        item = create_email_item(**email_dict)
        email_uuid = importer.soil.create_fact(item)
    return importer, email_uuid


class FakeAttachmentApi:
    """Refetches simple_message(); serves PDF_BYTES for any attachment id."""

    def __init__(self, attachment_bytes: bytes = PDF_BYTES):
        self.attachment_bytes = attachment_bytes
        self.fetched_messages: list[str] = []
        self.fetched_attachments: list[tuple[str, str]] = []

    def get_message(self, message_id):
        self.fetched_messages.append(message_id)
        return simple_message()  # has invoice.pdf attachment part

    def get_attachment(self, message_id, attachment_id):
        self.fetched_attachments.append((message_id, attachment_id))
        # Real API shape: data is a TOP-LEVEL field of the Attachment resource.
        return {"data": b64url_bytes(self.attachment_bytes), "size": len(self.attachment_bytes)}


def _drain(importer, store, api, account_filter=None, dry_run=False) -> tuple[list[dict], dict]:
    extractor = AttachmentExtractor(
        importer.soil, store, lambda subject: api, verbose=False
    )
    items = list(
        extractor.iter_uncovered(account_filter=account_filter, dry_run=dry_run)
    )
    return items, extractor.stats


class TestAttachmentStore:
    def test_write_returns_hash_and_relative_path(self, store):
        rel, digest = store.write(PDF_BYTES, "invoice.pdf")
        assert rel.endswith(".pdf")
        assert digest in rel
        assert (store.root / rel).read_bytes() == PDF_BYTES

    def test_sharded_two_levels(self, store):
        rel, digest = store.write(PDF_BYTES, "invoice.pdf")
        parts = rel.split("/")
        assert len(parts) == 3
        assert parts[0] == digest[:2]
        assert parts[1] == digest[2:4]

    def test_same_content_different_name_deduplicates(self, store):
        rel1, d1 = store.write(PDF_BYTES, "a.pdf")
        rel2, d2 = store.write(PDF_BYTES, "totally-different-name.pdf")
        assert rel1 == rel2
        assert d1 == d2
        assert len([p for p in store.root.rglob("*") if p.is_file()]) == 1

    def test_extension_sanitized(self, store):
        _, digest = store.write(PDF_BYTES, "../../evil/..%2F..pdf")
        name = store.path_for(digest, ".pdf").name
        written = [p.name for p in store.root.rglob("*") if p.is_file()]
        assert written == [f"{digest}.pdf"]

    def test_no_extension(self, store):
        rel, digest = store.write(PDF_BYTES, "README")
        assert rel.split("/")[-1] == digest


class TestAttachmentExtractor:
    def test_yields_runtime_dict_without_writing_facts(self, importer_with_email, store):
        """The yield contract: a prepared dict with the parent link and the
        binary already in the store — and NO fact or relation in Soil
        (persistence belongs to the runtime, which also scope-stamps)."""
        importer, email_uuid = importer_with_email
        api = FakeAttachmentApi()
        items, stats = _drain(importer, store, api)

        assert stats["downloaded"] == 1
        assert stats["errors"] == 0
        assert api.fetched_messages == ["18abc123"]  # via stored gmail_message_id
        assert len(items) == 1
        item = items[0]
        assert item["type"] == "Attachment"
        assert item["data"]["filename"] == "invoice.pdf"
        assert item["data"]["sha256"]
        assert item["metadata"]["email_uuid"] == email_uuid
        assert item["metadata"]["gmail_attachment_id"] == "ANGtdJx1"
        assert item["canonical_at"]  # arrived with its email

        # the file on disk matches the declared path and original bytes
        file_path = store.root / item["data"]["content_path"]
        assert file_path.read_bytes() == PDF_BYTES

        # runtime dict shape is complete for _to_fact
        assert set(item) >= {"type", "data", "metadata", "canonical_at", "realized_at"}

        # and nothing was persisted by the extractor itself
        with importer.soil:
            assert importer.soil.list_items(_type="Attachment", limit=10) == []
            rows = importer.soil._get_connection().execute(
                "SELECT count(*) AS n FROM system_relation WHERE kind = 'contained_by'"
            ).fetchone()
        assert rows["n"] == 0

    def test_runtime_persistence_makes_rerun_covered(self, importer_with_email, store):
        """After the runtime stores a pass's yields (store_yielded), the next
        pass is covered-only: zero API calls, zero downloads."""
        importer, _ = importer_with_email
        items, _ = _drain(importer, store, FakeAttachmentApi())
        store_yielded(importer.soil, items)

        api2 = FakeAttachmentApi()
        items2, stats2 = _drain(importer, store, api2)
        assert items2 == []
        assert stats2["emails_covered"] == 1  # email skipped without refetch
        assert stats2["skipped_existing"] == 0
        assert stats2["downloaded"] == 0
        # coverage skip: zero API calls at all on the rerun
        assert api2.fetched_messages == []
        assert api2.fetched_attachments == []

    def test_rerun_survives_gmail_id_churn(self, importer_with_email, store):
        """Gmail attachment ids change between fetches; the (email, filename)
        idempotency key must still skip the re-run."""
        importer, _ = importer_with_email

        class ChurningApi(FakeAttachmentApi):
            counter = 0

            def get_message(self, message_id):
                ChurningApi.counter += 1
                msg = super().get_message(message_id)
                for part in msg["payload"]["parts"]:
                    if part.get("body", {}).get("attachmentId"):
                        part["body"]["attachmentId"] = f"CHURNED-{ChurningApi.counter}"
                return msg

        items, _ = _drain(importer, store, ChurningApi())
        store_yielded(importer.soil, items)
        api2 = ChurningApi()
        items2, stats2 = _drain(importer, store, api2)
        assert stats2["emails_covered"] == 1
        assert stats2["downloaded"] == 0
        assert api2.fetched_messages == []

    def test_partial_coverage_refetches_only_missing(self, importer_with_email, store):
        """If one stored attachment fact vanishes (e.g. hand-deleted), the
        email is refetched and only the missing instance is prepared."""
        importer, _ = importer_with_email
        items, _ = _drain(importer, store, FakeAttachmentApi())
        store_yielded(importer.soil, items)
        with importer.soil:
            facts = importer.soil.list_items(_type="Attachment", limit=10)
            conn = importer.soil._get_connection()
            conn.execute("DELETE FROM system_relation WHERE source = ?", (facts[0].uuid,))
            conn.execute("DELETE FROM item WHERE uuid = ?", (facts[0].uuid,))

        api2 = FakeAttachmentApi()
        items2, stats2 = _drain(importer, store, api2)
        assert stats2["emails_covered"] == 0      # not fully covered anymore
        assert stats2["downloaded"] == 1          # refilled the gap
        assert stats2["skipped_existing"] == 0    # duplicate excluded by done set

    def test_dry_run_stores_nothing(self, importer_with_email, store):
        importer, _ = importer_with_email
        api = FakeAttachmentApi()
        items, stats = _drain(importer, store, api, dry_run=True)
        assert stats["attachments_found"] == 1
        assert stats["downloaded"] == 0
        assert items == []
        assert api.fetched_attachments == []
        assert not store.root.exists() or not any(store.root.rglob("*"))

    def test_refetch_failure_counts_error(self, importer_with_email, store):
        importer, _ = importer_with_email

        class FailingApi:
            def get_message(self, message_id):
                raise RuntimeError("gmail down")

            def get_attachment(self, message_id, attachment_id):  # pragma: no cover
                raise AssertionError("should not be reached")

        items, stats = _drain(importer, store, FailingApi())
        assert stats["errors"] == 1
        assert stats["downloaded"] == 0
        assert items == []

    def test_empty_payload_refused(self, importer_with_email, store):
        importer, _ = importer_with_email

        class EmptyApi(FakeAttachmentApi):
            def get_attachment(self, message_id, attachment_id):
                return {"data": "", "size": 0}

        items, stats = _drain(importer, store, EmptyApi())
        assert stats["errors"] == 1
        assert stats["downloaded"] == 0
        assert items == []
        with importer.soil:
            assert importer.soil.list_items(_type="Attachment", limit=10) == []

    def test_deleted_at_source_email_skipped(self, importer_with_email, store):
        from gmail_importer.gmail_api import GmailApiError

        importer, _ = importer_with_email

        class GoneApi:
            def get_message(self, message_id):  # pragma: no cover
                raise GmailApiError("gone", status_code=404)

            def get_attachment(self, message_id, attachment_id):  # pragma: no cover
                raise AssertionError("should not be reached")

        items, stats = _drain(importer, store, GoneApi())
        assert stats["emails_deleted"] == 1
        assert stats["errors"] == 0
        assert items == []

    def test_amended_email_stays_covered_no_recapture(self, importer_with_email, store):
        """system#11: a scope relabel supersedes the email under a NEW uuid;
        coverage keys on the immutable gmail_message_id, so the amended
        email is still covered — no refetch, no duplicate re-capture."""
        importer, email_uuid = importer_with_email
        items, _ = _drain(importer, store, FakeAttachmentApi())
        store_yielded(importer.soil, items)

        # amend the email: supersede it with a new uuid (same gmail id)
        email_dict = importer.translate_gmail_to_email(simple_message())
        with importer.soil:
            new_uuid = importer.soil.create_fact(create_email_item(**email_dict))
            importer.soil.mark_superseded(
                email_uuid, new_uuid, datetime.now(timezone.utc).isoformat()
            )

        api2 = FakeAttachmentApi()
        items2, stats2 = _drain(importer, store, api2)
        assert items2 == []                       # no duplicate re-capture
        assert stats2["emails_covered"] == 1      # the amended email is covered
        assert api2.fetched_messages == []        # zero network
        with importer.soil:
            # still exactly one live attachment fact per instance
            live = [f for f in importer.soil.list_items(_type="Attachment", limit=10)
                    if not f.superseded_by]
        assert len(live) == 1
        assert live[0].metadata["email_uuid"] == email_uuid  # original parent kept


class TestAccountScoping:
    """Per-account runs (RFC-012 §4.6) scan strictly their own emails."""

    @pytest.fixture
    def two_account_soil(self, importer):
        """Soil with one email owned by each of two accounts, both declaring
        attachments."""
        from test_rfc012_accounts import ACCOUNT, OTHER, _email_dict

        emails = {}
        with importer.soil:
            for subject in (ACCOUNT, OTHER):
                item = _email_dict(importer, account=subject)
                emails[subject] = importer.soil.create_fact(create_email_item(**item))
        return emails

    def test_account_filter_scans_only_own_emails(self, importer, store, two_account_soil):
        from test_rfc012_accounts import ACCOUNT, OTHER

        items, stats = _drain(importer, store, FakeAttachmentApi(), account_filter=ACCOUNT)
        assert stats["emails_scanned"] == 1
        assert len(items) == 1
        # the yielded instance is the ACCOUNT-owned email's
        assert items[0]["metadata"]["email_uuid"] == two_account_soil[ACCOUNT]
        assert items[0]["metadata"]["account"] == ACCOUNT
        assert items[0]["metadata"]["account"] != OTHER

    def test_foreign_account_never_reaches_this_clients_factory(
        self, importer, store, two_account_soil
    ):
        """Defense in depth: the provider passes its own run's client via a
        factory that rejects foreign subjects (a scan bug would fail loudly,
        not fetch cross-mailbox)."""
        from gmail_importer.gmail_api import GmailApiError
        from test_rfc012_accounts import ACCOUNT

        calls: list[str] = []

        def factory(subject):
            calls.append(subject)
            if subject is not None and subject.lower() != ACCOUNT.lower():
                raise GmailApiError(f"foreign account {subject}", status_code=403)
            return FakeAttachmentApi()

        extractor = AttachmentExtractor(
            importer.soil, store, factory, verbose=False, default_account=ACCOUNT
        )
        items = list(extractor.iter_uncovered(account_filter=ACCOUNT))
        assert len(items) == 1
        assert calls == [ACCOUNT]  # only the owning subject was requested


class TestTranslationMetadata:
    def test_gmail_attachments_recorded(self, importer):
        result = importer.translate_gmail_to_email(simple_message())
        assert result["metadata"]["gmail_attachments"] == [
            {
                "filename": "invoice.pdf",
                "attachment_id": "ANGtdJx1",
                "mime_type": "application/pdf",
                "size": 12345,
            }
        ]
        # legacy data fields unchanged
        assert result["data"]["attachment_filenames"] == ["invoice.pdf"]
        assert result["data"]["attachment_count"] == 1
