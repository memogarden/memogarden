"""
Unit tests for mush.vt frames module.

Tests for frozen dataclasses: Cell, Style, Row, Frame, etc.
"""

import pytest

from mush.vt.frames import (
    Cell,
    CursorState,
    Frame,
    FrameDiff,
    Row,
    Style,
    TerminalModes,
)


class TestCell:
    """Tests for Cell dataclass."""

    def test_cell_creation(self):
        """Test basic cell creation."""
        cell = Cell(char="A", width=1, style_id=0)
        assert cell.char == "A"
        assert cell.width == 1
        assert cell.style_id == 0
        assert cell.hyperlink is None

    def test_cell_with_hyperlink(self):
        """Test cell with hyperlink."""
        cell = Cell(char="A", width=1, style_id=0, hyperlink="https://example.com")
        assert cell.hyperlink == "https://example.com"

    def test_cell_immutability(self):
        """Test that cells are immutable."""
        cell = Cell(char="A", width=1, style_id=0)
        with pytest.raises(AttributeError):
            cell.char = "B"


class TestStyle:
    """Tests for Style dataclass."""

    def test_style_default(self):
        """Test style with all defaults."""
        style = Style()
        assert style.fg is None
        assert style.bg is None
        assert style.bold is False
        assert style.italic is False

    def test_style_with_attributes(self):
        """Test style with various attributes."""
        style = Style(fg=(255, 0, 0), bg=(0, 0, 255), bold=True, italic=True, underline="single")
        assert style.fg == (255, 0, 0)
        assert style.bg == (0, 0, 255)
        assert style.bold is True
        assert style.underline == "single"

    def test_style_immutability(self):
        """Test that styles are immutable."""
        style = Style(fg=(255, 0, 0))
        with pytest.raises(AttributeError):
            style.fg = (0, 0, 0)


class TestRow:
    """Tests for Row dataclass."""

    def test_row_creation(self):
        """Test basic row creation."""
        cells = (Cell(char="A", width=1, style_id=0), Cell(char="B", width=1, style_id=0))
        row = Row(cells=cells, wrapped=False)
        assert len(row.cells) == 2
        assert row.wrapped is False

    def test_row_wrapped(self):
        """Test wrapped row."""
        cells = (Cell(char="A", width=1, style_id=0),)
        row = Row(cells=cells, wrapped=True)
        assert row.wrapped is True


class TestCursorState:
    """Tests for CursorState dataclass."""

    def test_cursor_state(self):
        """Test cursor state creation."""
        cursor = CursorState(row=5, col=10, visible=True, shape="block")
        assert cursor.row == 5
        assert cursor.col == 10
        assert cursor.visible is True
        assert cursor.shape == "block"

    def test_cursor_invalid_shape(self):
        """Test cursor with various shapes."""
        for shape in ["block", "bar", "underline"]:
            cursor = CursorState(row=0, col=0, visible=True, shape=shape)
            assert cursor.shape == shape


class TestTerminalModes:
    """Tests for TerminalModes dataclass."""

    def test_modes_default(self):
        """Test modes with defaults."""
        modes = TerminalModes(
            alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
        )
        assert modes.alt_screen is False
        assert modes.bracketed_paste is False
        assert modes.echo is None  # Not available in standalone

    def test_modes_active(self):
        """Test modes with active flags."""
        modes = TerminalModes(
            alt_screen=True, bracketed_paste=True, mouse_reporting=True, application_cursor=True
        )
        assert modes.alt_screen is True
        assert modes.bracketed_paste is True


class TestFrame:
    """Tests for Frame dataclass."""

    def test_frame_creation(self):
        """Test basic frame creation."""
        cells = (Cell(char="A", width=1, style_id=0),)
        row = Row(cells=cells, wrapped=False)
        grid = (row,)
        styles = {}
        cursor = CursorState(row=0, col=0, visible=True, shape="block")
        modes = TerminalModes(
            alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
        )

        frame = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=1,
            rows=1,
            grid=grid,
            styles=styles,
            cursor=cursor,
            modes=modes,
        )

        assert frame.seq == 1
        assert frame.cols == 1
        assert frame.rows == 1
        assert len(frame.grid) == 1

    def test_frame_text(self):
        """Test Frame.text() method."""
        cells_a = (Cell(char="A", width=1, style_id=0), Cell(char="B", width=1, style_id=0))
        cells_b = (Cell(char="C", width=1, style_id=0), Cell(char="D", width=1, style_id=0))
        row_a = Row(cells=cells_a, wrapped=False)
        row_b = Row(cells=cells_b, wrapped=False)
        grid = (row_a, row_b)

        frame = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=2,
            rows=2,
            grid=grid,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        text = frame.text()
        assert text == "AB\nCD"

    def test_frame_text_wrapped(self):
        """Test Frame.text() with wrapped rows."""
        cells_a = (Cell(char="A", width=1, style_id=0), Cell(char="B", width=1, style_id=0))
        cells_b = (Cell(char="C", width=1, style_id=0), Cell(char="D", width=1, style_id=0))
        row_a = Row(cells=cells_a, wrapped=False)
        row_b = Row(cells=cells_b, wrapped=True)  # Wrapped continuation
        grid = (row_a, row_b)

        frame = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=2,
            rows=2,
            grid=grid,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        text = frame.text()
        # Wrapped rows should still be newline-separated in our MVP implementation
        assert text == "AB\nCD"

    def test_frame_digest(self):
        """Test Frame.digest() method."""
        cells = (Cell(char="A", width=1, style_id=0),)
        row = Row(cells=cells, wrapped=False)
        grid = (row,)

        frame = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=1,
            rows=1,
            grid=grid,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        digest = frame.digest()
        assert isinstance(digest, str)
        assert len(digest) == 16  # First 16 chars of SHA-256 hex

    def test_frame_digest_stability(self):
        """Test that identical content produces identical digest."""
        cells = (Cell(char="X", width=1, style_id=0),)
        row = Row(cells=cells, wrapped=False)
        grid = (row,)

        frame1 = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=1,
            rows=1,
            grid=grid,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        frame2 = Frame(
            seq=999,  # Different seq
            timestamp_mono=100.0,  # Different timestamp
            cols=1,
            rows=1,
            grid=grid,  # Same grid
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        # Same content should produce same digest
        assert frame1.digest() == frame2.digest()

    def test_frame_diff_identical(self):
        """Test Frame.diff() with identical frames."""
        cells = (Cell(char="A", width=1, style_id=0),)
        row = Row(cells=cells, wrapped=False)
        grid = (row,)

        frame1 = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=1,
            rows=1,
            grid=grid,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        frame2 = Frame(
            seq=2,
            timestamp_mono=1.0,
            cols=1,
            rows=1,
            grid=grid,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        diff = frame1.diff(frame2)
        assert diff is None  # Identical content

    def test_frame_diff_different(self):
        """Test Frame.diff() with different frames."""
        cells_a = (Cell(char="A", width=1, style_id=0),)
        cells_b = (Cell(char="B", width=1, style_id=0),)
        row_a = Row(cells=cells_a, wrapped=False)
        row_b = Row(cells=cells_b, wrapped=False)
        grid_a = (row_a,)
        grid_b = (row_b,)

        frame1 = Frame(
            seq=1,
            timestamp_mono=0.0,
            cols=1,
            rows=1,
            grid=grid_a,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        frame2 = Frame(
            seq=2,
            timestamp_mono=1.0,
            cols=1,
            rows=1,
            grid=grid_b,
            styles={},
            cursor=CursorState(row=0, col=0, visible=True, shape="block"),
            modes=TerminalModes(
                alt_screen=False, bracketed_paste=False, mouse_reporting=False, application_cursor=False
            ),
        )

        diff = frame1.diff(frame2)
        assert diff is not None
        assert isinstance(diff, FrameDiff)
        assert diff.seq_from == 2  # frame2.seq (old)
        assert diff.seq_to == 1    # frame1.seq (new)
        assert 0 in diff.changed_rows  # Row 0 changed


class TestFrameDiff:
    """Tests for FrameDiff dataclass."""

    def test_frame_diff_creation(self):
        """Test FrameDiff creation."""
        diff = FrameDiff(
            seq_from=1,
            seq_to=2,
            changed_rows=frozenset([0, 1]),
            cursor_changed=True,
            modes_changed=False,
            title_changed=False,
        )

        assert diff.seq_from == 1
        assert diff.seq_to == 2
        assert diff.cursor_changed is True
        assert diff.modes_changed is False

    def test_frame_diff_immutability(self):
        """Test that FrameDiff is immutable."""
        diff = FrameDiff(
            seq_from=1,
            seq_to=2,
            changed_rows=frozenset([0]),
            cursor_changed=False,
            modes_changed=False,
            title_changed=False,
        )

        with pytest.raises(AttributeError):
            diff.seq_from = 3
