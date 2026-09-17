"""Tests for MacroParameter and MacroFunction IR classes and JSON serialization."""

from __future__ import annotations

import json

import pytest

from headerkit._ir_json import json_to_header
from headerkit.ir import (
    Header,
    MacroFunction,
    MacroParameter,
    SourceLocation,
)
from headerkit.writers.json import header_to_json, header_to_json_dict


class TestMacroParameter:
    """Tests for MacroParameter instantiation and formatting."""

    def test_instantiation_defaults(self) -> None:
        p = MacroParameter(name="x")
        assert p.name == "x"
        assert p.is_variadic is False
        assert str(p) == "x"

    def test_instantiation_variadic_named(self) -> None:
        p = MacroParameter(name="args", is_variadic=True)
        assert p.name == "args"
        assert p.is_variadic is True
        assert str(p) == "args..."

    def test_instantiation_variadic_ellipsis(self) -> None:
        p = MacroParameter(name="...", is_variadic=True)
        assert p.name == "..."
        assert p.is_variadic is True
        assert str(p) == "..."


class TestMacroFunction:
    """Tests for MacroFunction instantiation and string formatting."""

    def test_instantiation_all_fields(self) -> None:
        loc = SourceLocation(file="defs.h", line=15, column=1)
        params = [
            MacroParameter(name="a", is_variadic=False),
            MacroParameter(name="b", is_variadic=False),
        ]
        macro = MacroFunction(
            name="MIN",
            parameters=params,
            is_variadic=False,
            body="((a) < (b) ? (a) : (b))",
            is_statement=False,
            has_token_pasting=False,
            namespace="my_ns",
            location=loc,
        )
        assert macro.name == "MIN"
        assert macro.parameters == params
        assert macro.is_variadic is False
        assert macro.body == "((a) < (b) ? (a) : (b))"
        assert macro.is_statement is False
        assert macro.has_token_pasting is False
        assert macro.namespace == "my_ns"
        assert macro.location == loc
        assert str(macro) == "#define MIN(a, b) ((a) < (b) ? (a) : (b))"

    def test_instantiation_defaults(self) -> None:
        macro = MacroFunction(name="EMPTY")
        assert macro.name == "EMPTY"
        assert macro.parameters == []
        assert macro.is_variadic is False
        assert macro.body == ""
        assert macro.is_statement is False
        assert macro.has_token_pasting is False
        assert macro.namespace is None
        assert macro.location is None
        assert str(macro) == "#define EMPTY()"

    def test_str_variadic(self) -> None:
        macro = MacroFunction(
            name="LOG",
            parameters=[MacroParameter("fmt")],
            is_variadic=True,
            body="printf(fmt, __VA_ARGS__)",
        )
        assert str(macro) == "#define LOG(fmt, ...) printf(fmt, __VA_ARGS__)"

    def test_str_variadic_only(self) -> None:
        macro = MacroFunction(
            name="PRINTF",
            parameters=[],
            is_variadic=True,
            body="printf(__VA_ARGS__)",
        )
        assert str(macro) == "#define PRINTF(...) printf(__VA_ARGS__)"

    def test_str_statement_and_token_pasting(self) -> None:
        macro = MacroFunction(
            name="CONCAT",
            parameters=[MacroParameter("a"), MacroParameter("b")],
            is_variadic=False,
            body="a ## b",
            is_statement=False,
            has_token_pasting=True,
        )
        assert macro.has_token_pasting is True
        assert str(macro) == "#define CONCAT(a, b) a ## b"


class TestMacroFunctionJson:
    """Tests for round-trip JSON serialization and deserialization."""

    def test_round_trip_dict_full(self) -> None:
        loc = SourceLocation(file="macros.h", line=42, column=5)
        macro = MacroFunction(
            name="CAT",
            parameters=[MacroParameter("a"), MacroParameter("b")],
            is_variadic=False,
            body="a ## b",
            is_statement=False,
            has_token_pasting=True,
            namespace="util",
            location=loc,
        )
        header = Header(path="macros.h", declarations=[macro])

        d = header_to_json_dict(header)
        assert d == {
            "path": "macros.h",
            "declarations": [
                {
                    "kind": "macro_function",
                    "name": "CAT",
                    "parameters": [
                        {"name": "a"},
                        {"name": "b"},
                    ],
                    "body": "a ## b",
                    "has_token_pasting": True,
                    "namespace": "util",
                    "location": {"file": "macros.h", "line": 42, "column": 5},
                }
            ],
        }

        reconstructed = json_to_header(d)
        assert reconstructed == header

    def test_round_trip_json_str_variadic_statement(self) -> None:
        macro = MacroFunction(
            name="DEBUG_LOG",
            parameters=[MacroParameter("fmt"), MacroParameter("rest", is_variadic=True)],
            is_variadic=True,
            body="do { fprintf(stderr, fmt, rest); } while (0)",
            is_statement=True,
            has_token_pasting=False,
            namespace=None,
            location=None,
        )
        header = Header(path="debug.h", declarations=[macro])

        s = header_to_json(header)
        parsed = json.loads(s)
        assert parsed == {
            "path": "debug.h",
            "declarations": [
                {
                    "kind": "macro_function",
                    "name": "DEBUG_LOG",
                    "parameters": [
                        {"name": "fmt"},
                        {"name": "rest", "is_variadic": True},
                    ],
                    "body": "do { fprintf(stderr, fmt, rest); } while (0)",
                    "is_variadic": True,
                    "is_statement": True,
                }
            ],
        }

        reconstructed = json_to_header(s)
        assert reconstructed == header

    def test_round_trip_minimal_macro(self) -> None:
        macro = MacroFunction(name="NOOP")
        header = Header(path="noop.h", declarations=[macro])

        d = header_to_json_dict(header)
        assert d == {
            "path": "noop.h",
            "declarations": [
                {
                    "kind": "macro_function",
                    "name": "NOOP",
                    "parameters": [],
                    "body": "",
                }
            ],
        }

        reconstructed = json_to_header(d)
        assert reconstructed == header

    def test_deserialization_missing_name_raises_key_error(self) -> None:
        malformed_decl = {
            "path": "bad.h",
            "declarations": [
                {
                    "kind": "macro_function",
                    "parameters": [],
                    "body": "foo",
                }
            ],
        }
        with pytest.raises(KeyError):
            json_to_header(malformed_decl)

    def test_deserialization_param_missing_name_raises_key_error(self) -> None:
        malformed_param = {
            "path": "bad.h",
            "declarations": [
                {
                    "kind": "macro_function",
                    "name": "FOO",
                    "parameters": [{"is_variadic": True}],
                }
            ],
        }
        with pytest.raises(KeyError):
            json_to_header(malformed_param)
