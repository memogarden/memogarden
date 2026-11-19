"""Entry -> Event/Note fact translation tests (schemas/event.yaml, FACT)."""

from conftest import done_entry
from tada_importer.translate import ends_at_utc, event_facts, to_utc_iso


def facts_for(entry, item=None):
    return list(event_facts(entry, "2026-11-06", "Asia/Singapore", item=item,
                            realized_at="2026-11-06T18:45:00+00:00"))


def test_utc_conversion():
    # SG is UTC+8 with no DST: 18:41 local -> 10:41 UTC
    assert to_utc_iso("2026-11-06T18:41", "Asia/Singapore") == "2026-11-06T10:41:00+00:00"


def test_ends_at_converts_duration_exclusively():
    # 09:30 SG + PT1H -> exclusive end 10:30 SG = 02:30 UTC
    assert ends_at_utc("2026-11-06T09:30", "PT1H", "Asia/Singapore") == \
        "2026-11-06T02:30:00+00:00"


def test_ends_at_garbage_duration_is_none():
    assert ends_at_utc("2026-11-06T09:30", "not-a-duration", "Asia/Singapore") is None


def test_completed_entry_maps_to_event():
    event, = facts_for(done_entry())
    assert event["type"] == "Event"
    d = event["data"]
    assert d["source_id"] == done_entry()["uid"]
    assert d["kind"] == "task"
    assert d["tags"] == ["buy"]
    m = event["metadata"]
    assert m["source"] == "tada"
    assert m["day"] == "2026-11-06"
    assert m["status"] == "completed"
    assert m["resolved_at"] == "2026-11-06T10:41:00+00:00"
    # completed -> canonical time is when it finished
    assert event["canonical_at"] == m["resolved_at"]


def test_optional_fields_omitted_when_absent():
    event, = facts_for(done_entry())
    assert "start_at" not in event["data"]
    assert "ends_at" not in event["data"]


def test_start_and_duration_map_to_ends_at():
    entry = done_entry(start="2026-11-06T09:30", duration="PT1H")
    event, = facts_for(entry)
    assert event["data"]["start_at"] == "2026-11-06T01:30:00+00:00"
    # EXCLUSIVE end — the one interval convention; duration never rides
    assert event["data"]["ends_at"] == "2026-11-06T02:30:00+00:00"
    assert "duration" not in event["data"]


def test_cancelled_canonical_at_prefers_slot():
    entry = done_entry(outcome="cancelled", at="2026-11-06T20:00",
                       start="2026-11-06T09:30")
    event, = facts_for(entry)
    assert event["metadata"]["status"] == "cancelled"
    assert event["metadata"]["resolved_at"] == "2026-11-06T12:00:00+00:00"
    # didn't happen: canonical time is the slot it was meant for
    assert event["canonical_at"] == event["data"]["start_at"]


def test_cancelled_without_slot_falls_back_to_decision_time():
    entry = done_entry(outcome="cancelled", at="2026-11-06T20:00")
    event, = facts_for(entry)
    assert event["canonical_at"] == event["metadata"]["resolved_at"]


def test_unknown_outcome_yields_nothing():
    assert facts_for(done_entry(outcome="skipped")) == []


def test_notes_become_note_fact_after_event():
    entry = done_entry(notes="A) 70 inch\nB) right")
    event, note = facts_for(entry)
    assert event["type"] == "Event"
    assert note["type"] == "Note"
    assert note["data"] == {"description": "A) 70 inch\nB) right"}
    assert note["metadata"]["task_source_id"] == entry["uid"]
    assert note["metadata"]["source"] == "tada"
    assert note["canonical_at"] == event["canonical_at"]


def test_no_note_fact_for_empty_notes():
    assert [f["type"] for f in facts_for(done_entry())] == ["Event"]
    assert [f["type"] for f in facts_for(done_entry(notes="   "))] == ["Event"]


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


def test_follows_from_tada_source_and_recurrence_of_in_metadata():
    item = {"uid": done_entry()["uid"], "followedFrom": "01a1aaaa-0000",
            "source": "ticktick", "recurrenceOf": "8b2d-templ"}
    event, = facts_for(done_entry(), item=item)
    assert event["metadata"]["follows_from"] == "01a1aaaa-0000"
    assert event["metadata"]["tada_source"] == "ticktick"
    assert event["metadata"]["recurrence_of"] == "8b2d-templ"
