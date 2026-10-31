"""Tests for Nim code generation for C macros and type hints."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from headerkit.ir import Header, MacroFunction, MacroParameter
from headerkit.scaffold import ScaffoldOptions
from headerkit.writers.nim import NimWriter, write_nim


class TestNimMacroCodeGeneration:
    """Tests for NimWriter macro code generation."""

    def test_untyped_expression_macro(self) -> None:
        macro = MacroFunction(
            name="MULTIPLY",
            parameters=[MacroParameter("a"), MacroParameter("b")],
            body="((a) * (b))",
        )
        header = Header(path="math_utils.h", declarations=[macro])
        writer = NimWriter(header_path="math_utils.h")
        output = writer.write(header)

        assert (
            'proc c_MULTIPLY*[T1, T2](a: T1, b: T2): T1 {.importc: "MULTIPLY", header: "math_utils.h", nodecl.}'
            in output
        )
        assert "template multiply*(a: untyped, b: untyped): untyped =" in output
        assert "c_MULTIPLY(a, b)" in output

    def test_zero_param_expression_macro(self) -> None:
        macro = MacroFunction(
            name="GET_MAGIC",
            parameters=[],
            body="(42)",
        )
        header = Header(path="magic.h", declarations=[macro])
        writer = NimWriter(header_path="magic.h")
        output = writer.write(header)

        assert 'proc c_GET_MAGIC*(): cint {.importc: "GET_MAGIC", header: "magic.h", nodecl.}' in output
        assert "template get_magic*(): untyped =" in output
        assert "c_GET_MAGIC()" in output

    def test_statement_macro_emits_void(self) -> None:
        macro = MacroFunction(
            name="SAFE_RESET",
            parameters=[MacroParameter("p")],
            body="do { if (p) { *(p) = 0; } } while (0)",
            is_statement=True,
        )
        header = Header(path="reset.h", declarations=[macro])
        writer = NimWriter(header_path="reset.h")
        output = writer.write(header)

        assert 'proc c_SAFE_RESET*[T](p: T): void {.importc: "SAFE_RESET", header: "reset.h", nodecl.}' in output
        assert "template safe_reset*(p: untyped): untyped =" in output
        assert "c_SAFE_RESET(p)" in output

    def test_token_pasting_macro_skipped_with_diagnostic(self) -> None:
        macro = MacroFunction(
            name="GLUE",
            parameters=[MacroParameter("a"), MacroParameter("b")],
            body="a ## b",
            has_token_pasting=True,
        )
        header = Header(path="glue.h", declarations=[macro])
        writer = NimWriter(header_path="glue.h")
        output = writer.write(header)

        assert "# Macro GLUE skipped: contains preprocessor token-pasting (##) operator" in output
        assert "proc c_GLUE" not in output
        assert "template glue" not in output

    def test_configured_typed_macro(self) -> None:
        macro = MacroFunction(
            name="CUSTOM_MALLOC",
            parameters=[MacroParameter("sz")],
            body="malloc(sz)",
        )
        header = Header(path="mem.h", declarations=[macro])
        macro_types = {
            "CUSTOM_MALLOC": {
                "return_type": "pointer",
                "parameters": [{"name": "sz", "type": "csize_t"}],
            }
        }
        writer = NimWriter(header_path="mem.h", macro_types=macro_types)
        output = writer.write(header)

        assert (
            'proc custom_malloc*(sz: csize_t): pointer {.importc: "CUSTOM_MALLOC", header: "mem.h", nodecl.}' in output
        )
        assert "template custom_malloc" not in output

    def test_scaffold_options_macro_types_passthrough(self) -> None:
        macro = MacroFunction(
            name="CALC",
            parameters=[MacroParameter("x")],
            body="((x) * 2)",
        )
        header = Header(path="calc.h", declarations=[macro])
        macro_types = {
            "CALC": {
                "return_type": "cint",
                "parameters": [{"name": "x", "type": "cint"}],
            }
        }
        options = ScaffoldOptions(
            target_language="nim",
            layout="file",
            options={"header_path": "calc.h", "macro_types": macro_types},
        )
        writer = NimWriter()
        layout = writer.write_layout(header, options)
        content = layout.files[0].content
        assert 'proc calc*(x: cint): cint {.importc: "CALC", header: "calc.h", nodecl.}' in content

    def test_nim_compiler_executes_generated_macro_bindings(self) -> None:
        if not shutil.which("nim"):
            pytest.skip("nim compiler is not installed on PATH")

        with tempfile.TemporaryDirectory() as td:
            h_path = Path(td) / "macros.h"
            h_path.write_text(
                "#define ADD(a, b) ((a) + (b))\n"
                "#define RESET(p) do { *(p) = 0; } while (0)\n"
                "#define SQUARE(x) ((x) * (x))\n"
            )

            macros = [
                MacroFunction(name="ADD", parameters=[MacroParameter("a"), MacroParameter("b")], body="((a) + (b))"),
                MacroFunction(
                    name="RESET", parameters=[MacroParameter("p")], body="do { *(p) = 0; } while (0)", is_statement=True
                ),
                MacroFunction(name="SQUARE", parameters=[MacroParameter("x")], body="((x) * (x))"),
            ]
            header = Header(path=str(h_path), declarations=macros)
            macro_types = {
                "SQUARE": {
                    "return_type": "cint",
                    "parameters": [{"name": "x", "type": "cint"}],
                }
            }
            nim_code = write_nim(header, header_path=str(h_path), macro_types=macro_types)

            # Append real execution assertions
            test_script = (
                nim_code
                + "\n\n"
                + "let sum = add(15, 25)\n"
                + "assert sum == 40\n"
                + "var target: cint = 99\n"
                + "reset(addr target)\n"
                + "assert target == 0\n"
                + "let sq = square(6)\n"
                + "assert sq == 36\n"
            )
            nim_file = Path(td) / "test_macros.nim"
            nim_file.write_text(test_script)

            cmd = ["nim", "c", "-r", "--hints:off", str(nim_file)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0, f"Nim compile/run failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    def test_token_pasting_macro_c_shim_generation(self) -> None:
        macro_zero = MacroFunction(
            name="REG_BASE",
            parameters=[],
            body="(PERIPH_ ## 0)",
            has_token_pasting=True,
        )
        macro_expr = MacroFunction(
            name="ADD_REG_OFFSET",
            parameters=[MacroParameter("offset")],
            body="(PERIPH_ ## 0 + (offset))",
            has_token_pasting=True,
        )
        macro_stmt = MacroFunction(
            name="CLEAR_REG",
            parameters=[MacroParameter("ptr_val")],
            body="do { *(ptr_val) = PERIPH_ ## 0; } while (0)",
            is_statement=True,
            has_token_pasting=True,
        )
        header = Header(path="periph.h", declarations=[macro_zero, macro_expr, macro_stmt])
        macro_types = {
            "ADD_REG_OFFSET": {
                "return_type": "cint",
                "parameters": [{"name": "offset", "type": "cint"}],
            }
        }
        output = write_nim(header, header_path="periph.h", macro_types=macro_types, emit_macro_shims=True)

        assert "skipped" not in output
        assert "static inline int hk_shim_REG_BASE(void)" in output
        assert "return REG_BASE();" in output
        assert "static inline int hk_shim_ADD_REG_OFFSET(int offset)" in output
        assert "return ADD_REG_OFFSET(offset);" in output
        assert "static inline void hk_shim_CLEAR_REG(int ptr_val)" in output
        assert "CLEAR_REG(ptr_val);" in output
        assert 'proc c_REG_BASE*(): cint {.importc: "hk_shim_REG_BASE", header: "periph.h", nodecl.}' in output
        assert (
            'proc add_reg_offset*(offset: cint): cint {.importc: "hk_shim_ADD_REG_OFFSET", header: "periph.h", nodecl.}'
            in output
        )
        assert (
            'proc c_CLEAR_REG*[T](ptr_val: T): void {.importc: "hk_shim_CLEAR_REG", header: "periph.h", nodecl.}'
            in output
        )

    def test_package_layout_emits_companion_macro_shim_files(self) -> None:
        macro = MacroFunction(
            name="READ_CH",
            parameters=[MacroParameter("delta")],
            body="(CH_BASE_ ## 0 + (delta))",
            has_token_pasting=True,
        )
        header = Header(path="channel.h", declarations=[macro])
        options = ScaffoldOptions(
            package_name="chpkg",
            target_language="nim",
            layout="package",
            options={"header_path": "channel.h", "emit_macro_shims": True},
        )
        layout = NimWriter().write_layout(header, options)
        shims_h = layout.get_file("src/chpkg/_shims.h")
        shims_c = layout.get_file("src/chpkg/_shims.c")
        assert shims_h is not None
        assert shims_c is not None
        assert '#include "channel.h"' in shims_h.content
        assert "static inline int hk_shim_READ_CH(int delta)" in shims_h.content
        assert "return READ_CH(delta);" in shims_h.content
        assert '#include "_shims.h"' in shims_c.content
        assert "typedef int hk_macro_shims_tu_anchor;" in shims_c.content

    @pytest.mark.allow("subprocess")
    def test_nim_compiler_executes_token_pasting_macro_shims(self) -> None:
        if not shutil.which("nim"):
            pytest.skip("nim compiler is not installed on PATH")

        with tempfile.TemporaryDirectory() as td:
            h_path = Path(td) / "hw_regs.h"
            h_path.write_text(
                textwrap.dedent("""\
                    #pragma once
                    #define HW_BANK_0 100
                    #define GET_BANK0() (HW_BANK_ ## 0)
                    #define BANK0_PLUS(x) (HW_BANK_ ## 0 + (x))
                """)
            )
            macros = [
                MacroFunction(
                    name="GET_BANK0",
                    parameters=[],
                    body="(HW_BANK_ ## 0)",
                    has_token_pasting=True,
                ),
                MacroFunction(
                    name="BANK0_PLUS",
                    parameters=[MacroParameter("x")],
                    body="(HW_BANK_ ## 0 + (x))",
                    has_token_pasting=True,
                ),
            ]
            header = Header(path=str(h_path), declarations=macros)
            nim_code = write_nim(
                header,
                header_path=str(h_path),
                macro_types={
                    "BANK0_PLUS": {
                        "return_type": "cint",
                        "parameters": [{"name": "x", "type": "cint"}],
                    }
                },
                emit_macro_shims=True,
            )
            test_script = (
                nim_code
                + "\n"
                + textwrap.dedent("""\
                    assert get_bank0() == 100
                    assert bank0_plus(23) == 123
                """)
            )
            nim_file = Path(td) / "test_token_paste.nim"
            nim_file.write_text(test_script)
            res = subprocess.run(
                ["nim", "c", "-r", "--hints:off", f"--nimcache:{Path(td) / 'nc'}", str(nim_file)],
                capture_output=True,
                text=True,
            )
            assert res.returncode == 0, f"Nim compile/run failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
