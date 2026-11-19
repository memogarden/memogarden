"""Translate tada inventory (open items, window resolutions, templates)
into entity upserts and conversion yields — the ENTITY lane (#30's
consumer; the runtime's upsert_entity / retire_entity contract).

Task = the in-flight REQUEST entity: etag = updatedAt drives the no-op
rule; NO source_status — its lifecycle ends in conversion. Recurrence =
the shared pattern entity: rrule + anchor; source_status 'ended' rides
its stamping lifecycle.

Upserts are FULL-RESOURCE mirrors (tada items are complete states, the
gcal precedent): absent optional fields yield explicit nulls so a stale
stored value clears (merge semantics: absent keys keep stored values,
explicit null clears).

Conversion = the Event fact FIRST, then the entity retire — the ordering
contract from task.yaml RESOLUTION; the runtime commits per yield, and a
replayed retire is an idempotent noop.
"""

from __future__ import annotations

from collections.abc import Iterator

from tada_importer.translate import event_facts, to_utc_iso

_SOURCE_KEY = {"source": "tada"}


def task_upsert(item: dict, zone: str) -> dict:
    """One open tada item -> a Task entity upsert (full-resource mirror)."""
    data: dict = {
        "source_id": item["uid"],
        "title": item["title"],
        "kind": item.get("kind", "task"),
        "status": item.get("status", "confirmed"),
        # Explicit nulls clear stale stored values (floating, deferred-off,
        # flags flipped off, tag removals) — faithful mirror, gcal precedent.
        "start_at": to_utc_iso(item["start"], zone) if item.get("start") else None,
        "duration": item.get("duration"),
        "time_zone": item.get("timeZone"),
        "appear_after": item.get("appearAfter"),
        "notify": item.get("notify"),
        "focus": item.get("focus"),
        "tags": item.get("tags", []),
        "recurrence_of": item.get("recurrenceOf"),
        "follows_from": item.get("followedFrom"),
    }
    metadata: dict = {"source": "tada", "etag": item["updatedAt"]}
    if item.get("source"):
        metadata["tada_source"] = item["source"]
    return {
        "op": "upsert_entity",
        "type": "Task",
        "key": {**_SOURCE_KEY, "source_id": item["uid"]},
        "data": data,
        "metadata": metadata,
    }


def cancelled_retire(item: dict) -> dict:
    """One cancelled tada item -> retire its Task entity, never an upsert.

    Cancelled items stay frozen in tada's store with no completedAt, so
    the inventory lists them forever (its filter is completedAt-only).
    task.yaml's Task is in-flight-only — a cancelled item must not mirror
    as a live entity, and the idempotent retire is also what keeps a
    post-conversion cancel from resurrecting its retired entity at the
    next heartbeat (the done-set dedups facts, not entity upserts)."""
    return {
        "op": "retire_entity",
        "type": "Task",
        "key": {**_SOURCE_KEY, "source_id": item["uid"]},
    }


def recurrence_upsert(template: dict, zone: str) -> dict:
    """One tada series template -> a Recurrence entity upsert."""
    data: dict = {
        "source_id": template["uid"],
        "title": template["title"],
        "kind": template.get("kind", "task"),
        "rrule": template["recurrenceRule"],
        "start_at": to_utc_iso(template["start"], zone) if template.get("start") else None,
        "duration": template.get("duration"),
        "time_zone": template.get("timeZone"),
        "notify": template.get("notify"),
        "tags": template.get("tags", []),
    }
    metadata: dict = {
        "source": "tada",
        "etag": template["updatedAt"],
        # source_status deliberately omitted unless known — an absent key
        # keeps a stored 'ended' stamp (merge semantics); tada v1 has no
        # un-end, and vanish-without-stamp stays the flagged open corner.
    }
    if template.get("source"):
        metadata["tada_source"] = template["source"]
    return {
        "op": "upsert_entity",
        "type": "Recurrence",
        "key": {**_SOURCE_KEY, "source_id": template["uid"]},
        "data": data,
        "metadata": metadata,
    }


def conversion_yields(
    item: dict, day: str, zone: str, realized_at: str | None = None
) -> Iterator[dict]:
    """One window-resolved tada item -> Event fact (+ Note), THEN the Task
    retire. Ordering contract: the fact lands before the retire; a replayed
    retire is a noop, so conversion replays converge."""
    entry = {
        "uid": item["uid"],
        "kind": item.get("kind", "task"),
        "title": item["title"],
        # Inventory resolutions carry completedAt (cancelled items leave the
        # live store at cancel time and arrive via the day-walk instead).
        "outcome": "done",
        "at": item["completedAt"],
        "start": item.get("start"),
        "duration": item.get("duration"),
        "tags": item.get("tags", []),
        "notes": item.get("notes"),
    }
    yield from event_facts(entry, day, zone, item=item, realized_at=realized_at)
    yield {
        "op": "retire_entity",
        "type": "Task",
        "key": {**_SOURCE_KEY, "source_id": item["uid"]},
    }
