# MUSH Architecture v0.1

**Status:** Approved for implementation
**Audience:** Claude Code
**Related:** MUSH PRD v0.2, MUSH Python API v0.1

---

## 1. Decisions Locked

Not open for re-litigation during implementation.

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Daemon language | Python 3.12+, asyncio | I/O + logic at human tempo; velocity and ecosystem coherence with MG/MARS; best AI-assisted-implementation target; most inheritable |
| D2 | VT engine | libghostty-vt, C API, pinned commit | Battle-tested; replaces hand-rolled ANSI parsing; upstream unstable → pin and absorb churn in one module |
| D3 | Binding mechanism | cffi, API mode | Compile-time header validation; ABI drift fails at build, not at runtime |
| D4 | Binding architecture | Two layers: `mush._ghostty` + `mush.vt` | Layer 1 is the sacrificial blast wall; only it changes on upstream churn |
| D5 | Engine linkage | Static-linked `libghostty-vt`, built via `zig build -Demit-lib-vt` | Single inert artifact; cross-compile for aarch64-linux trivially; no runtime dep graph |
| D6 | Human frontend, Stage 1 | TUI client over Unix socket | tmux architecture; validates protocol + arbiter with near-zero rendering investment |
| D7 | Human frontend, Stage 2 | FastAPI + WebSocket + ghostty-web (libghostty-vt WASM) | Headless Pi deployment; same engine at both ends of wire; xterm.js is compat fallback |
| D8 | Native GUI | None, ever | Headless deployment; browser is the only GUI runtime durable across decades |
| D9 | Agent frontend | MCP server over structured Frame-JSON | Replaces stale Letta reference in MUSH PRD v0.1 §14 |
| D10 | Persistence | SQLite, WAL mode, append-only | Matches MG substrate discipline; proto-Soil shape |
| D11 | Frame derivation | Snapshot-based (full grid copy) in MVP; contract allows future incremental backend | Don't optimize now; don't foreclose |
| D12 | Browser assets | ghostty-vt.wasm + ghostty-web JS vendored into repo, served from disk | No CDN at runtime |

---

## 2. Module Layout

```
mush/
  _ghostty/           # Layer 1: cffi binding (see Python API doc)
    build.py
    cdefs.py
    __init__.py
  vt/                 # Layer 2: Python API (see Python API doc)
    terminal.py
    frames.py
    errors.py
  daemon/
    pty_host.py       # PTY spawn, ownership, fd I/O, termios sampling
    arbiter.py        # control state machine
    inference.py      # input intent inference
    observation.py    # frame derivation, debounce, diff, digest
    eventlog.py       # SQLite append-only log
    session.py        # composes the above per process tree
    server.py         # asyncio entrypoint, Unix socket + FastAPI mount
    mcp_frontend.py   # MCP server exposing tools over sessions
    blacklist.py      # behavior-based blacklist rules and counters
  client/
    tui.py            # Stage 1 terminal client
  web/                # Stage 2 (M4 milestone)
    static/           # vendored ghostty-web + ghostty-vt.wasm
    app.py            # WebSocket endpoints, chrome API, replay endpoints
  vendor/
    libghostty-vt/    # pinned source or prebuilt static libs + headers
    PINNED_COMMIT     # one line: upstream git commit hash

tests/
  unit/
  integration/
  vtstreams/          # recorded PTY byte stream fixtures + golden outputs
```

**Dependency rule** (enforced by import-linter in CI):
- `daemon`, `client`, `web` → may import → `mush.vt`
- `mush.vt` → may import → `mush._ghostty`
- `mush._ghostty` and `mush.vt` → no upward imports
- No module imports `mush._ghostty` directly except `mush.vt`

---

## 3. Component Diagram

```
                              ┌──────────────────────────────────────────────┐
                              │             MUSH DAEMON (Python/asyncio)     │
                              │                                              │
  child process               │  ┌──────────┐    ┌───────────────────────┐  │
  via PTY slave               │  │ PtyHost  │───▶│  mush.vt.Terminal     │  │
       │                      │  │ fd I/O,  │    │  (libghostty-vt/cffi) │  │
       ▼                      │  │ termios  │    └──────────┬────────────┘  │
  PTY master ────────────────▶│  │ sampling │               │ snapshot()    │
       ▲                      │  └────┬─────┘               ▼               │
       │ (gated writes)       │       │             ┌──────────────────┐    │
       │                      │       │             │ Observation Layer │    │
       │                      │       │             │ (debounce, diff,  │    │
       │                      │       │             │  digest, frames)  │    │
       │                      │       ▼             └────────┬─────────┘    │
       │                      │  ┌──────────┐                │ Frame        │
       └──────────────────────│──│ Arbiter  │◀───────────────┘              │
                              │  │ (state   │                │              │
                              │  │  machine)│       ┌────────▼──────┐       │
                              │  └────┬─────┘       │  EventLog     │       │
                              │       │             │  (SQLite,     │       │
                              │       │             │   append-only)│       │
                              │       │             └───────────────┘       │
                              │  ┌────┴──────────────────────────────────┐  │
                              │  │           Frontend Server             │  │
                              │  │  Unix socket  (Stage 1 TUI)           │  │
                              │  │  WebSocket + HTTP  (Stage 2 browser)  │  │
                              │  │  MCP server  (agent)                  │  │
                              │  └───────────────────────────────────────┘  │
                              └──────────────────────────────────────────────┘
```

---

## 4. Data Flow: Output Direction

1. Child writes to PTY slave → bytes on PTY master fd.
2. `PtyHost` reads bytes; forwards verbatim to `Terminal.write()`.
3. Engine updates state. Device-query callbacks (`on_write_pty`, etc.) fire synchronously inside `write()`. Reply bytes are enqueued to the Arbiter as **system-actor** writes — never written to the fd inline.
4. Observation Layer: after `write()` returns, schedule a frame derivation if not already pending. Coalesce writes within 16 ms; derive frame at hard cap of 50 ms after first write in burst.
5. Derived `Frame` written to EventLog (`output_emitted` + `frames` row) **before** any frontend send.
6. Frontend Server fans the frame out: `frame_vt` (VT re-emission) to terminal and WebSocket renderers; `frame_json` (structured Frame) to MCP agent and chrome UI.

---

## 5. Data Flow: Input Direction

1. Frontend submits `InputEvent` tagged with actor identity (human / agent / system).
2. Arbiter validates: is this actor the active controller? Is the session state permitting injection? Is the hard control lock active? Is inferred mode SECRET?
3. **Accepted:** log `input_injected` (commit to DB), then write encoded bytes to PTY master.
4. **Rejected:** log policy failure; return structured error to submitting frontend. If the rejected actor was an agent attempting out-of-turn injection: freeze → OBSERVE_ONLY → `failure_detected` event → await human action.
5. Resize events accepted from the controller: TIOCSWINSZ on the PTY master AND `Terminal.resize()`, in that order.

**No path from any frontend to the PTY fd bypasses the Arbiter.** The `on_write_pty` callback path is the one subtle exception: it is mediated as a system-actor write (logged, attributable) via the Arbiter's internal fast path, not via a frontend.

---

## 6. Daemon Modules

### 6.1 `pty_host.py`

Responsibilities:
- Spawn child process (login shell or specified argv) with `os.openpty()` + `os.fork()` / `subprocess` with PTY
- Own the PTY master fd exclusively — no other module holds this fd
- Asyncio reader on the PTY master; forwards read bytes to `Terminal.write()`
- Periodic termios polling (every 100 ms by default) + poll on read-burst boundaries; publish mode-change events to inference
- On child exit: emit `session_end` event; begin teardown
- TIOCSWINSZ on resize events from the Arbiter

### 6.2 `arbiter.py`

Implements the PRD §9 state machine. States and transitions are encoded as an explicit table (not as ad-hoc if/else chains) so the invariants can be property-tested exhaustively.

```python
class ArbiterState(Enum):
    HUMAN_ACTIVE    = "human_active"
    AGENT_ACTIVE    = "agent_active"
    OBSERVE_ONLY    = "observe_only"
    PASSTHROUGH     = "passthrough"

class OperatingMode(Enum):
    OBSERVE_APPROVE     = "observe_approve"
    AUTONOMOUS_BOUNDED  = "autonomous_bounded"
```

The transition function is a pure function `(state, event, actor) → (new_state, allowed: bool)` with no side effects. The Arbiter class wraps it with logging and notification. This makes the transition logic independently unit-testable without asyncio or PTY infrastructure.

Invariant assertions are `assert` statements, not comments. Each assertion maps to one of the PRD §7 invariants. They run in production (not just tests) because violations are bugs, not edge cases.

System-actor write fast path: `on_write_pty` callback bytes are accepted without going through the full input validation pipeline, but ARE logged as `input_injected(actor=system)`. They bypass controller-identity checks only — not the freeze/lock checks. If the session is frozen (any failure class in progress), system-actor writes are also queued, not silently dropped.

### 6.3 `inference.py`

Pure function `infer(termios_state: TermiosState, engine_modes: TerminalModes) → (InferredMode, confidence: float)`.

```python
class InferredMode(Enum):
    LINE             = "line"           # canonical + echo on
    SECRET           = "secret"         # canonical + echo off
    SINGLE_KEY       = "single_key"     # raw + VMIN=1 + VTIME=0
    FULL_INTERACTIVE = "full_interactive"  # alt-screen or mouse reporting active
    NON_INTERACTIVE  = "non_interactive"   # no read activity / output-only programs
    UNKNOWN          = "unknown"        # conflicting signals; degrade safely
```

Decision table (explicit, not heuristic):

| termios canonical | termios echo | alt_screen | mouse | → Mode |
|-------------------|-------------|-----------|-------|--------|
| True | True | False | False | LINE |
| True | False | False | False | SECRET |
| False | any | False | False | SINGLE_KEY |
| any | any | True | any | FULL_INTERACTIVE |
| any | any | False | True | FULL_INTERACTIVE |
| conflicting / unreadable | | | | UNKNOWN |

UNKNOWN always produces confidence < threshold → hard control lock in Arbiter. Mode is re-evaluated on every termios poll event and on every engine `modes()` change. Mode changes are logged as `state_transition` events and pushed to all frontends immediately.

### 6.4 `observation.py`

Owns frame derivation timing and the dedup/diff logic.

- Maintains a debounce timer: first `write()` after quiescence starts a 16 ms window; timer fires at 50 ms hard cap regardless.
- On timer fire: call `Terminal.snapshot()`, compute `Frame.digest()`, compare against last-emitted digest. If identical: skip (no event, no frontend send). If different: emit `output_emitted` event + frame row, then fan out.
- Exposes `current_frame: Frame | None` — the most recently emitted frame. Used by the MCP frontend for `mush_read_screen`.
- Does not own the EventLog connection or the frontend fan-out sockets; it calls injected callbacks for both.

### 6.5 `eventlog.py`

Thin asyncio-compatible write path over a synchronous SQLite connection (WAL mode; the single-writer constraint means no connection pool is needed). All writes go through one method:

```python
async def append(self, event: Event) -> int:
    """Write event to the events table. Returns event_id.
    MUST complete before the caller performs the side effect the event records."""

async def append_frame(self, event_id: int, frame: Frame) -> None:
    """Write frame to the frames table, linked to event_id."""
```

Schema per PRD §16. No UPDATE, no DELETE. These are not just style choices — they are enforced by the absence of those statement patterns anywhere in the codebase. A linter rule or grep in CI is sufficient.

### 6.6 `blacklist.py`

Evaluates hard and soft criteria against observations from PtyHost. Emits `blacklist_triggered` events. Hard triggers call the Arbiter's state machine with a `BLACKLIST_HARD` event → PASSTHROUGH. Soft triggers increment counters; escalate to PASSTHROUGH on threshold breach.

Linux-specific detection (`/proc/<pid>/fd` scan, proc tree) is guarded by platform check; on non-Linux, soft-criterion heuristics only. Every trigger alerts the operator via the chrome channel.

---

## 7. Wire Protocol

Length-prefixed msgpack over Unix socket (Stage 1). JSON over WebSocket (Stage 2). Logically identical; only transport and encoding differ.

### Server → Client

| Message | Fields | Trigger |
|---------|--------|---------|
| `frame_vt` | session_id, seq, data: bytes | Every new frame (terminal renderers) |
| `frame_json` | session_id, seq, frame: Frame | Every new frame (agent, chrome) |
| `chrome` | session_id, controller, op_mode, inferred_mode, blacklist, last_actor, title, pwd | Any field changes |
| `approval_request` | id, summary, proposed_input, declared_intent | OBSERVE_APPROVE mode, agent input pending |
| `failure` | class, detail | Any failure event |
| `session_end` | reason | Session teardown |

### Client → Server

| Message | Fields | Notes |
|---------|--------|-------|
| `attach` | session_id OR new: {argv, cols, rows} | First message after connect |
| `input` | kind: keys\|paste\|signal\|resize, data, declared_intent? | Proposal to Arbiter; declared_intent advisory, logged |
| `control` | action: handoff\|reclaim\|lock\|resume\|override_ack\|approve\|deny, ... | State machine transitions |
| `detach` | — | Clean disconnect |

`declared_intent` is logged as `intent_declared`. It is advisory only; it never gates safety decisions.

---

## 8. Frontend Specifications

### 8.1 Stage 1 — TUI client (`client/tui.py`)

Connect to Unix socket → `attach` → receive `frame_vt` → write to stdout. Maintain one reserved status line (terminal row 0 or bottom, configurable) rendering the six chrome fields. Capture stdin in raw mode → `input(kind=keys, ...)` messages. Prefix key (default `C-]`) opens a control menu: handoff, reclaim, lock, resume, approve, deny, detach. Approval prompts render inline as a one-line overlay. No curses required — the operator's terminal emulator renders all VT content.

### 8.2 Agent frontend — MCP server (`daemon/mcp_frontend.py`)

Every tool call: log `toolcall_received` before execution, `toolcall_completed` after.

| Tool | Returns | Notes |
|------|---------|-------|
| `mush_list_sessions()` | `[{session_id, title, pwd, controller, inferred_mode, blacklist_status}]` | |
| `mush_read_screen(session_id, format)` | `str` or `Frame-JSON` | format: "text" or "frame"; uses Observation.current_frame |
| `mush_read_events(session_id, since_event_id, limit)` | `[Event]` | Bounded; reads from EventLog |
| `mush_declare_intent(session_id, text)` | `{ok}` | Logged as `intent_declared`; advisory only |
| `mush_send_input(session_id, kind, data)` | `{ok}` or `{error, reason}` | Subject to Arbiter; out-of-turn → failure path |
| `mush_request_control(session_id)` | `{pending}` | Requires operator approval via `approval_request` / `approve` flow |
| `mush_yield_control(session_id)` | `{ok}` | Immediate; authority-reducing |

Raw PTY byte streams are never returned. The agent sees Frame-JSON or text, not escape sequences.

### 8.3 Stage 2 — Browser (`web/`)

FastAPI app mounted in the daemon process. Endpoints:
- `GET /static/*` → vendored ghostty-web assets (served from disk, no CDN)
- `GET /sessions` → session list JSON
- `WS /sessions/{session_id}` → §7 protocol in JSON
- `GET /sessions/{session_id}/replay` → replay viewer

Browser: ghostty-web `Terminal` instance. `terminal.write(frameVt.data)` on `frame_vt` messages. `terminal.onData` → `input(kind=keys)` to WebSocket (proposal to Arbiter; renderer trust does not mean channel trust). `terminal.resize()` → `input(kind=resize)`. `chrome` messages → header bar DOM update. `approval_request` → modal dialog DOM element.

Constraint: use only ghostty-web's xterm-compatible API surface. Do not call ghostty-web-specific APIs that have no xterm.js equivalent. The swap test at M4 exit (replace ghostty-web with xterm.js and verify the acceptance suite passes) enforces this.

---

## 9. Milestones

### M0 — Engine binding
**Deliverables:** vendor + pin libghostty-vt; static lib builds for x86_64-linux and aarch64-linux; `mush._ghostty` Layer 1; `mush.vt` (`Terminal`, `Frame` and friends); VT stream fixture suite; capture script.
**Exit gate:** `Terminal` round-trips all fixtures; device-query fixtures trigger `on_write_pty` callbacks; reentrancy contract test passes; pin-verification test passes; close/after-close tests pass.

### M1 — Daemon core
**Deliverables:** `PtyHost`, `Session`, `Arbiter`, `inference`, `Observation`, `EventLog`; replay tool (`mush replay <session_id>`); property test suite (Hypothesis) for Arbiter transitions; failure-injection tests for all Extension failure classes; log-ordering tests.
**Exit gate:** invariant test suite green; replay reconstructs sessions byte-exactly from cold DB; memory under 100MB with one idle session.

### M2 — Stage 1 frontend
**Deliverables:** Unix socket protocol; `client/tui.py`; manual acceptance script.
**Exit gate:** manual acceptance — shell, vim, sudo password prompt (SECRET mode: agent locked out, content redacted in log), blacklist-triggering program (PASSTHROUGH + alert). PRD §23 success criteria demonstrable interactively.

### M3 — Agent frontend
**Deliverables:** `mcp_frontend.py`; OBSERVE_APPROVE end-to-end test with real agent.
**Exit gate:** agent declares intent, proposes input, operator approves, action executes, full trail recoverable from log alone. Out-of-turn injection test → failure path verified.

### M4 — Stage 2 browser
**Deliverables:** vendored ghostty-web + wasm; `web/app.py`; basic replay viewer.
**Exit gate:** M2 acceptance script passes in browser; renderer swap test (ghostty-web → xterm.js) passes.

---

## 10. Risks

| Risk | Mitigation |
|------|------------|
| libghostty-vt C API churn | Pin + cffi API mode (loud build failure) + Layer 1 isolation; pin upgrades are isolated commits |
| Snapshot cost under firehose output | Debounce in Observation Layer; `Frame.digest` dedup; D11 incremental-backend path available when needed — measure first |
| `on_write_pty` reentrancy | Enqueue to Arbiter fast path, never write fd inline; reentrancy contract test in M0 |
| ghostty-web immaturity | xterm-compatible surface only; swap test enforced at M4 |
| termios sampling races | Sample at burst boundaries + interval; treat inference as confidence-weighted; UNKNOWN → degrade |
| `/proc` blacklist detection portability | Linux-only, guarded by platform check; non-Linux degrades to soft heuristics only |

---

## 11. Instructions to Implementer

1. **Read order:** MUSH PRD v0.2 → MUSH Python API v0.1 → this document. The PRD §7 invariants are acceptance criteria; encode them as assertions and tests in M1 before writing any frontend code.
2. **Dependencies:** `cffi`, `fastapi`, `uvicorn[standard]`, `msgpack`, `pytest`, `hypothesis`. No additional runtime dependencies without a justification comment in `pyproject.toml`. Use `uv` or `pip-tools` for pinning.
3. **Build libghostty-vt first.** Commit the pin before writing any binding code. If current upstream `main` fails to build, pin to the most recent passing commit and document it.
4. **Fixtures before mocks.** Record real PTY byte streams in `tests/vtstreams/` before writing any daemon tests. Test the engine against reality.
5. **Four things that must not happen:** (1) session management in `mush.vt`; (2) PTY fd writes anywhere but the Arbiter path; (3) UPDATE or DELETE in `eventlog.py`; (4) automatic recovery from any failure class. These are the four architectural violations that are hardest to detect in code review.
6. **Spec gaps:** when the spec is silent, prefer the conservative reading — degrade, log, surface to operator. Mark genuine gaps as `SPEC-GAP:` comments; surface them rather than resolving silently.
7. **Deferred scope (do not implement in MVP):** log compaction; AUI projection; multi-agent per session; structured key/mouse encoding via VT engine encoders; scrollback in Frame (formatter covers it); MG substrate migration.

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-06-11 | Initial: locked decisions, module layout, component diagram, data flow, daemon module specs, wire protocol, frontend specs, milestones, risks |
