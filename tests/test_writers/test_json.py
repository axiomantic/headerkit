"""Tests for the JSON writer."""

import json

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
    SourceLocation,
    Struct,
    Typedef,
    Variable,
)


class TestHeaderToJson:
    """Tests for the header_to_json() function."""

    def test_empty_header(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [])
        result = json.loads(header_to_json(header))
        assert result["path"] == "test.h"
        assert result["declarations"] == []
        assert "included_headers" not in result

    def test_function_declaration(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("int"), [Parameter("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "function"
        assert decl["name"] == "foo"
        assert decl["return_type"] == {"kind": "ctype", "name": "int"}
        assert len(decl["parameters"]) == 1
        assert decl["parameters"][0]["name"] == "x"
        assert decl["parameters"][0]["type"] == {"kind": "ctype", "name": "int"}
        assert decl["is_variadic"] is False

    def test_struct_with_fields(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "struct"
        assert decl["name"] == "Point"
        assert len(decl["fields"]) == 2
        assert decl["fields"][0] == {"name": "x", "type": {"kind": "ctype", "name": "int"}}
        assert decl["fields"][1] == {"name": "y", "type": {"kind": "ctype", "name": "int"}}

    def test_union(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Data", [Field("i", CType("int"))], is_union=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "union"

    def test_enum_with_values(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "enum"
        assert decl["name"] == "Color"
        assert decl["values"] == [
            {"name": "RED", "value": 0},
            {"name": "GREEN", "value": 1},
        ]

    def test_enum_value_auto_increment(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Enum("Flags", [EnumValue("A", None), EnumValue("B", 5)])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["values"] == [
            {"name": "A"},
            {"name": "B", "value": 5},
        ]

    def test_typedef(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Typedef("myint", CType("int", ["unsigned"]))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "typedef"
        assert decl["name"] == "myint"
        assert decl["underlying_type"] == {
            "kind": "ctype",
            "name": "int",
            "qualifiers": ["unsigned"],
        }

    def test_variable(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("count", CType("int"))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "variable"
        assert decl["name"] == "count"
        assert decl["type"] == {"kind": "ctype", "name": "int"}

    def test_constant(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("SIZE", 100, is_macro=True)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "constant"
        assert decl["name"] == "SIZE"
        assert decl["value"] == 100
        assert decl["is_macro"] is True

    def test_constant_with_type(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("MAX", 255, type=CType("int"))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {"kind": "ctype", "name": "int"}

    def test_constant_no_value(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("UNKNOWN", None)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["kind"] == "constant"
        assert decl["name"] == "UNKNOWN"
        assert "value" not in decl

    def test_constant_not_macro_omits_is_macro(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Constant("VAL", 42)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "is_macro" not in decl

    def test_constant_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Constant("SIZE", 100, location=SourceLocation("test.h", 5))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 5}

    def test_pointer_type(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("ptr", Pointer(CType("int")))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "pointer",
            "pointee": {"kind": "ctype", "name": "int"},
        }

    def test_pointer_with_qualifiers(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("ptr", Pointer(CType("int"), ["const"]))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "pointer",
            "pointee": {"kind": "ctype", "name": "int"},
            "qualifiers": ["const"],
        }

    def test_array_type(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("arr", Array(CType("int"), 10))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "array",
            "element_type": {"kind": "ctype", "name": "int"},
            "size": 10,
        }

    def test_array_flexible(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("arr", Array(CType("char"), None))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "array",
            "element_type": {"kind": "ctype", "name": "char"},
        }

    def test_array_symbolic_size(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("buf", Array(CType("char"), "BUFFER_SIZE"))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"]["size"] == "BUFFER_SIZE"

    def test_function_pointer_type(self) -> None:
        from headerkit.writers.json import header_to_json

        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))], is_variadic=False)
        header = Header("test.h", [Variable("cb", fp)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"]["kind"] == "function_pointer"
        assert decl["type"]["return_type"] == {"kind": "ctype", "name": "void"}
        assert decl["type"]["is_variadic"] is False

    def test_nested_pointer(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [Variable("pp", Pointer(Pointer(CType("char"))))])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"] == {
            "kind": "pointer",
            "pointee": {
                "kind": "pointer",
                "pointee": {"kind": "ctype", "name": "char"},
            },
        }

    def test_included_headers(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [], included_headers={"stdlib.h", "aaa.h"})
        result = json.loads(header_to_json(header))
        assert result["included_headers"] == ["aaa.h", "stdlib.h"]

    def test_indent_none_compact(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [])
        result = header_to_json(header, indent=None)
        assert "\n" not in result

    def test_indent_custom(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header("test.h", [])
        result = header_to_json(header, indent=4)
        # Should be valid JSON with 4-space indent
        parsed = json.loads(result)
        assert parsed["path"] == "test.h"
        # Verify 4-space indent in raw output
        assert "    " in result

    def test_output_is_valid_json(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [
                Struct("S", [Field("x", CType("int"))]),
                Enum("E", [EnumValue("A", 0)]),
                Function("f", CType("void"), []),
                Typedef("T", CType("int")),
                Variable("v", CType("int")),
                Constant("C", 42),
            ],
        )
        result = header_to_json(header)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert len(parsed["declarations"]) == 6
        kinds = {d["kind"] for d in parsed["declarations"]}
        assert kinds == {"struct", "enum", "function", "typedef", "variable", "constant"}

    def test_variadic_function(self) -> None:
        from headerkit.writers.json import header_to_json

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
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["is_variadic"] is True

    def test_function_with_namespace(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [], namespace="ns")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["namespace"] == "ns"

    def test_function_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [], location=SourceLocation("test.h", 42, 1))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 42, "column": 1}

    def test_function_omits_namespace_when_none(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "namespace" not in decl

    def test_enum_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Enum("Color", [EnumValue("RED", 0)], location=SourceLocation("test.h", 3))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 3}

    def test_typedef_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Typedef("myint", CType("int"), location=SourceLocation("test.h", 7))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 7}

    def test_variable_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Variable("count", CType("int"), location=SourceLocation("test.h", 12, 3))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 12, "column": 3}

    def test_anonymous_parameter(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [Parameter(None, CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        param = result["declarations"][0]["parameters"][0]
        assert "name" not in param
        assert param["type"] == {"kind": "ctype", "name": "int"}


class TestHeaderToJsonDict:
    """Tests for the header_to_json_dict() function."""

    def test_returns_dict(self) -> None:
        from headerkit.writers.json import header_to_json_dict

        header = Header("test.h", [Function("foo", CType("void"), [])])
        result = header_to_json_dict(header)
        assert isinstance(result, dict)
        assert result["path"] == "test.h"
        assert len(result["declarations"]) == 1

    def test_dict_is_json_serializable(self) -> None:
        from headerkit.writers.json import header_to_json_dict

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))])],
        )
        result = header_to_json_dict(header)
        # Round-trip: serialize and deserialize should produce identical dict
        json_str = json.dumps(result)
        assert json.loads(json_str) == result


class TestJsonWriter:
    """Tests for the JsonWriter class (protocol-compliant wrapper)."""

    def test_writer_protocol_compliance(self) -> None:
        from headerkit.writers import WriterBackend
        from headerkit.writers.json import JsonWriter

        writer = JsonWriter()
        assert isinstance(writer, WriterBackend)

    def test_writer_name(self) -> None:
        from headerkit.writers.json import JsonWriter

        writer = JsonWriter()
        assert writer.name == "json"

    def test_writer_format_description(self) -> None:
        from headerkit.writers.json import JsonWriter

        writer = JsonWriter()
        assert writer.format_description == "JSON serialization of IR for inspection and tooling"

    def test_writer_default_indent(self) -> None:
        from headerkit.writers.json import JsonWriter

        header = Header("test.h", [Function("foo", CType("void"), [])])
        writer = JsonWriter()
        result = writer.write(header)
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "foo"
        # Default indent=2 means newlines present
        assert "\n" in result

    def test_writer_custom_indent(self) -> None:
        from headerkit.writers.json import JsonWriter

        header = Header("test.h", [])
        writer = JsonWriter(indent=None)
        result = writer.write(header)
        assert "\n" not in result

    def test_via_registry(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("json")
        header = Header("test.h", [Function("bar", CType("int"), [])])
        result = writer.write(header)
        parsed = json.loads(result)
        assert parsed["declarations"][0]["name"] == "bar"

    def test_via_registry_with_kwargs(self) -> None:
        from headerkit.writers import get_writer

        writer = get_writer("json", indent=None)
        header = Header("test.h", [])
        result = writer.write(header)
        assert "\n" not in result


class TestStructSerialization:
    """Detailed struct serialization edge cases."""

    def test_struct_with_location(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))], location=SourceLocation("test.h", 10, 5))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 10, "column": 5}

    def test_struct_with_location_no_column(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))], location=SourceLocation("test.h", 10))],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["location"] == {"file": "test.h", "line": 10}

    def test_cppclass(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Widget", [], is_cppclass=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["is_cppclass"] is True

    def test_struct_with_methods(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [
                Struct(
                    "Obj",
                    [Field("val", CType("int"))],
                    methods=[Function("get", CType("int"), [])],
                )
            ],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert len(decl["methods"]) == 1
        assert decl["methods"][0]["kind"] == "function"
        assert decl["methods"][0]["name"] == "get"

    def test_struct_with_namespace(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Foo", [], namespace="ns")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["namespace"] == "ns"

    def test_struct_with_template_params(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Vec", [], template_params=["T"])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["template_params"] == ["T"]

    def test_struct_with_cpp_name(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Vec", [], cpp_name="std::vector")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["cpp_name"] == "std::vector"

    def test_struct_with_notes(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [], notes=["opaque type"])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["notes"] == ["opaque type"]

    def test_struct_with_inner_typedefs(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [], inner_typedefs={"value_type": "int"})],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["inner_typedefs"] == {"value_type": "int"}

    def test_struct_omits_empty_optional_fields(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        # These should NOT be present when empty/None/False
        assert "methods" not in decl
        assert "is_cppclass" not in decl
        assert "namespace" not in decl
        assert "template_params" not in decl
        assert "cpp_name" not in decl
        assert "notes" not in decl
        assert "inner_typedefs" not in decl
        assert "location" not in decl

    def test_struct_is_typedef(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))], is_typedef=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["is_typedef"] is True

    def test_struct_anonymous(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct(None, [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["name"] is None

    def test_struct_is_packed(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Packed", [Field("x", CType("int"))], is_packed=True)],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["is_packed"] is True

    def test_struct_not_packed_omits_is_packed(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Normal", [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "is_packed" not in decl


class TestFieldSerialization:
    """Tests for Field serialization with new fields."""

    def test_field_with_bit_width(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("Flags", [Field("flags", CType("unsigned int"), bit_width=4)])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert field["name"] == "flags"
        assert field["bit_width"] == 4

    def test_field_without_bit_width_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert "bit_width" not in field

    def test_field_with_anonymous_struct(self) -> None:
        from headerkit.writers.json import header_to_json

        anon = Struct(None, [Field("a", CType("int")), Field("b", CType("float"))])
        header = Header(
            "test.h",
            [Struct("Outer", [Field("inner", CType("int"), anonymous_struct=anon)])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert "anonymous_struct" in field
        anon_dict = field["anonymous_struct"]
        assert anon_dict["kind"] == "struct"
        assert len(anon_dict["fields"]) == 2
        assert anon_dict["fields"][0]["name"] == "a"
        assert anon_dict["fields"][1]["name"] == "b"

    def test_field_with_anonymous_union(self) -> None:
        from headerkit.writers.json import header_to_json

        anon = Struct(None, [Field("i", CType("int")), Field("f", CType("float"))], is_union=True)
        header = Header(
            "test.h",
            [Struct("Data", [Field("u", CType("int"), anonymous_struct=anon)])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        anon_dict = field["anonymous_struct"]
        assert anon_dict["kind"] == "union"

    def test_field_without_anonymous_struct_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Struct("S", [Field("x", CType("int"))])],
        )
        result = json.loads(header_to_json(header))
        field = result["declarations"][0]["fields"][0]
        assert "anonymous_struct" not in field


class TestCallingConventionSerialization:
    """Tests for calling convention serialization."""

    def test_function_with_calling_convention(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("WinMain", CType("int"), [], calling_convention="stdcall")],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["calling_convention"] == "stdcall"

    def test_function_without_calling_convention_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        header = Header(
            "test.h",
            [Function("foo", CType("void"), [])],
        )
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "calling_convention" not in decl

    def test_function_pointer_with_calling_convention(self) -> None:
        from headerkit.writers.json import header_to_json

        fp = FunctionPointer(CType("void"), [], calling_convention="stdcall")
        header = Header("test.h", [Variable("cb", fp)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert decl["type"]["calling_convention"] == "stdcall"

    def test_function_pointer_without_calling_convention_omits_key(self) -> None:
        from headerkit.writers.json import header_to_json

        fp = FunctionPointer(CType("void"), [])
        header = Header("test.h", [Variable("cb", fp)])
        result = json.loads(header_to_json(header))
        decl = result["declarations"][0]
        assert "calling_convention" not in decl["type"]
