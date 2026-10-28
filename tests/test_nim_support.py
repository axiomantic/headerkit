"""Tests for Nim --header support, NimCleanupTransform, and NimJsonDocEnricher."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from headerkit import NimCleanupTransform, NimJsonDocEnricher
from headerkit.backends import is_backend_available
from headerkit.backends.libclang import _find_nim_libpath, _has_nimbase_in_dirs
from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.ir import (
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
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
        """Verify N_CDECL, N_NIMCALL, N_STDCALL, N_FASTCALL, N_NOCONV, N_INLINE, N_NOINLINE, N_*_PTR, and NCSTRING."""
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
        unit = backend.parse(code, "nim_mod.h")

        # Negative control: N_CDECL / N_NIMCALL / N_LIB_PRIVATE must NEVER appear as Variable or Function names
        decl_names = [getattr(d, "name", None) for d in unit.declarations]
        assert "N_CDECL" not in decl_names
        assert "N_NIMCALL" not in decl_names
        assert "N_LIB_PRIVATE" not in decl_names
        assert "N_LIB_EXPORT" not in decl_names

        loc = lambda line, col: SourceLocation(file="nim_mod.h", line=line, column=col)  # noqa: E731
        c_int = CType("int", is_elaborated=False)
        c_void = CType("void", is_elaborated=False)
        c_char_ptr = Pointer(CType("char", is_elaborated=False))
        expected_declarations = [
            Struct(
                name="MyItem",
                fields=[
                    Field(name="id", type=c_int, access="public"),
                    Field(name="label", type=c_char_ptr, access="public"),
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
                underlying_type=Pointer(
                    FunctionPointer(
                        return_type=c_int,
                        parameters=[
                            Parameter(name="a_p0", type=c_int),
                            Parameter(name="b_p1", type=c_int),
                        ],
                    )
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
                return_type=c_char_ptr,
                parameters=[
                    Parameter(name="input_p0", type=c_char_ptr),
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


class TestNimCleanupTransform:
    """Verify NimCleanupTransform strips _p0/`gensym suffixes, filters signalHandler, and normalizes Nim types."""

    def test_parameter_suffix_stripping_and_signal_handler_filtering(self) -> None:
        """Verify _p0, _p1, `gensym0, _gensym1 stripping and signalHandler removal with negative controls."""
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

        cleaned = unit >> NimCleanupTransform()
        expected = SourceUnit(
            path="nim_out.h",
            declarations=[
                Typedef(name="Color", underlying_type=CType("int32_t")),
                Struct(
                    name="Vector",
                    fields=[
                        Field(name="data", type=Pointer(CType("char", is_elaborated=False))),
                        Field(name="length", type=CType("intptr_t")),
                        Field(name="elem_0", type=CType("bool")),
                    ],
                ),
                Enum(
                    name="NimMode",
                    values=[EnumValue("MODE_FAST", 0)],
                    underlying_type="int32_t",
                ),
                Function(
                    name="nim_add",
                    return_type=CType("intptr_t"),
                    parameters=[Parameter(name="x", type=CType("intptr_t"))],
                ),
                Function(
                    name="compute",
                    return_type=CType("double"),
                    parameters=[
                        Parameter(name="alpha", type=CType("int32_t")),
                        Parameter(name="beta", type=CType("float")),
                        Parameter(name="tag", type=Pointer(CType("char", is_elaborated=False))),
                        Parameter(name="flag", type=CType("bool")),
                        Parameter(name="normal_param", type=CType("int")),
                        Parameter(name="p0", type=CType("int")),
                        Parameter(name="_p0", type=CType("int")),
                        Parameter(name="step_p", type=CType("int")),
                    ],
                ),
            ],
        )
        assert cleaned == expected


class TestNimJsonDocEnricher:
    """Verify NimJsonDocEnricher populates docstrings and restores parameter names."""

    def test_jsondoc_enrichment_and_pyi_rendering(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Verify NimJsonDocEnricher attaches docstrings across all 6 IR declarations and handles signature.arguments."""
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
        enricher = NimJsonDocEnricher(
            entries=[
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
        )
        enriched = unit >> NimCleanupTransform() >> enricher
        stub = PyiWriter().write(enriched)
        expected_stub = textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            import enum

            MAX_ITERS: int = 100
            \"\"\"Maximum solver iterations.\"\"\"

            class Mode(enum.IntEnum):
                \"\"\"Execution mode.\"\"\"
                FAST: int = 0

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

        # Negative control: invalid JSON file logs warning and leaves unit intact
        bad_json = tmp_path / "broken.json"
        bad_json.write_text("{not valid json", encoding="utf-8")
        bad_enricher = NimJsonDocEnricher(jsondoc_path=bad_json)
        unchanged = unit >> bad_enricher
        assert unchanged == unit
        assert any("Failed to load jsondoc file" in r.message for r in caplog.records)

    def test_jsondoc_kind_keyed_disambiguation_between_type_and_proc(self) -> None:
        """Verify NimJsonDocEnricher keys docstrings by (category, name) so skProc and skType do not collide."""
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
        enricher = NimJsonDocEnricher(
            entries=[
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
        )
        enriched = unit >> enricher
        expected = SourceUnit(
            path="collision.h",
            declarations=[
                Struct(
                    name="Widget",
                    fields=[Field("id", CType("int"))],
                    doc="Widget data structure.",
                ),
                Function(
                    name="Widget",
                    return_type=CType("int"),
                    parameters=[Parameter("widget_id", CType("int"))],
                    doc="Construct a Widget.",
                ),
                Struct(
                    name="OnlyProcDoc",
                    fields=[Field("v", CType("int"))],
                    doc=None,
                ),
                Function(
                    name="OnlyProcDoc",
                    return_type=CType("void"),
                    parameters=[],
                    doc="Proc-only documentation must not leak onto struct.",
                ),
            ],
        )
        assert enriched == expected


class TestRealNimHeaderBackends:
    """Verify both tree-sitter and libclang parse real Nim --header output."""

    def test_find_nim_libpath_parses_nim_dump_json(self, tmp_path: Path) -> None:
        """Verify _find_nim_libpath extracts libpath from 'nim dump --dump.format:json .'."""
        _find_nim_libpath.cache_clear()
        fake_lib = tmp_path / "nim" / "lib"
        fake_lib.mkdir(parents=True)
        (fake_lib / "nimbase.h").write_text("/* fake nimbase.h */\n", encoding="utf-8")

        mock_proc = MagicMock(
            returncode=0,
            stdout=f'Hint: used config\n{{"version":"2.2.12","libpath":"{fake_lib}"}}\n',
        )
        with patch("headerkit.backends.libclang.subprocess.run", return_value=mock_proc) as mock_run:
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
        """Verify LibclangBackend.parse() exercises nimbase.h diagnostic retry and PARSE_SKIP_FUNCTION_BODIES retry."""
        from headerkit.backends.libclang import LibclangBackend

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
        monkeypatch.setattr("headerkit.backends.libclang._find_nim_libpath", lambda: str(nim_lib))

        # (a) Diagnostic retry path when an included header requires nimbase.h
        inc_dir = tmp_path / "inc"
        inc_dir.mkdir()
        (inc_dir / "inner_nim.h").write_text(
            '#include "nimbase.h"\nN_LIB_EXPORT N_NIMCALL(NI, umbrella_calc)(NI x_p0);\n',
            encoding="utf-8",
        )
        backend = LibclangBackend()
        umbrella_unit = backend.parse(
            '#include "inner_nim.h"\n',
            str(inc_dir / "main.h"),
            include_dirs=[str(inc_dir)],
        )
        umbrella_pyi = PyiWriter().write(umbrella_unit >> NimCleanupTransform())
        assert umbrella_pyi == textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            def umbrella_calc(x: int) -> int: ...
        """)

        # (b) PARSE_SKIP_FUNCTION_BODIES retry path when inline body references undeclared runtime symbol
        debug_code = textwrap.dedent("""\
            #include "nimbase.h"
            static N_INLINE(void, nimFrame)(void* s_p0) {
                framePtr__system_u1 = s_p0;
            }
            N_LIB_EXPORT N_NIMCALL(NI, debug_add)(NI a_p0, NI b_p1);
        """)
        debug_unit = backend.parse(debug_code, str(tmp_path / "debug_nim.h"))
        debug_pyi = PyiWriter().write(debug_unit >> NimCleanupTransform())
        assert debug_pyi == textwrap.dedent("""\
            # Auto-generated by headerkit - do not edit

            def debug_add(a: int, b: int) -> int: ...
        """)

    @pytest.mark.skipif(shutil.which("nim") is None, reason="Nim compiler not installed")
    def test_real_nim_compiler_header_treesitter_and_libclang(self, tmp_path: Path) -> None:
        """Compile a real Nim module with --header and parse with both tree-sitter and libclang."""
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

        ts_backend = TreeSitterBackend()
        ts_cleaned = ts_backend.parse(code, str(header_path)) >> NimCleanupTransform()
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
            from headerkit.backends.libclang import LibclangBackend

            lc_backend = LibclangBackend()
            lc_unit = lc_backend.parse(
                code,
                str(header_path),
            )
            lc_cleaned = lc_unit >> NimCleanupTransform()
            lc_pyi = PyiWriter().write(lc_cleaned)
            assert lc_pyi == expected_pyi


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
