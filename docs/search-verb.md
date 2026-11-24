# Search Verb (Session 9)

**Status:** ✅ Complete — updated 2026-11-24 (api#95 phase 1: `strategy: lexical` FTS5 BM25 over fact data; phase 2a `semantic`/`hybrid` staged on branch `feat/search-verb-semantic-hybrid`, pending merge; see the strategy section and "The FTS Index" below)
**Date:** 2026-02-09
**Tests:** search verb tests are part of memogarden-api's suite (541 passing 2026-11-24, incl. the lexical and staged semantic/hybrid strategies)

## The FTS Index (`item_fts`)

The lexical plane's index is **not a separate database** — it is an FTS5
virtual table living **inside `soil.db` itself** (the same single file as
every other table), plus its FTS5 "shadow tables". Measured 2026-11-24 at
~266k live items: `item` ≈ 2.4 GB, `item_fts_data` ≈ 600 MB (the other
shadow tables are housekeeping-scale). No extra files beyond soil.db's own
WAL — backups and RPi replication stay single-file.

### External content — document text is not duplicated

```sql
CREATE VIRTUAL TABLE item_fts USING fts5(
    data,
    content='item',          -- documents live in item.data
    content_rowid='rowid'    -- index keyed by item rowid
)
```

`content='item'` makes this an **external-content** index: text is stored
once, in `item.data`; the index stores only the inverted index
(token → rowid). Two consequences: the index is *derived state* — fully
reconstructible from `item` at any time — and any writer that changes or
removes `item` rows without telling the index will desync it (see the
lifecycle gap below).

### Maintenance lifecycle (trigger-maintained, never app-managed)

The system#12 change_log rationale applies verbatim: writers may bypass
Python, triggers cannot be bypassed. So the index is maintained by:

- **`item_fts_ai`** — `AFTER INSERT ON item` trigger indexing every new
  row: `INSERT INTO item_fts(rowid, data) VALUES (NEW.rowid, NEW.data)`.
- **Atomic boundary stamp** — creating the trigger records
  `item_fts_created_maxrowid` in `_schema_metadata`, so the trigger and
  the backfill can never overlap on one row (a duplicate external-content
  insert corrupts the index).
- **`Soil.backfill_item_fts()`** — resumable watermark sweep
  (`item_fts_backfill_rowid`) for rows predating the DDL; one
  `BEGIN IMMEDIATE` transaction per batch. Completed 2026-11-24
  (266,458 rows; a second run is a no-op).
- **`Soil.rebuild_item_fts()`** — the FTS5 `'rebuild'` command plus a
  done marker; full reconstruction from `item`. Rebuild parity
  (incremental state == rebuild output) is the standing integrity check.

### Lifecycle sync (shipped 2026-11-25) + the dead-token bloat caveat

`item_fts_ad` (AFTER DELETE) issues the FTS5 `'delete'` command with
`OLD.data`, so hard DELETEs leave the index immediately — closing the
corruption vector where a deleted rowid, reused by SQLite for a new row,
would make ghost tokens resolve to a *different* item's content. This is
not hypothetical: gcal occurrence churn inserted-then-deleted 811 items on
2026-11-24 alone (change_log evidence).

Two deliberate non-features, both consequences of FTS5 `'delete'` having
**exactly-once semantics** — a duplicate or mismatched `'delete'`
corrupts the index (observed live as "database disk image is malformed"
when an earlier AU+AD trigger pair double-fired):

- **No AU trigger**: a supersede flip does not un-index. An AU `'delete'`
  would double-fire with the row's eventual DELETE. Superseded rows still
  match by default (parity with the fuzzy path, unchanged from Phase 1);
  `live_only=True` keeps the console's results correct.
- **No data-amend trigger**: `data` is append-only (verified: the only
  `UPDATE item` paths touch lifecycle columns).

**Bloat caveat & resolution.** Because supersede flips don't un-index,
superseded versions' tokens accumulate until a rebuild — skewing bm25
statistics and growing the index (`item_fts_data` ≈ 600 MB baseline at
~266k items). Resolution: periodic `Soil.rebuild_item_fts()`, which
re-indexes every row from scratch and resets dead-token mass to "every
superseded row that exists today" (bounded, not zero). Scheduled **weekly**
by the `memogarden-maintenance` user timer (`scripts/maintenance/` —
Sunday 04:17, `Persistent=true` for the laptop; rebuild holds the FTS
write lock for its duration, readers keep serving, writers queue behind
it). Watch `item_fts_data` size via `dbstat`; if weekly feels frequent
the cost is one ~minutes-long locked write per week. Do **not** add a
"purge superseded" sweep instead — a per-row `'delete'` sweep can
double-fire exactly like the AU did, unless it grows marker
bookkeeping, which is not worth it at this scale.

### Query path

`Soil.search_items_fts()` hardens raw query text into safe MATCH syntax
(quoted prefix terms, implicit AND — injection-proof against every FTS5
operator token), ranks with `bm25()` (more negative = better) with a rowid
tiebreak, and enforces RFC-011 §4.9(3) scopes **pre-match** (scoped-out
rows never become matches, so counts stay truthful). `live_only`, `types`,
and `scope_filter` are post-MATCH refinements (console chips). The
tokenizer is `unicode61` — CJK runs are not tokenized, which is why the
console ladder falls through to narrow LIKE (`keyword_fallback`) for
CJK/substring queries.

### Two planes, two storage models

| plane | index | lives in | maintained by |
|---|---|---|---|
| lexical | `item_fts` (FTS5, external-content) | inside `soil.db` — one file with everything | triggers + backfill/rebuild |
| semantic | `search_index.npz` + `search_index_ids.json` + `search_meta.json` | derived cache files owned by [memogarden-search](https://github.com/memogarden/memogarden-search) (`MEMOGARDEN_SEARCH_CACHE`) | offline builder; vectors never touch the DB |

## Overview

Implemented the `search` verb for semantic search and discovery across entities (Core) and facts (Soil). Provides fuzzy text matching with configurable coverage levels and effort modes.

## Architecture

### Request Schema

```json
{
  "op": "search",
  "query": "search terms",
  "target_type": "entity|fact|all",
  "coverage": "names|content|full",
  "strategy": "fuzzy|auto",
  "effort": "quick|standard|deep",
  "limit": 100
}
```

### Response Schema

```json
{
  "ok": true,
  "result": {
    "query": "search terms",
    "target_type": "entity",
    "coverage": "content",
    "strategy": "auto",
    "effort": "standard",
    "count": 42,
    "results": [
      {
        "kind": "entity",  // or "fact"
        "uuid": "core_abc123...",
        "type": "Transaction",
        "data": { ... }
      }
    ]
  }
}
```

## Search Parameters

### `target_type` - Search Scope
- `entity` - Search Core entities only
- `fact` - Search Soil items/facts only
- `all` - Search both entities and facts (default)

### `coverage` - Search Depth
- `names` - Title/name fields only (fastest)
  - Entities: `type` field
  - Facts: `_type` field
- `content` - Names + body text (default)
  - Entities: `type` + `data` fields
  - Facts: `_type` + `data` fields
- `full` - All searchable fields including metadata
  - Entities: `type` + `data` + derived fields
  - Facts: `_type` + `data` + `metadata`

### `strategy` - Search Algorithm
- `fuzzy` - Text matching with typo tolerance (SQLite LIKE)
- `auto` - System chooses based on query (currently: fuzzy)
- `lexical` - **Shipped 2026-11-24 (api#95 phase 1):** FTS5 BM25-ranked
  full-text over fact `data` (external-content index `item_fts`,
  trigger-maintained; queries hardened to quoted prefix terms with
  implicit AND; RFC-011 §4.9(3) pre-match scope enforcement identical to
  `fuzzy`). Entity matches still use LIKE. Result rows carry `rank`
  (bm25; more negative = better). Superseded rows match, same as `fuzzy`.
  CJK runs are not tokenized (unicode61) — CJK/substring recall remains
  on the LIKE path (console: `keyword_fallback` mode).
- **Future:** `semantic` - Embedding-based vector search (shipped for the
  console via the lazy embed daemon; not yet a verb strategy)
- **Staged (api#95 phase 2a, branch `feat/search-verb-semantic-hybrid`,
  pending merge):** `semantic` — daemon cosine re-checked at the API
  boundary under current-time visibility; `hybrid` — Reciprocal Rank
  Fusion (k=60) of lexical + semantic; the response `strategy` field
  echoes whichever plane actually ran (honest degradation).

### `effort` - Search Thoroughness
- `quick` - Cached results, shallow index (future)
- `standard` - Full search (default, current)
- `deep` - Exhaustive search with higher limits (future)

### `limit` - Result Pagination
- Maximum results to return (default: 100)
- **Future:** Continuation tokens for deep pagination

## Implementation

### Entity Search (Core)

```python
# system/core/entity.py
def search(self, query: str, coverage: str = "content", limit: int = 100) -> List[sqlite3.Row]:
    """Search entities by fuzzy text matching."""
    if coverage == "names":
        where_clause = "type LIKE ?"
        params = (f"%{query}%",)
    elif coverage == "content":
        where_clause = "type LIKE ? OR data LIKE ?"
        params = (f"%{query}%", f"%{query}%")
    else:  # full
        where_clause = "type LIKE ? OR data LIKE ? OR derived_from LIKE ?"
        params = (f"%{query}%", f"%{query}%", f"%{query}%")

    sql = f"""
        SELECT * FROM entity
        WHERE {where_clause}
        AND active = 1
        LIMIT ?
    """
    return self._execute_query(sql, params + (limit,))
```

### Fact/Item Search (Soil)

```python
# system/soil/database.py
def search_items(self, query: str, coverage: str = "content", limit: int = 100) -> List[sqlite3.Row]:
    """Search items/facts by fuzzy text matching."""
    if coverage == "names":
        where_clause = "_type LIKE ?"
        params = (f"%{query}%",)
    elif coverage == "content":
        where_clause = "_type LIKE ? OR data LIKE ?"
        params = (f"%{query}%", f"%{query}%")
    else:  # full
        where_clause = "_type LIKE ? OR data LIKE ? OR metadata LIKE ?"
        params = (f"%{query}%", f"%{query}%", f"%{query}%")

    sql = f"""
        SELECT * FROM item
        WHERE {where_clause}
        LIMIT ?
    """
    return self._execute_query(sql, params + (limit,))
```

## Key Files

- [`api/handlers/core.py:handle_search`](../memogarden-api/api/handlers/core.py#L870) - Search handler
- [`system/core/entity.py:search`](../memogarden-system/system/core/entity.py) - Entity search
- [`system/soil/database.py:search_items`](../memogarden-system/system/soil/database.py) - Item search
- [`api/schemas/semantic.py:SearchRequest`](../memogarden-api/api/schemas/semantic.py) - Request schema
- [`tests/test_search.py`](../memogarden-api/tests/test_search.py) - 8 tests

## Test Coverage

1. **test_search_entities_by_type** - Search by entity type (names coverage)
2. **test_search_entities_by_data_content** - Search by data field (content coverage)
3. **test_search_facts_by_content** - Search facts/notes by content
4. **test_search_all_target_type** - Search both entities and facts
5. **test_search_with_limit** - Pagination with limit parameter
6. **test_search_empty_results** - Handle queries with no matches
7. **test_search_fuzzy_matching** - Partial string matching
8. **test_search_full_coverage** - Search all fields including metadata

## RFC-005 v7.1 Alignment

### ✅ Implemented
- Fuzzy text matching (SQLite LIKE with wildcards)
- Coverage levels: names, content, full
- Target types: entity, fact, all
- Result format with `kind` marker ("entity" or "fact")
- Limit parameter for basic pagination

### ⏳ Deferred (Documented TODOs)
- **Continuation tokens** - Base64-encoded offset/limit/timestamp for deep pagination
- **Semantic strategy** - Embedding-based vector search as a verb strategy (console semantic mode ships today via the lazy embed daemon; see `api/v1/operator.py`)
- **Quick effort mode** - Cached results for frequent queries
- **Deep effort mode** - Exhaustive search with higher limits
- **Threshold filtering** - Similarity score filtering (requires the semantic strategy)

## Performance Characteristics

### Current Implementation (Fuzzy)
- **Speed:** Fast for small-medium datasets (<10K entities)
- **Precision:** Medium (text matching only)
- **Recall:** High (finds all partial matches)

### Future (Semantic)
- **Speed:** Medium (embedding computation + vector search)
- **Precision:** High (semantic similarity)
- **Recall:** Medium-High (depends on embedding quality)

## Example Usage

### Search for Transactions
```bash
curl -X POST http://localhost:5000/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "search",
    "query": "coffee",
    "target_type": "entity",
    "coverage": "content",
    "limit": 10
  }'
```

### Search All Content
```bash
curl -X POST http://localhost:5000/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "search",
    "query": "invoice",
    "target_type": "all",
    "coverage": "full",
    "limit": 50
  }'
```

## Dependencies

- Session 1: Semantic API - Core bundle (foundation)
- Session 2: Semantic API - Soil bundle (fact search)

## References

- RFC-005 v7.1: API Design, Section 6 (Semantic Bundle)
- Implementation Plan: Session 9
- [`test_search.py`](../memogarden-api/tests/test_search.py) - Test examples
