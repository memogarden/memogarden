"""Provider sync semantics: cursor, done-sets, crash repair, relations,
and the entity lane (Task/Recurrence upserts + Task→Event conversion)."""

import json
from datetime import date
from pathlib import Path

from conftest import FakeTadaClient, done_entry, make_provider, store_fact
from tada_importer.client import TadaApiError


def ingest_via_runtime(soil, provider, batch):
    """Run a provider batch the way the runtime does: store each yielded
    fact, then store the relations its create_relations returns. Entity
    yields (op dicts) pass through uncounted — their storage semantics
    (find_by_data + etag no-op + retire idempotency) live in the api."""
    facts, relations, ops = 0, 0, []
    for yielded in provider._sync_batch(batch):
        if yielded.get("op"):
            ops.append(yielded)
            continue
        fact = store_fact(soil, yielded)
        facts += 1
        for relation in (provider.create_relations(fact) or []):
            with soil as s:
                s.create_relation(relation)
            relations += 1
    return facts, relations, ops


def consume_sync(soil, provider):
    """Full sync() the way the runtime would: facts stored (+relations),
    entity yields collected in order."""
    facts, relations, ops = [], [], []
    for yielded in provider.sync():
        if yielded.get("op"):
            ops.append(yielded)
            continue
        fact = store_fact(soil, yielded)
        facts.append(fact)
        for relation in (provider.create_relations(fact) or []):
            with soil as s:
                s.create_relation(relation)
            relations.append(relation)
    return facts, relations, ops


def counts(soil):
    with soil as s:
        rows = s._get_connection().execute(
            "SELECT _type, COUNT(*) AS n FROM item GROUP BY _type"
        ).fetchall()
        rel = s._get_connection().execute(
            "SELECT COUNT(*) AS n FROM system_relation"
        ).fetchone()
    return {r["_type"]: r["n"] for r in rows} | {"relation": rel["n"]}


def test_batch_stores_event_note_and_relation(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(
        days={"2026-11-06": [done_entry(notes="the reasoning")]}))
    facts, relations, ops = ingest_via_runtime(soil, provider, ["2026-11-06"])
    assert (facts, relations) == (2, 1)
    # The retire rides whichever lane lands the Event — the fact lane here.
    assert [o["op"] for o in ops] == ["retire_entity"]
    assert counts(soil) == {"Event": 1, "Note": 1, "relation": 1}
    with soil as s:
        row = s._get_connection().execute(
            "SELECT kind, source_type, target_type FROM system_relation"
        ).fetchone()
    assert (row["kind"], row["source_type"], row["target_type"]) == \
        ("contained_by", "item", "item")


def test_done_set_skips_reingest(soil, state_path):
    client = FakeTadaClient(days={"2026-11-06": [done_entry()]})
    provider = make_provider(soil, state_path, client)
    ingest_via_runtime(soil, provider, ["2026-11-06"])
    facts, _, _ = ingest_via_runtime(soil, provider, ["2026-11-06"])
    assert facts == 0
    assert counts(soil) == {"Event": 1, "relation": 0}


def test_crash_repair_redelivers_only_the_note(soil, state_path):
    """A run that died between an Event and its Note: the Event is skipped,
    the Note re-delivers, and its relation resolves the stored Event."""
    client = FakeTadaClient(days={"2026-11-06": [done_entry(notes="kept notes")]})
    provider = make_provider(soil, state_path, client)
    batch = ["2026-11-06"]

    yielded = list(provider._sync_batch(batch))
    store_fact(soil, yielded[0])  # Event stored; the process "dies" before the Note lands

    facts, relations, _ = ingest_via_runtime(soil, provider, batch)
    assert facts == 1  # only the Note re-delivered
    assert relations == 1
    assert counts(soil) == {"Event": 1, "Note": 1, "relation": 1}


def test_show_failure_stores_entry_only(soil, state_path):
    entry = done_entry()
    client = FakeTadaClient(days={"2026-11-06": [entry]},
                            items={entry["uid"]: {"uid": entry["uid"]}},
                            fail_show={entry["uid"]})
    provider = make_provider(soil, state_path, client)
    facts, _, _ = ingest_via_runtime(soil, provider, ["2026-11-06"])
    assert facts == 1  # Event without a note, no crash
    assert counts(soil) == {"Event": 1, "relation": 0}


def test_cursor_advances_on_clean_completion(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(
        days={"2026-11-05": [], "2026-11-06": [done_entry()]}),
        backfill_since="2026-11-05")
    provider._fixed_today = date(2026, 11, 7)
    list(provider.sync())
    state = json.loads(Path(state_path).read_text())
    assert state == {"last_day": "2026-11-06"}


def test_today_excluded_from_fact_walk(soil, state_path):
    client = FakeTadaClient(days={"2026-11-07": [done_entry()]})
    provider = make_provider(soil, state_path, client,
                             backfill_since="2026-11-07")
    provider._fixed_today = date(2026, 11, 7)
    list(provider.sync())
    # Today's snapshot never polled by the day-walk (the entity lane's
    # inventory call is expected — sync() runs it every time).
    assert [args for op, args in client.calls if op == "list"] == []


def test_truncated_batch_defers_cursor(soil, state_path):
    client = FakeTadaClient(days={"2026-11-05": [done_entry()]})
    provider = make_provider(soil, state_path, client, batch_limit=2,
                             backfill_since="2026-11-05")
    provider._fixed_today = date(2026, 11, 9)
    # pending = 11-05..11-08 (4 days) -> batch caps at 2, cursor at 11-06
    list(provider.sync())
    assert json.loads(Path(state_path).read_text()) == {"last_day": "2026-11-06"}
    # next sync resumes where the cursor left off and finishes the window
    list(provider.sync())
    assert json.loads(Path(state_path).read_text()) == {"last_day": "2026-11-08"}
    assert [args["day"] for op, args in client.calls if op == "list"] == \
        ["2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"]


def test_pending_days_respects_backfill_since(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(),
                             backfill_since="2026-11-04")
    provider._fixed_today = date(2026, 11, 7)
    assert provider._pending_days(provider._fixed_today) == \
        ["2026-11-04", "2026-11-05", "2026-11-06"]


def test_backfill_walk_yields_event_then_retire(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(
        days={"2026-11-05": [done_entry()]}),
        backfill_since="2026-11-05")
    provider._fixed_today = date(2026, 11, 7)
    yielded = list(provider._sync_batch(["2026-11-05"]))
    assert yielded[0]["type"] == "Event"
    assert yielded[1]["op"] == "retire_entity"


# ---------------------------------------------------------------------------
# The entity lane
# ---------------------------------------------------------------------------

def lane_client():
    return FakeTadaClient(inventory_result={
        "items": [
            {"uid": "t-open-1", "title": "Buy earplugs", "kind": "task",
             "status": "confirmed", "timeZone": "Asia/Singapore", "tags": [],
             "updatedAt": "2026-11-19T08:00"},
            {"uid": "t-done-1", "title": "Fresh resolution", "kind": "task",
             "completedAt": "2026-11-19T07:30", "timeZone": "Asia/Singapore",
             "tags": [], "updatedAt": "2026-11-19T07:30"},
        ],
        "templates": [
            {"uid": "tmpl-1", "title": "Water plants", "kind": "task",
             "recurrenceRule": "FREQ=DAILY;INTERVAL=2",
             "start": "2026-11-15T09:00", "timeZone": "Asia/Singapore",
             "tags": [], "updatedAt": "2026-11-15T09:00"},
        ],
    })


def test_entity_lane_upserts_converts_and_retires(soil, state_path):
    provider = make_provider(soil, state_path, lane_client(),
                             backfill_since="2026-11-19")
    provider._fixed_today = date(2026, 11, 19)
    facts, relations, ops = consume_sync(soil, provider)

    # Conversion of the window-resolved item: Event + Note (notes absent ->
    # no Note), the retire rides after.
    assert [f._type for f in facts] == ["Event"]
    ops_kinds = [(o.get("op"), o.get("type")) for o in ops]
    assert ops_kinds == [
        ("upsert_entity", "Task"),        # open item
        ("upsert_entity", "Recurrence"),  # template
        ("retire_entity", "Task"),        # conversion retire
    ]
    upserts = [o for o in ops if o["op"] == "upsert_entity"]
    by_type = {o["type"]: o for o in upserts}
    assert by_type["Task"]["key"] == {"source": "tada", "source_id": "t-open-1"}
    assert by_type["Recurrence"]["key"] == {"source": "tada", "source_id": "tmpl-1"}
    assert by_type["Recurrence"]["data"]["rrule"] == "FREQ=DAILY;INTERVAL=2"
    event = facts[0]
    assert event.metadata["status"] == "completed"
    assert event.data["source_id"] == "t-done-1"


def test_conversion_dedups_against_day_walk(soil, state_path):
    """The day-walk landed the Event (it ran first); the entity lane must
    not duplicate it — but the retire still rides (idempotent)."""
    client = lane_client()
    # The day-walk walks YESTERDAY (today's snapshot is still mutating) —
    # the dedup scenario needs the walk day behind the inventory window.
    client.days = {"2026-11-18": [done_entry(uid="t-done-1")]}
    provider = make_provider(soil, state_path, client,
                             backfill_since="2026-11-18")
    provider._fixed_today = date(2026, 11, 19)
    facts, relations, ops = consume_sync(soil, provider)

    # Exactly ONE Event for the uid (the day-walk's); the lane yields no
    # second one.
    event_uids = [f.data["source_id"] for f in facts if f._type == "Event"]
    assert event_uids.count("t-done-1") == 1
    # Two retires (one per lane) — idempotent noops on the runtime side.
    retires = [o for o in ops if o["op"] == "retire_entity"
               and o["key"]["source_id"] == "t-done-1"]
    assert len(retires) == 2
    assert counts(soil) == {"Event": 1, "relation": 0}


def test_inventory_failure_defers_entity_lane(soil, state_path):
    """The entity lane's transport failure must not poison the fact lane —
    deferred, not failed (the next heartbeat retries)."""
    client = FakeTadaClient(days={"2026-11-06": [done_entry()]})

    class DyingInventory(client.__class__):
        def inventory(self, resolved_within=2):
            raise TadaApiError("inventory down", status_code=503)

    dying = DyingInventory(days=client.days)
    provider = make_provider(soil, state_path, dying,
                             backfill_since="2026-11-06")
    provider._fixed_today = date(2026, 11, 7)
    facts, relations, ops = consume_sync(soil, provider)
    assert [f._type for f in facts] == ["Event"]  # fact lane unaffected
    assert ops and ops[0]["op"] == "retire_entity"  # fact lane's own retire
    assert not any(o["op"] == "upsert_entity" for o in ops)  # lane deferred
