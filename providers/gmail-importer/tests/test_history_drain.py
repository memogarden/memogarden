"""A history backlog larger than one run's page budget drains across runs.

Until 2026-11-26 the incremental walk raised when more than 20 pages of
change history had accumulated since the stored cursor, and left the cursor
alone — so every later run began from the same place and failed identically,
forever. One production account sat in that state for five days after a bulk
mailbox change. These tests pin the resumable walk in both sync paths.
"""

import json

import pytest

from conftest import simple_message
from gmail_importer.gmail_api import HISTORY_PAGES, GmailApiError, walk_history
from gmail_importer.importer import GmailImporter
from test_incremental import make_importer
from test_rfc012_backfill import ACCOUNT, _make_provider, _run, _state


def one_record_pages(record_ids, prefix="m"):
    """One history record per page, each adding one message."""
    return [
        [{"id": str(rid), "messagesAdded": [{"message": {"id": f"{prefix}{rid}"}}]}]
        for rid in record_ids
    ]


class PagedHistoryApi:
    """Scripted, paging history + a newest-first mailbox listing.

    `walks` maps a start historyId to the list of pages a walk from it
    returns; an unknown start id means "no changes since".
    """

    def __init__(self, walks, current="900", listing=(), account=ACCOUNT):
        self.walks = walks
        self.current = current
        self.listing = list(listing)
        self.account = account
        self.fetched: list[str] = []
        self.starts: list[str] = []   # every cursor a walk began from
        self.list_calls = 0

    def get_profile(self):
        return {"historyId": self.current, "emailAddress": self.account}

    def history_list(self, start_history_id, page_token=None):
        if start_history_id not in self.walks:
            return {"history": [], "historyId": self.current}
        if not page_token:
            self.starts.append(start_history_id)
        pages = self.walks[start_history_id]
        index = int(page_token) if page_token else 0
        resp = {"history": pages[index], "historyId": self.current}
        if index + 1 < len(pages):
            resp["nextPageToken"] = str(index + 1)
        return resp

    def list_messages(self, query=None, page_token=None):
        self.list_calls += 1
        return {"messages": [{"id": i} for i in self.listing]}

    def iter_message_ids(self, query=None, max_pages=12):
        self.list_calls += 1
        return list(self.listing)

    def get_message(self, message_id):
        self.fetched.append(message_id)
        msg = simple_message()
        msg["id"] = message_id
        msg["labelIds"] = ["INBOX"]
        msg["payload"] = {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
                {"name": "From", "value": "x@example.com"},
                {"name": "To", "value": "y@example.com"},
                {"name": "Subject", "value": f"Mail {message_id}"},
            ],
            "body": {"data": "SGk="},
        }
        return msg


class TestWalkHistory:
    def test_reads_to_the_budget_and_reports_the_resume_point(self):
        pages = one_record_pages(range(101, 126))            # 25 pages
        records, complete, last_id = walk_history(PagedHistoryApi({"100": pages}), "100")
        assert complete is False
        assert last_id == "120"                              # the 20th record
        assert [r["id"] for r in records] == [str(i) for i in range(101, 121)]

    def test_a_walk_that_ends_inside_the_budget_is_complete(self):
        pages = one_record_pages(range(101, 104))
        records, complete, last_id = walk_history(PagedHistoryApi({"100": pages}), "100")
        assert (complete, last_id, len(records)) == (True, "103", 3)

    def test_no_record_id_to_resume_from(self):
        pages = [[{"messagesAdded": [{"message": {"id": "a"}}]}] for _ in range(25)]
        _records, complete, last_id = walk_history(PagedHistoryApi({"100": pages}), "100")
        assert (complete, last_id) == (False, None)


class TestProviderDrain:
    """The in-server provider path (what the hourly heartbeat runs)."""

    def test_a_backlog_beyond_the_page_budget_drains_across_runs(
            self, tmp_path, monkeypatch):
        api = PagedHistoryApi({
            "100": one_record_pages(range(101, 126)),   # 25 pages since the cursor
            "120": one_record_pages(range(121, 126)),   # what Gmail returns after 120
        })
        provider = _make_provider(tmp_path, batch_limit=100)
        (tmp_path / "state.json").write_text(json.dumps(
            {"account": ACCOUNT, "history_id": "100", "backfill": "complete"}
        ))
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        # Run 1: the 20 pages the budget allows, then the cursor resumes at
        # the last record read — it does not jump to the mailbox's present
        first = _run(provider, tmp_path)
        assert first == [f"m{i}" for i in range(101, 121)]
        assert _state(tmp_path)["history_id"] == "120"

        # Run 2: the remaining pages; the walk completes and jumps to present
        api.fetched.clear()
        second = _run(provider, tmp_path)
        assert second == [f"m{i}" for i in range(121, 126)]
        assert _state(tmp_path)["history_id"] == "900"
        assert api.starts == ["100", "120"]

    def test_a_drain_cut_by_the_batch_cap_keeps_the_cursor(
            self, tmp_path, monkeypatch):
        """More new mail in the 20 pages than one batch may deliver: the
        cursor stays put until every candidate those pages hold has been
        delivered (the done-set absorbs what already landed)."""
        api = PagedHistoryApi({"100": one_record_pages(range(101, 126))})
        provider = _make_provider(tmp_path, batch_limit=5)
        (tmp_path / "state.json").write_text(json.dumps(
            {"account": ACCOUNT, "history_id": "100", "backfill": "complete"}
        ))
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        delivered, runs = [], 0
        while _state(tmp_path)["history_id"] == "100" and runs < 10:
            delivered += _run(provider, tmp_path)
            runs += 1

        assert runs == 4                                  # 20 pages' mail in batches of 5
        assert delivered == [f"m{i}" for i in range(101, 121)]   # each exactly once
        assert _state(tmp_path)["history_id"] == "120"

    def test_backfill_waits_for_the_history_backlog(self, tmp_path, monkeypatch):
        """New mail first: the reverse-chronological backfill stage does not
        run while a history backlog is still draining."""
        api = PagedHistoryApi(
            {"100": one_record_pages(range(101, 126))}, listing=["old1", "old2"]
        )
        provider = _make_provider(tmp_path, batch_limit=100)
        (tmp_path / "state.json").write_text(json.dumps(
            {"account": ACCOUNT, "history_id": "100", "backfill": "running"}
        ))
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        first = _run(provider, tmp_path)
        assert first == [f"m{i}" for i in range(101, 121)]
        assert api.list_calls == 0, "enumerated the mailbox mid-drain"

        api.walks["120"] = one_record_pages(range(121, 126))
        second = _run(provider, tmp_path)
        assert second[:5] == [f"m{i}" for i in range(121, 126)]
        assert "old1" in second and "old2" in second      # backlog done → backfill

    def test_pages_without_record_ids_fail_loudly(self, tmp_path, monkeypatch):
        """Unread history must never be skipped: pages past the budget that
        carry no record id are an error, not a silent jump to the present."""
        pages = [[{"messagesAdded": [{"message": {"id": "a"}}]}] for _ in range(25)]
        provider = _make_provider(tmp_path, batch_limit=100)
        (tmp_path / "state.json").write_text(json.dumps(
            {"account": ACCOUNT, "history_id": "100", "backfill": "complete"}
        ))
        api = PagedHistoryApi({"100": pages})
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        with pytest.raises(GmailApiError, match="no record id to resume from"):
            _run(provider, tmp_path)
        assert _state(tmp_path)["history_id"] == "100"


class TestImporterDrain:
    """The importer's own incremental sync (the CLI path)."""

    def test_a_budgeted_walk_resumes_where_it_stopped(self, tmp_path, monkeypatch,
                                                     importer):
        state_path = tmp_path / "gmail-sync-state.json"
        state_path.write_text(json.dumps(
            {"account": "t@example.com", "history_id": "100"}
        ))
        api = PagedHistoryApi(
            {"100": one_record_pages(range(101, 126)),
             "120": one_record_pages(range(121, 126))},
            account="t@example.com",
        )
        imp: GmailImporter = make_importer(importer.soil, state_path)
        monkeypatch.setattr(imp, "_build_api_client", lambda: api)

        imp.import_incremental(limit=100, verbose=False)
        assert json.loads(state_path.read_text())["history_id"] == "120"

        imp.import_incremental(limit=100, verbose=False)
        assert json.loads(state_path.read_text())["history_id"] == "900"


def test_the_budget_is_the_documented_twenty_pages():
    assert HISTORY_PAGES == 20
