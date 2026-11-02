"""Tests for progressive ingestion: tracked cursor, next-N batches.

Each "run" uses a fresh GmailImporter over the same Soil — matching
production, where every hourly timer fire is a new process. Stats are
therefore per-run, and the dedup index is rebuilt from committed facts.
"""

import pytest
from system.soil import create_email_item

from conftest import simple_message
from gmail_importer import importer as importer_module
from gmail_importer.importer import GmailImporter


class ScriptedGmail:
    """A mailbox of 6 messages, oldest-first a..f.

    The real finder returns the OLDEST `target` ids in newest-first order;
    tests monkeypatch find_oldest_message_ids with oldest_window(target)
    to mimic exactly that contract.
    """

    ORDER_OLDEST_FIRST = ["a", "b", "c", "d", "e", "f"]

    @staticmethod
    def oldest_window(target: int) -> list[str]:
        """Newest-first ids of the `target` oldest messages (finder contract)."""
        return list(reversed(ScriptedGmail.ORDER_OLDEST_FIRST[:target]))

    def __init__(self):
        self.fetched: list[str] = []

    def get_message(self, message_id):
        self.fetched.append(message_id)
        msg = simple_message()
        msg["id"] = message_id
        msg["payload"] = {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
                {"name": "From", "value": "x@example.com"},
                {"name": "To", "value": "y@example.com"},
                {"name": "Subject", "value": f"Mail {message_id}"},
            ],
            "body": {"data": "SGVsbG8="},  # "Hello"
        }
        return msg


def make_importer(soil) -> GmailImporter:
    """Fresh importer over existing Soil (construction rebuilds dedup index)."""
    with soil:
        imp = GmailImporter(soil=soil, account_id="t@example.com", hacm_path="/tmp/x.enc")
    return imp


def run_next(soil, monkeypatch, limit=2):
    """One hourly run: fresh importer, scripted Gmail, progressive import."""
    imp = make_importer(soil)
    api = ScriptedGmail()
    monkeypatch.setattr(imp, "_build_api_client", lambda: api)
    monkeypatch.setattr(
        importer_module,
        "find_oldest_message_ids",
        lambda api_, target, verbose, page_budget: ScriptedGmail.oldest_window(target),
    )
    stats = imp.import_next(limit=limit, verbose=False)
    return imp, api, stats


def ingest_ids_directly(soil, importer, mids) -> None:
    """Place specific messages into Soil as if ingested previously."""
    from datetime import datetime, timezone

    realized = datetime.now(timezone.utc).isoformat()
    with soil:
        for mid in mids:
            item = create_email_item(
                **importer.translate_gmail_to_email(
                    ScriptedGmail().get_message(mid), realized_at=realized
                )
            )
            soil.create_fact(item)


def soil_emails(soil) -> list[str]:
    with soil:
        return sorted(
            f.data["title"].replace("Mail ", "")
            for f in soil.list_items(_type="Email", limit=100)
        )


class TestProgressiveIngestion:
    def test_first_run_skips_ingested_fetches_next_batch(self, importer, monkeypatch):
        """cursor=2 (a, b ingested): a and b are NOT fetched; batch is c, d."""
        ingest_ids_directly(importer.soil, importer, ["a", "b"])
        imp, api, stats = run_next(importer.soil, monkeypatch, limit=2)
        assert api.fetched == ["c", "d"]
        assert stats["imported"] == 2
        assert soil_emails(importer.soil) == ["a", "b", "c", "d"]

    def test_cursor_advances_across_runs(self, importer, monkeypatch):
        ingest_ids_directly(importer.soil, importer, ["a", "b"])
        _, _, s1 = run_next(importer.soil, monkeypatch, limit=2)
        _, _, s2 = run_next(importer.soil, monkeypatch, limit=2)
        assert s1["imported"] == 2  # c, d
        assert s2["imported"] == 2  # e, f
        assert soil_emails(importer.soil) == list("abcdef")

    def test_exhausted_mailbox_is_clean_noop(self, importer, monkeypatch):
        ingest_ids_directly(importer.soil, importer, list("abcdef"))
        imp, api, stats = run_next(importer.soil, monkeypatch, limit=2)
        assert stats["imported"] == 0
        assert api.fetched == []  # exhausted: nothing fetched at all

    def test_nonprefix_done_set_selected_correctly(self, importer, monkeypatch):
        """Done set {b, c} is not a mailbox prefix (e.g. mixed import
        sources). Set-based selection picks the first two window ids NOT in
        the done set — no count assumptions, no overlap, nothing skipped."""
        ingest_ids_directly(importer.soil, importer, ["b", "c"])
        imp, api, stats = run_next(importer.soil, monkeypatch, limit=2)
        assert api.fetched == ["a", "d"]  # b, c excluded by the done set
        assert stats["imported"] == 2
        assert soil_emails(importer.soil) == ["a", "b", "c", "d"]

    def test_skipped_message_retried_next_run(self, importer, monkeypatch):
        """A message skipped for validation stays out of the done set and is
        retried next run — self-healing after a validation change."""
        # Simulate the no-subject mail: strip Subject so validate_email_data
        # (relaxed) now accepts an empty title instead of skipping.
        done_ids = ["a", "c"]  # pretend b was once skipped (e.g. old validator)
        ingest_ids_directly(importer.soil, importer, ["a", "c"])
        # note: 'b' was never ingested
        imp, api, stats = run_next(importer.soil, monkeypatch, limit=3)
        assert set(api.fetched) == {"b", "d", "e"}
        assert stats["imported"] == 3

    def test_page_budget_scales_with_cursor(self):
        from gmail_importer.gmail_api import page_budget_for

        assert page_budget_for(0, 100) == 12          # default floor
        assert page_budget_for(5000, 100) == 13       # ceil(5100/500)=11 + 2
        assert page_budget_for(6000, 100) == 15       # ceil(6100/500)=13 + 2
        assert page_budget_for(19000, 100) == 40      # capped


class TestLegacyOldestMode:
    def test_import_oldest_still_fetches_head(self, importer, monkeypatch):
        imp = make_importer(importer.soil)
        api = ScriptedGmail()
        monkeypatch.setattr(imp, "_build_api_client", lambda: api)
        monkeypatch.setattr(
            importer_module,
            "find_oldest_message_ids",
            lambda api_, target, verbose, page_budget=12: ScriptedGmail.oldest_window(target),
        )
        stats = imp.import_oldest(limit=2, verbose=False)
        assert api.fetched == ["a", "b"]  # head of the mailbox, oldest-first
        assert stats["imported"] == 2
