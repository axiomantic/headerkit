"""Tests for the prompt writer."""

from __future__ import annotations

import json

import pytest

from headerkit.ir import (
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

# =============================================================================
# Compact mode tests
# =============================================================================


class TestCompactStruct:
    """Struct rendering in compact mode."""

    def test_struct_one_line(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "STRUCT Point {x:int, y:int}" in output

    def test_opaque_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Struct("Handle", [])])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "STRUCT Handle (opaque)" in output

    def test_packed_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "STRUCT __packed Packed {x:int}" in output

    def test_union(self) -> None:
        from headerkit.writers.prompt import PromptWriter

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
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "UNION Data {i:int, f:float}" in output

    def test_bitfield(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [Field("flags", CType("uint32"), bit_width=4)],
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "flags:uint32:4b" in output


class TestCompactEnum:
    """Enum rendering in compact mode."""

    def test_enum_one_line(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Enum(
                    "Status",
                    [
                        EnumValue("OK", 0),
                        EnumValue("ERROR", 1),
                        EnumValue("TIMEOUT", 2),
                    ],
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "ENUM Status: OK=0, ERROR=1, TIMEOUT=2" in output

    def test_enum_with_auto_values(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", None), EnumValue("GREEN", 1)])],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "RED, GREEN=1" in output


class TestCompactFunction:
    """Function rendering in compact mode."""

    def test_function_with_params_and_return(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Function(
                    "open",
                    Pointer(CType("Handle")),
                    [
                        Parameter("path", Pointer(CType("char", ["const"]))),
                        Parameter("flags", CType("int")),
                    ],
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "FUNC open(path:const char*, flags:int) -> Handle*" in output

    def test_variadic_function(self) -> None:
        from headerkit.writers.prompt import PromptWriter

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
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "FUNC printf(fmt:const char*, ...) -> int" in output

    def test_void_function(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Function("init", CType("void"), [])])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "FUNC init() -> void" in output


class TestCompactConstant:
    """Constant rendering in compact mode."""

    def test_constant(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("MYLIB_VERSION", 42)])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "CONST MYLIB_VERSION=42" in output

    def test_constant_string_value(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("VERSION", '"1.0"')])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert 'CONST VERSION="1.0"' in output

    def test_constant_no_value(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("UNKNOWN", None)])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "CONST UNKNOWN=?" in output


class TestCompactTypedef:
    """Typedef rendering in compact mode."""

    def test_typedef_simple(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Typedef("myint", CType("int", ["unsigned"]))],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "TYPEDEF myint = unsigned int" in output

    def test_function_pointer_typedef_as_callback(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Typedef(
                    "EventCb",
                    FunctionPointer(
                        CType("void"),
                        [
                            Parameter("id", CType("int")),
                            Parameter("ctx", Pointer(CType("void"))),
                        ],
                    ),
                )
            ],
        )
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "CALLBACK EventCb(id:int, ctx:void*) -> void" in output


class TestCompactVariable:
    """Variable rendering in compact mode."""

    def test_variable(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Variable("count", CType("int"))])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert "VAR count:int" in output


class TestCompactHeader:
    """Header-level compact rendering."""

    def test_header_comment(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("mylib.h", [])
        writer = PromptWriter(verbosity="compact")
        output = writer.write(header)
        assert output.startswith("// mylib.h (headerkit compact)\n")


# =============================================================================
# Standard mode tests
# =============================================================================


class TestStandardMode:
    """Standard (YAML-like) mode rendering."""

    def test_header_comment(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("mylib.h", [])
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert output.startswith("# mylib.h (headerkit standard)\n")

    def test_sections_grouped_by_type(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Constant("SIZE", 100),
                Enum("Color", [EnumValue("RED", 0)]),
                Struct("Point", [Field("x", CType("int"))]),
                Function("init", CType("void"), []),
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        # Sections appear in order
        const_pos = output.index("constants:")
        enum_pos = output.index("enums:")
        struct_pos = output.index("structs:")
        func_pos = output.index("functions:")
        assert const_pos < enum_pos < struct_pos < func_pos

    def test_empty_sections_omitted(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "functions:" in output
        assert "constants:" not in output
        assert "enums:" not in output
        assert "structs:" not in output
        assert "typedefs:" not in output
        assert "variables:" not in output

    def test_all_parameter_names_present(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Function(
                    "foo",
                    CType("int"),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("float")),
                    ],
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "x: int" in output
        assert "y: float" in output

    def test_struct_fields_with_types(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "fields:" in output
        assert "x: int" in output
        assert "y: int" in output

    def test_opaque_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Struct("Handle", [])])
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "opaque: true" in output

    def test_packed_struct(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "packed: true" in output

    def test_bitfield_in_standard(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct(
                    "Flags",
                    [Field("flags", CType("uint32"), bit_width=4)],
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "uint32 (4 bits)" in output

    def test_callback_section(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Typedef(
                    "Callback",
                    FunctionPointer(
                        CType("void"),
                        [Parameter("data", Pointer(CType("void")))],
                    ),
                )
            ],
        )
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "callbacks:" in output
        assert "typedefs:" not in output

    def test_variadic_function_standard(self) -> None:
        from headerkit.writers.prompt import PromptWriter

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
        writer = PromptWriter(verbosity="standard")
        output = writer.write(header)
        assert "..." in output
        assert "fmt" in output


# =============================================================================
# Verbose mode tests
# =============================================================================


class TestVerboseMode:
    """Verbose mode (JSON with cross-references)."""

    def test_verbose_is_valid_json(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function(
                    "make_point",
                    CType("Point"),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("int")),
                    ],
                ),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)
        assert "declarations" in parsed

    def test_cross_references_present(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function(
                    "make_point",
                    CType("Point"),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("int")),
                    ],
                ),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)

        # Point struct should have used_in referencing make_point
        point_decl = parsed["declarations"][0]
        assert point_decl["name"] == "Point"
        assert "used_in" in point_decl
        assert "make_point" in point_decl["used_in"]

    def test_cross_references_via_pointer(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Handle", []),
                Function(
                    "get_handle",
                    Pointer(CType("Handle")),
                    [],
                ),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)

        handle_decl = parsed["declarations"][0]
        assert handle_decl["name"] == "Handle"
        assert "used_in" in handle_decl
        assert "get_handle" in handle_decl["used_in"]

    def test_no_cross_refs_when_not_referenced(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Struct("Unused", [Field("x", CType("int"))]),
                Function("foo", CType("void"), []),
            ],
        )
        writer = PromptWriter(verbosity="verbose")
        output = writer.write(header)
        parsed = json.loads(output)

        unused_decl = parsed["declarations"][0]
        assert unused_decl["name"] == "Unused"
        assert "used_in" not in unused_decl


# =============================================================================
# General tests
# =============================================================================


class TestPromptWriterGeneral:
    """General PromptWriter behavior."""

    def test_invalid_verbosity_raises(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        with pytest.raises(ValueError, match="Unknown verbosity"):
            PromptWriter(verbosity="extra")

    def test_default_verbosity_is_compact(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        header = Header("test.h", [Constant("X", 1)])
        writer = PromptWriter()
        output = writer.write(header)
        assert "// test.h (headerkit compact)" in output

    def test_writer_protocol_compliance(self) -> None:
        from headerkit.writers import WriterBackend
        from headerkit.writers.prompt import PromptWriter

        writer = PromptWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        writer = PromptWriter()
        assert writer.name == "prompt"

    def test_writer_format_description(self) -> None:
        from headerkit.writers.prompt import PromptWriter

        writer = PromptWriter()
        assert writer.format_description == "Token-optimized output for LLM context"

    def test_via_registry(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("prompt")
        header = Header("test.h", [Constant("X", 1)])
        output = writer.write(header)
        assert "CONST X=1" in output

    def test_via_registry_with_kwargs(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("prompt", verbosity="standard")
        header = Header("test.h", [Constant("X", 1)])
        output = writer.write(header)
        assert "constants:" in output

    def test_token_ordering(self) -> None:
        """compact < standard < verbose token count for same header."""
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Constant("SIZE", 100),
                Enum(
                    "Status",
                    [EnumValue("OK", 0), EnumValue("ERROR", 1)],
                ),
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                ),
                Function(
                    "create",
                    Pointer(CType("Point")),
                    [
                        Parameter("x", CType("int")),
                        Parameter("y", CType("int")),
                    ],
                ),
                Typedef("PointPtr", Pointer(CType("Point"))),
                Variable("origin", CType("Point")),
            ],
        )
        compact = PromptWriter(verbosity="compact").write(header)
        standard = PromptWriter(verbosity="standard").write(header)
        verbose = PromptWriter(verbosity="verbose").write(header)

        assert len(compact) < len(standard) < len(verbose)

    def test_mixed_declarations(self) -> None:
        """Header with all declaration types renders in all modes."""
        from headerkit.writers.prompt import PromptWriter

        header = Header(
            "test.h",
            [
                Constant("VER", 1),
                Enum("E", [EnumValue("A", 0)]),
                Struct("S", [Field("x", CType("int"))]),
                Function("f", CType("void"), [Parameter("a", CType("int"))]),
                Typedef("T", CType("int")),
                Variable("v", CType("int")),
                Typedef(
                    "Cb",
                    FunctionPointer(CType("void"), [Parameter("x", CType("int"))]),
                ),
            ],
        )

        # Compact mode: verify ALL 7 declaration types appear
        compact_output = PromptWriter(verbosity="compact").write(header)
        assert "CONST VER=1" in compact_output
        assert "ENUM E: A=0" in compact_output
        assert "STRUCT S {x:int}" in compact_output
        assert "FUNC f(a:int) -> void" in compact_output
        assert "TYPEDEF T = int" in compact_output
        assert "VAR v:int" in compact_output
        assert "CALLBACK Cb(x:int) -> void" in compact_output

        # Standard mode: verify ALL section headers present
        standard_output = PromptWriter(verbosity="standard").write(header)
        assert "constants:" in standard_output
        assert "enums:" in standard_output
        assert "structs:" in standard_output
        assert "functions:" in standard_output
        assert "typedefs:" in standard_output
        assert "variables:" in standard_output
        assert "callbacks:" in standard_output

        # Verbose mode: verify valid JSON with all declarations
        verbose_output = PromptWriter(verbosity="verbose").write(header)
        parsed = json.loads(verbose_output)
        assert len(parsed["declarations"]) == 7
        names = {d["name"] for d in parsed["declarations"]}
        assert names == {"VER", "E", "S", "f", "T", "v", "Cb"}
