-- MemoGarden Soil Database Schema
-- Schema Version: 20261124
-- Description: Base Soil tables + change feed (system#12 search-sync slice)
--
-- DESIGN:
-- - item: Polymorphic timeline with JSON data field
-- - system_relation: Immutable structural facts
-- - change_log: Trigger-maintained write feed (monotonic seq per DB)
-- - Type-specific schemas in /schemas/types/items/*.schema.json

-- ============================================================================
-- SCHEMA METADATA
-- ============================================================================

CREATE TABLE IF NOT EXISTS _schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO _schema_metadata VALUES
    ('version', '20261124', datetime('now')),
    ('base_schema', 'memogarden-soil-base', datetime('now')),
    ('description', 'Soil base tables only; type schemas in JSON', datetime('now')),
    ('epoch_date', '2020-01-01', datetime('now'));

-- ============================================================================
-- ITEM TABLE (Polymorphic timeline with JSON data)
-- ============================================================================

CREATE TABLE IF NOT EXISTS item (
    uuid TEXT PRIMARY KEY,              -- soil_ prefix
    _type TEXT NOT NULL,                -- 'Note', 'Message', 'Email', etc.
    realized_at TEXT NOT NULL,          -- ISO 8601 (system time)
    canonical_at TEXT NOT NULL,         -- ISO 8601 (user time)
    integrity_hash TEXT,                -- SHA256 of content
    fidelity TEXT NOT NULL DEFAULT 'full',
    superseded_by TEXT,
    superseded_at TEXT,
    
    -- Type-specific data (validated by /schemas/types/items/{_type}.schema.json)
    data JSON NOT NULL,
    
    -- Provider-specific metadata (not validated)
    metadata JSON
);

CREATE INDEX IF NOT EXISTS idx_item_type ON item(_type);
CREATE INDEX IF NOT EXISTS idx_item_realized ON item(realized_at);
CREATE INDEX IF NOT EXISTS idx_item_canonical ON item(canonical_at);
CREATE INDEX IF NOT EXISTS idx_item_fidelity ON item(fidelity);

-- ============================================================================
-- ITEM FULL-TEXT INDEX (derived, rebuildable — api#95 Phase 1)
-- ============================================================================

-- External-content FTS5 over item.data: zero text duplication — the index
-- stores only the inverted term index and reads `data` back from `item`
-- (content_rowid = item.rowid) when snippets or the 'rebuild' command need
-- the original text. Default unicode61 tokenizer: whole-word latin matching
-- with BM25 ranking. CJK runs are NOT tokenized by unicode61, so CJK recall
-- stays on the LIKE paths (parity with pre-FTS behavior).
--
-- Maintenance is structural: items are append-only (no UPDATE ever touches
-- `data`; no DELETE FROM item exists), so a single AFTER INSERT trigger
-- keeps the index complete for everything inserted after it was created.
-- AU/AD triggers are deliberately absent — a supersede UPDATE never
-- changes `data`.
--
-- Legacy rows (inserted before this DDL first executed) are backfilled
-- incrementally by Soil.backfill_item_fts(), keyed off the
-- item_fts_created_maxrowid boundary recorded below. The boundary is the
-- max(item.rowid) at the moment the trigger was created; everything above
-- it is guaranteed trigger-indexed, so backfill batches can never
-- double-index a row (a duplicate external-content insert corrupts the
-- index).
--
-- Caveat: anything that removes or renumbers item rowids desyncs an
-- external-content index — VACUUM (may renumber rowids of tables without
-- an INTEGER PRIMARY KEY) and bulk DELETE FROM item (the index has no AD
-- trigger; the test harness cleans up with a rebuild for exactly this
-- reason). No maintenance path does either today (RFC-007 `maintain
-- --vacuum` is unbuilt); if one ever ships, rebuild afterwards:
--   Soil(...).rebuild_item_fts()  (atomic, lock-heavy at prod corpus size)
--
-- The section below is wrapped in a single transaction: the boundary stamp
-- must be atomic with trigger creation or a concurrent insert (documented:
-- account runs collide with restarts) could be trigger-indexed AND
-- backfilled — a duplicate external-content insert corrupts the index
-- (phantom-term false positives, wrong bm25 ranks). executescript honors
-- in-script transaction control; IF NOT EXISTS / INSERT OR IGNORE keep the
-- replay idempotent.

BEGIN IMMEDIATE;

CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(
    data,
    content='item',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS item_fts_ai AFTER INSERT ON item BEGIN
    INSERT INTO item_fts(rowid, data) VALUES (NEW.rowid, NEW.data);
END;

-- Record the legacy/pre-trigger rowid boundary once (INSERT OR IGNORE keeps
-- the original value on every subsequent server start).
INSERT OR IGNORE INTO _schema_metadata VALUES
    ('item_fts_created_maxrowid', (SELECT COALESCE(MAX(rowid), 0) FROM item),
     datetime('now')),
    ('item_fts_index',
     'external-content over item.data; api#95 Phase 1; legacy rows backfilled '
     || 'via backfill_item_fts()', datetime('now'));

COMMIT;

-- ============================================================================
-- SYSTEM RELATION TABLE (Immutable structural facts)
-- ============================================================================

CREATE TABLE IF NOT EXISTS system_relation (
    uuid TEXT PRIMARY KEY,              -- soil_ prefix
    kind TEXT NOT NULL,                 -- 'triggers', 'cites', 'replies_to', etc.
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- 'item' | 'entity'
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    created_at INTEGER NOT NULL,        -- Days since epoch
    evidence JSON,
    metadata JSON,
    
    UNIQUE(kind, source, target)
);

CREATE INDEX IF NOT EXISTS idx_sysrel_source ON system_relation(source);
CREATE INDEX IF NOT EXISTS idx_sysrel_target ON system_relation(target);
CREATE INDEX IF NOT EXISTS idx_sysrel_kind ON system_relation(kind);

-- ============================================================================
-- CHANGE FEED (memogarden-system#12 slice — search-sync high-water mark)
-- ============================================================================
-- Trigger-maintained feed of every write to the content tables, in the same
-- transaction as the write it records (rollback removes the feed rows too —
-- the feed never reports a change that did not commit). Served to derived
-- consumers (FTS5 lexical index, embed-daemon vector catch-up, offline index
-- rebuilds) as the monotonic "what changed since seq N" sweep.
--
-- Why triggers, not call-site instrumentation: writers reach these tables
-- through many paths — Soil methods, raw conn.execute repairs
-- (system/core/__init__.py §migrate_private_scope_rename), migration SQL,
-- provider runtimes, CLI passes — and any instrumented-method list silently
-- misses the next raw-SQL writer. The DB fires on all of them.
--
-- seq is INTEGER PRIMARY KEY AUTOINCREMENT: monotonic per database, never
-- reused (SQLite guarantees max(seq) grows); WAL single-writer serializes
-- concurrent feed appends. Consumers checkpoint their high-water seq.
--
-- Boundary: content tables only (item, system_relation). Infra tables the
-- api owns directly (providers, provider_runs, ...) are outside the feed —
-- search and derived views never consume them.
--
-- Pruning: append-only by design; prune from the head once every consumer
-- has checkpointed past it (no consumer checkpoints exist yet — leave whole).

CREATE TABLE IF NOT EXISTS change_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tbl TEXT NOT NULL,                  -- 'item' | 'system_relation'
    uuid TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- 'insert' | 'update' | 'delete'
    changed_at TEXT NOT NULL            -- UTC ISO 8601 (trigger time)
);

-- AFTER UPDATE with a content guard: identical-value rewrites (idempotent
-- repair loops re-writing the same text) do not pollute the feed. Recursive
-- triggers are OFF by default, so trigger bodies that UPDATE the row
-- (entity_update_timestamp in core.sql) do not double-fire here.
CREATE TRIGGER IF NOT EXISTS item_change_insert
AFTER INSERT ON item
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('item', NEW.uuid, 'insert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS item_change_update
AFTER UPDATE ON item
    WHEN NEW._type IS NOT OLD._type
      OR NEW.realized_at IS NOT OLD.realized_at
      OR NEW.canonical_at IS NOT OLD.canonical_at
      OR NEW.integrity_hash IS NOT OLD.integrity_hash
      OR NEW.fidelity IS NOT OLD.fidelity
      OR NEW.superseded_by IS NOT OLD.superseded_by
      OR NEW.superseded_at IS NOT OLD.superseded_at
      OR NEW.data IS NOT OLD.data
      OR NEW.metadata IS NOT OLD.metadata
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('item', NEW.uuid, 'update', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS item_change_delete
AFTER DELETE ON item
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('item', OLD.uuid, 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS sysrel_change_insert
AFTER INSERT ON system_relation
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('system_relation', NEW.uuid, 'insert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS sysrel_change_update
AFTER UPDATE ON system_relation
    WHEN NEW.kind IS NOT OLD.kind
      OR NEW.source IS NOT OLD.source
      OR NEW.target IS NOT OLD.target
      OR NEW.source_type IS NOT OLD.source_type
      OR NEW.target_type IS NOT OLD.target_type
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.evidence IS NOT OLD.evidence
      OR NEW.metadata IS NOT OLD.metadata
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('system_relation', NEW.uuid, 'update', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS sysrel_change_delete
AFTER DELETE ON system_relation
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('system_relation', OLD.uuid, 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

-- ============================================================================
-- COMMON QUERIES
-- ============================================================================

-- Q1: Get items by type
-- SELECT * FROM item WHERE _type = 'Email' ORDER BY canonical_at DESC;

-- Q2: Get timeline in date range
-- SELECT * FROM item 
-- WHERE canonical_at BETWEEN '2025-01-01' AND '2025-01-31'
-- ORDER BY canonical_at;

-- Q3: Find relations involving an item
-- SELECT * FROM system_relation WHERE source = ? OR target = ?;
