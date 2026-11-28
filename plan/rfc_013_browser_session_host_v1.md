# RFC-013 — Browser Session Host (memogarden-sessions)

**Status:** Accepted — design converged 2026-11-15; invariants locked; mechanism validated by spike, verdict accepted 2026-11-15 (§12); shell-layering lens recorded 2026-11-27 (§13 — **TENTATIVE**, only the trajectory-integration intent is fixed)
**Repos:** `memogarden-sessions/` (sibling app repo, created 2026-11-15;
scaffolded — daemon, CLI, tests, spike archive, HANDOVER.md — with its
ingestion half LIVE via `providers/sessions-importer`, see the RFC-013
decision register)
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
- **Restore is per-profile = per-session** under the 1:1 mapping: Chromium's SNSS
  restore state, like all profile state, lives inside the session's own dir and never
  crosses sessions (verified in smoke — adjacent sessions restore independently).
  Two accepted consequences: (a) Chromium restores *last-exit* state — desired/pinned
  tabs are a daemon-layer enhancement on top (Core session entity holds desired state;
  daemon re-asserts after restore); (b) cross-session tab moves are daemon-mediated
  (read from A over CDP, open in B) — the isolation trade Firefox containers made in
  reverse (they had tab mobility but no lifecycle; we get lifecycle at that price).
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
- **Ingestion mechanism DECIDED 2026-11-18, IMPLEMENTED + LIVE 2026-11-18/19** (decision register:
  `plan/rfc_013_ingestion_decision_register.md` — supersedes the parenthetical above
  where they differ): drain via the **provider mechanism** (in-server
  `sessions-importer` pull over the daemon's staging cache); the daemon never writes
  facts and holds a **trigger-only** credential (restricted key class, E3 —
  implemented: `api_keys.capabilities`, 403 `capability_denied` outside the
  capability's route set). Custody area
  reclassified as a **staging cache** — steady-state drained/near-empty, MemoGarden's
  content-addressed store is the canonical residence; feed = downloads, screenshots,
  per-navigation `PageVisit` facts (all sessions), full §12.1 `SessionEvent` mirror
  (single fact kind, refusals included), session entity mirror. Downloads → Triage;
  history/events unscoped; Finances scope = triage destination. Loss-free: undrained
  captures are never evicted; zero local retention post-drain. First live drain
  verified 2026-11-18 (UOB statement PDF → Attachment fact, sha256-verified, staged
  copy deleted).

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

---

## 12. Spike findings (2026-11-15) — mechanism validated at spike fidelity

Verdict: **accepted by operator** after three feel-test rounds. Spike code
lives in `memogarden-sessions/spike/` (commits 29255ec..a2243ee, branch
`agent/sessions-host/spike`, merged to `main`). Design kernel below;
**implementation mechanics** (extension bridge internals, probes, polling)
live in `memogarden-sessions/HANDOVER.md` — design in RFCs, mechanics in
the implementation repo.

### 12.1 Vocabulary

- `seize` → **`commandeer`** everywhere, event vocabulary included:
  commandeer is an override for a specific purpose, not a tug-of-war move.
- Events: `agent_request`, `operator_grant`, `operator_commandeer`
  (optional `reason` recorded on the event), `agent_release`,
  `transition_refused`, `dispatch_refused`.

### 12.2 Signaling — tab strip only

- Page badges and favicon dots **rejected** (badges obscure page visuals;
  browsers do not revert a tab icon when an injected icon is removed).
  **The daemon never touches favicons.**
- Control state signals through the **tab strip**, drawn by a shipped
  companion extension: agent-held = green group, pending request =
  blinking yellow, operator-held = undecorated.
- The extension is composition-only infrastructure — a shell (§13) draws
  signaling natively and retires it.

### 12.3 Soft lock (the operator-facing direction)

- Agent-held tabs present a transparent pane swallowing physical
  keyboard/pointer input; the first interception raises a small toast
  with an in-browser **Commandeer button** (a page flag the daemon polls).
- **Invariant refinement (§5):** "the operator is never locked out" →
  **"the operator is never SILENTLY locked out."** Soft locks are
  permitted iff every interception surfaces an immediate override path.
  Soft locks guard against inadvertent input; they are not an
  access-control mechanism.
- Known hole: browser-level shortcuts (Ctrl+W/T) bypass any in-page pane —
  a daemon-level concern for later.

### 12.4 Dispatch paths — load-bearing for the daemon

- The **physical-input path** (CDP Input domain) is subject to arbiter
  gating *and* the soft-lock pane.
- The **direct-DOM path** (CDP Runtime domain) is the agent's route on
  locked tabs: unaffected by the pane. **The daemon chooses the dispatch
  path per control state.**

### 12.5 Base browser

- **Branded Chrome is disqualified**: it silently blocks
  `--load-extension`, so the companion extension never loads. The session
  browser must be an **open Chromium build** (Playwright's pinned
  Chromium verified; Arch's `chromium` equally valid).
- §4's "pin the driver, not the browser" stands: accept browser churn,
  require only open builds.

### 12.6 Deferred from the spike

- Gating granularity is per-action: a commandeered tab lets one in-flight
  action land.
- `file://` pages are off the table for extension content scripts — the
  session daemon serves/intercepts pages over localhost; real-site
  coverage untested.
- Favicon restoration, the browser-shortcut hole, window-separation as an
  alternative soft-lock shape, and group-tile ergonomics.
- **Operator→agent handover affordance** ("I'm done — agent can take over
  the session"): richer than the CLI `grant`; protocol + UI surface to be
  explored (live-observed need, DBS scenario 2026-11-15).

---

## 13. Shell layering (2026-11-27) — TENTATIVE lens

> **Read this first.** Exactly one thing in this section is fixed: **the
> browsing trajectory (pages visited, links followed, downloads captured,
> lifecycle and control events) belongs in the PIS as first-class facts.**
> That intent is already implemented and live — `providers/sessions-importer`
> drains Attachment / SessionEvent / PageVisit facts and mirrors Session
> entities (decision register, slices 1–5). Everything else below is an
> *exploratory decomposition* to inform whether and when a different shell
> would serve that intent better. No prototype is green-lit; no stack below
> is ratified. §3 and §12 remain the shipped reality.

### 13.1 Why the lens exists

The composition stage (§3–§12) pays a recurring tax: every hard-won spike
finding is a negotiation with Chromium's **desktop paradigm** — the
data-model assumptions of a consumer browser, not Google branding (which is
cosmetic):

- downloads default to a Downloads dir (custody capture is a daemon-side
  parasite on a scatter-to-`~/Downloads` design; operators look for captured
  files in the wrong place);
- one stable profile managed by the user (per-purpose sessions exist only
  as `--user-data-dir` carvings; dehydration = hand-listed cache globs);
- sessions and history are internal and opaque (restored tabs never fire
  page events → ghost-tab adoption; tab strip unscriptable via CDP → the
  §12.2 extension detour; SNSS restore reachable only through pref
  incantations + crash-classification workarounds; favicons never revert).

The load-bearing fact: **all of that lives in the browser layer (`chrome/`),
not the engine.** `content/` + Blink + V8 has no Downloads dir, no desktop
profile, no SNSS, no history service — an embedder of the content layer
gets raw events instead. Discarding the browser layer is therefore not
forking the engine (§11's guardrail survives — see §13.5).

### 13.2 Three-tier decomposition (lens only)

| Tier | What it is | Posture | Operator ruling 2026-11-27 |
|---|---|---|---|
| **1 — Engine** (Blink + V8, media stack, web platform) | web compatibility itself | rented, pinned | needed **in full except Widevine** |
| **2 — Content infrastructure** (multi-process, renderer sandbox, network stack, cookie/storage partitions, navigation events, downloads-as-events, DevTools/CDP) | the embedder tier — Electron/CEF territory | rented via embedder | needed. Session-per-process preferred (own process per session, attached to its context/task). DevTools = agent-RPA surface only, never the consumer surface |
| **3 — Browser services** (profiles, history, SNSS restore, Downloads dir, browser UI, extensions, password manager, PDF viewer, Google services) | the "desktop paradigm" | **owned or dropped** | integrated with memogarden (§13.3) or discarded |

Isolation granularity under session-per-process, named honestly:
cross-session ✓ (already true today); per-tab ✓ (embedder per-WebContents
default); **per-site-iframe — substrate-reported, not inherent**: Electron
ships Chromium site isolation enabled by default (verified 2026-11-28), so
the residual this lens assumed is narrower there; verify per substrate.
Not worth chasing beyond what the substrate gives (finance sessions load
same-trusted-page iframes; research sessions are covered by the per-tab
line).

### 13.3 Tier-3 ownership mapping (tentative)

| Chromium service | Becomes |
|---|---|
| profiles | Session entities + partitions (already modeled in Core) |
| history service | PageVisit facts (already drained) |
| SNSS session restore | own dehydrate model — tab set + chosen per-tab state; more honest than restore-by-incantation given §8's fidelity limits |
| Downloads manager + dir | custody capture → Attachment facts (**live since 2026-11-18**; becomes a native embedder event, no Downloads dir exists) |
| PDF viewer (PDFium) | separate **document-viewer component** — an ecosystem need independent of sessions (Artifacts, docling markdown, `/console/documents`); downloads-not-viewing is already the pattern |
| print / spellcheck / find-in-page | free from any embedder — not even a decision |
| extensions | optional hatch (an extension pipeline exists), **demoted from load-bearing** |
| Safe Browsing / sync / Google services / auto-update | dropped; engine updates become pin-and-bump (the Playwright-build model today) |

### 13.4 Known residuals — the prototype gate list

A content-layer shell loses real things; these are the verdicts a
prototype must clear (substrate specifics live in the sessions-repo issue
and agent memory, not here):

1. **Credential tethering** — password managers that pair with a desktop
   app over the browser's native-messaging channel cannot work in an
   embedder shell. The credential path is the primary gate: self-contained
   extensions if the substrate admits them; the MemoGarden-native fallback
   is an operator-gated credential bridge (custody question open).
2. **PDF rendering** — embedders ship no built-in viewer; the
   document-viewer component (§13.3) must exist before sessions render
   PDFs (soft, given the downloads-not-viewing pattern).
3. **Extension compatibility** — substrates admit only subsets of the
   extension platform. Control signaling must never depend on an extension
   (a shell draws it natively — §12.2 retires); extensions are an
   operator-experience hatch only.
4. **Site-isolation residual** (§13.2) — accepted, recorded.
5. **Substrate toolchain** — an embedder brings its own runtime ecosystem;
   the daemon contract insulates everything above it.

Candidate shape, if ever prototyped: **one thin embedder shell behind the
unchanged daemon contract** — one session per process; custody capture,
navigation events, and tab persistence as shell-native services (the fact
stream becomes *generated*, not mined, unlocking click-level trajectory
later); control signaling drawn natively; the daemon's verbs become
native shell calls.

### 13.5 Guardrail reinterpretation (amends §3 / §11 — tentative)

"**Never fork the browser**" survives, reinterpreted: **never fork the
engine.** The browser layer is the part a shell would deliberately
*replace*, and embedders track upstream Chromium releases without engine
code — vendor-tracked maintenance, not fork maintenance. Until a prototype
is green-lit, §3's two-column boundary and §11's composition-only
non-goal stand unchanged.

### 13.6 Triggers that would move the lens to a prototype

- Google-integration pain resurgent — note **ungoogled-chromium is the
  cheap composition-stage fix, not a paradigm fix** (a resolution-chain
  swap in the daemon; explicitly *not* the end-state).
- Sessions needed as embedded UI: mobile host, or many-sessions-one-process
  via partitions.
- The §13.4 password-manager verdict answered (in either direction).
- Operator handover / control signaling wanting more than an extension can
  draw (live-observed need, §12.6).

### 13.7 In-page bridge (actionable today, independent of the shell)

Formalize the in-page surfaces introduced by the spike (the signaling
bridge, §12.2; the snapshot element refs) into a named **`window.mg.*`**
contract documented in `schemas/sessions.yaml`. The guest-facing protocol
becoming *ours* (not Chromium's) is what keeps any shell swappable.
Proposed; not yet ratified.

### 13.8 Substrate steer (2026-11-27) — Electron-class first

Operator ruling on the §13.4 trade-off: **the operator experience wins —
extensions carry the password managers.** For any prototype:
**Electron-class embedder first** (its extension subset admits more of the
operator's tooling; the credential-tethering residual stands regardless);
the **Python-native embedder (PySide6/QtWebEngine)** is the noted
substitute — it trades the extension hatch away entirely, so it becomes
attractive only if the extension path ever loses relevance. Substrate
mechanics, gate experiments, and the comparison record are parked out of
this RFC (sessions-repo issue; agent memory). The daemon contract keeps
the ordering reversible.
