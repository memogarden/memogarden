"""
Unit tests for mush.vt errors module.

Tests for error hierarchy and result checking.
"""

import pytest

from mush.vt.errors import (
    GhosttyAllocationError,
    GhosttyCallbackError,
    GhosttyError,
    GhosttyInvalidValue,
    GhosttyNoValue,
    GhosttyOutOfSpace,
    GhosttyStateError,
    check_result,
    GHOSTTY_RESULT_MAP,
)


class TestGhosttyError:
    """Tests for GhosttyError base class."""

    def test_error_creation(self):
        """Test basic error creation."""
        error = GhosttyError(code=-1, message="Test error")
        assert error.code == -1
        assert str(error) == "GhosttyError(-1): Test error"

    def test_error_default_message(self):
        """Test error with default message."""
        error = GhosttyError(code=-2)
        assert str(error) == "GhosttyError(-2):"

    def test_error_string_representation(self):
        """Test error __str__ includes class name."""
        error = GhosttyAllocationError(code=-1, message="Allocation failed")
        error_str = str(error)
        assert "GhosttyAllocationError" in error_str
        assert "-1" in error_str


class TestErrorSubclasses:
    """Tests for specific error subclasses."""

    def test_allocation_error(self):
        """Test GhosttyAllocationError."""
        error = GhosttyAllocationError(code=-1, message="Out of memory")
        assert isinstance(error, GhosttyError)
        assert error.code == -1

    def test_state_error(self):
        """Test GhosttyStateError."""
        error = GhosttyStateError(code=-3, message="Invalid state")
        assert isinstance(error, GhosttyError)
        assert error.code == -3

    def test_invalid_value(self):
        """Test GhosttyInvalidValue."""
        error = GhosttyInvalidValue(code=-2, message="Invalid parameter")
        assert isinstance(error, GhosttyError)
        assert error.code == -2

    def test_out_of_space(self):
        """Test GhosttyOutOfSpace."""
        error = GhosttyOutOfSpace(code=-3, message="Buffer too small")
        assert isinstance(error, GhosttyError)
        assert error.code == -3

    def test_no_value(self):
        """Test GhosttyNoValue."""
        error = GhosttyNoValue(code=-4, message="No value available")
        assert isinstance(error, GhosttyError)
        assert error.code == -4


class TestGhosttyCallbackError:
    """Tests for GhosttyCallbackError."""

    def test_callback_error_creation(self):
        """Test callback error wraps original exception."""
        original = ValueError("Callback failed")
        error = GhosttyCallbackError(original)

        assert error.code == -999  # Special code
        assert error.original_exception is original
        assert "Callback error" in str(error)

    def test_callback_error_preserves_original(self):
        """Test that original exception is preserved."""
        original = RuntimeError("Something went wrong")
        error = GhosttyCallbackError(original)

        # Original exception should be accessible
        assert isinstance(error.original_exception, RuntimeError)
        assert str(error.original_exception) == "Something went wrong"


class TestResultMap:
    """Tests for GHOSTTY_RESULT_MAP."""

    def test_result_map_completeness(self):
        """Test that all standard result codes are mapped."""
        # Standard codes from the C API
        standard_codes = [0, -1, -2, -3, -4]

        for code in standard_codes:
            if code == 0:
                assert GHOSTTY_RESULT_MAP[code] is None  # Success
            else:
                assert GHOSTTY_RESULT_MAP[code] is not None  # Error class

    def test_result_map_classes(self):
        """Test that result map points to correct classes."""
        assert GHOSTTY_RESULT_MAP[-1] == GhosttyAllocationError
        assert GHOSTTY_RESULT_MAP[-2] == GhosttyInvalidValue
        assert GHOSTTY_RESULT_MAP[-3] == GhosttyOutOfSpace
        assert GHOSTTY_RESULT_MAP[-4] == GhosttyNoValue


class TestCheckResult:
    """Tests for check_result function."""

    def test_check_result_success(self):
        """Test that success (code=0) does not raise."""
        # Should not raise
        check_result(0)
        check_result(0, "Context message")

    def test_check_result_allocation_error(self):
        """Test that code=-1 raises GhosttyAllocationError."""
        with pytest.raises(GhosttyAllocationError) as exc_info:
            check_result(-1)

        assert exc_info.value.code == -1

    def test_check_result_with_context(self):
        """Test that context is included in error message."""
        with pytest.raises(GhosttyInvalidValue) as exc_info:
            check_result(-2, "Failed to resize terminal")

        error = exc_info.value
        assert "Failed to resize terminal" in str(error)
        assert error.code == -2

    def test_check_result_unknown_code(self):
        """Test that unknown codes raise base GhosttyError."""
        with pytest.raises(GhosttyError) as exc_info:
            check_result(-999)

        assert exc_info.value.code == -999
        # Should be base GhosttyError, not a subclass
        assert type(exc_info.value) == GhosttyError

    def test_check_result_sequence(self):
        """Test multiple check_result calls."""
        # All should succeed
        check_result(0, "Operation 1")
        check_result(0, "Operation 2")
        check_result(0, "Operation 3")

        # Then fail
        with pytest.raises(GhosttyAllocationError):
            check_result(-1, "Operation 4")


class TestErrorPropagation:
    """Tests for error propagation in typical scenarios."""

    def test_error_in_chain(self):
        """Test error propagation through a call chain."""

        def low_level_operation():
            check_result(-2, "low_level")

        def high_level_operation():
            try:
                low_level_operation()
            except GhosttyError as e:
                # Re-raise with additional context
                raise GhosttyInvalidValue(e.code, f"high_level: {e}") from e

        with pytest.raises(GhosttyInvalidValue) as exc_info:
            high_level_operation()

        error = exc_info.value
        assert "high_level" in str(error)
        assert error.code == -2

    def test_multiple_error_types(self):
        """Test that different error types can be raised."""
        errors = [
            (-1, GhosttyAllocationError),
            (-2, GhosttyInvalidValue),
            (-3, GhosttyOutOfSpace),
            (-4, GhosttyNoValue),
        ]

        for code, expected_class in errors:
            with pytest.raises(expected_class) as exc_info:
                check_result(code)
            assert exc_info.value.code == code
