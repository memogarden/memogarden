"""Entry -> Task/Note fact translation tests (schemas/task.yaml mappings)."""

from conftest import done_entry
from tada_importer.translate import task_facts, to_utc_iso


def facts_for(entry, item=None):
    return list(task_facts(entry, "2026-11-06", "Asia/Singapore", item=item,
                           realized_at="2026-11-06T18:45:00+00:00"))


def test_utc_conversion():
    # SG is UTC+8 with no DST: 18:41 local -> 10:41 UTC
    assert to_utc_iso("2026-11-06T18:41", "Asia/Singapore") == "2026-11-06T10:41:00+00:00"


def test_completed_entry_maps_to_task():
    task, = facts_for(done_entry())
    assert task["type"] == "Task"
    d = task["data"]
    assert d["source_id"] == done_entry()["uid"]
    assert d["status"] == "completed"
    assert d["status_updated_at"] == "2026-11-06T10:41:00+00:00"
    assert d["kind"] == "task"
    assert d["tags"] == ["buy"]
    assert task["canonical_at"] == d["status_updated_at"]  # completed -> finish time
    assert task["metadata"]["source"] == "tada"
    assert task["metadata"]["day"] == "2026-11-06"


def test_optional_fields_omitted_when_absent():
    task, = facts_for(done_entry())
    assert "start_at" not in task["data"]
    assert "duration" not in task["data"]


def test_start_and_duration_map():
    entry = done_entry(start="2026-11-06T09:30", duration="PT1H")
    task, = facts_for(entry)
    assert task["data"]["start_at"] == "2026-11-06T01:30:00+00:00"
    assert task["data"]["duration"] == "PT1H"


def test_cancelled_canonical_at_prefers_slot():
    entry = done_entry(outcome="cancelled", at="2026-11-06T20:00",
                       start="2026-11-06T09:30")
    task, = facts_for(entry)
    assert task["data"]["status"] == "cancelled"
    assert task["data"]["status_updated_at"] == "2026-11-06T12:00:00+00:00"
    # didn't happen: canonical time is the slot it was meant for
    assert task["canonical_at"] == task["data"]["start_at"]


def test_cancelled_without_slot_falls_back_to_decision_time():
    entry = done_entry(outcome="cancelled", at="2026-11-06T20:00")
    task, = facts_for(entry)
    assert task["canonical_at"] == task["data"]["status_updated_at"]


def test_unknown_outcome_yields_nothing():
    assert facts_for(done_entry(outcome="skipped")) == []


def test_notes_become_note_fact_after_task():
    entry = done_entry(notes="A) 70 inch\nB) right")
    task, note = facts_for(entry)
    assert task["type"] == "Task"
    assert note["type"] == "Note"
    assert note["data"] == {"description": "A) 70 inch\nB) right"}
    assert note["metadata"]["task_source_id"] == entry["uid"]
    assert note["metadata"]["source"] == "tada"
    assert note["canonical_at"] == task["canonical_at"]


def test_no_note_fact_for_empty_notes():
    assert [f["type"] for f in facts_for(done_entry())] == ["Task"]
    assert [f["type"] for f in facts_for(done_entry(notes="   "))] == ["Task"]


def test_item_notes_fallback_for_pre_notes_entries():
    # pre-§10.57 entries carry no notes; the live item's notes fill in
    entry = done_entry()
    item = {"uid": entry["uid"], "notes": "from the item file"}
    _, note = facts_for(entry, item=item)
    assert note["data"]["description"] == "from the item file"


def test_entry_notes_win_over_item_notes():
    entry = done_entry(notes="snapshot decision")
    item = {"uid": entry["uid"], "notes": "live item text"}
    _, note = facts_for(entry, item=item)
    assert note["data"]["description"] == "snapshot decision"


def test_follows_from_and_tada_source_in_metadata():
    item = {"uid": done_entry()["uid"], "followedFrom": "01a1aaaa-0000", "source": "ticktick"}
    task, = facts_for(done_entry(), item=item)
    assert task["metadata"]["follows_from"] == "01a1aaaa-0000"
    assert task["metadata"]["tada_source"] == "ticktick"
