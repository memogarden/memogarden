# RFC-013 addendum: sessions → Core ingestion — decision register

**Status:** DECIDED 2026-11-18 (two batch passes). No user-blocking items remain;
implementation pending. Residual OPENs are implementation-internal.

Context: RFC-013 (§8 flows, §12 findings) · `schemas/sessions.yaml` · RFC-012
(provider accounts, §4.9) · RFC-011 (scope policy at creation).

## 0. Decisions (2026-11-18)

- **D-1 — Cache feed.** Downloads + screenshots (settled earlier) **plus**: visit
  history from **ALL daemon-hosted sessions**, lifecycle/control events, and the
  session entity mirror. Modeling principle (operator, 2026-11-18): *fact-shaped
  observations drain as Soil facts* (visits, lifecycle, control, downloads);
  *identity/config-shaped state is a Core entity* (the Session entity). The
  "casual browsing = out of scope" verdict is untouched — daily browsing stays in
  Firefox and never enters a daemon-hosted session; "ALL sessions" means every
  session the daemon hosts.
- **D-2 — Drain trigger: trigger-only credential.** The daemon holds a minimal
  MemoGarden credential that can request provider runs (near-instant drain after
  session close); the heartbeat remains the fallback. Custody: Tier-1 operator env
  file (RFC-001 §6 posture). Enforcement gap → new item **E3** (below, OPEN).
- **D-3 — Loss & audit: never evict; no local copy.** Undrained captures are never
  deleted (cache may grow during outages). After drain, local copies are deleted;
  immutable Soil facts are the only archive.
- **D-4 — Scoping: REVISED in pass 2** (supersedes pass 1's account-keyed
  auto-labeling). Operator's final call:
  **all downloads land in Triage** (metadata.account still stamped — dbs/uob —
  so triage→Finances relabeling stays trivial and future rules can automate);
  **visit history stays UNSCOPED** (null-scope rule keyed on a stream metadata
  field); **SessionEvent facts unscoped too** (my reading of the same posture —
  operational audit, low sensitivity; veto if wrong);
  **create the Finances scope at implementation if absent** — it is the triage
  destination, NOT an auto-label target. Fail-closed default→triage remains the
  catch-all for anything else.
- **Terminology correction adopted:** drains are **Soil facts** (the provider
  runtime writes Soil); "Core facts" phrasing retired. Only the Session entity is
  Core-layer.

### Adopted defaults (stated pass 2, unobjected)

- **Provider-level runs** — no `provider_accounts` rows for sessions (no
  credentials to park/reauth); `metadata.account` stamped per fact from the
  session name. Account-backed runs revisit only if per-session cursors earn it.
- **AttachmentStore promoted to `memogarden-utils`** (shared with gmail —
  "reference code, don't copy it"); one shared content-addressed store.
- **Heartbeat fallback interval 15m** for the sessions provider; daemon triggers
  a run only when captures are actually pending.
- Manifest scope policy encodes D-4: null-scope rules on `metadata.stream` ∈
  {history, events}, default "triage".

## A. Residence & semantics

- **A1 — Custody → staging cache: DECIDED (direction 2026-11-16, confirmed).**
  Steady-state drained/near-empty; canonical bytes in MemoGarden's content-addressed
  store; "custody" retains only the capture-guarantee meaning.
- **A2 — Profiles out of scope: DECIDED.** Machine-bound session state, never
  drained, never cache.
- **A3 — Loss-free, no eviction: DECIDED (D-3).** No retention cap, no backpressure.
- **A4 — Local audit after drain: DECIDED (D-3).** No local archive; seq watermark
  + compact events.jsonl after drain; Soil facts are the audit history.

## B. Drain mechanics

- **B1 — Transport: pull via provider mechanism: DECIDED.** In-server
  `sessions-importer`; push intake only if an RPi-style split ever happens (E1).
  Daemon never writes facts.
- **B2 — Ack boundary: default adopted** (contract `ack(item)` hook if the runtime
  is touched for RFC-012 §4.9; else presence-as-queue + delete-after-commit via
  generator ordering). OPEN only as an implementation choice, not a user decision.
- **B3 — Dedup on replay: default adopted** — provider-side existence check on
  (session, sha256) before yielding; makes at-least-once harmless.
- **B4 — Event-stream drain: default adopted** — per-session **seq watermark**
  (L2 envelope already carries `seq`); watermark lives provider-side in a state
  file (gmail-sync-state precedent), advanced only after durable commit.
- **B5 — Cadence: DECIDED (D-2).** Post-close trigger via the daemon's credential
  + short-interval heartbeat fallback. OPEN: exact trigger call (today any
  authenticated principal can POST /providers/<name>/run — see E3).
- **B6 — Partial files: adopted** — only finalized downloads drain.
- **B7 — Ordering: adopted** — per session in seq order; per-fact commit keeps
  same-run relation targets visible (tada lesson).

## C. Capture scope

- **C1 — Download metadata gap: DECIDED (fill it).** Extend `_save_download` to
  record origin URL (`download.url()`), captured_at, downloaded_by (arbiter state at
  download start), content type, size, session, machine; start emitting
  `download_captured` properly (today: filename + print only).
- **C2 — Screenshots drain like downloads: DECIDED** (settled earlier).
- **C3 — Visit history: DECIDED — ALL daemon-hosted sessions, per-navigation
  `PageVisit` facts** (URL, title, timestamp, session, actor context). Dwell /
  rollups rejected for now. Volume accepted as modest (daily browsing stays in
  Firefox).
- **C4 — Lifecycle/control events: DECIDED — full §12.1 mirror as a single
  `SessionEvent` fact kind** with an event discriminator + actor, **including
  refusals** (transition_refused/dispatch_refused are load-bearing audit for the
  co-control protocol). Actor local names map to RFC-010 Operator/Agent entities
  at drain time, server-side.
- **C5 — Session entity mirror: DECIDED (D-1).** Sessions become first-class Core
  entities (label, purpose, password_manager, links, desired_state). Includes the
  eventual reverse config sync (Core → daemon manifest), which stays OUT of the
  drain mechanism (read-only daemon client, later slice).

## D. Representation in Core

- **D1 — Fact kind: adopted** — gmail-parity `Attachment` facts (content-addressed,
  provenance in metadata ⇒ statement pipeline consumes gmail + sessions uniformly;
  closes the "non-email ingest path" gap). Any Document rename belongs to the
  statement-pipeline slice.
- **D2 — Fold, don't pair: adopted** — one Attachment fact carries capture
  provenance in metadata; no separate download_captured event fact.
- **D3 — Cross-session duplicates: adopted** — instance facts keyed (session,
  sha256), gmail's (email, filename) precedent.
- **D4 — Scope defaults: DECIDED (D-4, revised pass 2).** Downloads → Triage;
  history + events unscoped; Finances scope created as triage destination.

## E. Topology & identity

- **E1 — Co-location: noted, contingent.** Pull reads local disk; an RPi split
  flips drain to a push intake endpoint. Bundle with the HACM machine-binding
  migration blocker when RPi gets serious. Cache format must survive the swap.
- **E2 — Keyless daemon: DECIDED — relaxed to trigger-only (D-2).** The daemon
  holds one minimal credential; it never writes facts.
- **E3 — Trigger-only enforcement: DECIDED (pass 2) — restricted key class.**
  Verified: POST /providers/run and /providers/<name>/run have no authz beyond
  blueprint authentication, and API keys are all-or-nothing (no capability model).
  Decision: capability flag(s) on `api_keys`, enforced in the v1 auth middleware;
  issuance stays operator-gated JWT (RFC-010 invariant intact). The capability set
  is extensible later (e.g. +read session config for the C5 reverse sync) without
  a second mechanism. Daemon key starts unbound (username actor; trigger actions
  don't need RFC-010 agent attribution).

## F. Ops & observability

- **F1/F2 — adopted as defaults:** cache depth (pending count/bytes/oldest age) in
  daemon status + drained/remaining in provider_runs detail; delete drained files
  immediately; empty `downloads/` dirs cleaned up.

## G. Docs to land in one amendment pass

RFC-013 §8 rewrite (custody→cache + trigger flow) · `schemas/sessions.yaml` v2
(cache semantics, drain watermark, visit-history + provenance vocabulary, Session
entity config) · RFC-012 §4.9 cross-link (ack hook) · api key capability note (E3
outcome) · statement-pipeline notes (non-email ingest path = this drain).

## Implementation sequencing

1. C1 (daemon: complete download provenance + emit download_captured).
2. Provider skeleton: sessions-importer — downloads/screenshots → Attachment facts
   (B2/B3/B4 defaults), watermark, ack/delete, verified against the UOB PDF
   already in custody.
3. E3 (restricted key class) + daemon trigger wiring (D-2).
4. Event drains: lifecycle/control facts (C4), visit history facts (C3).
5. Session entity slice (C5) + reverse config sync.
6. §G amendment pass.
