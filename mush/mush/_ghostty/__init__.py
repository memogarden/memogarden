"""
mush._ghostty - Layer 1: cffi binding to libghostty-vt.

This is a mechanical, near-raw binding to the C API. It is ugly on purpose.
All Pythonic niceness lives in Layer 2 (mush.vt).

When upstream libghostty-vt churns, only this module changes.
"""

from mush._ghostty._lib import ffi, lib  # noqa: F401

__all__ = ["ffi", "lib"]
