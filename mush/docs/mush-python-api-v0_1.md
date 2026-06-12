# MUSH Python API v0.1

**Status:** Approved for implementation
**Audience:** Claude Code
**Covers:** `mush._ghostty` (Layer 1 binding) and `mush.vt` (Layer 2 Python API)
**Related:** MUSH PRD v0.2, MUSH Architecture v0.1

---

## 1. Overview

The Python API is two layers with a hard dependency rule: only `mush.vt` imports `mush._ghostty`. Nothing above `mush.vt` (daemon, frontends, tests) imports `_ghostty` directly.

**Layer 1 (`mush._ghostty`)** is a near-mechanical cffi binding to the libghostty-vt C API at a single pinned upstream commit. It is ugly on purpose — no Pythonic sugar, raw `GhosttyResult` ints, raw cdata. Its sole job is to be a faithful, swappable mirror of one commit. When upstream churns, only this layer changes.

**Layer 2 (`mush.vt`)** is the Python API the daemon codes against. It is a pure screen-state oracle: bytes in, terminal state out, device replies out via callbacks. It has zero knowledge of sessions, agents, arbitration, PTYs, or any MUSH concept.

---

## 2. Layer 1 — `mush._ghostty`

### 2.1 Structure

```
mush/
  _ghostty/
    build.py       # cffi API-mode builder; reads vendored vt.h subset
    cdefs.py       # cdef declarations, one pinned-commit snapshot
    __init__.py    # exposes: ffi, lib — nothing else
```

### 2.2 Build mechanism

cffi **API mode** (not ABI mode). `build.py` compiles an extension module against the vendored static library and header. If upstream signatures drift, the build fails loudly — this is the desired behavior. Silent ABI drift producing runtime segfaults is the failure mode we are preventing.

```python
# build.py (outline)
from cffi import FFI
ffi = FFI()
ffi.cdef(open("cdefs.h").read())          # subset of vt.h, manually maintained
ffi.set_source(
    "mush._ghostty._lib",
    '#include "ghostty/vt.h"',
    libraries=["ghostty-vt"],
    library_dirs=[str(VENDOR_LIB_DIR)],
    include_dirs=[str(VENDOR_INCLUDE_DIR)],
    extra_link_args=["-static-libgcc"],
)
```

### 2.3 Pinning

`vendor/PINNED_COMMIT` contains exactly one line: the upstream git commit hash. A `make verify-pin` target (or equivalent CI step) checks the hash of the vendored `vt.h` against the expected hash for that commit. Upgrading the pin is a deliberate, isolated commit touching only `vendor/` and (if needed) `_ghostty/cdefs.py`.

The vendored artifact is one of:
- A pinned source checkout with `zig build -Demit-lib-vt` producing the static lib, OR
- Prebuilt static libs for x86_64-linux-gnu and aarch64-linux-gnu, committed directly

Build target: `zig build -Demit-lib-vt -Dtarget=<arch> --prefix <out>`. Zig serves as the C compiler — it is already required to build libghostty-vt, no extra toolchain needed.

### 2.4 MVP binding scope

Bind only what Layer 2 consumes in MVP. Everything else is deferred.

**Terminal lifecycle:**
```c
ghostty_terminal_new(allocator, opts, &term)
ghostty_terminal_free(terminal)
ghostty_terminal_reset(terminal)
ghostty_terminal_resize(terminal, cols, rows, cell_width_px, cell_height_px)
ghostty_terminal_vt_write(terminal, data, len)
```

**State reads:**
```c
ghostty_terminal_get(terminal, field, out)
ghostty_terminal_get_multi(terminal, fields, count, out)
ghostty_terminal_mode_get(terminal, mode, out)
```

**Grid access:**
```c
ghostty_terminal_grid_ref(terminal, &ref)
ghostty_grid_ref_cell(ref, row, col, &cell)
ghostty_grid_ref_row(ref, row, &row_info)
ghostty_grid_ref_graphemes(ref, cell, &graphemes)
ghostty_grid_ref_style(ref, style_id, &style)
ghostty_cell_get(cell, field, out)
ghostty_row_get(row, field, out)
```

**Formatter:**
```c
ghostty_formatter_terminal_new(terminal, opts, &fmt)
ghostty_formatter_format_alloc(fmt, &buf, &len)
ghostty_formatter_format_buf(fmt, buf, buf_len, &written)
ghostty_formatter_free(fmt)
```

**Callbacks (all must be registered — child programs must never hang on unanswered queries):**
```c
typedef void (*GhosttyTerminalWritePtyFn)(GhosttyTerminal, void*, const uint8_t*, uintptr_t)
typedef void (*GhosttyTerminalTitleChangedFn)(GhosttyTerminal, void*, const char*)
typedef void (*GhosttyTerminalPwdChangedFn)(GhosttyTerminal, void*, const char*)
typedef void (*GhosttyTerminalBellFn)(GhosttyTerminal, void*)
typedef bool (*GhosttyTerminalSizeFn)(GhosttyTerminal, void*, ...)
typedef bool (*GhosttyTerminalDeviceAttributesFn)(GhosttyTerminal, void*, ...)
typedef GhosttyString (*GhosttyTerminalEnquiryFn)(GhosttyTerminal, void*)
```

**Utilities:**
```c
ghostty_paste_is_safe(data, len)
ghostty_build_info(field, out)
```

**Explicitly out of MVP scope:** key/mouse/focus encoders; kitty graphics; render-state / row-iterator APIs (reserved for D11 incremental-backend upgrade path); selection; wasm utilities; OSC/SGR standalone parsers.

### 2.5 `__init__.py` contract

Exposes exactly two names: `ffi` and `lib`. No helper functions. No error translation. Callers receive raw `GhosttyResult` integers and raw cdata. All niceness lives in Layer 2.

```python
# mush/_ghostty/__init__.py
from mush._ghostty._lib import ffi, lib  # noqa: F401
```

---

## 3. Layer 2 — `mush.vt`

### 3.1 Data types (`mush/vt/frames.py`)

All types are frozen dataclasses. `Frame` is the loggable artifact — immutable, inert, safe to hash, diff, serialize, and append to the event log. The prohibition on live handles in the public API is architectural: a live `GridRef` that mutates under the caller during the next `write()` is hidden state, incompatible with the zero-private-state discipline.

```python
@dataclass(frozen=True, slots=True)
class Cell:
    char: str              # grapheme cluster; "" for spacer cells
    width: int             # 0 (spacer tail), 1, or 2 (wide char lead)
    style_id: int          # index into Frame.styles; stable within one Frame
    hyperlink: str | None = None

@dataclass(frozen=True, slots=True)
class Style:
    fg: tuple[int, int, int] | int | None   # RGB tuple, 256-color index, or None (default)
    bg: tuple[int, int, int] | int | None
    bold: bool = False
    italic: bool = False
    underline: str | None = None   # None | "single" | "double" | "curly" | "dotted" | "dashed"
    inverse: bool = False
    strikethrough: bool = False
    faint: bool = False
    invisible: bool = False

@dataclass(frozen=True, slots=True)
class Row:
    cells: tuple[Cell, ...]
    wrapped: bool           # True if this row is a soft-wrap continuation of the previous

@dataclass(frozen=True, slots=True)
class CursorState:
    row: int
    col: int
    visible: bool
    shape: str              # "block" | "bar" | "underline"; blinking not tracked in MVP

@dataclass(frozen=True, slots=True)
class TerminalModes:
    alt_screen: bool
    bracketed_paste: bool
    mouse_reporting: bool
    application_cursor: bool
    # Note: echo state is a termios property owned by PtyHost, not the VT engine.
    # It is surfaced in Frame for visibility but populated by the daemon, not mush.vt.
    echo: bool | None = None  # None = not available (mush.vt standalone use)

@dataclass(frozen=True, slots=True)
class Frame:
    """Immutable snapshot of the terminal viewport. The loggable artifact.

    Never contains live engine handles. Callers MUST NOT assume snapshot() cost
    is O(1) — the contract allows a future incremental backend. Treat it as
    potentially O(cols * rows) and batch accordingly.
    """
    seq: int                        # monotonic counter, per Terminal instance
    timestamp_mono: float           # time.monotonic() at snapshot time
    cols: int
    rows: int
    grid: tuple[Row, ...]           # viewport rows only; len == rows
    styles: Mapping[int, Style]     # style_id → Style; keys are Cell.style_id values
    cursor: CursorState
    modes: TerminalModes
    title: str | None               # OSC 0/2 window title
    pwd: str | None                 # OSC 7 working directory URI

    def text(self) -> str:
        """Plain-text projection of the viewport. Respects wide chars and
        soft-wrapping (wrapped rows are not newline-separated)."""
        ...

    def digest(self) -> str:
        """Stable content hash (SHA-256 hex, first 16 chars). Used by the
        Observation Layer for deduplication — identical grid content produces
        identical digest regardless of seq or timestamp."""
        ...

    def diff(self, other: "Frame") -> "FrameDiff | None":
        """Returns None if frames are identical (by digest). Otherwise returns
        the set of changed rows. Callers may use this for vt-delta log encoding
        but are not required to."""
        ...

@dataclass(frozen=True, slots=True)
class FrameDiff:
    seq_from: int
    seq_to: int
    changed_rows: frozenset[int]    # row indices with differing cell content
    cursor_changed: bool
    modes_changed: bool
    title_changed: bool
```

### 3.2 Errors (`mush/vt/errors.py`)

Layer 2 translates every non-OK `GhosttyResult` into this hierarchy. Status-code checking never appears in daemon code.

```python
class GhosttyError(Exception):
    """Base. Carries the raw GhosttyResult integer as .code."""
    code: int

class GhosttyAllocationError(GhosttyError): ...
class GhosttyStateError(GhosttyError):
    """Invalid handle, use-after-close, or operation invalid in current state."""
    ...
class GhosttyEngineMismatch(GhosttyError):
    """build_info verification failed against PINNED_COMMIT expectations."""
    ...
class GhosttyCallbackError(GhosttyError):
    """Exception raised inside a Python callback propagated out of write()."""
    ...
```

### 3.3 `Terminal` (`mush/vt/terminal.py`)

This is the complete public interface. No other names from `mush.vt` are required by daemon code.

```python
class Terminal:
    """Pure screen-state oracle over libghostty-vt.

    Owns NO PTY, spawns NO process, knows NOTHING of sessions, agents,
    arbitration, or MUSH. Input: bytes from a PTY master. Output: immutable
    Frame snapshots, formatted strings, and device-reply callbacks.

    Thread safety: NOT thread-safe. All methods must be called from one thread
    (or one asyncio event loop task). The callbacks fire synchronously inside
    write() on that same thread.
    """

    def __init__(
        self,
        cols: int,
        rows: int,
        *,
        scrollback: int = 10_000,
    ) -> None:
        """Construct and initialize the VT engine.

        Raises GhosttyAllocationError if the engine cannot allocate.
        Raises GhosttyEngineMismatch if build_info does not match PINNED_COMMIT.
        """
        ...

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Release the engine handle. Idempotent. After close(), all methods
        except close() and __enter__/__exit__ raise GhosttyStateError."""
        ...

    def __enter__(self) -> "Terminal": ...
    def __exit__(self, *exc) -> None: ...

    def reset(self) -> None:
        """Soft reset: clear screen, reset cursor and modes. Does not resize."""
        ...

    # -- Input ---------------------------------------------------------------

    def write(self, data: bytes) -> None:
        """Feed raw PTY output bytes into the engine.

        Updates terminal state synchronously. Device-query callbacks (on_write_pty,
        on_bell, etc.) MAY fire synchronously during this call, on this thread,
        before write() returns.

        CRITICAL: on_write_pty fires inside write(). The handler MUST NOT write
        to a PTY fd inline. It must enqueue the reply bytes to the Arbiter for
        attribution and logging as a system-actor write. Inline fd writes bypass
        the arbiter and violate the attribution invariant.

        Raises GhosttyStateError if called after close().
        Raises GhosttyCallbackError if a Python callback raises.
        """
        ...

    def resize(self, cols: int, rows: int) -> None:
        """Update engine viewport dimensions. Does not send SIGWINCH or TIOCSWINSZ —
        the caller (PtyHost) owns the PTY and is responsible for both.
        Should be called after the PTY resize, not before."""
        ...

    # -- Device-response callbacks -------------------------------------------
    # These are the control seam between mush.vt and the daemon.
    # The daemon MUST register on_write_pty before calling write().
    # All other callbacks SHOULD be registered; unregistered callbacks are no-ops.

    on_write_pty: Callable[[bytes], None] | None
    """Called when the engine wants to reply to the child process (device
    attribute responses, cursor position reports, size reports, ENQ, etc.).

    The handler is called on the same thread as write(). It MUST enqueue
    reply bytes to the Arbiter, never write to a PTY fd directly.

    Set this before the first write(). Setting it to None after write() has
    been called is permitted but risks unanswered device queries."""

    on_title_changed: Callable[[str], None] | None
    """Called when the child sets the window title via OSC 0 or OSC 2."""

    on_pwd_changed: Callable[[str], None] | None
    """Called when the child sets the working directory via OSC 7."""

    on_bell: Callable[[], None] | None
    """Called on BEL (\\x07)."""

    # -- State reads ---------------------------------------------------------

    def snapshot(self) -> Frame:
        """Return an immutable snapshot of the current viewport.

        MVP implementation: full grid walk, O(cols * rows). The contract
        permits a future incremental backend (render-state diff API); callers
        MUST NOT depend on this being fast. The Observation Layer is responsible
        for debouncing; do not call snapshot() on every write().

        The returned Frame is fully independent of the engine — it will not
        change when the next write() is called.

        Raises GhosttyStateError if called after close().
        """
        ...

    def format_text(self, *, include_scrollback: bool = False) -> str:
        """Plain-text dump of the viewport (or viewport + scrollback).
        Convenience wrapper over the ghostty formatter."""
        ...

    def format_vt(self) -> bytes:
        """Re-encode current screen state as VT escape sequences.

        Used by the Frontend Server to send the current screen to a new
        terminal-protocol client (Stage 1 TUI attach, Stage 2 WebSocket attach).
        Not suitable as a high-frequency streaming format — use frame_vt events
        from the Observation Layer instead."""
        ...

    def format_html(self) -> str:
        """HTML rendering of current screen, suitable for replay viewers."""
        ...

    def modes(self) -> TerminalModes:
        """Current terminal mode flags. Lightweight — does not snapshot the grid."""
        ...

    @property
    def title(self) -> str | None:
        """Current OSC window title. None if not set."""
        ...

    @property
    def pwd(self) -> str | None:
        """Current OSC 7 working directory URI. None if not set."""
        ...

    @property
    def cols(self) -> int: ...
    @property
    def rows(self) -> int: ...
    @property
    def closed(self) -> bool: ...

    # -- Utilities -----------------------------------------------------------

    @staticmethod
    def paste_is_safe(data: bytes) -> bool:
        """Returns False if data contains sequences that could be dangerous in
        bracketed-paste mode (e.g. embedded escape sequences that might be
        interpreted as commands). Wrapper over ghostty_paste_is_safe."""
        ...

    @staticmethod
    def engine_info() -> dict:
        """Returns build-time feature flags from ghostty_build_info:
        pinned commit hash, SIMD support, Kitty graphics support, tmux
        control mode support. Used for pin verification and diagnostics."""
        ...
```

---

## 4. Dependency Rule

Enforced by import-linter or equivalent CI check:

```
mush.daemon.*  →  may import  →  mush.vt.*
mush.client.*  →  may import  →  mush.vt.*
mush.web.*     →  may import  →  mush.vt.*
mush.vt.*      →  may import  →  mush._ghostty
─────────────────────────────────────────────
mush._ghostty  →  NO upward imports
mush.vt        →  NO imports from mush.daemon, mush.client, mush.web
```

Violations are build failures.

---

## 5. Testing Requirements

### M0 gate (must pass before M1 begins)

**Fixture suite** (`tests/vtstreams/`): recorded byte streams captured from a real PTY session with each of: bash prompt, vim (enter/edit/quit), htop (open/resize/quit), `ls --color`, `sudo <cmd>` (password prompt → SECRET inference in the daemon, but the engine itself must not regress on these bytes), `apt install` (progress bar), a deliberately pathological stream (high-frequency redraws + raw input). Each fixture has a corresponding golden output file for text and VT-re-emission assertions.

**Engine round-trip tests:** for each fixture, `Terminal.write(stream)` then assert `snapshot().text()` matches golden; assert `format_vt()` output can be re-ingested into a fresh `Terminal` and produce an identical `snapshot().digest()`.

**Callback tests:** fixtures containing device queries (cursor position request, DA1, size report) must trigger `on_write_pty` with non-empty bytes; assert these callbacks fire and are non-empty.

**Reentrancy contract test:** verify that `on_write_pty` is called synchronously *inside* `write()` — i.e., a flag set by the callback is readable before `write()` returns. This is the contract the daemon's enqueueing logic depends on.

**Pin verification test:** `Terminal.engine_info()["commit"]` matches contents of `vendor/PINNED_COMMIT`.

**Close/after-close tests:** all state-read methods raise `GhosttyStateError` after `close()`.

---

## 6. What `mush.vt` Must Not Do

These are architectural constraints, not style preferences. Violations require design review, not just code review.

1. **No PTY ownership.** `mush.vt` never opens, reads, writes, or closes a file descriptor.
2. **No session concepts.** No notion of "session", "process", "controller", "agent", or "arbiter" anywhere in `mush.vt`.
3. **No inline PTY writes in callbacks.** `on_write_pty` must be a notification, not an fd write. The implementer provides the handler; `mush.vt` only calls it.
4. **No mutable frames.** Every method returning a `Frame` must return a new, independent frozen object. Returning cached mutable state is a bug.
5. **No exception swallowing.** Every non-OK `GhosttyResult` becomes a `GhosttyError` subclass. Results are not silently ignored.

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-06-11 | Initial: Layer 1 binding spec, full Layer 2 API with normative signatures, dependency rule, M0 test requirements |
