"""TadaProvider — tada's drain as a registered Provider Protocol
implementation (PRD Provider Plugin Interface; RFC-005 registry entry
`tada`), running BOTH lanes:

- The FACT lane: the snapshot day-walk. The provider's cursor is the last
  fully-ingested local day (tada-sync-state.json) — provider-managed, so
  the runtime's `since` timestamp is accepted but ignored (sync is
  day-keyed, not timestamp-keyed). Today's snapshot is still mutating, so
  the walk stops at yesterday; a day with no snapshot file simply returns
  no entries. Post-flip (2026-11-19 ontology) the walk yields EVENT facts
  (+ Notes) — it doubles as the outage backstop for resolutions the
  entity lane's trailing window misses.

- The ENTITY lane: the inventory walk (tada's read-only `inventory` op —
  open items + window-resolved items + every series template). Open
  items upsert Task entities (etag = updatedAt no-op); window-resolved
  items CONVERT (Event fact, then the Task retire — task.yaml RESOLUTION);
  templates upsert Recurrence entities. The window re-observes
  completions same-day; the day-walk covers anything the window misses
  (outages longer than it). Both lanes share the Event done-set, so a
  resolution lands exactly once whichever lane reaches it first; retires
  are idempotent noops on the runtime side.

Dedup on the native uid: the Event done-set (read back from Soil —
correctness lives in memogarden) and the Note done-set are tracked
separately: a run that died between an Event and its Note repairs itself
on the next sync (the Event is skipped, the Note re-delivered, and
create_relations resolves the already-stored Event). sync() yields dicts
in the runtime's normalized shape ({type, data, metadata, canonical_at,
realized_at} for facts; {op, ...} for entity upserts/retires); the
runtime validates and stores (PRD split: providers yield, memogarden
stores).

Backfill: with no stored cursor the walk starts at `backfill_since` (the
first day in tada's snapshot archive) and is capped at `batch_limit` days
per sync — the cursor stays put until a batch completes cleanly, and the
done-set absorbs whatever already landed on re-delivery.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tada_importer.client import TadaApiClient, TadaApiError
from tada_importer.translate import event_facts
from tada_importer.translate_entities import (
    cancelled_retire,
    conversion_yields,
    recurrence_upsert,
    task_upsert,
)

# Relation created_at convention shared with the gmail provider: day-granularity
# ints counted from this epoch (RFC-002 says "days since epoch"; the epoch
# choice follows the existing live data).
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)

_DEFAULTS = {
    "base_url": "http://127.0.0.1:7427",
    "backfill_since": "2019-08-19",  # first day in tada's snapshot archive
    "batch_limit": 400,              # max snapshot days per sync
    "resolved_within": 2,            # entity-lane trailing window (days)
    "sync_state_path": "~/.local/share/memogarden/tada-sync-state.json",
    "zone": None,                    # resolved from /health when unset
    "request_timeout": 30.0,
}


class TadaProvider:
    """Provider Protocol implementation over tada's POST /cmd bridge."""

    # Capability declarations (derived at registration; the runtime refuses
    # undeclared yields): the entity lane upserts Task/Recurrence entities
    # and retires resolved Tasks (the Task→Event conversion).
    ENTITY_UPSERTS = True
    ENTITY_RETIRES = True

    def __init__(self, config: dict):
        merged = {**_DEFAULTS, **(config or {})}
        expand = lambda p: str(Path(p).expanduser())
        self._sync_state_path = expand(merged["sync_state_path"])
        self._batch_limit = int(merged["batch_limit"])
        self._resolved_within = int(merged["resolved_within"])
        self._backfill_since = merged["backfill_since"]
        self.client = TadaApiClient(
            merged["base_url"], timeout=float(merged["request_timeout"])
        )
        self._zone = merged.get("zone") or self.client.zone()

        # Soil: explicit path override, else the runtime's default resolution
        # (verb-based — identical to whatever environment we execute in).
        if merged.get("soil_path"):
            from system.soil import Soil

            soil = Soil(expand(merged["soil_path"]))
        else:
            from system.soil import get_soil

            soil = get_soil()
        self._soil = soil
        with soil:
            # Construction AND schema init (idempotent), like the gmail provider.
            soil.init_schema()

    def _log(self, msg: str) -> None:
        print(f"[tada provider] {msg}")

    # ------------------------------------------------------------------
    # Provider Protocol
    # ------------------------------------------------------------------

    def sync(self, since: str | None = None) -> Iterator[dict]:
        """Yield facts, entity upserts and retires for everything NEW since
        the stored day cursor.

        Args:
            since: Ignored — the day cursor in the sync state is the cursor.

        Cursor advancement: the sync state advances to the last day of the
        batch only when the generator runs to completion; a truncated batch
        (batch_limit) re-delivers the remainder next sync, with the done-set
        absorbing what already landed. The entity lane runs after the fact
        lane on every sync (the runtime commits per yield, so ordering is
        durable).
        """
        today = self._today()
        days = self._pending_days(today)
        if days:
            batch = days[: self._batch_limit]
            truncated = len(days) > len(batch)
            self._log(
                f"walking {len(batch)} day(s) {batch[0]}..{batch[-1]}"
                + (" (truncated)" if truncated else "")
            )
            yield from self._sync_batch(batch)
        else:
            self._log(f"no pending days (cursor at {self._load_state().get('last_day')})")
        yield from self._sync_entities(today)

    def create_relations(self, fact) -> list:
        """Link each tada Note to its Event (`contained_by`, mirroring the
        Attachment→Email precedent). Resolves the Event by the note's
        metadata.task_source_id — the Event was yielded first, so it is
        already stored by the time the runtime calls this."""
        if getattr(fact, "_type", None) != "Note":
            return []
        task_source_id = (fact.metadata or {}).get("task_source_id")
        if not task_source_id:
            return []

        from system.soil import generate_soil_uuid
        from system.soil.relation import SystemRelation

        with self._soil as soil:
            row = soil._get_connection().execute(
                """SELECT uuid FROM item WHERE _type = 'Event'
                   AND json_extract(data, '$.source_id') = ?""",
                (task_source_id,),
            ).fetchone()
        if row is None:
            return []

        days = (datetime.now(UTC) - _RELATION_EPOCH).days
        return [
            SystemRelation(
                uuid=generate_soil_uuid(),
                kind="contained_by",
                source=fact.uuid,
                source_type="item",
                target=row["uuid"],
                target_type="item",
                created_at=days,
                evidence=None,
                metadata=None,
            )
        ]

    # ------------------------------------------------------------------
    # Fact lane: the snapshot day-walk (also the outage backstop)
    # ------------------------------------------------------------------

    def _sync_batch(self, batch: list[str]) -> Iterator[dict]:
        events_done = self._landed_event_ids()
        notes_done = self._ingested_note_task_ids()
        realized_at = datetime.now(UTC).isoformat()
        completed = False
        try:
            for day in batch:
                for entry in self.client.list_day(day):
                    uid = entry.get("uid")
                    if not uid:
                        continue
                    if uid in events_done and uid in notes_done:
                        continue
                    item = self._enrich(uid)
                    for fact in event_facts(
                        entry, day, self._zone, item=item, realized_at=realized_at
                    ):
                        if fact["type"] == "Event" and uid in events_done:
                            continue
                        if fact["type"] == "Note" and uid in notes_done:
                            continue
                        # Conversion companionship: the retire rides
                        # whichever lane lands the Event (idempotent noop
                        # when the entity never existed or already retired).
                        yield fact
                        if fact["type"] == "Event":
                            yield {
                                "op": "retire_entity",
                                "type": "Task",
                                "key": {"source": "tada", "source_id": uid},
                            }
                    events_done.add(uid)
                    notes_done.add(uid)
            completed = True
        finally:
            # Advance the cursor only on full, clean consumption: by the time
            # the runtime asks for the item past the end, everything yielded
            # is already committed in Soil by the runtime itself.
            if completed:
                self._store_state(batch[-1])

    def _sync_entities(self, today: date) -> Iterator[dict]:
        """The entity lane: inventory walk → Task/Recurrence upserts for the
        open set and templates, Event+retire conversion for window
        resolutions. Runs on EVERY sync (no cursor — the runtime's etag
        no-op makes full walks cheap; conversion dedup rides the shared
        Event done-set)."""
        realized_at = datetime.now(UTC).isoformat()
        try:
            inventory = self.client.inventory(resolved_within=self._resolved_within)
        except TadaApiError as e:
            # The fact lane must not be poisoned by the entity lane's
            # transport (tada reachable for snapshots but not inventory is
            # a transient shape); the next heartbeat retries.
            self._log(f"inventory failed ({e}); entity lane deferred")
            return
        events_done = self._landed_event_ids()
        notes_done = self._ingested_note_task_ids()
        items = inventory.get("items") or []
        cancelled = [
            i for i in items
            if i.get("status") == "cancelled" and not i.get("completedAt")
        ]
        open_items = [
            i for i in items
            if not i.get("completedAt") and i.get("status") != "cancelled"
        ]
        resolved = [i for i in items if i.get("completedAt")]
        self._log(
            f"inventory: {len(open_items)} open, {len(resolved)} window-resolved, "
            f"{len(cancelled)} cancelled (retire-only), "
            f"{len(inventory.get('templates') or [])} templates"
        )
        for item in open_items:
            if not item.get("uid") or not item.get("updatedAt"):
                continue
            yield task_upsert(item, self._zone)
        for item in cancelled:
            if not item.get("uid"):
                continue
            yield cancelled_retire(item)
        for template in inventory.get("templates") or []:
            if not template.get("uid") or not template.get("updatedAt"):
                continue
            yield recurrence_upsert(template, self._zone)
        for item in resolved:
            uid = item.get("uid")
            if not uid:
                continue
            if uid not in events_done:
                for yielded in conversion_yields(
                    item, today.isoformat(), self._zone, realized_at=realized_at
                ):
                    yield yielded
                events_done.add(uid)
                notes_done.add(uid)
            else:
                # The Event already landed (day-walk reached it first);
                # the retire still rides — idempotent on the runtime side.
                yield {
                    "op": "retire_entity",
                    "type": "Task",
                    "key": {"source": "tada", "source_id": uid},
                }

    def _enrich(self, uid: str) -> dict | None:
        """Best-effort show() — recurrence_of / follows_from / source /
        notes fallback ride along when the store still has the item;
        entry-only otherwise."""
        try:
            return self.client.show(uid)
        except TadaApiError as e:
            self._log(f"show {uid} failed ({e}); storing entry-only")
            return None

    def _today(self) -> date:
        # Test seam: fixed today overrides the zone-local clock.
        fixed = getattr(self, "_fixed_today", None)
        if fixed is not None:
            return fixed
        return datetime.now(ZoneInfo(self._zone)).date()

    def _pending_days(self, today: date) -> list[str]:
        """Local days strictly between the cursor and today (exclusive) —
        today's snapshot is still mutating."""
        state = self._load_state()
        if state.get("last_day"):
            start = date.fromisoformat(state["last_day"]) + timedelta(days=1)
        else:
            start = date.fromisoformat(self._backfill_since)
        days: list[str] = []
        d = start
        while d < today:
            days.append(d.isoformat())
            d += timedelta(days=1)
        return days

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        path = Path(self._sync_state_path)
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def _store_state(self, last_day: str) -> None:
        path = Path(self._sync_state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_day": last_day}))

    def _landed_event_ids(self) -> set[str]:
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(data, '$.source_id') AS sid FROM item
                   WHERE _type = 'Event'
                     AND json_extract(metadata, '$.source') = 'tada'"""
            ).fetchall()
        return {r["sid"] for r in rows if r["sid"]}

    def _ingested_note_task_ids(self) -> set[str]:
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(metadata, '$.task_source_id') AS tid FROM item
                   WHERE _type = 'Note'
                     AND json_extract(metadata, '$.source') = 'tada'"""
            ).fetchall()
        return {r["tid"] for r in rows if r["tid"]}
