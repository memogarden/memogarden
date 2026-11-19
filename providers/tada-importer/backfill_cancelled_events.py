"""Backfill pre-snapshot cancelled tada items as cancelled Event facts.

The §10.57 cancel-snapshot flow (2026-11-07) records a cancellation at
cancel time; items cancelled BEFORE it shipped never entered any
snapshot, so the day-walk can never emit their Event — they existed in
memogarden only as (now retired) Task entities with no story (#15's
accepted gap). This one-off script synthesizes the missing fact side:
each still-frozen cancelled item becomes the ordinary cancelled Event
(schemas/event.yaml FACT section — the "unoccurred" record), with the
cancelled canonical_at rule (slot if it had one, else decision moment).

Honesty: times here are DERIVED, not source-asserted — a cancelled item
is frozen at cancel, so `updatedAt` ≈ the decision moment, but no
cancelledAt exists pre-§10.57. Facts carry fidelity 'summary' plus
`metadata.backfill` / `metadata.time_basis: updatedAt` stamps so they
are greppable and never mistaken for snapshot truth.

Steady-state cancels keep arriving via the day-walk (true cancelledAt
from snapshot entries); this script does NOT touch that lane, and it
never retires anything (the entities are already gone — PR #16).

Idempotent by the Event done-set (data.source_id probe, same as the
backport): re-runs and interrupted runs converge. Per-fact commits.

Usage (from the memogarden-api venv, which resolves `system` and the
provider checkout):
    python backfill_cancelled_events.py            # DRY RUN: counts + samples
    python backfill_cancelled_events.py --execute  # the real write

--base-url overrides the tada origin (default 127.0.0.1:7427);
--soil-path overrides Soil (testing).
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from system.soil import Fact, Soil, generate_soil_uuid, get_soil
from system.soil.relation import SystemRelation
from tada_importer.client import TadaApiClient, TadaApiError
from tada_importer.translate import event_facts, to_utc_iso

_BACKFILL_STAMP = "pre-snapshot-cancels-2026-11-19"
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def build_cancelled_entry(item: dict, full_item: dict | None) -> dict:
    """A frozen cancelled tada item -> a snapshot-shaped entry whose
    outcome/at the translator understands. `at` is the derived decision
    moment (updatedAt); the slot/duration/notes ride from the best
    available view of the item."""
    view = full_item or item
    return {
        "uid": item["uid"],
        "kind": view.get("kind", "task"),
        "title": view.get("title", item.get("title", "")),
        "outcome": "cancelled",
        "at": item["updatedAt"],
        "start": view.get("start"),
        "duration": view.get("duration"),
        "tags": view.get("tags", []),
        "notes": view.get("notes"),
    }


def _day_of(naive_local_at: str) -> str:
    """The entry's local day — same semantics as a snapshot day key."""
    return naive_local_at[:10]


def _has_event(soil: Soil, source_id: str) -> bool:
    with soil:
        row = soil._get_connection().execute(
            """SELECT 1 FROM item
               WHERE _type = 'Event'
                 AND json_extract(metadata, '$.source') = 'tada'
                 AND json_extract(data, '$.source_id') = ?
               LIMIT 1""",
            (source_id,),
        ).fetchone()
    return row is not None


def backfill_item(
    soil: Soil, item: dict, full_item: dict | None, zone: str, realized_at: str
) -> list[Fact]:
    """One cancelled item -> its Event fact (+ Note) with backfill stamps.
    Returns the stored facts; [] when the Event already landed."""
    if _has_event(soil, item["uid"]):
        return []
    entry = build_cancelled_entry(item, full_item)
    stored: list[Fact] = []
    event: Fact | None = None
    for yielded in event_facts(
        entry, _day_of(entry["at"]), zone, item=full_item, realized_at=realized_at
    ):
        metadata = {
            **(yielded.get("metadata") or {}),
            "backfill": _BACKFILL_STAMP,
            "time_basis": "updatedAt",
        }
        fact = Fact(
            uuid=generate_soil_uuid(),
            _type=yielded["type"],
            realized_at=realized_at,
            canonical_at=yielded["canonical_at"],
            data=yielded.get("data", {}),
            metadata=metadata,
            integrity_hash=None,
            fidelity="summary",
        )
        with soil:
            soil.create_fact(fact)
        stored.append(fact)
        if yielded["type"] == "Event":
            event = fact
    # The Note's contained_by link — same resolution rule as the
    # provider's create_relations (native source_id -> the Event).
    notes = [f for f in stored if f._type == "Note"]
    if event is not None and notes:
        days = (datetime.now(UTC) - _RELATION_EPOCH).days
        with soil:
            for note in notes:
                soil.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="contained_by",
                    source=note.uuid,
                    source_type="item",
                    target=event.uuid,
                    target_type="item",
                    created_at=days,
                    evidence=None,
                    metadata=None,
                ))
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="write for real (default: dry run)")
    parser.add_argument("--base-url", default="http://127.0.0.1:7427")
    parser.add_argument("--soil-path", default=None,
                        help="override Soil path (testing)")
    args = parser.parse_args()

    client = TadaApiClient(args.base_url)
    zone = client.zone()
    inventory = client.inventory()
    cancelled = [
        i for i in (inventory.get("items") or [])
        if i.get("status") == "cancelled" and not i.get("completedAt") and i.get("uid")
    ]
    print(f"[backfill] inventory: {len(cancelled)} cancelled item(s), zone {zone}")

    if args.soil_path:
        soil = Soil(args.soil_path)
    else:
        soil = get_soil()

    todo = [i for i in cancelled if not _has_event(soil, i["uid"])]
    skipped = len(cancelled) - len(todo)
    print(f"[backfill] {len(todo)} to backfill, {skipped} already have Events (done-set)")

    stored_count = 0
    for item in todo:
        try:
            full_item = client.show(item["uid"])
        except TadaApiError as e:
            print(f"[backfill] show {item['uid']} failed ({e}); entry-only")
            full_item = None
        if args.execute:
            facts = backfill_item(soil, item, full_item, zone,
                                  datetime.now(UTC).isoformat())
        else:
            entry = build_cancelled_entry(item, full_item)
            facts = list(event_facts(entry, _day_of(entry["at"]), zone,
                                     item=full_item))
        if facts:
            sample = facts[0]
            print(f"[backfill] {item['uid']}: {sample['type']}"
                  f" '{sample['data'].get('title')}' status="
                  f"{sample['metadata'].get('status')}"
                  f" canonical_at={sample['canonical_at']}"
                  + ("" if args.execute else "  (dry run)"))
        stored_count += len(facts)

    mode = "EXECUTED" if args.execute else "DRY RUN"
    print(f"[backfill] {mode}: {stored_count} fact(s) "
          f"across {len(todo)} item(s) — done")


if __name__ == "__main__":
    main()
