# MUSH Implementation Plan

**Context:** MUSH (Multi-User Session Host) is a PTY daemon enabling safe human–agent joint operation over existing CLI programs. Traditional terminals are hostile to agents due to blocking input, implicit state, and byte-stream I/O. MUSH provides explicit control arbitration, inferred input intent, and complete auditability.

**Documentation Reviewed:**
- MUSH PRD v0.2 (product requirements, invariants, state machine)
- MUSH Architecture v0.1 (locked decisions, module layout, data flow)
- MUSH Python API v0.1 (Layer 1/2 binding spec, testing requirements)

**Related Documents:**
- `/home/kureshii/memogarden/mush/docs/mush-prd-v0_2.md`
- `/home/kureshii/memogarden/mush/docs/mush-architecture-v0_1.md`
- `/home/kureshii/memogarden/mush/docs/mush-python-api-v0_1.md`

---

## Implementation Strategy

Four milestones (M0-M3), each with explicit exit gates. M0 must pass completely before M1 begins; M1 before M2; etc.

### M0 — Engine Binding (Foundation)
**Goal:** Establish libghostty-vt C engine integration and verify binding contract.

**Deliverables:**
1. `vendor/libghostty-vt/` — pinned upstream commit
2. `vendor/PINNED_COMMIT` — single-line hash file
3. Static libraries for x86_64-linux and aarch64-linux
4. `mush/_ghostty/` Layer 1 binding (cffi, API mode)
5. `mush/vt/` Layer 2 Python API
6. VT stream fixture suite in `tests/vtstreams/`

**Critical Files:**
- `mush/_ghostty/build.py` — cffi API-mode builder
- `mush/_ghostty/cdefs.py` — C function declarations
- `mush/_ghostty/__init__.py` — exposes `ffi, lib`
- `mush/vt/terminal.py` — Terminal class
- `mush/vt/frames.py` — Frame, Cell, Style, Row dataclasses
- `mush/vt/errors.py` — GhosttyError hierarchy
- `vendor/PINNED_COMMIT` — upstream hash

**M0 Exit Gate (must pass):**
- Terminal round-trips all fixtures (text matches golden output)
- Device-query fixtures trigger `on_write_pty` callbacks
- Reentrancy contract test passes (callback fires inside write())
- Pin verification test passes (engine_info matches PINNED_COMMIT)
- Close/after-close tests pass (GhosttyStateError raised)
- Property tests for all Terminal methods

**Verification:**
```bash
cd mush
pytest tests/unit/test_vt.py -v
pytest tests/unit/test_frames.py -v
pytest tests/fixtures/test_roundtrip.py -v
```

---

### M1 — Daemon Core (Session Management)
**Goal:** Implement PTY hosting, control arbitration, event logging, and observation layer.

**Deliverables:**
1. `mush/daemon/pty_host.py` — PTY spawn, fd I/O, termios sampling
2. `mush/daemon/arbiter.py` — control state machine
3. `mush/daemon/inference.py` — input intent inference from termios/engine
4. `mush/daemon/observation.py` — frame derivation, debounce, diff, digest
5. `mush/daemon/eventlog.py` — SQLite append-only log
6. `mush/daemon/session.py` — composes all modules per process tree
7. `mush/daemon/server.py` — asyncio entrypoint, Unix socket + FastAPI mount
8. `mush/daemon/blacklist.py` — behavior-based blacklist rules
9. Property test suite (Hypothesis) for Arbiter transitions
10. Failure-injection tests for all Extension failure classes
11. Replay tool (`mush replay <session_id>`)

**Critical Files:**
- `mush/daemon/arbiter.py` — state machine (pure function + wrapper)
- `mush/daemon/inference.py` — infer(termios, modes) → InferredMode
- `mush/daemon/eventlog.py` — append(event), append_frame(event_id, frame)
- `mush/daemon/session.py` — Session class composing PtyHost, Terminal, Arbiter, Observation, EventLog
- `tests/integration/test_arbiter_transitions.py` — Hypothesis property tests
- `tests/integration/test_failure_injection.py` — I/O, state, agent, policy failures

**M1 Exit Gate (must pass):**
- Invariant test suite green (all PRD §7 invariants encoded as assertions)
- Replay reconstructs sessions byte-exactly from cold DB
- Memory under 100MB with one idle session
- Arbiter transition property tests pass (exhaustive state/event/actor coverage)
- Failure-injection tests for all four failure classes
- Log-ordering tests (events before side effects)

**Verification:**
```bash
cd mush
pytest tests/unit/test_arbiter.py -v
pytest tests/unit/test_inference.py -v
pytest tests/unit/test_eventlog.py -v
pytest tests/integration/test_session.py -v
pytest tests/properties/test_arbiter_transitions.py -v
pytest tests/integration/test_failure_injection.py -v
python -m mush.cli replay test_session_id
```

---

### M2 — Stage 1 Frontend (TUI Client)
**Goal:** Implement Unix socket protocol and terminal UI client.

**Deliverables:**
1. Unix socket protocol (length-prefixed msgpack)
2. `mush/daemon/server.py` — Unix socket server
3. `mush/client/tui.py` — terminal client
4. Manual acceptance script

**Critical Files:**
- `mush/daemon/server.py` — msgpack protocol handler, frame fanout
- `mush/client/tui.py` — stdin capture, stdout rendering, chrome status line
- `tests/acceptance/test_manual_workflow.py` — shell, vim, sudo password prompt, blacklist

**M2 Exit Gate (manual acceptance):**
- Shell session works (bash prompt, commands)
- Vim session works (enter/edit/quit)
- Sudo password prompt works (SECRET mode: agent locked out, content redacted)
- Blacklist-triggering program works (PASSTHROUGH + alert)
- PRD §23 success criteria demonstrable interactively

**Verification:**
```bash
cd mush
python -m mush.daemon.server &
python -m mush.client.tui
# Run manual acceptance script interactively
```

---

### M3 — Agent Frontend (MCP Server)
**Goal:** Implement MCP server exposing tools over sessions.

**Deliverables:**
1. `mush/daemon/mcp_frontend.py` — MCP server
2. MCP tool implementations
3. OBSERVE_APPROVE end-to-end test

**Critical Files:**
- `mush/daemon/mcp_frontend.py` — MCP server, tools:
  - `mush_list_sessions()`
  - `mush_read_screen(session_id, format)`
  - `mush_read_events(session_id, since_event_id, limit)`
  - `mush_declare_intent(session_id, text)`
  - `mush_send_input(session_id, kind, data)`
  - `mush_request_control(session_id)`
  - `mush_yield_control(session_id)`
- `tests/integration/test_mcp_workflow.py` — end-to-end OBSERVE_APPROVE test

**M3 Exit Gate (must pass):**
- Agent declares intent, proposes input
- Operator approves, action executes
- Full trail recoverable from log alone
- Out-of-turn injection test → failure path verified

**Verification:**
```bash
cd mush
pytest tests/integration/test_mcp_workflow.py -v
# Run real agent test with MCP client
```

---

## Implementation Order

### Phase 1: Foundation (M0)
1. Pin libghostty-vt upstream commit
2. Build static libraries for both architectures
3. Implement Layer 1 cffi binding
4. Implement Layer 2 data types (Cell, Style, Row, Frame, etc.)
5. Implement Terminal class with callbacks
6. Create VT stream fixture suite
7. Write and pass M0 exit gate tests

### Phase 2: Daemon Core (M1)
1. Implement PtyHost (PTY spawn, fd I/O, termios sampling)
2. Implement inference module (infer termios/engine modes)
3. Implement EventLog (SQLite append-only)
4. Implement Arbiter state machine (pure function + wrapper)
5. Implement Observation Layer (frame derivation, debounce, diff)
6. Implement Session (compose all modules)
7. Implement daemon server entrypoint (asyncio)
8. Implement blacklist module
9. Write replay tool
10. Write and pass M1 exit gate tests

### Phase 3: Stage 1 Frontend (M2)
1. Implement Unix socket msgpack protocol in daemon
2. Implement TUI client (stdin capture, stdout rendering, chrome)
3. Write manual acceptance script
4. Pass M2 manual exit gate

### Phase 4: Agent Frontend (M3)
1. Implement MCP server
2. Implement all MCP tools
3. Write OBSERVE_APPROVE end-to-end test
4. Write out-of-turn injection test
5. Pass M3 exit gate

---

## Architecture Constraints (Non-Negotiable)

1. **Dependency Rule** (enforced by import-linter):
   - `daemon`, `client`, `web` → may import → `mush.vt`
   - `mush.vt` → may import → `mush._ghostty`
   - `mush._ghostty` → NO upward imports
   - No module imports `mush._ghostty` directly except `mush.vt`

2. **PTY Ownership**: Only `PtyHost` owns the PTY master fd. No other module writes to PTY directly.

3. **Arbiter Mediation**: All input goes through Arbiter. `on_write_pty` callback bytes are enqueued as system-actor writes, never written inline.

4. **Event Log Ordering**: Events written BEFORE side effects. `input_injected` committed before PTY write; `output_emitted` committed before frontend send.

5. **No UPDATE/DELETE**: EventLog uses only INSERT. No UPDATE or DELETE statements anywhere in codebase.

6. **Immutable Frames**: Terminal.snapshot() returns new frozen Frame objects. No cached mutable state.

7. **Error Translation**: Every non-OK GhosttyResult becomes GhostError subclass. No silent failures.

8. **Four Things That Must Not Happen**:
   - Session management in `mush.vt`
   - PTY fd writes anywhere but Arbiter path
   - UPDATE or DELETE in `eventlog.py`
   - Automatic recovery from any failure class

---

## Testing Strategy

**Unit Tests:** Each module tested in isolation. Pure functions (inference, arbiter transition) get exhaustive property tests.

**Integration Tests:** Session composition, daemon lifecycle, protocol handlers.

**Property Tests (Hypothesis):** Arbiter transition function, inference function, frame diff/digest.

**Failure Injection Tests:** All four failure classes (I/O, state, agent, policy).

**VT Stream Fixtures:** Real PTY byte streams captured from bash, vim, htop, sudo, apt install.

**Manual Acceptance:** Interactive terminal sessions for M2 exit gate.

**End-to-End Agent Test:** Real MCP client for M3 exit gate.

---

## Dependencies

**Runtime:**
- Python 3.12+
- cffi
- fastapi
- uvicorn[standard]
- msgpack
- pytest
- hypothesis

**Build:**
- Zig (for libghostty-vt static library)
- cffi compiler

**Vendored:**
- libghostty-vt source or prebuilt static libs
- ghostty-web (M4 milestone, deferred)

---

## Deferred Scope (Post-MVP)

- M4: Stage 2 browser frontend (ghostty-web, WebSocket, replay viewer)
- Log compaction (offline post-session tool)
- AUI projection (MemoGarden integration layer)
- Multi-agent per session
- Structured key/mouse encoding via VT engine encoders
- Scrollback in Frame (formatter covers it)
- MG substrate migration (frames→Soil, toolcalls→Action/ActionResult)

---

## Success Criteria

From PRD §23:

- No action occurs without being attributable, observable, and replayable
- Operators always know who acted and why an action was proposed
- Agent mistakes are fully traceable from event log alone
- Blacklist decisions are explainable and operator-controlled
- Failures halt sessions cleanly and visibly with no automatic recovery
- Replay from cold database reconstructs sessions byte-exactly

Implementation-specific:
- Replay-from-cold-DB byte-exactness
- Pin-upgrade exercise passes (only `vendor/` + `_ghostty/` change)
- Memory under 100MB with 10 idle sessions and one active `htop`

---

## Next Steps

**Immediate:** Start M0 implementation by:
1. Clone and pin libghostty-vt upstream commit
2. Set up `vendor/` directory structure
3. Write `PINNED_COMMIT` file
4. Begin Layer 1 cffi binding implementation

---

**Revision History:**

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-06-11 | Initial implementation plan based on PRD v0.2, Architecture v0.1, Python API v0.1 |
