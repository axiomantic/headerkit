"""Tests for function-like macro extraction via tree-sitter backend."""

from __future__ import annotations

import pytest

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.ir import MacroFunction, MacroParameter


class TestTreeSitterMacroFunctionExtraction:
    """Tests for extracting MacroFunction declarations in TreeSitterBackend."""

    @pytest.fixture(autouse=True)
    def setup_backend(self) -> None:
        self.backend = TreeSitterBackend()

    def test_simple_expression_macro(self) -> None:
        code = "#define MULTIPLY(a, b) ((a) * (b))\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "MULTIPLY"
        assert m.parameters == [
            MacroParameter(name="a", is_variadic=False),
            MacroParameter(name="b", is_variadic=False),
        ]
        assert m.is_variadic is False
        assert m.body == "((a) * (b))"
        assert m.is_statement is False
        assert m.has_token_pasting is False
        assert m.location is not None
        assert m.location.line == 1

    def test_empty_param_macro(self) -> None:
        code = "#define GET_MAGIC() (42)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "GET_MAGIC"
        assert m.parameters == []
        assert m.is_variadic is False
        assert m.body == "(42)"
        assert m.is_statement is False

    def test_variadic_c99_macro(self) -> None:
        code = "#define LOG_MSG(fmt, ...) printf(fmt, __VA_ARGS__)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "LOG_MSG"
        assert m.parameters == [
            MacroParameter(name="fmt", is_variadic=False),
            MacroParameter(name="__VA_ARGS__", is_variadic=True),
        ]
        assert m.is_variadic is True
        assert m.body == "printf(fmt, __VA_ARGS__)"

    def test_variadic_only_macro(self) -> None:
        code = "#define PASS_THROUGH(...) call(__VA_ARGS__)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "PASS_THROUGH"
        assert m.parameters == [MacroParameter(name="__VA_ARGS__", is_variadic=True)]
        assert m.is_variadic is True
        assert m.body == "call(__VA_ARGS__)"

    def test_variadic_gnu_named_macro(self) -> None:
        code = "#define LOG_GNU(fmt, args...) printf(fmt, args)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "LOG_GNU"
        assert m.parameters == [
            MacroParameter(name="fmt", is_variadic=False),
            MacroParameter(name="args", is_variadic=True),
        ]
        assert m.is_variadic is True
        assert m.body == "printf(fmt, args)"

    def test_statement_macro_do_while(self) -> None:
        code = "#define SWAP(t, a, b) do { t _tmp = a; a = b; b = _tmp; } while (0)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "SWAP"
        assert m.is_statement is True
        assert "while (0)" in m.body or "while(0)" in m.body

    def test_statement_macro_semicolon(self) -> None:
        code = "#define RESET_TWO(x, y) x = 0; y = 0\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "RESET_TWO"
        assert m.is_statement is True

    def test_token_pasting_macro(self) -> None:
        code = "#define GLUE(a, b) a ## b\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "GLUE"
        assert m.has_token_pasting is True
        assert "##" in m.body

    def test_token_pasting_inside_string_not_flagged(self) -> None:
        code = '#define STRING_WITH_HASH(s) "prefix ## " s\nvoid dummy(void);\n'
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 1
        m = macros[0]
        assert m.name == "STRING_WITH_HASH"
        assert m.has_token_pasting is False

    def test_preprocessor_branch_isolation(self) -> None:
        code = (
            "#ifdef ACTIVE\n"
            "#define ACTIVE_MACRO(x) ((x) + 1)\n"
            "#else\n"
            "#define INACTIVE_MACRO(x) ((x) - 1)\n"
            "#endif\n"
            "void dummy(void);\n"
        )
        header = self.backend.parse(code, "test.h")
        macro_names = [d.name for d in header.declarations if isinstance(d, MacroFunction)]
        assert "ACTIVE_MACRO" in macro_names
        assert "INACTIVE_MACRO" not in macro_names
