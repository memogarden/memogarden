"""
mush.vt - Layer 2: Python API for terminal state.

Pure screen-state oracle. Input: bytes from a PTY master. Output: immutable
Frame snapshots, formatted strings, and device-reply callbacks.

Has zero knowledge of sessions, agents, arbitration, or any MUSH concept.
"""

from mush.vt.errors import (
    GhosttyError,
    GhosttyAllocationError,
    GhosttyCallbackError,
    GhosttyEngineMismatch,
    GhosttyInvalidValue,
    GhosttyNoValue,
    GhosttyOutOfSpace,
    GhosttyStateError,
    check_result,
)
from mush.vt.frames import (
    Cell,
    CursorState,
    Frame,
    FrameDiff,
    Row,
    Style,
    TerminalModes,
)

# Terminal import is conditional - requires compiled C library
try:
    from mush.vt.terminal import Terminal
    _terminal_available = True
except ImportError:
    Terminal = None  # type: ignore
    _terminal_available = False

__all__ = [
    # Errors
    "GhosttyError",
    "GhosttyAllocationError",
    "GhosttyCallbackError",
    "GhosttyEngineMismatch",
    "GhosttyInvalidValue",
    "GhosttyNoValue",
    "GhosttyOutOfSpace",
    "GhosttyStateError",
    "check_result",
    # Frames
    "Cell",
    "CursorState",
    "Frame",
    "FrameDiff",
    "Row",
    "Style",
    "TerminalModes",
]

# Only include Terminal in exports if available
if _terminal_available:
    __all__.append("Terminal")
