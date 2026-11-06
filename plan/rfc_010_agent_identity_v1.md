# RFC-010: Agent Identity & Attribution

**Version:** 1.0
**Status:** Draft
**Author:** JS
**Date:** 2026-11-06

---

## Abstract

This RFC specifies **Agent Identity** for MemoGarden: how AI agents obtain
identity, how credentials bind to it, and how every audited operation
attributes itself to it. Agents are first-class **Agent entities** in the
Core entity registry — the same hash-chained registry that holds Artifacts,
Transactions, and Scopes — and API keys bind to them. Action facts record
the bound agent's entity uuid as `actor`, restoring the attribution model
specified by the PRD (§Audit Trail) and RFC-005 (Action Fact Schema).

The mechanism consists of three components:

1. **Agent Entity:** operator-minted identity object (`Agent` entity type),
   surviving key rotation and machine migration
2. **Key Binding:** a nullable `agent_entity_id` on `api_keys`, making
   "one key per agent" a database relationship instead of a naming
   convention
3. **Attribution Pipeline:** the auth middleware resolves the binding
   read-only into `g.actor` / `g.actor_type`; Action facts record both

This RFC focuses on **behavioral invariants** (§8) rather than
implementation minutiae, enabling optimization while preserving audit
semantics. It additively extends RFC-005's Action Fact Schema; no RFC-005
behavior changes.

---

## 1. Motivation

### 1.1 Problem Statement

MemoGarden is agent-first: humans and agents use the same APIs, and the PRD
commits to a "unified audit trail for human operators and AI agents". The
audit machinery exists. The identity behind it does not. Three concrete
gaps:

**Anonymous agents.** Auth today resolves `X-API-Key` → user and records
`actor = g.username` (`api/semantic.py`). The key — the agent's only
distinguishing artifact — is dropped at the auth boundary
(`api/middleware/decorators.py` keeps `user_id` and `username`; the
`api_key_id` returned by `verify_api_key_and_get_user` is discarded). Two
agents running under the same user are indistinguishable across the entire
Action-fact history. This makes:

- Per-agent traceability (`mg.search(filters={"actor": agent_uuid})`) impossible
- Incident forensics blind ("which agent wrote this fact?")
- The PRD's trace query specified but never satisfiable

**No provenance anchor.** Evidence objects carry
`source: 'agent_inferred'` with confidence and basis — but the "agent" in
that sentence is whatever the client self-reports. There is no server-side
ground truth for who inferred. This makes:

- Belief provenance unauditable (any client can claim any source)
- Agent handover (RFC-003 §1.1) missing its identity half: handover
  transfers context between *named* contributors, and agents currently
  cannot be named

**Identity dies with credentials.** The only per-agent state today is the
key's `name` label. Revoke-and-remint — routine rotation, the coming
laptop→RPi migration — forks that label into a new, unrelated string. This
makes:

- Longitudinal agent history fragmentary
- Multi-machine agents impossible to unify (two keys, two names, no
  relationship)

### 1.2 Current State

Two existing facts make closing these gaps cheap:

1. **API keys are already entity-backed.** `create_api_key`
   (`api/middleware/api_keys.py`) inserts a hash-chained `entity` row of
   type `ApiKey` sharing the key's uuid (FK `api_keys.id → entity(uuid)`,
   `core.sql`). The credential already participates in the entity registry;
   only the *identity above it* is missing.
2. **RFC-001 §4.1–4.3 already designed for this.** Its key model carried
   `participant_id` ("Agent or service identifier"), and its time-limited
   grants deliberately attach to the participant so they "survive key
   rotation". The participant concept was dropped during implementation.
   This RFC reintroduces it as the Agent entity.

### 1.3 Design Goals

**G1. Per-agent traceability:** every audited operation attributes to a
stable agent identity when one exists
**G2. Identity outlives credentials:** key rotation, expiry, and machine
migration never fork an agent's history
**G3. Operator-gated issuance:** agents, operators, and API keys are
created only by explicit operator action — never as a side effect of
authentication or any other operation
**G4. Zero new transport:** agents authenticate exactly as today
(`X-API-Key`); no login flows, no new credential formats
**G5. Honest unbound state:** keys without agents are a first-class,
queryable condition — not an error to be silently auto-fixed
**G6. Attribution, not authorization:** agent identity partitions the audit
trail, never permissions (scopes explicitly deferred; see §14)

---

## 2. Core Principle

**Identity is issued, never assumed.**

Operators mint identities. Agents hold credentials. The system attributes
operations. These are three different acts performed by three different
parties, and the architecture must not blur them:

- An agent cannot mint identity (no self-registration), because identity
  the agent can create, the agent can forge.
- The system cannot mint identity (no lazy backfill at auth time), because
  identity that appears as a side effect has no accountable issuer.
- The operator mints identity, because the operator is the only party with
  the authority to vouch for a new actor in the system.

**The governing invariant:** the authentication hot path is **read-only
over identity state**. No code path in request handling may create or
modify identity rows. All identity writes occur in JWT-gated management
endpoints. This is the same separation RFC-008 applies to transactions —
make the common path incapable of the dangerous action, rather than
careful within it.

**Alternative rejected:** minting the Agent entity lazily on first
authenticated use of an unbound key. It is operationally convenient (zero
migration steps) but violates G3 at the root: the issuer of record becomes
"whichever request happened to arrive first", and unbound keys silently
grow identities their operator never vouched for.

---

## 3. Architecture Overview

```
 Operator (JWT)                        Agent (X-API-Key)
      |                                       |
      |  POST /agents                         |
      |  POST /api-keys/  agent_id=...        |
      |  PATCH /api-keys/<id> agent_id=...    |
      v                                       v
 +------------- JWT-gated ------------+  +------------ read-only -----------+
 |          Identity Issuance         |  |        Authentication            |
 |   (the ONLY writers of identity)   |  |  resolve key -> agent binding    |
 +-----------------+------------------+  +---------------+------------------+
                   |                                          |
                   v                                          v
        +----------------------+                   +----------------------+
        |   Core entity table  |                   |  g.actor             |
        |  type='Agent'        |<--referenced by---|  g.actor_type        |
        |  core_<uuid>         |                   |  (flask.g, per req)  |
        +----------+-----------+                   +----------+-----------+
                   |                                          |
   api_keys.agent_entity_id (nullable FK)                     v
                                                    +----------------------+
                                                    |    Action fact       |
                                                    |  actor: core_<uuid>  |
                                                    |  actor_type: "agent" |
                                                    +----------------------+
```

Left side: identity creation, gated, rare, explicit. Right side: identity
*use*, per-request, read-only. The two sides share nothing but the
database.

---

## 4. The Agent Entity

### 4.1 Definition

**An Agent is a Core entity of type `Agent` representing a persistent
actor — a tool, assistant, or automated client — that holds MemoGarden
credentials and performs operations.**

- **uuid:** generated with the canonical `core_` prefix at creation
  (`uid.add_core_prefix(secret.generate_uuid())`)
- **Registry:** the existing `entity` table; hash-chained, versioned,
  delta-edited — identical treatment to every other entity
- **Schema:** `memogarden-system/system/schemas/types/entities/agent.schema.json`
  (new; entity data validated per the existing type-schema convention)

### 4.2 Entity Data

```json
{
  "label": "claude-code",
  "description": "ZCode sessions on the laptop",
  "owner": "usr_550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Req | Purpose |
|-------|-----|---------|
| `label` | yes | Operator-facing name. Unique among Agent entities, enforced at the endpoint (§6) |
| `description` | no | Free text: what this agent is, where it runs |
| `owner` | yes | Owning user id. Single-operator today; costs nothing and matches RFC-001 §4.4's multi-operator posture |

### 4.3 Lifecycle

| Transition | Mechanism | Notes |
|------------|-----------|-------|
| Mint | `POST /agents` (JWT) | The only creation path (§6.1) |
| Rename / re-describe | Entity delta edit | Mutable snapshot, immutable memory — the edit is logged like any entity edit |
| Suspend | Revoke all bound keys | De facto; key revocation already exists |
| Retire | Entity supersession (`superseded_by`) | Follow-up; metadata column already exists in the entity table |
| Delete | **None in v1** | History must remain resolvable; supersession replaces deletion |

Known divergence, deliberately not propagated: existing `ApiKey` entity
rows store bare uuids (`secret.generate_uuid()` without prefix) against the
entity table's documented `core_` convention. Agent entities follow the
documented convention; ApiKey rows are left as-is (cosmetic; §14).

### 4.4 Alternative rejected: agents as an infra table

The provider registry (RFC-004 §12.2) lives deliberately outside the
entity/delta machinery — it is deployment config, not user state. An
`agents` table would follow that precedent and be simpler. Rejected because
the cases are not analogous: agent identity *is* semantic-layer state —
the PRD positions agents as first-class contributors whose actions are
facts, and RFC-001 §4.3 attaches long-lived grants to the participant.
Identity outside the entity registry would be the only unversioned,
unhash-chained, unqueryable-via-Semantic-API object in the system.

---

## 5. Key Binding

### 5.1 Schema

One nullable column, one migration
(`memogarden-system`, following the `migrate_YYYYMMDD_to_YYYYMMDD.sql`
convention):

```sql
ALTER TABLE api_keys ADD COLUMN agent_entity_id TEXT REFERENCES entity(uuid);
```

**Cardinality:** many keys → one agent (laptop + RPi share one identity);
one key → at most one agent (attribution is unambiguous by construction).

### 5.2 Attribution States

| State | `actor` on Action facts | `actor_type` | Who gets it |
|-------|------------------------|--------------|-------------|
| Key bound to agent | `core_<agent uuid>` | `"agent"` | Bound key requests |
| Key unbound | owning username (today's behavior, byte-identical) | `"api_key"` | All keys that exist today |
| JWT session | owning username (today's behavior) | `"operator"` | Human browser/CLI sessions |

`actor_type = "api_key"` is a **queryable attribution-gap marker**:

```python
# Every operation performed under key authority without agent identity
mg.search(filters={"actor_type": "api_key"})
```

The operator closes gaps by binding, never automatically (G5). Until then,
the trail records exactly what is known — a key holder acted — with today's
precision and one new bit of honesty.

### 5.3 State Transitions

| From → To | Mechanism | Constraint |
|-----------|-----------|------------|
| unbound → bound | `POST /api-keys/` with `agent_id`, or `PATCH /api-keys/<id>` | Agent must exist and be owned by the caller |
| bound → bound (different agent) | `PATCH /api-keys/<id>` | Same ownership check; re-attribution is explicit and auditable |
| bound → unbound | `PATCH /api-keys/<id>` with `{"agent_id": null}` | Allowed (Open Question 3) |
| revoked → bound | — | **Rejected**; revoked keys bind nothing |

Binding state is *current fact*, not audit record: the history of "which
agent was this key attributed to, when" lives in Action facts and entity
deltas, both append-only.

### 5.4 Alternative rejected: the ApiKey entity as the actor

Zero schema change — `g.actor = f"core_{api_key_id}"` and done. Rejected on
continuity (G2): rotation forks identity; the laptop→RPi migration splits
one agent into two; nothing can later merge them. RFC-001 §4.3 already
resolved this question for grants (attach to participant, not key); the
same answer applies to attribution.

---

## 6. Issuance & Binding Protocol

All endpoints JWT-only (`@auth_required`). Key holders receive **401** —
identical to existing key-management endpoints (`GET/POST /api-keys/`,
`DELETE /api-keys/<id>`), preserving the invariant that credentials cannot
manage credentials.

### 6.1 Mint an Agent

```
POST /agents
Authorization: Bearer <JWT>
{"label": "claude-code", "description": "ZCode sessions on the laptop"}
```

`201`:

```json
{
  "agent_id": "core_6b0f1e2a-...",
  "label": "claude-code",
  "description": "ZCode sessions on the laptop",
  "owner": "usr_550e8400-...",
  "created_at": "2026-11-06T09:00:00Z"
}
```

Errors: `401` not JWT; `409` label collision with an existing agent; `422`
schema violation.

### 6.2 List Agents

```
GET /agents
Authorization: Bearer <JWT>
```

`200`:

```json
[
  {
    "agent_id": "core_6b0f1e2a-...",
    "label": "claude-code",
    "key_prefixes": ["mg_sk_agent_9a2b", "mg_sk_agent_c3d4"],
    "bound_key_count": 2,
    "created_at": "2026-11-06T09:00:00Z"
  }
]
```

Management listing only — agents as *data* are read through the Semantic
API like any entity. That is the point of dogfooding them.

### 6.3 Bind at Key Creation

Existing `POST /api-keys/` gains one optional field:

```
POST /api-keys/
Authorization: Bearer <JWT>
{"name": "claude-code-laptop", "agent_id": "core_6b0f1e2a-..."}
```

Response unchanged from today plus, when bound:

```json
{
  "id": "660e8400-...",
  "name": "claude-code-laptop",
  "key": "mg_sk_agent_...(shown once)",
  "prefix": "mg_sk_agent_",
  "agent_id": "core_6b0f1e2a-...",
  "created_at": "2026-11-06T09:00:00Z"
}
```

Errors: `404` unknown agent; `403` agent owned by another user; `422`
malformed `agent_id`.

This is the rotation and multi-machine path: mint key, bind to the *same*
agent, history stays continuous (G2).

### 6.4 Bind, Re-bind, or Unbind an Existing Key

```
PATCH /api-keys/660e8400-...
Authorization: Bearer <JWT>
{"agent_id": "core_6b0f1e2a-..."}     // bind / re-bind
{"agent_id": null}                     // unbind
```

`200` with the updated key record (no secret material). Errors per §6.3,
plus `404` unknown key, `409` binding a revoked key.

This endpoint *is* the migration path (§11): existing keys — including the
live gmail provider key — become agent-attributed when and only when the
operator runs this call.

---

## 7. Attribution Pipeline

### 7.1 Request Walkthrough (bound key)

1. Request arrives: `POST /mg`, `X-API-Key: mg_sk_agent_9a2b...`
2. `_authenticate_request` → `verify_api_key_and_get_user`: existing
   bcrypt loop over active keys; return extended to
   `(user_id, api_key_id, agent_entity_id)` — one indexed lookup by
   primary key, added to the existing verification query
3. Middleware sets, read-only:
   `g.user_id`, `g.username`, `g.is_admin`, `g.auth_method="api_key"`
   (unchanged) plus `g.actor="core_6b0f..."`, `g.actor_type="agent"`
4. Authorization proceeds exactly as today — full authority of the owning
   user; the binding plays no role in permission decisions
5. `handlers/decorators.py` writes the Action fact *before* execution
   (immediate commit, existing behavior):
   `data.actor = g.actor`, `data.actor_type = g.actor_type`
6. Handler runs; ActionResult fact records outcome (unchanged)
7. Envelope returns `"actor": "core_6b0f..."` (today: username)

Steps 2–7 contain one join and one extra JSON field. No new queries, no new
processes, no new tables.

### 7.2 Action Fact Schema Extension

Additive to RFC-005 v7.1:

```python
{
  "type": "Action",
  "uuid": "soil_<uuid4>",
  "actor": "core_6b0f1e2a-...",        # entity reference when agent-attributed
  "actor_type": "agent",               # NEW: "agent" | "api_key" | "operator"
  "operation": "edit",
  "params": { "...": "..." },
  "context": ["ctx_abc123"],
  "timestamp": "2026-11-06T09:00:00.123Z",
  "request_id": "req_xyz789",
  "parent_action": null
}
```

`actor_type` exists so the audit's most common filters need no join:

```python
mg.search(filters={"actor": "core_6b0f..."})   # one agent's operations (PRD query)
mg.search(filters={"actor_type": "api_key"})   # attribution gaps
mg.search(filters={"actor_type": "operator"})  # human-only operations
```

RFC-005's next revision should fold `actor_type` into its Action Fact
Schema and restate `actor` as "entity reference for agent-attributed
operations, username otherwise". ActionResult facts unchanged.

### 7.3 Introspection: `/auth/me`

Extended for key-authenticated requests. Bound:

```json
{
  "actor_type": "agent",
  "actor": "core_6b0f1e2a-...",
  "label": "claude-code",
  "owner": "admin",
  "key_prefix": "mg_sk_agent_9a2b",
  "username": "admin",
  "is_admin": true
}
```

Unbound:

```json
{
  "actor_type": "api_key",
  "actor": "admin",
  "key_prefix": "mg_sk_agent_c3d4",
  "username": "admin",
  "is_admin": true
}
```

JWT sessions return today's shape plus `"actor_type": "operator"`. No
agent fields ever leak on unbound keys.

Purpose: an agent discovers its canonical identity at runtime and stamps
Evidence provenance (`agent_inferred`, basis, confidence) with server-side
ground truth. Provenance stops being a self-report.

---

## 8. Behavioral Invariants

Implementation may vary; these must not.

**INV-1. Read-only authentication.** No request, authenticated or not,
ever creates or modifies an identity row (`entity` type Agent/ApiKey,
`api_keys`). Identity writes occur only in JWT-gated management endpoints.

**INV-2. Issuance is attributed.** Every Agent entity's creation is
traceable to a JWT-authenticated operator action (an Action fact with
`actor_type: "operator"` describing the mint).

**INV-3. Attribution reflects the binding at request time.** An operation
is attributed to the agent its key was bound to *when the request was
made*. Re-binding changes future attribution; past facts are immutable and
never reinterpreted.

**INV-4. Unbound keys authenticate normally.** Binding is never a
precondition for access. Unbound behavior is byte-identical to today's,
plus `actor_type: "api_key"`.

**INV-5. Authority is unaffected by identity.** A bound key and an unbound
key of the same user have identical permissions. The Agent entity conveys
no authority and can therefore leak nothing.

**INV-6. One key, at most one agent.** Attribution is a function, not a
relation. Multi-agent keys are expressible only as multiple keys.

**INV-7. Identity survives credentials.** Rotation, expiry, and revocation
never mutate or fork the Agent entity. Continuity of history across
credential events is the design's reason to exist.

---

## 9. Failure Modes & Edge Cases

**Revoked key, bound agent:** auth fails as today (revocation checked in
the verification loop). The binding row remains — harmless, and correct:
if the operator re-mints and re-binds, continuity is preserved.

**Agent entity edited mid-flight:** attribution resolves the uuid, not the
label; a rename between request and audit-read changes nothing material.

**Label collision:** `POST /agents` returns 409; labels are for operators,
uuids are for the system. No silent dedup, no auto-suffix.

**Provider runtime operations:** provider runs record to `provider_runs`
(RFC-004 §12.2), not Action facts; unaffected. If provider runs ever write
Action facts, a fourth `actor_type` (`"system"`) is the natural extension
(Open Question 2).

**Clock skew / `last_seen`:** unchanged semantics; the per-key `last_seen`
update stays in the verification path and remains the shared-key forensic
signal.

**Deleted agent:** impossible in v1 (§4.3) — attribution always resolves.

---

## 10. Cost Analysis

For a personal system, in the spirit of RFC-008 §4.3's exposure arithmetic:

- **Per-request latency:** one additional primary-key lookup on the
  existing verification query. The dominant auth cost is already the
  bcrypt loop over active keys (unchanged); the join is nanoseconds against
  milliseconds.
- **Per-Action-fact storage:** one uuid (36 bytes) + one short string. At
  the gmail workload (~1.2k facts/day including ingestion), annual growth
  from attribution is well under 1 MB.
- **Entities:** one row per agent ever minted. A personal deployment will
  count agents in single digits.
- **Processes/services:** zero new. No daemons, no timers, no workers
  (§14, service minimalism). The feature is columns, endpoints, and
  middleware field-plumbing inside the existing API process.
- **Rollout risk:** zero until first use — deployed behavior is
  byte-identical for every existing key and client (INV-4).

---

## 11. Migration

**Explicit binding only. No backfill, no script, no default.**

| Step | Actor | Action |
|------|-------|--------|
| 1 | Deploy | `memogarden-system` migration adds nullable column; `memogarden-api` ships endpoints + middleware |
| 2 | — | Every existing key now operates in the unbound state; behavior unchanged (INV-4) |
| 3 | Operator (optional, any time) | `POST /agents` per real agent |
| 4 | Operator (optional, any time) | `PATCH /api-keys/<id>` to bind live keys, e.g. the gmail provider key |

Old Action facts are immutable and keep username actors; history is never
rewritten (INV-3). There is no state that *must* be migrated for correct
operation — binding is an operator decision about audit quality, not a
correctness step.

---

## 12. Security Analysis

**Issuance stays human-gated.** Agents cannot mint, bind, list, or revoke
anything (§6 all JWT-only). A leaked agent key grants exactly what it
grants today — the owning user's authority — and is revocable exactly as
today. The Agent entity conveys no authority (INV-5), so binding changes
no threat model.

**Write-surface shrinkage.** Before: any authenticated request could, in
principle, trigger identity-adjacent writes through code paths that "help"
(auto-provisioning patterns). After: the set of code paths that write
identity rows is closed, enumerable, and JWT-gated (INV-1). Auditing
identity issuance = reading the JWT-gated Action facts.

**The contract that attribution rests on.** Sharing one key across two
agents collapses them into one actor, and no mechanism can detect it.
"One key per agent" — documented in `docs/api.md` — is the operational
assumption. `last_seen` per key remains the forensic signal for shared-key
suspicion. The RFC's contribution is making the honest path cheap and the
collapsed path *visible* (`actor_type: "api_key"` marks exactly the
operations that lack agent identity).

**No isolation change.** V1 is single-operator; agent identity partitions
audit, not data. RFC-001 §4.4 multi-operator isolation remains future work
and would layer on `Agent.owner`.

---

## 13. Testing Strategy

Per `memogarden-testing` conventions (`run_tests.sh`, pytest) in
`memogarden-api/tests/`:

1. **Issuance gating** — `POST /agents`, `PATCH /api-keys/<id>` under
   API-key auth → 401; under JWT → 201/200. Unbound `POST /api-keys/`
   response shape unchanged (regression guard for INV-4).
2. **Binding validation** — unknown/foreign `agent_id` → 404/403; revoked
   key → 409; bind → re-bind → unbind round-trip; label collision → 409.
3. **Attribution matrix** — Action facts for bound / unbound / JWT requests
   carry (`core_…`, `"agent"`) / (username, `"api_key"`) / (username,
   `"operator"`); ActionResult unchanged; envelope `actor` mirrors.
4. **Introspection** — `/auth/me` shapes per §7.3; no agent-field leakage
   on unbound keys.
5. **Invariants** — INV-1: no identity-row writes during authenticated
   data requests (assert entity/api_keys row counts before/after); INV-3:
   re-bind changes only future facts.
6. **Schema/migration** — `agent.schema.json` validation; migration
   nullability (pre-migration rows unaffected); `core_` prefix on new
   agent uuids.

---

## 14. Non-Goals & Future Work

- **Scopes/permissions per agent** — deferred (G6). Extension path anchors
  RFC-001 §4.2's scope model (`<resource>:<action>[:<specifier>]`) to the
  Agent entity: capability manifest in entity data or a scopes column,
  with §4.3-style time-limited grants.
- **Operator entities** — humans stay username-attributed in v1. When they
  land (same invariant: minted explicitly, e.g. at `/admin/register`), all
  actors become entity uuids and attribution is uniform.
- **Per-agent rate limits, agent self-registration, agent-to-agent auth** —
  self-registration contradicts G3 by definition; the others await a
  multi-agent scale that does not yet exist.
- **ApiKey entity bare-uuid cleanup** — cosmetic divergence (§4.3);
  separate migration if ever worth it.
- **`"system"` actor_type** — when in-server operations write Action
  facts, if ever (Open Question 2).

---

## Open Questions

1. **Label uniqueness enforcement depth** — endpoint-enforced uniqueness
   (planned) vs. a DB-level constraint. Entity data is a JSON column;
   a unique functional index is possible but likely not worth it for
   single-digit agent counts.
2. **`"system"` actor_type** — reserve the value in schemas now, or add
   when a writer appears? Default: add when needed; the enum is data, not
   wire format.
3. **Unbind** — §6.4 allows `{"agent_id": null}`. If audit continuity is
   judged more important than flexibility, unbind can be dropped (re-bind
   only). Default: allow both; revisit on first real use.

---

## References

- PRD `plan/memogarden_prd_v0_11_0.md` §Audit Trail, §Evidence Object
- RFC-001 v4 §4.1–4.4 — participant identity, scope model, grants
  surviving rotation, multi-operator posture (conceptual predecessor)
- RFC-005 v7.1 — Action Fact Schema (additively extended by this RFC);
  Semantic API envelope
- RFC-003 v4 — context/contributor model that agent handover assumes
- RFC-008 v1.2 — transaction philosophy this RFC's invariant style follows
- RFC-004 v2.2 §12.2 — provider registry (contrast case for §4.4)
- `docs/api.md` — agent auth model as documented (commit b8005ae)
- Implementation touchpoints: `memogarden-api/api/middleware/api_keys.py`,
  `memogarden-api/api/middleware/decorators.py`,
  `memogarden-api/api/handlers/decorators.py`,
  `memogarden-api/api/semantic.py`,
  `memogarden-system/system/schemas/sql/core.sql`

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-11-06 | Initial draft. Agent entity + key binding; explicit operator-gated issuance (INV-1–7); attribution pipeline with actor_type; /auth/me introspection; explicit-binding migration. Lazy backfill rejected (§2). |
