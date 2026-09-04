"""FastMath package backed by compiled Nim code with Headerkit ctypes interface."""

from __future__ import annotations

import ctypes
from pathlib import Path

pkg_dir = Path(__file__).parent
candidates = list(pkg_dir.glob("libfastmath*"))
if not candidates:
    candidates = list((pkg_dir.parent / "src").glob("libfastmath*"))

if not candidates:
    msg = "Could not find compiled libfastmath shared library"
    raise ImportError(msg)

_lib = ctypes.CDLL(str(candidates[0]))


class FastMatrix(ctypes.Structure):
    pass


_lib.NimMain.argtypes = []
_lib.NimMain.restype = None

_lib.createMatrix.argtypes = [ctypes.c_int64, ctypes.c_int64]
_lib.createMatrix.restype = ctypes.POINTER(FastMatrix)

_lib.destroyMatrix.argtypes = [ctypes.POINTER(FastMatrix)]
_lib.destroyMatrix.restype = None

_lib.addNumbers.argtypes = [ctypes.c_int64, ctypes.c_int64]
_lib.addNumbers.restype = ctypes.c_int64

_lib.computeSum.argtypes = [ctypes.POINTER(FastMatrix)]
_lib.computeSum.restype = ctypes.c_double

_nim_initialized = False


def _ensure_initialized() -> None:
    global _nim_initialized
    if not _nim_initialized:
        _lib.NimMain()
        _nim_initialized = True


_ensure_initialized()


def add_numbers(a: int, b: int) -> int:
    """Add two integers using Nim."""
    return int(_lib.addNumbers(a, b))


class Matrix:
    """Matrix container with explicit ownership lifecycle and Nim deallocation."""

    def __init__(self, rows: int, cols: int) -> None:
        _ensure_initialized()
        self._handle = _lib.createMatrix(rows, cols)
        if not self._handle:
            msg = "Failed to allocate FastMatrix in Nim"
            raise MemoryError(msg)

    def sum(self) -> float:
        """Compute sum of elements using Nim."""
        return float(_lib.computeSum(self._handle))

    def close(self) -> None:
        """Release Nim allocated memory."""
        if getattr(self, "_handle", None):
            _lib.destroyMatrix(self._handle)
            self._handle = None

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> Matrix:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


__all__ = ["Matrix", "add_numbers"]
