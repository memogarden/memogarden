# RFC-011 Implementation Progress

**Tracks:** implementation state and remaining work for RFC-011
(Scopes, Grants, and Attention-Gated Access; current v1.3 + visibility
addendum) and its dependent amendments (RFC-003 v5, RFC-005 v8,
RFC-001 v5, RFC-010 v1.2, RFC-009 v1.1). This file is the single status
tracker for RFC-011 work: dated status blocks live here so the RFC body
itself can stay timeless.

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

**Status 2026-11-20:** per-account `scope_policy` override (P1 item 4)
landed + deployed — system `e33c51d` + `7cdc661`, api `d034278`, client
`4844742`; first live use: gmail/nyjc → public **NYJC** scope, ~21k items
triage-stamped and backfilled. **Enforcement still OFF**
(`MEMOGARDEN_SCOPE_ENFORCEMENT` unset).

**Status 2026-11-21:** tracker review. `88a3c97` confirmed **merged to api
main** (2026-11-12) — stale "unmerged"/"ungoverned" notes corrected below
(P0 item 1, P3 item 8). P2 item 7 decision closed: the ratified SSE
**suppress** stands; build queued (see item 7). Enforcement remains the
headline open operator step (§Operational rollout).

**Status 2026-11-26:** the last two inventoried code items are built on
api `f409e48` (merged as `59f7876`, deployed 2026-11-26 13:47 +08) — P2
item 6 (SG-9, fact lanes) and item 7 (SSE suppress). Enforcement remains
**OFF**. The flip is not yet decisions-only: the engineering remainders
and the open exposure are listed under §Operational rollout.

## Done

| Slice | Commit | Suite |
|---|---|---|
| system: migration 20261109 (singular `active_scope`, `api_keys.grants`), `context.py` exclusive activation, `SCHEMA_VERSION_CHAIN` runner, `ensure_builtin_scopes()` | system `f651649` | 188 passed |
| api: v5 context verbs (grant-gated enter / atomic switch / focus alias), `/grants` CRUD (JWT-only, key + agent grants, envelope inheritance), `/auth/me` extension, `scope_access` module, enforcement flag (get/query/search filters, add auto-label, amend write-gate, SSE subscription gating) | api `7faf180` | 328 passed |
| Studio | verified no-op (reads none of the changed shapes) | — |
| Deploy | merge → restart → live migration rehearsed then applied; `/auth/me` serving grants + `active_scope` | — |
| docs/api.md | auth section, Scopes & Grants section, v5 verbs, grants endpoints | this commit |
| visibility addendum §5 UI surfacing (§6 item 6): scopes overview exposes `visibility`; console scopes screen gains badge + warn-on-flip control + mint-form knob; CLI `register scope --visibility` + `edit scope` (api `ed8b88b`, client `bb14583`; also: console mobile pass — nav rail, header compaction, table pruning; `/admin/register` bootstrap fix) | api `ed8b88b`, client `bb14583` | 480 + 125 passed |
| P2 items 6 + 7: SG-9 derived-source inheritance (both fact-creation lanes) and the §4.9(5) SSE suppress; `enter`/`leave` publish after commit; fixes a latent `NameError` on a full SSE queue | api `f409e48` (merge `59f7876`) | 587 passed |

## Gap inventory + priority order

Priority logic: **seal the label surface before labels exist** (governance
gaps are cheap now, expensive once a labeled corpus exists) → **provider
hook before the statement pipeline** → **close the §4.9 read-path
enumeration before agents traverse scoped material** → **SG-9 before
extraction** → **SSE payload routing before observation of scoped
material**.

### P0 — before any financial labeling happens

1. **Relabel governance** (§4.5, §4.7, SG-8) — **DONE, MERGED to main** (api
   `feat/rfc011-p0` `88a3c97` 2026-11-12, 336 passed): amend diffs old/new `metadata.scopes`
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
   suppress built 2026-11-26 (P2 item 7).

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
   legacy stays global). **Per-account override ADDED 2026-11-20**
   (system `e33c51d` + `7cdc661` — migration 20261120
   `provider_accounts.scope_policy` + schemas pin `9f7c838`; api
   `d034278` — account-PATCH field (JWT-only when present), runtime
   account-over-provider resolution (whole-replacement, RFC-012
   config-override pattern; runs are per-account so it resolves once
   per run), scopes-overview `account_policies`; client `4844742` —
   `edit account --default-scope/--clear-scope-policy`; console
   pickers on Providers + override table on Scopes; suites 222/485/128).
   First live use: gmail/nyjc → NYJC scope (user minted + set via
   console 2026-11-20, NYJC flipped public), ~21k triage-stamped nyjc
   items backfilled via amend loop (agent-attributed Action facts).
5. **§4.9 read-path completion** — **DONE, MERGED 2026-11-19** (system
   `e1dcd6b` + `bbaa3dc`, api `5c635c0`; suites system 222 green —
   first fully-green run since E3, api 478 green rebased on the
   metadata-filters merge).
   - Entity reads enforce §4.5 (get 403 `scope_read_denied`, query
     visibility-filtered, SG-10) — §4.9(2).
   - Entity writes enforce like amend: edit strict frame gate + D2
     symmetric label grants on `data.scopes`; forget write-gated.
   - Search PRE-MATCH (§4.9(3)): system `search_items`/`entity.search`
     gain `allowed_scopes` (None=unrestricted, []=unlabeled-only per
     SG-1) + `scope_uuid` (RFC-005 v8 convenience param); the old
     post-fetch search filter is retired — scoped hits can no longer
     displace real matches.
   - Explore (§4.9(4)): anchor gates like a read; inaccessible nodes
     are UNRESOLVED REFERENCES (uuid+layer stub, no type; truthful
     count); EDGES STAY FULLY LISTED — the addendum's open item 5
     ("edge visible iff both endpoints") settled the other way per
     containerizing-not-hiding (edges carry uuid/kind/endpoint-type
     only, no item data).
   - Visibility wired into reads only: public-visibility labels relax
     read gates from any frame (most permissive wins); write gates
     stay strictly frame-gated (visibility addendum).
   - Review found + fixed P0: explore node checks initially ran
     unconditionally (would have stubbed labeled nodes flag-off and
     for sovereign JWT — SG-11); regression pins added.
   - NEW tracked follow-up: `handle_track` remains ungated
     (reference-level disclosure only: uuid+type) — candidate for a
     §4.9(2) completion pass.

### P2 — with extraction / observation

6. **SG-9 `derive_inherit_scope`** (config, default-on) — **BUILT
   2026-11-26** (api `f409e48`): an UNLABELED
   fact that declares `metadata.derived_from` — the source Soil fact's
   uuid, bare or `soil_`-prefixed — copies that source's *current* labels
   at creation, following a superseded reference to its live version (a
   relabel is a new fact version). Switch: `MEMOGARDEN_DERIVE_INHERIT_SCOPE`,
   default on; `0/false/no/off` is the operator's escape hatch. Runs with
   the enforcement flag off too, like create auto-labeling (P0 item 2).
   Two fact-creation lanes. The **provider lane** copies verbatim
   (providers are not principals, SG-7), ordered provider-set labels →
   derived source → parent rule → scope policy. The source's labels are
   inherited whatever they are, the triage scope included: a document
   still waiting in triage is restricted to triage members, and text
   extracted from it must not reach anyone else — not even through a
   covering email that was already filed elsewhere. (An earlier draft of
   this item let a triage-labeled source fall through to a decided
   parent; that was reversed 2026-11-26 as a widening of the source's
   audience.) The **API lane**
   (`handle_add`) is explicit labels → derived source → creator's active
   scope, and under enforcement a non-sovereign requester keeps only the
   labels it holds a live grant for (D2's rule applied to inherited labels),
   so inheritance can never file a fact into a scope it cannot enter. When
   no label survives — switch off, nothing named, source unlabeled,
   missing or a Core entity — the fact takes the ordinary
   creation default. A derived fact does not follow later relabels of its
   source (a creation-time snapshot; the statement register's D8). Dormant
   on deploy: nothing emits the marker today. **Not covered:** entity
   creation lanes and Core-entity sources — the issue title's "for
   entities" (api#61) is not met; RFC §4.6 speaks of facts. The RFC names
   the rule, not the marker: `metadata.derived_from` is the
   implementation's choice, to be ratified with the statement pipeline.
   Tests: `tests/test_derive_inherit_scope.py` (19).
7. **SSE payload routing by `metadata.scopes`** (§4.9(5)) — decision
   CLOSED 2026-11-21: implement the ratified **suppress** (non-members
   receive nothing for scoped-item events). **BUILT 2026-11-26** (api
   `f409e48`), enforcement-gated: with the flag
   off, delivery is unchanged. Every publisher passes the labels of the
   items its event is about, as they stand *after* its write (a fresh read
   could land before the write commits); each non-sovereign connection is
   judged per publish against its live active scope plus the public
   short-circuit, and one unreadable item suppresses the whole event.
   Covered: `message_sent` and `artifact_delta` (both carry content),
   `relation_created` / `relation_modified` (both endpoints — read before
   the delete; stricter than §4.9(4)'s "edges stay fully listed", which is
   the safe direction), `context_updated` (containers), `frame_updated`
   (head item), `scope_created` / `scope_modified`. `enter` and `leave`
   publish **after** their transaction commits, so a principal's own
   stream is judged on the frame it just entered or left. A visibility
   lookup that raises fails closed (non-sovereign listeners suppressed)
   and never fails the write that published; an event published without
   labels under enforcement is logged as a warning. Relabels (visibility
   addendum §6 item 2): no event carries an item's OLD reference or labels
   (a Soil relabel is an `amend`, which publishes nothing; entity `edit`
   publishes only for Scope entities), so post-write labels close the
   public→private case. Same change fixed a latent `NameError` on a full
   SSE queue, which used to fail the writer's request and roll back its
   write. Tests: `TestScopeSuppress` + `TestBackpressure` in
   `tests/test_sse.py` (11).

### P3 — hygiene

8. **Scope-minting gating** (implements D1) — **DONE** (api `88a3c97`,
   merged 2026-11-12): Semantic `create` rejects Scope entities from API
   keys; operator JWT mints via the same verb (RFC-010 invariant applied
   to the boundary).
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
  for scoped-item events. Implemented 2026-11-26 (P2 item 7).

## Operational rollout (after P0)

1. Provision grants (`POST /grants`) — triage agents get `triage` +
   target scopes; analyst agents get their domains.
2. Set `MEMOGARDEN_SCOPE_ENFORCEMENT=1` in the `memogarden.service`
   unit environment; restart.
3. Provider policies on; statement ingestion builds on P1.

**Measured 2026-11-26 (production, read-only) — what a flip would and
would not do today:**

- **Keys:** six unrevoked, **none with a grant**; no context frame is
  active and no key has ever called `enter`. Two are capability-restricted
  to `provider_run`, one agent-bound app key reads only unscoped feed
  facts, and two are unrestricted unbound keys — one of them past its
  `expires_at`, which is not enforced (`verify_api_key_and_get_user`
  filters on `revoked_at` only, so it still authenticates): revoke it or
  account for it.
- **Reads:** no automation depends on reading a private scope, so the
  flip breaks no scheduled job. `NYJC` and `Household` are
  `visibility: public`; `Triage / Untrusted`, `Finances`, `Personal` and
  `NS` are private — an ungranted key loses exactly those four.
- **Writes:** 49,413 of the 53,369 relabel `amend`s to date were made by
  an agent key (1,952 by the operator, 4 by an unbound key). After the
  flip an ungranted key can neither read a private scope nor file anything
  into or out of one (D2) — **triage work stops for that key until it
  holds grants on the triage scope and every target scope.**
- **Still readable after the flip:** the global space (~15.1k unscoped
  emails, ~1.1k unscoped attachments — unclassified is a positive class,
  not a backlog), and **4,368 unlabelled superseded predecessors of
  privately-labelled items** (visibility addendum §6 item 1). "Enforcement
  hides what has been filed" is false for those rows until item 1 is
  decided.

**Engineering remainders before the flip is decisions-only:**

- visibility addendum §6 item 1 — implement, or explicitly accept;
- SG-9 for the entity lanes and Core-entity sources (P2 item 6 covers
  facts);
- no §4.5 write gate on `commit_artifact`, `link`, `unlink` or
  `edit_relation` (pre-existing);
- `handle_track` (P1 follow-up).

**Flip runbook (operator):**

1. Deploy api `main` first. The SSE suppress must be live before the flag:
   with enforcement on and the suppress absent, the stream is the one
   surface that would volunteer scoped payloads.
2. Baseline ingestion first: one gmail account's run is `failed`
   (`history page budget exceeded`, every run since 2026-11-21) — fix or
   accept it, or provider results will be misread as a flip effect.
3. Provision grants (console → Scopes & grants, or `POST /grants`; JWT).
   `POST /grants` with an `expires_at` currently fails with a 500
   (`Object of type datetime is not JSON serializable`) — grant without an
   expiry for now.
4. Add a drop-in, `memogarden.service.d/scope-enforcement.conf`, with
   `[Service]` / `Environment=MEMOGARDEN_SCOPE_ENFORCEMENT=1`; record it
   in the machine's sysinfo repo; then `systemctl --user daemon-reload`
   and restart `memogarden` between hourly ingest ticks.
5. Verify with an agent key: `/auth/me` reports the grants; a `query` over
   a private scope's item types returns none of them; `get` on a known
   private item is 403 `scope_read_denied`; the next provider run is
   `success`. The operator console (JWT) still sees everything.
6. Rollback: remove the drop-in and restart. No data migration either way.

## References

- RFC-011 v1.3 + visibility addendum; RFC-003 v5 §3.7 (INV-11a/11b/11c)
- Deploy record: system `f651649`, api `7faf180`, live since 2026-11-10
- Audit that produced this list: gmail-importer gap review 2026-11-10
  (relabel, provider hook, entity/relation enforcement, search
  pre-match, SSE payloads, SG-9, flag semantics, docs, minting,
  binaries)
