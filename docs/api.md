# MemoGarden API Documentation

**Last Updated:** 2026-11-06
**Version:** 0.1.0

Complete reference for MemoGarden APIs - Semantic API, REST API, and Authentication.

---

## Table of Contents

1. [API Overview](#api-overview)
2. [Authentication](#authentication)
3. [Scopes & Grants (RFC-011)](#scopes--grants-rfc-011)
4. [Semantic API (`/mg`)](#semantic-api)
5. [REST API (`/api/v1/`)](#rest-api)
6. [Error Handling](#error-handling)
7. [Common Data Types](#common-data-types)

---

## API Overview

MemoGarden provides three API interfaces:

### Operator Console Reads (RFC-015 §3.3)

JWT-only operator conveniences backing the Operator Console
(`/console/*` pages — Triage, Scopes & grants, Keys & agents, Providers,
Occurrences, Audit; no dashboard by operator decision):

```bash
# Triage queue: facts + entities labeled into a scope (default: builtin triage)
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/triage?limit=200&account=UOB"
# → {"scope", "facts": [...], "entities": [...], "total_facts", "total_entities"}

# Authority overview: scopes + item counts, grants per key, provider policies
curl -H "Authorization: Bearer $JWT" http://localhost:8600/api/v1/scopes/overview
# → {"enforcement", "scopes": [{uuid, label, fact_count, entity_counts}],
#     "grants": [{key_id, key_label, agent_label, grants: [...]}],
#     "provider_policies": {name: policy}}

# Occurrence review queue: past-end events, occurred_at null, no fact yet
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/events/pending-review?window_days=30"
# → {"events": [...], "count", "total_unmarked", "window_days"}
```

`/api/v1/triage` accepts `scope=` (uuid, builtin alias, or label) and
`account=` (RFC-012 attribution stamp).

### Change Password (RFC-015 §3.1)

```bash
curl -X POST http://localhost:8600/auth/change-password \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"current_password": "...", "new_password": "..."}'
```

JWT-only; the current password must be presented; the registration
policy applies (min 8 chars, letter + digit). Writes an identity Action
fact (RFC-010 INV-2).

### Operator CLI (RFC-015 §5)

The `memogarden` CLI carries the operator role via a JWT session —
`memogarden login` stores a 0600 session file at
`~/.config/memogarden/operator-session.json` (custody-follows-issuance);
`MEMOGARDEN_JWT` overrides it. Governed verbs fail fast without a
session. Verb-first grammar, per tier:

```bash
# Tier 0 — session (bare verbs)
memogarden login [--username u]     # prompts for password
memogarden whoami                   # either credential
memogarden change-password          # prompts; --current/--new for scripts
memogarden logout

# Tier 1 — identity & authority (JWT-gated)
memogarden register agent --label readly
memogarden list agents
memogarden register key --label zcode --agent readly   # raw key shown once
memogarden edit key --id <uuid> --bind readly | --unbind
memogarden remove key --id <uuid>
memogarden register scope --name Finances [--description d]
memogarden list scopes
memogarden remove scope --name Finances
memogarden register grant --agent <uuid> --scope triage --scope Finances [--expires ISO]
memogarden list grants --agent <uuid>
memogarden remove grant --id <grant_id>

# Tier 2 — provider operations (API-key OK)
memogarden list providers
memogarden show provider --name gmail
memogarden run provider --name gmail
memogarden run providers            # run all due (heartbeat parity)
memogarden list runs --provider gmail [--account A] [--status failed]
memogarden show run <run_id>
memogarden edit account --provider gmail --label nyjc \
    --status disabled --interval 3540 --config batch_limit=2500

# Tier 3 — review & audit flows (JWT or agent key)
memogarden enter Finances           # grant-gated (SG-4); leave Finances
memogarden list triage [--scope S] [--account A]
memogarden relabel fact --id soil_... --add Finances --remove triage
memogarden list actions [--actor core_...] [--actor-type agent] [--op amend]
memogarden query --type Action --filter actor=core_... --limit 50
memogarden track core_...
```

Scope references resolve labels, builtin aliases (`triage`, `private`),
and uuids client-side via the scopes overview. `relabel` is client-side
sugar over `/mg amend` — D2's per-label grant gate applies unchanged.

---

## Semantic API (`/mg`) - Primary Interface

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
| `/api/v1/providers/{name}/run` | POST | Trigger runs (async 202; account-backed providers run every active account) |
| `/api/v1/providers/{name}/run` | DELETE | Cancel a running run |
| `/api/v1/providers/run` | POST | Run all due work — providers plus due accounts (heartbeat endpoint) |
| `/api/v1/providers/{name}/accounts` | GET | List the provider's accounts (RFC-012) |
| `/api/v1/providers/{name}/accounts` | POST | Bind an upstream account (`{platform, subject, label?, credential_ref?, config?}`) |
| `/api/v1/providers/{name}/accounts/{id}` | GET | Account manifest (status, last run, error) |
| `/api/v1/providers/{name}/accounts/{id}` | PATCH | Update label/status/config/interval |
| `/api/v1/providers/{name}/accounts/{id}` | DELETE | Remove the binding (the HACM credential is untouched) |
| `/api/v1/providers/{name}/accounts/{id}/verify` | POST | Server-side credential probe (re-activates `needs_reauth` on success) |
| `/api/v1/providers/{name}/accounts/{id}/run` | POST | Trigger a run for one account (async 202) |

**Provider accounts (RFC-012).** An account binds one provider to one
verified upstream identity (e.g. a Gmail address). Each account syncs on its
own schedule with its own cursor and failure isolation; one dead account
never blocks the others. Lifecycle: `active` → `needs_reauth` (on a
credential-shaped run failure; the heartbeat skips it until re-linked) →
`active` (after `verify` following a re-consent). History ingestion order
(RFC-012 §4.10): a new account ingests its most recent mail first and
walks backward toward its oldest email, one batch per heartbeat, with no
operator action. Tokens live only in HACM
on the host — the API stores the credential *reference* (`{platform}:{subject}`)
and never sees OAuth material. Facts carry `metadata.account` (the verified
subject), which the provider scope policy can match for per-account labeling:

```bash
# Link a Gmail account end-to-end (consent + HACM + registry row in one
# command; opens a browser for Google consent):
python -m gmail_importer.ingest auth-login --account you@gmail.com \
    --label work --register

# Manage accounts via the CLI (token-free registry surface):
memogarden list accounts --provider gmail
memogarden show account --provider gmail --subject you@gmail.com
memogarden verify account --provider gmail --label work
memogarden run account --provider gmail --subject you@gmail.com
memogarden remove account --provider gmail --id <uuid>
```

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
1 error, 2 usage error.

### Provider Runs (RFC-015 §3.2)

Run history is readable — the verification side of delegated triggers:

```bash
# Recent runs for a provider, newest first (filters: account_id, status, limit)
curl -H "X-API-Key: $KEY" \
  "http://localhost:8600/api/v1/providers/gmail/runs?status=failed&limit=20"
# → {"runs": [{"id", "provider_name", "account_id", "status", "started_at",
#              "finished_at", "triggered_by", "detail"}], "count": n}

# Single run by id (same principal class: any authenticated principal)
curl -H "X-API-Key: $KEY" http://localhost:8600/api/v1/runs/<run_id>
```

`account_id=''` selects provider-level runs only. `detail` carries the
run's counts/error JSON. Read-only and non-governed: matches provider
GET's principal class (JWT or any API key). The CLI reads these via
`memogarden list runs --provider gmail` and `memogarden show run <id>`.

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
keys. **Key authority is scoped by grants (RFC-011):** a key holds a set of
scope grants — scopes it may *enter* — and starts with none (unscoped-only:
it sees the global space of unlabeled items and nothing else). JWT operator
sessions are sovereign: implicit all-grants, no filtering. See
[Scopes & Grants](#scopes--grants-rfc-011) below.

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

**Restricted key classes (capabilities):** keys minted with a non-empty
`capabilities` list are *restricted* — the middleware allows them only on
endpoints covered by those capabilities and returns `403 capability_denied`
for everything else. Known capability: `provider_run` (may request provider
runs — `POST /api/v1/providers/run`, `POST /api/v1/providers/<name>/run`,
`POST /api/v1/providers/<name>/accounts/<id>/run` — but cannot read or write
facts, entities, keys, or provider config). Keys minted without capabilities
are unrestricted (legacy behavior). Capability sets are set at creation or
via `PATCH /api-keys/<id> {"capabilities": [...]}` (JWT only, audited as
`set_capabilities` Action facts). Use case: infrastructure daemons that
should trigger work but hold no read/write authority (e.g. the browser
sessions daemon's post-close drain trigger — RFC-013 ingestion register E3).

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
| `/auth/me` | GET | Identity introspection — user info (JWT) or agent identity (key); includes `actor_type`/`actor`, `grants`, `active_scope` (RFC-011) |
| `/admin/register` | POST | One-time admin registration (localhost only, first run) |
| `/api-keys/` | GET | List API keys (includes `agent_id`) |
| `/api-keys/` | POST | Create new API key (optional `agent_id` to bind, RFC-010) |
| `/api-keys/<id>` | PATCH | Bind / re-bind / unbind an Agent entity (RFC-010) |
| `/api-keys/<id>` | DELETE | Revoke API key |
| `/agents` | POST | Mint an Agent entity (JWT only, RFC-010) |
| `/agents` | GET | List agents with bound key prefixes (JWT only, RFC-010) |
| `/grants` | POST | Issue a scope grant to a key or agent (JWT only, RFC-011) |
| `/grants` | GET | List grants for `?key_id=` or `?agent_id=` (JWT only, RFC-011) |
| `/grants/<grant_id>` | DELETE | Revoke a grant (JWT only, RFC-011) |

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

## Scopes & Grants (RFC-011)

MemoGarden's access model is **attention-gated**: a scope is an intention,
entering it declares that intention, and access follows attention.

**The model in five lines:**

1. Items without a scope label are the **global space** — readable and
   writable by every authenticated principal.
2. Items carry scope labels in `metadata.scopes` (a list of Scope UUIDs; an
   item may be in several). Scopes are **restricted by default**: entering
   one requires a grant.
3. **Access = granted ∧ entered.** A scoped item is readable/writable only
   by principals whose *active scope* is one of the item's labels. A grant
   held without entering authorizes nothing.
4. **One scope at a time** — scopes do not co-activate. Entering a second
   scope is an atomic switch; the first is left.
5. JWT operator sessions are sovereign: implicit all-grants, never
   filtered. Grants partition what *keys and agents* reach, never what the
   operator reaches.

**Issuing grants (JWT only — keys cannot mint grants, even their own):**

```bash
# Grant a key (the agent holding it may then enter the scope)
curl -X POST http://localhost:8080/grants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key_id": "660e8400-...", "scope_uuid": "core_1c9d...", \
       "expires_at": "2026-12-31T23:59:59Z"}'
# → 201 {"grants": [{"grant_id": "...", "scope_uuid": "...", ...}], "count": 1}

# Grant an agent instead: applies to ALL keys bound to it; keys bound
# later inherit the envelope at bind time
curl -X POST http://localhost:8080/grants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "core_6b0f...", "scope_uuid": "core_1c9d..."}'

# List / revoke
curl "http://localhost:8080/grants?key_id=660e8400-..." -H "Authorization: Bearer $TOKEN"
curl -X DELETE http://localhost:8080/grants/<grant_id> -H "Authorization: Bearer $TOKEN"
```

Revocation is immediate for future operations (grants are re-read per
request); an in-flight session is not retroactively ejected — revoke the
*key* itself for that.

**Labeling:** items created via `add` are auto-labeled with the creator's
active scope when enforcement is on; explicitly supplied
`metadata.scopes` require grants for those scopes. Derived facts inherit
their source's labels (amend merges metadata, so the superseding fact
carries the original's labels).

**Enforcement flag:** `MEMOGARDEN_SCOPE_ENFORCEMENT` (default **off**).
While off, reads are unfiltered and the rollout is provisioning-only.
When on, every read path (get, query, search, events) excludes items
outside the requester's active scope; search filtering happens before
results are returned — scoped items never surface as matches.

**Current limitations (v1, tracked in
[RFC-011 implementation progress](../plan/rfc_011_implementation.md)):**
entity and relation reads are not yet scope-enforced (facts are);
relabeling has no dedicated verb and amend's label changes are not yet
grant-checked; query filtering happens after pagination (pages can be
short). Providers are never scope-gated — they label at creation per
operator policy (`unscoped | triage | rules`).

---

## Operator Console Reads (RFC-015 §3.3)

JWT-only operator conveniences backing the Operator Console
(`/console/*` pages — Triage, Scopes & grants, Keys & agents, Providers,
Occurrences, Audit; no dashboard by operator decision):

```bash
# Triage queue: facts + entities labeled into a scope (default: builtin triage)
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/triage?limit=200&account=UOB"
# → {"scope", "facts": [...], "entities": [...], "total_facts", "total_entities"}

# Authority overview: scopes + item counts, grants per key, provider policies
curl -H "Authorization: Bearer $JWT" http://localhost:8600/api/v1/scopes/overview
# → {"enforcement", "scopes": [{uuid, label, fact_count, entity_counts}],
#     "grants": [{key_id, key_label, agent_label, grants: [...]}],
#     "provider_policies": {name: policy}}

# Occurrence review queue: past-end events, occurred_at null, no fact yet
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/events/pending-review?window_days=30"
# → {"events": [...], "count", "total_unmarked", "window_days"}
```

`/api/v1/triage` accepts `scope=` (uuid, builtin alias, or label) and
`account=` (RFC-012 attribution stamp).

### Change Password (RFC-015 §3.1)

```bash
curl -X POST http://localhost:8600/auth/change-password \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"current_password": "...", "new_password": "..."}'
```

JWT-only; the current password must be presented; the registration
policy applies (min 8 chars, letter + digit). Writes an identity Action
fact (RFC-010 INV-2).

### Operator CLI (RFC-015 §5)

The `memogarden` CLI carries the operator role via a JWT session —
`memogarden login` stores a 0600 session file at
`~/.config/memogarden/operator-session.json` (custody-follows-issuance);
`MEMOGARDEN_JWT` overrides it. Governed verbs fail fast without a
session. Verb-first grammar, per tier:

```bash
# Tier 0 — session (bare verbs)
memogarden login [--username u]     # prompts for password
memogarden whoami                   # either credential
memogarden change-password          # prompts; --current/--new for scripts
memogarden logout

# Tier 1 — identity & authority (JWT-gated)
memogarden register agent --label readly
memogarden list agents
memogarden register key --label zcode --agent readly   # raw key shown once
memogarden edit key --id <uuid> --bind readly | --unbind
memogarden remove key --id <uuid>
memogarden register scope --name Finances [--description d]
memogarden list scopes
memogarden remove scope --name Finances
memogarden register grant --agent <uuid> --scope triage --scope Finances [--expires ISO]
memogarden list grants --agent <uuid>
memogarden remove grant --id <grant_id>

# Tier 2 — provider operations (API-key OK)
memogarden list providers
memogarden show provider --name gmail
memogarden run provider --name gmail
memogarden run providers            # run all due (heartbeat parity)
memogarden list runs --provider gmail [--account A] [--status failed]
memogarden show run <run_id>
memogarden edit account --provider gmail --label nyjc \
    --status disabled --interval 3540 --config batch_limit=2500

# Tier 3 — review & audit flows (JWT or agent key)
memogarden enter Finances           # grant-gated (SG-4); leave Finances
memogarden list triage [--scope S] [--account A]
memogarden relabel fact --id soil_... --add Finances --remove triage
memogarden list actions [--actor core_...] [--actor-type agent] [--op amend]
memogarden query --type Action --filter actor=core_... --limit 50
memogarden track core_...
```

Scope references resolve labels, builtin aliases (`triage`, `private`),
and uuids client-side via the scopes overview. `relabel` is client-side
sugar over `/mg amend` — D2's per-label grant gate applies unchanged.

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

Add a new fact (Item) to Soil. `type` names the fact type; all other
request fields are top-level (the legacy `params`-nested form shown in older
revisions of this doc is rejected by the schema).

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "add",
    "type": "Message",
    "data": {
      "description": "Meeting notes from today",
      "fragments": ["Discussed budget", "Reviewed transactions"]
    }
  }'
```

#### amend

Amend a fact (create superseding fact). Like `add`, `data`/`canonical_at`/
`metadata` are top-level request fields — not nested under `params`.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "amend",
    "target": "soil_xxx",
    "data": {
      "description": "Updated meeting notes",
      "fragments": ["Discussed budget", "Reviewed transactions", "Action items"]
    }
  }'
```

Semantics (verified against `api/handlers/soil.py` `handle_amend`):
- `data` **replaces the original's data wholesale** — pass the complete
  corrected dict, not a diff.
- `metadata` merges over the original's metadata (scope-label inheritance
  and the RFC-011 relabel-grant rules apply to the merge).
- `canonical_at` defaults to the original's (amends do not reorder
  canonical-time-ordered views); `realized_at` is the amend time.
- The original is marked superseded (linked by a `supersedes` relation) and
  is excluded from query results by default (`superseded_by IS NULL`).
- A fact can only be amended while it is not itself already superseded —
  amend the current (superseding) fact instead.

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

### Context Bundle Operations (RFC-003 v5 / RFC-011)

#### enter

Adopt a scope as your exclusive active scope (RFC-011 §4.4). Requires a
grant on the scope (JWT operator sessions bypass). Entering while inside
another scope is an **atomic switch** — `previous_scope` in the response
reports what you left; re-entering your active scope is an idempotent
no-op. Every transition (and every denied enter) is audited as an Action
fact.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "enter",
    "scope": "core_xxx"
  }'
```

**Response:** `{"scope": "core_xxx", "previous_scope": null | "core_prev", "active_scope": "core_xxx"}`

#### leave

Release your active scope (the named scope must be the one you are in).
Afterwards you operate unscoped — global space only.

```bash
curl -X POST http://localhost:8080/mg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "op": "leave",
    "scope": "core_xxx"
  }'
```

**Response:** `{"scope": "core_xxx", "active_scope": null}`

#### focus

**Retired verb** (RFC-011): with exclusive activation there is no
primary-vs-active distinction. Kept as a no-op alias during the migration
window — responds with your current `active_scope`, changes nothing.
Migrate to `enter`.

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
- RFC-005 v8: API Design
- RFC-010 v1.2: Agent Identity & Attribution
- RFC-011 v1.1: Scopes, Grants, and Attention-Gated Access
- [RFC-011 implementation progress](../plan/rfc_011_implementation.md)
