# Database Index Inventory

**Status:** measured 2026-11-27 against production (`~/.local/share/memogarden/{core,soil}.db`, read-only) and cross-checked against the declared schema (`schemas/sql/{core,soil}.sql`). Zero drift: every declared index exists live; nothing exists live that isn't declared or documented below.

> **Framing (operator ruling, 2026-11-27):** indexes are **not part of the
> canonical MemoGarden schema**. The canonical schema is the data model —
> tables, columns, types, and the entity/fact YAML contracts under
> `schemas/`. Indexes are **accepted optimizations**: an implementation
> concern of the current code, expected to change as implementations
> change. Nothing should treat an index's presence, name, or shape as an
> API or data-model contract. Consumers must stay correct without any
> index existing; the unique indexes below are the one partial exception
> (they enforce integrity, not performance — see §Integrity).

## core.db — declared in `schemas/sql/core.sql` (17)

| index | table/columns | kind |
|---|---|---|
| `idx_entity_type` | entity(type) | index |
| `idx_entity_hash` | entity(hash) | index |
| `idx_entity_previous_hash` | entity(previous_hash) | index |
| `idx_entity_updated` | entity(updated_at) | index |
| `idx_userrel_source` | user_relation(source) | index |
| `idx_userrel_target` | user_relation(target) | index |
| `idx_userrel_horizon` | user_relation(time_horizon) | index |
| `idx_context_frame_participant` | context_frame(participant) | index |
| `idx_context_frame_project` | context_frame(project_uuid) | index |
| `idx_context_frame_active_scope` | context_frame(active_scope) | index |
| `idx_users_username` | users(username) | index |
| `idx_api_keys_user_id` | api_keys(user_id) | index |
| `idx_api_keys_active` | api_keys(revoked_at) WHERE revoked_at IS NULL | partial |
| `idx_providers_due` | providers(enabled, next_due_at) | index |
| `idx_provider_runs_provider` | provider_runs(provider_name, started_at DESC) | index |
| `idx_provider_runs_single_flight` | provider_runs(provider_name, account_id) WHERE status = 'running' | **partial UNIQUE** |
| `idx_provider_accounts_due` | provider_accounts(provider_name, status, next_due_at) | index |

Plus derived/infra objects (declared, from the change-feed batch, system#12):
`change_log` tables + `entity_change_{insert,update,delete}` triggers —
infrastructure, not indexes, listed here because they ride the same
"not canonical schema" framing.

## core.db — runtime-ensured (deliberately NOT in the schema file)

- `entity_fts` (FTS5 external-content over `entity.data`) + the
  `entity_fts_{ai,ad,au}` triggers. Runtime-ensured on every core open by
  operator ruling (2026-11-27): entity `data` mutates in place, an AU
  trigger is required, and the object is internal derived metadata —
  rebuilt, never migrated.

## soil.db — declared in `schemas/sql/soil.sql` (7)

| index | table/columns | kind |
|---|---|---|
| `idx_item_type` | item(_type) | index |
| `idx_item_realized` | item(realized_at) | index |
| `idx_item_canonical` | item(canonical_at) | index |
| `idx_item_fidelity` | item(fidelity) | index |
| `idx_sysrel_source` | system_relation(source) | index |
| `idx_sysrel_target` | system_relation(target) | index |
| `idx_sysrel_kind` | system_relation(kind) | index |

Plus derived/infra objects: `change_log` + `item_change_{insert,update,delete}`
and `sysrel_change_{insert,update,delete}` triggers (change-feed batch);
`item_fts` (FTS5, declared) with `item_fts_{ai,ad}` triggers only —
**insert-only by design** (facts are immutable; query-time staleness is
handled by the search ladder's live re-check, not by an AU trigger).

## Integrity-carrying unique indexes

The two partial UNIQUE indexes are the only index-shaped objects whose
absence changes behavior rather than speed:

- `idx_provider_runs_single_flight` — one running row per
  (provider, account); the DB-level half of provider single-flight.
- `idx_event_verdict_identity` (system#5, 2026-11-27) — one Event verdict
  fact per live event entity:
  `item(json_extract(data,'$.event_uuid')) WHERE _type='Event'
  AND superseded_by IS NULL AND json_extract(data,'$.event_uuid') IS NOT NULL`.
  Complements mark-occurred/dismiss's process-local `_MARK_LOCK` +
  in-transaction re-probe (api/v1/events.py): the lock's guarantee is
  per-process, the index makes one-verdict-per-event hold across
  processes. Applied via the soil executescript-at-boot mechanism
  (`init_system` → `Soil.init_schema()`), the same path as the api#95
  item_fts lifecycle triggers — no core migration needed.

## How indexes ship

- **core.db** changes: edit `schemas/sql/core.sql` **and** add a
  `migrate_<from>_to_<to>.sql` under
  `memogarden-system/system/schemas/sql/migrations/` (bump
  `EXPECTED_SCHEMA_VERSION` + chain) — the core runner is the only
  migration machinery.
- **soil.db** changes: edit `schemas/sql/soil.sql` only —
  `Soil.init_schema()` re-executes the file (all `IF NOT EXISTS`) at
  every app boot, so existing DBs converge at the next restart. No
  version bump required (api#95 precedent).
- Both files load from the `system/schemas/memogarden` submodule pin
  (vendored copy of the root repo), so a schema change ships as:
  root commit → push root → bump the system repo's submodule pin.
