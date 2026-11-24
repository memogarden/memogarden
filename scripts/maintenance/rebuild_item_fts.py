#!/usr/bin/env python3
"""Weekly item_fts rebuild — the FTS dead-token de-bloat lever.

The item_fts index carries superseded versions' tokens until a rebuild:
there is deliberately no AU trigger because FTS5 'delete' has exactly-
once semantics and an AU+AD pair double-fires into corruption (see
schemas/sql/soil.sql §ITEM FTS and docs/search-verb.md §Lifecycle sync).
Soil.rebuild_item_fts() re-indexes every row from scratch, resetting
dead-token mass to "every superseded row that exists today".

Scheduled by memogarden-maintenance.timer (weekly, Sunday 04:17,
Persistent=true — the laptop catches up on missed runs at next boot).

Run (server venv — `system` is an editable install there):
    %h/memogarden/memogarden-api/.venv/bin/python %h/memogarden/scripts/maintenance/rebuild_item_fts.py

Notes:
- rebuild holds the FTS write lock for its duration (WAL: readers keep
  serving; writers queue behind it — the hourly ingest heartbeat
  serializes via SQLite busy handling).
- a rebuild re-indexes ALL rows including currently-superseded ones, so
  dead-token mass resets to the current superseded set, not zero.
"""
import sqlite3
import sys
import time

from system.host.environment import get_db_path
from system.soil.database import Soil


def fts_data_mb(db) -> float:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT ROUND(SUM(pgsize)/1048576.0, 1) FROM dbstat "
            "WHERE name = 'item_fts_data'").fetchone()
        return row[0] if row and row[0] is not None else 0.0
    finally:
        con.close()


def main() -> int:
    db = get_db_path("soil")
    if not db.exists():
        print(f"soil db not found: {db}", file=sys.stderr)
        return 2
    before = fts_data_mb(db)
    t0 = time.time()
    with Soil(db) as soil:
        soil.rebuild_item_fts()
    after = fts_data_mb(db)
    print(f"item_fts rebuild: {time.time() - t0:.1f}s, "
          f"item_fts_data {before} MB -> {after} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
