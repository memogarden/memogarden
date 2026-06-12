"""
Unit tests for mush.vt.terminal module.

Tests for Terminal class. NOTE: These tests require the libghostty-vt
C library to be built. They will fail if the library is not available.
"""

import pytest

# Import Terminal - this will fail if C library is not built
try:
    from mush.vt.terminal import Terminal
    from mush.vt.errors import GhosttyStateError, GhosttyAllocationError
    TERMINAL_AVAILABLE = True
except ImportError as e:
    TERMINAL_AVAILABLE = False
    IMPORT_ERROR = str(e)


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalCreation:
    """Tests for Terminal creation and initialization."""

    def test_terminal_creation_basic(self):
        """Test basic terminal creation."""
        term = Terminal(cols=80, rows=24)
        assert term.cols == 80
        assert term.rows == 24
        assert term.closed is False

    def test_terminal_creation_with_scrollback(self):
        """Test terminal creation with custom scrollback."""
        term = Terminal(cols=80, rows=24, scrollback=5000)
        assert term.closed is False

    def test_terminal_creation_invalid_dimensions(self):
        """Test terminal creation with invalid dimensions."""
        with pytest.raises(ValueError):
            Terminal(cols=0, rows=24)

        with pytest.raises(ValueError):
            Terminal(cols=80, rows=0)

        with pytest.raises(ValueError):
            Terminal(cols=-1, rows=24)


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalLifecycle:
    """Tests for Terminal lifecycle methods."""

    def test_terminal_close(self):
        """Test terminal close."""
        term = Terminal(cols=80, rows=24)
        assert term.closed is False

        term.close()
        assert term.closed is True

    def test_terminal_close_idempotent(self):
        """Test that close is idempotent."""
        term = Terminal(cols=80, rows=24)
        term.close()
        term.close()  # Should not raise
        assert term.closed is True

    def test_terminal_context_manager(self):
        """Test terminal as context manager."""
        with Terminal(cols=80, rows=24) as term:
            assert term.closed is False
            assert term.cols == 80

        # Should be closed after context
        assert term.closed is True

    def test_terminal_reset(self):
        """Test terminal reset."""
        term = Terminal(cols=80, rows=24)
        term.reset()  # Should not raise


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalWrite:
    """Tests for Terminal.write() method."""

    def test_write_simple_text(self):
        """Test writing simple text."""
        term = Terminal(cols=80, rows=24)
        term.write(b"Hello, World!")  # Should not raise

    def test_write_empty(self):
        """Test writing empty bytes."""
        term = Terminal(cols=80, rows=24)
        term.write(b"")  # Should not raise

    def test_write_after_close(self):
        """Test writing after close raises error."""
        term = Terminal(cols=80, rows=24)
        term.close()

        with pytest.raises(GhosttyStateError):
            term.write(b"test")


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalSnapshot:
    """Tests for Terminal.snapshot() method."""

    def test_snapshot_basic(self):
        """Test basic snapshot creation."""
        term = Terminal(cols=80, rows=24)
        frame = term.snapshot()

        assert frame is not None
        assert frame.seq > 0
        assert frame.cols == 80
        assert frame.rows == 24
        assert len(frame.grid) == 24

    def test_snapshot_after_write(self):
        """Test snapshot after writing data."""
        term = Terminal(cols=80, rows=24)
        term.write(b"Hello")

        frame = term.snapshot()
        assert frame.seq > 0

    def test_snapshot_sequential(self):
        """Test that snapshots increment seq."""
        term = Terminal(cols=80, rows=24)

        frame1 = term.snapshot()
        frame2 = term.snapshot()

        assert frame2.seq > frame1.seq

    def test_snapshot_after_close(self):
        """Test snapshot after close raises error."""
        term = Terminal(cols=80, rows=24)
        term.close()

        with pytest.raises(GhosttyStateError):
            term.snapshot()


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalResize:
    """Tests for Terminal.resize() method."""

    def test_resize_valid(self):
        """Test valid resize."""
        term = Terminal(cols=80, rows=24)
        term.resize(100, 30)

        assert term.cols == 100
        assert term.rows == 30

    def test_resize_invalid(self):
        """Test resize with invalid dimensions."""
        term = Terminal(cols=80, rows=24)

        with pytest.raises(ValueError):
            term.resize(0, 30)

        with pytest.raises(ValueError):
            term.resize(100, 0)

    def test_resize_after_close(self):
        """Test resize after close raises error."""
        term = Terminal(cols=80, rows=24)
        term.close()

        with pytest.raises(GhosttyStateError):
            term.resize(100, 30)


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalModes:
    """Tests for Terminal.modes() method."""

    def test_modes_initial(self):
        """Test initial terminal modes."""
        term = Terminal(cols=80, rows=24)
        modes = term.modes()

        assert modes.alt_screen is False
        assert modes.bracketed_paste is False


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalCallbacks:
    """Tests for Terminal callbacks."""

    def test_write_pty_callback(self):
        """Test on_write_pty callback."""
        received = []

        term = Terminal(cols=80, rows=24)

        def callback(data):
            received.append(data)

        term.on_write_pty = callback
        # TODO: Write data that triggers device query
        # term.write(b"\x1b[c")  # Device Attributes query

        # For now, just verify callback can be set
        assert term.on_write_pty is not None


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalUtilities:
    """Tests for Terminal utility methods."""

    def test_paste_is_safe(self):
        """Test Terminal.paste_is_safe static method."""
        assert Terminal.paste_is_safe(b"safe text") is True
        # TODO: Test with unsafe data

    def test_engine_info(self):
        """Test Terminal.engine_info static method."""
        info = Terminal.engine_info()
        assert isinstance(info, dict)
        assert "commit" in info


@pytest.mark.skipif(not TERMINAL_AVAILABLE, reason="Terminal not available (C library not built)")
class TestTerminalProperties:
    """Tests for Terminal properties."""

    def test_properties(self):
        """Test terminal properties."""
        term = Terminal(cols=80, rows=24)

        assert term.cols == 80
        assert term.rows == 24
        assert term.closed is False
        assert term.title is None  # MVP placeholder
        assert term.pwd is None  # MVP placeholder


def test_terminal_not_available():
    """Test that provides clear error when Terminal is not available."""
    if not TERMINAL_AVAILABLE:
        import mush.vt.terminal

        # Attempting to import should have failed
        # This test documents the expected failure mode
        assert "mush._ghostty" in IMPORT_ERROR or "_lib" in IMPORT_ERROR
