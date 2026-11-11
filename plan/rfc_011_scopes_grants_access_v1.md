# RFC-011: Scopes, Grants, and Attention-Gated Access

**Version:** 1.2
**Status:** Accepted
**Author:** MemoGarden Project
**Created:** 2026-11-09
**Depends on:** RFC-003 v5 (Context Mechanism), RFC-001 v5 §4 (Auth), RFC-010 v1.2 (Agent Identity)

## Abstract

MemoGarden unifies its security model with its attention model. A **Scope** — the
existing RFC-003 work container, from Greek *skopos*, "aim, object of attention" —
is an intention. Entering a scope is adopting an intention; leaving it is releasing
one. Access to restricted material follows attention: a principal can only read
items labeled with a scope it has explicitly entered, and it can only enter scopes
it holds a **Grant** for. Items without a scope label are global and accessible to
any authenticated principal. Scopes are restricted by default; activation is
exclusive (one scope at a time); providers are exempt from access gates and label
items at creation per operator policy. The RFC-001 §4.2 capability grammar is
retired from active use in favor of grants; the word "scope" is never again used
in MemoGarden to mean a permission.

This RFC exists to make ingested sensitive material (household/financial documents,
work correspondence) separable by context — work vs. personal, financial vs. social
— *before* such ingestion goes live, without introducing hierarchy, field-level
ACLs, or encryption claims foreign to the platform's availability-first posture
(RFC-001 §6).

## 1. Motivation

### 1.1 Problem Statement

1. **Sensitive ingestion is imminent.** Statement PDFs, bills, payslips, and
   account summaries are already accumulating as Attachment facts via the Gmail
   importer. Once document extraction lands, their contents become queryable by
   every key-holder. There is currently no mechanism to keep a work-assistant
   agent out of financial material, or a household agent out of work material.
2. **Authority is all-or-nothing.** Every API key carries the full authority of
   the owning user (`docs/api.md`: "there are no per-key scopes"). RFC-010 gave
   agents *identity* but explicitly not *authorization* (G6).
3. **Agents leak context across boundaries.** Agents crossing project boundaries
   routinely mix project goals, especially under degraded context coherence. A
   mechanism that forces intention to be declared — and that structurally limits
   what an in-context agent can fetch — addresses the attention problem and the
   access problem with one mechanism.

### 1.2 Current State

- The Scope entity, ContextFrames, and `enter`/`leave`/`focus` verbs are
  **implemented** (RFC-003; `api/handlers/core.py`; `context_frame.active_scopes`,
  `primary_scope`). They carry *no* access semantics: any participant may enter
  any scope.
- RFC-001 §4.1–4.3 designs key fields (`baseline_scopes`, `allowable_scopes`) and
  a capability grammar; **none of it is implemented** (api_keys has no scope
  columns; no enforcement code exists anywhere).
- No sensitivity, classification, or visibility concept exists on any fact or
  entity.
- The noun "scope" is already used in four senses across the corpus (§3); the
  authorization sense is the least used and entirely unimplemented.

### 1.3 Design Goals

1. One concept, two faces: attention and access are the same mechanism.
2. Separation of contexts, not confidentiality from the host (RFC-001 §6 posture
   unchanged: availability over confidentiality).
3. No hierarchy: scopes are peers; there are no levels, only sets.
4. Default-open global space: unlabeled items stay freely accessible; scoping
   only ever removes access for principals outside the entered scope.
5. Retrofittable: existing scopes, keys, and consumers continue to function;
   enforcement activates only after the operator provisions grants.
6. Agent-first: humans and agents obey the same rules through the same API.

## 2. Core Principle

> *Skopos* means both "the aim" and "the watcher's object of attention." In
> MemoGarden these were never separable: **to attend is to intend; to access is
> to attend.**

Security in MemoGarden is **intention discipline**. A principal working inside a
scope declares that intention by entering the scope; the system then honors the
intention in both directions — it feeds the principal that scope's material, and
it withholds everything else. A grant is the operator's standing trust that a
principal may adopt a given intention. Enforcement is the refusal to let any
principal read what it has not declared an intention — and been trusted — to read.

## 3. Vocabulary and Disambiguation

"Scope" appears in four senses across the historical corpus. This RFC fixes the
terms permanently:

| Sense | Term | Ruling |
|---|---|---|
| Work container / attention context (RFC-003) | **Scope** | The only unqualified use of "scope". |
| Authorization capability (RFC-001 §4.2) | **Grant** | Retired from "scope". RFC-001 §4.3's existing vocabulary promoted to the name of the concept. |
| OAuth/protocol token scope | **OAuth scope** | Always qualified. External term; not ours to rename. |
| Generic English ("out of scope", "transaction scope") | as-is | Harmless; never a defined term. |

New terms defined by this RFC:

- **Scope** — an RFC-003 Scope entity; an intention container. Unchanged as an
  entity; acquires access semantics per §4.1.
- **Grant** — a standing, operator-issued permission binding a principal (API
  key) to a set of scopes it may enter. Etymologically sound: Latin *credere*,
  to trust. May be time-limited (RFC-001 §4.3).
- **Active scope** — the single scope a principal is currently inside
  (ContextFrame state). Exclusive (§4.4).
- **Enter / leave** — adoption / release of an intention. Security-relevant
  transitions (§4.4).
- **Scope label** — the authoritative per-item record of which scopes an item
  belongs to, stored in item metadata (§4.2).
- **Unscoped** — *not a scope*. The unlabeled state: global space, accessible to
  every authenticated principal.
- **Restricted scope** — every scope, by default (§4.1). Non-restricted scopes
  are a future extension (§10).
- **Principal** — an authenticated API key (with its bound Agent, per RFC-010).
  **Provider** — an ingestion process; *not* a principal (§4.8).

Cleanup riders (folded into affected-RFC amendments, §11): the Agent entity's
`allowed_scopes` field (declared, never read or written by code) is repurposed as
the truthful per-agent grant manifest it already sounds like; the undocumented
"Lives in System scope" phrase in entity schema descriptions is rewritten.

## 4. Model

### 4.1 Scopes Are Restricted by Default

Every scope is an access boundary. Entering any scope requires a grant (§4.3);
accessing items labeled with a scope requires being inside that scope (§4.5).
There are no non-restricted scopes in v1; if a use case emerges for
attention-only scopes, they may be added later as an explicit per-scope property.

Existing RFC-003 scopes (e.g., Project Studio project scopes) become restricted
when enforcement activates. This is intentional: a scope an agent works in is
precisely a context it should be granted into, and operator provisioning of
grants is a one-time, explicit act (§8).

**Provisioned by default:** the operator's fresh install ships with two builtin
scopes, both ordinary restricted scopes:

- `triage` (label: "Triage / Untrusted") — where ingested items land pending
  sorting, and where sorting work happens.
- `private` — operator-only material. Provisioned with no key grants; only the
  operator may enter.

"Unscoped" is not provisioned because it is not a scope — it is the absence of a
label (§4.2).

### 4.2 Scope Labeling of Items

- The authoritative record of an item's scopes is the item's **metadata**:
  `metadata.scopes` — a JSON array of Scope UUIDs. An item may belong to multiple
  scopes (e.g., a joint account statement labeled into both partners' scopes).
  Empty/absent array ⇒ unscoped ⇒ global.
- **Soil facts:** metadata sits outside `integrity_hash` (`system/soil/fact.py`
  hashes `data` only), so triage relabeling does not violate the immutability
  model and does not require supersede chains. Item content stays immutable; its
  scope is mutable governance state. Scope changes are themselves audited (SG-8).
- **Core entities:** scope is part of entity data (entities are mutable by
  design); EntityDelta facts already record changes.
- **Binaries (Attachment facts):** scope lives on the Attachment *fact*, never on
  the file. Files on disk remain governed by RFC-001 §6 posture (host trust);
  any future API endpoint that serves bytes MUST enforce scope gates from its
  first commit.
- Indexing (expression index on the JSON field, or a rebuilt lookup structure)
  is permitted for performance and is **never authoritative** — rebuildable from
  metadata at any time, matching the platform's non-authoritative-index pattern.

### 4.3 Grants

A grant is recorded on the API key record — the credential presented at request
time is the single source of enforcement truth for v1:

```
api_keys.grants  JSON  -- [{scope_uuid, granted_at, expires_at|null}, ...]
```

- Grants are **operator-issued only**, via JWT-gated endpoints (§6), honoring the
  RFC-010 user-set invariant: identity and authority changes are never implicit
  in the auth hot path. Request-time validation reads the stored grants; it
  never mints them.
- Grants may be time-limited (`expires_at`), per RFC-001 §4.3. Expired grants
  fail at next `enter`; an in-flight session is not retroactively ejected (§7).
- The **Agent entity** mirrors the envelope for introspection: when a key binds
  to an agent (RFC-010), `agent.data.allowed_scopes` documents the agent's
  intended grant envelope. Enforcement reads the key, never the entity; the
  mirror is descriptive. Operators managing an agent's access manage its keys'
  grants.
- **Operator keys** (human, JWT) hold implicit all-grants and need no records.
- A key with no grants is *unscoped-only*: it may read and create unscoped items
  and cannot enter any scope. This is the default state of every existing key
  before provisioning (§8) — the retrofit path.

### 4.4 Activation: Enter and Leave

Amendment to RFC-003 §3.7 / §4.1:

- **Exclusive activation.** A principal is in at most one scope at any moment.
  The ContextFrame's `active_scopes` array and `primary_scope` are replaced by a
  single `active_scope` (transition plan in §11).
- **`enter` requires a grant** on the requesting key for the target scope.
  Denied enters are logged (SG-8).
- **`enter` while already inside a scope is an atomic switch**: implicit
  `leave` of the current scope (logged) followed by `enter` of the target
  (logged as one `scope_switch` action). Rationale: preserves the exclusivity
  invariant in one call, keeps the audit trail complete. (Alternative rejected
  for v1: rejecting with "leave first" — two calls for the common case, and the
  invariant is identical.)
- **`leave`** clears the active scope; the principal falls back to unscoped-only
  operation.
- **`focus` is retired.** With one active scope, primary *is* active. The
  endpoint is kept as a deprecated no-op alias through the migration window
  (§11).
- **Enter must mean something.** Enter is not an access-formality: agent runtimes
  (mars, MUSH, Studio sessions) SHOULD, on enter, load the scope's charter —
  goals, standing context, conventions — into working context and release the
  prior scope's material. The API enforces what may be fetched; the runtime makes
  the intention real. RFC-003 line 209 already observed that explicit transitions
  "enable easier audit of scope transitions"; under this RFC that audit is
  security-relevant.

### 4.5 The Access Predicate

For a principal *P* requesting an item *I*:

```
I unscoped            → allowed (any authenticated principal)
I has scopes S        → allowed iff P.active_scope ∈ S
```

- The grant is checked at `enter` time; holding an active scope implies holding
  its grant. Enforcement at read time therefore checks membership only.
- Reads: fact/entity GET, query/list, **search (filtered before matching — a
  filename hit is a leak)**, relation traversal responses that embed item data,
  and event streams (§4.9).
- Writes (edit, supersede, tombstone, relation edits on scoped items): same
  predicate — be inside one of the item's scopes.
- **Relabeling** — symmetric grants (v1.2, D2): any label **added or removed**
  by a non-sovereign principal requires a grant on that label's scope (operator
  JWT bypasses, SG-11). Adding the Finances label and removing it are the same
  privilege — you must hold the compartment to re-file something in or out of
  it. Rationale: removal is the exposure-widening direction (a de-scoped item
  falls back to global), and one symmetric rule keeps a single place to reason
  (§4.7).
- There is no read-only/write-only grant distinction in v1. A grant to enter a
  scope is full access within it. Simplicity is deliberate (§10).

### 4.6 Write Paths and Labeling at Creation

- **Inheritance default:** an item created by a principal inside scope *S* is
  labeled *S* unless the creator explicitly labels otherwise. Items created
  while unscoped are unscoped. Scope "applied upon creation" (per the ratified
  proposal) falls out of session state — no separate act.
- **Derived facts inherit.** A fact derived from a scoped source (e.g., text
  extracted from a Finances statement PDF) inherits the source's scope labels by
  default, via a config option (`derive_inherit_scope = true` default-on).
  Without this, extraction is a plaintext side-channel around the entire model.
  Config-off is the operator's escape hatch, not the default.
- Scope "inheritance" beyond these two rules is left to the user and to simple
  config options, per the ratified proposal. No relation type propagates labels
  implicitly in v1.

### 4.7 Relabeling and Triage

- Triage is a scope like any other. The triage workflow: hold a grant on
  `triage` (and on target scopes), enter `triage`, relabel items into their
  final scopes, leave. Under §4.4's exclusivity, the sorting agent works in one
  intention at a time; sorting material into Finances requires the Finances
  grant, which the operator bestows knowingly.
- Every relabel is logged with actor, old labels, new labels, and result (SG-8).
  Downgrades (restricting) and upgrades (widening) are equally visible in the
  audit trail. Under v1.2's symmetric rule (D2), both directions are also
  grant-gated: removing a scope's label requires holding that scope's grant,
  because de-scoping is the exposure-widening direction.
- Items arriving before enforcement activates, or from providers under
  `unscoped` policy, sit in global space until triaged. This window is
  acknowledged and accepted (§9); providers may close it at the source (§4.8).

### 4.8 Providers

Providers (gmail-importer, mbox-importer, tada-importer, future statement
ingesters) are **not principals**. They hold no grants, never enter scopes, and
are never scope-gated: they are the ingestion machinery of the operator's own
system.

- Providers **label at creation** per a provider-level `scope_policy` set by the
  operator:
  - `unscoped` — default; items land in global space;
  - `triage` — all items land in the Triage scope;
  - `rules` — provider-defined matchers assign initial scopes (e.g., gmail:
    sender/doc-class → scope). Matcher configuration is provider-specific and
    operator-configured; the provider framework supplies the policy hook.
- Providers write labels; they do not need read access to scoped material to do
  so. The labeling act is governed by operator policy, not by grants.
- Provider runs remain audited via `provider_runs` and Action facts, attributed
  per RFC-010 where applicable.

### 4.9 Enforcement Points

All of the following enforce §4.5 server-side, by default, without opt-in:

1. Soil fact GET / query / list handlers.
2. Core entity handlers.
3. **Search** — scoped items are excluded *before* matching, on every coverage
   level and strategy. A convenience `scope` filter parameter may exist, but
   enforcement never depends on it being passed.
4. Relation traversal: responses embedding scoped item data are filtered; a
   relation to an inaccessible item is presented as an unresolved reference, not
   omitted silently (distinguishable from a dangling relation only in that the
   count is reported truthfully).
5. Event streams (SSE observe mode, RFC-009): scoped item payloads are
   delivered only to principals inside the scope. **v1.2 (rider): suppress**
   — non-members receive nothing for scoped-item events, not even the
   reference; the reference itself reveals that a scoped item exists and
   changed. Global (unscoped) events flow to all subscribers as before.
6. Any future binary/file-serving endpoint: scope gates from its first commit.

**Accepted metadata leak:** an unscoped parent item (e.g., an email) still lists
`attachment_filenames` of scoped children. Filenames can reveal context
("OCBC_Statement_Nov2026.pdf"). Consistent with the ratified no-fine-grained-
scoping stance, this is accepted; the mitigation is at the source — provider
rules may scope the parent email itself when its sender/doc-class is sensitive.

### 4.10 Operator Sovereignty

The operator's human keys (JWT) hold implicit all-grants and are never
scope-gated. MemoGarden is a personal system; the operator can always see their
own data, including everything in every scope and the global space. Enforcement
partitions what *keys and agents* reach, never what the operator reaches.

## 5. Behavioral Invariants

- **SG-1 — Global default.** Unscoped items are readable and writable by every
  authenticated principal. Absent a label, nothing changes from today.
- **SG-2 — Restricted by default.** Every scope requires a grant to enter.
  There are no non-restricted scopes in v1.
- **SG-3 — Exclusive activation.** A principal holds at most one active scope.
  Enter-while-active is an atomic, logged switch.
- **SG-4 — Grants gate entry.** `enter` without a grant fails and is logged.
  Grants are operator-issued, JWT-gated, never minted at auth time.
- **SG-5 — Access follows activation.** Scoped items are accessible iff the
  requester's active scope is one of the item's labels. Grant possession without
  activation authorizes nothing.
- **SG-6 — Metadata is authoritative.** `metadata.scopes` is the truth; indexes
  are rebuildable caches. Relabeling mutates metadata only and is audited.
- **SG-7 — Providers are exempt, and label at creation.** Provider writes are
  never scope-gated; their labels come from operator policy.
- **SG-8 — Everything is audited.** Enter, leave, switch, denied enter, denied
  read, relabel: each produces an Action fact with actor, targets, and result
  (RFC-001 §11.5, RFC-010 attribution). Audit facts record *references*
  (UUIDs, operations), never scoped item content.
- **SG-9 — Derived inherits.** Derived facts inherit source scope labels unless
  config-disabled. Extraction cannot become a side-channel by default.
- **SG-10 — Enforcement is server-side, total, and silent-free.** Every read
  path filters; clients cannot opt out; filtered results are presented
  truthfully (unresolved-reference semantics, §4.9), never as invisible absence.
- **SG-11 — The operator sees everything.** Operator keys bypass scope gates,
  always.

## 6. Issuance & Management Protocol

All endpoints JWT-gated (operator only), consistent with RFC-010's issuance
model. API keys cannot mint, modify, or revoke grants — including their own.

**Scope creation is operator-gated too (v1.2, D1).** Scopes are security
boundaries; RFC-010's invariant applies: boundary issuance is explicit and
operator-gated, never a side effect of ordinary writes. The Semantic
create verb rejects `_type: "Scope"` from API keys (mirroring the
identity-type rejection); operator JWT sessions mint scopes through the
same verb under sovereignty. Builtin scopes are seeded at startup (§4.1).

- `POST /grants` — `{key_id | agent_id, scope_uuid, expires_at?}`. For
  `agent_id`, applies to all keys currently bound to that agent and is recorded
  as the agent's envelope (§4.3); future bindings inherit the envelope at bind
  time. (Alternative considered — per-key-only management — rejected: an agent
  with several keys would need N identical grants, and the operator thinks in
  agents.)
- `DELETE /grants/{grant_id}` — revoke immediately; next gated operation fails.
- `GET /grants?key_id=|agent_id=` — enumerate.
- `GET /auth/me` — extended to report the key's grants, the bound agent's
  envelope, and the current active scope (RFC-010 §7.3 pattern).
- Enter/leave/switch: existing RFC-003 context verbs, amended per §4.4; every
  transition emits an Action fact.

## 7. Failure Modes & Edge Cases

- **Expired grant mid-session.** Expiry is checked at `enter`/`switch`; an
  active session is not ejected retroactively. Leave-and-reenter fails. Bounded
  exposure window; acceptable for a personal system, documented here.
- **Revoked grant mid-session.** Same as expiry; operators who need immediate
  ejection revoke the *key* (existing RFC-010 machinery), which is immediate.
- **Key with grants used by a runtime that never enters.** The principal operates
  unscoped-only. Grants without activation authorize nothing (SG-5) — this is
  the designed failure-safe direction.
- **Item relabeled while a principal is inside.** Next request re-evaluates the
  predicate; if the principal's active scope no longer matches, access fails
  truthfully (SG-10).
- **Deleted scope.** Items labeled with a deleted scope UUID are treated as
  unscoped for any principal unable to match it; operator cleanup relabels.
  Scope deletion is operator-only and warns on labeled items.
- **Subagents (RFC-003 fork/merge).** A subagent forks within its parent's
  active scope and inherits exclusivity naturally: one scope per principal
  already. Subagent grant checks occur if it re-enters independently.
- **Concurrent enter on shared principal.** ContextFrames are per-principal
  state; concurrent sessions on one key race on `active_scope` (RFC-003 §3.14
  concurrent access). v1: last-write-wins with full audit; documented, not
  policed.

## 8. Migration & Rollout

1. **Schema:** `api_keys.grants` column (JSON, default `[]`); `context_frame`
   migration to singular `active_scope` (fold `primary_scope`/`active_scopes`
   during the window; see §11). Provision `triage` and `private` builtin scopes
   idempotently.
2. **Enforcement flag:** config `scope_enforcement = off | on`, default `off`.
   With the flag off, enter/leave behave as today (advisory), labels may be
   written and grants provisioned, nothing is filtered.
3. **Provisioning:** operator grants scopes to existing keys (Studio keys, agent
   keys, importer-era keys) via §6 endpoints; agents' `allowed_scopes` envelopes
   documented.
4. **Activation:** operator flips `scope_enforcement = on` for the deployment.
   From this moment SG-1..SG-11 hold. Existing unscoped workflows — the entire
   current corpus, tada, mbox, default-gmail — are unaffected (all unscoped).
5. **Provider policies:** applied per provider when operators are ready
   (e.g., gmail `rules` routing finance senders to `triage`).

Steps 2–5 are independently revertible; none requires data migration backward.

## 9. Security Analysis

**What this protects.** Context separation between key-holders: a compromised or
over-scoped agent key's blast radius is its grants plus the global space — never
the operator's full corpus. Cross-intention leakage by agents is structurally
limited: material outside the declared intention is not fetchable. The audit
trail answers "what did this agent attend to, and what did it try to reach?"

**What this does not protect.** Host-level access remains out of scope, exactly
as RFC-001 §6.1 ruled (availability over confidentiality; FDE is the boundary).
Unscoped items — today's entire corpus — remain visible to every principal until
labeled; the pre-triage window is real, and provider `rules`/`triage` policies
are the primary mitigation. Unscoped-parent metadata leaks (§4.9) are accepted.
Grants live beside other key state in the same database as everything else; no
new confidentiality claims are made. The enforcement flag (§8) means a
deployment may run unsecured if the operator never activates — by design; this
is a personal system and the operator's sovereignty (SG-11) dominates.

**Naming risk.** The unified use of "scope" (attention *and* boundary) is
deliberate and stated here: a restricted scope is not a second kind of thing —
it is the same intention container, with grants watching its door. Documentation
must repeat this sentence rather than inventing synonyms that re-fragment the
concept (the "compartment" proposal was considered and retired for exactly this
reason).

## 10. Non-Goals & Future Work

- **No hierarchy.** No public/secret levels, no ordering, no inheritance chains
  between scopes. Peers and sets only.
- **No field-level or partial scoping.** Contact-card fields, mixed-content
  documents: out of scope by design. Hybrid cases are handled by message-passing
  with appropriate redaction — manually or by an agent today; a future
  MemoGarden app may make this programmatic.
- **No non-restricted scopes in v1.** Deferred until a use case is clear.
- **No cross-operator changes.** RFC-001 §4.4/§12.1 operator isolation is
  orthogonal (database partitioning) and untouched.
- **No encryption claims.** Nothing here encrypts anything (RFC-001 §6).
- **No automatic context coercion in the API.** Charter-loading on enter is
  runtime responsibility (mars/MUSH/Studio); the API audits transitions and
  gates fetches, it does not rewrite agent context.
- **OAuth scope naming stays.** Google's term; we qualify and move on.

## 11. Amendments (Executed)

Ratified 2026-11-09. The following amendments were executed in the same pass;
each amended document is the specification of record for its area — this section
is the change log:

1. **RFC-003 → v5** (`rfc_003_context_mechanism_v5.md`) — §3.7 INV-11 rewritten:
   exclusive activation (INV-11a, atomic `scope_switch`), grant-gated entry
   (INV-11b, audited denials), implied initial activation (INV-11c); `focus`
   verb retired (deprecated alias); INV-2 append = user + the active scope;
   §4 verb definitions and internal methods on singular `active_scope`; new
   §6.4 integration section; terminology updated.
2. **RFC-005 → v8** (`rfc_005_memogarden_api_design_v8.md`) — context verbs
   amended (grant-gated `enter` with atomic switch, `leave` → `active_scope`,
   `focus` deprecated); search scope enforcement pre-match + optional
   convenience `scope` field; Grants Management endpoints (`/grants`,
   operator JWT-gated) + `/auth/me` extension; Open Question 8 resolved.
3. **RFC-001 → v5** (`rfc_001_security_operations_v5.md`) — §4.1 key record:
   `baseline_scopes`/`allowable_scopes` replaced by `grants`;
   `participant_id` anchored to RFC-010 Agent entities; §4.2 grammar retained
   as reserved headroom; §4.3 points to RFC-011 as specification of record.
4. **RFC-010 → v1.2** — G6 deferral consumed (authorization = RFC-011);
   `agent.data.allowed_scopes` documented as the grant-envelope mirror.
5. **RFC-009 → v1.1** — scope-filtering note on notification events and
   observe streams (scoped content reaches only principals inside the scope).
6. **Schemas** — `agent.schema.json` (grant-envelope wording for
   `allowed_scopes`; "Lives in System scope" phrasing removed),
   `operator.schema.json` (same), `scope.schema.json` root + bundled copies
   (access-boundary description, `metadata.scopes` pointer).
7. **Budget PRD** — statement-ingestion agent workflows list scope grants &
   triage as a prerequisite.

Deferred to implementation (deliberately *not* spec amendments): the
`api_keys.grants` column and `context_frame.active_scope` migrations (§8
rollout step 1), and `docs/api.md` grants/scope sections (documented when
implemented, per the RFC-010 precedent).

## Open Questions

1. **Scope charter format.** What a runtime loads on enter (a Scope entity data
   field? referenced Artifact?) is deliberately unspecified here; to be settled
   with mars/MUSH runtime work.
2. ~~**SSE granularity.**~~ — **RESOLVED v1.2 (rider): suppress.** Non-members
   receive nothing for scoped-item events. The gap audit found fact payloads
   routing by conversation scope rather than item labels; existence itself is
   a leak, so the liveness argument loses.
3. **Slug identifiers.** Grants and policies reference Scope UUIDs; human-facing
   slugs (e.g., `finances`) can be added to the Scope entity later without
   breaking anything. Deferred.

## References

- RFC-001 v4 §4 (Auth model), §6 (Posture), §11.5 (Audit Trail)
- RFC-003 v4 (Context Mechanism — Scope entity, ContextFrame, enter/leave/focus)
- RFC-005 v7.1 (Semantic API — context verbs; Open Question: auth)
- RFC-010 v1.1 (Agent Identity — G6, key binding, issuance invariant)
- Budget PRD §"Document-Centric Traceability", §"Agent-Assisted Workflows"
- Etymology: *scope* < Gk. *skopos* "aim, object of attention"; *grant* < OFr.
  *graanter* < Lat. *credere* "to believe, to trust"

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-11-09 | Initial draft. Unifies RFC-003 scopes with access control via grants and exclusive activation; retires "scope" as a permission term; defines provider labeling, triage workflow, enforcement points, rollout. |
| 1.1 | 2026-11-09 | Ratified. §11 amendments executed: RFC-003 → v5, RFC-005 → v8, RFC-001 → v5, RFC-010 → v1.2, RFC-009 → v1.1, entity schema descriptions, Budget PRD prerequisite note; §11 converted to executed-amendment log. |
| 1.2 | 2026-11-10 | **Gap-audit amendment.** D1: scope creation is operator-gated (Semantic create rejects Scope from API keys; RFC-010 invariant applied to boundaries). D2: symmetric label grants — removing a label requires the same grant as adding it (de-scope is the exposure-widening direction). Rider: §4.9(5) resolved to suppress — non-members receive nothing for scoped-item events. Open question 2 closed. |

---

**Status:** Accepted (2026-11-09). §11 amendments executed across the spec
corpus. Implementation follows §8 rollout order.

**END OF RFC**
