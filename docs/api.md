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
Occurrences, Audit, Settings; no dashboard by operator decision).
Since the 2026-11-19 consolidation the console is the single web GUI:
Keys & agents does full mint/bind/revoke in-console and the standalone
login/api-keys/agents/settings pages are retired (their URLs redirect
into the console):

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

`/api/v1/triage` accepts `scope=` (uuid, builtin alias, or label) and
`account=` (RFC-012 attribution stamp).

### Decision-model recommendations + item URLs (2026-11-22)

The triage queue attaches a per-fact `rec` object
(`{pred, confidence, runner, gate, soft?, nn_dist?}`) from the operator-owned
sidecar `~/.local/share/memogarden/triage-model/recommendations.json`
(path override: `MEMOGARDEN_TRIAGE_RECS`; mtime-cached; absence of an
entry = escalate = no badge). `gate` is the binary classification:
`pass` (confident unclassified) or `escalate`; scope routing on
escalated items is a suggestion only. Response also carries
`decision_model` metadata and `sort` (the ordering actually applied).
The console renders badges + one-tap apply; `✨` marks AI recommendations.

`GET /api/v1/triage?sort=explore` orders facts farthest-first by
`nn_dist` (cosine distance to the nearest operator-decided anchor,
written by recommend.py on every entry) so each decision populates the
least-covered region of the item space; rows without the signal (new
mail, no sidecar) sort last, ties keep canonical-time order. Falls
back to `recent` (canonical-time DESC, the default) when the sidecar
predates the signal — check the response `sort` field to know which
applied. Console toggle persists the preference (default `explore`).

```bash
# Agreement eval: operator amend decisions vs stored recommendations
# (first n=100 non-escalated; soft leans and cleared handling per spec)
curl -H "Authorization: Bearer $JWT" "http://localhost:8600/api/v1/triage/eval?n=100"
# → {"model", "generated_at", "target", "n", "correct", "agreement", "decisions": [...]}

# Triage queue, exploration order (default recent)
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/triage?limit=200&sort=explore"

# Single-fact inspector (unique item URL /item/<uuid> — console shell):
# any historical uuid resolves forward through supersedes; attachments
# (contained family, collapsed to live versions) and parents/references
# (contained_by + replies_to, kind-tagged) included. Facts only.
curl -H "Authorization: Bearer $JWT" "http://localhost:8600/api/v1/items/soil_…"
# → {"item": {uuid, type, canonical_at, data, metadata}, "chain": [...],
#     "attachments": [...], "parents": [{uuid, kind, type, title, scopes}]}

# Item browse/search: scope/type filters; recent browse without q. A
# query runs hybrid — lexical (FTS5 BM25) fused with ✨ semantic (the
# model layer's embed daemon, memogarden-search) — when both planes
# answer; with only one plane up it runs that plane alone (semantic-only
# included); with neither it falls back to narrow keyword LIKE (CJK and
# substrings the tokenizer can't see). Every mode pages with limit/offset;
# a semantic-shaped page walks a candidate list capped at 50 per plane, so
# those modes end there. Daemon hits are re-checked against live rows for
# scope AND type before they are returned (api#91). mode names the plane
# that ran, with "+rerank" appended when the rerank ran on that page;
# index_lag rides along whenever a semantic plane shaped the page.
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/items/search?q=lazada%20invoice&scope=Household&type=Email&limit=50"
# → {"mode": "hybrid|semantic|lexical|keyword_fallback|recent"
#     [+ "+rerank"], "results":
#     [{uuid, type, title, canonical_at, scopes, score?}],
#     "offset", "limit", "next_offset", "has_more", "index_lag"?}
```

Console pages: `/console/items` (browse/search) and `/item/<uuid>`
(item inspector; `/console/item?uuid=` redirects). Scope changes from
the inspector cascade to undecided family members (triage/never-labeled
children); decided children and parents are never rewritten.

### Artifacts — ingested documents (statement pipeline, register v2 D9)

The document-ingestion stage (docling worker + watch) creates a Core
`Artifact` entity per document — markdown content, provenance-linked to
its `Attachment` fact (`data.source.attachment_uuid`), scope-labeled in
`data.scopes`. Read surface (all JWT-only, §4.5-gated — advisory while
`MEMOGARDEN_SCOPE_ENFORCEMENT` is off):

```bash
# List (newest first): ?doc_class= & ?scope= (uuid/alias/label) filters
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/artifacts?limit=50"
# → {"artifacts": [{uuid, label, category, scopes, pages, seconds,
#     truncated, content_type, created_at, updated_at}],   # no content
#    "total", "offset", "limit"}

# Search: recent (no q) → keyword_fallback (LIKE) → lexical (entity_fts
# bm25 — the runtime-ensured internal index over entity content; FTS
# failures degrade down the ladder, never up). ?doc_class= filters.
# Lexical hits are re-checked against the LIVE entity row for
# existence + scope.
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/artifacts/search?q=sp+services&limit=50"
# → {"mode": "lexical|keyword_fallback|recent", "q", "results":
#     [{uuid, label, category, scopes, pages, seconds, updated_at, score?}],
#     "offset", "limit", "next_offset", "has_more"}

# Detail: full content + provenance + the source fact's summary
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/artifacts/core_<uuid>"
# → {"artifact": {…, content, source, derived_from},
#    "source_fact": {uuid, filename, mime_type, size, sha256, …}}

# Source file: the original PDF, streamed from the content-addressed
# store (path resolved under the store root, never trusted verbatim)
curl -OJ -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/artifacts/core_<uuid>/file"
```

Console page: `/console/documents` — browse/search/detail for ingested
documents (doc_class filter: statement/overview/bill/notice/policy/
document/mixed/unknown), with relabel through the existing `/mg edit`
verb (no new write surface; cascade to derived facts is register D8,
a later slice). No REST write surface: artifacts are created by the
extractor only, which stamps `doc_class` at arrival (stage-2 rules-v1,
content-only, evidence-carrying; mixed/unknown stay in Triage).


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
memogarden register scope --name Finances [--description d] [--visibility private|public]
memogarden list scopes                                     # includes visibility
memogarden edit scope --name Finances --visibility public  # flip; warns on bulk effect (console parity)
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

Scope references resolve labels, builtin aliases (`triage`, `personal`),
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
| `/api/v1/providers/{name}/scope-policy` | PUT | Set the provider's scope policy (`{rules, default}`, RFC-011 §4.8; JWT-only) |
| `/api/v1/providers/{name}/accounts` | GET | List the provider's accounts (RFC-012) |
| `/api/v1/providers/{name}/accounts` | POST | Bind an upstream account (`{platform, subject, label?, credential_ref?, config?}`) |
| `/api/v1/providers/{name}/accounts/{id}` | GET | Account manifest (status, last run, error) |
| `/api/v1/providers/{name}/accounts/{id}` | PATCH | Update label/status/config/scope_policy/interval (`scope_policy` PATCH is JWT-only) |
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
subject), which the provider scope policy can match for per-account labeling.
Attachment facts flow through the same runtime seam (RFC-012 §4.9, 2026-11-20)
and are scope-stamped identically to their account's effective policy.
An account may also carry its own **scope policy override**
(`provider_accounts.scope_policy`, `{rules, default}` in the RFC-011 §4.8
shape): when set it REPLACES the provider-level policy entirely for that
account's runs (runs are per-account, so the effective policy resolves once
per run); an explicit null clears the override. PATCHing `scope_policy` is
operator-gated (JWT only — scope labels are authority boundaries, same
invariant as `PUT /providers/{name}/scope-policy`). `GET` manifests and
`list accounts` include the field (`null` = inherit the provider policy):

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

# Per-account scope policy (JWT; REF = scope label, triage/personal alias,
# or uuid; `none` = explicitly unscoped):
memogarden edit account gmail --label nyjc --default-scope NYJC
memogarden edit account gmail --label nyjc --clear-scope-policy
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

**Labeling:** items created via `add` are labeled with the creator's
active scope — in advisory mode too, so labels are right before
enforcement turns on; explicitly supplied `metadata.scopes` require
grants for those scopes (checked under enforcement). A derived fact
inherits its source's labels two ways: amend merges metadata, so the
superseding fact carries the original's labels; and an UNLABELED fact
that declares `metadata.derived_from` (a source **Soil fact's** uuid)
copies that source's *current* labels at creation (RFC-011 §4.6, SG-9 —
the anti-side-channel default for extraction). The second rule is
default-on and applies in both fact-creation lanes: providers copy the
source's labels verbatim — the triage scope included, so text extracted
from a document still waiting in triage stays in triage — while an API
requester keeps only the labels it holds a live grant for under
enforcement, so inheritance can never file a fact into a scope the
requester cannot enter. When no label survives, the fact takes the
ordinary creation default. Nothing emits the marker yet (the statement
pipeline will be the first); Core entities are not covered as sources.
`MEMOGARDEN_DERIVE_INHERIT_SCOPE=off` is the operator's escape hatch.

**Enforcement flag:** `MEMOGARDEN_SCOPE_ENFORCEMENT` (default **off**).
While off, reads are unfiltered and the rollout is provisioning-only.
When on, every read path (fact get, entity get/query, search, explore,
events) excludes items outside the requester's active scope. Reads
honor the visibility axis: a label on a `visibility: public` scope is
readable from any frame (most permissive wins on mixed labels) —
writes stay frame-gated (visibility addendum). Search enforcement is
pre-match at the SQL level: scoped items outside the allowed set never
become matches, so scoped hits cannot displace real ones. `explore`
gates its anchor like a read and presents inaccessible nodes as
unresolved references (truthful count, edges unlisted never). Entity
edit/forget are write-gated and D2 label-grant-checked like amend. On
the event stream (`/mg/events`) this is **suppress** (§4.9(5)): an
API-key subscriber receives nothing — not even the reference — for an
event about an item it cannot read right now. Relation events are judged
on both endpoints, and one unreadable item suppresses the whole event.
Events about unlabeled items flow as before, and JWT sessions are never
suppressed.

**Current limitations (v1, tracked in
[RFC-011 implementation progress](../plan/rfc_011_implementation.md)
and the [visibility addendum](../plan/rfc_011_visibility_addendum.md)):**
- Superseded originals keep their old labels (addendum §6 item 1): `get`
  serves an unlabelled predecessor of a privately-labeled item, and the
  search verb's `fuzzy`/`lexical` strategies still match it. ~4.4k such
  rows exist today. Undecided.
- SG-9 covers facts only — entity creation lanes and Core-entity sources
  are not covered.
- `commit_artifact`, `link`, `unlink` and `edit_relation` have no write
  gate; `handle_track` is ungated.
- Query filtering happens after pagination (`total` reflects the fetched
  set, pages can be short — cardinality leak is ratified).
- `POST /grants` with `expires_at` fails with a 500.

Providers are never scope-gated — they label at creation per operator
policy (`unscoped | triage | rules`).

---

## Operator Console Reads (RFC-015 §3.3)

JWT-only operator conveniences backing the Operator Console
(`/console/*` pages — Triage, Scopes & grants, Keys & agents, Providers,
Occurrences, Audit, Settings; no dashboard by operator decision).
Since the 2026-11-19 consolidation the console is the single web GUI:
Keys & agents does full mint/bind/revoke in-console and the standalone
login/api-keys/agents/settings pages are retired (their URLs redirect
into the console):

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

`/api/v1/triage` accepts `scope=` (uuid, builtin alias, or label) and
`account=` (RFC-012 attribution stamp).

### Decision-model recommendations + item URLs (2026-11-22)

The triage queue attaches a per-fact `rec` object
(`{pred, confidence, runner, gate, soft?, nn_dist?}`) from the operator-owned
sidecar `~/.local/share/memogarden/triage-model/recommendations.json`
(path override: `MEMOGARDEN_TRIAGE_RECS`; mtime-cached; absence of an
entry = escalate = no badge). `gate` is the binary classification:
`pass` (confident unclassified) or `escalate`; scope routing on
escalated items is a suggestion only. Response also carries
`decision_model` metadata and `sort` (the ordering actually applied).
The console renders badges + one-tap apply; `✨` marks AI recommendations.

`GET /api/v1/triage?sort=explore` orders facts farthest-first by
`nn_dist` (cosine distance to the nearest operator-decided anchor,
written by recommend.py on every entry) so each decision populates the
least-covered region of the item space; rows without the signal (new
mail, no sidecar) sort last, ties keep canonical-time order. Falls
back to `recent` (canonical-time DESC, the default) when the sidecar
predates the signal — check the response `sort` field to know which
applied. Console toggle persists the preference (default `explore`).

```bash
# Agreement eval: operator amend decisions vs stored recommendations
# (first n=100 non-escalated; soft leans and cleared handling per spec)
curl -H "Authorization: Bearer $JWT" "http://localhost:8600/api/v1/triage/eval?n=100"
# → {"model", "generated_at", "target", "n", "correct", "agreement", "decisions": [...]}

# Triage queue, exploration order (default recent)
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/triage?limit=200&sort=explore"

# Single-fact inspector (unique item URL /item/<uuid> — console shell):
# any historical uuid resolves forward through supersedes; attachments
# (contained family, collapsed to live versions) and parents/references
# (contained_by + replies_to, kind-tagged) included. Facts only.
curl -H "Authorization: Bearer $JWT" "http://localhost:8600/api/v1/items/soil_…"
# → {"item": {uuid, type, canonical_at, data, metadata}, "chain": [...],
#     "attachments": [...], "parents": [{uuid, kind, type, title, scopes}]}

# Item browse/search: scope/type filters; recent browse without q. A
# query runs hybrid — lexical (FTS5 BM25) fused with ✨ semantic (the
# model layer's embed daemon, memogarden-search) — when both planes
# answer; with only one plane up it runs that plane alone (semantic-only
# included); with neither it falls back to narrow keyword LIKE (CJK and
# substrings the tokenizer can't see). Every mode pages with limit/offset;
# a semantic-shaped page walks a candidate list capped at 50 per plane, so
# those modes end there. Daemon hits are re-checked against live rows for
# scope AND type before they are returned (api#91). mode names the plane
# that ran, with "+rerank" appended when the rerank ran on that page;
# index_lag rides along whenever a semantic plane shaped the page.
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8600/api/v1/items/search?q=lazada%20invoice&scope=Household&type=Email&limit=50"
# → {"mode": "hybrid|semantic|lexical|keyword_fallback|recent"
#     [+ "+rerank"], "results":
#     [{uuid, type, title, canonical_at, scopes, score?}],
#     "offset", "limit", "next_offset", "has_more", "index_lag"?}
```

Console pages: `/console/items` (browse/search) and `/item/<uuid>`
(item inspector; `/console/item?uuid=` redirects). Scope changes from
the inspector cascade to undecided family members (triage/never-labeled
children); decided children and parents are never rewritten.


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
memogarden register scope --name Finances [--description d] [--visibility private|public]
memogarden list scopes                                     # includes visibility
memogarden edit scope --name Finances --visibility public  # flip; warns on bulk effect (console parity)
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

Scope references resolve labels, builtin aliases (`triage`, `personal`),
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
      "_type": "Task",
      "source_id": "01a1a5fc-52cf-768a-8261-e3b94da7972a"
    },
    "limit": 10
  }'
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `filters` | object | No | `_type` narrows the entity type. Any other key is a **data-field equality** filter (#27): plain or dotted keys address `entity.data` (dotted segments become nested JSON paths), composable with `_type` and each other. Filtering runs in the WHERE clause — before pagination, and the count shares the predicate. |
| `start` | integer | No | Pagination start index (default: 0) |
| `limit` | integer | No | Max results (default: 20) |

Superseded entities are excluded by default (`superseded_by IS NULL`).

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

**Fact queries** (`target_type: "fact"`): `type` names the fact type;
equality filters run on the fact's `data` fields (plain or dotted keys,
e.g. `"actor"` or `"params.resource"`) — and on **metadata** via the
reserved `metadata.` dotted prefix (bare `"metadata"` is rejected as
ambiguous; api#66):

```bash
# All completed tada occurrences — verdict + provenance live in metadata
curl -X POST http://localhost:8080/mg \
  -H "X-API-Key: mg_sk_..." \
  -d '{"op": "query", "target_type": "fact", "type": "Event",
       "filters": {"metadata.source": "tada",
                   "metadata.status": "completed"},
       "count": 50}'
```

Superseded facts are excluded by default (`superseded_by IS NULL`).

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

Text search across entities and facts — LIKE matching by default, or
BM25-ranked FTS5 full-text over fact data with `strategy: "lexical"`
(api#95 phase 1; entity matches still LIKE; `semantic` reserved per
RFC-005).

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
| `query` | string | Yes | Search query (lexical strategy hardens it: quoted prefix terms, implicit AND) |
| `target_type` | string | No | "entity", "fact", or "all" (default: "all") |
| `strategy` | string | No | "fuzzy"/"auto" (LIKE, default) or "lexical" (FTS5 BM25 over fact data) |
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
| `Recurrence` | **The shared recurrence entity** — a rule + anchor; occurrences instantiate it via native `recurrence_of`. Generalizes the finance recurring-transaction type; tada series is the first conformer (schemas/recurrence.yaml) |
| `Task` | **A request** — the mutable belief while in flight (tada ontology; statuses confirmed/tentative only). Resolution is CONVERSION into an Event fact + entity retirement, never a stamp (schemas/task.yaml) |
| `Event` | Calendar-event belief (gcal; RFC-014). **Disambiguation:** the Event *fact* below is the occurrence record — same word, different layers (`core_` vs `soil_`) |
| `Artifact` | Project Studio artifact |
| `Scope` | Project Studio scope (RFC-003) |
| `Contact` / `Account` | Legacy finance-domain types — not in the `/mg` create baseline; retained for existing data |

### Fact Types (Items)

| Type | Description |
|------|-------------|
| `Event` | **The universal occurrence record** — immutable memory that something happened; verdict in `metadata.status` (source vocabulary: tada `completed`/`cancelled`, gcal `occurred` via mark-occurred). Authored by Task conversion, provider yield, or `/mg add` (baselined; schemas/event.yaml FACT section). The legacy `Task` fact type is retired — all history superseded onto Events (2026-11-19) — and `EventOccurrence` is retired with it (#31) |
| `Message` | Conversation message |
| `Email` | Imported email (gmail provider / mbox importer) |
| `Note` | User note — the content carrier: prose lives in Note facts (`contained_by`), never inline on Events/Tasks |
| `ToolCall` | Agent tool invocation |
| `Entry` / `Subscription` | RSS/Atom article + feed (rss-importer) |
| `EntityDelta` | Entity change record — every provider entity update/retirement writes one (the delta stream IS the change history) |

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
