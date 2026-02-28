"""Tests for the CFFI cdef writer."""

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers.cffi import _format_field, _format_params, decl_to_cffi, header_to_cffi, type_to_cffi


class TestTypeToCffi:
    def test_simple_type(self):
        assert type_to_cffi(CType("int")) == "int"

    def test_qualified_type(self):
        assert type_to_cffi(CType("int", ["const"])) == "const int"

    def test_pointer(self):
        assert type_to_cffi(Pointer(CType("int"))) == "int *"

    def test_double_pointer(self):
        assert type_to_cffi(Pointer(Pointer(CType("char")))) == "char **"

    def test_array(self):
        assert type_to_cffi(Array(CType("int"), 10)) == "int[10]"

    def test_flexible_array(self):
        assert type_to_cffi(Array(CType("char"), None)) == "char[]"

    def test_function_pointer(self):
        fp = FunctionPointer(CType("void"), [])
        assert type_to_cffi(fp) == "void(*)(void)"

    def test_function_pointer_with_params(self):
        fp = FunctionPointer(
            CType("int"),
            [Parameter("x", CType("int")), Parameter(None, CType("float"))],
        )
        assert type_to_cffi(fp) == "int(*)(int x, float)"

    def test_pointer_to_function_pointer(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        result = type_to_cffi(Pointer(fp))
        assert result == "void(*)(int x)"

    def test_pointer_to_function_pointer_no_params(self):
        fp = FunctionPointer(CType("void"), [])
        result = type_to_cffi(Pointer(fp))
        assert result == "void(*)(void)"


class TestDeclToCffi:
    def test_struct_with_fields(self):
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        result = decl_to_cffi(s)
        assert result == "struct Point {\n    int x;\n    int y;\n};"

    def test_opaque_struct(self):
        s = Struct("Opaque", [])
        result = decl_to_cffi(s)
        assert result == "struct Opaque { ...; };"

    def test_typedef_struct(self):
        s = Struct("Point", [Field("x", CType("int"))], is_typedef=True)
        result = decl_to_cffi(s)
        assert result == "typedef struct Point {\n    int x;\n} Point;"

    def test_union(self):
        u = Struct("Data", [Field("i", CType("int"))], is_union=True)
        result = decl_to_cffi(u)
        assert result == "union Data {\n    int i;\n};"

    def test_enum(self):
        e = Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])
        result = decl_to_cffi(e)
        assert result == "enum Color {\n    RED = 0,\n    GREEN = 1,\n};"

    def test_function(self):
        f = Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])
        result = decl_to_cffi(f)
        assert result is not None
        assert "int add(int a, int b);" in result

    def test_variadic_function(self):
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        result = decl_to_cffi(f)
        assert result == "int printf(const char * fmt, ...);"

    def test_typedef(self):
        t = Typedef("myint", CType("unsigned int"))
        result = decl_to_cffi(t)
        assert result is not None
        assert "typedef unsigned int myint;" in result

    def test_constant_integer(self):
        c = Constant("SIZE", 100, is_macro=True)
        result = decl_to_cffi(c)
        assert result == "#define SIZE 100"

    def test_constant_non_integer_skipped(self):
        c = Constant("STR", '"hello"', is_macro=True)
        result = decl_to_cffi(c)
        assert result is None

    def test_constant_float_returns_none(self):
        """CFFI only supports integer constants; float values should return None."""
        c = Constant("PI", 3.14, is_macro=True)
        result = decl_to_cffi(c)
        assert result is None

    def test_unknown_declaration_type_returns_none(self):
        """decl_to_cffi should return None for unknown declaration types."""

        class FakeDecl:
            name = "fake"

        result = decl_to_cffi(FakeDecl())
        assert result is None

    def test_variable(self):
        v = Variable("count", CType("int"))
        result = decl_to_cffi(v)
        assert result is not None
        assert "int count;" in result

    def test_anonymous_struct_skipped(self):
        s = Struct(None, [Field("x", CType("int"))])
        result = decl_to_cffi(s)
        assert result is None

    def test_exclude_patterns(self):
        import re

        f = Function("_internal_func", CType("void"), [])
        result = decl_to_cffi(f, exclude_patterns=[re.compile(r"_internal_")])
        assert result is None


class TestFormatField:
    def test_array_field(self):
        f = Field("buf", Array(CType("char"), 256))
        result = _format_field(f)
        assert result == "    char buf[256];"

    def test_function_pointer_field(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        f = Field("callback", fp)
        result = _format_field(f)
        assert result == "    void (*callback)(int x);"

    def test_regular_field(self):
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"


class TestFormatParams:
    def test_array_parameter(self):
        params = [Parameter("buf", Array(CType("char"), 256))]
        result = _format_params(params, False)
        assert result == "char buf[256]"

    def test_function_pointer_parameter(self):
        fp = FunctionPointer(CType("int"), [Parameter("x", CType("float"))])
        params = [Parameter("cb", fp)]
        result = _format_params(params, False)
        assert result == "int (*cb)(float x)"

    def test_empty_params_void(self):
        result = _format_params([], False)
        assert result == "void"

    def test_variadic_only(self):
        result = _format_params([], True)
        assert result == "..."


class TestHeaderToCffi:
    def test_simple_header(self):
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        result = header_to_cffi(header)
        assert "struct Point" in result
        assert "get_point" in result

    def test_exclude_patterns(self):
        header = Header(
            "test.h",
            [
                Function("public_func", CType("void"), []),
                Function("_private_func", CType("void"), []),
            ],
        )
        result = header_to_cffi(header, exclude_patterns=[r"_private_"])
        assert "public_func" in result
        assert "_private_func" not in result

    def test_opaque_struct_with_typedef_dedup(self):
        """Opaque struct + matching typedef should emit only the typedef."""
        header = Header(
            "test.h",
            [
                Struct("nng_socket", []),  # opaque
                Typedef("nng_socket", CType("struct nng_socket")),
            ],
        )
        result = header_to_cffi(header)
        assert "typedef" in result
        # Should NOT have "struct nng_socket { ...; };" separately
        lines = result.split("\n")
        struct_lines = [line for line in lines if line.startswith("struct nng_socket")]
        assert len(struct_lines) == 0

    def test_enum_typedef_pair_combined(self):
        """Enum + matching typedef should be combined into typedef enum."""
        header = Header(
            "test.h",
            [
                Enum("nng_pipe_ev", [EnumValue("NNG_PIPE_EV_ADD", 0), EnumValue("NNG_PIPE_EV_REM", 1)]),
                Typedef("nng_pipe_ev", CType("enum nng_pipe_ev")),
            ],
        )
        result = header_to_cffi(header)
        assert "typedef enum {" in result
        assert "NNG_PIPE_EV_ADD" in result
        assert "} nng_pipe_ev;" in result

    def test_tag_kind_qualification(self):
        """Bare struct name in typedef should get 'struct' prefix."""
        header = Header(
            "test.h",
            [
                Struct("inner_s", [Field("x", CType("int"))]),
                Typedef("outer_t", CType("inner_s")),
            ],
        )
        result = header_to_cffi(header)
        # The typedef should qualify "inner_s" as "struct inner_s"
        assert "typedef struct inner_s outer_t;" in result


class TestCffiWriter:
    """Tests for the CffiWriter class (protocol-compliant wrapper)."""

    def test_writer_produces_same_output_as_function(self):
        """CffiWriter.write() should produce identical output to header_to_cffi()."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        writer = CffiWriter()
        assert writer.write(header) == header_to_cffi(header)

    def test_writer_with_exclude_patterns(self):
        """CffiWriter should forward exclude_patterns to header_to_cffi."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            "test.h",
            [
                Function("public_func", CType("void"), []),
                Function("_private_func", CType("void"), []),
            ],
        )
        writer = CffiWriter(exclude_patterns=[r"_private_"])
        result = writer.write(header)
        assert "public_func" in result
        assert "_private_func" not in result

    def test_writer_protocol_compliance(self):
        """CffiWriter should satisfy the WriterBackend protocol."""
        from headerkit.writers import WriterBackend
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self):
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert writer.name == "cffi"

    def test_writer_format_description(self):
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert writer.format_description == "CFFI cdef declarations for ffibuilder.cdef()"
