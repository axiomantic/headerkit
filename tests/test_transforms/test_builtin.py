"""Tests for built-in IR filters and deep type cross-reference rewriting."""

from __future__ import annotations

from pathlib import Path

import pytest

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Parameter,
    Pointer,
    Reference,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
    strip_padding_fields,
)
from headerkit.transforms.builtin import (
    FilterByHeader,
    FilterBySymbol,
    FilterDeprecated,
    FilterUnexportedSymbols,
    FilterUnexportedSymbolsTransform,
    MakeOpaque,
    StripPaddingFields,
    StripPrefix,
    _PathSet,
    normalize_binary_symbol,
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


def test_filter_by_header_pathset_allow_deny_symlinks_and_none_locations(tmp_path: Path) -> None:
    """Verify _PathSet and FilterByHeader handle allowlist, denylist, deny>allow, symlinks, and None locations."""
    real_inc = tmp_path / "real_include"
    pub_dir = real_inc / "nng"
    priv_dir = pub_dir / "supplemental"
    priv_dir.mkdir(parents=True)

    symlink_inc = tmp_path / "symlink_include"
    symlink_inc.symlink_to(real_inc)

    pub_hdr = symlink_inc / "nng" / "nng.h"
    priv_hdr = symlink_inc / "nng" / "supplemental" / "internal.h"
    sys_hdr = tmp_path / "usr" / "include" / "stdint.h"

    d_pub = Function(
        name="nng_open",
        return_type=CType("int"),
        location=SourceLocation(file=str(pub_hdr), line=10, column=1),
    )
    d_priv = Function(
        name="nng_internal_init",
        return_type=CType("int"),
        location=SourceLocation(file=str(priv_hdr), line=20, column=1),
    )
    d_sys = Function(
        name="uint32_helper",
        return_type=CType("int"),
        location=SourceLocation(file=str(sys_hdr), line=5, column=1),
    )
    d_builtin = Typedef(
        name="__builtin_va_list",
        underlying_type=Pointer(CType("char")),
        location=None,
    )

    unit = SourceUnit(
        path=str(real_inc / "umbrella.h"),
        declarations=[d_pub, d_priv, d_sys, d_builtin],
    )

    # Allow nng/* via real_inc search_dirs, deny supplemental/*; deny wins over allow, and location=None is dropped
    f_allow_deny = FilterByHeader(
        allow=["nng/*"],
        deny=["nng/supplemental/*"],
        include_dirs=[str(real_inc)],
    )
    filtered = unit >> f_allow_deny
    assert filtered == SourceUnit(
        path=str(real_inc / "umbrella.h"),
        declarations=[d_pub],
    )

    # Negative control: when only denylist is active (allowlist empty), location=None is preserved
    f_deny_only = FilterByHeader(
        deny=["stdint.h", "nng/supplemental/*"],
        include_dirs=[str(real_inc)],
    )
    filtered_deny_only = unit >> f_deny_only
    assert filtered_deny_only == SourceUnit(
        path=str(real_inc / "umbrella.h"),
        declarations=[d_pub, d_builtin],
    )

    # Verify _PathSet.cache_fingerprint() is deterministic
    ps = _PathSet(entries=("nng/*",), search_dirs=(str(real_inc),))
    assert ps.cache_fingerprint() == {
        "entries": ["nng/*"],
        "search_dirs": [str(real_inc)],
    }


def test_filter_by_symbol_and_cascade_excluded_types() -> None:
    """Verify FilterBySymbol cascades excluded types to functions, fields, typedefs, and variables."""
    s_priv = Struct(name="nng_id_map", fields=[Field("count", CType("int"))])
    s_pub = Struct(
        name="nng_ctx",
        fields=[
            Field("id", CType("int")),
            Field("priv_map", Pointer(CType("nng_id_map"))),
        ],
    )
    td_priv = Typedef(name="nng_id_map_ptr", underlying_type=Pointer(CType("nng_id_map")))
    fn_priv_arg = Function(
        name="nng_id_get",
        return_type=Pointer(CType("void")),
        parameters=[Parameter("m", Pointer(CType("nng_id_map")))],
    )
    fn_pub = Function(
        name="nng_close",
        return_type=CType("int"),
        parameters=[Parameter("ctx", CType("nng_ctx"))],
    )
    var_priv = Variable(name="g_map", type=Pointer(CType("nng_id_map")))
    var_pub = Variable(name="g_version", type=CType("int"))

    unit = SourceUnit(
        path="nng.h",
        declarations=[s_priv, s_pub, td_priv, fn_priv_arg, fn_pub, var_priv, var_pub],
    )

    # With cascade_excluded_types=True (default): excluding nng_id_map also prunes
    # the priv_map field on nng_ctx, td_priv, fn_priv_arg, and var_priv.
    res_cascaded = unit >> FilterBySymbol(exclude_patterns=["nng_id_map"], cascade_excluded_types=True)
    assert res_cascaded == SourceUnit(
        path="nng.h",
        declarations=[
            Struct(name="nng_ctx", fields=[Field("id", CType("int"))]),
            fn_pub,
            var_pub,
        ],
    )

    # Negative control: with cascade_excluded_types=False, only nng_id_map itself is removed
    res_no_cascade = unit >> FilterBySymbol(exclude_patterns=["nng_id_map"], cascade_excluded_types=False)
    assert res_no_cascade == SourceUnit(
        path="nng.h",
        declarations=[s_pub, td_priv, fn_priv_arg, fn_pub, var_priv, var_pub],
    )


def test_filter_deprecated() -> None:
    """Verify FilterDeprecated removes deprecated declarations and methods."""
    fn_ok = Function(name="active_api", return_type=CType("int"), is_deprecated=False)
    fn_dep = Function(
        name="legacy_api",
        return_type=CType("int"),
        is_deprecated=True,
    )
    st = Struct(
        name="Config",
        fields=[
            Field("flags", CType("int")),
        ],
        methods=[
            Function(name="run", return_type=CType("void"), is_deprecated=False),
            Function(name="run_old", return_type=CType("void"), is_deprecated=True),
        ],
    )
    st_dep = Struct(name="OldStruct", fields=[Field("x", CType("int"))], is_deprecated=True)
    td_dep = Typedef(name="OldAlias", underlying_type=CType("int"), is_deprecated=True)
    var_dep = Variable(name="g_old", type=CType("int"), is_deprecated=True)

    unit = SourceUnit(path="api.h", declarations=[fn_ok, fn_dep, st, st_dep, td_dep, var_dep])
    filtered = unit >> FilterDeprecated()
    assert filtered == SourceUnit(
        path="api.h",
        declarations=[
            fn_ok,
            Struct(
                name="Config",
                fields=[Field("flags", CType("int"))],
                methods=[Function(name="run", return_type=CType("void"), is_deprecated=False)],
            ),
        ],
    )


def test_filter_unexported_symbols_and_binary_symbol_normalization() -> None:
    """Verify FilterUnexportedSymbolsTransform normalizes Mach-O/COFF/ELF symbols and preserves static inline."""
    assert normalize_binary_symbol("_nng_open") == {"_nng_open", "nng_open"}
    assert normalize_binary_symbol("_stdcall_fn@12") == {"_stdcall_fn", "stdcall_fn"}
    assert normalize_binary_symbol("@fastcall_fn@8") == {"fastcall_fn"}
    assert normalize_binary_symbol("__imp__win_api@4") == {"_win_api", "win_api"}
    assert normalize_binary_symbol("memcpy@@GLIBC_2.14") == {"memcpy"}
    assert normalize_binary_symbol(" sched_yield@GLIBC_2.2.5 ") == {"sched_yield"}

    fn_exported = Function(name="nng_open", return_type=CType("int"))
    fn_unexported = Function(name="nng_internal_dial", return_type=CType("int"))
    fn_static_inline = Function(
        name="nng_msg_len_inline",
        return_type=CType("int"),
        is_static=True,
        is_inline=True,
    )
    var_exported = Variable(name="nng_version_num", type=CType("int"))
    var_unexported = Variable(name="nng_secret_state", type=CType("int"))
    st_type = Struct(name="nng_socket", fields=[Field("id", CType("int"))])
    en_type = Enum(name="nng_flag", values=[EnumValue("NNG_FLAG_ALLOC", 1)])
    const_val = Constant(name="NNG_MAJOR_VERSION", value=1)

    unit = SourceUnit(
        path="nng.h",
        declarations=[
            fn_exported,
            fn_unexported,
            fn_static_inline,
            var_exported,
            var_unexported,
            st_type,
            en_type,
            const_val,
        ],
    )

    filt = FilterUnexportedSymbols(["_nng_open", "__imp_nng_version_num@0"])
    assert isinstance(filt, FilterUnexportedSymbolsTransform)
    res = unit >> filt
    assert res == SourceUnit(
        path="nng.h",
        declarations=[
            fn_exported,
            fn_static_inline,
            var_exported,
            st_type,
            en_type,
            const_val,
        ],
    )

