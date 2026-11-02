"""Tests for attachment integrity verification (mutation detection)."""

import pytest
from system.soil import create_email_item

from conftest import simple_message
from gmail_importer.attachments import AttachmentExtractor, AttachmentStore
from gmail_importer.verify import verify_attachments

PDF_BYTES = b"%PDF-1.4 fake invoice content"


@pytest.fixture
def extracted(importer, tmp_path):
    """An importer with one ingested email and its attachment extracted."""
    email_dict = importer.translate_gmail_to_email(simple_message())
    with importer.soil:
        item = create_email_item(**email_dict)
        importer.soil.create_fact(item)

    class FakeApi:
        def get_message(self, message_id):
            return simple_message()

        def get_attachment(self, message_id, attachment_id):
            import base64

            return {"data": base64.urlsafe_b64encode(PDF_BYTES).decode().rstrip("=")}

    store = AttachmentStore(tmp_path / "attachments")
    AttachmentExtractor(importer.soil, store, lambda: FakeApi(), verbose=False).extract_all()
    return importer, store


class TestVerify:
    def test_clean_store_passes(self, extracted):
        importer, store = extracted
        report = verify_attachments(importer.soil, store.root)
        assert report.checked == 1
        assert report.ok == 1
        assert report.problems == 0

    def test_mutated_file_detected(self, extracted):
        importer, store = extracted
        with importer.soil:
            fact = importer.soil.list_items(_type="Attachment", limit=1)[0]
        path = store.root / fact.data["content_path"]
        path.write_bytes(b"%PDF-1.4 TAMPERED CONTENT")
        report = verify_attachments(importer.soil, store.root)
        assert len(report.mutated) == 1
        assert report.ok == 0
        assert report.problems == 1

    def test_missing_file_detected(self, extracted):
        importer, store = extracted
        with importer.soil:
            fact = importer.soil.list_items(_type="Attachment", limit=1)[0]
        (store.root / fact.data["content_path"]).unlink()
        report = verify_attachments(importer.soil, store.root)
        assert len(report.missing) == 1
        assert report.ok == 0

    def test_orphaned_file_detected(self, extracted):
        importer, store = extracted
        stray_dir = store.root / "ff" / "ff"
        stray_dir.mkdir(parents=True)
        (stray_dir / ("f" * 64 + ".pdf")).write_bytes(b"unclaimed bytes")
        report = verify_attachments(importer.soil, store.root)
        assert len(report.orphaned) == 1
        assert report.checked == 1 and report.ok == 1  # claimed file still ok

    def test_fact_without_path_flagged(self, importer, tmp_path):
        from system.soil.fact import Fact, generate_soil_uuid
        from datetime import datetime, timezone

        soil = importer.soil
        with soil:
            soil.create_fact(Fact(
                uuid=generate_soil_uuid(), _type="Attachment",
                realized_at=datetime.now(timezone.utc).isoformat(),
                canonical_at=datetime.now(timezone.utc).isoformat(),
                fidelity="full", data={"filename": "x.pdf"}, metadata={},
            ))
        report = verify_attachments(soil, tmp_path / "attachments")
        assert len(report.mutated) == 1  # reported as a problem
