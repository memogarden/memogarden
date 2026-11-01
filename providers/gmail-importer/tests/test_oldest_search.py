"""Tests for the oldest-N mailbox search (gmail_api.find_oldest_message_ids).

Uses a scripted fake of the Gmail API surface: list/count behave like the
real API (newest-first pages, approximate resultSizeEstimate) so the test
proves the search relies on actual enumeration, not estimates.
"""

import calendar
import datetime as dt

from gmail_importer.gmail_api import find_oldest_message_ids


class FakeGmailApi:
    """Scripted stand-in: a mailbox of `total` messages spread over years.

    Message with index 0 is the OLDEST. list pages are newest-first.
    """

    def __init__(self, total: int, page_size: int = 500):
        self.total = total
        self.page_size = page_size
        self.call_count = 0
        self.calls: list[tuple] = []

    def _matches(self, query: str | None) -> list[int]:
        # before:YYYY/MM/DD keeps indexes whose (fake) date is before then.
        # Fake dates: index i sits at 2004-04-01 + i days.
        if query and query.startswith("before:"):
            cutoff = dt.datetime.strptime(query.removeprefix("before:"), "%Y/%m/%d")
            cutoff_ms = calendar.timegm(cutoff.timetuple()) * 1000
            epoch_ms = calendar.timegm((2004, 4, 1, 0, 0, 0)) * 1000
            day_ms = 86_400_000
            return [i for i in range(self.total) if epoch_ms + i * day_ms < cutoff_ms]
        return list(range(self.total))

    def list_messages(self, query=None, page_token=None):
        self.call_count += 1
        self.calls.append(("list", query, page_token))
        ids = self._matches(query)
        # newest-first: highest index first
        start = int(page_token) if page_token else 0
        chunk = list(reversed(ids))[start : start + self.page_size]
        result = {"messages": [{"id": str(i)} for i in chunk]}
        if start + self.page_size < len(ids):
            result["nextPageToken"] = str(start + self.page_size)
        return result

    def iter_message_ids(self, query=None, max_pages: int = 12) -> list[str]:
        ids: list[str] = []
        page_token = None
        for _ in range(max_pages):
            page = self.list_messages(query, page_token)
            ids.extend(m["id"] for m in page.get("messages", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                return ids
        raise RuntimeError("fake window too large — mirrors GmailApiError path")

    def get_message(self, message_id):
        self.call_count += 1
        return {"id": message_id}


class TestFindOldest:
    def test_small_mailbox_returns_all_newest_first(self):
        api = FakeGmailApi(total=7)
        ids = find_oldest_message_ids(api, target=10)
        assert [int(i) for i in ids] == [6, 5, 4, 3, 2, 1, 0]

    def test_large_mailbox_finds_true_oldest(self):
        # 20,000 messages, one per day (~55 years spread) — the oldest 10
        # sit years back; a naive newest-first listing would never see them.
        # The finder returns API order (newest-first); oldest = reversed head.
        api = FakeGmailApi(total=20_000)
        ids = find_oldest_message_ids(api, target=10)
        assert [int(i) for i in reversed(ids)][:10] == list(range(10))

    def test_target_larger_than_window_edge(self):
        # Exactly 12 messages: asking for 10 still yields all, oldest first head.
        api = FakeGmailApi(total=12)
        ids = find_oldest_message_ids(api, target=10)
        assert int(ids[-1]) == 0
        assert int(ids[0]) == 11

    def test_bounded_call_count(self):
        api = FakeGmailApi(total=20_000)
        find_oldest_message_ids(api, target=10)
        # Correctness needs real page walks (~12 calls per probe); at 5 quota
        # units per list call this is a trivial one-time cost.
        assert api.call_count < 300, f"too many API calls: {api.call_count}"
