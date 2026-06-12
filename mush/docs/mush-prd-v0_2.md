# MUSH PRD v0.2

**Status:** Approved for implementation
**Supersedes:** MUSH PRD v0.1 + MUSH PRD Extension (both retired; all content folded in)
**Related:** MUSH Architecture and Python API v0.1 (separate document)

---

## 1. Problem Statement

Traditional terminals are hostile to agents due to blocking input, implicit state, and byte-stream I/O. Agents cannot safely or predictably share control with humans. Goal: enable human–agent joint operation over existing CLI programs, without modifying those programs.

---

## 2. Product Shape

A PTY daemon with multiple frontends. The daemon exclusively owns all PTYs and session state. Frontends:

- Interactive terminal UI for the operator (Stage 1: TUI client; Stage 2: browser)
- Agent access via MCP tools
- Replay / audit viewer (deferred)

All representations derive from the same authoritative session state.

---

## 3. Goals

- Support common interactive CLI workflows
- Humans and agents see identical output and session state
- Explicit single-controller model (human or agent) at all times
- Works with existing Unix programs via PTY
- Maintain high development velocity
- All actions attributable, observable, and replayable

---

## 4. Non-Goals

- Perfect compatibility with all legacy or pathological TUI programs
- Replacing shells, editors, multiplexers, or debuggers
- Competing as a power-user shell environment
- Full semantic understanding of arbitrary CLI programs
- Detecting or judging agent intent or correctness
- Native GUI of any kind

---

## 5. Target Users

Operators using AI copilots in the terminal; developers running interactive build, test, and deployment tools; ops/infra workflows with human-in-the-loop automation.

---

## 6. Core Principles

- Terminal is a session controller, not a byte pipe
- Input intent is inferred, not declared by programs
- Conservative inference beats clever automation
- All control transitions are explicit, visible, and logged
- Safety and traceability take precedence over convenience

---

## 7. System Invariants (Non-Negotiable)

These must hold under all execution paths. Violations indicate a system bug, not a policy exception.

**Authority and control**
- At most one controller (human or agent) may inject input at any time.
- All controller changes must be explicit, logged, and human-visible.
- Automatic transitions may only reduce agent authority, never increase it.
- Only a human may initiate transitions that increase agent authority.

**Input and output integrity**
- No input may be injected without an active controller.
- No output may be rendered to any frontend without first being logged.
- No output may be dropped silently; truncation must be explicit and logged.

**Isolation and trust**
- Remote agents never interact with PTYs directly.
- All agent actions must pass through the control arbiter.
- Agent-declared intent is advisory only and must not gate safety decisions.

**Failure discipline**
- On ambiguity or inconsistency, the system must degrade to a less automated state.
- Recovery from failure always requires explicit human action.
- No automatic retries. No silent recovery.

---

## 8. Explicit Relaxation: Program Blacklisting

The terminal may blacklist programs that violate shared-control or observability assumptions. Blacklisting is behavior-based, not name-based.

**Hard criteria (immediate blacklist):**
- Reads directly from `/dev/tty` or bypasses the PTY
- Spawns or manages its own PTY tree
- Assumes exclusive, uninterrupted terminal control
- Uses non-reversible or opaque termios state transitions

**Soft criteria (escalates to blacklist):**
- Aggressive full-screen redraws with raw input
- Non-deterministic or timing-sensitive I/O behavior
- Undefined or hostile ANSI escape usage
- Unbounded interactive loops without detectable prompts
- Excessive output frequency or memory footprint

**Policy:**
- No program-specific plugins or deep semantic adapters
- Blacklist triggers always alert the operator
- Overrides require explicit operator acknowledgment
- Blacklisted programs run in human-only PASSTHROUGH mode or are explicitly unsupported

---

## 9. Session State Machine

**States:** `HUMAN_ACTIVE`, `AGENT_ACTIVE`, `OBSERVE_ONLY`, `PASSTHROUGH`

**Operating modes (orthogonal):** `OBSERVE_APPROVE` (agent proposes; operator edits or approves), `AUTONOMOUS_BOUNDED` (agent acts within pre-approved capability bounds)

**Transition table:**

| From | Event | To | Initiator |
|------|-------|----|-----------|
| HUMAN_ACTIVE | operator handoff | AGENT_ACTIVE | human only |
| HUMAN_ACTIVE | operator lock | OBSERVE_ONLY | human |
| AGENT_ACTIVE | operator reclaim | HUMAN_ACTIVE | human |
| AGENT_ACTIVE | agent yield | HUMAN_ACTIVE | agent (authority-reducing) |
| AGENT_ACTIVE | any failure class | OBSERVE_ONLY | system (automatic) |
| any | hard blacklist trigger | PASSTHROUGH | system (automatic) |
| OBSERVE_ONLY | operator resume | HUMAN_ACTIVE | human only |
| PASSTHROUGH | operator override (explicit ack) | HUMAN_ACTIVE | human only |

Agents may never: change operating mode, override blacklists, or escalate privileges.

---

## 10. Input Handling

Structured input event types: keystrokes, paste, signals (SIGINT, SIGTERM, etc.), resize. Unified input model for human and agent. Hard control lock during SECRET-inferred mode and any unresolved ambiguity.

---

## 11. Input Intent Inference

Inferred from two sources (no program-side changes required):

- **termios state:** canonical vs raw, echo on/off, VMIN/VTIME (sampled by PtyHost on read-burst boundaries and on a polling interval)
- **Engine state:** alt-screen, mouse reporting, bracketed paste, cursor visibility (from `Terminal.modes()`)

**Inferred modes:** `LINE`, `SECRET` (echo off + canonical), `SINGLE_KEY` (raw VMIN=1), `FULL_INTERACTIVE` (alt-screen and/or mouse active), `NON_INTERACTIVE`

Inference failures always degrade safely: hard control lock while ambiguous; escalation to the failure path if ambiguity persists. Every mode change is logged and pushed to all frontends (visibility guarantee §14).

**SECRET mode:** agent injection locked out regardless of controller state; input event content redacted in log (byte length and metadata only).

---

## 12. Output Handling

- PTY output captured by PtyHost, fed to the VT engine
- Engine maintains authoritative terminal state (parsed grid, cursor, modes, title, pwd)
- Normalized output frames derived by the Observation Layer on a debounced cadence (coalesce within 16 ms of last write; hard cap 50 ms)
- Identical frame content for human and agent frontends — one observation layer, N renderers
- Raw PTY byte streams are not exposed to agents

---

## 13. Control Arbitration and Consent

Only one controller may inject input at any time. The operator is the ultimate authority.

**OBSERVE_APPROVE mode:** agent proposes actions; operator edits or approves before injection.
**AUTONOMOUS_BOUNDED mode:** agent may act within pre-approved capability bounds; operator can reclaim at any time.

Device-query replies (DA, cursor position report, size reports) generated by the VT engine are mediated as **system-actor** writes — logged with `actor=system`, routed through the arbiter, attributable to the system rather than any human or agent.

---

## 14. Failure Taxonomy

For any failure in the following classes, the required response is: (1) freeze all input injection, (2) transition session to OBSERVE_ONLY (or PASSTHROUGH for hard blacklist), (3) emit structured `failure_detected` event, (4) await explicit human decision.

**I/O and PTY failures:** PTY read stall or unexpected EOF; PTY write error (EIO, EPIPE); PTY desynchronization.

**Terminal state failures:** unexpected or unsupported termios transition; undecodable or malformed escape sequence; output buffer overflow or unbounded growth.

**Agent interaction failures:** agent toolcall error; agent attempts input while not in control; remote agent timeout or disconnect mid-action.

**Policy and safety failures:** blacklist rule violation; inference confidence drops below safe threshold.

---

## 15. Visibility Guarantees

The operator always has visibility into: current controller, inferred input mode, operating mode, blacklist status, agent-declared intent (advisory), and source of the last action (human / agent / system). These six fields are pushed to all frontends on every change.

---

## 16. Event Log

Append-only SQLite (WAL mode). Schema:

```sql
CREATE TABLE events (
  event_id        INTEGER PRIMARY KEY,
  timestamp_wall  TEXT    NOT NULL,
  timestamp_mono  REAL    NOT NULL,
  session_id      TEXT    NOT NULL,
  actor           TEXT    NOT NULL CHECK (actor IN ('human','agent','system')),
  event_type      TEXT    NOT NULL,
  payload         BLOB    NOT NULL
);

CREATE TABLE frames (
  frame_id    INTEGER PRIMARY KEY,
  event_id    INTEGER NOT NULL REFERENCES events(event_id),
  session_id  TEXT    NOT NULL,
  seq         INTEGER NOT NULL,
  digest      TEXT    NOT NULL,
  encoding    TEXT    NOT NULL CHECK (encoding IN ('full','vt-delta')),
  body        BLOB    NOT NULL
);
```

**Event types:** `session_start`, `session_end`, `state_transition`, `controller_change`, `approval_granted`, `approval_denied`, `input_injected`, `output_emitted`, `intent_declared`, `toolcall_received`, `toolcall_completed`, `failure_detected`, `blacklist_triggered`.

**Ordering guarantees:** events written before side effects; `input_injected` committed before PTY write; `output_emitted` (with frame row) committed before frontend send. Payloads size-bounded; truncation explicit and logged. No UPDATE or DELETE statements exist in the codebase. Compaction is an offline post-session tool (deferred).

---

## 17. Security and Secrets

- **In scope:** secrets required for daemon operation (session tokens, MCP credentials)
- **Out of scope:** agent-internal secrets; environment secrets within the PTY (env vars, SSH keys)
- **SECRET mode:** content of input events redacted from log during inferred password entry; byte length and metadata preserved
- Environment mutations may be observable without revealing secret values

---

## 18. Agent Interface

Agents interact exclusively via MCP tools. Raw PTY byte streams are not exposed. The agent sees the same frames the human sees.

**Tools:**
- `mush_list_sessions()` → session list with controller/mode/blacklist status
- `mush_read_screen(session_id, format)` → text or structured Frame-JSON snapshot
- `mush_read_events(session_id, since_event_id, limit)` → bounded event history
- `mush_declare_intent(session_id, text)` → advisory only; logged as `intent_declared`
- `mush_send_input(session_id, kind, data)` → subject to arbiter; out-of-turn attempts trigger failure path
- `mush_request_control(session_id)` → requires operator approval
- `mush_yield_control(session_id)` → immediate (authority-reducing)

Every tool call is logged as `toolcall_received` / `toolcall_completed`.

*Note:* AUI projection of frames into RFC-010 view syntax is a MemoGarden-side rendering layer applied at MG integration. Not in MVP scope.

---

## 19. Human Frontends

**Stage 1 — TUI client:** operator's own terminal emulator renders; daemon re-emits session grid as VT sequences over Unix socket; client renders a status bar (chrome fields) and captures operator input in raw mode; prefix key maps to control actions (handoff, reclaim, lock, approve, deny, detach). Validates the full protocol and arbiter with minimal rendering investment.

**Stage 2 — Browser:** FastAPI + WebSocket served by the daemon; ghostty-web (libghostty-vt compiled to WASM, canvas renderer) for terminal rendering; xterm.js remains a drop-in swap (use only ghostty-web's xterm-compatible API surface); chrome rendered as a header bar; approval dialogs as DOM elements; replay viewer over the event log. Ghostty-web WASM and JS vendored into the repo and served from disk — no CDN dependency at runtime. Same wire protocol as Stage 1; only transport and encoding differ.

---

## 20. Performance Envelope

- Human-perceived latency must remain acceptable for interactive use
- Total memory footprint < 100MB (daemon + active sessions)
- Maximum 10 concurrent sessions (MVP)

---

## 21. MVP Scope

Single daemon instance; TUI frontend (Stage 1); MCP agent frontend; single agent per session; shell and common CLI tools; manual control handoff; replay tool operating from cold DB.

---

## 22. Out of Scope

- Competing with tmux / screen
- Competing with vim / emacs
- Becoming a universal automation framework
- Supporting all ANSI or TUI edge cases
- Automated detection of agent misbehavior
- Native GUI of any kind
- Log compaction (deferred to post-MVP offline tool)
- AUI projection (MemoGarden integration layer)
- Multi-agent per session
- Structured key/mouse encoding via VT engine encoders
- MG substrate migration (frames→Soil, toolcalls→Action/ActionResult)

---

## 23. Success Criteria

The system is successful if:

- No action occurs without being attributable, observable, and replayable
- Operators always know who acted and why an action was proposed
- Agent mistakes are fully traceable from the event log alone
- Blacklist decisions are explainable and operator-controlled
- Failures halt sessions cleanly and visibly with no automatic recovery
- Replay from a cold database reconstructs sessions byte-exactly

Implementation-specific: (a) replay-from-cold-DB byte-exactness; (b) pin-upgrade exercise passes (only `vendor/` + `_ghostty/` change); (c) memory under 100MB with 10 idle sessions and one active `htop`.

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | (prior) | Original PRD |
| 0.1-ext | (prior) | PRD Extension: invariants, failure taxonomy, event log schema |
| 0.2 | 2026-06-11 | Consolidated v0.1 + Extension; replaced Letta agent interface (§18) with MCP tools; added Stage 2 browser frontend (§19); added session state machine transition table (§9); added SECRET-mode redaction rule (§17); added replay success criterion (§23) |
