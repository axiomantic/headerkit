"""Integration roundtrip tests: parse C headers with libclang -> IR -> CFFI cdef string.

These tests verify the complete pipeline from C source code through the
libclang parser backend to CFFI-compatible cdef output.
"""

from __future__ import annotations

import pytest

from clangir.backends import get_backend, is_backend_available
from clangir.writers.cffi import header_to_cffi

# Skip entire module if libclang is not available
pytestmark = pytest.mark.skipif(
    not is_backend_available("libclang"),
    reason="libclang backend not available",
)


@pytest.fixture
def backend():
    """Get a libclang backend instance."""
    return get_backend("libclang")


def parse_and_convert(backend, code: str, exclude_patterns=None) -> str:
    """Parse C code and convert to CFFI cdef string."""
    header = backend.parse(code, "test.h")
    return header_to_cffi(header, exclude_patterns=exclude_patterns)


class TestVariableRoundtrip:
    """Test parsing and converting variable declarations."""

    def test_int_variable(self, backend):
        cdef = parse_and_convert(backend, "int x;")
        assert "int x;" in cdef

    def test_pointer_variable(self, backend):
        cdef = parse_and_convert(backend, "char *name;")
        assert "char * name;" in cdef


class TestStructRoundtrip:
    """Test parsing and converting struct declarations."""

    def test_simple_struct(self, backend):
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        cdef = parse_and_convert(backend, code)
        assert "struct Point" in cdef
        assert "int x;" in cdef
        assert "int y;" in cdef

    def test_struct_with_pointer_field(self, backend):
        code = """
        struct Node {
            int value;
            struct Node *next;
        };
        """
        cdef = parse_and_convert(backend, code)
        assert "struct Node {" in cdef
        assert "int value;" in cdef
        assert "struct Node * next;" in cdef

    def test_typedef_struct(self, backend):
        code = """
        typedef struct {
            float r;
            float g;
            float b;
        } Color;
        """
        cdef = parse_and_convert(backend, code)
        assert "Color" in cdef
        assert "float r;" in cdef
        assert "float g;" in cdef
        assert "float b;" in cdef


class TestFunctionRoundtrip:
    """Test parsing and converting function declarations."""

    def test_simple_function(self, backend):
        code = "int add(int a, int b);"
        cdef = parse_and_convert(backend, code)
        assert "int add(int a, int b);" in cdef

    def test_void_function(self, backend):
        code = "void do_nothing(void);"
        cdef = parse_and_convert(backend, code)
        assert "void do_nothing(void);" in cdef

    def test_pointer_return(self, backend):
        code = "char *get_name(void);"
        cdef = parse_and_convert(backend, code)
        assert "char * get_name(void);" in cdef

    def test_variadic_function(self, backend):
        code = "int printf(const char *fmt, ...);"
        cdef = parse_and_convert(backend, code)
        assert "int printf(const char * fmt, ...);" in cdef


class TestEnumRoundtrip:
    """Test parsing and converting enum declarations."""

    def test_simple_enum(self, backend):
        code = """
        enum Color {
            RED = 0,
            GREEN = 1,
            BLUE = 2
        };
        """
        cdef = parse_and_convert(backend, code)
        assert "enum Color {" in cdef
        assert "RED = 0," in cdef
        assert "GREEN = 1," in cdef
        assert "BLUE = 2," in cdef

    def test_typedef_enum(self, backend):
        code = """
        typedef enum {
            OFF = 0,
            ON = 1
        } Switch;
        """
        cdef = parse_and_convert(backend, code)
        assert "OFF = 0," in cdef
        assert "ON = 1," in cdef
        assert "} Switch;" in cdef


class TestTypedefRoundtrip:
    """Test parsing and converting typedef declarations."""

    def test_simple_typedef(self, backend):
        code = "typedef unsigned int uint32;"
        cdef = parse_and_convert(backend, code)
        assert "typedef unsigned int uint32;" in cdef

    def test_pointer_typedef(self, backend):
        code = "typedef void *handle_t;"
        cdef = parse_and_convert(backend, code)
        assert "typedef void * handle_t;" in cdef

    def test_function_pointer_typedef(self, backend):
        code = "typedef void (*callback_fn)(int status);"
        cdef = parse_and_convert(backend, code)
        # libclang may not preserve parameter names in function pointer typedefs
        assert "typedef void (*callback_fn)(int" in cdef
        assert cdef.strip().endswith(";")
        assert "callback_fn" in cdef


class TestUnionRoundtrip:
    """Test parsing and converting union declarations."""

    def test_simple_union(self, backend):
        code = """
        union Data {
            int i;
            float f;
            char c;
        };
        """
        cdef = parse_and_convert(backend, code)
        assert "union Data {" in cdef
        assert "int i;" in cdef
        assert "float f;" in cdef
        assert "char c;" in cdef


class TestMacroConstantRoundtrip:
    """Test parsing and converting macro constants."""

    def test_integer_macro(self, backend):
        code = "#define MAX_SIZE 1024\nvoid func(void);"
        cdef = parse_and_convert(backend, code)
        assert "#define MAX_SIZE 1024" in cdef

    def test_non_integer_macro_skipped(self, backend):
        code = '#define VERSION "1.0"\nvoid func(void);'
        cdef = parse_and_convert(backend, code)
        # String macros are not supported by CFFI and should be omitted
        assert "VERSION" not in cdef


class TestFunctionPointerTypedefRoundtrip:
    """Test parsing and converting function pointer typedefs."""

    def test_function_pointer_typedef(self, backend):
        code = "typedef int (*comparator_fn)(const void *a, const void *b);"
        cdef = parse_and_convert(backend, code)
        assert "typedef" in cdef
        assert "comparator_fn" in cdef
        assert "..." not in cdef  # should not be variadic


class TestMultipleDeclarations:
    """Test parsing headers with multiple declarations."""

    def test_complete_header(self, backend):
        code = """
        typedef unsigned int mysize_t;

        struct Buffer {
            char *data;
            mysize_t length;
        };

        enum Status {
            OK = 0,
            ERROR = 1
        };

        struct Buffer *buffer_create(mysize_t size);
        void buffer_destroy(struct Buffer *buf);
        int buffer_write(struct Buffer *buf, const char *data, mysize_t len);
        """
        cdef = parse_and_convert(backend, code)

        # Verify struct
        assert "struct Buffer {" in cdef
        assert "char * data;" in cdef
        assert "mysize_t length;" in cdef or "unsigned int length;" in cdef

        # Verify enum
        assert "OK = 0," in cdef
        assert "ERROR = 1," in cdef

        # Verify functions
        assert "buffer_create(" in cdef
        assert "buffer_destroy(" in cdef
        assert "buffer_write(" in cdef

    def test_interdependent_types(self, backend):
        code = """
        struct Config {
            int flags;
        };

        typedef struct Config Config;

        void init(Config *cfg);
        """
        cdef = parse_and_convert(backend, code)
        assert "Config" in cdef
        assert "int flags;" in cdef
        assert "init(" in cdef


class TestExcludePatterns:
    """Test that exclude_patterns work in the roundtrip pipeline."""

    def test_exclude_by_prefix(self, backend):
        code = """
        void public_func(void);
        void _private_func(void);
        void __internal_func(void);
        """
        cdef = parse_and_convert(backend, code, exclude_patterns=[r"^_"])
        assert "public_func" in cdef
        assert "_private_func" not in cdef
        assert "__internal_func" not in cdef

    def test_exclude_by_pattern(self, backend):
        code = """
        void nng_open(void);
        void nng_close(void);
        void debug_print(void);
        """
        cdef = parse_and_convert(backend, code, exclude_patterns=[r"^debug_"])
        assert "nng_open" in cdef
        assert "nng_close" in cdef
        assert "debug_print" not in cdef

    def test_no_exclude(self, backend):
        code = """
        void func_a(void);
        void func_b(void);
        """
        cdef = parse_and_convert(backend, code)
        assert "func_a" in cdef
        assert "func_b" in cdef
