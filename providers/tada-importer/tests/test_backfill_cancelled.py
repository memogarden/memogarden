"""The pre-snapshot cancelled-Events backfill: derived facts, stamps,
idempotency, and the Note's contained_by link."""

from conftest import make_provider  # noqa: F401  (soil fixture lives there)
from backfill_cancelled_events import backfill_item, build_cancelled_entry

ZONE = "Asia/Singapore"
NOW = "2026-11-19T06:00:00+00:00"


def cancelled_item(**over):
    item = {
        "uid": "01a1canceled-1",
        "title": "Wooden key",
        "kind": "task",
        "status": "cancelled",
        "timeZone": "Asia/Singapore",
        "tags": ["home"],
        "updatedAt": "2026-11-15T21:30",
    }
    item.update(over)
    return item


def test_backfill_stores_cancelled_event_with_stamps(soil):
    item = cancelled_item(start="2026-11-15T20:00", duration="PT30M")
    facts = backfill_item(soil, item, item, ZONE, NOW)
    event, = facts
    assert event._type == "Event"
    assert event.fidelity == "summary"
    assert event.metadata["status"] == "cancelled"
    assert event.metadata["source"] == "tada"
    assert event.metadata["backfill"] == "pre-snapshot-cancels-2026-11-19"
    assert event.metadata["time_basis"] == "updatedAt"
    assert event.metadata["day"] == "2026-11-15"
    # cancelled canonical_at rule: the slot it was meant for (SGT -> UTC)
    assert event.data["start_at"] == "2026-11-15T12:00:00+00:00"
    assert event.canonical_at == event.data["start_at"]


def test_backfill_without_slot_falls_back_to_decision_time(soil):
    item = cancelled_item()
    event, = backfill_item(soil, item, item, ZONE, NOW)
    assert event.canonical_at == event.metadata["resolved_at"]
    assert event.metadata["resolved_at"] == "2026-11-15T13:30:00+00:00"


def test_backfill_is_idempotent_on_the_done_set(soil):
    item = cancelled_item()
    first = backfill_item(soil, item, item, ZONE, NOW)
    assert first
    assert backfill_item(soil, item, item, ZONE, NOW) == []


def test_backfill_links_notes_to_the_event(soil):
    item = cancelled_item(notes="returned to landlord")
    facts = backfill_item(soil, item, item, ZONE, NOW)
    assert [f._type for f in facts] == ["Event", "Note"]
    event, note = facts
    with soil as s:
        row = s._get_connection().execute(
            "SELECT target FROM system_relation WHERE kind='contained_by' "
            "AND source=?",
            (note.uuid,),
        ).fetchone()
    assert row["target"] == event.uuid


def test_build_cancelled_entry_prefers_the_full_view():
    inventory_item = cancelled_item()
    full = cancelled_item(notes="kept", start="2026-11-15T20:00")
    entry = build_cancelled_entry(inventory_item, full)
    assert entry["outcome"] == "cancelled"
    assert entry["at"] == "2026-11-15T21:30"   # the derived decision moment
    assert entry["notes"] == "kept"
    assert entry["start"] == "2026-11-15T20:00"
