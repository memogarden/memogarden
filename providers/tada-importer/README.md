# tada-importer

Ingests the user's [tada](https://tada.ngjunsiang.me) task history into
MemoGarden as **Task facts** (generic type, `schemas/task.yaml`), with item
notes stored as linked **Note** facts. Registered provider name: `tada`.

## Shape

- **Source**: tada's HTTP command bridge (`POST /cmd`) on `tada serve` —
  never the store files. tada may move to another LAN machine or a cloud
  host; that is a `base_url` config change. Note: tada serve currently has
  **no auth** — keep `base_url` localhost-bound until tada grows credentials.
- **Enumeration**: day-walk over `list --day` — snapshot entries carry
  `outcome` (`done`/`cancelled`), `at` (resolution time), optional `notes`,
  `start`/`duration`, `tags`. A day with no snapshot yields no entries.
  Today's snapshot is still mutating, so the walk stops at **yesterday**.
- **Enrichment**: one best-effort `show <uid>` per new entry, harvesting
  `followedFrom` (→ `metadata.follows_from`), tada's own `source`
  (→ `metadata.tada_source`), and a notes fallback for pre-notes entries.
  Failure is non-fatal (entry-only fact).
- **Notes**: entries with non-empty notes produce a Note fact
  (`Note.description`), linked to its Task via a `contained_by` relation —
  the Attachment→Email precedent. The Task is yielded before its Note so the
  relation resolves against the already-stored Task.
- **Mapping**: `outcome done → status completed`, `cancelled → cancelled`;
  `at → status_updated_at`; naive local wall times convert to UTC using the
  zone from `/health`. `skipped` is schema-reserved (tada records no skips).

## Cursor and dedup

Provider-managed state in `~/.local/share/memogarden/tada-sync-state.json`
(`last_day`), advanced only when a sync batch completes cleanly; the
runtime's `since` timestamp is ignored (day-keyed, not timestamp-keyed).
Dedup is a done-set over the task's native uid, tracked **separately for
Task facts and their Note facts** — a run that died between a Task and its
Note re-delivers just the Note next sync, and `create_relations` resolves
the already-stored Task. With no stored cursor, the walk starts at
`backfill_since` (default `2019-08-19`, the archive's first day) and is
capped at `batch_limit` days per sync.

Coverage caveat: cancellations and inline notes exist only in snapshots
written by tada §10.57+ — historical entries carry neither, so note/Note and
cancelled coverage starts where tada started writing them (pre-existing
entries were migrated to the `outcome`/`at` shape, titles/tags/times are
universal).

## Provider registration

```bash
memogarden register provider \
  --name tada \
  --module tada_importer.provider \
  --class TadaProvider \
  --interval 3600 \
  --description "tada task history ingestion"
```

The module must be importable in the API server's venv — a one-line
`tada_importer.pth` in `memogarden-api/.venv/lib/python3.14/site-packages/`
pointing at this checkout (same mechanism as `gmail_importer`), then restart
`memogarden.service`. The origin pin (RFC-004 §12.2) already allowlists the
workspace `providers/` root.

## Config (registry `config` JSON; references only, no secrets)

| key | default | meaning |
|---|---|---|
| `base_url` | `http://127.0.0.1:7427` | tada serve root |
| `backfill_since` | `2019-08-19` | first day walked with no cursor |
| `batch_limit` | `400` | max snapshot days per sync |
| `sync_state_path` | `~/.local/share/memogarden/tada-sync-state.json` | day cursor |
| `zone` | from `/health` | IANA zone for naive wall times |
| `request_timeout` | `30.0` | per-request timeout (s) |
| `soil_path` | runtime default | explicit Soil path override |

## Tests

```bash
PYTHONPATH=providers/tada-importer memogarden-api/.venv/bin/python -m pytest providers/tada-importer/tests
```
