# sessions-importer

Drain worker for the [memogarden-sessions](../../memogarden-sessions)
staging cache: pulls captured downloads and screenshots into MemoGarden
Soil as gmail-parity **Attachment** facts in the shared content-addressed
store, then deletes the staged file. The daemon's custody area is a
**staging cache**, not a residence — steady state is drained and
near-empty.

Decisions of record: `plan/rfc_013_ingestion_decision_register.md`
(RFC-013 §8 addendum).

## How it works

- **Presence-as-queue** (B2): a staged file present = pending; gone =
  drained. No done-set for files.
- **Ack-after-commit** (B2): a staged file is deleted only after its
  fact is durably committed — the delete happens when the generator is
  re-entered for the next item (the runtime commits per yielded fact
  first), or after the loop completes normally.
- **At-least-once is harmless** (B3): `(session, sha256)` dedup check
  before yielding; a replay never duplicates a fact.
- **Provenance folded** (D2/C1): the daemon's `download_captured` event
  (url, captured_at, downloaded_by, size, content type) becomes the
  Attachment fact's metadata. Pre-provenance files drain with
  `captured_at` from file mtime, `url=None`, `downloaded_by=unknown`.
- **Partials untouched** (B6): `.crdownload` / `.part` / `.tmp` skipped.
- **Loss-free** (D-3): undrained captures are never evicted; the cache
  simply grows while MemoGarden is unreachable, then drains.

## Fact shape

gmail-parity `Attachment` (D1) so the statement pipeline consumes both
origins uniformly:

- `data`: `filename, mime_type, size, sha256, title, content_path`
- `metadata`: `provider=memogarden-sessions, source=browser_session,
  stream=downloads|screenshots, session, machine, account=<session>,
  url, downloaded_by, captured_at, storage_root, drained_at`

`metadata.account` carries the session name (RFC-012 attribution) —
scope rules and triage relabels key on it.

## Scoping (D-4)

All downloads land in **Triage** (the runtime's fail-closed default);
`Finances` is the relabel destination. History/events streams (future
slices) will ride null-scope rules keyed on `metadata.stream`.

## Registration

```bash
# from memogarden-client (verb-first grammar)
memogarden register provider sessions \
  --module sessions_importer.importer --class SessionsImporter \
  --interval 900 \
  --config '{"sessions_root": "~/.local/share/memogarden/sessions", "store_root": "~/.local/share/memogarden/attachments"}'

# scope policy: fail-closed default (explicit, for the record)
curl -X PUT "$MG_URL/api/v1/providers/sessions/scope-policy" ... \
  -d '{"default": "triage"}'
```

The provider runs in-server on the heartbeat; after a session closes,
trigger immediately with `POST /api/v1/providers/sessions/run` (the
daemon will do this itself once the restricted key class lands —
register item E3).

## Config

| key | default |
|---|---|
| `sessions_root` | `~/.local/share/memogarden/sessions` |
| `store_root` | `~/.local/share/memogarden/attachments` |

## Tests

```bash
PYTHONPATH="$PWD/providers/sessions-importer:$PWD/memogarden-utils:$PWD/memogarden-system" \
  memogarden-api/.venv/bin/python -m pytest providers/sessions-importer/tests/ -q
```
