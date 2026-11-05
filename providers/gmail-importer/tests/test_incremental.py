"""Tests for historyId-based incremental sync."""

import json

import pytest
from system.soil import create_email_item

from conftest import simple_message
from gmail_importer.importer import GmailImporter


class FakeHistoryApi:
    """Scripted profile + history + messages for incremental tests.

    history_map maps start_history_id -> response; a missing start id
    raises a 404 GmailApiError (expired history). Messages carry per-id
    labelIds so TRASH/SPAM fetch-time filtering is exercised.
    """

    def __init__(self, history_map: dict, current: str = "900", labels: dict = None):
        self.history_map = history_map
        self.current = current
        self.labels = labels or {}
        self.fetched: list[str] = []

    def get_profile(self):
        return {"historyId": self.current, "emailAddress": "t@example.com"}

    def history_list(self, start_history_id, page_token=None):
        resp = self.history_map.get(start_history_id)
        if resp is None:
            from gmail_importer.gmail_api import GmailApiError

            raise GmailApiError("Gmail API error 404 ...", status_code=404)
        return resp

    def get_message(self, message_id):
        self.fetched.append(message_id)
        msg = simple_message()
        msg["id"] = message_id
        msg["labelIds"] = self.labels.get(message_id, ["INBOX"])
        msg["payload"] = {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
                {"name": "From", "value": "x@example.com"},
                {"name": "To", "value": "y@example.com"},
                {"name": "Subject", "value": f"New mail {message_id}"},
            ],
            "body": {"data": "SGk="},  # "Hi"
        }
        return msg


def make_importer(soil, state_path) -> GmailImporter:
    # Construction builds the dedup index — requires the Soil context.
    with soil:
        imp = GmailImporter(
            soil=soil,
            account_id="t@example.com",
            hacm_path="/tmp/x.enc",
            sync_state_path=str(state_path),
        )
    return imp


class TestIncrementalSync:
    def test_seed_then_diff_then_filters(self, importer, tmp_path, monkeypatch):
        """The realistic sequence: seed, diff with trash + rescue + noise,
        then a quiet no-op run."""
        state_path = tmp_path / "gmail-sync-state.json"

        def run(history_map, current="900", labels=None):
            api = FakeHistoryApi(history_map, current=current, labels=labels)
            imp = make_importer(importer.soil, state_path)
            monkeypatch.setattr(imp, "_build_api_client", lambda: api)
            stats = imp.import_incremental(limit=10, verbose=False)
            state = json.loads(state_path.read_text())
            return api, stats, state

        # Run 1: seeds at the current historyId (400), imports nothing
        api1, stats1, state1 = run(
            {"400": {"history": [], "historyId": "400"}}, current="400"
        )
        assert stats1["imported"] == 0
        assert state1["history_id"] == "400"
        assert api1.fetched == []

        # Run 2: diff 400->900 — adds n1..n3 plus a rescue (labelsRemoved n4);
        # n2 carries a TRASH label at fetch time so the policy drops it.
        history = {
            "400": {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "n1"}},
                            {"message": {"id": "n2"}},
                            {"message": {"id": "n3"}},
                        ]
                    },
                    {"labelsRemoved": [{"message": {"id": "n4"}}]},
                ],
                "historyId": "900",
            }
        }
        labels = {"n2": ["TRASH"], "n3": ["CATEGORY_PROMOTIONS"], "n4": ["INBOX"]}
        api2, stats2, state2 = run(history, current="900", labels=labels)
        assert api2.fetched == ["n1", "n2", "n3", "n4"]  # rescue included
        assert stats2["imported"] == 3
        assert stats2["skipped"] == 1  # the TRASH one
        assert state2["history_id"] == "900"

        # Run 3: no changes since 900 — clean no-op
        api3, stats3, _ = run({"900": {"history": [], "historyId": "900"}})
        assert stats3["imported"] == 0
        assert api3.fetched == []

    def test_burst_larger_than_limit_leaves_state_untouched(
        self, importer, tmp_path, monkeypatch
    ):
        """Candidates beyond the batch cap re-deliver next run (state not
        advanced), so the cap never loses mail."""
        state_path = tmp_path / "gmail-sync-state.json"

        class BurstApi(FakeHistoryApi):
            def __init__(self):
                super().__init__({})

            def history_list(self, start_history_id, page_token=None):
                return {
                    "history": [
                        {
                            "messagesAdded": [
                                {"message": {"id": f"b{i}"}} for i in range(5)
                            ]
                        }
                    ],
                    "historyId": "900",
                }

        def run(limit):
            api = BurstApi()
            imp = make_importer(importer.soil, state_path)
            monkeypatch.setattr(imp, "_build_api_client", lambda: api)
            stats = imp.import_incremental(limit=limit, verbose=False)
            state = json.loads(state_path.read_text()) if state_path.exists() else None
            return api, stats, state

        _, s_seed, state_seed = run(2)  # seeds, imports nothing
        assert s_seed["imported"] == 0
        assert state_seed["history_id"] == "900"

        _, s1, state1 = run(2)
        assert s1["imported"] == 2  # burst of 5 > cap 2
        # state NOT advanced past seed — re-diff next run, dedup absorbs
        assert state1["history_id"] == "900"

        _, s2, _ = run(2)
        assert s2["imported"] == 2
        _, s3, _ = run(2)
        assert s3["imported"] == 1  # 2 of the re-delivered are deduped

        with importer.soil:
            total = len(importer.soil.list_items(_type="Email", limit=100))
        assert total == 5

    def test_expired_history_404_reseeds(self, importer, tmp_path, monkeypatch):
        state_path = tmp_path / "gmail-sync-state.json"
        state_path.write_text(json.dumps(
            {"account": "t@example.com", "history_id": "50"}
        ))

        imp = make_importer(importer.soil, state_path)
        monkeypatch.setattr(imp, "_build_api_client", lambda: FakeHistoryApi({}))
        stats = imp.import_incremental(limit=10, verbose=False)
        assert stats["imported"] == 0
        # reseeded at the current historyId; gap mail is covered by backfill
        assert json.loads(state_path.read_text())["history_id"] == "900"

    def test_wrong_account_state_reseeds(self, importer, tmp_path, monkeypatch):
        state_path = tmp_path / "gmail-sync-state.json"
        state_path.write_text(json.dumps(
            {"account": "other@example.com", "history_id": "123"}
        ))
        imp = make_importer(importer.soil, state_path)
        api = FakeHistoryApi({})
        monkeypatch.setattr(imp, "_build_api_client", lambda: api)
        stats = imp.import_incremental(limit=10, verbose=False)
        assert stats["imported"] == 0  # seeded, nothing diffed
        state = json.loads(state_path.read_text())
        assert state["account"] == "t@example.com"
        assert state["history_id"] == "900"
