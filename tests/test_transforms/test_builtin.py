"""Tests for built-in IR filters and deep type cross-reference rewriting."""

from __future__ import annotations

import pytest

from headerkit.ir import (
    Array,
    CType,
    Field,
    Function,
    FunctionPointer,
    Parameter,
    Pointer,
    Reference,
    SourceLocation,
    SourceUnit,
    Struct,
    Variable,
    strip_padding_fields,
)
from headerkit.transforms.builtin import (
    FilterByHeader,
    MakeOpaque,
    StripPaddingFields,
    StripPrefix,
    rewrite_type_references,
)


def test_rewrite_type_references_deep() -> None:
    mapping = {"ma_device": "Device", "ma_context": "Context"}

    # CType
    t1 = CType("ma_device")
    assert rewrite_type_references(t1, mapping) == CType("Device")

    # Pointer -> pointee
    t2 = Pointer(pointee=CType("ma_device"))
    res_p = rewrite_type_references(t2, mapping)
    assert isinstance(res_p, Pointer)
    assert res_p.pointee == CType("Device")

    # Reference -> target
    t3 = Reference(target=CType("ma_context"))
    res_r = rewrite_type_references(t3, mapping)
    assert isinstance(res_r, Reference)
    assert res_r.target == CType("Context")

    # Array -> element_type
    t4 = Array(element_type=CType("ma_device"), size=4)
    res_a = rewrite_type_references(t4, mapping)
    assert isinstance(res_a, Array)
    assert res_a.element_type == CType("Device")

    # FunctionPointer -> return_type & parameters
    t5 = FunctionPointer(
        return_type=CType("ma_device"),
        parameters=[Parameter(name="ctx", type=Pointer(pointee=CType("ma_context")))],
    )
    res_fp = rewrite_type_references(t5, mapping)
    assert isinstance(res_fp, FunctionPointer)
    assert res_fp.return_type == CType("Device")
    assert isinstance(res_fp.parameters[0].type, Pointer)
    assert res_fp.parameters[0].type.pointee == CType("Context")


def test_strip_prefix_updates_cross_references() -> None:
    st = Struct(
        name="ma_device",
        fields=[Field(name="id", type=CType("int"))],
    )
    fn = Function(
        name="ma_device_init",
        return_type=CType("int"),
        parameters=[
            Parameter(name="dev", type=Pointer(pointee=CType("ma_device"))),
        ],
    )
    anon_st = Struct(name=None, fields=[Field(name="x", type=CType("int"))])
    var = Variable(name="ma_log_level", type=CType("int"))

    unit = SourceUnit(path="miniaudio.h", declarations=[st, anon_st, fn, var])
    stripped = unit >> StripPrefix("ma_")

    assert isinstance(stripped, SourceUnit)
    assert stripped.declarations[0].name == "device"
    assert stripped.declarations[1].name is None
    fn_stripped = stripped.declarations[2]
    assert isinstance(fn_stripped, Function)
    assert fn_stripped.name == "device_init"
    param_type = fn_stripped.parameters[0].type
    assert isinstance(param_type, Pointer)
    assert param_type.pointee == CType("device")
    var_stripped = stripped.declarations[3]
    assert isinstance(var_stripped, Variable)
    assert var_stripped.name == "log_level"


def test_make_opaque() -> None:
    st = Struct(
        name="InternalState",
        fields=[Field(name="secret", type=CType("int"))],
    )
    unit = SourceUnit(path="test.h", declarations=[st])
    res = unit >> MakeOpaque("InternalState")

    assert len(res.declarations) == 1
    opaque_st = res.declarations[0]
    assert isinstance(opaque_st, Struct)
    assert opaque_st.name == "InternalState"
    assert opaque_st.fields == []


def test_filter_by_header() -> None:
    d1 = Function(name="f1", return_type=CType("void"), location=SourceLocation(file="sys/types.h", line=1, column=1))
    d2 = Function(name="f2", return_type=CType("void"), location=SourceLocation(file="app/main.h", line=1, column=1))
    unit = SourceUnit(path="all.h", declarations=[d1, d2])

    res = unit >> FilterByHeader(["*/app/*"], mode="include")
    assert len(res.declarations) == 1
    assert res.declarations[0].name == "f2"


def test_strip_padding_fields_filter_and_legacy_wrapper() -> None:
    f1 = Field(name="a", type=CType("int"), is_padding=False)
    f2 = Field(name="__pad", type=CType("char"), is_padding=True)
    st = Struct(name="Padded", fields=[f1, f2])
    unit = SourceUnit(path="pad.h", declarations=[st])

    res_filter = unit >> StripPaddingFields()
    st_clean = res_filter.declarations[0]
    assert isinstance(st_clean, Struct)
    assert len(st_clean.fields) == 1
    assert st_clean.fields[0].name == "a"

    with pytest.deprecated_call():
        res_legacy = strip_padding_fields(unit)
    st_legacy = res_legacy.declarations[0]
    assert isinstance(st_legacy, Struct)
    assert len(st_legacy.fields) == 1
    assert st_legacy.fields[0].name == "a"
