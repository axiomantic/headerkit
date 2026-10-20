"""Tests for function-like macro extraction via libclang backend."""

from __future__ import annotations

import pytest

from headerkit.backends.libclang import LibclangBackend
from headerkit.ir import Constant, MacroFunction, MacroParameter

libclang = pytest.mark.libclang


@libclang
class TestLibclangMacroFunctionExtraction:
    """Tests for extracting MacroFunction declarations in LibclangBackend."""

    @pytest.fixture(autouse=True)
    def setup_backend(self) -> None:
        self.backend = LibclangBackend()

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

    def test_macro_redefinition_replaces_earlier(self) -> None:
        code = "#define CALC(x) ((x) + 1)\n#define CALC(x) ((x) * 2)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction) and d.name == "CALC"]
        assert len(macros) == 1
        assert macros[0].body == "((x) * 2)"

    def test_macro_undef_dropped(self) -> None:
        code = "#define TEMP_MACRO(x) ((x) + 1)\n#undef TEMP_MACRO\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction) and d.name == "TEMP_MACRO"]
        assert len(macros) == 0

    def test_object_macro_with_leading_paren_is_constant_not_function(self) -> None:
        code = "#define VALUE (1 + 2)\nvoid dummy(void);\n"
        header = self.backend.parse(code, "test.h")
        macros = [d for d in header.declarations if isinstance(d, MacroFunction)]
        assert len(macros) == 0
        constants = [d for d in header.declarations if isinstance(d, Constant) and d.name == "VALUE"]
        assert len(constants) == 1
        assert constants[0].value == 3
