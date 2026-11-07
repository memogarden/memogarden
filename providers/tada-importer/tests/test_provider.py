"""Provider sync semantics: cursor, done-sets, crash repair, relations."""

import json
from datetime import date
from pathlib import Path

from conftest import FakeTadaClient, done_entry, make_provider, store_fact


def ingest_via_runtime(soil, provider, batch):
    """Run a provider batch the way the runtime does: store each yielded
    fact, then store the relations its create_relations returns."""
    facts, relations = 0, 0
    for yielded in provider._sync_batch(batch):
        fact = store_fact(soil, yielded)
        facts += 1
        for relation in (provider.create_relations(fact) or []):
            with soil as s:
                s.create_relation(relation)
            relations += 1
    return facts, relations


def counts(soil):
    with soil as s:
        rows = s._get_connection().execute(
            "SELECT _type, COUNT(*) AS n FROM item GROUP BY _type"
        ).fetchall()
        rel = s._get_connection().execute(
            "SELECT COUNT(*) AS n FROM system_relation"
        ).fetchone()
    return {r["_type"]: r["n"] for r in rows} | {"relation": rel["n"]}


def test_batch_stores_task_note_and_relation(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(
        days={"2026-11-06": [done_entry(notes="the reasoning")]}))
    facts, relations = ingest_via_runtime(soil, provider, ["2026-11-06"])
    assert (facts, relations) == (2, 1)
    assert counts(soil) == {"Task": 1, "Note": 1, "relation": 1}
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
    facts, _ = ingest_via_runtime(soil, provider, ["2026-11-06"])
    assert facts == 0
    assert counts(soil) == {"Task": 1, "relation": 0}


def test_crash_repair_redelivers_only_the_note(soil, state_path):
    """A run that died between a Task and its Note: Task is skipped, the
    Note re-delivers, and its relation resolves the stored Task."""
    client = FakeTadaClient(days={"2026-11-06": [done_entry(notes="kept notes")]})
    provider = make_provider(soil, state_path, client)
    batch = ["2026-11-06"]

    yielded = list(provider._sync_batch(batch))
    store_fact(soil, yielded[0])  # Task stored; the process "dies" before the Note lands

    facts, relations = ingest_via_runtime(soil, provider, batch)
    assert facts == 1  # only the Note re-delivered
    assert relations == 1
    assert counts(soil) == {"Task": 1, "Note": 1, "relation": 1}


def test_show_failure_stores_entry_only(soil, state_path):
    entry = done_entry()
    client = FakeTadaClient(days={"2026-11-06": [entry]},
                            items={entry["uid"]: {"uid": entry["uid"]}},
                            fail_show={entry["uid"]})
    provider = make_provider(soil, state_path, client)
    facts, _ = ingest_via_runtime(soil, provider, ["2026-11-06"])
    assert facts == 1  # Task without a note, no crash
    assert counts(soil) == {"Task": 1, "relation": 0}


def test_cursor_advances_on_clean_completion(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(
        days={"2026-11-05": [], "2026-11-06": [done_entry()]}),
        backfill_since="2026-11-05")
    provider._fixed_today = date(2026, 11, 7)
    list(provider.sync())
    state = json.loads(Path(state_path).read_text())
    assert state == {"last_day": "2026-11-06"}


def test_today_excluded_from_walk(soil, state_path):
    client = FakeTadaClient(days={"2026-11-07": [done_entry()]})
    provider = make_provider(soil, state_path, client,
                             backfill_since="2026-11-07")
    provider._fixed_today = date(2026, 11, 7)
    assert list(provider.sync()) == []
    assert client.calls == []  # today's snapshot never even polled


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


def test_backfill_walk_yields_entries(soil, state_path):
    provider = make_provider(soil, state_path, FakeTadaClient(
        days={"2026-11-05": [done_entry()]}),
        backfill_since="2026-11-05")
    provider._fixed_today = date(2026, 11, 7)
    yielded = list(provider.sync())
    assert [f["type"] for f in yielded] == ["Task"]
