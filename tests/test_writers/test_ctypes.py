"""Tests for the ctypes binding writer."""

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
from headerkit.writers.ctypes import (
    CTYPES_TYPE_MAP,
    CtypesWriter,
    header_to_ctypes,
    type_to_ctypes,
)


class TestTypeMapping:
    """Test C type -> ctypes type mapping for all common types."""

    def test_void(self) -> None:
        assert type_to_ctypes(CType("void")) == "None"

    def test_char(self) -> None:
        assert type_to_ctypes(CType("char")) == "ctypes.c_char"

    def test_signed_char(self) -> None:
        assert type_to_ctypes(CType("signed char")) == "ctypes.c_byte"

    def test_unsigned_char(self) -> None:
        assert type_to_ctypes(CType("unsigned char")) == "ctypes.c_ubyte"

    def test_short(self) -> None:
        assert type_to_ctypes(CType("short")) == "ctypes.c_short"

    def test_unsigned_short(self) -> None:
        assert type_to_ctypes(CType("unsigned short")) == "ctypes.c_ushort"

    def test_int(self) -> None:
        assert type_to_ctypes(CType("int")) == "ctypes.c_int"

    def test_unsigned_int(self) -> None:
        assert type_to_ctypes(CType("unsigned int")) == "ctypes.c_uint"

    def test_unsigned_int_via_qualifiers(self) -> None:
        assert type_to_ctypes(CType("int", ["unsigned"])) == "ctypes.c_uint"

    def test_long(self) -> None:
        assert type_to_ctypes(CType("long")) == "ctypes.c_long"

    def test_unsigned_long(self) -> None:
        assert type_to_ctypes(CType("unsigned long")) == "ctypes.c_ulong"

    def test_long_long(self) -> None:
        assert type_to_ctypes(CType("long long")) == "ctypes.c_longlong"

    def test_unsigned_long_long(self) -> None:
        assert type_to_ctypes(CType("unsigned long long")) == "ctypes.c_ulonglong"

    def test_float(self) -> None:
        assert type_to_ctypes(CType("float")) == "ctypes.c_float"

    def test_double(self) -> None:
        assert type_to_ctypes(CType("double")) == "ctypes.c_double"

    def test_long_double(self) -> None:
        assert type_to_ctypes(CType("long double")) == "ctypes.c_longdouble"

    def test_size_t(self) -> None:
        assert type_to_ctypes(CType("size_t")) == "ctypes.c_size_t"

    def test_ssize_t(self) -> None:
        assert type_to_ctypes(CType("ssize_t")) == "ctypes.c_ssize_t"

    def test_wchar_t(self) -> None:
        assert type_to_ctypes(CType("wchar_t")) == "ctypes.c_wchar"

    def test_bool_underscore(self) -> None:
        assert type_to_ctypes(CType("_Bool")) == "ctypes.c_bool"

    def test_bool(self) -> None:
        assert type_to_ctypes(CType("bool")) == "ctypes.c_bool"

    def test_int8_t(self) -> None:
        assert type_to_ctypes(CType("int8_t")) == "ctypes.c_int8"

    def test_int16_t(self) -> None:
        assert type_to_ctypes(CType("int16_t")) == "ctypes.c_int16"

    def test_int32_t(self) -> None:
        assert type_to_ctypes(CType("int32_t")) == "ctypes.c_int32"

    def test_int64_t(self) -> None:
        assert type_to_ctypes(CType("int64_t")) == "ctypes.c_int64"

    def test_uint8_t(self) -> None:
        assert type_to_ctypes(CType("uint8_t")) == "ctypes.c_uint8"

    def test_uint16_t(self) -> None:
        assert type_to_ctypes(CType("uint16_t")) == "ctypes.c_uint16"

    def test_uint32_t(self) -> None:
        assert type_to_ctypes(CType("uint32_t")) == "ctypes.c_uint32"

    def test_uint64_t(self) -> None:
        assert type_to_ctypes(CType("uint64_t")) == "ctypes.c_uint64"

    def test_const_int_maps_to_c_int(self) -> None:
        """const qualifier should be stripped for ctypes mapping."""
        assert type_to_ctypes(CType("int", ["const"])) == "ctypes.c_int"

    def test_unknown_type_passthrough(self) -> None:
        """Unknown types (user-defined) should pass through as-is."""
        assert type_to_ctypes(CType("MyStruct")) == "MyStruct"

    def test_type_map_completeness(self) -> None:
        """Verify the type map has all expected entries."""
        assert len(CTYPES_TYPE_MAP) == 28


class TestPointerTypes:
    def test_simple_pointer(self) -> None:
        assert type_to_ctypes(Pointer(CType("int"))) == "ctypes.POINTER(ctypes.c_int)"

    def test_double_pointer(self) -> None:
        result = type_to_ctypes(Pointer(Pointer(CType("int"))))
        assert result == "ctypes.POINTER(ctypes.POINTER(ctypes.c_int))"

    def test_const_char_pointer(self) -> None:
        """const char * should map to c_char_p."""
        result = type_to_ctypes(Pointer(CType("char", ["const"])))
        assert result == "ctypes.c_char_p"

    def test_char_pointer(self) -> None:
        """char * should map to c_char_p."""
        result = type_to_ctypes(Pointer(CType("char")))
        assert result == "ctypes.c_char_p"

    def test_void_pointer(self) -> None:
        """void * should map to c_void_p."""
        result = type_to_ctypes(Pointer(CType("void")))
        assert result == "ctypes.c_void_p"

    def test_struct_pointer(self) -> None:
        result = type_to_ctypes(Pointer(CType("MyStruct")))
        assert result == "ctypes.POINTER(MyStruct)"

    def test_pointer_to_function_pointer(self) -> None:
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        result = type_to_ctypes(Pointer(fp))
        assert result == "ctypes.CFUNCTYPE(None, ctypes.c_int)"


class TestArrayTypes:
    def test_fixed_array(self) -> None:
        result = type_to_ctypes(Array(CType("int"), 10))
        assert result == "ctypes.c_int * 10"

    def test_char_array(self) -> None:
        result = type_to_ctypes(Array(CType("char"), 64))
        assert result == "ctypes.c_char * 64"

    def test_flexible_array(self) -> None:
        result = type_to_ctypes(Array(CType("int"), None))
        assert result == "ctypes.POINTER(ctypes.c_int)"


class TestFunctionPointerTypes:
    def test_simple_function_pointer(self) -> None:
        fp = FunctionPointer(CType("void"), [])
        result = type_to_ctypes(fp)
        assert result == "ctypes.CFUNCTYPE(None)"

    def test_function_pointer_with_params(self) -> None:
        fp = FunctionPointer(
            CType("int"),
            [Parameter("a", CType("int")), Parameter("b", CType("float"))],
        )
        result = type_to_ctypes(fp)
        assert result == "ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_float)"

    def test_function_pointer_returning_pointer(self) -> None:
        fp = FunctionPointer(
            Pointer(CType("void")),
            [Parameter("size", CType("size_t"))],
        )
        result = type_to_ctypes(fp)
        assert result == "ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t)"


class TestStructToCtypes:
    def test_struct_with_simple_fields(self) -> None:
        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        result = header_to_ctypes(header)
        assert "class Point(ctypes.Structure):" in result
        assert '("x", ctypes.c_int),' in result
        assert '("y", ctypes.c_int),' in result
        assert "_fields_ = [" in result

    def test_struct_with_bitfields(self) -> None:
        """Bitfields should use the 3-tuple format."""
        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [
                        Field("a", CType("unsigned int"), bit_width=4),
                        Field("b", CType("unsigned int"), bit_width=1),
                    ],
                )
            ],
        )
        result = header_to_ctypes(header)
        assert '("a", ctypes.c_uint, 4),' in result
        assert '("b", ctypes.c_uint, 1),' in result

    def test_packed_struct(self) -> None:
        """Packed structs should have _pack_ = 1."""
        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        result = header_to_ctypes(header)
        assert "_pack_ = 1" in result
        assert "class Packed(ctypes.Structure):" in result

    def test_non_packed_struct_no_pack(self) -> None:
        header = Header(
            "test.h",
            [Struct("Normal", [Field("x", CType("int"))])],
        )
        result = header_to_ctypes(header)
        assert "_pack_" not in result

    def test_union(self) -> None:
        header = Header(
            "test.h",
            [
                Struct(
                    "Data",
                    [Field("i", CType("int")), Field("f", CType("float"))],
                    is_union=True,
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "class Data(ctypes.Union):" in result
        assert '("i", ctypes.c_int),' in result
        assert '("f", ctypes.c_float),' in result

    def test_opaque_struct(self) -> None:
        """Opaque struct (no fields) should use 'pass'."""
        header = Header("test.h", [Struct("Opaque", [])])
        result = header_to_ctypes(header)
        assert "class Opaque(ctypes.Structure):" in result
        assert "    pass" in result
        assert "_fields_" not in result

    def test_opaque_union(self) -> None:
        header = Header("test.h", [Struct("OpaqueU", [], is_union=True)])
        result = header_to_ctypes(header)
        assert "class OpaqueU(ctypes.Union):" in result
        assert "    pass" in result

    def test_array_field(self) -> None:
        header = Header(
            "test.h",
            [Struct("Buf", [Field("data", Array(CType("char"), 64))])],
        )
        result = header_to_ctypes(header)
        assert '("data", ctypes.c_char * 64),' in result

    def test_pointer_field(self) -> None:
        header = Header(
            "test.h",
            [Struct("Node", [Field("next", Pointer(CType("Node")))])],
        )
        result = header_to_ctypes(header)
        assert '("next", ctypes.POINTER(Node)),' in result

    def test_anonymous_struct_skipped(self) -> None:
        header = Header("test.h", [Struct(None, [Field("x", CType("int"))])])
        result = header_to_ctypes(header)
        assert "class " not in result or "class " in result.split("Structures")[0]
        # Actually, the anonymous struct should simply not generate a class
        # Check that no _fields_ line appears
        assert "_fields_" not in result


class TestEnumToCtypes:
    def test_enum_as_constants(self) -> None:
        header = Header(
            "test.h",
            [
                Enum(
                    "Color",
                    [EnumValue("RED", 0), EnumValue("GREEN", 1), EnumValue("BLUE", 2)],
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "# enum Color" in result
        assert "RED = 0" in result
        assert "GREEN = 1" in result
        assert "BLUE = 2" in result

    def test_anonymous_enum(self) -> None:
        header = Header(
            "test.h",
            [Enum(None, [EnumValue("FLAG_A", 1), EnumValue("FLAG_B", 2)])],
        )
        result = header_to_ctypes(header)
        assert "# enum anonymous" in result
        assert "FLAG_A = 1" in result
        assert "FLAG_B = 2" in result

    def test_empty_enum_skipped(self) -> None:
        header = Header("test.h", [Enum("Empty", [])])
        result = header_to_ctypes(header)
        assert "Empty" not in result


class TestFunctionPrototypes:
    def test_simple_function(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "add",
                    CType("int"),
                    [Parameter("a", CType("int")), Parameter("b", CType("int"))],
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "_lib.add.argtypes = [ctypes.c_int, ctypes.c_int]" in result
        assert "_lib.add.restype = ctypes.c_int" in result

    def test_void_return(self) -> None:
        header = Header(
            "test.h",
            [Function("init", CType("void"), [])],
        )
        result = header_to_ctypes(header)
        assert "_lib.init.restype = None" in result

    def test_no_args(self) -> None:
        header = Header(
            "test.h",
            [Function("get_count", CType("int"), [])],
        )
        result = header_to_ctypes(header)
        assert "_lib.get_count.argtypes = []" in result

    def test_variadic_function(self) -> None:
        """Variadic functions should only annotate fixed args."""
        header = Header(
            "test.h",
            [
                Function(
                    "printf",
                    CType("int"),
                    [Parameter("fmt", Pointer(CType("char", ["const"])))],
                    is_variadic=True,
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "_lib.printf.argtypes = [ctypes.c_char_p]" in result
        assert "_lib.printf.restype = ctypes.c_int" in result

    def test_const_char_pointer_param(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "puts",
                    CType("int"),
                    [Parameter("s", Pointer(CType("char", ["const"])))],
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "_lib.puts.argtypes = [ctypes.c_char_p]" in result

    def test_pointer_param(self) -> None:
        header = Header(
            "test.h",
            [
                Function(
                    "process",
                    CType("void"),
                    [Parameter("data", Pointer(CType("int")))],
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "_lib.process.argtypes = [ctypes.POINTER(ctypes.c_int)]" in result

    def test_custom_lib_name(self) -> None:
        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = header_to_ctypes(header, lib_name="mylib")
        assert "mylib.foo.argtypes = []" in result
        assert "mylib.foo.restype = None" in result

    def test_calling_convention_comment(self) -> None:
        header = Header(
            "test.h",
            [Function("WinMain", CType("int"), [], calling_convention="stdcall")],
        )
        result = header_to_ctypes(header)
        assert "# calling convention: stdcall" in result
        assert "_lib.WinMain.restype = ctypes.c_int" in result


class TestFunctionPointerTypedef:
    def test_function_pointer_typedef(self) -> None:
        """Function pointer typedef should use CFUNCTYPE."""
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
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "Callback = ctypes.CFUNCTYPE(None, ctypes.c_void_p)" in result

    def test_direct_function_pointer_typedef(self) -> None:
        """Direct FunctionPointer typedef (without wrapping Pointer) should also work."""
        header = Header(
            "test.h",
            [
                Typedef(
                    "Handler",
                    FunctionPointer(
                        CType("int"),
                        [Parameter("code", CType("int"))],
                    ),
                )
            ],
        )
        result = header_to_ctypes(header)
        assert "Handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)" in result


class TestTypedefToCtypes:
    def test_struct_typedef_alias(self) -> None:
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int"))]),
                Typedef("Point_t", CType("struct Point")),
            ],
        )
        result = header_to_ctypes(header)
        assert "Point_t = Point" in result

    def test_simple_type_alias_comment(self) -> None:
        header = Header(
            "test.h",
            [Typedef("myint", CType("int"))],
        )
        result = header_to_ctypes(header)
        assert "# typedef int -> myint" in result

    def test_pointer_typedef(self) -> None:
        header = Header(
            "test.h",
            [Typedef("intptr", Pointer(CType("int")))],
        )
        result = header_to_ctypes(header)
        assert "intptr = ctypes.POINTER(ctypes.c_int)" in result

    def test_array_typedef(self) -> None:
        header = Header(
            "test.h",
            [Typedef("Buffer", Array(CType("char"), 256))],
        )
        result = header_to_ctypes(header)
        assert "Buffer = ctypes.c_char * 256" in result


class TestConstants:
    def test_integer_constant(self) -> None:
        header = Header("test.h", [Constant("SIZE", 100, is_macro=True)])
        result = header_to_ctypes(header)
        assert "SIZE = 100" in result

    def test_float_constant(self) -> None:
        header = Header("test.h", [Constant("PI", 3.14, is_macro=True)])
        result = header_to_ctypes(header)
        assert "PI = 3.14" in result

    def test_string_constant(self) -> None:
        header = Header("test.h", [Constant("VERSION", '"1.0.0"', is_macro=True)])
        result = header_to_ctypes(header)
        assert 'VERSION = b"1.0.0"' in result

    def test_string_constant_unquoted(self) -> None:
        header = Header("test.h", [Constant("NAME", "hello", is_macro=True)])
        result = header_to_ctypes(header)
        assert 'NAME = b"hello"' in result

    def test_none_value_skipped(self) -> None:
        header = Header("test.h", [Constant("UNKNOWN", None, is_macro=True)])
        result = header_to_ctypes(header)
        assert "UNKNOWN" not in result


class TestModuleStructure:
    def test_docstring_contains_path(self) -> None:
        header = Header("myheader.h", [])
        result = header_to_ctypes(header)
        assert '"""ctypes bindings generated from myheader.h."""' in result

    def test_imports(self) -> None:
        header = Header("test.h", [])
        result = header_to_ctypes(header)
        assert "import ctypes" in result
        assert "import ctypes.util" in result
        assert "import sys" in result

    def test_section_headers_present(self) -> None:
        header = Header(
            "test.h",
            [
                Constant("SIZE", 10),
                Enum("Color", [EnumValue("RED", 0)]),
                Struct("Point", [Field("x", CType("int"))]),
                Typedef("myint", CType("int")),
                Function("foo", CType("void"), []),
            ],
        )
        result = header_to_ctypes(header)
        assert "# Constants" in result
        assert "# Enums" in result
        assert "# Structures and Unions" in result
        assert "# Typedefs" in result
        assert "# Function Prototypes" in result


class TestCtypesWriter:
    """Tests for the CtypesWriter class (protocol-compliant wrapper)."""

    def test_writer_produces_same_output_as_function(self) -> None:
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        writer = CtypesWriter()
        assert writer.write(header) == header_to_ctypes(header)

    def test_writer_custom_lib_name(self) -> None:
        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        writer = CtypesWriter(lib_name="mylib")
        result = writer.write(header)
        assert "mylib.foo.argtypes" in result

    def test_writer_protocol_compliance(self) -> None:
        from headerkit.writers import WriterBackend

        writer = CtypesWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self) -> None:
        writer = CtypesWriter()
        assert writer.name == "ctypes"

    def test_writer_format_description(self) -> None:
        writer = CtypesWriter()
        assert writer.format_description == "Python ctypes bindings"

    def test_writer_registered(self) -> None:
        from headerkit.writers import is_writer_available

        assert is_writer_available("ctypes")

    def test_writer_via_get_writer(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("ctypes")
        assert writer.name == "ctypes"


class TestVariables:
    def test_variable_as_comment(self) -> None:
        header = Header("test.h", [Variable("count", CType("int"))])
        result = header_to_ctypes(header)
        assert "# _lib.count: ctypes.c_int" in result
