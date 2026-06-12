"""
Terminal class for mush.vt Layer 2.

Pure screen-state oracle over libghostty-vt. Owns NO PTY, spawns NO process,
knows NOTHING of sessions, agents, arbitration, or MUSH.

Input: bytes from a PTY master. Output: immutable Frame snapshots,
formatted strings, and device-reply callbacks.
"""

from __future__ import annotations

import time
from typing import Callable

from mush._ghostty import ffi, lib
from mush.vt.errors import GhosttyError, GhosttyStateError, check_result
from mush.vt.frames import (
    Cell,
    CursorState,
    Frame,
    Row,
    Style,
    TerminalModes,
)


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

        Args:
            cols: Number of columns
            rows: Number of rows
            scrollback: Maximum number of lines to keep in scrollback history

        Raises:
            GhosttyAllocationError: if the engine cannot allocate
            GhosttyEngineMismatch: if build_info does not match PINNED_COMMIT
        """
        if cols <= 0 or rows <= 0:
            raise ValueError(f"cols and rows must be positive, got cols={cols}, rows={rows}")

        self._seq = 0
        self._closed = False

        # Initialize options
        opts = ffi.new("GhosttyTerminalOptions*")
        opts.cols = cols
        opts.rows = rows
        opts.max_scrollback = scrollback

        # Create terminal
        terminal_ptr = ffi.new("GhosttyTerminal*")
        alloc = lib.ghostty_allocator_default()
        result = lib.ghostty_terminal_new(alloc, terminal_ptr, opts[0])
        check_result(result, "ghostty_terminal_new")

        self._terminal = terminal_ptr[0]
        self._cols = cols
        self._rows = rows

        # Callbacks (set by daemon)
        self.on_write_pty: Callable[[bytes], None] | None = None
        self.on_title_changed: Callable[[str], None] | None = None
        self.on_pwd_changed: Callable[[str], None] | None = None
        self.on_bell: Callable[[], None] | None = None

        # Set up userdata pointer for callbacks
        self._userdata = ffi.new_handle(self)

        # Configure callbacks
        self._setup_callbacks()

    def _setup_callbacks(self) -> None:
        """Configure C callbacks to route to Python methods."""

        @ffi.callback("GhosttyTerminalWritePtyFn")
        def write_pty_callback(terminal, userdata, data, len):
            try:
                py_self = ffi.from_handle(userdata)
                bytes_data = ffi.unpack(data, len)
                if py_self.on_write_pty:
                    py_self.on_write_pty(bytes_data)
            except Exception as e:
                # Will be caught and wrapped in GhosttyCallbackError
                raise

        @ffi.callback("GhosttyTerminalTitleChangedFn")
        def title_changed_callback(terminal, userdata, title):
            try:
                py_self = ffi.from_handle(userdata)
                title_str = ffi.string(title).decode("utf-8") if title else ""
                if py_self.on_title_changed:
                    py_self.on_title_changed(title_str)
            except Exception as e:
                raise

        @ffi.callback("GhosttyTerminalPwdChangedFn")
        def pwd_changed_callback(terminal, userdata, pwd):
            try:
                py_self = ffi.from_handle(userdata)
                pwd_str = ffi.string(pwd).decode("utf-8") if pwd else ""
                if py_self.on_pwd_changed:
                    py_self.on_pwd_changed(pwd_str)
            except Exception as e:
                raise

        @ffi.callback("GhosttyTerminalBellFn")
        def bell_callback(terminal, userdata):
            try:
                py_self = ffi.from_handle(userdata)
                if py_self.on_bell:
                    py_self.on_bell()
            except Exception as e:
                raise

        # Store callbacks to prevent garbage collection
        self._write_pty_cb = write_pty_callback
        self._title_changed_cb = title_changed_callback
        self._pwd_changed_cb = pwd_changed_callback
        self._bell_cb = bell_callback

        # Register callbacks with terminal
        lib.ghostty_terminal_set(self._terminal, lib.GHOSTTY_TERMINAL_OPT_USERDATA, self._userdata)
        lib.ghostty_terminal_set(self._terminal, lib.GHOSTTY_TERMINAL_OPT_WRITE_PTY, write_pty_callback)
        lib.ghostty_terminal_set(self._terminal, lib.GHOSTTY_TERMINAL_OPT_TITLE_CHANGED, title_changed_callback)
        lib.ghostty_terminal_set(self._terminal, lib.GHOSTTY_TERMINAL_OPT_PWD_CHANGED, pwd_changed_callback)
        lib.ghostty_terminal_set(self._terminal, lib.GHOSTTY_TERMINAL_OPT_BELL, bell_callback)

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Release the engine handle.

        Idempotent. After close(), all methods except close() and
        __enter__/__exit__ raise GhosttyStateError.
        """
        if not self._closed:
            lib.ghostty_terminal_free(self._terminal)
            self._closed = True

    def __enter__(self) -> "Terminal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def reset(self) -> None:
        """Soft reset: clear screen, reset cursor and modes.

        Does not resize.
        """
        self._check_closed()
        lib.ghostty_terminal_reset(self._terminal)

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

        Args:
            data: Raw PTY bytes to write

        Raises:
            GhosttyStateError: if called after close()
            GhosttyCallbackError: if a Python callback raises
        """
        self._check_closed()

        try:
            lib.ghostty_terminal_vt_write(self._terminal, data, len(data))
        except Exception as e:
            # Check if this is a callback error
            if "callback" in str(e).lower():
                from mush.vt.errors import GhosttyCallbackError

                # Re-raise as callback error if not already
                if not isinstance(e, GhosttyCallbackError):
                    raise GhosttyCallbackError(e) from e
                raise
            # Otherwise re-raise as-is
            raise

    def resize(self, cols: int, rows: int) -> None:
        """Update engine viewport dimensions.

        Does not send SIGWINCH or TIOCSWINSZ — the caller (PtyHost) owns
        the PTY and is responsible for both. Should be called after the
        PTY resize, not before.

        Args:
            cols: New number of columns
            rows: New number of rows
        """
        self._check_closed()
        if cols <= 0 or rows <= 0:
            raise ValueError(f"cols and rows must be positive, got cols={cols}, rows={rows}")

        result = lib.ghostty_terminal_resize(self._terminal, cols, rows, 8, 16)
        check_result(result, "ghostty_terminal_resize")
        self._cols = cols
        self._rows = rows

    # -- State reads ---------------------------------------------------------

    def snapshot(self) -> Frame:
        """Return an immutable snapshot of the current viewport.

        MVP implementation: full grid walk, O(cols * rows). The contract
        permits a future incremental backend (render-state diff API); callers
        MUST NOT depend on this being fast. The Observation Layer is responsible
        for debouncing; do not call snapshot() on every write().

        The returned Frame is fully independent of the engine — it will not
        change when the next write() is called.

        Returns:
            Immutable Frame snapshot

        Raises:
            GhosttyStateError: if called after close()
        """
        self._check_closed()
        self._seq += 1

        # Get grid reference
        grid_ref_ptr = ffi.new("GhosttyGridRef*")
        result = lib.ghostty_terminal_grid_ref(self._terminal, grid_ref_ptr)
        check_result(result, "ghostty_terminal_grid_ref")
        grid_ref = grid_ref_ptr[0]

        try:
            # Extract grid data
            grid_rows = []
            styles_map: dict[int, Style] = {}

            for row_idx in range(self._rows):
                # Get row info
                row_info_ptr = ffi.new("GhosttyRowInfo*")
                result = lib.ghostty_grid_ref_row(grid_ref, row_idx, row_info_ptr)
                check_result(result, f"ghostty_grid_ref_row({row_idx})")
                row_info = row_info_ptr[0]

                cells = []
                for col_idx in range(self._cols):
                    # Get cell
                    cell_ptr = ffi.new("GhosttyCell*")
                    result = lib.ghostty_grid_ref_cell(grid_ref, row_idx, col_idx, cell_ptr)
                    check_result(result, f"ghostty_grid_ref_cell({row_idx}, {col_idx})")
                    cell_data = cell_ptr[0]

                    # Convert to Python Cell (simplified for MVP)
                    cell = Cell(char="?", width=1, style_id=0)
                    cells.append(cell)

                row = Row(cells=tuple(cells), wrapped=row_info.soft_wrapped)
                grid_rows.append(row)

            # Get cursor state
            cursor = CursorState(row=0, col=0, visible=True, shape="block")

            # Get modes
            modes = self._read_modes()

            # Get title and pwd
            title = None  # Would need to call ghostty_terminal_get
            pwd = None  # Would need to call ghostty_terminal_get

            return Frame(
                seq=self._seq,
                timestamp_mono=time.monotonic(),
                cols=self._cols,
                rows=self._rows,
                grid=tuple(grid_rows),
                styles=styles_map,
                cursor=cursor,
                modes=modes,
                title=title,
                pwd=pwd,
            )

        finally:
            lib.ghostty_grid_ref_free(grid_ref)

    def _read_modes(self) -> TerminalModes:
        """Read terminal mode flags."""
        mode_names = [
            (lib.GHOSTTY_MODE_ALTERNATE_SCREEN, "alt_screen"),
            (lib.GHOSTTY_MODE_BRACKETED_PASTE, "bracketed_paste"),
            (lib.GHOSTTY_MODE_MOUSE_TRACKING, "mouse_reporting"),
            (lib.GHOSTTY_MODE_APPLICATION_CURSOR_KEYS, "application_cursor"),
        ]

        mode_values = {}
        for mode_c, name in mode_names:
            out_ptr = ffi.new("bool*")
            result = lib.ghostty_terminal_mode_get(self._terminal, mode_c, out_ptr)
            check_result(result, f"ghostty_terminal_mode_get({name})")
            mode_values[name] = out_ptr[0]

        return TerminalModes(
            alt_screen=mode_values["alt_screen"],
            bracketed_paste=mode_values["bracketed_paste"],
            mouse_reporting=mode_values["mouse_reporting"],
            application_cursor=mode_values["application_cursor"],
            echo=None,  # Not available in standalone mush.vt
        )

    def format_text(self, *, include_scrollback: bool = False) -> str:
        """Plain-text dump of the viewport (or viewport + scrollback).

        Convenience wrapper over the ghostty formatter.

        Args:
            include_scrollback: Whether to include scrollback

        Returns:
            Plain text representation
        """
        # For MVP, just use snapshot().text()
        # Full implementation would use ghostty_formatter
        return self.snapshot().text()

    def format_vt(self) -> bytes:
        """Re-encode current screen state as VT escape sequences.

        Used by the Frontend Server to send the current screen to a new
        terminal-protocol client (Stage 1 TUI attach, Stage 2 WebSocket attach).
        Not suitable as a high-frequency streaming format — use frame_vt events
        from the Observation Layer instead.

        Returns:
            VT escape sequences
        """
        # MVP placeholder
        return b""

    def format_html(self) -> str:
        """HTML rendering of current screen, suitable for replay viewers.

        Returns:
            HTML representation
        """
        # MVP placeholder
        return ""

    def modes(self) -> TerminalModes:
        """Current terminal mode flags.

        Lightweight — does not snapshot the grid.

        Returns:
            TerminalModes object
        """
        self._check_closed()
        return self._read_modes()

    @property
    def title(self) -> str | None:
        """Current OSC window title. None if not set."""
        # MVP placeholder
        return None

    @property
    def pwd(self) -> str | None:
        """Current OSC 7 working directory URI. None if not set."""
        # MVP placeholder
        return None

    @property
    def cols(self) -> int:
        """Current number of columns."""
        return self._cols

    @property
    def rows(self) -> int:
        """Current number of rows."""
        return self._rows

    @property
    def closed(self) -> bool:
        """Whether the terminal has been closed."""
        return self._closed

    # -- Utilities -----------------------------------------------------------

    @staticmethod
    def paste_is_safe(data: bytes) -> bool:
        """Returns False if data contains sequences that could be dangerous in
        bracketed-paste mode (e.g. embedded escape sequences that might be
        interpreted as commands). Wrapper over ghostty_paste_is_safe.

        Args:
            data: Data to check

        Returns:
            True if paste is safe, False otherwise
        """
        return bool(lib.ghostty_paste_is_safe(data, len(data)))

    @staticmethod
    def engine_info() -> dict:
        """Returns build-time feature flags from ghostty_build_info:
        pinned commit hash, SIMD support, Kitty graphics support, tmux
        control mode support. Used for pin verification and diagnostics.

        Returns:
            Dictionary with engine build info
        """
        # Get commit hash
        hash_str_ptr = ffi.new("GhosttyString*")
        result = lib.ghostty_build_info(lib.GHOSTTY_BUILD_INFO_COMMIT_HASH, hash_str_ptr)
        check_result(result, "ghostty_build_info")

        hash_bytes = ffi.unpack(hash_str_ptr[0].ptr, hash_str_ptr[0].len)
        commit = hash_bytes.decode("utf-8") if hash_bytes else ""

        return {"commit": commit}

    # -- Internal helpers -----------------------------------------------------

    def _check_closed(self) -> None:
        """Raise GhosttyStateError if terminal is closed."""
        if self._closed:
            raise GhosttyStateError(0, "Terminal is closed")
