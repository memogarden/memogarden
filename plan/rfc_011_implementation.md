# RFC-011 Implementation Progress

**Tracks:** implementation state and remaining work for RFC-011 v1.1
(Scopes, Grants, and Attention-Gated Access) and its dependent amendments
(RFC-003 v5, RFC-005 v8, RFC-001 v5, RFC-010 v1.2, RFC-009 v1.1).

**Status 2026-11-10:** core mechanism **live** (merged to main in
memogarden-system `f651649` + memogarden-api `7faf180`; service deployed;
live core.db migrated to schema `20261109`; builtin scopes
`Triage / Untrusted` + `Personal` seeded; enforcement **off**).

**Status 2026-11-19:** builtin scope renamed `Private` → `Personal` (RFC-011 v1.3
erratum) with startup reference migration `migrate_private_scope_rename` (system
`feat/scope-personal-rename`); canonical schema declares Scope `visibility`
(public | private, default private — decisions in
`rfc_011_visibility_addendum.md`, implementation pending item 5). Deploy note executed:
schemas-v2 tagged on the root repo (2026-11-19) and the system repo's
`system/schemas/memogarden` submodule pin bumped to it (system `1f35192`) —
the served copy carries the attribute (no runtime consumer; no restart
needed).

## Done

| Slice | Commit | Suite |
|---|---|---|
| system: migration 20261109 (singular `active_scope`, `api_keys.grants`), `context.py` exclusive activation, `SCHEMA_VERSION_CHAIN` runner, `ensure_builtin_scopes()` | system `f651649` | 188 passed |
| api: v5 context verbs (grant-gated enter / atomic switch / focus alias), `/grants` CRUD (JWT-only, key + agent grants, envelope inheritance), `/auth/me` extension, `scope_access` module, enforcement flag (get/query/search filters, add auto-label, amend write-gate, SSE subscription gating) | api `7faf180` | 328 passed |
| Studio | verified no-op (reads none of the changed shapes) | — |
| Deploy | merge → restart → live migration rehearsed then applied; `/auth/me` serving grants + `active_scope` | — |
| docs/api.md | auth section, Scopes & Grants section, v5 verbs, grants endpoints | this commit |

## Gap inventory + priority order

Priority logic: **seal the label surface before labels exist** (governance
gaps are cheap now, expensive once a labeled corpus exists) → **provider
hook before the statement pipeline** → **close the §4.9 read-path
enumeration before agents traverse scoped material** → **SG-9 before
extraction** → **SSE payload routing before observation of scoped
material**.

### P0 — before any financial labeling happens

1. **Relabel governance** (§4.5, §4.7, SG-8) — **DONE** (api `feat/rfc011-p0`
   `88a3c97`, 336 passed, unmerged): amend diffs old/new `metadata.scopes`
   and requires a grant on every label added OR removed (D2 symmetric
   rule); denials carry the attempted change in Action-fact details and
   the before-state persists in the superseded fact (SG-8).
   No relabel verb exists; `amend` merges metadata, so passing
   `metadata.scopes` *is* a relabel — ungoverned in both directions:
   an unscoped key can widen an item into a scope (no target-grant
   check), an in-scope key can de-scope to global (no rule at all —
   see decision D2). Work: grant-check label changes on `amend`
   (distinguish inherited labels from changed ones), relabel-specific
   Action facts (actor, old labels, new labels), and the triage
   workflow this unlocks.
2. **Advisory-mode flag semantics** (§8 step 2) — **DONE** (same commit):
   enter grant-check and explicit-label checks are enforcement-gated
   (flag off = advisory, unchecked); create auto-labeling runs advisory
   too — the labeling corpus is correct before the flag flips.
3. **RFC-011 amendment** — **DONE** (RFC-011 → **v1.2**): D1 scope-minting
   operator-gated, D2 symmetric label grants, §4.9(5) resolved to
   suppress (open question 2 closed). Decisions ratified 2026-11-12.
   Implementation: scope-minting gate landed (api `88a3c97`); SSE
   suppress implementation remains in P2 item 7.

### P1 — before the statement pipeline goes live

4. **Provider `scope_policy` hook** (§4.8, SG-7) — **DONE, MERGED +
   DEPLOYED 2026-11-13** (system `a70d952` — migration 20261113,
   `providers.scope_policy`; api `0a36b79` — runtime application at the
   `_to_fact`/`create_fact` seam (children included; providers cannot
   forget), `PUT /providers/<name>/scope-policy` JWT-only, builtin
   aliases triage/personal — renamed from triage/private 2026-11-19,
   see the RFC-011 v1.3 erratum). Ratified design: rules-based, ordered
   matchers over merged data+metadata fields, first match wins, default
   catch-all → triage (fail-closed), null = unscoped, provider-set
   labels win. Live: schema migrated, gmail at NULL policy = the
   ratified default — the next inbound email lands in triage (current
   runs insert 0 facts; mailbox caught up, so live labeling first
   demonstrates on new mail). Backlog sub-choice remains operator's at
   rollout (retroactive default = whole backlog → triage; lean =
   backfill explicit rules only, finance senders → triage, unmatched
   legacy stays global).
5. **§4.9 read-path completion** — OPEN.
   - Relation traversal (`explore`) filtering with
     unresolved-reference semantics (§4.9(4)) — the statement flow
     will traverse `contained_by` (email → attachment).
   - Entity handlers get/query/edit (§4.9(2)) — an Artifact with
     `data.scopes` is currently decorated, not protected.
   - Search pre-match (§4.9(3)) — scoped items currently participate
     in matching before exclusion; the limit is applied before
     filtering so scoped hits displace real matches. Add the RFC-005
     v8 `scope` convenience param while in there.
   Facts-only enforcement is the interim safe rule (documents as
   Attachment facts are covered); close the rest before agents
   traverse.

### P2 — with extraction / observation

6. **SG-9 `derive_inherit_scope`** (config, default-on) — OPEN,
   deliberately sequenced: hard precondition for document extraction
   (the anti-side-channel default — extracted text must not bypass the
   source's scope). First commit of the extraction work.
7. **SSE payload routing by `metadata.scopes`** (§4.9(5)) — OPEN.
   Fact events currently route by conversation `scope_uuid` or as
   global; a scoped fact's payload can reach subscribers outside the
   scope. Subscription gating is done; item-payload routing is not.
   Decide the RFC open question here: reference-without-payload (RFC
   default) vs suppress — the payload leak argues for suppress.

### P3 — hygiene

8. **Scope-minting gating** (implements D1) — any key can currently
   mint Scope entities via `create`. Noise, not escalation, but
   ungoverned.
9. **mg_client docstrings + skill master copies** — pre-RFC-011
   vocabulary (`mg_client` context-verb models, project-studio spec
   §11.3's stale `project:read` capability-scope language).
10. **Binary ingestion** — belongs to the statement-ingestion plan,
    not this RFC; any future upload/serve endpoint enforces scopes
    from its first commit (§4.9(6)). Note: email-arrived PDFs are
    already in as bytes via the gmail attachments pass (SG-7 exempt);
    the missing path is non-email sources (portal downloads, scans).

## Policy decisions — RESOLVED 2026-11-12 (ratified; in RFC-011 v1.2)

- **D1 — scope-minting authority:** operator-gated (Semantic create
  rejects Scope from API keys; operator JWT mints via the same verb;
  RFC-010 invariant applied to boundaries). Landed in `88a3c97`.
- **D2 — de-scope rule:** symmetric grants — any label added or removed
  requires a grant on that label's scope. Landed in `88a3c97`.
- **SSE payloads (P2 rider):** suppress — non-members receive nothing
  for scoped-item events. Implementation remains in P2 item 7.

## Operational rollout (after P0)

1. Provision grants (`POST /grants`) — triage agents get `triage` +
   target scopes; analyst agents get their domains.
2. Set `MEMOGARDEN_SCOPE_ENFORCEMENT=1` in the `memogarden.service`
   unit environment; restart.
3. Provider policies on; statement ingestion builds on P1.

## References

- RFC-011 v1.1 + amendment riders; RFC-003 v5 §3.7 (INV-11a/11b/11c)
- Deploy record: system `f651649`, api `7faf180`, live since 2026-11-10
- Audit that produced this list: gmail-importer gap review 2026-11-10
  (relabel, provider hook, entity/relation enforcement, search
  pre-match, SSE payloads, SG-9, flag semantics, docs, minting,
  binaries)
