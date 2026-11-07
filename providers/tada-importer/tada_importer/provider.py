"""TadaProvider — the tada day-walk as a registered Provider Protocol
implementation (PRD Provider Plugin Interface; RFC-005 registry entry `tada`).

The provider's cursor is the last fully-ingested local day
(tada-sync-state.json) — provider-managed, so the runtime's `since` timestamp
is accepted but ignored (sync is day-keyed, not timestamp-keyed). Today's
snapshot is still mutating, so the walk stops at yesterday; a day with no
snapshot file simply returns no entries.

Entries dedupe on the task's native uid, tracked separately for Task facts
and their Note facts: a run that died between a Task and its Note repairs
itself on the next sync (the Task is skipped, the Note re-delivered, and
create_relations resolves the already-stored Task). sync() yields dicts in
the runtime's normalized shape ({type, data, metadata, canonical_at,
realized_at}); the runtime validates and inserts (PRD split: providers yield,
MemoGarden stores).

Backfill: with no stored cursor the walk starts at `backfill_since` (the
first day in tada's snapshot archive) and is capped at `batch_limit` days per
sync — the cursor stays put until a batch completes cleanly, and the done-set
absorbs whatever already landed on re-delivery.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tada_importer.client import TadaApiClient, TadaApiError
from tada_importer.translate import task_facts

# Relation created_at convention shared with the gmail provider: day-granularity
# ints counted from this epoch (RFC-002 says "days since epoch"; the epoch
# choice follows the existing live data).
_RELATION_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)

_DEFAULTS = {
    "base_url": "http://127.0.0.1:7427",
    "backfill_since": "2019-08-19",  # first day in tada's snapshot archive
    "batch_limit": 400,              # max snapshot days per sync
    "sync_state_path": "~/.local/share/memogarden/tada-sync-state.json",
    "zone": None,                    # resolved from /health when unset
    "request_timeout": 30.0,
}


class TadaProvider:
    """Provider Protocol implementation over tada's POST /cmd bridge."""

    def __init__(self, config: dict):
        merged = {**_DEFAULTS, **(config or {})}
        expand = lambda p: str(Path(p).expanduser())
        self._sync_state_path = expand(merged["sync_state_path"])
        self._batch_limit = int(merged["batch_limit"])
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
        """Yield Task/Note facts for snapshot days NEW since the stored day cursor.

        Args:
            since: Ignored — the day cursor in the sync state is the cursor.

        Cursor advancement: the sync state advances to the last day of the
        batch only when the generator runs to completion; a truncated batch
        (batch_limit) re-delivers the remainder next sync, with the done-set
        absorbing what already landed.
        """
        today = self._today()
        days = self._pending_days(today)
        if not days:
            self._log(f"no pending days (cursor at {self._load_state().get('last_day')})")
            return
        batch = days[: self._batch_limit]
        truncated = len(days) > len(batch)
        self._log(
            f"walking {len(batch)} day(s) {batch[0]}..{batch[-1]}"
            + (" (truncated)" if truncated else "")
        )
        yield from self._sync_batch(batch)

    def create_relations(self, fact) -> list:
        """Link each tada Note to its Task (`contained_by`, mirroring the
        Attachment→Email precedent). Resolves the Task by the note's
        metadata.task_source_id — the Task was yielded first, so it is
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
                """SELECT uuid FROM item WHERE _type = 'Task'
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
    # Sync internals
    # ------------------------------------------------------------------

    def _sync_batch(self, batch: list[str]) -> Iterator[dict]:
        tasks_done = self._ingested_task_ids()
        notes_done = self._ingested_note_task_ids()
        realized_at = datetime.now(UTC).isoformat()
        completed = False
        try:
            for day in batch:
                for entry in self.client.list_day(day):
                    uid = entry.get("uid")
                    if not uid:
                        continue
                    if uid in tasks_done and uid in notes_done:
                        continue
                    item = self._enrich(uid)
                    for fact in task_facts(
                        entry, day, self._zone, item=item, realized_at=realized_at
                    ):
                        if fact["type"] == "Task" and uid in tasks_done:
                            continue
                        if fact["type"] == "Note" and uid in notes_done:
                            continue
                        yield fact
                    tasks_done.add(uid)
                    notes_done.add(uid)
            completed = True
        finally:
            # Advance the cursor only on full, clean consumption: by the time
            # the runtime asks for the item past the end, everything yielded
            # is already committed in Soil by the runtime itself.
            if completed:
                self._store_state(batch[-1])

    def _enrich(self, uid: str) -> dict | None:
        """Best-effort show() — followedFrom / source / notes fallback ride
        along when the store still has the item; entry-only otherwise."""
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

    def _ingested_task_ids(self) -> set[str]:
        with self._soil as soil:
            rows = soil._get_connection().execute(
                """SELECT json_extract(data, '$.source_id') AS sid FROM item
                   WHERE _type = 'Task'
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
