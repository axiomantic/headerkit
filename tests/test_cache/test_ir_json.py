"""Tests for JSON IR deserialization (round-trip invariant)."""

from __future__ import annotations

import dataclasses

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


def _default_of(f: dataclasses.Field[object]) -> object:
    """The dataclass default for ``f``, or a sentinel for a required field."""
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return object()


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


class TestEveryIrFieldSurvivesTheRoundTrip:
    """The invariant this module's docstring claims, asserted rather than stated.

    ``json_to_header(header_to_json_dict(h)) == h`` was prose, and prose fails
    silently. Executed on a real parse of either backend it was False: the
    serializer never wrote ``Field.is_padding``, ``Struct.nested_records``,
    ``Enum.is_scoped``, ``Enum.namespace`` or ``Enum.cpp_name``, so a cache hit
    returned a different IR than the parse that filled it.
    """

    @staticmethod
    def _maximal_header() -> Header:
        """A header exercising every field this serializer is meant to carry.

        Values are chosen to differ from the dataclass default, because a field
        left at its default round-trips whether or not it is written -- which is
        exactly how five of them stayed broken.
        """
        loc = SourceLocation(file="h.h", line=3, column=9)
        inner = Struct("Inner", [Field("q", CType("int"))])
        method = Function("m", CType("void"), [Parameter("p", CType("int"))], location=loc)
        return Header(
            "h.h",
            [
                Struct(
                    "S",
                    methods=[method],
                    constructors=[method],
                    destructor=method,
                    conversions=[method],
                    vtable_entries=[method],
                    attributes=["deprecated"],
                    is_abstract=True,
                    is_deprecated=True,
                    fields=[
                        Field("a", CType("const unsigned int", qualifiers=["const"], is_elaborated=True), bit_width=4),
                        Field("", CType("unsigned int"), bit_width=3, is_padding=True),
                        Field("st", CType("int"), is_static=True, access="private"),
                        Field("an", CType("int"), anonymous_struct=inner, is_anonymous_transparent=True),
                    ],
                    is_typedef=True,
                    is_cppclass=True,
                    namespace="ns",
                    template_params=["T"],
                    cpp_name="ns::S",
                    is_packed=True,
                    notes=["n1"],
                    inner_typedefs={"A": "int"},
                    nested_records=[inner],
                    bases=[BaseSpecifier("B", access="protected", is_virtual=True)],
                    alignment=16,
                    location=loc,
                ),
                Enum(
                    "E",
                    [EnumValue("X", 1)],
                    is_scoped=True,
                    underlying_type="int",
                    underlying_type_known=False,
                    is_typedef=True,
                    namespace="ns",
                    cpp_name="ns::E",
                    location=loc,
                ),
            ],
        )

    def test_a_maximal_header_round_trips_unchanged(self) -> None:
        header = self._maximal_header()
        assert _round_trip(header) == header

    def test_every_dataclass_field_is_written_or_declared_unwritten(self) -> None:
        """Closes the class, not the five instances.

        A field the serializer never writes is invisible to the round trip
        whenever the fixture happens to leave it at its default, and invisible
        to the IR fingerprint too -- that hashes the dataclasses, which know
        nothing about what gets serialized. This walks the objects beside the
        dicts they produced and fails on any field absent from both the output
        and the allowlist below, so a newly added field has to be handled
        rather than silently dropped.
        """
        #: Fields deliberately not serialized, each with the reason it is safe.
        unwritten: dict[str, set[str]] = {
            # Reconstructed from the "kind" discriminator on the way back in.
            "Struct": {"is_union"},
        }
        header = self._maximal_header()
        data = header_to_json_dict(header)

        seen: dict[str, tuple[set[str], object]] = {}

        def walk(obj: object, d: object) -> None:
            if not dataclasses.is_dataclass(obj) or not isinstance(d, dict):
                return
            name = type(obj).__name__
            keys, _ = seen.setdefault(name, (set(), obj))
            keys.update(d)
            seen[name] = (keys, obj)
            for f in dataclasses.fields(obj):
                value = getattr(obj, f.name)
                rendered = d.get(f.name)
                if isinstance(value, list) and isinstance(rendered, list):
                    for item, sub in zip(value, rendered, strict=False):
                        walk(item, sub)
                else:
                    walk(value, rendered)

        for decl, rendered in zip(header.declarations, data["declarations"], strict=False):
            walk(decl, rendered)

        # Only a field carrying a *non-default* value is required to appear:
        # the serializer omits defaults by design, so demanding a key for one
        # would assert the format rather than the invariant. Which is why the
        # fixture above sets every field away from its default.
        missing: dict[str, list[str]] = {}
        for name, (keys, obj) in seen.items():
            gap = sorted(
                f.name
                for f in dataclasses.fields(type(obj))
                if f.name not in keys
                and f.name not in unwritten.get(name, set())
                and getattr(obj, f.name) != _default_of(f)
            )
            if gap:
                missing[name] = gap
        assert not missing, (
            f"these IR fields are never written by the serializer: {missing}. A cache hit will "
            "return the dataclass default for each of them instead of what the parser reported."
        )
