---
name: memogarden-schema
description: SQLite schema modification workflow for MemoGarden — the two
  change paths (core-side migration runner vs soil-side self-applies),
  version-bump checklist, submodule pin mechanics, index framing. Use when
  adding tables/columns/indexes or touching the schema files.
---

# Working with SQLite Schema

> **Rewritten 2026-11-28** — the previous version taught the dead
> `memogarden-core` layout. This version reflects the verified mechanisms
> (exercised by schema 20261128/20261129 on 2026-11-28).

## Where the schema lives

- **Canonical**: `/schemas/sql/core.sql` + `/schemas/sql/soil.sql` (root
  repo) — single source of truth, versioned by banner + the
  `_schema_metadata` seed row inside each file. YAML type contracts live in
  `/schemas/*.yaml`, machine schemas in `/schemas/types/`.
- **Bundled copy**: `memogarden-system/system/schemas/memogarden/` — a
  gitlink (submodule) pin of the ROOT repo. This copy is what the server
  actually loads (loader order: importlib.resources → submodule mount →
  workspace fallback). A schema change ships as: **root commit → push root →
  bump the system repo's submodule pin**.
- **Migrations**: `memogarden-system/system/schemas/sql/migrations/
  migrate_<from>_to_<to>.sql` (plain files in the system repo).
- **Load resolution** (`system/schemas.py::get_sql_schema`): importlib
  resources → `system/schemas/memogarden/schemas/sql/<layer>.sql` →
  `system/schemas/sql/<layer>.sql` → `<workspace>/schemas/sql/<layer>.sql`.

## THE TWO CHANGE PATHS — pick correctly

### Core-side change (provider_runs, api_keys, entity, users, …)

Edit `core.sql` **and** add `migrations/migrate_<from>_to_<to>.sql`, **and**
bump `EXPECTED_SCHEMA_VERSION` + `SCHEMA_VERSION_CHAIN` in
`system/core/__init__.py`. The runner (`_run_migrations`) walks the chain at
every `init_db()` — i.e. at app boot. Fresh DBs seed at EXPECTED directly
from core.sql. Exercises: 20261124 (change feed), 20261128 (run tagging),
20261129 (key descriptions).

### Soil-side change (item, system_relation, item_fts, …)

Edit `soil.sql` **only**. `init_system` calls `Soil.init_schema()`
**unconditionally at every boot** (executescript, all `IF NOT EXISTS`), so
existing DBs converge at the next restart — no migration file, **no
EXPECTED bump** (soil's version banner is informational; api#95's
item_fts triggers landed exactly this way). Pre-flight any
possibly-failing DDL read-only first (e.g. a new UNIQUE index fails hard
if existing data violates it).

## Version-bump checklist (core-side; every step has bitten a session)

1. `core.sql`: banner line 2 + `_schema_metadata` seed row + the DDL.
2. `EXPECTED_SCHEMA_VERSION` + `SCHEMA_VERSION_CHAIN` append.
3. Migration file: `BEGIN IMMEDIATE` … DDL … `UPDATE _schema_metadata SET
   value='<new>'`. Additive ALTERs only (SQLite has no DROP-COLUMN-IF-
   EXISTS guard); **new columns must be strippable** for fixtures.
4. **Version-guard tests** assert the literal: `test_agent_identity.py`
   (`test_expected_version_is_…`), `test_rfc011_scope_grants.py`
   (`== EXPECTED_SCHEMA_VERSION == "<ver>"`). Update both.
5. **Fixtures that simulate old DBs must STRIP the new columns** (seed
   fresh → stamp version back → DROP the new columns → walk the chain):
   `test_change_feed.py::migrated_db` fixture and every
   `*migration_converges*` test. A real old DB never carried them.
6. Make new migration tests **EXPECTED-relative** (`assert version ==
   EXPECTED_SCHEMA_VERSION`), never hardcoded — future bumps stop
   breaking them.
7. Submodule pin: root push → `git submodule update` / checkout in the
   system worktree → commit the gitlink.
8. Suite green (system; then api — whose tests resolve schemas through
   the MAIN system checkout, since the editable install's finder beats
   PYTHONPATH there).

## Indexes — NOT canonical schema (operator framing, 2026-11-28)

Indexes are accepted optimizations expected to change with
implementation; consumers must stay correct without them. Inventory +
framing: `docs/database-indexes.md`. The partial UNIQUE indexes
(`idx_provider_runs_single_flight`, `idx_event_verdict_identity`) are the
integrity-carrying exception — document any new one there.

## Conventions (unchanged, still binding)

- Raw SQL, parameterized; **no ORM**; SQLite only.
- UTC ISO 8601 everywhere via `utils.isodatetime`; uuids via
  `utils.uid` / `system.soil.generate_soil_uuid` (plain in storage,
  `core_`/`soil_` prefixes added at the API layer).
- WAL + `foreign_keys=ON` + `busy_timeout=5000` are set in both
  connection factories — don't unset.
- **Multi-statement write sequences use `Soil.write_txn()`**
  (BEGIN IMMEDIATE, system#15): one deterministic lock acquisition;
  Python's auto-begun DEFERRED transactions starve under concurrent
  writers. Single-INSERT writes are fine as-is.
- Facts are immutable: changes are amends (new version + supersedes);
  entities mutate in place via `core.entity.update_data`.
- JSON `data`/`metadata` columns; scopes live at `data.scopes`
  (entities) / `metadata.scopes` (facts), plain uuids.

## Testing

- Fresh-DB check: `init_db()` into a temp `MEMOGARDEN_DATA_DIR`, assert
  columns/version; existing-DB check: seed, stamp version back, strip
  new columns, run the migration/`init_db()` again, assert convergence.
- Worktree suites: `git submodule update --init` FIRST (missing
  submodule = 5 confusing type-schema test failures).
- After schema changes: `PRAGMA integrity_check;` on a copy.
- Prod pre-flights are read-only URIs:
  `sqlite3 "file:$HOME/.local/share/memogarden/core.db?mode=ro" …`.

## Non-goals / traps

- No ORM, no new deps, no async.
- `key_prefix` on api_keys is a static type tag, not an identifier.
- The full migration mechanism doc (`plan/future/migration-mechanism.md`)
  is design-reference; the runner in `system/core/__init__.py` is the
  implemented truth.
