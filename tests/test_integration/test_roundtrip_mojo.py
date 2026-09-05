"""Integration roundtrip tests: parse C headers with libclang -> IR -> Mojo output."""

from __future__ import annotations

import pytest

from headerkit.backends import is_backend_available
from headerkit.writers.mojo import write_mojo

pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


def parse_and_mojo(backend: pytest.FixtureRequest, code: str) -> str:
    """Parse C code and convert to Mojo binding string."""
    header = backend.parse(code, "test.h")  # type: ignore[attr-defined]
    return write_mojo(header)


class TestMojoEmpty:
    """Verify the writer does not crash on an empty header."""

    def test_empty_header(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_mojo(backend, "")
        assert "struct Library:" in output
        assert "var handle: DLHandle" in output


class TestMojoFunctionRoundtrip:
    """Test parsing and converting functions to Mojo."""

    def test_simple_function(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_mojo(backend, "int add(int a, int b);")
        assert "fn add(self, a: Int32, b: Int32) -> Int32:" in output
        assert 'var f = self.handle.get_function[fn(Int32, Int32) -> Int32]("add")' in output
        assert "return f(a, b)" in output

    def test_void_function(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_mojo(backend, "void reset_system(void);")
        assert "fn reset_system(self):" in output
        assert 'var f = self.handle.get_function[fn() -> None]("reset_system")' in output
        assert "f()" in output


class TestMojoStructRoundtrip:
    """Test parsing and converting struct declarations to Mojo."""

    def test_simple_struct(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_mojo(backend, "struct Point { int x; int y; };")
        assert "@value" in output
        assert '@register_passable("trivial")' in output
        assert "struct Point:" in output
        assert "var x: Int32" in output
        assert "var y: Int32" in output


class TestMojoEnumRoundtrip:
    """Test parsing and converting enum declarations to Mojo."""

    def test_simple_enum(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_mojo(backend, "enum Status { STATUS_OK = 0, STATUS_ERROR = 1 };")
        assert "struct Status:" in output
        assert "var value: Int32" in output
        assert "alias STATUS_OK = Status(0)" in output
        assert "alias STATUS_ERROR = Status(1)" in output


class TestMojoTypedefRoundtrip:
    """Test parsing and converting typedefs to Mojo."""

    def test_primitive_typedef(self, backend: pytest.FixtureRequest) -> None:
        output = parse_and_mojo(backend, "typedef unsigned int uint32_custom;")
        assert "alias uint32_custom = UInt32" in output
