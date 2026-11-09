"""Tests for NimBackend, NimonyBackend, clean_nim_c_unit, SourceMeta, and ParsedDoc."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from headerkit import (
    NimBackend,
    NimonyBackend,
    ParamDoc,
    ParsedDoc,
    SourceMeta,
    clean_nim_c_unit,
    generate,
    get_backend,
    list_backends,
)
from headerkit.backends import is_backend_available
from headerkit.backends.nim import _find_nim_libpath, _has_nimbase_in_dirs
from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.ir import (
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    InputSpec,
    Parameter,
    Pointer,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers.pyi import PyiWriter


class TestTreeSitterNimHeaderSupport:
    """Verify TreeSitterBackend parses Nim nimbase.h macros cleanly without regex."""

    def test_nimbase_calling_conventions_and_export_macros(self) -> None:
        """Verify N_CDECL, N_NIMCALL, N_LIB_PRIVATE, N_STDCALL, N_FASTCALL, N_NOCONV, N_INLINE, N_NOINLINE, N_*_PTR, and NCSTRING."""
        code = textwrap.dedent("""\
            typedef struct MyItem MyItem;
            struct MyItem {
                int id;
                NCSTRING label;
            };
            typedef N_CDECL_PTR(int, CompareCb)(int a_p0, int b_p1);
            N_LIB_PRIVATE N_CDECL(int, addNumbers)(int a_p0, int b_p1);
            NIM_EXTERNC N_LIB_EXPORT N_NIMCALL(NCSTRING, formatName)(NCSTRING input_p0);
            N_LIB_IMPORT N_STDCALL(void, winHook)(int code_p0);
            N_LIB_PRIVATE N_FASTCALL(int*, fastLookup)(const char* key_p0);
            N_LIB_PRIVATE N_NOCONV(void, signalHandler)(int sign_p0);
            N_LIB_PRIVATE N_NOINLINE(void, slowPath)(void);
            static N_INLINE(int, inlineHelper)(int x_p0);
            N_LIB_EXPORT_VAR int globalCounter;
        """)
        backend = TreeSitterBackend()
        unit = backend.parse(code, "nim_mod.h", **NimBackend.c_backend_options())

        # Negative control: N_CDECL / N_NIMCALL / N_LIB_PRIVATE must NEVER appear as Variable or Function names
        decl_names = [getattr(d, "name", None) for d in unit.declarations]
        assert "N_CDECL" not in decl_names
        assert "N_NIMCALL" not in decl_names
        assert "N_LIB_PRIVATE" not in decl_names
        assert "N_LIB_EXPORT" not in decl_names

        loc = lambda line, col: SourceLocation(file="nim_mod.h", line=line, column=col)  # noqa: E731
        c_int = CType("int", is_elaborated=False)
        c_void = CType("void", is_elaborated=False)
        c_ncstring = CType("NCSTRING", is_elaborated=False)
        expected_declarations = [
            Struct(
                name="MyItem",
                fields=[
                    Field(name="id", type=c_int, access="public"),
                    Field(name="label", type=c_ncstring, access="public"),
                ],
                is_typedef=False,
                location=loc(2, 1),
            ),
            Typedef(
                name="MyItem",
                underlying_type=CType("MyItem", is_elaborated=True),
                location=loc(1, 23),
            ),
            Typedef(
                name="CompareCb",
                underlying_type=FunctionPointer(
                    return_type=c_int,
                    parameters=[
                        Parameter(name="a_p0", type=c_int),
                        Parameter(name="b_p1", type=c_int),
                    ],
                ),
                location=loc(6, 15),
            ),
            Function(
                name="addNumbers",
                return_type=c_int,
                parameters=[
                    Parameter(name="a_p0", type=c_int),
                    Parameter(name="b_p1", type=c_int),
                ],
                location=loc(7, 19),
            ),
            Function(
                name="formatName",
                return_type=c_ncstring,
                parameters=[
                    Parameter(name="input_p0", type=c_ncstring),
                ],
                location=loc(8, 35),
            ),
            Function(
                name="winHook",
                return_type=c_void,
                parameters=[
                    Parameter(name="code_p0", type=c_int),
                ],
                location=loc(9, 19),
            ),
            Function(
                name="fastLookup",
                return_type=Pointer(c_int),
                parameters=[
                    Parameter(name="key_p0", type=Pointer(CType("char", ["const"], is_elaborated=False))),
                ],
                location=loc(10, 20),
            ),
            Function(
                name="signalHandler",
                return_type=c_void,
                parameters=[
                    Parameter(name="sign_p0", type=c_int),
                ],
                location=loc(11, 20),
            ),
            Function(
                name="slowPath",
                return_type=c_void,
                parameters=[],
                location=loc(12, 20),
            ),
            Function(
                name="inlineHelper",
                return_type=c_int,
                parameters=[
                    Parameter(name="x_p0", type=c_int),
                ],
                location=loc(13, 19),
            ),
            Variable(
                name="globalCounter",
                type=c_int,
                location=loc(14, 22),
            ),
        ]
        assert unit.declarations == expected_declarations


class TestCleanNimCUnitAndNimBackend:
    """Verify NimBackend and clean_nim_c_unit strip _p0/`gensym suffixes, filter signalHandler, and populate SourceMeta."""

    def test_parameter_suffix_stripping_and_signal_handler_filtering(self) -> None:
        """Verify _p0, _p1, `gensym0, _gensym1 stripping, signalHandler removal, and SourceMeta attachment."""
        unit = SourceUnit(
            path="nim_out.h",
            declarations=[
                Typedef(name="NCSTRING", underlying_type=Pointer(CType("char"))),
                Typedef(name="NI32", underlying_type=CType("int32_t")),
                Typedef(name="Color", underlying_type=CType("NI32")),
                Struct(
                    name="TFrame_",
                    fields=[Field(name="len", type=CType("NI"))],
                ),
                Struct(
                    name="Vector",
                    fields=[
                        Field(name="data`gensym0", type=CType("NCSTRING")),
                        Field(name="length_colon_len_0", type=CType("NI")),
                        Field(name="elem_0", type=CType("_Bool")),
                    ],
                ),
                Enum(
                    name="NimMode",
                    values=[EnumValue("MODE_FAST", 0)],
                    underlying_type="NI32",
                ),
                Function(
                    name="nim_add",
                    return_type=CType("NI"),
                    parameters=[Parameter(name="x_p0", type=CType("NI"))],
                ),
                Function(
                    name="nimGC_unref",
                    return_type=CType("void"),
                    parameters=[],
                ),
                Function(
                    name="compute",
                    return_type=CType("NF"),
                    parameters=[
                        Parameter(name="alpha_p0", type=CType("NI32")),
                        Parameter(name="beta_p1", type=CType("NF32")),
                        Parameter(name="tag`gensym0", type=CType("NCSTRING")),
                        Parameter(name="flag_gensym12_p2", type=CType("NIM_BOOL")),
                        # Negative controls: parameters without Nim hygiene suffixes must stay untouched
                        Parameter(name="normal_param", type=CType("int")),
                        Parameter(name="p0", type=CType("int")),
                        Parameter(name="_p0", type=CType("int")),
                        Parameter(name="step_p", type=CType("int")),
                        Parameter(name="step_p0_p0", type=CType("int")),
                    ],
                ),
                Function(
                    name="signalHandler",
                    return_type=CType("void"),
                    parameters=[Parameter(name="sign_p0", type=CType("int"))],
                ),
                Function(
                    name="NimMain",
                    return_type=CType("void"),
                    parameters=[],
                ),
                Function(
                    name="callDepthLimitReached__system_u4494",
                    return_type=CType("void"),
                    parameters=[],
                ),
            ],
        )

        cleaned = clean_nim_c_unit(unit)
        expected = SourceUnit(
            path="nim_out.h",
            declarations=[
                Typedef(
                    name="Color",
                    underlying_type=CType("int32_t"),
                    source_meta=SourceMeta(source_language="nim", source_name="Color", source_type="NI32"),
                ),
                Struct(
                    name="Vector",
                    fields=[
                        Field(
                            name="data",
                            type=Pointer(CType("char", is_elaborated=False)),
                            source_meta=SourceMeta(source_language="nim", source_name="data", source_type="NCSTRING"),
                        ),
                        Field(
                            name="length",
                            type=CType("intptr_t"),
                            source_meta=SourceMeta(source_language="nim", source_name="length", source_type="NI"),
                        ),
                        Field(
                            name="elem_0",
                            type=CType("bool"),
                            source_meta=SourceMeta(source_language="nim", source_name="elem_0", source_type="_Bool"),
                        ),
                    ],
                    source_meta=SourceMeta(source_language="nim", source_name="Vector", source_type="Vector"),
                ),
                Enum(
                    name="NimMode",
                    values=[EnumValue("MODE_FAST", 0)],
                    underlying_type="int32_t",
                    source_meta=SourceMeta(source_language="nim", source_name="NimMode", source_type="NimMode"),
                ),
                Function(
                    name="nim_add",
                    return_type=CType("intptr_t"),
                    parameters=[
                        Parameter(
                            name="x",
                            type=CType("intptr_t"),
                            source_meta=SourceMeta(source_language="nim", source_name="x", source_type="NI"),
                        )
                    ],
                    source_meta=SourceMeta(source_language="nim", source_name="nim_add", source_type="NI"),
                ),
                Function(
                    name="compute",
                    return_type=CType("double"),
                    parameters=[
                        Parameter(
                            name="alpha",
                            type=CType("int32_t"),
                            source_meta=SourceMeta(source_language="nim", source_name="alpha", source_type="NI32"),
                        ),
                        Parameter(
                            name="beta",
                            type=CType("float"),
                            source_meta=SourceMeta(source_language="nim", source_name="beta", source_type="NF32"),
                        ),
                        Parameter(
                            name="tag",
                            type=Pointer(CType("char", is_elaborated=False)),
                            source_meta=SourceMeta(source_language="nim", source_name="tag", source_type="NCSTRING"),
                        ),
                        Parameter(
                            name="flag",
                            type=CType("bool"),
                            type_overrides={"pyi": "bool"},
                            source_meta=SourceMeta(source_language="nim", source_name="flag", source_type="NIM_BOOL"),
                        ),
                        Parameter(
                            name="normal_param",
                            type=CType("int"),
                            source_meta=SourceMeta(
                                source_language="nim", source_name="normal_param", source_type="int"
                            ),
                        ),
                        Parameter(
                            name="p0",
                            type=CType("int"),
                            source_meta=SourceMeta(source_language="nim", source_name="p0", source_type="int"),
                        ),
                        Parameter(
                            name="_p0",
                            type=CType("int"),
                            source_meta=SourceMeta(source_language="nim", source_name="_p0", source_type="int"),
                        ),
                        Parameter(
                            name="step_p",
                            type=CType("int"),
                            source_meta=SourceMeta(source_language="nim", source_name="step_p", source_type="int"),
                        ),
                        Parameter(
                            name="step_p0",
                            type=CType("int"),
                            source_meta=SourceMeta(source_language="nim", source_name="step_p0", source_type="int"),
                        ),
                    ],
                    source_meta=SourceMeta(source_language="nim", source_name="compute", source_type="NF"),
                ),
            ],
        )
        assert cleaned == expected

    def test_unmangles_tyobject_tyenum_and_inlines_typroc(self) -> None:
        """Verify clean_nim_c_unit unmangles tyObject_<Name>__<hash>, tyEnum_<Name>__<hash>, and inlines tyProc__<hash>."""
        fp_sig = FunctionPointer(
            return_type=CType("NI"),
            parameters=[Parameter(name="code_p0", type=CType("NI32"))],
        )
        unit = SourceUnit(
            path="nim_mangled.h",
            declarations=[
                Typedef(name="tyProc__a1b2c3d4", underlying_type=fp_sig),
                Struct(
                    name="tyObject_Packet__9f8e7d",
                    fields=[
                        Field(name="kind", type=CType("tyEnum_PacketKind__112233")),
                        Field(name="on_done", type=CType("tyProc__a1b2c3d4")),
                    ],
                ),
                Enum(
                    name="tyEnum_PacketKind__112233",
                    values=[EnumValue("PK_DATA", 0), EnumValue("PK_ACK", 1)],
                    underlying_type="NI32",
                ),
                Function(
                    name="sendPacket",
                    return_type=CType("NIM_BOOL"),
                    parameters=[
                        Parameter(name="pkt_p0", type=Pointer(CType("tyObject_Packet__9f8e7d"))),
                        Parameter(name="cb_p1", type=CType("tyProc__a1b2c3d4")),
                    ],
                ),
            ],
        )

        cleaned = clean_nim_c_unit(unit)
        expected_fp = FunctionPointer(
            return_type=CType("intptr_t"),
            parameters=[Parameter(name="code", type=CType("int32_t"))],
        )
        assert cleaned == SourceUnit(
            path="nim_mangled.h",
            declarations=[
                Struct(
                    name="Packet",
                    fields=[
                        Field(
                            name="kind",
                            type=CType("PacketKind"),
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name="kind",
                                source_type="tyEnum_PacketKind__112233",
                            ),
                        ),
                        Field(
                            name="on_done",
                            type=expected_fp,
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name="on_done",
                                source_type="tyProc__a1b2c3d4",
                            ),
                        ),
                    ],
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name="Packet",
                        source_type="tyObject_Packet__9f8e7d",
                    ),
                ),
                Enum(
                    name="PacketKind",
                    values=[EnumValue("PK_DATA", 0), EnumValue("PK_ACK", 1)],
                    underlying_type="int32_t",
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name="PacketKind",
                        source_type="tyEnum_PacketKind__112233",
                    ),
                ),
                Function(
                    name="sendPacket",
                    return_type=CType("bool"),
                    parameters=[
                        Parameter(
                            name="pkt",
                            type=Pointer(CType("Packet")),
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name="pkt",
                                source_type="tyObject_Packet__9f8e7d*",
                            ),
                        ),
                        Parameter(
                            name="cb",
                            type=expected_fp,
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name="cb",
                                source_type="tyProc__a1b2c3d4",
                            ),
                        ),
                    ],
                    type_overrides={"pyi": "bool"},
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name="sendPacket",
                        source_type="NIM_BOOL",
                    ),
                ),
            ],
        )


class TestNimBackendEnrichmentAndDirectNimParsing:
    """Verify NimBackend enriches docstrings, ParsedDoc, and SourceMeta from jsondoc and direct .nim AST."""

    def test_jsondoc_enrichment_and_pyi_rendering(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Verify clean_nim_c_unit attaches docstrings + ParsedDoc across all 6 IR declarations and renders .pyi."""
        unit = SourceUnit(
            path="mathlib.h",
            declarations=[
                Constant(name="MAX_ITERS", value=100),
                Enum(name="Mode", values=[EnumValue("FAST", 0)]),
                Struct(
                    name="Point",
                    fields=[
                        Field("x", CType("double")),
                        Field("y", CType("double")),
                    ],
                ),
                Typedef(name="Coord", underlying_type=CType("double")),
                Variable(name="g_tolerance", type=CType("double")),
                Function(
                    name="add",
                    return_type=CType("int"),
                    parameters=[
                        Parameter("a_p0", CType("int")),
                        Parameter("b_p1", CType("int")),
                    ],
                ),
            ],
        )
        entries = [
            {"name": "MAX_ITERS", "description": "Maximum solver iterations."},
            {"name": "Mode", "description": "Execution mode."},
            {
                "name": "Point",
                "type": "skType",
                "description": "2D Cartesian point.",
            },
            {"name": "Coord", "description": "Coordinate scalar alias."},
            {"name": "g_tolerance", "description": "Global convergence tolerance."},
            {
                "name": "add",
                "type": "skProc",
                "description": "Add two integers together.",
                "signature": {
                    "arguments": [
                        {"name": "lhs", "type": "cint"},
                        {"name": "rhs", "type": "cint"},
                    ]
                },
            },
        ]
        enriched = clean_nim_c_unit(unit, jsondoc_entries=entries)
        stub = PyiWriter().write(enriched)
        expected_stub = textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            import enum

            MAX_ITERS: int = 100
            \"\"\"Maximum solver iterations.\"\"\"

            class Mode(enum.IntEnum):
                \"\"\"Execution mode.\"\"\"
                FAST = 0

            class Point:
                \"\"\"2D Cartesian point.\"\"\"
                x: float
                y: float

            Coord = float
            \"\"\"Coordinate scalar alias.\"\"\"

            g_tolerance: float
            \"\"\"Global convergence tolerance.\"\"\"

            def add(lhs: int, rhs: int) -> int:
                \"\"\"Add two integers together.\"\"\"
                ...
        """)
        assert stub == expected_stub

        # Negative control: invalid JSON file logs warning and leaves declarations intact
        bad_json = tmp_path / "broken.json"
        bad_json.write_text("{not valid json", encoding="utf-8")
        _ = clean_nim_c_unit(unit, jsondoc_path=bad_json)
        assert any("Failed to load jsondoc file" in r.message for r in caplog.records)

    def test_jsondoc_kind_keyed_disambiguation_between_type_and_proc(self) -> None:
        """Verify clean_nim_c_unit keys docstrings by (category, name) so skProc and skType do not collide."""
        unit = SourceUnit(
            path="collision.h",
            declarations=[
                Struct(
                    name="Widget",
                    fields=[Field("id", CType("int"))],
                ),
                Function(
                    name="Widget",
                    return_type=CType("int"),
                    parameters=[Parameter("w_p0", CType("int"))],
                ),
                Struct(
                    name="OnlyProcDoc",
                    fields=[Field("v", CType("int"))],
                ),
                Function(
                    name="OnlyProcDoc",
                    return_type=CType("void"),
                    parameters=[],
                ),
            ],
        )
        entries = [
            {
                "name": "Widget",
                "type": "skType",
                "description": "Widget data structure.",
            },
            {
                "name": "Widget",
                "type": "skProc",
                "description": "Construct a Widget.",
                "parameters": ["widget_id"],
            },
            {
                "name": "OnlyProcDoc",
                "type": "skProc",
                "description": "Proc-only documentation must not leak onto struct.",
            },
        ]
        enriched = clean_nim_c_unit(unit, jsondoc_entries=entries)
        decls = enriched.declarations
        assert isinstance(decls[0], Struct) and decls[0].doc == "Widget data structure."
        assert isinstance(decls[1], Function) and decls[1].doc == "Construct a Widget."
        assert decls[1].parameters[0].name == "widget_id"
        assert isinstance(decls[2], Struct) and decls[2].doc is None
        assert isinstance(decls[3], Function) and decls[3].doc == "Proc-only documentation must not leak onto struct."

    @pytest.mark.skipif(shutil.which("nim") is None, reason="Nim compiler not installed")
    def test_direct_nim_source_parsing_and_include_fragments(self, tmp_path: Path) -> None:
        """Verify NimBackend parses .nim files directly via _nim_ast_helper.nim, follows includes, and populates SourceMeta & ParsedDoc."""
        assert "nim" in list_backends()
        assert "nimony" in list_backends()
        assert InputSpec.from_path("mylib.nim").language == "nim"
        assert InputSpec.from_path("mylib.nif").language == "nif"

        proj = tmp_path / "mylib"
        src_dir = proj / "src"
        frag_dir = src_dir / "fragments"
        frag_dir.mkdir(parents=True)

        root_nim = src_dir / "mylib.nim"
        frag_nim = frag_dir / "ops.nim"

        root_nim.write_text(
            textwrap.dedent("""\
                include fragments/ops

                type
                  EngineConfig* {.exportc: "mylib_config_t", bycopy.} = object
                    ## Engine runtime configuration.
                    threads*: cint ## Worker thread count.
                    verbose*: bool

                proc initEngine*(cfg: ptr EngineConfig, threads: cint): bool {.exportc: "mylib_init_engine", cdecl, dynlib.} =
                  ## Initialize the engine with a thread count.
                  ##
                  ## :param cfg: Configuration struct pointer.
                  ## :param threads: Number of worker threads.
                  ## :return: True if initialized.
                  true
            """),
            encoding="utf-8",
        )
        frag_nim.write_text(
            textwrap.dedent("""\
                proc computeFast*(lhs: cint, rhs: cint): cint {.exportc: "mylib_compute", cdecl, dynlib.} =
                  ## Fast two-operand computation from fragment.
                  lhs + rhs

                proc computeFast*(lhs: cint, rhs: cint, scale: cint): cint {.exportc: "mylib_compute_scaled", deprecated: "Use mylib_compute instead", cdecl, dynlib.} =
                  ## Scaled three-operand computation from fragment.
                  (lhs + rhs) * scale
            """),
            encoding="utf-8",
        )

        backend = get_backend("nim")
        unit = backend.parse(root_nim.read_text(encoding="utf-8"), str(root_nim))
        assert str(frag_nim.resolve()) in unit.included_headers

        funcs = [d for d in unit.declarations if isinstance(d, Function)]
        assert [f.name for f in funcs] == ["mylib_compute", "mylib_compute_scaled", "mylib_init_engine"]

        init_fn = funcs[2]
        assert init_fn.source_meta == SourceMeta(
            source_language="nim",
            source_name="initEngine",
            source_type="bool",
            attributes=(("exportc", ""), ("cdecl", ""), ("dynlib", "")),
            is_exported=True,
            module=str(root_nim.resolve()),
        )
        assert init_fn.parsed_doc == ParsedDoc(
            summary="Initialize the engine with a thread count.",
            params=(
                ParamDoc(name="cfg", description="Configuration struct pointer."),
                ParamDoc(name="threads", description="Number of worker threads."),
            ),
            returns="True if initialized.",
            source_format="rst",
        )
        assert init_fn.parameters[0].doc == "Configuration struct pointer."
        assert init_fn.parameters[0].source_meta == SourceMeta(
            source_language="nim",
            source_name="cfg",
            source_type="ptr EngineConfig",
            module=str(root_nim.resolve()),
        )
        assert funcs[1].is_deprecated is True

        # Also test generate() auto-selecting NimBackend for .nim input
        pyi_out = generate(root_nim, "pyi", no_cache=True)
        expected_pyi = textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            def mylib_compute(lhs: int, rhs: int) -> int:
                \"\"\"Fast two-operand computation from fragment.\"\"\"
                ...

            def mylib_compute_scaled(lhs: int, rhs: int, scale: int) -> int:
                \"\"\"Scaled three-operand computation from fragment.

                Deprecated.
                \"\"\"
                ...

            class mylib_config_t:
                \"\"\"Engine runtime configuration.\"\"\"
                threads: int
                verbose: bool

            def mylib_init_engine(cfg: mylib_config_t, threads: int) -> bool:
                \"\"\"Initialize the engine with a thread count.

                :param cfg: Configuration struct pointer.
                :param threads: Number of worker threads.
                :returns: True if initialized.
                \"\"\"
                ...
        """)
        assert pyi_out == expected_pyi


class TestNimonyBackend:
    """Verify NimonyBackend parses Nimony NIF (.nif) S-expression AST files into IR with SourceMeta & ParsedDoc."""

    def test_nimony_nif_parsing_and_pyi_generation(self, tmp_path: Path) -> None:
        """Verify NimonyBackend extracts structs, enums, procs, consts, SourceMeta(source_language='nimony'), and ParsedDoc."""
        nif_code = textwrap.dedent("""\
            .nif24
            (stmts
              (type Vec2.0.math x . (pragmas (exportc "naga_vec2_t") (bycopy))
                (object .
                  "2D vector in Nimony."
                  (fld x.0 x . (f +64) .)
                  (fld y.0 x . (f +64) .)))
              (enum Status.0.math x . (pragmas (size 4))
                (enum (u +32)
                  (efld Ok.0 x . +0 "Operation succeeded")
                  (efld Err.0 x . +1 "Operation failed")))
              (proc dotProduct.0.math x . .
                (params
                  (param a.0 . . (ptr naga_vec2_t) .)
                  (param b.0 . . (ptr naga_vec2_t) .))
                (f +64)
                (pragmas (exportc "naga_dot_product") (cdecl))
                .
                (stmts
                  "Compute dot product of two 2D vectors.\\n\\n:param a: First vector pointer.\\n:param b: Second vector pointer.\\n:return: Scalar dot product."))
              (const VERSION.0.math x (pragmas (exportc)) (i -32) +3)
            )
        """)
        nif_file = tmp_path / "math.nif"
        nif_file.write_text(nif_code, encoding="utf-8")

        backend = NimonyBackend()
        unit = backend.parse(nif_code, str(nif_file))

        assert len(unit.declarations) == 4
        struct_decl = unit.declarations[0]
        assert isinstance(struct_decl, Struct)
        assert struct_decl.name == "naga_vec2_t"
        assert struct_decl.source_meta == SourceMeta(
            source_language="nimony",
            source_name="Vec2",
            source_type="object",
            attributes=(("exportc", ""), ("bycopy", "")),
            is_exported=True,
            module=str(nif_file),
        )
        assert struct_decl.parsed_doc == ParsedDoc(summary="2D vector in Nimony.", source_format="plain")

        enum_decl = unit.declarations[1]
        assert isinstance(enum_decl, Enum)
        assert enum_decl.name == "Status"
        assert enum_decl.values == [
            EnumValue(
                name="Ok",
                value=0,
                doc="Operation succeeded",
                parsed_doc=ParsedDoc(summary="Operation succeeded", source_format="plain"),
                source_meta=SourceMeta(source_language="nimony", source_name="Ok", module=str(nif_file)),
            ),
            EnumValue(
                name="Err",
                value=1,
                doc="Operation failed",
                parsed_doc=ParsedDoc(summary="Operation failed", source_format="plain"),
                source_meta=SourceMeta(source_language="nimony", source_name="Err", module=str(nif_file)),
            ),
        ]

        fn_decl = unit.declarations[2]
        assert isinstance(fn_decl, Function)
        assert fn_decl.name == "naga_dot_product"
        assert fn_decl.calling_convention == "cdecl"
        assert fn_decl.source_meta == SourceMeta(
            source_language="nimony",
            source_name="dotProduct",
            source_type="float64",
            attributes=(("exportc", ""), ("cdecl", "")),
            is_exported=True,
            module=str(nif_file),
        )
        assert fn_decl.parsed_doc == ParsedDoc(
            summary="Compute dot product of two 2D vectors.",
            params=(
                ParamDoc(name="a", description="First vector pointer."),
                ParamDoc(name="b", description="Second vector pointer."),
            ),
            returns="Scalar dot product.",
            source_format="rst",
        )

        const_decl = unit.declarations[3]
        assert isinstance(const_decl, Constant)
        assert const_decl.name == "VERSION"
        assert const_decl.value == 3

        # Verify generate() auto-detects NimonyBackend for .nif files
        pyi_out = generate(nif_file, "pyi", no_cache=True)
        expected_pyi = textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            import enum

            class naga_vec2_t:
                \"\"\"2D vector in Nimony.\"\"\"
                x: float
                y: float

            class Status(enum.IntEnum):
                Ok = 0
                Err = 1

            def naga_dot_product(a: naga_vec2_t, b: naga_vec2_t) -> float:
                \"\"\"Compute dot product of two 2D vectors.

                :param a: First vector pointer.
                :param b: Second vector pointer.
                :returns: Scalar dot product.
                \"\"\"
                ...

            VERSION: int = 3
        """)
        assert pyi_out == expected_pyi


class TestRealNimHeaderBackends:
    """Verify both tree-sitter and libclang parse real Nim --header output via NimBackend."""

    def test_find_nim_libpath_parses_nim_dump_json(self, tmp_path: Path) -> None:
        """Verify _find_nim_libpath extracts libpath from 'nim dump --dump.format:json .'."""
        _find_nim_libpath.cache_clear()
        fake_lib = tmp_path / "nim" / "lib"
        fake_lib.mkdir(parents=True)
        (fake_lib / "nimbase.h").write_text("/* fake nimbase.h */\n", encoding="utf-8")

        dump_payload = json.dumps({"version": "2.2.12", "libpath": str(fake_lib)})
        mock_proc = MagicMock(
            returncode=0,
            stdout=f"Hint: used config\n{dump_payload}\n",
        )
        nim_mod = sys.modules["headerkit.backends.nim"]
        with patch.object(nim_mod.subprocess, "run", return_value=mock_proc) as mock_run:
            result = _find_nim_libpath()
            assert result == str(fake_lib)
            mock_run.assert_called_once_with(
                ["nim", "dump", "--dump.format:json", "."],
                cwd=tempfile.gettempdir(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        assert _has_nimbase_in_dirs([str(fake_lib)], None, "foo.h") is True
        assert _has_nimbase_in_dirs(None, ["-I", str(fake_lib)], "foo.h") is True
        assert _has_nimbase_in_dirs(None, ["-isystem", str(fake_lib)], "foo.h") is True
        assert _has_nimbase_in_dirs([str(tmp_path)], None, "foo.h") is False
        _find_nim_libpath.cache_clear()

    @pytest.mark.skipif(not is_backend_available("libclang"), reason="libclang not available")
    def test_libclang_nimbase_retry_and_skip_function_bodies(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify NimBackend.prepare_c_parse_args() and NimBackend.parse() handle nimbase.h and skip_function_bodies."""
        nim_lib = tmp_path / "nimlib"
        nim_lib.mkdir()
        (nim_lib / "nimbase.h").write_text(
            textwrap.dedent("""\
                #ifndef NIMBASE_H
                #define NIMBASE_H
                #define N_NIMCALL(rettype, name) rettype name
                #define N_LIB_EXPORT extern
                #define N_INLINE(rettype, name) inline rettype name
                typedef long long NI;
                #endif
            """),
            encoding="utf-8",
        )
        nim_mod = sys.modules["headerkit.backends.nim"]
        monkeypatch.setattr(nim_mod, "_find_nim_libpath", lambda: str(nim_lib))

        nim_backend = NimBackend(c_backend="libclang")

        # (a) Included header requires nimbase.h resolved via NimBackend
        inc_dir = tmp_path / "inc"
        inc_dir.mkdir()
        (inc_dir / "inner_nim.h").write_text(
            '#include "nimbase.h"\nN_LIB_EXPORT N_NIMCALL(NI, umbrella_calc)(NI x_p0);\n',
            encoding="utf-8",
        )
        umbrella_code = '#include "inner_nim.h"\n'
        umbrella_unit = nim_backend.parse(
            umbrella_code,
            str(inc_dir / "main.h"),
            include_dirs=[str(inc_dir)],
        )
        umbrella_pyi = PyiWriter().write(umbrella_unit)
        assert umbrella_pyi == textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            def umbrella_calc(x: int) -> int: ...
        """)

        # (b) skip_function_bodies when inline body references undeclared runtime symbol
        debug_code = textwrap.dedent("""\
            #include "nimbase.h"
            static N_INLINE(void, nimFrame)(void* s_p0) {
                framePtr__system_u1 = s_p0;
            }
            N_LIB_EXPORT N_NIMCALL(NI, debug_add)(NI a_p0, NI b_p1);
        """)
        debug_unit = nim_backend.parse(
            debug_code,
            str(tmp_path / "debug_nim.h"),
        )
        debug_pyi = PyiWriter().write(debug_unit)
        assert debug_pyi == textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            def debug_add(a: int, b: int) -> int: ...
        """)

    @pytest.mark.skipif(shutil.which("nim") is None, reason="Nim compiler not installed")
    def test_real_nim_compiler_header_treesitter_and_libclang(self, tmp_path: Path) -> None:
        """Compile a real Nim module with --header and parse with NimBackend using both tree-sitter and libclang."""
        nim_src = tmp_path / "sample_nim.nim"
        nim_src.write_text(
            textwrap.dedent("""\
                type
                  Point* {.exportc: "Point", bycopy.} = object
                    x*: cdouble
                    y*: cdouble

                proc add*(a: cint, b: cint): cint {.exportc: "add", cdecl, dynlib.} =
                  a + b

                proc greet*(name: cstring): cstring {.exportc: "greet", cdecl, dynlib.} =
                  name
            """),
            encoding="utf-8",
        )
        nimcache = tmp_path / "nimcache"
        header_path = nimcache / "sample_nim.h"
        cmd = [
            "nim",
            "c",
            "-d:release",
            "-d:noSignalHandler",
            "--app:lib",
            "--noMain",
            f"--nimcache:{nimcache}",
            f"--header:{header_path.name}",
            str(nim_src),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        assert res.returncode == 0, f"nim c failed: {res.stderr}\n{res.stdout}"
        assert header_path.is_file()

        code = header_path.read_text(encoding="utf-8")
        ts_nim_backend = NimBackend(c_backend="tree-sitter")
        ts_cleaned = ts_nim_backend.parse(code, str(header_path), nim_source_path=nim_src)
        ts_pyi = PyiWriter().write(ts_cleaned)

        expected_pyi = textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            class Point:
                x: float
                y: float

            def add(a: int, b: int) -> int: ...

            def greet(name: bytes | str) -> bytes: ...
        """)
        assert ts_pyi == expected_pyi

        if is_backend_available("libclang"):
            lc_nim_backend = NimBackend(c_backend="libclang")
            lc_cleaned = lc_nim_backend.parse(code, str(header_path), nim_source_path=nim_src)
            lc_pyi = PyiWriter().write(lc_cleaned)
            assert lc_pyi == expected_pyi

    @pytest.mark.skipif(not is_backend_available("libclang"), reason="libclang not available")
    def test_libclang_glibc_dunder_int64_and_typedef_before_struct_def(self, tmp_path: Path) -> None:
        """Verify _resolve_dependencies ignores __* system typedefs and _process_typedef removes prior forward decls."""
        from headerkit.backends.libclang import LibclangBackend

        sys_inc = tmp_path / "sysinc"
        sys_inc.mkdir()
        (sys_inc / "fake_stdint.h").write_text(
            textwrap.dedent("""\
                typedef signed long int __int64_t;
                typedef __int64_t int64_t;
                typedef int64_t NI64;
                typedef NI64 NI;
            """),
            encoding="utf-8",
        )
        hdr = textwrap.dedent("""\
            #include "fake_stdint.h"
            typedef struct Point Point;
            struct Point {
                double x;
                double y;
            };
            NI compute_point(Point p_p0, NI scale_p1);
        """)
        backend = LibclangBackend()
        unit = backend.parse(hdr, str(tmp_path / "main.h"), extra_args=[f"-isystem{sys_inc}"])
        cleaned = clean_nim_c_unit(unit)
        pyi = PyiWriter().write(cleaned)
        assert pyi == textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            class Point:
                x: float
                y: float

            def compute_point(p: Point, scale: int) -> int: ...
        """)
        structs = [d for d in cleaned.declarations if isinstance(d, Struct)]
        assert len(structs) == 1
        assert structs[0].name == "Point"
        assert [f.name for f in structs[0].fields] == ["x", "y"]

        # Direct LLVM <= 21 cursor sequence test: forward-decl in _seen + declarations
        # followed by _process_typedef where record_type.get_declaration().is_definition() is True
        import headerkit.backends as b
        from headerkit.backends.libclang import ClangASTConverter, CursorKind, TypeKind

        assert sys.modules["headerkit.backends.libclang"] is b.libclang

        conv = ClangASTConverter("main.h")
        struct_key = conv._record_key("struct", "Point")
        conv._seen.add(struct_key)
        conv.declarations.append(
            Struct(name="Point", fields=[], methods=[], is_union=False, is_cppclass=False, namespace=None)
        )

        fake_field_cursor = MagicMock()
        fake_field_cursor.kind = CursorKind.FIELD_DECL
        fake_decl = MagicMock()
        fake_decl.spelling = "Point"
        fake_decl.kind = CursorKind.STRUCT_DECL
        fake_decl.is_definition.return_value = True
        fake_decl.get_children.return_value = [fake_field_cursor]
        fake_record_type = MagicMock()
        fake_record_type.kind = TypeKind.RECORD
        fake_record_type.get_declaration.return_value = fake_decl
        fake_underlying = MagicMock()
        fake_underlying.kind = TypeKind.ELABORATED
        fake_underlying.spelling = "struct Point"
        fake_underlying.get_named_type.return_value = fake_record_type
        fake_typedef_cursor = MagicMock()
        fake_typedef_cursor.spelling = "Point"
        fake_typedef_cursor.underlying_typedef_type = fake_underlying

        with (
            patch.object(conv, "_convert_field", return_value=Field("x", CType("double"))),
            patch.object(conv, "_get_location", return_value=None),
        ):
            conv._process_typedef(fake_typedef_cursor)

        assert conv.declarations == [
            Struct(
                name="Point",
                fields=[Field("x", CType("double"))],
                methods=[],
                is_union=False,
                is_cppclass=False,
                namespace=None,
                location=None,
                is_typedef=True,
            )
        ]


class TestCindexShutdownSafety:
    """Verify vendored cindex.__del__ runtime patching and libclang _PROBE_INDEX cleanup at interpreter shutdown."""

    def test_cleanup_probe_index_resets_global_and_cache(self) -> None:
        """_cleanup_probe_index() resets _PROBE_INDEX, live backend _index, and clears probe cache."""
        import headerkit.backends.libclang as lc_mod

        sentinel = object()
        orig_probe = lc_mod._PROBE_INDEX
        backend = lc_mod.LibclangBackend()
        backend._index = sentinel
        try:
            lc_mod._PROBE_INDEX = sentinel
            lc_mod._cleanup_probe_index()
            assert lc_mod._PROBE_INDEX is None
            assert backend._index is None
            assert lc_mod._unnamed_bitfields_impose_alignment.cache_info().currsize == 0
        finally:
            lc_mod._PROBE_INDEX = orig_probe

    @pytest.mark.parametrize("ver", [f"v{v}" for v in range(14, 24)])
    def test_cindex_del_methods_safe_when_conf_is_none(self, ver: str) -> None:
        """All vendored cindex.__del__ methods patched by _patch_shutdown_safe_destructors return cleanly when cindex.conf is None."""
        from headerkit._clang import _patch_shutdown_safe_destructors

        mod = importlib.import_module(f"headerkit._clang.{ver}.cindex")
        _patch_shutdown_safe_destructors(mod)
        cls_names = [
            "_CXString",
            "Diagnostic",
            "TokenGroup",
            "CodeCompletionResults",
            "Index",
            "TranslationUnit",
            "CompileCommands",
            "CompilationDatabase",
            "Rewriter",
            "PrintingPolicy",
        ]
        found_names = [name for name in cls_names if hasattr(mod, name)]
        ver_num = int(ver[1:])
        expected_names = cls_names[:8] if ver_num <= 18 else (cls_names[:9] if ver_num == 19 else cls_names)
        assert found_names == expected_names

        with patch.object(mod, "conf", None):
            for name in found_names:
                cls = getattr(mod, name)
                dummy = MagicMock()
                assert cls.__del__(dummy) is None

        # Negative control: when conf is present, Index.__del__ invokes conf.lib.clang_disposeIndex
        mock_conf = MagicMock()
        with patch.object(mod, "conf", mock_conf):
            dummy_idx = MagicMock()
            mod.Index.__del__(dummy_idx)
            mock_conf.lib.clang_disposeIndex.assert_called_once_with(dummy_idx)

    @pytest.mark.skipif(not is_backend_available("libclang"), reason="libclang not available")
    def test_subprocess_parse_bitfield_probe_emits_no_shutdown_stderr(self) -> None:
        """Subprocess parsing unnamed bitfield exits 0 with empty stderr at interpreter shutdown."""
        code = (
            "from headerkit.backends import get_backend; "
            "b = get_backend('libclang'); "
            "b.parse('struct S { char a; unsigned int : 8; char b; };', 'test.h')"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert res.returncode == 0
        assert res.stderr == ""

    @pytest.mark.skipif(not is_backend_available("libclang"), reason="libclang not available")
    def test_generate_pipeline_with_nim_backend_skips_function_bodies(self, tmp_path: Path) -> None:
        """headerkit.generate() with backend_name='nim' skips inline function bodies and strips _p0 suffixes."""
        header_path = tmp_path / "nim_inline.h"
        header_path.write_text(
            textwrap.dedent("""\
                static inline void nimFrame(void) {
                    undeclared_nim_internal_helper(42);
                }
                int exported_add(int a_p0, int b_p1);
            """),
            encoding="utf-8",
        )
        cdef_out = generate(
            header_path,
            "cffi",
            backend_name="nim",
            no_cache=True,
        )
        assert cdef_out.strip() == "int exported_add(int a, int b);"

    def test_packaged_nim_helper_resources_via_importlib_resources(self) -> None:
        """Verify _nim_ast_helper.nim and _nimony_nif_helper.nim are accessible via importlib.resources.files."""
        import importlib.resources

        from headerkit.backends.nim import _load_backend_resource_bytes

        pkg_files = importlib.resources.files("headerkit.backends")
        ast_helper = pkg_files.joinpath("_nim_ast_helper.nim")
        nif_helper = pkg_files.joinpath("_nimony_nif_helper.nim")

        assert ast_helper.is_file() is True
        assert nif_helper.is_file() is True

        ast_bytes = ast_helper.read_bytes()
        nif_bytes = nif_helper.read_bytes()
        assert len(ast_bytes) > 0
        assert len(nif_bytes) > 0
        assert _load_backend_resource_bytes("_nim_ast_helper.nim") == ast_bytes
        assert _load_backend_resource_bytes("_nimony_nif_helper.nim") == nif_bytes

        # Negative control: nonexistent resource returns None
        assert _load_backend_resource_bytes("_nonexistent_helper_file.nim") is None

    @pytest.mark.skipif(shutil.which("nim") is None, reason="Nim compiler not installed")
    def test_public_extract_nim_ast_data_exports_and_schema(self, tmp_path: Path) -> None:
        """Verify extract_nim_ast_data is publicly exported from headerkit, headerkit.backends, and headerkit.backends.nim."""
        from headerkit import extract_nim_ast_data as hk_extract
        from headerkit.backends import extract_nim_ast_data as b_extract
        from headerkit.backends.nim import extract_nim_ast_data as nim_extract

        assert hk_extract is nim_extract
        assert b_extract is nim_extract

        nim_file = tmp_path / "ast_api.nim"
        nim_code = textwrap.dedent("""\
            proc addTwo*(a: int32, b: int32): int32 {.exportc: "c_add_two", cdecl.} =
              ## Add two 32-bit integers.
              a + b
        """)
        nim_file.write_text(nim_code, encoding="utf-8")

        ast_data = hk_extract(nim_file)
        assert isinstance(ast_data, dict)
        assert ast_data["path"] == str(nim_file)
        assert ast_data["included_files"] == []
        decls = ast_data["declarations"]
        assert len(decls) == 1
        assert decls[0]["kind"] == "routine"
        assert decls[0]["name"] == "addTwo"
        assert decls[0]["c_name"] == "c_add_two"
        assert decls[0]["doc"] == "Add two 32-bit integers."

        # Negative control: unavailable nim_executable returns None
        assert hk_extract(nim_file, nim_executable="nonexistent-nim-binary-xyz") is None
