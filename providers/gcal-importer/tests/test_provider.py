"""Engine tests for GoogleCalendarProvider (RFC-014 §4): seed, incremental,
tombstones, 410 reseed, audit diff, budgets, translation rules."""

import pytest

from gcal_importer.calendar_api import GCalApiError
from tests.conftest import FakeCalendarClient, make_event, read_state


def _key(provider, cid, event_id):
    return provider._key(cid, event_id)


def _seeded(provider, tmp_state):
    """Drive one seed run for e1, then reflect the (runtime-side) stored
    corpus into the fake etag map."""
    provider._client.pages["me@gmail.com"] = [
        {"items": [
            make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1"),
        ], "nextSyncToken": "T1"},
    ]
    list(provider.sync())
    provider._stored = {"me@gmail.com": {"e1": ("E1", None)}}


class TestSeed:
    def test_seed_yields_events_and_stores_token(self, provider, tmp_state):
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"},
                           {"dateTime": "2026-11-20T10:00:00Z"}, etag="E1"),
                make_event("e2", {"dateTime": "2026-11-21T09:00:00+01:00"},
                           etag="E2"),
            ], "nextPageToken": "p2"},
            {"items": [], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())

        assert len(yields) == 2
        first = yields[0]
        assert first["op"] == "upsert_entity"
        assert first["type"] == "Event"
        assert first["key"] == {
            "source": "google_calendar", "account": "me@gmail.com",
            "calendar_id": "me@gmail.com", "source_id": "e1",
        }
        # Timed events normalize to UTC; occurred_at is NEVER set.
        assert first["data"]["starts_at"] == "2026-11-20T09:00:00+00:00"
        assert "occurred_at" not in first["data"]
        assert first["metadata"]["etag"] == "E1"
        assert first["metadata"]["source"] == "google_calendar"
        assert first["metadata"]["calendar_summary"] == "Personal"

        state = read_state(tmp_state)
        assert state["calendars"]["me@gmail.com"]["sync_token"] == "T1"
        assert state["account"] == "me@gmail.com"

    def test_past_at_seed_marked_captured_after_fact(self, provider):
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("old", {"date": "2020-01-01"}, {"date": "2020-01-02"}),
                make_event("future", {"dateTime": "2027-06-01T09:00:00Z"},
                           {"dateTime": "2027-06-01T10:00:00Z"}),
            ], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        by_id = {y["data"]["source_id"]: y for y in yields}
        assert by_id["old"]["metadata"]["captured_after_fact"] is True
        assert "captured_after_fact" not in by_id["future"]["metadata"]

    def test_cleared_fields_translated_as_null(self, provider):
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("bare", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1"),
            ], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        data = yields[0]["data"]
        # Explicit nulls (RFC-014 §3.2 merge semantics): absent optionals
        # mean "cleared at source", and the runtime merge needs the null.
        assert data["title"] is None
        assert data["location"] is None
        assert data["participants"] is None
        assert yields[0]["metadata"]["attendee_count"] is None

    def test_date_only_events_stay_bare_dates(self, provider):
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                # One-day date-only event: end date is EXCLUSIVE (next day).
                make_event("day", {"date": "2026-12-24"}, {"date": "2026-12-25"}),
            ], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        data = yields[0]["data"]
        assert data["starts_at"] == "2026-12-24"
        assert data["ends_at"] == "2026-12-25"  # verbatim exclusive, not converted

    def test_owner_role_default_excludes_subscribed(self, provider, tmp_state):
        subscribed = {"id": "holidays", "summary": "Holidays", "accessRole": "reader"}
        provider._client = FakeCalendarClient(
            calendars=[provider._client.calendar_entries[0], subscribed],
            pages={"me@gmail.com": [{"items": [], "nextSyncToken": "T1"}]},
        )
        yields = list(provider.sync())
        assert yields == []
        state = read_state(tmp_state)
        assert set(state["calendars"]) == {"me@gmail.com"}

    def test_allowlist_overrides_owner_default(self, provider, tmp_state):
        reader = {"id": "holidays", "summary": "Holidays", "accessRole": "reader"}
        provider._client = FakeCalendarClient(
            calendars=[provider._client.calendar_entries[0], reader],
            pages={
                "holidays": [{"items": [], "nextSyncToken": "TH"}],
            },
        )
        provider._calendar_ids = ["holidays"]
        yields = list(provider.sync())
        assert yields == []
        state = read_state(tmp_state)
        assert set(state["calendars"]) == {"holidays"}

    def test_seed_page_budget_truncation_defers_token(self, provider, tmp_state):
        provider._page_budget = 1
        provider._client.pages["me@gmail.com"] = [
            {"items": [make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"})],
             "nextPageToken": "p2"},
            {"items": [], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        assert len(yields) == 1
        # Deferred, never failed: generator completed, but the token of a
        # truncated walk is NOT stored.
        state = read_state(tmp_state)
        assert state["calendars"]["me@gmail.com"].get("sync_token") is None

    def test_participants_capped_with_count(self, provider):
        attendees = [{"email": f"p{i}@x.com"} for i in range(60)]
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("big", {"dateTime": "2026-11-20T09:00:00Z"},
                           attendees=attendees),
            ], "nextSyncToken": "T1"},
        ]
        provider._participants_max = 50
        yields = list(provider.sync())
        data = yields[0]["data"]
        assert len(data["participants"]) == 50
        assert yields[0]["metadata"]["attendee_count"] == 60

    def test_self_response_extracted(self, provider):
        attendees = [
            {"email": "a@x.com"},
            {"email": "me@gmail.com", "self": True, "responseStatus": "accepted"},
        ]
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("ev", {"dateTime": "2026-11-20T09:00:00Z"},
                           attendees=attendees, organizer={"email": "a@x.com"}),
            ], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        assert yields[0]["metadata"]["self_response"] == "accepted"
        assert yields[0]["data"]["organizer"] == "a@x.com"

    def test_recurring_instance_metadata(self, provider):
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event(
                    "inst_1", {"dateTime": "2026-11-20T09:00:00Z"},
                    recurringEventId="master1",
                    originalStartTime={"dateTime": "2026-11-20T08:00:00Z"},
                    iCalUID="master1@google.com",
                ),
            ], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        meta = yields[0]["metadata"]
        assert meta["recurring_event_id"] == "master1"
        assert meta["original_start_at"] == "2026-11-20T08:00:00+00:00"
        assert meta["ical_uid"] == "master1@google.com"

    def test_seed_resumes_past_known_events(self, provider, tmp_state):
        # Live-found defect: a truncated seed re-walks from page 1 next
        # run — without client-side skips, every continuation run burns
        # its whole batch budget re-delivering known events and never
        # reaches new ones.
        listing = {"items": [
            make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1"),
            make_event("e2", {"dateTime": "2026-11-21T09:00:00Z"}, etag="E2"),
            make_event("e3", {"dateTime": "2026-11-22T09:00:00Z"}, etag="E3"),
        ], "nextSyncToken": "T1"}
        provider._batch_limit = 2
        provider._client.pages["me@gmail.com"] = [dict(listing)]
        y1 = list(provider.sync())
        assert [y["data"]["source_id"] for y in y1] == ["e1", "e2"]

        # Runtime stored e1/e2 — reflected in the (Core-projected) map.
        # Run 2 re-lists the calendar (Google serves the full listing on
        # every walk); the known events are skipped client-side.
        provider._stored = {"me@gmail.com": {"e1": ("E1", None), "e2": ("E2", None)}}
        provider._client.pages["me@gmail.com"] = [dict(listing)]
        y2 = list(provider.sync())
        assert [y["data"]["source_id"] for y in y2] == ["e3"]
        # Full listing walked → token finally stored.
        assert read_state(tmp_state)["calendars"]["me@gmail.com"]["sync_token"] == "T1"

    def test_seed_known_cancelled_still_yields(self, provider, tmp_state):
        # Resurrection guard on the skip: a stored-cancelled id whose
        # listing entry is living must NOT be skipped even on etag match
        # (the living payload clears the stamps via the merge).
        provider._stored = {"me@gmail.com": {"e1": ("E1", "cancelled")}}
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1"),
            ], "nextSyncToken": "T1"},
        ]
        yields = list(provider.sync())
        assert len(yields) == 1
        assert yields[0]["metadata"]["source_status"] is None


class TestIncremental:
    def test_incremental_yields_changes_and_advances_token(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T10:00:00Z"}, etag="E2"),
            ], "nextSyncToken": "T2"},
        ]
        yields = list(provider.sync())
        assert len(yields) == 1
        assert yields[0]["data"]["starts_at"] == "2026-11-20T10:00:00+00:00"
        assert read_state(tmp_state)["calendars"]["me@gmail.com"]["sync_token"] == "T2"

    def test_known_tombstone_yields_stamp_only_upsert(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"},
                           status="cancelled"),
            ], "nextSyncToken": "T2"},
        ]
        yields = list(provider.sync())
        assert len(yields) == 1
        stamp = yields[0]
        assert stamp["data"] == {}  # stamp-only: beliefs untouched by the merge
        assert stamp["metadata"]["source_status"] == "cancelled"
        assert stamp["metadata"]["detection_source"] == "sync_tombstone"
        assert stamp["metadata"]["etag"] == "E1"
        assert stamp["metadata"]["removed_at_source"] == "2026-11-16T12:00:00+00:00"

    def test_unknown_tombstone_skipped(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        provider._stored = {"me@gmail.com": {}}  # we never stored e9
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e9", {"dateTime": "2026-11-20T09:00:00Z"},
                           status="cancelled"),
            ], "nextSyncToken": "T2"},
        ]
        assert list(provider.sync()) == []

    def test_410_clears_token_reseeds_and_forces_audit(self, provider, tmp_state):
        _seeded(provider, tmp_state)

        original = provider._client.events_list_page

        def gone_then_pages(calendar_id, params):
            if params.get("syncToken"):
                raise GCalApiError("410 fullSyncRequired", status_code=410)
            return original(calendar_id, params)

        provider._client.events_list_page = gone_then_pages
        provider._client.pages["me@gmail.com"] = [
            # Page for the post-410 reseed…
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1b"),
                make_event("e2", {"dateTime": "2026-11-21T09:00:00Z"}, etag="E5"),
            ], "nextSyncToken": "T5"},
            # …and a page for the forced audit's full listing (both events
            # still present → seen covers the known set → no stamps).
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1b"),
                make_event("e2", {"dateTime": "2026-11-21T09:00:00Z"}, etag="E5"),
            ], "nextSyncToken": "T5"},
        ]
        yields = list(provider.sync())
        # Reseed yielded both events, the forced audit re-listed both
        # (noop-absorbable) — no removal stamps anywhere.
        assert len(yields) == 4
        assert all(y["metadata"].get("source_status") is None for y in yields)
        state = read_state(tmp_state)
        assert state["calendars"]["me@gmail.com"]["sync_token"] == "T5"
        assert state["audit_last"] == "2026-11-16"

    def test_living_payload_clears_removal_triple(self, provider, tmp_state):
        # Resurrection (review P1-1): a living payload for a previously
        # stamped id carries explicit Nones so the merge sheds the stamps.
        _seeded(provider, tmp_state)
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"},
                           status="cancelled"),
            ], "nextSyncToken": "T2"},
        ]
        stamps = list(provider.sync())
        assert stamps[0]["metadata"]["source_status"] == "cancelled"

        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E2"),
            ], "nextSyncToken": "T3"},
        ]
        yields = list(provider.sync())
        meta = yields[0]["metadata"]
        assert meta["source_status"] is None
        assert meta["removed_at_source"] is None
        assert meta["detection_source"] is None

    def test_incremental_upsert_budget_defers_token(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        provider._batch_limit = 1
        provider._client.pages["me@gmail.com"] = [
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E2"),
                make_event("e2", {"dateTime": "2026-11-21T09:00:00Z"}, etag="E3"),
            ], "nextSyncToken": "T2"},
        ]
        yields = list(provider.sync())
        assert len(yields) == 1  # deferred, never failed
        # The truncated delta never reached nextSyncToken → token unchanged.
        assert read_state(tmp_state)["calendars"]["me@gmail.com"]["sync_token"] == "T1"

    def test_auth_failure_propagates_with_flag(self, provider, tmp_state):
        _seeded(provider, tmp_state)

        def unauthorized(calendar_id, params):
            raise GCalApiError("401", status_code=401, auth_failure=True)

        provider._client.events_list_page = unauthorized
        with pytest.raises(GCalApiError) as excinfo:
            list(provider.sync())
        assert excinfo.value.auth_failure is True
        # Nothing persisted: the cursor stays at T1.
        assert read_state(tmp_state)["calendars"]["me@gmail.com"]["sync_token"] == "T1"


class TestAudit:
    def test_audit_diff_yields_stamp_for_silently_vanished(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        # e1 stored but absent from the listing (silent vanish).
        provider._client.pages["me@gmail.com"] = [
            {"items": [], "nextSyncToken": "TX"},
        ]
        provider._audit_interval_days = 0  # force due
        yields = list(provider.sync())
        assert len(yields) == 1
        stamp = yields[0]
        assert stamp["metadata"]["detection_source"] == "audit_diff"
        assert stamp["metadata"]["source_status"] == "cancelled"
        assert stamp["data"] == {}

    def test_audit_skips_already_cancelled(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        provider._stored = {"me@gmail.com": {"e1": ("E1", "cancelled")}}
        provider._client.pages["me@gmail.com"] = [
            {"items": [], "nextSyncToken": "TX"},
        ]
        provider._audit_interval_days = 0
        assert list(provider.sync()) == []

    def test_truncated_audit_diffs_nothing_and_stays_due(self, provider, tmp_state):
        # Review P0-1: a truncated audit must NOT diff a partial listing
        # (false vanish stamps are unrepairable) and must not record
        # itself done — it re-runs next sync.
        _seeded(provider, tmp_state)
        provider._fixed_now = "2026-12-16T12:00:00+00:00"  # 30 days → due
        provider._page_budget = 0  # every listing truncates immediately
        provider._client.pages["me@gmail.com"] = [
            {"items": [], "nextSyncToken": "TX"},
        ]
        provider._audit_interval_days = 0
        yields = list(provider.sync())
        assert yields == []  # no stamps from a zero-page "listing"
        state = read_state(tmp_state)
        assert state["audit_last"] == "2026-11-16"  # unchanged → still due

    def test_audit_picks_up_newly_materialized_instances(self, provider, tmp_state):
        _seeded(provider, tmp_state)
        provider._client.pages["me@gmail.com"] = [
            # Incremental: a new instance of an (unchanged) master appears.
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1"),
                make_event("new_inst", {"dateTime": "2027-01-05T09:00:00Z"},
                           etag="E9"),
            ], "nextSyncToken": "TX"},
            # Audit listing right after: both still present → no stamps.
            {"items": [
                make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"}, etag="E1"),
                make_event("new_inst", {"dateTime": "2027-01-05T09:00:00Z"},
                           etag="E9"),
            ], "nextSyncToken": "TY"},
        ]
        provider._audit_interval_days = 0
        yields = list(provider.sync())
        ids = [y["data"]["source_id"] for y in yields]
        # Incremental delivered the new instance; the audit re-listed both.
        assert ids.count("new_inst") == 2
        assert all(y["metadata"].get("source_status") is None for y in yields)


class TestStateDiscipline:
    def test_subject_mismatch_resets_state(self, provider, tmp_state):
        from gcal_importer.state import store_state

        store_state(tmp_state, {"account": "someone-else@gmail.com",
                                "calendars": {"me@gmail.com": {"sync_token": "TPOISON"}}})
        provider._client.pages["me@gmail.com"] = [
            {"items": [], "nextSyncToken": "T1"},
        ]
        list(provider.sync())
        state = read_state(tmp_state)
        assert state["account"] == "me@gmail.com"
        assert state["calendars"]["me@gmail.com"]["sync_token"] == "T1"

    def test_abandoned_generator_persists_nothing(self, provider, tmp_state):
        provider._client.pages["me@gmail.com"] = [
            {"items": [make_event("e1", {"dateTime": "2026-11-20T09:00:00Z"})],
             "nextPageToken": "p2"},
            {"items": [], "nextSyncToken": "T1"},
        ]
        gen = provider.sync()
        next(gen)  # consume one yield, then abandon the generator
        gen.close()
        # Nothing persisted: the finally block advances state only when
        # the generator runs to completion (gmail/tada discipline).
        assert read_state(tmp_state) is None
