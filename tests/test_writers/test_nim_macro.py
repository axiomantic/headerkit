"""Tests for Nim code generation for C macros and type hints."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
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

        assert 'proc c_MULTIPLY*[T](a, b: T): T {.importc: "MULTIPLY", header: "math_utils.h", nodecl.}' in output
        assert "template multiply*(a, b: untyped): untyped =" in output
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
