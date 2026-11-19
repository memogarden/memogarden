"""Translate tada snapshot entries into Event and Note facts.

The 2026-11-19 ontology: an occurrence record is an EVENT fact (the
terminal verdict rides metadata as source vocabulary); the retired Task
FACT type is legacy — providers/tada_importer/backport_task_facts.py
converts pre-flip history.

Mappings (schemas/event.yaml, FACT section):
- outcome 'done' -> metadata.status 'completed'; 'cancelled' -> 'cancelled';
  unknown future outcomes yield nothing (the provider logs them).
- entry `at` -> metadata.resolved_at; entry `start` -> data.start_at;
  start + duration -> data.ends_at (EXCLUSIVE end — the one interval
  convention; tada's duration is converted, never carried alongside).
- canonical_at: completed -> resolved_at (when it finished); cancelled ->
  start_at (the slot it was meant for — the honest "when it happened" for
  a task that didn't), falling back to resolved_at when there is no slot.
- non-empty notes -> a Note fact (Note.description is required, so empty
  notes produce none), linked later by the provider's create_relations
  (metadata.task_source_id resolves the Event — historical key name kept;
  renaming would amend 833 immutable facts for cosmetics).
- tada extensions (recurrence_of, follows_from, tada_source) ride in
  metadata, populated from the best-effort show() enrichment.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_STATUS_BY_OUTCOME = {"done": "completed", "cancelled": "cancelled"}

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def to_utc_iso(naive: str, zone: str) -> str:
    """tada naive wall time (e.g. '2026-11-06T18:41') -> ISO 8601 UTC."""
    dt = datetime.fromisoformat(naive).replace(tzinfo=ZoneInfo(zone))
    return dt.astimezone(UTC).isoformat()


def ends_at_utc(start_naive: str, duration_iso: str, zone: str) -> str | None:
    """start + ISO duration -> EXCLUSIVE ends_at in UTC. None on an
    unparseable duration — absence is honest, a bad duration must not
    corrupt the slot."""
    match = _DURATION.fullmatch(duration_iso.strip())
    if not match:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    start = datetime.fromisoformat(start_naive).replace(tzinfo=ZoneInfo(zone))
    return (start + delta).astimezone(UTC).isoformat()


def event_facts(
    entry: dict,
    day: str,
    zone: str,
    item: dict | None = None,
    realized_at: str | None = None,
) -> Iterator[dict]:
    """Yield the Event fact for a snapshot entry, then its Note fact.

    Order matters: the runtime stores each yielded fact and immediately
    calls create_relations on it, so the Event must precede its Note for
    the note's contained_by relation to resolve. `item` is the optional
    show() enrichment; when absent (or snapshot-resolved) fields it
    uniquely carries are simply missing.
    """
    outcome = entry.get("outcome", "done")
    status = _STATUS_BY_OUTCOME.get(outcome)
    if status is None:
        return

    resolved_at = to_utc_iso(entry["at"], zone)
    start_utc = to_utc_iso(entry["start"], zone) if entry.get("start") else None
    ends_utc = (
        ends_at_utc(entry["start"], entry["duration"], zone)
        if entry.get("start") and entry.get("duration")
        else None
    )

    data: dict = {
        "source_id": entry["uid"],
        "title": entry["title"],
        "kind": entry.get("kind", "task"),
    }
    if start_utc:
        data["start_at"] = start_utc
    if ends_utc:
        data["ends_at"] = ends_utc
    if entry.get("tags"):
        data["tags"] = entry["tags"]

    metadata: dict = {
        "source": "tada",
        "day": day,
        "status": status,
        "resolved_at": resolved_at,
    }
    followed = (item or {}).get("followedFrom")
    if followed:
        metadata["follows_from"] = followed
    recurrence = (item or {}).get("recurrenceOf")
    if recurrence:
        metadata["recurrence_of"] = recurrence
    tada_source = (item or {}).get("source")
    if tada_source:
        metadata["tada_source"] = tada_source

    # canonical_at: when it finished, else the slot it was meant for, else
    # the decision moment.
    canonical_at = resolved_at if status == "completed" else (start_utc or resolved_at)

    yield {
        "type": "Event",
        "data": data,
        "metadata": metadata,
        "canonical_at": canonical_at,
        "realized_at": realized_at,
    }

    notes = entry.get("notes") or (item or {}).get("notes") or ""
    if notes.strip():
        yield {
            "type": "Note",
            "data": {"description": notes},
            "metadata": {
                "source": "tada",
                "day": day,
                "task_source_id": entry["uid"],
            },
            "canonical_at": canonical_at,
            "realized_at": realized_at,
        }
