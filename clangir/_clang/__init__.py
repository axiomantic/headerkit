"""Vendored clang Python bindings with version-aware loader.

This package vendors clang cindex.py from multiple LLVM release tags
and selects the correct version based on the system's installed LLVM.

Usage::

    from clangir._clang import get_cindex

    cindex = get_cindex()
    # cindex is the appropriate cindex module for your system LLVM
"""

import importlib
import warnings
from types import ModuleType

_cached_cindex: ModuleType | None = None

VENDORED_VERSIONS = ("18", "19", "20", "21")
LATEST_VENDORED = "21"
OLDEST_VENDORED = "18"


def get_cindex() -> ModuleType:
    """Load the appropriate vendored cindex module for the system's LLVM version.

    Detection order:
    1. CIR_CLANG_VERSION env var (explicit override)
    2. llvm-config --version (most reliable)
    3. clang -dM -E to get __clang_major__ (works for Apple clang)
    4. Fallback to latest vendored version with warning

    Returns the cindex module ready for use. The result is cached.
    """
    global _cached_cindex
    if _cached_cindex is not None:
        return _cached_cindex

    from clangir._clang._version import detect_llvm_version

    version = detect_llvm_version()

    if version is None:
        warnings.warn(
            f"Could not detect LLVM version. Using vendored cindex for LLVM {LATEST_VENDORED}. "
            f"Set CIR_CLANG_VERSION to override.",
            stacklevel=2,
        )
        version = LATEST_VENDORED

    if version not in VENDORED_VERSIONS:
        try:
            v_int = int(version)
        except ValueError:
            warnings.warn(f"Invalid LLVM version {version!r}, falling back to {LATEST_VENDORED}", stacklevel=2)
            version = LATEST_VENDORED
            v_int = int(version)
        oldest_int = int(OLDEST_VENDORED)
        latest_int = int(LATEST_VENDORED)

        if v_int < oldest_int:
            warnings.warn(
                f"LLVM {version} is older than oldest vendored version ({OLDEST_VENDORED}). "
                f"Using {OLDEST_VENDORED}. Set CIR_CLANG_VERSION to override.",
                stacklevel=2,
            )
            version = OLDEST_VENDORED
        elif v_int > latest_int:
            warnings.warn(
                f"LLVM {version} is newer than latest vendored version ({LATEST_VENDORED}). "
                f"Using {LATEST_VENDORED}. Set CIR_CLANG_VERSION to override.",
                stacklevel=2,
            )
            version = LATEST_VENDORED

    module = importlib.import_module(f"clangir._clang.v{version}.cindex")
    _cached_cindex = module
    return module
