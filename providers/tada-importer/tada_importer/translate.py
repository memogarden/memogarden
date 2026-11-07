"""Translate tada snapshot entries into Task and Note facts.

Mappings (see schemas/task.yaml):
- outcome 'done' -> status 'completed'; 'cancelled' -> 'cancelled'; unknown
  future outcomes yield nothing (the provider logs them).
- entry `at` -> status_updated_at; entry `start` -> start_at. tada wall times
  are naive in the store zone (health-reported); Soil stores UTC ISO 8601.
- canonical_at: completed -> status_updated_at; cancelled/skipped -> start_at,
  falling back to status_updated_at when there is no slot.
- non-empty notes -> a Note fact (Note.description is required, so empty notes
  produce none), linked later by the provider's create_relations.
- tada extensions (follows_from, tada_source) ride in metadata, populated from
  the best-effort show() enrichment.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_STATUS_BY_OUTCOME = {"done": "completed", "cancelled": "cancelled"}


def to_utc_iso(naive: str, zone: str) -> str:
    """tada naive wall time (e.g. '2026-11-06T18:41') -> ISO 8601 UTC."""
    dt = datetime.fromisoformat(naive).replace(tzinfo=ZoneInfo(zone))
    return dt.astimezone(UTC).isoformat()


def task_facts(
    entry: dict,
    day: str,
    zone: str,
    item: dict | None = None,
    realized_at: str | None = None,
) -> Iterator[dict]:
    """Yield the Task fact for a snapshot entry, then its Note fact.

    Order matters: the runtime stores each yielded fact and immediately calls
    create_relations on it, so the Task must precede its Note for the note's
    contained_by relation to resolve. `item` is the optional show()
    enrichment; when absent (or snapshot-resolved) fields it uniquely carries
    are simply missing.
    """
    outcome = entry.get("outcome", "done")
    status = _STATUS_BY_OUTCOME.get(outcome)
    if status is None:
        return

    at_utc = to_utc_iso(entry["at"], zone)
    start_utc = to_utc_iso(entry["start"], zone) if entry.get("start") else None

    data: dict = {
        "source_id": entry["uid"],
        "title": entry["title"],
        "kind": entry.get("kind", "task"),
        "status": status,
        "status_updated_at": at_utc,
        "tags": entry.get("tags", []),
    }
    if start_utc:
        data["start_at"] = start_utc
    if entry.get("duration"):
        data["duration"] = entry["duration"]

    # canonical_at: when it finished, else the slot it was meant for, else
    # the decision moment.
    canonical_at = at_utc if status == "completed" else (start_utc or at_utc)

    metadata: dict = {"source": "tada", "day": day}
    followed = (item or {}).get("followedFrom")
    if followed:
        metadata["follows_from"] = followed
    tada_source = (item or {}).get("source")
    if tada_source:
        metadata["tada_source"] = tada_source

    task = {
        "type": "Task",
        "data": data,
        "metadata": metadata,
        "canonical_at": canonical_at,
        "realized_at": realized_at,
    }
    yield task

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
