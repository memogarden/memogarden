# RFC-013 — Browser Session Host (memogarden-sessions)

**Status:** Accepted — design converged 2026-11-15; invariants locked; mechanism deferred to spike
**Repos:** `memogarden-sessions/` (new sibling app repo, created 2026-11-15, unscaffolded)
**Related:** MUSH (shared-session precedent), RFC-005 (API design), RFC-010 (agent identity), RFC-011 (scopes/grants), RFC-012 (provider accounts), statement-ingestion assessment

---

## 1. Problem

Browsers conflate *session-as-window* with *session-as-identity*. Profiles, tab groups, and
containers partition identity or color tabs, but none of them provide durable **lifecycle**:
closing a window feels like losing your place, so windows stay open, tabs accumulate, and
memory goes with them. Separately, operators and agents cannot share a live browser:
automation tooling (Playwright, Puppeteer, agent-browser clouds) is agent-only with no
operator affordances, and extension-based co-control inherits the single-browser-frame
conflation this RFC is escaping.

## 2. Scope verdict

**Companion, not replacement.** We are not killing the browser, only its UI/UX monopoly over
session identity and lifecycle.

- **In scope:** structured sessions — financial portals, project workspaces, household admin;
  anything with identity + lifecycle + agent involvement.
- **Out of scope:** casual browsing and the "generic session with tens of tabs". Tab-hoarding
  of half-read articles is a *capture* problem (MemoGarden philosophy: capture, close,
  retrieve later), not a session problem.

## 3. Architecture boundary

Every design question is answered by asking which side of this line it is on:

| Stays in Chromium's jurisdiction | Session host owns |
|---|---|
| rendering, JS, sandbox | identity packaging (`--user-data-dir` per session) |
| storage engines (cookies, localStorage) | lifecycle (create / open / dehydrate / rehydrate / cull) |
| security model, updates | control arbitration (operator ⇄ agent) |
| session restore fidelity | download interception → Soil |
| | introspection + metadata (CDP-derived) |

**Hard guardrail: never fork the browser.** Composition only (stock Chromium + daemon + CLI +
minimal operator surface). A UX need that cannot be met by composition is deferred, not
built into a fork.

## 4. Stack decisions

- **System Chromium** (Arch package) + **pinned Playwright version**. `connectOverCDP`
  tolerates minor version skew; pin the driver, not the browser.
- **Transport: `--remote-debugging-pipe`, always.** No listening ports, ever. The pipe is
  exclusive to the daemon.
- **Daemon = sole CDP client.** Agents never touch CDP or get a Playwright handle; they call
  the daemon API, which drives Playwright internally (the `mg_client` pattern one layer down).
  This keeps per-agent attribution honest (RFC-010 actor entities) and the pipe exclusive.
- **Python daemon** — aligns with MemoGarden Python 3.13 + first-class Playwright Python.
- **Downloads intercepted → Soil** as a second ingest path, emitting the same
  document/Evidence facts as gmail-importer so the postprocessing pipeline is shared. This
  closes the "no non-email ingest path" gap from the 2026-11-09 statement assessment.
- **One local user-unit daemon** on the interactive machine; metadata in Core. Consistent
  with service minimalism (no per-feature servers/venvs) and the provider-runtime split
  (Core dispatches, devices execute).

## 5. Locked invariants

1. **Control unit = tab.** Session-level control state is a derived summary, not the
   arbitration unit. (Exhibit B: agent on tabs the operator isn't using; Exhibit A: 2FA
   handover is one tab mid-flow.)
2. **Operator sovereignty.** Transitions are asymmetric: the operator may always seize;
   the agent must always yield on request. Same invariant shape as operator-gated identity
   issuance (RFC-010).
3. **Transitions are explicit, named events, recorded as facts** — request / grant / seize /
   release, with actor (RFC-010 Agent/Operator entities). Low-frequency and semantically
   meaningful: fact-worthy without flooding the corpus.
4. **Live arbitration is local; Core is eventually-consistent history.** The daemon decides
   *now* (it owns the pipe); Core records *what happened* (async commit — cf. the
   `Soil.commit()` same-run visibility lesson). A control transition never blocks on a Core
   round-trip.
5. **One trusted controller between agents and the browser.** Daemon is the only CDP client;
   agent identity → daemon session → browser actions. Audit falls out for free.

**Load-bearing reality:** on a headed browser the operator cannot be (and should not be)
physically locked out — a human's mouse always reaches the page. "Control" therefore means
**gating agent-side dispatch** (the daemon refuses to inject input into operator-held tabs)
**plus signaling** (visual state so both parties know who has the wheel). The protocol
formalizes the agent's behavior; the operator's side is sovereignty + affordance.

## 6. Session model & lifecycle

- **Session = `user-data-dir`.** Opaque state (cookies, site data) stays browser-modeled;
  the PIS holds metadata: entity, labels, sites-in-session (derived from cookie domains),
  `last_verified_login`, lifecycle events.
- **Dehydrate** = graceful shutdown → optionally purge `Cache*` (cache ≠ state; cookies,
  Local Storage, `Sessions/` restore files are kept) → snapshot dir → zero RSS.
  **Rehydrate** = restore + relaunch. The Chromium profile lock gives single-flight
  ("a session is open in exactly one place") for free.
- **Session dirs are credentials.** Custody follows RFC-001 posture: FDE reliance, 0600.
- **Snapshot cheapness:** overlayfs/btrfs snapshots make dehydration cheap on Arch.
- **Memory:** a closed session costs ~nothing; an open session is a full browser process
  (~150–400 MB fixed). The memory story therefore *depends* on aggressive lifecycle — and
  the PIS provides the psychology for it: closing a *session* is filing, not discarding.
- **Fidelity limits (accepted):** session restore brings back tabs, history, and mostly
  scroll; it loses form inputs, in-flight checkouts, and SPA in-memory state beyond what
  the app itself persists. ⇒ **Conservative dehydration policy:** operator-invoked, or
  long-idle with consent. Never auto-dehydrate mid-checkout.

## 7. Co-control protocol

Invariants above are locked (that is the protocol's durable part — it shapes the daemon API
and Core fact vocabulary). The **mechanism** — exact state machine, operator-presence
detection, handover request surfacing, operator affordance — is the spike's output
(§10), not designed on paper first.

## 8. Target flows

**Exhibit A — statement ingestion.** Email arrives (statement ready) → triage → `finances`
→ associated session `desired_state = rehydrated` → operator **or** agent downloads the
statement → lands in Soil → postprocessed → absorbed into the PIS without the
download-and-attach dance. Design notes:

- Rehydration is **requested, not automatic** — the label move sets `desired_state`; the
  daemon surfaces "finances session requested" rather than spawning a window unbidden.
  Agents can request via API under policy; default is human-visible. Single-flight means an
  already-open session cannot silently rehydrate elsewhere — the state machine handles this
  explicitly.
- Downloads→Soil emits the same document/Evidence facts as gmail-importer ⇒ shared
  postprocessing ⇒ the browser becomes a document *provider* in effect (modeled as an app,
  not a registry provider).

**Exhibit B — research/shopping.** Operator works some tabs; agent assists on background
tabs (collating, summarizing, capturing visited URLs via CDP page-lifecycle events and
downloaded files); operator pauses → session dehydrates, freeing resources; session stays
attached to project artifacts (todos, plans, journal logs — Studio's existing entity
linking); rehydrate and continue where left off.

## 9. Mobile (future; informs stack only)

Sessions are **machine-bound artifacts** (HACM-store precedent: stores don't roam, iOS
forbids the daemon outright). Mobile **directs, doesn't run**: registry view, document
read, dispatch "rehydrate finances on the desktop and download the statement" — the same
daemon API, no Playwright anywhere near the phone. No desktop-only assumptions in the
entity model: window geometry lives in the daemon, not in Core.

## 10. Spike (next step)

One headed Chromium over the pipe; daemon as sole CDP client; **two synthetic controllers**
exercising per-tab control states; transitions emitted as facts. Purpose: feel whether
handover is right *before any schema is cut*. Risk isn't feasibility (all composition of
proven primitives) — it's whether handover *feels* right, which is only knowable by
driving it.

## 11. Non-goals

- No fork / custom browser chrome — composition only (§3 guardrail).
- No casual browsing absorption (§2).
- No physical operator lockout (§5) — sovereignty + affordance instead.
- No automatic rehydration (§8) — requested and human-visible by default.
