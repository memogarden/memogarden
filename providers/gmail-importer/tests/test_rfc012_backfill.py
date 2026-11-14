"""Tests for the RFC-012 §6.3 ingestion-order policy: history backfill
walks a mailbox reverse-chronologically (most recent first) until the
oldest email is ingested.

Covers:
- a fresh account's first sync ingests the newest batch, in newest-first
  order, alongside the historyId seed
- subsequent syncs continue the walk backward; completion is recorded in
  the per-account sync state ("backfill": "complete")
- a completed backfill stops enumerating the mailbox
- new mail captured by the historyId stage is never double-yielded by the
  backfill stage in the same run
- a truncated incremental batch defers the backfill stage
- an account whose mailbox is already fully ingested completes the
  backfill without producing facts (legacy-account posture)
"""

import json

import pytest

from conftest import simple_message
from gmail_importer.provider import GmailProvider


ACCOUNT = "a@example.com"


def _make_provider(tmp_path, batch_limit=3):
    provider = GmailProvider(
        {
            "hacm_path": "/tmp/x.enc",
            "soil_path": str(tmp_path / "soil.db"),
            "sync_state_path": str(tmp_path / "state.json"),
            "excluded_ids_path": str(tmp_path / "excluded.json"),
            "search_cache_path": str(tmp_path / "cache.json"),
            "batch_limit": batch_limit,
        },
        account={"id": "acc1", "platform": "google", "subject": ACCOUNT,
                 "label": None, "credential_ref": f"google:{ACCOUNT}",
                 "config": {}},
    )
    return provider


class FakeListingApi:
    """History walk + a newest-first mailbox listing (messages.list order).

    `listing` is newest-first; get_message serves simple_message() shaped
    per id, optionally with labels (for TRASH/SPAM filtering).
    """

    def __init__(self, history_map=None, current="400", listing=(), labels=None):
        self.history_map = history_map or {}
        self.current = current
        self.listing = list(listing)
        self.labels = labels or {}
        self.fetched: list[str] = []
        self.list_calls = 0

    def get_profile(self):
        return {"historyId": self.current, "emailAddress": ACCOUNT}

    def history_list(self, start_history_id, page_token=None):
        resp = self.history_map.get(start_history_id)
        if resp is None:
            # default: no changes since that cursor (a real history walk
            # with nothing new returns exactly this shape)
            return {"history": [], "historyId": start_history_id}
        return resp

    def list_messages(self, query=None, page_token=None):
        self.list_calls += 1
        return {"messages": [{"id": i} for i in self.listing]}

    def iter_message_ids(self, query=None, max_pages=12):
        self.list_calls += 1  # a full enumeration happened (the real client
        return list(self.listing)  # walks list_messages pages to get here)

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
                {"name": "Subject", "value": f"Mail {message_id}"},
            ],
            "body": {"data": "SGk="},
        }
        return msg


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text())


def _run(provider, tmp_path):
    """Consume one sync like the runtime does: persist each yielded fact
    into Soil and return the yielded ids. Persistence uses its OWN Soil
    instance (as the real runtime does) — nesting `with` on the provider's
    instance breaks its context flag."""
    from system.soil import Soil, create_email_item

    out = []
    runtime_soil = Soil(tmp_path / "soil.db")
    with runtime_soil:
        for y in provider.sync():
            runtime_soil.create_fact(create_email_item(**y))
            out.append(y["metadata"]["gmail_message_id"])
    return out


class TestReverseChronBackfill:
    def test_first_sync_seeds_and_ingests_newest_batch(self, tmp_path, monkeypatch):
        """Fresh account: seed + the NEWEST batch_limit mails, newest-first."""
        provider = _make_provider(tmp_path, batch_limit=3)
        # messages.list is newest-first: head = most recent
        api = FakeListingApi(listing=["new1", "new2", "mid", "old2", "old1"])
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        yielded = _run(provider, tmp_path)
        assert yielded == ["new1", "new2", "mid"]  # newest-first, newest batch
        state = _state(tmp_path)
        assert state["history_id"] == "400"  # seeded
        assert state["backfill"] == "running"

    def test_backfill_walks_backward_across_syncs_then_completes(
        self, tmp_path, monkeypatch
    ):
        provider = _make_provider(tmp_path, batch_limit=3)
        api = FakeListingApi(listing=["new1", "new2", "mid", "old2", "old1"])
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        first = _run(provider, tmp_path)
        second = _run(provider, tmp_path)
        assert first == ["new1", "new2", "mid"]
        assert second == ["old2", "old1"]  # continues backward
        assert _state(tmp_path)["backfill"] == "complete"

        calls_after_complete = api.list_calls
        third = _run(provider, tmp_path)
        assert third == []  # complete: no more enumeration, no facts
        assert api.list_calls == calls_after_complete  # no re-enumeration

    def test_new_mail_not_double_yielded_with_backfill(self, tmp_path, monkeypatch):
        """Mail captured by the historyId stage must not be re-yielded by
        the backfill stage in the SAME run (it heads the newest-first
        listing, and neither the done-set nor the state can know yet)."""
        provider = _make_provider(tmp_path, batch_limit=3)
        api = FakeListingApi(listing=["n1", "n2", "n3", "n4", "n5"], current="400")
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        first = _run(provider, tmp_path)
        assert first == ["n1", "n2", "n3"]  # mid-backfill, not complete

        # new mail arrives: heads the listing AND shows in the history diff
        api.listing = ["fresh", "n1", "n2", "n3", "n4", "n5"]
        api.history_map["400"] = {
            "history": [{"messagesAdded": [{"message": {"id": "fresh"}}]}],
            "historyId": "900",
        }
        api.current = "900"
        yielded = _run(provider, tmp_path)
        assert yielded.count("fresh") == 1
        assert yielded[0] == "fresh"  # historyId stage served it
        state = _state(tmp_path)
        assert state["history_id"] == "900"
        assert state["backfill"] == "complete"  # fresh + n4 + n5 drained it

    def test_truncated_incremental_defers_backfill(self, tmp_path, monkeypatch):
        provider = _make_provider(tmp_path, batch_limit=2)
        # 3-item mailbox: the seed run backfills 2 of 3 — backfill stays
        # 'running' (a 2-item mailbox would complete during the seed run)
        api = FakeListingApi(listing=["old1", "old2", "old3"], current="400")
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        assert _run(provider, tmp_path) == ["old1", "old2"]  # seed + backfill batch
        assert _state(tmp_path)["backfill"] == "running"

        api.history_map["400"] = {
            "history": [
                {"messagesAdded": [{"message": {"id": f"n{i}"}} for i in range(4)]}
            ],
            "historyId": "900",
        }
        yielded = _run(provider, tmp_path)
        assert yielded == ["n0", "n1"]  # incremental batch only
        state = _state(tmp_path)
        assert state["history_id"] == "400"  # NOT advanced (truncated)
        assert state["backfill"] == "running"
        assert api.list_calls == 1  # backfill deferred — no re-enumeration

    def test_fully_ingested_mailbox_completes_without_facts(
        self, tmp_path, monkeypatch
    ):
        """Legacy-account posture: done-set already covers the mailbox; the
        one-time enumeration finds nothing new and completes."""
        from system.soil import create_email_item

        provider = _make_provider(tmp_path, batch_limit=3)
        api = FakeListingApi(listing=["m1", "m2", "m3"])
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)
        with provider.importer.soil:
            for mid in ("m1", "m2", "m3"):
                item = provider.importer.translate_gmail_to_email(
                    simple_message(), realized_at="2026-11-14T00:00:00+00:00"
                )
                item["metadata"]["gmail_message_id"] = mid
                item["metadata"]["account"] = ACCOUNT
                provider.importer.soil.create_fact(create_email_item(**item))

        yielded = _run(provider, tmp_path)
        assert yielded == []
        assert _state(tmp_path)["backfill"] == "complete"

    def test_transient_fetch_failure_blocks_completion(self, tmp_path, monkeypatch):
        """A fetch that fails inside the draining batch must not mark the
        walk complete — the id retries on the next run (RFC-012 §4.10)."""
        provider = _make_provider(tmp_path, batch_limit=3)
        api = FakeListingApi(listing=["n1", "n2", "n3"])
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        original_get = api.get_message

        def flaky(message_id):
            if message_id == "n3":
                raise RuntimeError("transient gmail hiccup")
            return original_get(message_id)

        api.get_message = flaky
        yielded = _run(provider, tmp_path)
        assert yielded == ["n1", "n2"]
        assert _state(tmp_path)["backfill"] == "running"  # NOT complete

        # heals on the next run
        api.get_message = original_get
        assert _run(provider, tmp_path) == ["n3"]
        assert _state(tmp_path)["backfill"] == "complete"

    def test_duplicate_history_candidates_yielded_once(self, tmp_path, monkeypatch):
        """An id present in both messagesAdded and labelsRemoved of one
        history window is yielded once (review P2-6)."""
        provider = _make_provider(tmp_path, batch_limit=5)
        api = FakeListingApi(
            history_map={
                "400": {
                    "history": [
                        {"messagesAdded": [{"message": {"id": "dupe"}}]},
                        {"labelsRemoved": [{"message": {"id": "dupe"}}]},
                    ],
                    "historyId": "900",
                }
            },
            current="400",
            listing=[],  # empty mailbox listing: backfill completes immediately
        )
        monkeypatch.setattr(provider.importer, "_build_api_client", lambda: api)

        list(provider.sync())  # seed at 400
        yielded = _run(provider, tmp_path)
        assert yielded == ["dupe"]  # exactly once


class TestReverseLinkThreading:
    """RFC-012 §4.10: reverse-chronological ingestion stores a reply
    BEFORE its parent — create_relations reverse-links stored replies to
    a parent when it finally lands, making threading eventual-consistent."""

    def _email(self, provider, rfc_id, in_reply_to=None, references=None):
        from conftest import simple_message

        item = provider.importer.translate_gmail_to_email(
            simple_message(), realized_at="2026-11-14T00:00:00+00:00"
        )
        item["data"]["rfc_message_id"] = rfc_id
        item["data"]["in_reply_to"] = in_reply_to
        item["data"]["references"] = references
        return item

    def test_parent_landing_links_stored_replies(self, tmp_path):
        from system.soil import create_email_item

        provider = _make_provider(tmp_path)
        reply = self._email(provider, "<reply@x>", in_reply_to="parent@x")
        with provider.importer.soil:
            reply_uuid = provider.importer.soil.create_fact(
                create_email_item(**reply)
            )

        # parent arrives AFTER the reply (reverse-chronological): the
        # fact-like parent has no in_reply_to of its own
        fact_like = type(
            "F", (), {"uuid": "runtime_parent", "data": {
                "rfc_message_id": "<parent@x>",
            }}
        )()
        relations = provider.create_relations(fact_like)
        assert len(relations) == 1
        rel = relations[0]
        assert rel.kind == "replies_to"
        assert rel.source == reply_uuid  # the stored reply
        assert rel.target == "runtime_parent"  # links to the newly landed parent

    def test_forward_threading_still_works(self, tmp_path):
        from system.soil import create_email_item

        provider = _make_provider(tmp_path)
        parent = self._email(provider, "<parent@x>")
        with provider.importer.soil:
            parent_uuid = provider.importer.soil.create_fact(
                create_email_item(**parent)
            )

        fact_like = type(
            "F", (), {"uuid": "runtime_reply", "data": {
                "rfc_message_id": "<reply@x>",
                "in_reply_to": "parent@x",
            }}
        )()
        relations = provider.create_relations(fact_like)
        assert len(relations) == 1
        assert relations[0].source == "runtime_reply"
        assert relations[0].target == parent_uuid

    def test_references_member_links_exactly(self, tmp_path):
        """A reply whose references list contains the parent links to it;
        an unrelated mail with a substring-overlapping id does not.
        (Stored references are angle-stripped, per parse_references.)"""
        from system.soil import create_email_item

        provider = _make_provider(tmp_path)
        reply = self._email(
            provider, "<reply@x>",
            references=["grandparent@x", "parent@x"],
        )
        decoy = self._email(provider, "<aparent@x-not>", references=["other@x"])
        with provider.importer.soil:
            reply_uuid = provider.importer.soil.create_fact(create_email_item(**reply))
            provider.importer.soil.create_fact(create_email_item(**decoy))

        fact_like = type(
            "F", (), {"uuid": "runtime_parent", "data": {
                "rfc_message_id": "<parent@x>",
            }}
        )()
        relations = provider.create_relations(fact_like)
        assert len(relations) == 1
        assert relations[0].source == reply_uuid  # decoy not linked
