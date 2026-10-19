from __future__ import annotations

from headerkit.ir import Header, MacroFunction, MacroParameter
from headerkit.writers.cffi import CffiWriter
from headerkit.writers.ctypes import CtypesWriter
from headerkit.writers.cython import CythonWriter


def test_macro_function_cython_cdef_extern() -> None:
    macro = MacroFunction(
        name="MAX",
        parameters=[MacroParameter("a"), MacroParameter("b")],
        body="((a) > (b) ? (a) : (b))",
    )
    unit = Header(path="math.h", declarations=[macro])
    writer = CythonWriter()
    out = writer.write(unit)
    assert "int MAX(int a, int b)" in out


def test_macro_function_cython_token_pasting_skipped() -> None:
    macro = MacroFunction(
        name="CONCAT",
        parameters=[MacroParameter("a"), MacroParameter("b")],
        has_token_pasting=True,
    )
    unit = Header(path="util.h", declarations=[macro])
    writer = CythonWriter()
    out = writer.write(unit)
    assert "CONCAT" in out
    assert "skipped" in out.lower()


def test_macro_function_ctypes_comment_stub() -> None:
    macro = MacroFunction(name="FOO", parameters=[MacroParameter("x")])
    unit = Header(path="util.h", declarations=[macro])
    writer = CtypesWriter()
    out = writer.write(unit)
    assert "# Macro function 'FOO(x)' skipped" in out


def test_macro_function_cffi_prototype() -> None:
    macro = MacroFunction(name="BAR", parameters=[MacroParameter("x")])
    unit = Header(path="util.h", declarations=[macro])
    writer = CffiWriter()
    out = writer.write(unit)
    assert "int BAR(int x);" in out


def test_macro_function_variadic_cython_and_cffi() -> None:
    macro = MacroFunction(
        name="LOG",
        parameters=[MacroParameter("fmt"), MacroParameter("__VA_ARGS__", is_variadic=True)],
        is_variadic=True,
    )
    unit = Header(path="log.h", declarations=[macro])
    out_cython = CythonWriter().write(unit)
    assert "int LOG(int fmt, ...)" in out_cython
    assert "__VA_ARGS__" not in out_cython

    out_cffi = CffiWriter().write(unit)
    assert "int LOG(int fmt, ...);" in out_cffi
    assert "__VA_ARGS__" not in out_cffi
