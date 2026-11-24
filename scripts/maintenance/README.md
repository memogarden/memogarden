# memogarden-maintenance — weekly item_fts rebuild

The item_fts lexical index accumulates superseded versions' tokens until a
rebuild (FTS5 'delete' is exactly-once; no supersede trigger — see
`schemas/sql/soil.sql` §ITEM FTS and `docs/search-verb.md` §Lifecycle sync).
These units run `Soil.rebuild_item_fts()` weekly.

## Install (user units, this host IS the server)

```bash
cp ~/memogarden/scripts/maintenance/memogarden-maintenance.{service,timer} \
   ~/.config/systemd/user/
systemctl --user daemon-reload
# verify once by hand BEFORE enabling the schedule:
systemctl --user start memogarden-maintenance.service
journalctl --user -u memogarden-maintenance --no-pager | tail -3
# then arm the weekly schedule
systemctl --user enable --now memogarden-maintenance.timer
systemctl --user list-timers memogarden-maintenance.timer
```

## Record-keeping

Copy the two unit files into `~/sysinfo/systemd/user/` and commit per
`~/sysinfo/PROTOCOLS.md` (tracked systemd user units live there).

## Files

- `rebuild_item_fts.py` — runs `Soil.rebuild_item_fts()` via the server
  venv against the canonical soil db (get_db_path; honours
  `MEMOGARDEN_DATA_DIR`), reports timing and `item_fts_data` MB before/after.
- `memogarden-maintenance.service` — oneshot; SyslogIdentifier for journal
  filtering.
- `memogarden-maintenance.timer` — Sunday 04:17 + 0-5min jitter,
  Persistent=true (laptop catch-up).
