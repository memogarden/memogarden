"""Entity-lane translation tests: Task/Recurrence upserts + conversion."""

from tada_importer.translate_entities import (
    conversion_yields,
    recurrence_upsert,
    task_upsert,
)

ZONE = "Asia/Singapore"


def open_item(**over):
    item = {
        "uid": "t-open-1",
        "title": "Buy earplugs",
        "kind": "task",
        "status": "confirmed",
        "timeZone": "Asia/Singapore",
        "tags": ["buy"],
        "updatedAt": "2026-11-19T08:00",
    }
    item.update(over)
    return item


def template(**over):
    item = {
        "uid": "tmpl-1",
        "title": "Water plants",
        "kind": "task",
        "recurrenceRule": "FREQ=DAILY;INTERVAL=2",
        "start": "2026-11-15T09:00",
        "duration": "PT15M",
        "timeZone": "Asia/Singapore",
        "tags": ["home"],
        "updatedAt": "2026-11-15T09:00",
    }
    item.update(over)
    return item


def resolved_item(**over):
    item = open_item(uid="t-done-1", title="Fresh resolution")
    item.update({"completedAt": "2026-11-19T07:30", **over})
    return item


class TestTaskUpsert:
    def test_open_item_shape(self):
        up = task_upsert(open_item(), ZONE)
        assert up["op"] == "upsert_entity"
        assert up["type"] == "Task"
        assert up["key"] == {"source": "tada", "source_id": "t-open-1"}
        assert up["data"]["source_id"] == "t-open-1"
        assert up["data"]["title"] == "Buy earplugs"
        assert up["data"]["status"] == "confirmed"
        assert up["data"]["tags"] == ["buy"]
        assert up["metadata"]["source"] == "tada"
        assert up["metadata"]["etag"] == "2026-11-19T08:00"
        # A Task carries NO source_status — its lifecycle ends in conversion.
        assert "source_status" not in up["metadata"]

    def test_absent_optionals_yield_explicit_nulls(self):
        # Full-resource mirror: nulls CLEAR stale stored values on merge.
        up = task_upsert(open_item(), ZONE)
        assert up["data"]["start_at"] is None
        assert up["data"]["duration"] is None
        assert up["data"]["recurrence_of"] is None
        assert up["data"]["follows_from"] is None

    def test_scheduled_and_linked_fields_map(self):
        item = open_item(
            start="2026-11-20T09:30", duration="PT30M",
            recurrenceOf="tmpl-1", followedFrom="t-pred", source="ticktick",
            appearAfter="2026-12-01", notify=True, focus=True,
        )
        up = task_upsert(item, ZONE)
        assert up["data"]["start_at"] == "2026-11-20T01:30:00+00:00"
        assert up["data"]["duration"] == "PT30M"
        assert up["data"]["recurrence_of"] == "tmpl-1"
        assert up["data"]["follows_from"] == "t-pred"
        assert up["data"]["appear_after"] == "2026-12-01"
        assert up["data"]["notify"] is True
        assert up["data"]["focus"] is True
        assert up["metadata"]["tada_source"] == "ticktick"


class TestRecurrenceUpsert:
    def test_template_shape(self):
        up = recurrence_upsert(template(), ZONE)
        assert up["op"] == "upsert_entity"
        assert up["type"] == "Recurrence"
        assert up["key"] == {"source": "tada", "source_id": "tmpl-1"}
        assert up["data"]["rrule"] == "FREQ=DAILY;INTERVAL=2"
        assert up["data"]["start_at"] == "2026-11-15T01:00:00+00:00"
        assert up["data"]["time_zone"] == "Asia/Singapore"
        assert up["metadata"]["etag"] == "2026-11-15T09:00"
        # Absent while live; 'ended' persists via merge semantics once stamped.
        assert "source_status" not in up["metadata"]


class TestConversionYields:
    def test_event_note_then_retire_in_order(self):
        item = resolved_item(notes="kept")
        yields = list(conversion_yields(item, "2026-11-19", ZONE))
        assert [f"op:{y['op']}" if y.get("op") else y.get("type") for y in yields] == \
            ["Event", "Note", "op:retire_entity"]
        event = yields[0]
        assert event["type"] == "Event"
        assert event["metadata"]["status"] == "completed"
        assert event["metadata"]["resolved_at"] == "2026-11-18T23:30:00+00:00"
        retire = yields[-1]
        assert retire["op"] == "retire_entity"
        assert retire["type"] == "Task"
        assert retire["key"] == {"source": "tada", "source_id": "t-done-1"}

    def test_resolved_at_comes_from_completed_at(self):
        item = resolved_item(completedAt="2026-11-19T07:45")
        event, = [y for y in conversion_yields(item, "2026-11-19", ZONE)
                  if y.get("type") == "Event"]
        assert event["metadata"]["resolved_at"] == "2026-11-18T23:45:00+00:00"


class TestCancelledRetire:
    def test_retire_shape_never_upsert(self):
        """A cancelled tada item retires its Task entity — it must never
        mirror as a live entity (task.yaml: Task is in-flight-only)."""
        from tada_importer.translate_entities import cancelled_retire

        retire = cancelled_retire(open_item(status="cancelled"))
        assert retire == {
            "op": "retire_entity",
            "type": "Task",
            "key": {"source": "tada", "source_id": "t-open-1"},
        }
