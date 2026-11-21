# RFC 005: MemoGarden API Design

**Version:** 9.0  
**Status:** Draft  
**Author:** JS  
**Date:** 2026-11-21

> **v9 coherence alignment.** The three-interface schematic stands as
> **roadmap**. Interface 3 (Semantic API) is **shipped and canonical** —
> `POST /mg`, the verb grammar, envelope, audit facts, and grants are the
> live core. Interfaces 1 and 2 (internal handle class, `/apps` REST
> compatibility) and the `/capabilities` discovery surface are **roadmap,
> not yet built**: they serve the RFC-009 application model, which awaits
> the agent runtime that will host app-capable agents (the MARS experiment
> was set down as the wrong vehicle; its successor is undesigned).
> Framework names in this document are advisory illustrations, not
> prescriptions. A surface map of today's API families — most of which
> post-date v8 — closes this RFC.

## Summary

MemoGarden provides three distinct interfaces:
1. **Internal Python API** - Direct database access for server and internal modules
2. **REST API** - Simple CRUD operations for external integrations
3. **Semantic API** - Message-passing interface for MemoGarden Apps

The Internal Python API is the canonical implementation. The REST and Semantic APIs are HTTP interfaces served by `memogarden-api`, which uses the Internal API to access the system.

**Key clarification (v4.0):** The Semantic API is the primary interface for MemoGarden Apps. SDKs (Python, TypeScript, Dart, Java) target the Semantic API exclusively. The REST API exists as a compatibility layer for non-MemoGarden integrations (curl, Zapier, legacy scripts).

## Motivation

Three different use cases require three different interfaces:

**Internal modules** (HTTP server, agents, importers):
- Direct database access
- Full control over transactions and performance
- Filesystem-level operations
- Same-process, same-machine

**External integrations** (Zapier, scripts, legacy apps):
- Conventional CRUD semantics
- Namespace isolation
- Familiar REST patterns
- Standard HTTP client libraries

**MemoGarden Apps** (calendar, notes, project studio):
- Rich semantic operations
- Graph traversal and context-aware queries
- Supervised execution via MemoGarden
- SDK-based development
- See RFC-009 for full application model

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│   MemoGarden Apps                           │
│   (supervised, SDK-based)                   │
├─────────────────────────────────────────────┤
│   SDKs (Python, TS, Dart, Java)             │
│   → Semantic API only                       │
└───────────────────┬─────────────────────────┘
                    │
┌─────────────────┬─┴──────────────────────────┐
│   External      │                            │
│   Integrations  │                            │
│   (curl, Zapier)│                            │
│   → REST API    │                            │
└────────┬────────┘                            │
         │ HTTP                                │
         ▼                                     ▼
┌─────────────────────────────────────────────┐
│   memogarden-api (HTTP Server)              │
│                                             │
│   REST API:      /apps/{id}/...             │
│   Semantic API:  /mg                        │
└───────────────────┬─────────────────────────┘
                    │ Python imports
                    ▼
┌─────────────────────────────────────────────┐
│   Internal Python API                       │
│   (MemoGarden class)                        │
└───────────────────┬─────────────────────────┘
                    │ SQLite
                    ▼
┌─────────────────────────────────────────────┐
│   Soil + Core databases                     │
└─────────────────────────────────────────────┘
```

## Semantic Verbs

MemoGarden's semantic API uses carefully chosen verbs that accurately reflect the nature of data transformations. Each verb has specific semantic meaning:

### Verb Definitions

**Facts** - Immutable ground truth
- **add**: to bring in. Implies data already exists externally and we're bringing it into MemoGarden
- **amend**: to rectify, to set a record straight. Creates superseding fact that corrects/updates prior fact
- **get**: to obtain. Retrieve fact by identifier
- **query**: to ask, to seek by asking. Find facts matching criteria

**Entities** - Derived beliefs in Core
- **create**: to bring into being. Reifies a belief into existence in MemoGarden
- **edit**: to revise and publish. Makes changes to entity state
- **forget**: to lose the power of recall. Entity becomes inactive but traces remain in Soil
- **get**: to obtain. Retrieve entity by identifier
- **query**: to ask, to seek by asking. Find entities matching criteria
- **track**: to follow footsteps. Trace causal chain from current entity back to originating facts

**Relations** - Links between entities
- **link**: to bind, fasten, couple. Connect two entities with a user relation (directed edge)
- **unlink**: to separate, unfasten. Remove relation between entities
- **edit**: to revise and publish. Modify relation attributes (e.g., time horizon)
- **get**: to obtain. Retrieve relation by identifier
- **query**: to ask, to seek by asking. Find relations matching criteria
- **explore**: to investigate by traveling. Graph expansion from anchor point

**Schemas** - Type definitions
- **register**: to record officially. Add new schema definition to MemoGarden *(roadmap — no server handler yet)*

**Context** - Attention and scope management (bundled with Core)
- **enter**: to go into. Add scope to active set
- **leave**: to depart from. Remove scope from active set
- **focus**: to concentrate attention. Switch primary scope among active scopes *(retired v8 — deprecated no-op alias, kept for compatibility)*
- **rejoin**: to come back together. Subagent merges its view-stream into primary context *(roadmap — awaits the multi-agent runtime; no server handler yet)*

**Semantic** - Meaning-based operations
- **search**: to look for, to explore in order to find. Discover entities/facts by meaning and similarity

## Capability Bundles

The Semantic API is partitioned into capability bundles that group related operations:

**Core** - Entity management and context
- Verbs: create, edit, forget, get, query (entities)
- Schemas: register
- Context: enter, leave, focus, rejoin

**Soil** - Immutable fact storage
- Verbs: add, amend, get, query (facts)

**Relations** - Graph structure
- Verbs: link, unlink, edit, get, query, explore (relations)

**Semantic** - Meaning-based discovery
- Verbs: search

### Bundle Dependencies

```
              ┌───────────┐
              │ SEMANTIC  │
              └─────┬─────┘
                    │
             requires all
         ┌──────────┼──────────┐
         ▼          ▼          ▼
   ┌──────────┐ ┌──────┐ ┌───────────┐
   │   SOIL   │ │ CORE │ │ RELATIONS │
   └──────────┘ └──────┘ └───────────┘
        │          ▲           │
   independent     └───────────┘
                Relations requires Core
```

- **Core**: Standalone, no dependencies (includes context verbs)
- **Soil**: Standalone, no dependencies
- **Relations**: Requires Core (relations connect entities, includes explore)
- **Semantic**: Requires Core + Soil + Relations

## App Profiles

Rather than requiring developers to understand bundle combinatorics, apps declare a single profile. Each profile implies a set of bundles and a contract the app commits to.

| Profile | Bundles Included | Example Apps |
|---------|------------------|--------------|
| **Core** | Core | Contacts, inventory, simple lists |
| **Soil** | Soil | Email archive, sensor logs, event collectors |
| **Relational** | Core + Relations | Mind maps, org charts, network diagrams |
| **Factual** | Core + Soil | Budget tracker, journal, habit tracker |
| **Semantic** | Core + Soil + Relations + Semantic | Project studio, knowledge base, research tools |

### Profile Contract

Declaring a profile commits the app to:

1. **Supporting all verbs** in that profile's bundles
2. **Exposing capabilities as toolcalls** for agent interaction
3. **Surfacing relevant UI elements**:
   - Soil → history/audit trail views
   - Relations → graph visualization
   - Semantic → search/suggest interfaces

See RFC-009 for full application model specification.

## Verb Options Schema

### Response Envelope

All Semantic API responses use a consistent envelope:

```json
{
  "ok": true,
  "actor": "usr_xxx",
  "timestamp": "2026-02-06T12:34:56Z",
  "result": { /* verb-specific payload */ }
}
```

On error:
```json
{
  "ok": false,
  "actor": "usr_xxx",
  "timestamp": "2026-02-06T12:34:56Z",
  "error": { /* per RFC-006 */ }
}
```

### Null Semantics

MemoGarden uses `Unknown` conceptually for absent values:
- SQL: `NULL`
- JSON: `null`
- Python: `None`

Semantically, `null` in MemoGarden means "not yet known" rather than "intentionally empty." This aligns with personal information systems where most missing data represents information the operator hasn't captured yet.

### Simple Verbs (UUID-only)

| Verb | Request | Response |
|------|---------|----------|
| `get` | `{"op": "get", "target": "<uuid>"}` | Full object (type determined by UUID prefix) |
| `forget` | `{"op": "forget", "target": "<uuid>"}` | Full updated object with `forgotten_at` |
| `unlink` | `{"op": "unlink", "target": "<uuid>"}` | `{target, unlinked_at}` |

UUID prefix indicates target type and table (`fct_`, `ent_`, `rel_`, `scp_`).

### Write Verbs

#### add (Fact)

```json
{
  "op": "add",
  "type": "Email",
  "data": { /* schema-validated content */ },
  "metadata": { /* optional app-defined fields */ }
}
```

**Response:** Full created fact object.

#### create (Entity)

```json
{
  "op": "create",
  "type": "Contact",
  "data": { /* schema-validated content */ },
  "metadata": { /* optional app-defined fields */ }
}
```

**Response:** Full created entity object.

#### amend (Fact)

```json
{
  "op": "amend",
  "original": "fct_xxx",
  "content": { /* new fact body */ }
}
```

**Response:** Full new (superseding) fact object. System updates `superseded_by` on original.

#### link (Relation)

```json
{
  "op": "link",
  "from": "ent_xxx",
  "to": "ent_yyy",
  "relation_type": "authored_by",
  "time_horizon": "6M"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `from` | yes | Source entity UUID |
| `to` | yes | Target entity UUID |
| `relation_type` | yes | Relation type string |
| `time_horizon` | no | Duration string (e.g., "6M", "1Y") |

**Response:** Full created relation object.

#### edit (Entity/Relation)

```json
{
  "op": "edit",
  "target": "ent_xxx",
  "set": {"priority": "high", "tags": ["urgent"]},
  "unset": ["deprecated_field", "old_notes"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `target` | yes | Entity or relation UUID |
| `set` | no | Fields to add or update |
| `unset` | no | Field names to remove |

`set` handles both add-new and update-existing. `unset` removes fields entirely.

**Response:** Full updated object.

#### register (Schema)

```json
{
  "op": "register",
  "schema": {
    "name": "Contact",
    "parent": "Entity",
    "properties": { /* JSON Schema */ }
  }
}
```

**Response:** `{schema_name, registered_at}`

### Context Verbs

Scopes are access boundaries (RFC-011): `enter` requires a grant; activation is
exclusive (one active scope per principal); every transition and denial is
audited as an Action fact.

#### enter

```json
{
  "op": "enter",
  "scope": "scp_xxx"
}
```

**Response:** `{scope, previous_scope, active_scope}`

Grant-gated (RFC-011 §4.3/§4.4): without a grant the enter fails and is audited.
Entering while already inside a scope is an atomic switch — implicit leave of the
current scope, then enter; `previous_scope` is present only on a switch.

#### leave

```json
{
  "op": "leave",
  "scope": "scp_xxx"
}
```

**Response:** `{scope, active_scope}`

`active_scope` is `null` after a successful leave. `scope` must match the
currently active scope.

#### focus

**RETIRED (v8 / RFC-011).** With exclusive activation there is no
primary-vs-active distinction. The verb remains a deprecated no-op alias during
the migration window and responds `{scope, active_scope}`.

**Implied activation:** When a subagent is created (has only one scope) or when user first registered (has only one scope).

#### rejoin

```json
{
  "op": "rejoin"
}
```

No required arguments. Schema left open for future needs.

**Response:** `{merged_at, child_scope, parent_scope}`

### Query Verb

```json
{
  "op": "query",
  "target_type": "entity",
  "type": {"family": "Note"},
  "filters": {
    "status": "active",
    "tags": {"any": ["urgent", "flagged"]},
    "category": {"not": "archived"}
  },
  "linked": {
    "to": "ent_xxx",
    "via": "authored_by"
  },
  "start": "2026-01-01T00:00:00Z",
  "end": "2026-02-01T00:00:00Z",
  "sort": "reverse",
  "start_index": 0,
  "count": 20
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `target_type` | yes | `entity` / `fact` / `relation` |
| `type` | no | `{"exact": "..."}` or `{"family": "..."}` |
| `filters` | no | Field-value filters with operators |
| `linked` | no | Relational filter (one hop) |
| `start`, `end` | no | ISO timestamp bounds |
| `sort` | no | `chronological`, `reverse`, `by_horizon` |
| `start_index`, `count` | no | Pagination |

**Filter operators:**
- Bare value → equality
- `{"any": [...]}` → union (OR)
- `{"all": [...]}` → intersection (AND, for multi-value fields)
- `{"not": value}` → negation
- `{"not": {"any": [...]}}` → none of these

**Linked filter:**
- `{"to": "ent_xxx"}` → has outgoing edge to X
- `{"from": "ent_xxx"}` → has incoming edge from X
- `{"with": "ent_xxx"}` → either direction
- `{"via": "relation_type"}` → filter by relation type

**Response:** `{results: [...], total, start_index, count}`

### Track Verb

```json
{
  "op": "track",
  "target": "ent_xxx",
  "depth": 3
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `target` | yes | Entity UUID (starting point) |
| `depth` | no | Hop limit (default: unlimited) |

**Response:** Tree structure with `kind` markers:

```json
{
  "target": "ent_xxx",
  "chain": [
    {
      "kind": "entity",
      "id": "ent_xxx",
      "sources": [
        {
          "kind": "fact",
          "id": "fct_aaa",
          "sources": []
        },
        {
          "kind": "entity",
          "id": "ent_yyy",
          "sources": [
            {"kind": "fact", "id": "fct_bbb", "sources": []}
          ]
        }
      ]
    }
  ]
}
```

Tree format handles "diamond ancestry" naturally.

### Search Verb

```json
{
  "op": "search",
  "text": "copenhagen conference",
  "strategy": "auto",
  "coverage": "names",
  "effort": "standard",
  "types": ["Event", "Note"],
  "start": "2025-01-01T00:00:00Z",
  "end": "2026-01-01T00:00:00Z",
  "limit": 20,
  "threshold": 0.5,
  "continuation": "tok_abc"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `text` | yes | Query string |
| `strategy` | no | `semantic` / `fuzzy` / `auto` (default: `auto`) |
| `coverage` | no | `names` / `content` / `full` (default: `names`) |
| `effort` | no | `quick` / `standard` / `deep` (default: `quick`) |
| `types` | no | Restrict to schema types |
| `start`, `end` | no | Time bounds |
| `limit` | no | Max results |
| `threshold` | no | Minimum similarity score |
| `continuation` | no | Pagination token from previous search |
| `scope` | no | Restrict to a scope UUID — convenience filter ONLY; access enforcement is automatic and independent (RFC-011) |

**Coverage levels:**
- `names` — title/name fields only (fast)
- `content` — names + body text
- `full` — all indexed fields including metadata

**Strategy:**
- `semantic` — embedding similarity
- `fuzzy` — text matching, tolerates typos/partial
- `auto` — system chooses based on query characteristics

**Response:**

```json
{
  "results": [
    {"id": "ent_xxx", "score": 0.87, "reason": "title match: Copenhagen Tech Conference"}
  ],
  "continuation": "tok_def"
}
```

**Scope enforcement (RFC-011):** results are filtered to the principal's access
BEFORE matching — items outside the principal's active scope never appear as
matches, at any coverage level or strategy (a filename hit on a scoped item is
itself a leak). The `scope` request field is a convenience filter, never the
enforcement mechanism.

### Explore Verb

```json
{
  "op": "explore",
  "anchor": "ent_xxx",
  "radius": 2,
  "direction": "both",
  "types": ["Contact", "Note"],
  "start": "2025-01-01T00:00:00Z",
  "end": "2026-01-01T00:00:00Z",
  "limit": 50
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `anchor` | yes | Starting point UUID |
| `radius` | no | Hops from anchor (default: 1) |
| `direction` | no | `outgoing` / `incoming` / `both` (default: `both`) |
| `types` | no | Filter discovered nodes by type |
| `start`, `end` | no | Time bounds on discovered nodes |
| `limit` | no | Max results |

**Response:**

```json
{
  "anchor": "ent_xxx",
  "radius": 2,
  "results": [
    {"id": "ent_yyy", "distance": 1, "path": ["rel_aaa"]},
    {"id": "ent_zzz", "distance": 2, "path": ["rel_aaa", "rel_bbb"]}
  ]
}
```

`distance` is hop count; `path` shows relation UUIDs traversed.

## Audit Facts

**Design Constraint:** Multiple agents coordinate without locks. An agent must be able to see that another agent is performing an operation *before* that operation completes.

**Solution:** Two-fact pattern with immediate commit:
1. **Action fact** - Created when operation starts, committed immediately (visible to other agents during execution)
2. **ActionResult fact** - Created when operation completes, linked via `result_of` relation

**Query Pattern:**
```python
# In-progress operations (Action without ActionResult)
actions = mg.query('Action', {})
in_progress = [a for a in actions 
               if not mg.explore(a.uuid, relation_filter={'kind': 'result_of'})]

# Failed operations
failed = mg.query('ActionResult', {'filter': {'data.result': 'failure'}})
```

**Commit Strategy:**
- **Action:** `soil._conn.commit()` immediately after creation (3x write overhead, acceptable for personal system)
- **ActionResult:** Normal transaction commit when operation completes
- **Rationale:** Other agents must see "operation in progress" without waiting for completion

**Use Cases:**
- **Long-running ops:** Agent B queries Actions→sees Agent A's in-progress operation
- **Async workflows:** Action created synchronously, ActionResult arrives seconds/minutes later
- **Distributed coordination:** "claim_task" Action prevents duplicate work before task completes

---

### Action Fact Schema (Operation Initiation)

Created immediately when a Semantic API operation begins, *before* execution:

```python
{
  "type": "Action",
  "uuid": "soil_<uuid4>",              # Standard Soil fact UUID
  
  # Actor identification
  "actor": "core_<uuid4>",              # References Operator or Agent entity
  
  # Operation details
  "operation": "search" | "get" | "edit" | "add" | "remove" | 
               "add_relation" | "remove_relation" | "edit_relation" |
               "enter" | "leave" | "focus" | "rejoin" |
               "explore" | "track" | "discover" | "register",
  
  "params": {                           # Request parameters (operation-specific)
    # Structure varies by operation
    # Examples:
    # search: {"query": "...", "filters": {...}, "strategy": "..."}
    # edit: {"uuid": "...", "edits": {"set": {...}, "unset": [...]}}
    # add_relation: {"kind": "...", "subject": "...", "object": "..."}
  },
  
  # Context tracking
  "context": ["ctx_abc123", "ctx_def456"],  # Active scope UUIDs at call time
  "primary_context": "ctx_abc123",          # Primary scope (optional)
  
  # Temporal metadata
  "timestamp": "2026-02-06T14:23:45.123Z",  # ISO 8601 with milliseconds
  
  # Correlation (optional)
  "request_id": "req_xyz789",               # For multi-request workflows
  "parent_action": "soil_<uuid4>"           # Causal chain link (optional)
}
```

**Timing:** Created/committed immediately when operation starts, *before* handler executes.

---

### ActionResult Fact Schema (Operation Completion)

Created when the operation completes (success, failure, timeout, or cancellation):

```python
{
  "type": "ActionResult",
  "uuid": "soil_<uuid4>",
  
  # Execution result
  "result": "success" | "failure" | "timeout" | "cancelled",
  
  # Error details (present only if result="failure")
  "error": {
    "code": "validation_error" | "not_found" | "lock_conflict" | 
            "permission_denied" | "internal_error",
    "message": "Human-readable error description",
    "details": {...}                    # Optional structured error data
  },
  
  # Result summary (present only if result="success")  
  "result_summary": {
    # Structure varies by operation:
    # search/explore: {"result_count": 42}
    # add: {"created_uuid": "soil_..."}
    # edit/remove: {"affected_uuids": ["core_...", "soil_..."]}
    # add_relation: {"relation_uuid": "core_..."}
  },
  
  # Temporal metadata
  "timestamp": "2026-02-06T14:23:45.456Z",  # Completion time
  "duration_ms": 333                         # Execution duration
}
```

**Timing:** Created when operation completes. May be delayed for async operations.

**Relation:** ActionResult → Action via `result_of` system relation.

---

### Linking Action and Result

A system relation connects Action and ActionResult facts:

```python
{
  "kind": "result_of",                 # System relation type
  "source": "soil_<uuid4>",            # ActionResult fact UUID
  "source_type": "fact",
  "target": "soil_<uuid4>",            # Action fact UUID
  "target_type": "fact",
  "created_at": <days_since_epoch>
}
```

This relation enables queries like:

```python
# Find result for specific action
result = mg.explore(action_uuid, relation_filter={"kind": "result_of"})

# Find hung operations (actions without results)
actions = mg.query('Action', {'filter': {'timestamp_before': threshold}})
for action in actions:
    results = mg.explore(action.uuid, relation_filter={"kind": "result_of"})
    if not results:
        log.warning(f"Hung operation: {action.uuid}")
```

---

### Audit Query Examples

**Find all failed operations:**
```python
results = mg.query('ActionResult', {'filter': {'data.result': 'failure'}})
for result in results:
    action = mg.explore(result.uuid, relation_filter={"kind": "result_of"}, direction="outbound")
    print(f"Failed {action.operation}: {result.error.message}")
```

**Trace agent behavior:**
```python
actions = mg.query('Action', {'filter': {'data.actor': agent_uuid}})
# Returns chronological list of all operations performed by agent
```

**Analyze operation performance:**
```python
results = mg.query('ActionResult', {
    'filter': {'timestamp_after': '2026-02-01T00:00:00Z'}
})
latencies = [r.duration_ms for r in results]
p95 = percentile(latencies, 95)
```

---

### Implementation Note

The `with_audit()` decorator uses `soil._conn.commit()` to immediately persist Action facts. This is temporary until Soil provides `soil.create_and_commit_item()`. See RFC-007 Section 6.5: Connection Lifecycle Refactor for long-term solution.

**Current implementation:**
```python
def with_audit(operation: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Create Action fact
            action = soil.add_item('Action', {
                'actor': current_actor(),
                'operation': operation,
                'params': serialize_params(args, kwargs)
            })
            
            # IMMEDIATE COMMIT - other agents can see this now
            soil._conn.commit()
            
            # Execute operation
            start = time.time()
            try:
                result = func(*args, **kwargs)
                status = 'success'
                result_data = {'result': result}
            except Exception as e:
                status = 'error'
                result_data = {'error': str(e)}
                raise
            finally:
                # Create ActionResult (commits with transaction)
                action_result = soil.add_item('ActionResult', {
                    'status': status,
                    'duration_ms': int((time.time() - start) * 1000),
                    **result_data
                })
                soil.add_system_relation('result_of', action_result.uuid, action.uuid)
            
            return result
        return wrapper
    return decorator
```

**Why this matters:** Without immediate commit, Action facts would only become visible when the entire operation completes—defeating the purpose of progress visibility.

---

### Fossilization Policy

**Automatic creation:** Action and ActionResult facts are created automatically by Semantic API handlers using the internal API with `bypass_semantic_api=True` to prevent recursion.

**Fossilization:** Action/ActionResult facts subject to normal fossilization based on time horizons:
- High-frequency operations (searches): aggressive fossilization (+7d)
**System relation:** The "result_of" relation type is registered as a system relation kind (immutable, not subject to time horizons).

## Capability Discovery

*(Roadmap — not yet built; serves the RFC-009 app model.)*

Apps and clients can query available capabilities:

```python
# Internal API
capabilities = mg.get_capabilities()
# Returns: {'bundles': ['core', 'soil', 'relations', 'semantic'], 'version': '0.11.0'}

# Semantic API (HTTP)
POST /mg
{"op": "get_capabilities"}
# Returns: {"bundles": ["core", "soil", "relations", "semantic"], "version": "0.11.0"}

# REST API
GET /capabilities
# Returns: {"bundles": ["core", "soil", "relations", "semantic"], "version": "0.11.0"}
```

## Part 1: Internal Python API

*(Roadmap as a handle-class API. The shipped internal surface is the
module-level `system.soil` / `system.core` layers; the handle class is
the FFI-facing shape and awaits that era.)*

### Design Principles

1. **Handle-based** - Connection-like pattern (similar to sqlite3, psycopg3)
2. **Flat namespace** - All operations are top-level methods (Soil/Core are hidden)
3. **FFI-friendly** - Designed to map to future C API for Rust/FilC rewrite
4. **Thread-per-handle** - Not thread-safe, one handle per thread
5. **Transaction-stateful** - Transaction state lives in handle, not passed as parameters

### Initialization

```python
from memogarden import MemoGarden

# Auto-detect profile (most common)
mg = MemoGarden()

# Explicit profile
mg = MemoGarden(profile='device')
mg = MemoGarden(profile='personal')

# With options
mg = MemoGarden(profile='device', options={
    'data_dir': '/custom/path',
    'encryption': True,
    'cache_size_mb': 500
})

# Testing
mg = MemoGarden(profile='memory')  # In-memory, ephemeral

# Context manager
with MemoGarden() as mg:
    results = mg.search(...)
```

### Thread Safety

**One handle per thread.** Handles are NOT thread-safe:

```python
# ✓ Good: One handle per thread
def worker():
    mg = MemoGarden()
    mg.add_fact('Note', {...})
    mg.close()

# ✗ Bad: Shared handle across threads
mg = MemoGarden()
def worker():
    mg.add_fact('Note', {...})  # Race conditions!
```

Multiple handles can safely access the same database (SQLite handles locking).

## Part 2: HTTP API Server

The `memogarden-api` package provides an HTTP server with two interfaces:

### Server Architecture

One server process hosts all route groups. Framework choice is
implementation detail, not invariant (as built: Flask + gunicorn; the
FastAPI sketch was the original illustration).

```python
# memogarden-api/api/main.py — schematic
app = create_server()                    # any framework

# Singleton internal handle
_mg = _internal_handle()                 # Interface 1 (roadmap)

# REST API routes (Interface 2, roadmap)
@app.post("/apps/{app_id}/facts")
def create_fact(app_id, request): ...

# Semantic API dispatcher (Interface 3 — shipped)
@app.post("/mg")
def semantic_api(message):
    return HANDLERS[message["op"]](_mg, message)

# Capability discovery (roadmap)
@app.get("/capabilities")
def get_capabilities(): return _mg.get_capabilities()
```

### REST API

**Purpose:** Compatibility layer for external integrations (curl, Zapier,
legacy scripts) — *(roadmap, not yet built; serves the RFC-009 app model)*

**Pattern:** Resource-oriented paths with HTTP verbs

**Namespace isolation:** Apps scoped to `/apps/{app_id}/...`

```
# Facts
POST   /apps/{app_id}/facts
GET    /apps/{app_id}/facts/{uuid}
GET    /apps/{app_id}/facts?type=Email&since=2026-01-01
PUT    /apps/{app_id}/facts/{uuid}         # Creates superseding fact
DELETE /apps/{app_id}/facts/{uuid}

# Entities  
POST   /apps/{app_id}/entities
GET    /apps/{app_id}/entities/{uuid}
GET    /apps/{app_id}/entities?type=Contact
PATCH  /apps/{app_id}/entities/{uuid}
DELETE /apps/{app_id}/entities/{uuid}

# Relations
POST   /apps/{app_id}/relations
GET    /apps/{app_id}/relations/{uuid}
GET    /apps/{app_id}/relations?from={uuid}
DELETE /apps/{app_id}/relations/{uuid}

# Capabilities
GET    /capabilities
```

### Grants Management (RFC-011)

**Purpose:** Operator-issued authorization for principals to enter scopes.

**Auth:** JWT only — API keys cannot mint, modify, or revoke grants, including
their own (RFC-010 issuance invariant).

```
POST   /grants                      # {key_id | agent_id, scope_uuid, expires_at?}
DELETE /grants/{grant_id}           # revoke immediately; next gated operation fails
GET    /grants?key_id=|agent_id=    # enumerate
```

For `agent_id`, the grant applies to all keys currently bound to that agent and
is recorded as the agent's grant envelope (`agent.data.allowed_scopes`); future
bindings inherit the envelope at bind time. `/auth/me` (RFC-010 §7.3) reports
the key's grants, the bound agent's envelope, and the current active scope.

### Semantic API

**Purpose:** Primary interface for MemoGarden Apps via SDKs

**Pattern:** Message-passing via single `/mg` endpoint

**Operations:** Correspond to Internal API methods

```python
# Message format
{
    "op": "search",
    "text": "project notes",
    "effort": "standard",
    "limit": 20
}

# Response format (envelope)
{
    "ok": true,
    "actor": "usr_xxx",
    "timestamp": "2026-02-06T12:34:56Z",
    "result": {
        "results": [...],
        "continuation": "tok_abc"
    }
}
```

**Supported operations:**

| Bundle | Operations |
|--------|------------|
| Core | `create`, `edit`, `forget`, `get`, `query` (entities); `register` (schemas); `enter`, `leave`, `focus`, `rejoin` (context) |
| Soil | `add`, `amend`, `get`, `query` (facts) |
| Relations | `link`, `unlink`, `edit`, `get`, `query`, `explore` (relations) |
| Semantic | `search` |

## Part 3: External Client Libraries

### Python Client

```python
from memogarden.client import MemoGardenClient

class MemoGardenClient:
    """HTTP client for Semantic API."""
    
    def __init__(self, uri: str = 'http://localhost:8080', token: str = None):
        self.uri = uri
        self.session = requests.Session()
        if token:
            self.session.headers['Authorization'] = f'Bearer {token}'
    
    def search(self, text: str, /, *,
               strategy: str = 'auto',
               coverage: str = 'names',
               effort: str = 'quick',
               limit: int = 20) -> SearchResult:
        """Execute semantic search."""
        response = self.session.post(
            f'{self.uri}/mg',
            json={
                'op': 'search',
                'text': text,
                'strategy': strategy,
                'coverage': coverage,
                'effort': effort,
                'limit': limit
            }
        )
        response.raise_for_status()
        return SearchResult(**response.json()['result'])
    
    def get_capabilities(self) -> dict:
        """Query available bundles."""
        response = self.session.post(
            f'{self.uri}/mg',
            json={'op': 'get_capabilities'}
        )
        response.raise_for_status()
        return response.json()['result']
    
    # ... additional methods mirror Internal API
```

### TypeScript/Dart/Java Clients

Similar pattern - target Semantic API exclusively, same method signatures as Python client adapted to language idioms.

## Cross-Language Portability

### Design for FFI

The Internal Python API is designed to eventually map to C functions:

```c
// Hypothetical future C API
typedef struct mg_handle mg_handle;

mg_handle* mg_open(const char* profile, const char* options_json);
void mg_close(mg_handle* mg);

char* mg_add_fact(mg_handle* mg, const char* type, 
                  const char* content_json, const char* metadata_json);
char* mg_search(mg_handle* mg, const char* params_json);
char* mg_get_capabilities(mg_handle* mg);
```

### Portability Rules

1. **Simple types at boundaries** - JSON-serializable only
2. **Consistent method names** - Same vocabulary across languages
3. **Null/None for missing** - Universal absence representation (semantically "Unknown")
4. **Flat namespace** - No nested resource objects
5. **Synchronous core** - Async wrappers per language

## Open Questions

1. ~~**Query parameters**~~ - **RESOLVED v6.0**: Filter syntax, sort order, pagination, time-based queries specified
2. ~~**Search parameters**~~ - **RESOLVED v6.0**: Effort modes, coverage levels, continuation tokens specified
3. ~~**Track return format**~~ - **RESOLVED v6.0**: Tree structure with kind markers
4. ~~**Scope mechanics**~~ - **RESOLVED v6.0**: enter/leave/focus separation, rejoin semantics
5. **Schema system** - Full design for registration, inheritance, validation enforcement
6. **Configuration management** - Runtime config, environment variables, precedence rules
7. **Multi-handle coordination** - Concurrent access patterns, lock contention handling
8. ~~**Authentication/authorization**~~ - **RESOLVED v8.0**: authentication = JWT + X-API-Key (implemented; RFC-010); authorization = RFC-011 grants with attention-gated scope access
9. **Rate limiting** - Per-app, per-operation policies
10. **Async wrappers** - For internal API (sync core + async wrappers)

## Deferred Items

1. **Temporal entity reconstruction** - Query entity state at point-in-time or over range; requires walking delta chain, streaming results. Extend `query` verb when needed.
2. **Recurring time patterns** - Query by Q1/Q2/Q3/Q4, specific months, days of week. Adds complexity to timestamp handling.
3. **Undirected relations** - Current design assumes directed edges. Undirected semantics need careful schema and implementation design.

## Future Extensions

1. **WebSocket subscriptions** - Real-time entity change notifications
2. **Query builder** - Fluent interface for complex search construction
3. **Streaming results** - Yield results incrementally for large queries
4. **Backup/restore API** - Handle-level database operations
5. **Statistics API** - Query execution stats, storage usage, index health
6. **Migration tools** - Schema evolution and data transformation

## References

- RFC-001 v5: Security & Operations Architecture
- RFC-002 v5: Relation Time Horizon & Fossilization
- RFC-003 v5: Context Capture Mechanism
- RFC-004 v2.3: Package Structure & Deployment
- RFC-006 v1: Error Handling & Diagnostics
- RFC-008 v1.3: Transaction Semantics
- RFC-009 v1: MemoGarden Application Model
- RFC-010 v1.2: Agent Identity & Attribution
- RFC-011 v1.3: Scopes, Grants, and Attention-Gated Access
- RFC-012 v1.3: Provider Accounts
- RFC-013: Browser Session Host + Ingestion Decision Register
- RFC-014 v1: Google Calendar Provider
- RFC-015 v1: Operator Console
- PRD v0.11.0: MemoGarden Personal Information System
- SQLite C API: https://www.sqlite.org/c3ref/intro.html
- Python DB-API 2.0: https://peps.python.org/pep-0249/
- JSON-RPC 2.0: https://www.jsonrpc.org/

---

## Addendum (2026-11-21): API surface map

The families below exist in the running server (memogarden-api) and
post-date v8; each is designed in its owning RFC. This map is an index,
not a specification:

| Surface | Route families | Design of record |
|---|---|---|
| Semantic API | `POST /mg`, `GET /mg/events` (SSE observe) | this RFC; SSE observe in RFC-011 §4.9 / RFC-015 |
| Auth & identity | `/auth/*`, `/admin/register`, `/api-keys/*`, `/agents` | RFC-010; RFC-001 §4 |
| Grants & scopes | `/grants`, scope fields on verbs | RFC-011 (+ visibility addendum) |
| Providers | `/api/v1/providers/*` (registry, runs, scope-policy, accounts) | RFC-004 §12.2; RFC-012 (accounts) |
| Transactions | `/api/v1/transactions`, `/api/v1/recurrences` | RFC-008 domain; PRD v0.11 |
| Event review | `/api/v1/events/*` (pending-review, mark-occurred) | RFC-014 §6 |
| Operator console | `/console/*` pages, `/api/v1/operator`, `/api/v1/runs` | RFC-015 |
| E3 capabilities | `api_keys.capabilities`, middleware enforcement | RFC-013 register (E3) |

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-02 | Initial dual-interface design (REST + message-passing) |
| 2.0 | 2026-02-02 | Complete Python API specification with cross-language portability |
| 3.0 | 2026-02-02 | Internal vs External API distinction, /mg endpoint, single server architecture |
| 3.1 | 2026-02-02 | Transaction API clarification: cross-DB atomicity note, reference to RFC-008 |
| 4.0 | 2026-02-04 | Capability bundles, app profiles, capability discovery, Semantic API as primary for apps, Item→Fact terminology, reference to RFC-009 |
| 5.0 | 2026-02-04 | Semantic verb definitions: 12 core verbs with explicit meanings. Added Context bundle. Removed validate from semantic API. Deferred parameter design. |
| 6.0 | 2026-02-06 | **Complete verb options schemas**: All 17 verbs with request/response formats. Added `focus` (context), `explore` (relations). Response envelope with `actor`. Edit uses `set`/`unset` semantics. Query filter DSL with operators. Search split into `search` (content-based) and `explore` (graph-based). Track returns tree format. Null semantics clarified as "Unknown". Context verbs moved to Core bundle. Resolved open questions 1-4. |
| 7.0 | 2026-02-07 | **Audit facts specification**: Added Action and ActionResult fact schemas for complete API operation audit trail. System relation "result_of" links Action→ActionResult. Enables crash recovery, hung operation detection, agent behavior tracing, and performance analysis. |
| 8.0 | 2026-11-09 | **RFC-011 alignment (authorization):** Context verbs rewritten — exclusive activation, grant-gated `enter` (atomic switch; `previous_scope` in response), `leave` returns `active_scope: null`, `focus` retired as deprecated alias. Search: scope enforcement pre-match + optional convenience `scope` field. Added Grants Management endpoints (`POST/DELETE/GET /grants`, operator JWT-gated) and `/auth/me` extension (grants, agent envelope, active scope). Open question 8 resolved. |
| 9.0 | 2026-11-21 | Coherence alignment: three-interface schematic reaffirmed as roadmap — Semantic API (interface 3) shipped and canonical; internal handle class, `/apps` REST compatibility, `/capabilities`, and `register`/`rejoin` verbs recorded as roadmap pending the agent runtime (MARS set down as the wrong vehicle; successor undesigned). Framework references made advisory (Flask + gunicorn as built). API surface-map addendum added; reference versions updated. |

---

**Status:** Draft (v9 alignment 2026-11-21)  
**Where things stand:**
1. ~~Review capability bundle definitions~~ Done
2. ~~Finalize verb options schemas~~ Done
3. ~~Implement HTTP server with Semantic API~~ Done — shipped and live
4. ~~External client~~ Shipped as `mg_client` (memogarden-client)
5. Capability discovery endpoint — roadmap (RFC-009 app model)
6. Internal handle-class API — roadmap (FFI era)
7. `/apps` REST compatibility surface — roadmap (RFC-009 app model)
8. `register` / `rejoin` verbs — roadmap (schema registry / multi-agent runtime)

---

**END OF RFC**