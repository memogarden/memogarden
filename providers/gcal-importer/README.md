# gcal-importer — Google Calendar provider (RFC-014)

Mirrors each concrete calendar event into a **Core Event entity** (the
mutable belief — "the schedule as the user believes it"). Soil facts are
created only by the mark-occurred review action (`POST
/events/{uuid}/mark-occurred`, RFC-014 Phase B), never by ingestion and
never when a date passes.

First ENTITY_UPSERTS provider: `sync()` yields
`{"op": "upsert_entity", "type": "Event", "key", "data", "metadata"}`
dicts under the RFC-014 §3 runtime contract extension; the runtime owns
storage (natural-key lookup → etag no-op/update/create, scope policy,
EntityDelta authoring, per-item commit). Events are individual
instantiations (`singleEvents=true` — Google expands recurrence); a
series links its instances via native `recurring_event_id`.

## Engine

- **Incremental first** per calendar: `events.list(syncToken,
  singleEvents=true)`, identical parameter set every call (API contract).
  `cancelled` tombstones of stored events yield stamp-only upserts
  (`source_status`/`removed_at_source`/`detection_source`) — the entity
  keeps its belief fields; unknown tombstones are skipped.
- **Seed**: full listing, **no timeMin** (full history — the settled
  backfill decision). Past-at-seed events carry `captured_after_fact`
  (review triage marker).
- **410 GONE** → token cleared, inline reseed, forced audit.
- **Audit** (monthly, `audit_interval_days`): full listing diffed against
  the etag map. Load-bearing: master deletions / RRULE shortening tombstone
  only the master (which we never store) — instances vanish silently and
  surface ONLY here; newly materialized instances also arrive here.
- **Cursor discipline** (gmail/tada precedent): the per-subject state file
  advances only when the sync generator runs to completion; budgets defer,
  never fail; re-delivery is absorbed by the etag no-op rule.

## Config (provider or account level; account overrides)

| Key | Default | Meaning |
|---|---|---|
| `calendar_ids` | `[]` → owner-role calendars | Allowlist; `list-calendars` CLI to discover |
| `batch_limit` | 500 | Upserts per run before deferral |
| `page_budget` | 40 | `events.list` pages per run |
| `participants_max` | 50 | Trim participants; true count in `attendee_count` |
| `audit_interval_days` | 30 | Audit cadence (forced post-reseed) |

## Credentials

Shares the `google:{subject}` HACM entry with the gmail provider (RFC-012
§3) — **revoking it disconnects gmail + gcal together**. The scope set
lives in `gmail-oauth.GOOGLE_SCOPES` (widened with `calendar.readonly` in
RFC-014 §5); run `auth-login` once to re-consent and refresh the stored
token in place.

## CLI

```bash
python -m gcal_importer.ingest auth-login --account me@gmail.com --register
python -m gcal_importer.ingest list-calendars --account me@gmail.com
```

## Registration

```bash
memogarden register provider --name gcal \
  --module gcal_importer.provider --class GoogleCalendarProvider \
  --interval 3600 --description "Google Calendar → Core Event entities (RFC-014)"
```

Scope posture: registered with NO scope policy → the ratified fail-closed
default routes events to triage; per-calendar rules can key on
`metadata.calendar_id` later (RFC-011 §4.8).
