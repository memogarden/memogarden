"""
Error hierarchy for mush.vt Layer 2.

Every non-OK GhosttyResult becomes a GhostError subclass.
No silent failures.
"""

from __future__ import annotations


class GhosttyError(Exception):
    """Base error for all ghostty-related errors.

    Carries the raw GhosttyResult integer as .code.
    """

    code: int

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        super().__init__(message or f"GhosttyError: code={code}")

    def __str__(self) -> str:
        base_msg = super().__str__()
        # Only include non-default messages
        if base_msg == f"GhosttyError: code={self.code}":
            return f"{self.__class__.__name__}({self.code}):"
        return f"{self.__class__.__name__}({self.code}): {base_msg}"


class GhosttyAllocationError(GhosttyError):
    """Operation failed due to failed allocation (GHOSTTY_OUT_OF_MEMORY)."""

    pass


class GhosttyStateError(GhosttyError):
    """Invalid handle, use-after-close, or operation invalid in current state.

    Raised when called after close() or on invalid terminal handle.
    """

    pass


class GhosttyInvalidValue(GhosttyError):
    """Operation failed due to invalid value (GHOSTTY_INVALID_VALUE)."""

    pass


class GhosttyOutOfSpace(GhosttyError):
    """Operation failed because the provided buffer was too small (GHOSTTY_OUT_OF_SPACE)."""

    pass


class GhosttyNoValue(GhosttyError):
    """The requested value has no value (GHOSTTY_NO_VALUE)."""

    pass


class GhosttyEngineMismatch(GhosttyError):
    """build_info verification failed against PINNED_COMMIT expectations."""

    pass


class GhosttyCallbackError(GhosttyError):
    """Exception raised inside a Python callback propagated out of write()."""

    original_exception: Exception

    def __init__(self, original_exception: Exception) -> None:
        self.original_exception = original_exception
        # Call parent with (code, message)
        super().__init__(-999, f"Callback error: {original_exception}")


# Map GhosttyResult codes to exception classes
GHOSTTY_RESULT_MAP = {
    0: None,  # GHOSTTY_SUCCESS
    -1: GhosttyAllocationError,  # GHOSTTY_OUT_OF_MEMORY
    -2: GhosttyInvalidValue,  # GHOSTTY_INVALID_VALUE
    -3: GhosttyOutOfSpace,  # GHOSTTY_OUT_OF_SPACE
    -4: GhosttyNoValue,  # GHOSTTY_NO_VALUE
}


def check_result(code: int, context: str = "") -> None:
    """Check a GhosttyResult code and raise appropriate exception if non-zero.

    Args:
        code: The GhosttyResult integer code
        context: Optional context string for error messages

    Raises:
        GhosttyError or appropriate subclass if code is non-zero
    """
    if code == 0:
        return

    exc_class = GHOSTTY_RESULT_MAP.get(code, GhosttyError)
    message = f"{context}: " if context else ""
    raise exc_class(code, message)
