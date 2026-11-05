"""Tests for deleted-at-source message handling: 404 is mail lifecycle,
not an error; excluded ids stop occupying batch slots."""

import json

import pytest

from conftest import simple_message
from gmail_importer import importer as importer_module
from gmail_importer.gmail_api import GmailApiError
from gmail_importer.importer import GmailImporter


class DeletedMailApi:
    """The mailbox lists a deleted message; fetching it 404s.

    One live message (id 'live') plus one deleted ('gone' — 404 on fetch,
    and surfaced as messagesDeleted in history).
    """

    def __init__(self, current="900"):
        self.current = current
        self.fetched: list[str] = []

    def get_profile(self):
        return {"historyId": self.current}

    def history_list(self, start_history_id, page_token=None):
        return {
            "history": [
                {
                    "messagesAdded": [{"message": {"id": "gone"}}],
                    "messagesDeleted": [{"message": {"id": "gone"}}],
                    "messagesAdded": [{"message": {"id": "live"}}],
                }
            ],
            "historyId": self.current,
        }

    def get_message(self, message_id):
        self.fetched.append(message_id)
        if message_id == "gone":
            raise GmailApiError("Gmail API error 404 on /messages/gone", status_code=404)
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
            "body": {"data": "SGk="},
        }
        return msg


def make_importer(soil, state_path, excluded_path):
    with soil:
        imp = GmailImporter(
            soil=soil,
            account_id="t@example.com",
            hacm_path="/tmp/x.enc",
            sync_state_path=str(state_path),
            excluded_ids_path=str(excluded_path),
        )
    return imp


class TestDeletedHandling:
    def test_fetch_404_is_not_an_error_and_excludes_id(self, importer, tmp_path, monkeypatch):
        """Progressive run: the deleted id is skipped as mail lifecycle,
        counted separately from errors, and persisted to the exclusion file."""
        state_path = tmp_path / "gmail-sync-state.json"
        excluded_path = tmp_path / "gmail-excluded-ids.json"
        imp = make_importer(importer.soil, state_path, excluded_path)
        api = DeletedMailApi()
        monkeypatch.setattr(imp, "_build_api_client", lambda: api)
        monkeypatch.setattr(
            importer_module,
            "find_oldest_message_ids",
            lambda api_, target, verbose, page_budget: ["gone", "live"],
        )
        stats = imp.import_next(limit=10, verbose=False)
        assert stats["deleted"] == 1
        assert stats["errors"] == 0
        assert stats["imported"] == 1  # 'live' still ingested
        assert set(api.fetched) == {"gone", "live"}
        excluded = json.loads(excluded_path.read_text())["ids"]
        assert "gone" in excluded

    def test_excluded_id_never_fetched_again(self, importer, tmp_path, monkeypatch):
        state_path = tmp_path / "gmail-sync-state.json"
        excluded_path = tmp_path / "gmail-excluded-ids.json"
        excluded_path.parent.mkdir(parents=True, exist_ok=True)
        excluded_path.write_text(json.dumps({"ids": ["gone"]}))

        imp = make_importer(importer.soil, state_path, excluded_path)
        api = DeletedMailApi()
        monkeypatch.setattr(imp, "_build_api_client", lambda: api)
        monkeypatch.setattr(
            importer_module,
            "find_oldest_message_ids",
            lambda api_, target, verbose, page_budget: ["gone", "live"],
        )
        stats = imp.import_next(limit=10, verbose=False)
        assert api.fetched == ["live"]  # 'gone' never fetched
        assert stats["deleted"] == 0
        assert stats["imported"] == 1

    def test_incremental_messages_deleted_excluded(self, importer, tmp_path, monkeypatch):
        """history messagesDeleted records exclude ids before fetch."""
        state_path = tmp_path / "gmail-sync-state.json"
        excluded_path = tmp_path / "gmail-excluded-ids.json"
        state_path.write_text(json.dumps({"account": "t@example.com", "history_id": "400"}))

        history_map = {
            "400": {
                "history": [
                    {
                        "messagesDeleted": [{"message": {"id": "gone"}}],
                        "messagesAdded": [{"message": {"id": "live"}}],
                    }
                ],
                "historyId": "900",
            }
        }
        imp = make_importer(importer.soil, state_path, excluded_path)
        api = DeletedMailApi(current="900")
        monkeypatch.setattr(imp, "_build_api_client", lambda: api)
        stats = imp.import_incremental(limit=10, verbose=False)
        assert api.fetched == ["live"]  # 'gone' excluded via messagesDeleted
        assert stats["imported"] == 1
        assert stats["errors"] == 0
        assert "gone" in json.loads(excluded_path.read_text())["ids"]

    def test_other_fetch_errors_still_count(self, importer, tmp_path, monkeypatch):
        """Non-404 failures remain errors (retry-worthy, exit non-zero)."""
        state_path = tmp_path / "gmail-sync-state.json"
        excluded_path = tmp_path / "gmail-excluded-ids.json"

        class DownApi(DeletedMailApi):
            def get_message(self, message_id):
                raise GmailApiError("Gmail API error 503 on /messages/x", status_code=503)

        imp = make_importer(importer.soil, state_path, excluded_path)
        monkeypatch.setattr(imp, "_build_api_client", lambda: DownApi())
        monkeypatch.setattr(
            importer_module,
            "find_oldest_message_ids",
            lambda api_, target, verbose, page_budget: ["gone"],
        )
        stats = imp.import_next(limit=10, verbose=False)
        assert stats["errors"] == 1
        assert stats["deleted"] == 0
