"""Tests for GmailProvider — the Provider Protocol shim."""

import json

import pytest

from conftest import simple_message
from gmail_importer.provider import GmailProvider


class FakeHistoryApi:
    def __init__(self, history_map, current="900", labels=None):
        self.history_map = history_map
        self.current = current
        self.labels = labels or {}
        self.fetched: list[str] = []
        # RFC-012 §6.3: the provider's backfill stage enumerates the mailbox
        # newest-first each sync until complete; an empty listing = fully
        # ingested = backfill completes immediately (preserving the
        # seed-only semantics these shim tests were written against).
        self.listing: list[str] = []
        self.list_calls = 0

    def get_profile(self):
        return {"historyId": self.current}

    def list_messages(self, query=None, page_token=None):
        self.list_calls += 1
        return {"messages": [{"id": i} for i in self.listing]}

    def iter_message_ids(self, query=None, max_pages=12):
        return list(self.listing)

    def history_list(self, start_history_id, page_token=None):
        resp = self.history_map.get(start_history_id)
        if resp is None:
            from gmail_importer.gmail_api import GmailApiError

            raise GmailApiError("404", status_code=404)
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
            "body": {"data": "SGk="},
        }
        return msg


@pytest.fixture
def env(tmp_path):
    config = {
        "account": "t@example.com",
        "hacm_path": "/tmp/x.enc",
        "soil_path": str(tmp_path / "soil.db"),
        "sync_state_path": str(tmp_path / "gmail-sync-state.json"),
        "excluded_ids_path": str(tmp_path / "gmail-excluded-ids.json"),
        "search_cache_path": str(tmp_path / "gmail-search-cache.json"),
        "batch_limit": 10,
    }
    provider = GmailProvider(config)
    return provider, config


def email_count(provider) -> int:
    with provider.importer.soil:
        return len(provider.importer.soil.list_items(_type="Email", limit=100))


class TestGmailProvider:
    def test_seed_run_yields_nothing_and_advances_state(self, env, monkeypatch):
        provider, config = env
        api = FakeHistoryApi({"400": {"history": [], "historyId": "400"}}, current="400")
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        yielded = list(provider.sync(since="ignored"))
        assert yielded == []
        state = json.loads(open(config["sync_state_path"]).read())
        assert state["history_id"] == "400"

    def test_diff_yields_runtime_normalized_dicts(self, env, monkeypatch):
        provider, config = env
        history = {
            "400": {
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "n1"}},
                            {"message": {"id": "n2"}},
                        ]
                    },
                    {"labelsRemoved": [{"message": {"id": "n3"}}]},
                ],
                "historyId": "900",
            }
        }
        labels = {"n2": ["SPAM"], "n3": ["INBOX"]}
        api = FakeHistoryApi(history, current="400", labels=labels)
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        assert list(provider.sync(since="ignored")) == []  # seed
        yielded = list(provider.sync(since="ignored"))
        # n2 (SPAM at fetch) filtered; n1 + rescued n3 yielded in runtime shape
        assert [y["metadata"]["gmail_message_id"] for y in yielded] == ["n1", "n3"]
        for y in yielded:
            assert y["type"] == "Email"
            assert "rfc_message_id" in y["data"]
            assert "gmail_message_id" in y["metadata"]
            assert "canonical_at" in y

    def test_batch_cap_keeps_state_on_seed(self, env, monkeypatch):
        """A burst larger than batch_limit: yields `limit` and the sync state
        is NOT advanced (the remainder re-delivers next sync)."""
        provider, config = env
        provider.batch_limit = 2

        class Burst(FakeHistoryApi):
            def __init__(self):
                super().__init__({}, current="400")

            def history_list(self, start_history_id, page_token=None):
                return {
                    "history": [
                        {"messagesAdded": [{"message": {"id": f"b{i}"}} for i in range(5)]}
                    ],
                    "historyId": "900",
                }

        api = Burst()
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        assert list(provider.sync(since=None)) == []  # seed
        run1 = list(provider.sync(since=None))
        assert len(run1) == 2
        state = json.loads(open(config["sync_state_path"]).read())
        assert state["history_id"] == "400"  # seed value kept

    def test_create_relations_threads_replies(self, env, monkeypatch):
        provider, config = env
        from system.soil import create_email_item

        with provider.importer.soil:
            parent = create_email_item(**{
                "_type": "Email",
                "realized_at": "2026-11-04T00:00:00+00:00",
                "canonical_at": "2026-11-04T00:00:00+00:00",
                "fidelity": "full",
                "data": {
                    "rfc_message_id": "<parent@example.com>",
                    "from_address": "a@b.c",
                    "to_addresses": ["d@e.f"],
                    "sent_at": "2026-11-04T00:00:00+00:00",
                    "title": "Parent",
                    "description": "body",
                    "content_type": "text/plain",
                },
                "metadata": {},
            })
            parent_uuid = provider.importer.soil.create_fact(parent)

        fact_like = type(
            "F", (), {"uuid": "runtime_child", "data": {
                "in_reply_to": "parent@example.com",
                "rfc_message_id": "<child@example.com>",
            }}
        )()
        relations = provider.create_relations(fact_like)
        assert len(relations) == 1
        rel = relations[0]
        assert rel.kind == "replies_to"
        assert rel.source == "runtime_child"
        assert rel.target == parent_uuid

    def test_no_parent_no_relations(self, env):
        provider, _ = env
        fact_like = type("F", (), {"uuid": "x", "data": {}})()
        assert provider.create_relations(fact_like) == []
