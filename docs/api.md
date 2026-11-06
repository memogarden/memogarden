# MemoGarden API Documentation

**Last Updated:** 2026-11-06
**Version:** 0.1.0

Complete reference for MemoGarden APIs - Semantic API, REST API, and Authentication.

---

## Table of Contents

1. [API Overview](#api-overview)
2. [Authentication](#authentication)
3. [Semantic API (`/mg`)](#semantic-api)
4. [REST API (`/api/v1/`)](#rest-api)
5. [Error Handling](#error-handling)
6. [Common Data Types](#common-data-types)

---

## API Overview

MemoGarden provides three API interfaces:

### Semantic API (`/mg`) - Primary Interface

The **Semantic API** is a unified message-passing interface following RFC-005 v7. All operations use a single `/mg` endpoint with operation-based dispatch.

**Benefits:**
- Single endpoint for all operations
- Consistent request/response envelope
- Operation-based validation via Pydantic schemas
- Designed for AI agents and advanced integrations

**Use for:** AI agents, complex workflows, dynamic operations

### REST API (`/api/v1/`) - Traditional CRUD

The **REST API** provides predictable, resource-based endpoints for fixed-schema entities.

**Benefits:**
- Traditional RESTful patterns
- Predictable URL structure
- Standard HTTP methods (GET, POST, PUT, DELETE)
- Easy integration with REST clients

**Use for:** Web apps, mobile apps, simple integrations

### Providers API (`/api/v1/providers`) — Ingestion Registry

Registered providers are executed in-process by the server when due
(heartbeat via the hourly `memogarden-ingest.timer`). Authentication for
these endpoints: `X-API-Key: mg_sk_agent_...` header (API keys are **not**
accepted as Bearer tokens — Bearer is JWT-only).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/providers` | GET | List registered providers |
| `/api/v1/providers` | POST | Register a provider (`{name, module, class, interval, config, ...}`) |
| `/api/v1/providers/{name}` | GET | Provider manifest + last run status/detail |
| `/api/v1/providers/{name}/run` | POST | Trigger a run (async 202, single-flight lock) |
| `/api/v1/providers/{name}/run` | DELETE | Cancel a running run |
| `/api/v1/providers/run` | POST | Run all due providers (heartbeat endpoint) |

Registration requires the provider module to be importable in the server
environment and its class to implement the Provider Protocol
(`cls(config)` + `sync(since)` yielding Facts; optional
`create_relations(fact)`). The gmail provider is published at
[memogarden/memogarden-gmail-provider](https://github.com/memogarden/memogarden-gmail-provider).

**CLI:** the `memogarden` CLI ([memogarden-client](../memogarden-client/README.md))
wraps registration — `memogarden register provider` posts to this endpoint:

```bash
export MEMOGARDEN_URL=http://localhost:8600
export MEMOGARDEN_API_KEY=mg_sk_agent_...

# Field flags (name, module, class are required)
memogarden register provider \
    --name gmail \
    --module gmail_importer.provider \
    --class GmailProvider \
    --interval 3600 \
    --config batch_limit=1000

# Or a JSON payload file / stdin ('-' reads stdin)
memogarden register provider --file payload.json
```

Optional flags: `--interval` (seconds, >= 60, default 3600),
`--description`, `--disabled`, `--config KEY=VALUE` (repeatable; VALUE is
parsed as JSON when possible). `--file` takes a JSON object with the same
fields and cannot be combined with field flags. Exit codes: 0 success,
1 error, 2 usage error. Run listing/runs via the endpoints above (no CLI
verbs yet — the grammar is `memogarden <verb> <noun>`, RFC-005 verbs).

---

## Authentication

All API endpoints require authentication. MemoGarden supports two methods:

### JWT Token Authentication (Recommended)

**How it works:**
1. Login with username/password to receive JWT token
2. Include token in `Authorization` header
3. Token is valid for 30 days (configurable)

**Login:**
```bash
curl -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "changeme"}'
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "usr_550e8400-e29b-41d4-a716-446655440000",
    "username": "admin",
    "is_admin": true,
    "created_at": "2026-02-14T10:00:00Z"
  }
}
```

**Using the token:**
```bash
export TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# Semantic API
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"op": "query", "filters": {"_type": "Transaction"}}'

# REST API
curl http://localhost:8080/api/v1/transactions \
  -H "Authorization: Bearer $TOKEN"
```

### API Key Authentication

**How it works:**
1. Create API key via API or UI
2. Include key in `X-API-Key` header
3. Keys never expire (unless revoked)

**Agent auth model:** API keys are the intended credentials for agents and
automated clients — `agent` is the only key type (every key looks like
`mg_sk_agent_<64 hex>`). The flow: the human operator logs in (JWT) and
mints **one key per agent** via `POST /api-keys/`, naming it after the agent
(e.g. `{"name": "claude-code"}`); the name shows up in key listings
(`GET /api-keys/`) so operators can tell agents apart. Agents never see a
login flow — they hold a key and send it as `X-API-Key`. Key-management
endpoints accept JWT only, so a key holder cannot list, create, or revoke
keys. Keys carry the full authority of the owning user; there are no
per-key scopes.

**Agent identity (RFC-010):** keys can be *bound* to an `Agent` entity —
minted by the operator via `POST /agents`. Bound keys attribute their
operations to the agent: Action facts and the `/mg` response envelope
carry `actor = core_<agent uuid>` + `actor_type = "agent"`; unbound keys
keep `actor = username` with `actor_type = "api_key"` (a queryable
attribution-gap marker); JWT sessions use `actor_type = "operator"`.
Identity issuance is explicit and operator-gated — no code path in
authentication creates identity state. Bind at key creation
(`agent_id` field) or later via `PATCH /api-keys/<id>`; rotation and
multi-machine = new keys bound to the same agent. Agents introspect
their canonical identity via `GET /auth/me` with their key. See
[RFC-010](../plan/rfc_010_agent_identity_v1.md) for the full spec.

| Auth state | `actor` | `actor_type` |
|------------|---------|--------------|
| Key bound to agent | `core_<agent uuid>` | `agent` |
| Key unbound | owning username | `api_key` |
| JWT session | username | `operator` |

**Create API key:**
```bash
curl -X POST http://localhost:8080/api-keys/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app"}'
```

**Response (full key shown only once):**
```json
{
  "id": "key_xxx",
  "name": "my-app",
  "key": "mg_sk_agent_9a2b8c7d...",  // Save this!
  "prefix": "mg_sk_agent_",
  "created_at": "2026-02-14T10:00:00Z"
}
```

**Using the key:**
```bash
curl http://localhost:8080/api/v1/transactions \
  -H "X-API-Key: mg_sk_agent_9a2b8c7d..."
```

### Authentication Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/login` | POST | Login and get JWT token |
| `/auth/logout` | POST | Logout (no-op for stateless JWT) |
| `/auth/me` | GET | Identity introspection — user info (JWT) or agent identity (key); includes `actor_type`/`actor` |
| `/admin/register` | POST | One-time admin registration (localhost only, first run) |
| `/api-keys/` | GET | List API keys (includes `agent_id`) |
| `/api-keys/` | POST | Create new API key (optional `agent_id` to bind, RFC-010) |
| `/api-keys/<id>` | PATCH | Bind / re-bind / unbind an Agent entity (RFC-010) |
| `/api-keys/<id>` | DELETE | Revoke API key |
| `/agents` | POST | Mint an Agent entity (JWT only, RFC-010) |
| `/agents` | GET | List agents with bound key prefixes (JWT only, RFC-010) |

### Agent Identity (RFC-010)

```bash
# Mint an agent (JWT)
curl -X POST http://localhost:8080/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label": "claude-code", "description": "ZCode sessions on the laptop"}'
# → 201 {"agent_id": "core_...", "label": "claude-code", ...}

# Bind at key creation (rotation / multi-machine: new key, same agent_id)
curl -X POST http://localhost:8080/api-keys/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "claude-code", "agent_id": "core_..."}'

# Bind an existing key (the migration path; null unbinds)
curl -X PATCH http://localhost:8080/api-keys/<key-id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "core_..."}'

# An agent discovers its canonical identity
curl http://localhost:8080/auth/me -H "X-API-Key: mg_sk_agent_..."
# → {"actor_type": "agent", "actor": "core_...", "label": "claude-code", ...}
```

Per-agent traceability via the Semantic API `query` verb (fact-data
equality filters):

```bash
# One agent's operations
curl -X POST http://localhost:8080/mg -H "X-API-Key: mg_sk_agent_..." \
  -d '{"op": "query", "target_type": "fact", "type": "Action",
       "filters": {"actor": "core_..."}, "count": 50}'

# Attribution gaps (key authority without agent identity)
curl -X POST http://localhost:8080/mg -H "X-API-Key: mg_sk_agent_..." \
  -d '{"op": "query", "target_type": "fact", "type": "Action",
       "filters": {"actor_type": "api_key"}, "count": 50}'
```

---

## Semantic API

### Endpoint

```
POST /mg
```

### Request Format

All Semantic API requests follow this structure:

```json
{
  "op": "operation_name",
  "param1": "value1",
  "param2": "value2"
}
```

### Response Format

All responses use a standard envelope:

```json
{
  "ok": true,
  "actor": "usr_550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-02-14T10:30:00Z",
  "result": { ... }
}
```

On error:
```json
{
  "ok": false,
  "actor": "usr_xxx",
  "timestamp": "2026-02-14T10:30:00Z",
  "error": {
    "type": "ValidationError",
    "message": "Missing required field: target"
  }
}
```

---

### Core Bundle Operations

#### create

Create a new entity.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "create",
    "type": "Transaction",
    "data": {
      "amount": 100.00,
      "currency": "SGD",
      "description": "Grocery shopping at NTUC"
    }
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Entity type (Transaction, Recurrence, Contact, Account) |
| `data` | object | Yes | Entity-specific data fields |

**Response:**
```json
{
  "ok": true,
  "actor": "usr_xxx",
  "timestamp": "2026-02-14T10:30:00Z",
  "result": {
    "uuid": "core_550e8400-e29b-41d4-a716-446655440000",
    "_type": "Transaction",
    "version": 1,
    "data": { "amount": 100.00, "currency": "SGD", "description": "..." },
    "created_at": "2026-02-14T10:30:00Z",
    "updated_at": "2026-02-14T10:30:00Z"
  }
}
```

#### get

Retrieve an entity by UUID.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "get",
    "target": "core_550e8400-e29b-41d4-a716-446655440000"
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | string | Yes | Entity UUID (core_*, soil_*, rel_*, usr_*) |

#### edit

Modify an entity using set/unset semantics.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "edit",
    "target": "core_xxx",
    "set": { "description": "Updated description" },
    "unset": ["tags"]
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | string | Yes | Entity UUID |
| `set` | object | No | Fields to set/update |
| `unset` | array | No | Field names to remove |

#### forget

Soft delete an entity.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "forget",
    "target": "core_xxx"
  }'
```

#### query

Query entities with filters.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "query",
    "filters": {
      "_type": "Transaction"
    },
    "limit": 10
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `filters` | object | No | Type filter: `{"_type": "Transaction"}` |
| `start` | integer | No | Pagination start index (default: 0) |
| `limit` | integer | No | Max results (default: 20) |

**Response:**
```json
{
  "ok": true,
  "actor": "usr_xxx",
  "timestamp": "2026-02-14T10:30:00Z",
  "result": {
    "results": [ ... ],
    "total": 42,
    "start_index": 0,
    "count": 10
  }
}
```

---

### Soil Bundle Operations

#### add

Add a new fact (Item) to Soil.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "add",
    "params": {
      "_type": "Message",
      "data": {
        "description": "Meeting notes from today",
        "fragments": ["Discussed budget", "Reviewed transactions"]
      }
    }
  }'
```

#### amend

Amend a fact (create superseding fact).

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "amend",
    "target": "soil_xxx",
    "params": {
      "data": {
        "description": "Updated meeting notes",
        "fragments": ["Discussed budget", "Reviewed transactions", "Action items"]
      }
    }
  }'
```

---

### Relations Bundle Operations

#### link

Create a relation between entities.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "link",
    "source": "core_xxx",
    "target": "soil_yyy",
    "kind": "cites",
    "metadata": {"purpose": "Supporting documentation"}
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | Source entity UUID |
| `target` | string | Yes | Target entity UUID |
| `kind` | string | Yes | Relation kind (cites, derives_from, etc.) |
| `metadata` | object | No | Optional metadata |

#### unlink

Remove a relation.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "unlink",
    "source": "core_xxx",
    "target": "soil_yyy",
    "kind": "cites"
  }'
```

#### explore

Traverse relation graph from an anchor.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "explore",
    "anchor": "core_xxx",
    "direction": "outgoing",
    "max_depth": 2,
    "max_results": 50
  }'
```

**Response:**
```json
{
  "ok": true,
  "actor": "usr_xxx",
  "timestamp": "2026-02-14T10:30:00Z",
  "result": {
    "nodes": [
      {"uuid": "core_a", "kind": "entity", "distance": 0},
      {"uuid": "soil_b", "kind": "fact", "distance": 1}
    ],
    "edges": [
      {"uuid": "rel_xxx", "source": "core_a", "target": "soil_b", "kind": "cites"}
    ]
  }
}
```

---

### Context Bundle Operations (RFC-003)

#### enter

Enter a scope (add to active set).

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "enter",
    "scope": "core_xxx"
  }'
```

#### leave

Leave a scope (remove from active set).

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "leave",
    "scope": "core_xxx"
  }'
```

#### focus

Focus a scope (set as primary).

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "focus",
    "scope": "core_xxx"
  }'
```

---

### Advanced Operations

#### track

Trace causal chain from entity back to originating facts.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "track",
    "target": "core_xxx"
  }'
```

#### search

Fuzzy text search across entities and facts.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "search",
    "query": "grocery",
    "target_type": "entity",
    "coverage": "content",
    "limit": 10
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Search query |
| `target_type` | string | No | "entity", "fact", or "all" (default: "all") |
| `coverage` | string | No | "names", "content", or "full" (default: "content") |
| `limit` | integer | No | Max results (default: 20) |

---

## REST API

### Endpoint Structure

```
/api/v1/{resource}/{id}
```

### Transactions

**List transactions:**
```bash
curl http://localhost:8080/api/v1/transactions \
  -H "Authorization: Bearer $TOKEN"
```

**Get single transaction:**
```bash
curl http://localhost:8080/api/v1/transactions/core_xxx \
  -H "Authorization: Bearer $TOKEN"
```

**Create transaction:**
```bash
curl -X POST http://localhost:8080/api/v1/transactions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 100.00,
    "currency": "SGD",
    "description": "Grocery shopping"
  }'
```

**Update transaction:**
```bash
curl -X PUT http://localhost:8080/api/v1/transactions/core_xxx \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated description"
  }'
```

**Delete transaction:**
```bash
curl -X DELETE http://localhost:8080/api/v1/transactions/core_xxx \
  -H "Authorization: Bearer $TOKEN"
```

### Recurrences

**List recurrences:**
```bash
curl http://localhost:8080/api/v1/recurrences \
  -H "Authorization: Bearer $TOKEN"
```

**Create recurrence:**
```bash
curl -X POST http://localhost:8080/api/v1/recurrences \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 50.00,
    "currency": "SGD",
    "description": "Monthly subscription",
    "frequency": "monthly",
    "day_of_month": 1
  }'
```

---

## Error Handling

### Error Response Format

All errors follow this structure:

```json
{
  "error": {
    "type": "ErrorType",
    "message": "Human-readable error message",
    "details": { ... }  // Optional
  }
}
```

### Error Types

| Type | HTTP Code | Description |
|------|------------|-------------|
| `ValidationError` | 400 | Request validation failed |
| `AuthenticationError` | 401 | Invalid/missing credentials |
| `ResourceNotFound` | 404 | Entity/fact not found |
| `ConflictError` | 409 | Optimistic lock conflict |
| `InternalServerError` | 500 | Unexpected server error |

### Common Errors

**Missing required field:**
```json
{
  "error": {
    "type": "ValidationError",
    "message": "Missing required field: op"
  }
}
```

**Invalid credentials:**
```json
{
  "error": {
    "type": "AuthenticationError",
    "message": "Invalid username or password"
  }
}
```

**Entity not found:**
```json
{
  "error": {
    "type": "ResourceNotFound",
    "message": "Entity not found",
    "details": {"uuid": "core_abc123"}
  }
}
```

---

## Common Data Types

### UUID Format

All UUIDs use a prefix to indicate layer:

| Prefix | Layer | Example |
|--------|-------|---------|
| `core_` | Core entities | `core_550e8400-e29b-41d4-a716-446655440000` |
| `soil_` | Soil facts (Items) | `soil_550e8400-e29b-41d4-a716-446655440000` |
| `rel_` | Relations | `rel_550e8400-e29b-41d4-a716-446655440000` |
| `usr_` | Users | `usr_550e8400-e29b-41d4-a716-446655440000` |

### Timestamp Format

All timestamps use ISO 8601 format: `2026-02-14T10:30:00Z`

### Entity Types

| Type | Description |
|------|-------------|
| `Transaction` | Financial transaction |
| `Recurrence` | Recurring transaction pattern |
| `Contact` | Contact information |
| `Account` | Financial account |
| `Artifact` | Project Studio artifact |
| `Scope` | Project Studio scope (RFC-003) |

### Fact Types (Items)

| Type | Description |
|------|-------------|
| `Message` | Conversation message |
| `Email` | Imported email (gmail provider / mbox importer) |
| `Note` | User note |
| `ToolCall` | Agent tool invocation |

---

## System Endpoints

### Health Check

```bash
curl http://localhost:8080/health
```

**Response:**
```json
{"status": "ok"}
```

### System Status

```bash
curl http://localhost:8080/status
```

**Response:**
```json
{
  "status": "ok",
  "databases": {
    "soil": "connected",
    "core": "connected",
    "paths": {
      "soil": "/var/lib/memogarden/soil.db",
      "core": "/var/lib/memogarden/core.db"
    }
  },
  "consistency": {
    "status": "normal"
  }
}
```

---

## See Also

- [Quickstart Guide](quickstart.md)
- [Deployment Guide](deployment.md) — user-session mode and the ingestion heartbeat
- [Configuration Reference](configuration.md)
- [memogarden-gmail-provider](https://github.com/memogarden/memogarden-gmail-provider) — gmail ingestion provider
- RFC-005 v7: API Design
