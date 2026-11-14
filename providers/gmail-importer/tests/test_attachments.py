"""Tests for content-addressed attachment storage and extraction."""

import pytest
from system.soil import create_email_item

from conftest import b64url_bytes, simple_message
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

    def get_attachment_empty(self, message_id, attachment_id):  # mirror of the bug
        return {"body": {"data": b64url_bytes(self.attachment_bytes)}}


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
    def test_extracts_fact_relation_and_file(self, importer_with_email, store):
        importer, email_uuid = importer_with_email
        api = FakeAttachmentApi()
        stats = AttachmentExtractor(
            importer.soil, store, lambda subject: api, verbose=False
        ).extract_all()

        assert stats["downloaded"] == 1
        assert stats["errors"] == 0
        assert api.fetched_messages == ["18abc123"]  # via stored gmail_message_id

        with importer.soil:
            facts = importer.soil.list_items(_type="Attachment", limit=10)
        assert len(facts) == 1
        fact = facts[0]
        assert fact.data["filename"] == "invoice.pdf"
        assert fact.data["sha256"]
        assert fact.metadata["email_uuid"] == email_uuid
        assert fact.metadata["gmail_attachment_id"] == "ANGtdJx1"

        # the file on disk matches the declared path and original bytes
        file_path = store.root / fact.data["content_path"]
        assert file_path.read_bytes() == PDF_BYTES

        # relation: attachment contained_by email
        with importer.soil:
            rows = importer.soil._get_connection().execute(
                "SELECT kind, source, target FROM system_relation WHERE kind = 'contained_by'"
            ).fetchall()
        assert [tuple(r) for r in rows] == [("contained_by", fact.uuid, email_uuid)]

    def test_rerun_is_idempotent(self, importer_with_email, store):
        importer, _ = importer_with_email
        AttachmentExtractor(importer.soil, store, lambda subject: FakeAttachmentApi(), verbose=False).extract_all()
        api2 = FakeAttachmentApi()
        stats2 = AttachmentExtractor(importer.soil, store, lambda subject: api2, verbose=False).extract_all()
        assert stats2["emails_covered"] == 1  # email skipped without refetch
        assert stats2["skipped_existing"] == 0
        assert stats2["downloaded"] == 0
        # coverage skip: zero API calls at all on the rerun
        assert api2.fetched_messages == []
        assert api2.fetched_attachments == []
        with importer.soil:
            assert len(importer.soil.list_items(_type="Attachment", limit=10)) == 1

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

        AttachmentExtractor(importer.soil, store, lambda subject: ChurningApi(), verbose=False).extract_all()
        api2 = ChurningApi()
        stats2 = AttachmentExtractor(importer.soil, store, lambda subject: api2, verbose=False).extract_all()
        assert stats2["emails_covered"] == 1
        assert stats2["downloaded"] == 0
        assert api2.fetched_messages == []
        with importer.soil:
            assert len(importer.soil.list_items(_type="Attachment", limit=10)) == 1

    def test_partial_coverage_refetches_only_missing(self, importer_with_email, store):
        """If one attachment fact is missing (e.g. a past failure), the email
        is refetched and only the missing instance is downloaded."""
        importer, _ = importer_with_email
        AttachmentExtractor(importer.soil, store, lambda subject: FakeAttachmentApi(), verbose=False).extract_all()
        # drop one extracted fact to simulate a past failure
        with importer.soil:
            facts = importer.soil.list_items(_type="Attachment", limit=10)
            conn = importer.soil._get_connection()
            conn.execute("DELETE FROM system_relation WHERE source = ?", (facts[0].uuid,))
            conn.execute("DELETE FROM item WHERE uuid = ?", (facts[0].uuid,))

        api2 = FakeAttachmentApi()
        stats2 = AttachmentExtractor(importer.soil, store, lambda subject: api2, verbose=False).extract_all()
        assert stats2["emails_covered"] == 0      # not fully covered anymore
        assert stats2["downloaded"] == 1          # refilled the gap
        assert stats2["skipped_existing"] == 0    # duplicate excluded by done set
        with importer.soil:
            assert len(importer.soil.list_items(_type="Attachment", limit=10)) == 1

    def test_dry_run_stores_nothing(self, importer_with_email, store):
        importer, _ = importer_with_email
        api = FakeAttachmentApi()
        stats = AttachmentExtractor(
            importer.soil, store, lambda subject: api, verbose=False
        ).extract_all(dry_run=True)
        assert stats["attachments_found"] == 1
        assert stats["downloaded"] == 0
        assert api.fetched_attachments == []
        assert not store.root.exists() or not any(store.root.rglob("*"))
        with importer.soil:
            assert importer.soil.list_items(_type="Attachment", limit=10) == []

    def test_refetch_failure_counts_error(self, importer_with_email, store):
        importer, _ = importer_with_email

        class FailingApi:
            def get_message(self, message_id):
                raise RuntimeError("gmail down")

            def get_attachment(self, message_id, attachment_id):  # pragma: no cover
                raise AssertionError("should not be reached")

        stats = AttachmentExtractor(
            importer.soil, store, lambda subject: FailingApi(), verbose=False
        ).extract_all()
        assert stats["errors"] == 1
        assert stats["downloaded"] == 0

    def test_empty_payload_refused(self, importer_with_email, store):
        importer, _ = importer_with_email

        class EmptyApi(FakeAttachmentApi):
            def get_attachment(self, message_id, attachment_id):
                return {"data": "", "size": 0}

        stats = AttachmentExtractor(
            importer.soil, store, lambda subject: EmptyApi(), verbose=False
        ).extract_all()
        assert stats["errors"] == 1
        assert stats["downloaded"] == 0
        with importer.soil:
            assert importer.soil.list_items(_type="Attachment", limit=10) == []


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
