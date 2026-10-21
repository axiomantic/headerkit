"""Vendored clang Python bindings with version-aware loader.

This package vendors clang cindex.py from multiple LLVM release tags
and selects the correct version based on the system's installed LLVM.

Usage::

    from headerkit._clang import get_cindex

    cindex = get_cindex()
    # cindex is the appropriate cindex module for your system LLVM
"""

import ctypes
import importlib
import platform
import sys
import warnings
from ctypes import c_void_p
from types import ModuleType

_NEEDS_INTEROP_STRING_PATCH = platform.python_implementation() != "CPython"

_NEEDS_BIG_ENDIAN_CALLBACK_PATCH = (
    sys.byteorder == "big" and ctypes.sizeof(ctypes.c_void_p) == 8 and sys.version_info < (3, 14)
)

_cached_cindex: ModuleType | None = None


def _patch_big_endian_callbacks(cindex: ModuleType) -> None:
    """Work around CPython issue gh-156933 on 64-bit big-endian architectures (e.g. s390x).

    In Python < 3.14 on 64-bit big-endian architectures, ctypes.CFUNCTYPE callbacks
    returning 32-bit integers (such as c_int) fail to widen narrow return values per
    the libffi contract. Writing 4 bytes to an 8-byte buffer writes into the high-order
    bits on big-endian machines, leaving the low-order bits as 0 or stack garbage.
    In the s390x ELF ABI, 32-bit return values are evaluated in the low 32 bits of register
    r2. When clang_visitChildren evaluates CXChildVisitResult, it reads 0
    (CXChildVisit_Break), immediately aborting AST traversal on the very first node.

    Widening the visitor callbacks' return type to ctypes.c_ssize_t (64-bit integer)
    ensures ctypes writes all 8 bytes, correctly placing the return value in the low 32
    bits of r2 for native callers across both endiannesses.
    """
    new_cursor_cb = ctypes.CFUNCTYPE(ctypes.c_ssize_t, cindex.Cursor, cindex.Cursor, ctypes.py_object)
    new_fields_cb = ctypes.CFUNCTYPE(ctypes.c_ssize_t, cindex.Cursor, ctypes.py_object)

    if hasattr(cindex, "cursor_visit_callback"):
        cindex.cursor_visit_callback = new_cursor_cb  # type: ignore[attr-defined]
    if hasattr(cindex, "fields_visit_callback"):
        cindex.fields_visit_callback = new_fields_cb  # type: ignore[attr-defined]

    callbacks = getattr(cindex, "callbacks", None)
    if isinstance(callbacks, dict):
        if "cursor_visit" in callbacks:
            callbacks["cursor_visit"] = new_cursor_cb
        if "fields_visit" in callbacks:
            callbacks["fields_visit"] = new_fields_cb

    fn_list = getattr(cindex, "FUNCTION_LIST", getattr(cindex, "functionList", None))
    if fn_list is not None:
        for idx, entry in enumerate(fn_list):
            name = entry[0]
            if name == "clang_visitChildren":
                fn_list[idx] = (name, [cindex.Cursor, new_cursor_cb, ctypes.py_object], entry[2])
            elif name in ("clang_visitCXXBaseClasses", "clang_visitCXXMethods", "clang_Type_visitFields"):
                fn_list[idx] = (name, [cindex.Type, new_fields_cb, ctypes.py_object], entry[2])

    config_cls = getattr(cindex, "Config", None)
    if config_cls is not None and getattr(config_cls, "loaded", False) and hasattr(cindex, "conf"):
        try:
            lib = cindex.conf.lib
            for attr, cb, target_type in [
                ("clang_visitChildren", new_cursor_cb, cindex.Cursor),
                ("clang_visitCXXBaseClasses", new_fields_cb, cindex.Type),
                ("clang_visitCXXMethods", new_fields_cb, cindex.Type),
                ("clang_Type_visitFields", new_fields_cb, cindex.Type),
            ]:
                if hasattr(lib, attr):
                    getattr(lib, attr).argtypes = [target_type, cb, ctypes.py_object]
        except Exception:
            pass


VENDORED_VERSIONS = ("14", "15", "16", "17", "18", "19", "20", "21", "22", "23")
LATEST_VENDORED = "23"
OLDEST_VENDORED = "14"


class _compat_c_interop_string(c_void_p):
    """PyPy-compatible replacement for c_interop_string.

    The vendored cindex.py defines c_interop_string as a subclass of
    ctypes.c_char_p, but PyPy does not support subclassing c_char_p.
    This replacement uses c_void_p as the base and manually manages
    string buffer storage to provide an identical API.
    """

    _buffer: ctypes.Array[ctypes.c_char] | None = None

    def __init__(self, p: str | bytes | None = None) -> None:
        if p is None:
            p = b""
        if isinstance(p, str):
            p = p.encode("utf8")
        buf = ctypes.create_string_buffer(p)
        super().__init__(ctypes.cast(buf, c_void_p).value)
        self._buffer = buf  # prevent GC

    def __str__(self) -> str:
        return self.value or ""

    def _get_raw_ptr(self) -> int | None:
        """Read the raw void pointer value from the c_void_p base."""
        return super().value

    @property
    def value(self) -> str | None:  # type: ignore[override]
        ptr = self._get_raw_ptr()
        if ptr is None:
            return None
        return ctypes.string_at(ptr).decode("utf8")

    @classmethod
    def from_param(cls, param: str | bytes | None) -> "_compat_c_interop_string":
        if isinstance(param, str):
            return cls(param)
        if isinstance(param, bytes):
            return cls(param)
        if param is None:
            return cls(param)
        raise TypeError(f"Cannot convert '{type(param).__name__}' to '{cls.__name__}'")

    @staticmethod
    def to_python_string(x: "_compat_c_interop_string") -> str | None:
        return x.value


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

    from headerkit._clang._version import detect_llvm_version

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

    cindex = importlib.import_module(f"headerkit._clang.v{version}.cindex")

    if _NEEDS_INTEROP_STRING_PATCH:
        cindex.c_interop_string = _compat_c_interop_string  # type: ignore[attr-defined]

    if _NEEDS_BIG_ENDIAN_CALLBACK_PATCH:
        _patch_big_endian_callbacks(cindex)

    _cached_cindex = cindex
    return cindex
