"""
cffi API-mode builder for libghostty-vt.

This script compiles the _lib extension module against the vendored static library
and header. If upstream signatures drift, the build fails loudly.
"""

import os
import sys
from pathlib import Path
from cffi import FFI

# Read cdefs
this_dir = Path(__file__).parent
cdefs_path = this_dir / "cdefs.py"

# Import cdefs content
import importlib.util
spec = importlib.util.spec_from_file_location("cdefs", cdefs_path)
cdefs_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdefs_module)

cdefs = cdefs_module.cdefs

# Paths to vendor library and headers
vendor_dir = this_dir.parent.parent / "vendor" / "libghostty-vt"
include_dir = vendor_dir / "include"
lib_dir = this_dir.parent.parent / "vendor" / "lib"

# Verify vendored assets exist
if not include_dir.exists():
    raise FileNotFoundError(f"Header directory not found: {include_dir}")
if not lib_dir.exists():
    raise FileNotFoundError(f"Library directory not found: {lib_dir}")

# Create FFI instance
ffi = FFI()

# Parse cdefs
ffi.cdef(cdefs)

# Set source for compilation
ffi.set_source(
    "mush._ghostty._lib",
    '#include "ghostty/vt/terminal.h"',
    libraries=["ghostty-vt"],
    library_dirs=[str(lib_dir)],
    include_dirs=[str(include_dir)],
    extra_link_args=["-static-libgcc"],
)

if __name__ == "__main__":
    # Compile the module
    ffi.compile()
    print(f"Successfully compiled mush._ghostty._lib")
