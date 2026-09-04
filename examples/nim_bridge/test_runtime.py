"""Test verifying end-to-end Python interaction with Nim shared library."""

from __future__ import annotations

import ctypes
import threading
from pathlib import Path

lib_path = Path(__file__).parent / "src" / "libfastmath.dylib"
assert lib_path.exists(), f"Missing {lib_path}"
_lib = ctypes.CDLL(str(lib_path))


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


def ensure_nim_initialized() -> None:
    global _nim_initialized
    if not _nim_initialized:
        _lib.NimMain()
        _nim_initialized = True


ensure_nim_initialized()


class Matrix:
    def __init__(self, rows: int, cols: int) -> None:
        ensure_nim_initialized()
        self._handle = _lib.createMatrix(rows, cols)
        if not self._handle:
            msg = "Failed to allocate FastMatrix in Nim"
            raise MemoryError(msg)

    def sum(self) -> float:
        return float(_lib.computeSum(self._handle))

    def close(self) -> None:
        if getattr(self, "_handle", None):
            _lib.destroyMatrix(self._handle)
            self._handle = None

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> Matrix:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


def test_basic_computation() -> None:
    assert _lib.addNumbers(15, 27) == 42


def test_memory_lifecycle() -> None:
    for _ in range(100):
        with Matrix(100, 100) as m:
            s = m.sum()
            assert s == 0.0


def test_multithreaded_safety() -> None:
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                with Matrix(10, 10) as m:
                    _ = m.sum()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"


if __name__ == "__main__":
    test_basic_computation()
    print("test_basic_computation passed")
    test_memory_lifecycle()
    print("test_memory_lifecycle passed")
    test_multithreaded_safety()
    print("test_multithreaded_safety passed")
    print("All Nim runtime tests passed cleanly!")
