"""Tests for JSON IR deserialization (round-trip invariant)."""

from __future__ import annotations

from dataclasses import asdict

from headerkit.ir import (
    Array,
    BaseSpecifier,
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
    Reference,
    SourceLocation,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers.json import header_to_json, header_to_json_dict


def _round_trip(header: Header) -> Header:
    """Serialize then deserialize a Header, returning the reconstructed copy."""
    from headerkit._ir_json import json_to_header

    d = header_to_json_dict(header)
    return json_to_header(d)


def _round_trip_json_str(header: Header) -> Header:
    """Round-trip through JSON string (not dict)."""
    from headerkit._ir_json import json_to_header

    s = header_to_json(header)
    return json_to_header(s)


class TestRoundTripTypes:
    """Round-trip tests for each TypeExpr variant."""

    def test_ctype_simple(self) -> None:
        h = Header("t.h", [Variable("x", CType("int"))])
        assert _round_trip(h) == h

    def test_ctype_with_qualifiers(self) -> None:
        h = Header("t.h", [Variable("x", CType("long", ["unsigned", "const"]))])
        assert _round_trip(h) == h

    def test_pointer(self) -> None:
        h = Header("t.h", [Variable("p", Pointer(CType("char")))])
        assert _round_trip(h) == h

    def test_pointer_with_qualifiers(self) -> None:
        h = Header("t.h", [Variable("p", Pointer(CType("int"), ["const"]))])
        assert _round_trip(h) == h

    def test_nested_pointer(self) -> None:
        h = Header("t.h", [Variable("pp", Pointer(Pointer(CType("char"))))])
        assert _round_trip(h) == h

    def test_array_fixed(self) -> None:
        h = Header("t.h", [Variable("a", Array(CType("int"), 10))])
        assert _round_trip(h) == h

    def test_array_flexible(self) -> None:
        h = Header("t.h", [Variable("a", Array(CType("char"), None))])
        assert _round_trip(h) == h

    def test_array_symbolic(self) -> None:
        h = Header("t.h", [Variable("a", Array(CType("char"), "BUFSIZ"))])
        assert _round_trip(h) == h

    def test_function_pointer_simple(self) -> None:
        fp = FunctionPointer(CType("void"), [])
        h = Header("t.h", [Typedef("cb", fp)])
        assert _round_trip(h) == h

    def test_function_pointer_with_params(self) -> None:
        fp = FunctionPointer(
            CType("int"),
            [Parameter("a", CType("int")), Parameter(None, Pointer(CType("void")))],
            is_variadic=True,
            calling_convention="stdcall",
        )
        h = Header("t.h", [Typedef("cb", fp)])
        assert _round_trip(h) == h


class TestRoundTripDeclarations:
    """Round-trip tests for each Declaration variant."""

    def test_struct_simple(self) -> None:
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_anonymous(self) -> None:
        s = Struct(None, [Field("val", CType("int"))])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_union(self) -> None:
        u = Struct("Data", [Field("i", CType("int"))], is_union=True)
        h = Header("t.h", [u])
        assert _round_trip(h) == h

    def test_struct_cppclass_with_methods(self) -> None:
        method = Function("resize", CType("void"), [Parameter("w", CType("int"))])
        s = Struct(
            "Widget",
            [Field("width", CType("int"))],
            methods=[method],
            is_cppclass=True,
            namespace="ui",
            template_params=["T"],
            cpp_name="Widget<T>",
        )
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_cpp_class_semantics(self) -> None:
        ctor = Function(
            "Model",
            CType("void"),
            [Parameter("seed", CType("int"))],
            access="public",
            is_explicit=True,
        )
        dtor = Function(
            "~Model",
            CType("void"),
            [],
            access="public",
            is_virtual=True,
            is_defaulted=True,
        )
        pure_method = Function(
            "getNumRows",
            CType("int"),
            [],
            access="public",
            is_virtual=True,
            is_pure_virtual=True,
        )
        const_method = Function(
            "publicMethod",
            CType("int"),
            [Parameter("s", Pointer(CType("char", ["const"])))],
            access="public",
            is_const=True,
        )
        static_method = Function(
            "staticMethod",
            CType("int"),
            [],
            access="public",
            is_static=True,
        )
        conv = Function(
            "operator int",
            CType("int"),
            [],
            access="public",
            is_const=True,
        )
        field_pub = Field("pubField", CType("int"), access="public")
        field_priv = Field("privateField", CType("int"), access="private")
        static_field = Field("staticField", CType("int"), access="public", is_static=True)

        s = Struct(
            "Model",
            fields=[field_pub, field_priv, static_field],
            methods=[pure_method, const_method, static_method],
            bases=[BaseSpecifier("Base", "public", is_virtual=True)],
            is_abstract=True,
            is_cppclass=True,
            constructors=[ctor],
            destructor=dtor,
            conversions=[conv],
            namespace="juce",
        )
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_packed_with_notes(self) -> None:
        s = Struct(
            "Packed",
            [Field("a", CType("char"))],
            is_packed=True,
            notes=["alignment: 1"],
            inner_typedefs={"size_type": "unsigned int"},
        )
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_struct_typedef(self) -> None:
        s = Struct("S", [], is_typedef=True)
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_field_with_bitwidth(self) -> None:
        s = Struct("Bits", [Field("flags", CType("uint32_t"), bit_width=4)])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_field_with_anonymous_struct(self) -> None:
        inner = Struct(None, [Field("x", CType("int"))], is_union=False)
        s = Struct("Outer", [Field("pos", CType("void"), anonymous_struct=inner)])
        h = Header("t.h", [s])
        assert _round_trip(h) == h

    def test_enum_simple(self) -> None:
        e = Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_enum_anonymous(self) -> None:
        e = Enum(None, [EnumValue("A", None)])
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_enum_typedef(self) -> None:
        e = Enum("E", [], is_typedef=True)
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_enum_expression_value(self) -> None:
        e = Enum("Flags", [EnumValue("MASK", "FLAG_A | FLAG_B")])
        h = Header("t.h", [e])
        assert _round_trip(h) == h

    def test_function_simple(self) -> None:
        f = Function("add", CType("int"), [Parameter("a", CType("int"))])
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_function_variadic(self) -> None:
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
            calling_convention="cdecl",
            namespace="std",
        )
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_function_template(self) -> None:
        f = Function(
            "swap",
            CType("void"),
            [Parameter("a", Pointer(CType("T"))), Parameter("b", Pointer(CType("T")))],
            template_params=["T"],
            namespace="std",
        )
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_function_reference_and_default_params(self) -> None:
        f = Function(
            "process",
            CType("void"),
            [
                Parameter("in_ref", Reference(CType("int", ["const"]), is_rvalue=False)),
                Parameter("rval_ref", Reference(CType("int"), is_rvalue=True)),
                Parameter("flags", CType("int"), default_value="0"),
            ],
            is_noexcept=True,
        )
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_typedef(self) -> None:
        td = Typedef("size_t", CType("long", ["unsigned"]))
        h = Header("t.h", [td])
        assert _round_trip(h) == h

    def test_variable(self) -> None:
        v = Variable("count", CType("int"))
        h = Header("t.h", [v])
        assert _round_trip(h) == h

    def test_constant_macro(self) -> None:
        c = Constant("SIZE", 100, is_macro=True)
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_typed(self) -> None:
        c = Constant("MAX", 255, type=CType("int"))
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_string_value(self) -> None:
        c = Constant("VER", '"1.0"', is_macro=True)
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_float_value(self) -> None:
        c = Constant("PI", 3.14, type=CType("double"))
        h = Header("t.h", [c])
        assert _round_trip(h) == h

    def test_constant_no_value(self) -> None:
        c = Constant("UNKNOWN", None)
        h = Header("t.h", [c])
        assert _round_trip(h) == h


class TestRoundTripSourceLocation:
    """Round-trip tests for SourceLocation on declarations."""

    def test_location_with_column(self) -> None:
        f = Function("foo", CType("void"), location=SourceLocation("x.h", 10, 5))
        h = Header("t.h", [f])
        assert _round_trip(h) == h

    def test_location_without_column(self) -> None:
        f = Function("foo", CType("void"), location=SourceLocation("x.h", 10))
        h = Header("t.h", [f])
        assert _round_trip(h) == h


class TestRoundTripHeader:
    """Round-trip tests for Header-level fields."""

    def test_included_headers(self) -> None:
        h = Header("t.h", [], included_headers={"stdio.h", "stdlib.h"})
        assert _round_trip(h) == h

    def test_empty_header(self) -> None:
        h = Header("t.h", [])
        assert _round_trip(h) == h

    def test_json_string_round_trip(self) -> None:
        h = Header(
            "t.h",
            [Function("f", CType("void")), Variable("v", CType("int"))],
        )
        assert _round_trip_json_str(h) == h

    def test_comprehensive(self) -> None:
        """Round-trip a Header with every declaration type."""
        h = Header(
            path="all.h",
            declarations=[
                Struct(
                    "Point",
                    [Field("x", CType("int")), Field("y", CType("int"))],
                    location=SourceLocation("all.h", 1, 1),
                ),
                Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)]),
                Function(
                    "add",
                    CType("int"),
                    [Parameter("a", CType("int")), Parameter("b", CType("int"))],
                ),
                Typedef("size_t", CType("long", ["unsigned"])),
                Variable("count", CType("int")),
                Constant("MAX", 1024, is_macro=True),
            ],
            included_headers={"stdio.h"},
        )
        assert _round_trip(h) == h


class TestErrorHandling:
    """Tests for deserialization error cases."""

    def test_invalid_type(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        with pytest.raises(ValueError, match="Expected dict"):
            json_to_header([1, 2, 3])  # type: ignore[arg-type]

    def test_missing_kind_in_type(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {"path": "t.h", "declarations": [{"kind": "variable", "name": "x", "type": {}}]}
        with pytest.raises(ValueError, match="missing 'kind'"):
            json_to_header(d)

    def test_unknown_type_kind(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {
            "path": "t.h",
            "declarations": [{"kind": "variable", "name": "x", "type": {"kind": "bogus"}}],
        }
        with pytest.raises(ValueError, match="Unknown type kind"):
            json_to_header(d)

    def test_unknown_decl_kind(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {"path": "t.h", "declarations": [{"kind": "bogus"}]}
        with pytest.raises(ValueError, match="Unknown declaration kind"):
            json_to_header(d)

    def test_missing_kind_in_decl(self) -> None:
        import pytest

        from headerkit._ir_json import json_to_header

        d = {"path": "t.h", "declarations": [{"name": "x"}]}
        with pytest.raises(ValueError, match="missing 'kind'"):
            json_to_header(d)


class TestTriStateFieldsSurviveTheRoundTrip:
    """The three fields the ctypes writer refuses on must survive JSON.

    Each is tri-state, and the third state is "no parser recorded this". A
    round trip that drops the field silently substitutes ``None`` for a real
    observation, and a consumer reading the restored IR then refuses a type it
    could have resolved -- or, for ``underlying_type_known``, stops refusing one
    it should.

    This was found by a C++ test that compares whole declaration lists and
    happened to contain a typedef of a tag. Nothing asserted the fields
    themselves, so a round trip that dropped every one of them would have gone
    unnoticed the moment that unrelated fixture changed.
    """

    def test_is_elaborated_survives_in_all_three_states(self):
        header = Header(
            path="t.h",
            declarations=[
                Typedef(name="Elab", underlying_type=CType("Gauge", is_elaborated=True)),
                Typedef(name="Bare", underlying_type=CType("Gauge", is_elaborated=False)),
                Typedef(name="Unset", underlying_type=CType("Gauge")),
            ],
        )
        restored = _round_trip_json_str(header)
        got = [d.underlying_type.is_elaborated for d in restored.declarations]
        assert got == [True, False, None]

    def test_an_enums_declared_width_survives(self):
        header = Header(
            path="t.h",
            declarations=[Enum(name="W", values=[EnumValue("A", 0)], underlying_type="unsigned long long")],
        )
        restored = _round_trip_json_str(header)
        assert restored.declarations[0].underlying_type == "unsigned long long"

    def test_a_width_the_parser_could_not_see_survives_as_unknown(self):
        """``False`` here is the whole point: restoring the default would resolve it."""
        header = Header(
            path="t.h",
            declarations=[Enum(name="W", values=[EnumValue("A", 0)], underlying_type_known=False)],
        )
        restored = _round_trip_json_str(header)
        assert restored.declarations[0].underlying_type_known is False


class TestExhaustiveFieldRoundTrip:
    """Every non-default field across all IR dataclasses must survive round-trip serialization."""

    def test_all_fields_populated_round_trip(self) -> None:
        loc = SourceLocation(file="comprehensive.hpp", line=42, column=7)

        inner_struct = Struct(
            name="Inner",
            fields=[
                Field(
                    name="inner_f",
                    type=CType("float", qualifiers=["const"], is_elaborated=False),
                    bit_width=2,
                    anonymous_struct=None,
                    is_anonymous_transparent=False,
                    access="public",
                    is_static=False,
                    is_padding=False,
                )
            ],
            methods=[],
            is_union=False,
            is_cppclass=True,
            is_typedef=False,
            is_packed=False,
            namespace="outer_ns",
            template_params=[],
            cpp_name="outer_ns::Inner",
            notes=[],
            inner_typedefs={},
            nested_records=[],
            bases=[],
            is_abstract=False,
            constructors=[],
            destructor=None,
            conversions=[],
            vtable_entries=[],
            attributes=[],
            is_deprecated=False,
            alignment=None,
            location=loc,
        )

        test_struct = Struct(
            name="Widget",
            fields=[
                Field(
                    name="flags",
                    type=CType("unsigned int", qualifiers=["volatile"], is_elaborated=True),
                    bit_width=4,
                    anonymous_struct=None,
                    is_anonymous_transparent=True,
                    access="protected",
                    is_static=True,
                    is_padding=False,
                ),
                Field(
                    name="",
                    type=CType("int"),
                    bit_width=3,
                    anonymous_struct=None,
                    is_anonymous_transparent=False,
                    access=None,
                    is_static=False,
                    is_padding=True,
                ),
                Field(
                    name="nested_obj",
                    type=CType("Inner"),
                    bit_width=None,
                    anonymous_struct=inner_struct,
                    is_anonymous_transparent=False,
                    access="private",
                    is_static=False,
                    is_padding=False,
                ),
            ],
            methods=[
                Function(
                    name="get_id",
                    return_type=CType("int"),
                    parameters=[Parameter(name="opt", type=CType("int"), default_value="0")],
                    is_variadic=False,
                    calling_convention=None,
                    namespace="outer_ns",
                    template_params=[],
                    is_static=False,
                    is_const=True,
                    is_virtual=True,
                    is_pure_virtual=False,
                    is_explicit=False,
                    access="public",
                    is_deleted=False,
                    is_defaulted=False,
                    is_noexcept=True,
                    is_inline=True,
                    body="{ return 1; }",
                    attributes=["nodiscard"],
                    is_deprecated=False,
                    location=loc,
                )
            ],
            is_union=False,
            is_cppclass=True,
            is_typedef=True,
            is_packed=True,
            namespace="outer_ns",
            template_params=["T", "U"],
            cpp_name="outer_ns::Widget",
            notes=["generated binding"],
            inner_typedefs={"Index": "unsigned long"},
            nested_records=[inner_struct],
            bases=[BaseSpecifier(name="BaseWidget", access="public", is_virtual=True)],
            is_abstract=True,
            constructors=[
                Function(
                    name="Widget",
                    return_type=CType("void"),
                    parameters=[Parameter(name="val", type=CType("int"), default_value="10")],
                    is_variadic=False,
                    calling_convention=None,
                    namespace="outer_ns",
                    template_params=[],
                    is_static=False,
                    is_const=False,
                    is_virtual=False,
                    is_pure_virtual=False,
                    is_explicit=True,
                    access="public",
                    is_deleted=False,
                    is_defaulted=False,
                    is_noexcept=False,
                    is_inline=False,
                    body=None,
                    attributes=[],
                    is_deprecated=False,
                    location=loc,
                )
            ],
            destructor=Function(
                name="~Widget",
                return_type=CType("void"),
                parameters=[],
                is_variadic=False,
                calling_convention=None,
                namespace="outer_ns",
                template_params=[],
                is_static=False,
                is_const=False,
                is_virtual=True,
                is_pure_virtual=False,
                is_explicit=False,
                access="public",
                is_deleted=False,
                is_defaulted=True,
                is_noexcept=True,
                is_inline=False,
                body=None,
                attributes=[],
                is_deprecated=False,
                location=loc,
            ),
            conversions=[
                Function(
                    name="operator bool",
                    return_type=CType("bool"),
                    parameters=[],
                    is_variadic=False,
                    calling_convention=None,
                    namespace="outer_ns",
                    template_params=[],
                    is_static=False,
                    is_const=True,
                    is_virtual=False,
                    is_pure_virtual=False,
                    is_explicit=True,
                    access="public",
                    is_deleted=False,
                    is_defaulted=False,
                    is_noexcept=True,
                    is_inline=False,
                    body=None,
                    attributes=[],
                    is_deprecated=False,
                    location=loc,
                )
            ],
            vtable_entries=[
                Function(
                    name="render",
                    return_type=CType("void"),
                    parameters=[],
                    is_variadic=False,
                    calling_convention=None,
                    namespace="outer_ns",
                    template_params=[],
                    is_static=False,
                    is_const=False,
                    is_virtual=True,
                    is_pure_virtual=True,
                    is_explicit=False,
                    access="public",
                    is_deleted=False,
                    is_defaulted=False,
                    is_noexcept=False,
                    is_inline=False,
                    body=None,
                    attributes=[],
                    is_deprecated=False,
                    location=loc,
                )
            ],
            attributes=["aligned(16)"],
            is_deprecated=True,
            alignment=16,
            location=loc,
        )

        test_enum = Enum(
            name="Color",
            values=[
                EnumValue(name="Red", value=1),
                EnumValue(name="Green", value="1 << 1"),
                EnumValue(name="Blue", value=None),
            ],
            is_typedef=True,
            namespace="outer_ns",
            location=loc,
            is_scoped=True,
            cpp_name="outer_ns::Color",
            underlying_type="uint8_t",
            underlying_type_known=False,
        )

        test_fn = Function(
            name="process",
            return_type=Reference(
                target=Pointer(
                    pointee=CType("char", qualifiers=["const"], is_elaborated=False),
                    qualifiers=["const"],
                ),
                is_rvalue=True,
                qualifiers=["const"],
            ),
            parameters=[
                Parameter(
                    name="buffer",
                    type=Array(element_type=CType("char"), size=256),
                    default_value=None,
                ),
                Parameter(
                    name="callback",
                    type=FunctionPointer(
                        return_type=CType("int"),
                        parameters=[Parameter(name="code", type=CType("int"), default_value=None)],
                        is_variadic=True,
                        calling_convention="cdecl",
                    ),
                    default_value="nullptr",
                ),
            ],
            is_variadic=True,
            calling_convention="fastcall",
            namespace="outer_ns",
            template_params=["V"],
            is_static=True,
            is_const=False,
            is_virtual=False,
            is_pure_virtual=False,
            is_explicit=False,
            access="public",
            is_deleted=True,
            is_defaulted=False,
            is_noexcept=True,
            is_inline=True,
            body="{ return nullptr; }",
            attributes=["deprecated", "hot"],
            is_deprecated=True,
            location=loc,
        )

        test_typedef = Typedef(
            name="Callback",
            underlying_type=Pointer(
                FunctionPointer(
                    return_type=CType("void"),
                    parameters=[Parameter(name="arg", type=CType("void*"))],
                    is_variadic=False,
                    calling_convention=None,
                )
            ),
            namespace="outer_ns",
            attributes=["may_alias"],
            is_deprecated=True,
            location=loc,
        )

        test_var = Variable(
            name="global_counter",
            type=CType("long long", qualifiers=["volatile"]),
            namespace="outer_ns",
            attributes=["thread_local"],
            is_deprecated=True,
            alignment=8,
            location=loc,
        )

        test_const = Constant(
            name="BUFFER_LIMIT",
            value=4096,
            evaluated_value=4096,
            raw_expression="1 << 12",
            type=CType("int"),
            is_macro=True,
            location=loc,
        )

        header = Header(
            path="comprehensive.hpp",
            declarations=[
                test_struct,
                test_enum,
                test_fn,
                test_typedef,
                test_var,
                test_const,
            ],
            included_headers={"header1.h", "header2.h"},
            language="cpp",
            classification="header",
        )

        restored_from_dict = _round_trip(header)
        assert asdict(restored_from_dict) == asdict(header)

        restored_from_str = _round_trip_json_str(header)
        assert asdict(restored_from_str) == asdict(header)
