"""Integration roundtrip tests: parse C/C++ headers with libclang -> IR -> CShim output."""

from __future__ import annotations

import pytest

from headerkit.backends import is_backend_available
from headerkit.writers.cshim import write_cshim

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


def parse_and_cshim(backend: pytest.FixtureRequest, code: str, *, catch_exceptions: bool = False) -> str:
    """Parse C/C++ code and convert to CShim C-ABI wrapper."""
    header = backend.parse(code, "test.h")  # type: ignore[attr-defined]
    return write_cshim(header, catch_exceptions=catch_exceptions)


class TestCShimEmpty:
    """Verify CShim writer does not crash on an empty header."""

    def test_empty_header(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_cshim(backend, "")
        assert "// Auto-generated C-ABI shim by HeaderKit" in output
        assert '#include "test.h"' in output


class TestCShimFunctionRoundtrip:
    """Test parsing and generating C-ABI extern "C" wrappers for functions."""

    def test_simple_function(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_cshim(backend, "int calculate(int a, int b);")
        assert 'extern "C" {' in output
        assert "int calculate(int a, int b);" in output
        assert "int calculate(int a, int b) {" in output
        assert "return calculate(a, b);" in output

    def test_void_function(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_cshim(backend, "void reset_device(void);")
        assert "void reset_device(void);" in output
        assert "void reset_device(void) {" in output
        assert "reset_device();" in output

    def test_exception_catching(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_cshim(backend, "int risky_op(int val);", catch_exceptions=True)
        assert "#include <exception>" in output
        assert "try {" in output
        assert "return risky_op(val);" in output
        assert "catch (...) {" in output
        assert "return static_cast<int>(0);" in output
