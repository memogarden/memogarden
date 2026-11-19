"""Backport legacy Task facts → Event facts (the 2026-11-19 ontology flip).

The tada lane pre-dated the Task→Event conversion: resolutions ingested
before the flip are Task facts (status in data). This script converts
that history, following /mg amend's exact write path — create the
superseding fact, stamp the original (mark_superseded), record the
`supersedes` relation — and re-points Note facts to their converted
Events with new contained_by relations (old Task-targeted relations stay:
system relations are append-only history).

Idempotent by construction: a Task fact whose data.source_id already has
an Event fact is skipped, so re-runs (or an interrupted run) converge.
Per-fact commits — crash-safe, resumable.

Usage (from the memogarden-api venv, which resolves `system` and the
provider checkout):
    python backport_task_facts.py            # DRY RUN: counts + samples
    python backport_task_facts.py --execute  # the real write

Defaults to the operator's Soil (verb-based resolution); --soil-path
overrides (testing).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from system.soil import Fact, Soil, generate_soil_uuid, get_soil
from system.soil.relation import SystemRelation

_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)

_STATUS_BY_OUTCOME = {"done": "completed", "cancelled": "cancelled"}


def _day_int() -> int:
    return (datetime.now(UTC) - _RELATION_EPOCH).days


def _existing_event_uuid(soil: Soil, source_id: str) -> str | None:
    """The uuid of the tada Event fact for this source_id, if one landed."""
    with soil:
        row = soil._get_connection().execute(
            """SELECT uuid FROM item
               WHERE _type = 'Event'
                 AND json_extract(metadata, '$.source') = 'tada'
                 AND json_extract(data, '$.source_id') = ?""",
            (source_id,),
        ).fetchone()
    return row["uuid"] if row else None


def _event_exists(soil: Soil, source_id: str) -> bool:
    return _existing_event_uuid(soil, source_id) is not None
    with soil:
        row = soil._get_connection().execute(
            """SELECT 1 FROM item
               WHERE _type = 'Event'
                 AND json_extract(metadata, '$.source') = 'tada'
                 AND json_extract(data, '$.source_id') = ?""",
            (source_id,),
        ).fetchone()
    return row is not None


def _note_already_linked(soil: Soil, note_uuid: str, event_uuid: str) -> bool:
    with soil:
        row = soil._get_connection().execute(
            """SELECT 1 FROM system_relation
               WHERE kind = 'contained_by' AND source = ? AND target = ?""",
            (note_uuid, event_uuid),
        ).fetchone()
    return row is not None


def _convert_fact(fact, now: str) -> Fact:
    """Legacy Task fact -> Event fact (schemas/event.yaml FACT section)."""
    data = fact.data or {}
    metadata = fact.metadata or {}
    status = data.get("status", "completed")
    resolved_at = data.get("status_updated_at")
    start_at = data.get("start_at")

    event_data: dict = {
        "source_id": data["source_id"],
        "title": data.get("title", ""),
    }
    if data.get("kind"):
        event_data["kind"] = data["kind"]
    if start_at:
        event_data["start_at"] = start_at
    # Legacy facts carry duration alongside start (pre-ends_at convention) —
    # conversion to the exclusive end needs zone arithmetic the fact row no
    # longer has, so duration is NOT carried (absence is honest; the slot's
    # start and the resolution moment carry the temporal truth we still have).
    if data.get("tags"):
        event_data["tags"] = data["tags"]

    event_metadata: dict = {
        "source": metadata.get("source", "tada"),
        "status": status,
    }
    if resolved_at:
        event_metadata["resolved_at"] = resolved_at
    if metadata.get("day"):
        event_metadata["day"] = metadata["day"]
    if metadata.get("follows_from"):
        event_metadata["follows_from"] = metadata["follows_from"]
    if metadata.get("tada_source"):
        event_metadata["tada_source"] = metadata["tada_source"]

    # canonical_at honesty rule (carried from the legacy mapping):
    # completed -> resolved_at; cancelled/skipped -> the slot, else the
    # decision moment.
    canonical_at = fact.canonical_at or resolved_at or fact.realized_at

    return Fact(
        uuid=generate_soil_uuid(),
        _type="Event",
        realized_at=now,
        canonical_at=canonical_at,
        data=event_data,
        metadata=event_metadata,
        integrity_hash=None,  # computed by create_fact
        fidelity="full",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="write the conversion (default: dry run)")
    parser.add_argument("--soil-path", default=None,
                        help="Soil path override (default: the runtime's)")
    args = parser.parse_args()

    soil = Soil(args.soil_path) if args.soil_path else get_soil()
    now = datetime.now(UTC).isoformat()
    day = _day_int()

    with soil:
        rows = soil._get_connection().execute(
            """SELECT uuid, data, metadata, canonical_at, realized_at FROM item
               WHERE _type = 'Task'
                 AND json_extract(metadata, '$.source') = 'tada'
                 AND superseded_by IS NULL"""
        ).fetchall()

    tasks = [dict(r) for r in rows]
    print(f"legacy tada Task facts (live): {len(tasks)}")

    to_convert, to_supersede = [], []
    import json

    for t in tasks:
        data = json.loads(t["data"])
        source_id = data.get("source_id", "")
        event_uuid = _existing_event_uuid(soil, source_id)
        if event_uuid is not None:
            # Flip-era conversion: the Event exists — the Task fact must
            # still be superseded ONTO it (a skip that leaves the Task live
            # keeps a duplicate resolution on the books).
            to_supersede.append((t, event_uuid))
        else:
            to_convert.append((t, data))
    statuses: dict[str, int] = {}
    for _, data in to_convert:
        statuses[data.get("status", "?")] = statuses.get(data.get("status", "?"), 0) + 1
    print(f"  already converted (supersede onto existing Events): {len(to_supersede)}")
    print(f"  to convert: {len(to_convert)} {statuses}")

    with soil:
        notes = soil._get_connection().execute(
            """SELECT uuid, metadata FROM item
               WHERE _type = 'Note'
                 AND json_extract(metadata, '$.source') = 'tada'
                 AND json_extract(metadata, '$.task_source_id') IS NOT NULL
                 AND superseded_by IS NULL"""
        ).fetchall()
    note_rows = [dict(r) for r in notes]
    repoints = 0
    for n in note_rows:
        n_meta = json.loads(n["metadata"] or "{}")
        event_row = None
        if _event_exists(soil, n_meta.get("task_source_id", "")):
            with soil:
                event_row = soil._get_connection().execute(
                    """SELECT uuid FROM item WHERE _type = 'Event'
                       AND json_extract(data, '$.source_id') = ?""",
                    (n_meta["task_source_id"],),
                ).fetchone()
            if event_row and not _note_already_linked(
                soil, n["uuid"], event_row["uuid"]
            ):
                repoints += 1
    print(f"  Notes to re-point: {repoints} (of {len(note_rows)} tada Notes)")

    if not args.execute:
        with soil:
            for t, data in to_convert[:3]:
                print(f"  sample: {data.get('source_id', '?')[:8]} [{data.get('status')}] "
                      f"{data.get('title', '')[:60]!r}")
        print("DRY RUN — no writes. Pass --execute to convert.")
        return 0

    converted = 0
    for i, (t, data) in enumerate(to_convert, 1):
        fact_row = dict(t)
        original = Fact(
            uuid=fact_row["uuid"],
            _type="Task",
            realized_at=fact_row["realized_at"],
            canonical_at=fact_row["canonical_at"],
            data=data,
            metadata=json.loads(fact_row["metadata"] or "{}"),
            integrity_hash=None,
            fidelity="full",
        )
        event = _convert_fact(original, now)
        with soil as s:
            event_uuid = s.create_fact(event)
            s.mark_superseded(
                original_uuid=t["uuid"],
                superseded_by_uuid=event_uuid,
                superseded_at=now,
            )
            s.create_relation(SystemRelation(
                uuid=generate_soil_uuid(),
                kind="supersedes",
                source=event_uuid,
                source_type="item",
                target=t["uuid"],
                target_type="item",
                created_at=day,
                evidence={"source": "backport", "method": "task_fact_backport"},
            ))
        converted += 1
        if converted % 500 == 0:
            print(f"  converted {converted}/{len(to_convert)}")

    superseded = 0
    for t, event_uuid in to_supersede:
        # Flip-era Task fact -> supersede ONTO its already-existing Event.
        with soil as s:
            s.mark_superseded(
                original_uuid=t["uuid"],
                superseded_by_uuid=event_uuid,
                superseded_at=now,
            )
            s.create_relation(SystemRelation(
                uuid=generate_soil_uuid(),
                kind="supersedes",
                source=event_uuid,
                source_type="item",
                target=t["uuid"],
                target_type="item",
                created_at=day,
                evidence={"source": "backport", "method": "task_fact_backport"},
            ))
        superseded += 1
    if to_supersede:
        print(f"  {superseded} flip-era Task facts superseded onto their existing Events")

    repointed = 0
    for n in note_rows:
        n_meta = json.loads(n["metadata"] or "{}")
        if not _event_exists(soil, n_meta.get("task_source_id", "")):
            continue
        with soil:
            event_row = soil._get_connection().execute(
                """SELECT uuid FROM item WHERE _type = 'Event'
                   AND json_extract(data, '$.source_id') = ?""",
                (n_meta["task_source_id"],),
            ).fetchone()
        if event_row and not _note_already_linked(soil, n["uuid"], event_row["uuid"]):
            with soil as s:
                s.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="contained_by",
                    source=n["uuid"],
                    source_type="item",
                    target=event_row["uuid"],
                    target_type="item",
                    created_at=day,
                    evidence={"source": "backport", "method": "task_fact_backport"},
                ))
            repointed += 1
            if repointed % 200 == 0:
                print(f"  re-pointed {repointed}/{repoints}")

    print(f"DONE: {converted} Task facts -> Event facts; {superseded} superseded onto existing Events; {repointed} Notes re-pointed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
