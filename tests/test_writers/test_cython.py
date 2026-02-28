"""Tests for the Cython .pxd writer."""

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
from headerkit.writers.cython import CythonWriter, PxdWriter, write_pxd


class TestSimpleStruct:
    """C struct -> cdef struct."""

    def test_struct_with_fields(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                ),
            ],
        )
        result = write_pxd(header)
        assert 'cdef extern from "test.h":' in result
        assert "cdef struct Point:" in result
        assert "    int x" in result
        assert "    int y" in result

    def test_opaque_struct(self) -> None:
        header = Header("test.h", [Struct("Opaque", [])])
        result = write_pxd(header)
        assert "cdef struct Opaque" in result
        # No colon for forward declaration
        lines = result.strip().split("\n")
        opaque_line = [ln for ln in lines if "Opaque" in ln][0]
        assert not opaque_line.rstrip().endswith(":")

    def test_typedef_struct(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int"))],
                    is_typedef=True,
                ),
            ],
        )
        result = write_pxd(header)
        assert "ctypedef struct Point:" in result
        assert "    int x" in result

    def test_union(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Data",
                    [
                        Field("i", CType("int")),
                        Field("f", CType("float")),
                    ],
                    is_union=True,
                ),
            ],
        )
        result = write_pxd(header)
        assert "cdef union Data:" in result
        assert "    int i" in result
        assert "    float f" in result


class TestEnum:
    """C enum -> cdef enum."""

    def test_named_enum(self) -> None:
        header = Header(
            "test.h",
            [
                Enum(
                    "Color",
                    [
                        EnumValue("RED", 0),
                        EnumValue("GREEN", 1),
                        EnumValue("BLUE", 2),
                    ],
                ),
            ],
        )
        result = write_pxd(header)
        assert "cdef enum Color:" in result
        assert "    RED" in result
        assert "    GREEN" in result
        assert "    BLUE" in result

    def test_typedef_enum(self) -> None:
        header = Header(
            "test.h",
            [
                Enum(
                    "Status",
                    [EnumValue("OK", 0), EnumValue("ERR", 1)],
                    is_typedef=True,
                ),
            ],
        )
        result = write_pxd(header)
        assert "ctypedef enum Status:" in result

    def test_empty_enum(self) -> None:
        header = Header("test.h", [Enum("Empty", [])])
        result = write_pxd(header)
        assert "cdef enum Empty:" in result
        assert "    pass" in result


class TestFunction:
    """C function -> function declaration."""

    def test_simple_function(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "add",
                    CType("int"),
                    [
                        Parameter("a", CType("int")),
                        Parameter("b", CType("int")),
                    ],
                ),
            ],
        )
        result = write_pxd(header)
        assert "int add(int a, int b)" in result

    def test_variadic_function(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "printf",
                    CType("int"),
                    [
                        Parameter("fmt", Pointer(CType("char", ["const"]))),
                    ],
                    is_variadic=True,
                ),
            ],
        )
        result = write_pxd(header)
        assert "int printf(const char* fmt, ...)" in result

    def test_void_return(self) -> None:
        header = Header(
            "test.h",
            [Function("init", CType("void"), [])],
        )
        result = write_pxd(header)
        assert "void init()" in result


class TestTypedef:
    """C typedef -> ctypedef."""

    def test_simple_typedef(self) -> None:
        header = Header(
            "test.h",
            [Typedef("myint", CType("int"))],
        )
        result = write_pxd(header)
        assert "ctypedef int myint" in result

    def test_typedef_unsigned_long(self) -> None:
        header = Header(
            "test.h",
            [Typedef("size_type", CType("long", ["unsigned"]))],
        )
        result = write_pxd(header)
        assert "ctypedef unsigned long size_type" in result


class TestConstant:
    """Constants (int, float) -> typed constants."""

    def test_int_constant(self) -> None:
        header = Header(
            "test.h",
            [Constant("SIZE", 100, type=CType("int"))],
        )
        result = write_pxd(header)
        assert "int SIZE" in result

    def test_float_constant(self) -> None:
        header = Header(
            "test.h",
            [Constant("PI", 3.14, type=CType("double"))],
        )
        result = write_pxd(header)
        assert "double PI" in result

    def test_macro_default_int(self) -> None:
        """Macro without detected type defaults to int."""
        header = Header(
            "test.h",
            [Constant("MAX", 255, is_macro=True)],
        )
        result = write_pxd(header)
        assert "int MAX" in result

    def test_string_constant(self) -> None:
        header = Header(
            "test.h",
            [
                Constant(
                    "VERSION",
                    '"1.0"',
                    type=CType("char", ["const"]),
                ),
            ],
        )
        result = write_pxd(header)
        assert "const char* VERSION" in result


class TestCppClass:
    """C++ class -> cdef cppclass."""

    def test_basic_cppclass(self) -> None:
        header = Header(
            "widget.h",
            [
                Struct(
                    "Widget",
                    fields=[Field("width", CType("int"))],
                    methods=[
                        Function(
                            "resize",
                            CType("void"),
                            [
                                Parameter("w", CType("int")),
                                Parameter("h", CType("int")),
                            ],
                        ),
                    ],
                    is_cppclass=True,
                ),
            ],
        )
        result = write_pxd(header)
        assert "cdef cppclass Widget:" in result
        assert "    int width" in result
        assert "    void resize(int w, int h)" in result

    def test_cppclass_with_cpp_name(self) -> None:
        header = Header(
            "lib.h",
            [
                Struct(
                    "MyClass",
                    fields=[Field("val", CType("int"))],
                    is_cppclass=True,
                    cpp_name="my_ns::MyClass",
                ),
            ],
        )
        result = write_pxd(header)
        assert '"my_ns::MyClass"' in result


class TestCppNamespaceGrouping:
    """C++ namespace grouping."""

    def test_namespaced_declarations(self) -> None:
        header = Header(
            "ns.h",
            [
                Function("global_fn", CType("void"), []),
                Struct(
                    "Widget",
                    fields=[Field("x", CType("int"))],
                    is_cppclass=True,
                    namespace="ui",
                ),
            ],
        )
        result = write_pxd(header)
        assert 'cdef extern from "ns.h":' in result
        assert 'cdef extern from "ns.h" namespace "ui":' in result
        assert "void global_fn()" in result
        assert "cdef cppclass Widget:" in result

    def test_multiple_namespaces(self) -> None:
        header = Header(
            "multi.h",
            [
                Struct(
                    "A",
                    fields=[Field("a", CType("int"))],
                    is_cppclass=True,
                    namespace="ns1",
                ),
                Struct(
                    "B",
                    fields=[Field("b", CType("int"))],
                    is_cppclass=True,
                    namespace="ns2",
                ),
            ],
        )
        result = write_pxd(header)
        assert 'namespace "ns1"' in result
        assert 'namespace "ns2"' in result


class TestCppTemplateParameters:
    """C++ template parameters."""

    def test_template_class(self) -> None:
        header = Header(
            "container.h",
            [
                Struct(
                    "Container",
                    fields=[Field("data", Pointer(CType("T")))],
                    is_cppclass=True,
                    template_params=["T"],
                ),
            ],
        )
        result = write_pxd(header)
        assert "cdef cppclass Container[T]:" in result

    def test_multi_template_params(self) -> None:
        header = Header(
            "map.h",
            [
                Struct(
                    "Map",
                    fields=[],
                    methods=[
                        Function(
                            "get",
                            CType("V"),
                            [Parameter("key", CType("K"))],
                        ),
                    ],
                    is_cppclass=True,
                    template_params=["K", "V"],
                ),
            ],
        )
        result = write_pxd(header)
        assert "cdef cppclass Map[K, V]:" in result


class TestFunctionPointerTypedef:
    """Function pointer typedef -> ctypedef function pointer."""

    def test_simple_fnptr_typedef(self) -> None:
        header = Header(
            "test.h",
            [
                Typedef(
                    "Callback",
                    Pointer(
                        FunctionPointer(
                            CType("void"),
                            [Parameter("data", Pointer(CType("void")))],
                        )
                    ),
                ),
            ],
        )
        result = write_pxd(header)
        assert "ctypedef void (*Callback)(void* data)" in result

    def test_fnptr_typedef_no_params(self) -> None:
        header = Header(
            "test.h",
            [
                Typedef(
                    "VoidFn",
                    Pointer(FunctionPointer(CType("void"), [])),
                ),
            ],
        )
        result = write_pxd(header)
        assert "ctypedef void (*VoidFn)()" in result

    def test_direct_fnptr_typedef(self) -> None:
        """FunctionPointer directly as underlying_type (not wrapped in Pointer)."""
        header = Header(
            "test.h",
            [
                Typedef(
                    "Handler",
                    FunctionPointer(
                        CType("int"),
                        [Parameter("code", CType("int"))],
                    ),
                ),
            ],
        )
        result = write_pxd(header)
        assert "ctypedef int (*Handler)(int code)" in result


class TestBitfield:
    """Bitfield field -> comment noting unsupported."""

    def test_bitfield_emits_comment(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [
                        Field("active", CType("unsigned int"), bit_width=1),
                        Field("mode", CType("unsigned int"), bit_width=3),
                        Field("normal", CType("int")),
                    ],
                ),
            ],
        )
        result = write_pxd(header)
        assert "unsigned int active" in result
        assert "# bitfield: 1 bits" in result
        assert "unsigned int mode" in result
        assert "# bitfield: 3 bits" in result
        # Normal field should NOT have bitfield comment
        normal_line = [ln for ln in result.split("\n") if "int normal" in ln][0]
        assert "bitfield" not in normal_line


class TestPackedStruct:
    """Packed struct -> comment noting unsupported."""

    def test_packed_emits_comment(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Packed",
                    [Field("x", CType("int"))],
                    is_packed=True,
                ),
            ],
        )
        result = write_pxd(header)
        assert "# NOTE: packed struct" in result
        assert "cdef struct Packed:" in result

    def test_non_packed_no_comment(self) -> None:
        header = Header(
            "test.h",
            [
                Struct("Normal", [Field("x", CType("int"))]),
            ],
        )
        result = write_pxd(header)
        assert "packed" not in result.lower()


class TestWriterProtocol:
    """WriterBackend protocol compliance."""

    def test_protocol_compliance(self) -> None:
        from headerkit.writers import WriterBackend

        writer = CythonWriter()
        assert isinstance(writer, WriterBackend)

    def test_name(self) -> None:
        writer = CythonWriter()
        assert writer.name == "cython"

    def test_format_description(self) -> None:
        writer = CythonWriter()
        assert writer.format_description == "Cython .pxd declarations for C/C++ interop"

    def test_writer_produces_same_output_as_function(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                ),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        writer = CythonWriter()
        assert writer.write(header) == write_pxd(header)


class TestKeywordEscaping:
    """Keyword escaping (e.g., field named 'class' -> 'class_')."""

    def test_field_named_class(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Node",
                    [
                        Field("class", CType("int")),
                        Field("value", CType("int")),
                    ],
                ),
            ],
        )
        result = write_pxd(header)
        # Should be escaped with C name alias
        assert 'class_ "class"' in result
        assert "    int value" in result

    def test_function_named_import(self) -> None:
        header = Header(
            "test.h",
            [Function("import", CType("void"), [])],
        )
        result = write_pxd(header)
        assert 'import_ "import"' in result

    def test_enum_value_keyword(self) -> None:
        header = Header(
            "test.h",
            [
                Enum(
                    "Tokens",
                    [
                        EnumValue("lambda", 1),
                        EnumValue("NORMAL", 2),
                    ],
                ),
            ],
        )
        result = write_pxd(header)
        assert 'lambda_ "lambda"' in result
        assert "NORMAL" in result

    def test_parameter_keyword_escaping(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "foo",
                    CType("void"),
                    [Parameter("class", CType("int"))],
                ),
            ],
        )
        result = write_pxd(header)
        assert "int class_" in result


class TestVariable:
    def test_global_variable(self) -> None:
        header = Header(
            "test.h",
            [Variable("count", CType("int"))],
        )
        result = write_pxd(header)
        assert "int count" in result

    def test_array_variable(self) -> None:
        header = Header(
            "test.h",
            [Variable("table", Array(CType("int"), 256))],
        )
        result = write_pxd(header)
        assert "int table[256]" in result


class TestCimports:
    """Automatic cimport generation."""

    def test_stdint_cimport(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "get_size",
                    CType("uint32_t"),
                    [],
                ),
            ],
        )
        result = write_pxd(header)
        assert "from libc.stdint cimport uint32_t" in result

    def test_stdio_cimport(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "get_file",
                    Pointer(CType("FILE")),
                    [],
                ),
            ],
        )
        result = write_pxd(header)
        assert "from libc.stdio cimport FILE" in result


class TestCallingConvention:
    """Calling convention -> comment noting unsupported."""

    def test_stdcall_function(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "WinMain",
                    CType("int"),
                    [],
                    calling_convention="stdcall",
                ),
            ],
        )
        result = write_pxd(header)
        assert "int WinMain()" in result
        assert "# calling convention: __stdcall__" in result

    def test_no_calling_convention(self) -> None:
        header = Header(
            "test.h",
            [Function("normal", CType("void"), [])],
        )
        result = write_pxd(header)
        assert "calling convention" not in result


class TestRegistration:
    """Writer is registered and accessible via get_writer."""

    def test_registered(self) -> None:
        from headerkit.writers import is_writer_available

        assert is_writer_available("cython")

    def test_get_writer(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("cython")
        assert writer.name == "cython"


class TestCircularTypedef:
    """Circular typedefs should be skipped."""

    def test_typedef_self_reference_skipped(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int"))],
                    is_typedef=True,
                ),
                Typedef("Point", CType("struct Point")),
            ],
        )
        result = write_pxd(header)
        # Should have the ctypedef struct, not a duplicate "ctypedef Point Point"
        assert "ctypedef struct Point:" in result
        assert "ctypedef Point Point" not in result


class TestBoolType:
    """_Bool -> bint mapping."""

    def test_bool_to_bint(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "is_valid",
                    CType("_Bool"),
                    [Parameter("x", CType("int"))],
                ),
            ],
        )
        result = write_pxd(header)
        assert "bint is_valid(int x)" in result


class TestExternBlock:
    """Extern block formatting."""

    def test_empty_header_has_pass(self) -> None:
        header = Header("empty.h", [])
        result = write_pxd(header)
        assert 'cdef extern from "empty.h":' in result
        assert "    pass" in result


class TestFullOutputFormat:
    """Exact full-text output assertions ported from autopxd2."""

    def test_empty_header_exact(self) -> None:
        header = Header("test.h", [])
        result = write_pxd(header)
        assert result == 'cdef extern from "test.h":\n    pass\n'

    def test_simple_function_exact(self) -> None:
        header = Header(
            "test.h",
            [Function("foo", CType("int"), [])],
        )
        result = write_pxd(header)
        assert result == 'cdef extern from "test.h":\n\n    int foo()\n'

    def test_struct_exact(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef struct Point:\n        int x\n        int y\n'
        assert result == expected

    def test_union_exact(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Data",
                    [Field("i", CType("int")), Field("f", CType("float"))],
                    is_union=True,
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef union Data:\n        int i\n        float f\n'
        assert result == expected

    def test_enum_exact(self) -> None:
        header = Header(
            "test.h",
            [
                Enum(
                    "Color",
                    [
                        EnumValue("RED", 0),
                        EnumValue("GREEN", 1),
                        EnumValue("BLUE", 2),
                    ],
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef enum Color:\n        RED\n        GREEN\n        BLUE\n'
        assert result == expected

    def test_function_exact(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "add",
                    CType("int"),
                    [Parameter("a", CType("int")), Parameter("b", CType("int"))],
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    int add(int a, int b)\n'
        assert result == expected

    def test_variadic_function_exact(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "printf",
                    CType("int"),
                    [Parameter("fmt", Pointer(CType("char", ["const"])))],
                    is_variadic=True,
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    int printf(const char* fmt, ...)\n'
        assert result == expected

    def test_typedef_exact(self) -> None:
        header = Header(
            "test.h",
            [Typedef("myint", CType("int"))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    ctypedef int myint\n'
        assert result == expected

    def test_variable_exact(self) -> None:
        header = Header(
            "test.h",
            [Variable("count", CType("int"))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    int count\n'
        assert result == expected


class TestPointerArrayFormatting:
    """Exact pointer and array formatting ported from autopxd2."""

    def test_double_pointer_exact(self) -> None:
        header = Header(
            "test.h",
            [Variable("ptr", Pointer(Pointer(CType("char"))))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    char** ptr\n'
        assert result == expected

    def test_const_pointer_exact(self) -> None:
        header = Header(
            "test.h",
            [Variable("str_", Pointer(CType("char", ["const"])))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    const char* str_\n'
        assert result == expected

    def test_array_of_pointers_exact(self) -> None:
        header = Header(
            "test.h",
            [Variable("ptrs", Array(Pointer(CType("char")), 10))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    char* ptrs[10]\n'
        assert result == expected

    def test_array_in_struct_exact(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Buffer",
                    [Field("data", Array(CType("char"), 256))],
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef struct Buffer:\n        char data[256]\n'
        assert result == expected

    def test_flexible_array_exact(self) -> None:
        header = Header(
            "test.h",
            [Variable("arr", Array(CType("int"), None))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    int arr[]\n'
        assert result == expected


class TestCimportAdditions:
    """Additional cimport tests ported from autopxd2."""

    def test_multiple_cimport_groups(self) -> None:
        """Multiple module cimports are grouped correctly."""
        header = Header(
            "test.h",
            [
                Function(
                    "test",
                    CType("uint32_t"),
                    [Parameter("f", Pointer(CType("FILE")))],
                ),
            ],
        )
        result = write_pxd(header)
        assert "from libc.stdint cimport" in result
        assert "from libc.stdio cimport" in result

    def test_cimports_before_extern(self) -> None:
        """Cimport statements appear before extern from block."""
        header = Header(
            "test.h",
            [
                Function("test", CType("uint32_t"), []),
            ],
        )
        result = write_pxd(header)
        cimport_pos = result.find("from libc.stdint cimport")
        extern_pos = result.find('cdef extern from "test.h"')
        assert cimport_pos < extern_pos, "cimport should precede extern from"

    def test_no_duplicate_cimports(self) -> None:
        """Same type used multiple times generates single cimport."""
        header = Header(
            "test.h",
            [
                Function("func1", CType("uint32_t"), []),
                Function(
                    "func2",
                    CType("uint32_t"),
                    [Parameter("x", CType("uint32_t"))],
                ),
            ],
        )
        result = write_pxd(header)
        cimport_line = [line for line in result.split("\n") if "from libc.stdint cimport" in line][0]
        assert cimport_line.count("uint32_t") == 1

    def test_size_t_no_cimport(self) -> None:
        """size_t is a Cython built-in, so no cimport needed."""
        header = Header(
            "test.h",
            [Struct("Data", [Field("val", CType("size_t"))])],
        )
        result = write_pxd(header)
        assert "cimport" not in result
        assert "size_t val" in result

    def test_stdint_in_function_param(self) -> None:
        """stdint type in function parameter generates cimport."""
        header = Header(
            "test.h",
            [
                Function(
                    "foo",
                    CType("void"),
                    [Parameter("n", CType("uint16_t"))],
                ),
            ],
        )
        result = write_pxd(header)
        expected = 'from libc.stdint cimport uint16_t\n\ncdef extern from "test.h":\n\n    void foo(uint16_t n)\n'
        assert result == expected


class TestQualifiedTypes:
    """Type qualifier formatting ported from autopxd2."""

    def test_const_variable(self) -> None:
        header = Header(
            "test.h",
            [Variable("val", CType("int", ["const"]))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    const int val\n'
        assert result == expected

    def test_volatile_variable(self) -> None:
        header = Header(
            "test.h",
            [Variable("flag", CType("int", ["volatile"]))],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    volatile int flag\n'
        assert result == expected


class TestComplexCases:
    """Complex declaration cases ported from autopxd2."""

    def test_struct_prefix_stripped_for_known_type(self) -> None:
        """'struct Point' return type becomes 'Point' when Point is declared."""
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int"))]),
                Function("get_point", CType("struct Point"), []),
            ],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef struct Point:\n        int x\n\n    Point get_point()\n'
        assert result == expected

    def test_empty_struct_forward_declaration(self) -> None:
        """Empty struct is a forward declaration (no colon, no pass)."""
        header = Header(
            "test.h",
            [Struct("Empty", [])],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef struct Empty\n'
        assert result == expected

    def test_anonymous_enum_format(self) -> None:
        """Anonymous enum has no name after 'cdef enum'."""
        header = Header(
            "test.h",
            [Enum(None, [EnumValue("VALUE", 42)])],
        )
        result = write_pxd(header)
        expected = 'cdef extern from "test.h":\n\n    cdef enum:\n        VALUE\n'
        assert result == expected


class TestStubCimportPrefix:
    """Stub cimport prefix feature for emitting stub type cimports."""

    def test_stub_cimport_prefix_none_no_output(self) -> None:
        """Default behavior: no stub cimport lines emitted."""
        header = Header(
            "test.h",
            [
                Function(
                    "vprintf_wrapper",
                    CType("void"),
                    [Parameter("args", CType("va_list"))],
                ),
            ],
        )
        result = write_pxd(header)
        assert "cimport" not in result
        assert "va_list args" in result

    def test_stub_cimport_prefix_emits_cimport(self) -> None:
        """With prefix, emits 'from prefix.stdarg cimport va_list'."""
        header = Header(
            "test.h",
            [
                Function(
                    "vprintf_wrapper",
                    CType("void"),
                    [Parameter("args", CType("va_list"))],
                ),
            ],
        )
        result = write_pxd(header, stub_cimport_prefix="test.stubs")
        assert "from test.stubs.stdarg cimport va_list" in result

    def test_stub_cimport_prefix_multiple_types(self) -> None:
        """Multiple stub types from different modules emit separate cimport lines."""
        header = Header(
            "test.h",
            [
                Function(
                    "fn1",
                    CType("void"),
                    [Parameter("args", CType("va_list"))],
                ),
                Function(
                    "fn2",
                    CType("void"),
                    [Parameter("addr", Pointer(CType("sockaddr")))],
                ),
            ],
        )
        result = write_pxd(header, stub_cimport_prefix="test.stubs")
        assert "from test.stubs.stdarg cimport va_list" in result
        assert "from test.stubs.sys_socket cimport sockaddr" in result

    def test_stub_cimport_prefix_via_pxd_writer(self) -> None:
        """PxdWriter accepts stub_cimport_prefix directly."""
        header = Header(
            "test.h",
            [
                Function(
                    "fn",
                    CType("void"),
                    [Parameter("args", CType("va_list"))],
                ),
            ],
        )
        writer = PxdWriter(header, stub_cimport_prefix="my.pkg.stubs")
        result = writer.write()
        assert "from my.pkg.stubs.stdarg cimport va_list" in result
