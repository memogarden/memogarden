"""
Immutable data types for terminal state snapshots.

All types are frozen dataclasses. Frame is the loggable artifact —
immutable, inert, safe to hash, diff, serialize, and append to the event log.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Cell:
    """A single cell in the terminal grid.

    Attributes:
        char: Grapheme cluster; "" for spacer cells
        width: 0 (spacer tail), 1, or 2 (wide char lead)
        style_id: Index into Frame.styles; stable within one Frame
        hyperlink: Optional hyperlink URL
    """

    char: str
    width: int
    style_id: int
    hyperlink: str | None = None


@dataclass(frozen=True, slots=True)
class Style:
    """Text styling attributes.

    Attributes:
        fg: RGB tuple, 256-color index, or None (default)
        bg: RGB tuple, 256-color index, or None
        bold: Bold text
        italic: Italic text
        underline: Underline style
        inverse: Inverse/reverse video
        strikethrough: Strikethrough text
        faint: Faint/dim text
        invisible: Invisible text
    """

    fg: tuple[int, int, int] | int | None = None
    bg: tuple[int, int, int] | int | None = None
    bold: bool = False
    italic: bool = False
    underline: str | None = None  # None | "single" | "double" | "curly" | "dotted" | "dashed"
    inverse: bool = False
    strikethrough: bool = False
    faint: bool = False
    invisible: bool = False


@dataclass(frozen=True, slots=True)
class Row:
    """A row in the terminal viewport.

    Attributes:
        cells: Tuple of cells in this row
        wrapped: True if this row is a soft-wrap continuation of the previous
    """

    cells: tuple[Cell, ...]
    wrapped: bool = False


@dataclass(frozen=True, slots=True)
class CursorState:
    """Terminal cursor state.

    Attributes:
        row: Cursor row (0-indexed)
        col: Cursor column (0-indexed)
        visible: Whether cursor is visible
        shape: Cursor shape: "block" | "bar" | "underline"
    """

    row: int
    col: int
    visible: bool
    shape: str  # "block" | "bar" | "underline"


@dataclass(frozen=True, slots=True)
class TerminalModes:
    """Terminal mode flags.

    Attributes:
        alt_screen: Alternate screen mode
        bracketed_paste: Bracketed paste mode
        mouse_reporting: Mouse reporting mode
        application_cursor: Application cursor keys mode
        echo: Echo state (None if not available in standalone mush.vt)
    """

    alt_screen: bool
    bracketed_paste: bool
    mouse_reporting: bool
    application_cursor: bool
    echo: bool | None = None  # None = not available (mush.vt standalone use)


@dataclass(frozen=True, slots=True)
class Frame:
    """Immutable snapshot of the terminal viewport. The loggable artifact.

    Never contains live engine handles. Callers MUST NOT assume snapshot() cost
    is O(1) — the contract allows a future incremental backend. Treat it as
    potentially O(cols * rows) and batch accordingly.

    Attributes:
        seq: Monotonic counter, per Terminal instance
        timestamp_mono: time.monotonic() at snapshot time
        cols: Number of columns in viewport
        rows: Number of rows in viewport
        grid: Viewport rows only; len == rows
        styles: Mapping of style_id → Style
        cursor: Cursor state
        modes: Terminal mode flags
        title: OSC 0/2 window title
        pwd: OSC 7 working directory URI
    """

    seq: int
    timestamp_mono: float
    cols: int
    rows: int
    grid: tuple[Row, ...]
    styles: Mapping[int, Style]
    cursor: CursorState
    modes: TerminalModes
    title: str | None = None
    pwd: str | None = None

    def text(self) -> str:
        """Plain-text projection of the viewport.

        Respects wide chars and soft-wrapping (wrapped rows are not
        newline-separated).

        Returns:
            Plain text representation of the viewport
        """
        lines = []
        for row in self.grid:
            line = "".join(cell.char for cell in row.cells)
            lines.append(line)
        return "\n".join(lines)

    def digest(self) -> str:
        """Stable content hash (SHA-256 hex, first 16 chars).

        Used by the Observation Layer for deduplication — identical grid
        content produces identical digest regardless of seq or timestamp.

        Returns:
            First 16 characters of SHA-256 hex digest
        """
        # Create a canonical string representation for hashing
        parts = []
        for row in self.grid:
            for cell in row.cells:
                parts.append(f"{cell.char}:{cell.width}:{cell.style_id}")

        content = "|".join(parts)
        hash_obj = hashlib.sha256(content.encode("utf-8"))
        return hash_obj.hexdigest()[:16]

    def diff(self, other: "Frame") -> "FrameDiff | None":
        """Returns None if frames are identical (by digest).

        Otherwise returns the set of changed rows. Callers may use this
        for vt-delta log encoding but are not required to.

        Args:
            other: Another Frame to compare against

        Returns:
            FrameDiff if frames differ, None if identical
        """
        if self.digest() == other.digest():
            return None

        # Find changed rows
        changed_rows = frozenset(i for i, (r1, r2) in enumerate(zip(self.grid, other.grid)) if r1 != r2)

        return FrameDiff(
            seq_from=other.seq,
            seq_to=self.seq,
            changed_rows=changed_rows,
            cursor_changed=self.cursor != other.cursor,
            modes_changed=self.modes != other.modes,
            title_changed=self.title != other.title,
        )


@dataclass(frozen=True, slots=True)
class FrameDiff:
    """Difference between two frames.

    Attributes:
        seq_from: Sequence number of the old frame
        seq_to: Sequence number of the new frame
        changed_rows: Row indices with differing cell content
        cursor_changed: Whether cursor state changed
        modes_changed: Whether terminal modes changed
        title_changed: Whether title changed
    """

    seq_from: int
    seq_to: int
    changed_rows: frozenset[int]
    cursor_changed: bool
    modes_changed: bool
    title_changed: bool
