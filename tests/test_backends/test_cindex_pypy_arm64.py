"""Tests for PyPy and ARM64 AAPCS64 compatibility in vendored cindex.py modules."""

from __future__ import annotations

import ctypes
import glob
import os

import pytest

import headerkit._clang
from headerkit._clang import VENDORED_VERSIONS, _compat_c_interop_string, get_cindex


class TestVendoredCindexPyPyArm64Fixes:
    """Verify all vendored cindex.py versions include PyPy & ARM64 AAPCS64 fixes."""

    @pytest.mark.parametrize("version", VENDORED_VERSIONS)
    def test_vendored_cindex_source_contains_buffer_copies_and_compiles(self, version: str) -> None:
        clang_dir = os.path.dirname(os.path.abspath(headerkit._clang.__file__))
        cindex_path = os.path.join(clang_dir, f"v{version}", "cindex.py")
        assert os.path.isfile(cindex_path)

        with open(cindex_path, encoding="utf-8") as f:
            source = f.read()

        compile(source, cindex_path, "exec")

        assert "from headerkit._clang import _NEEDS_INTEROP_STRING_PATCH, _compat_c_interop_string" in source
        assert "child = Cursor.from_buffer_copy(child); child._tu = self._tu" in source
        assert "field = Cursor.from_buffer_copy(field); field._tu = self._tu" in source
        assert "loc = SourceLocation.from_buffer_copy(lptr.contents)" in source
        assert "File(c_object_p.from_buffer_copy(fobj))" in source
        assert "conf.lib.clang_equalCursors(Cursor.from_buffer_copy(self), Cursor.from_buffer_copy(other))" in source
        if int(version) >= 20:
            assert "base = Cursor.from_buffer_copy(base); base._tu = self._tu" in source
        if int(version) >= 21:
            assert "method = Cursor.from_buffer_copy(method); method._tu = self._tu" in source

    def test_all_vendored_cindex_files_covered(self) -> None:
        clang_dir = os.path.dirname(os.path.abspath(headerkit._clang.__file__))
        cindex_files = sorted(glob.glob(os.path.join(clang_dir, "v*", "cindex.py")))
        assert len(cindex_files) == len(VENDORED_VERSIONS)

    def test_cursor_eq_passes_independent_buffer_copies(self) -> None:
        """Simulate clang_equalCursors mutating X.data[1] = 0 in place (ARM64 AAPCS64)."""
        cindex = get_cindex()
        c1 = cindex.Cursor()
        c1._kind_id = 5
        c1.xdata = 0
        c1.data[0] = 42
        c1.data[1] = 1
        c1.data[2] = 99

        c2 = cindex.Cursor()
        c2._kind_id = 5
        c2.xdata = 0
        c2.data[0] = 42
        c2.data[1] = 1
        c2.data[2] = 99

        copy1 = cindex.Cursor.from_buffer_copy(c1)
        copy2 = cindex.Cursor.from_buffer_copy(c2)
        # Mutate the copies in place the way clang_equalCursors does in C
        copy1.data[1] = 0
        copy2.data[1] = 0

        assert c1.data[1] == 1
        assert c2.data[1] == 1

    def test_compat_c_interop_string_null_void_p_returns_none(self) -> None:
        """Negative control: a NULL c_void_p or object with .value=None returns None."""
        null_ptr = ctypes.c_void_p(None)
        assert _compat_c_interop_string.to_python_string(null_ptr) is None

        class _NullHolder:
            value = None

        assert _compat_c_interop_string.to_python_string(_NullHolder()) is None


class TestBackendCapabilitiesAndIrPrecision:
    """Verify ParserBackend capability properties and IR precision across libclang and tree-sitter."""

    def test_backend_capability_properties(self) -> None:
        from headerkit.backends.libclang import LibclangBackend
        from headerkit.backends.treesitter import TreeSitterBackend

        lc = LibclangBackend()
        ts = TreeSitterBackend()

        assert lc.follows_includes is True
        assert lc.supports_compiler_args is True
        assert ts.follows_includes is False
        assert ts.supports_compiler_args is False

    def test_libclang_and_treesitter_function_pointer_stars_parity(self) -> None:
        from headerkit.backends.libclang import LibclangBackend
        from headerkit.backends.treesitter import TreeSitterBackend
        from headerkit.ir import FunctionPointer, Pointer, Typedef

        code = """
        typedef int (fn_zero_t)(int a);
        typedef int (*fn_one_t)(int a);
        typedef int (**fn_two_t)(int a);
        """
        ts = TreeSitterBackend()
        ts_header = ts.parse(code, "fp.h")
        ts_tds = {d.name: d.underlying_type for d in ts_header.declarations if isinstance(d, Typedef)}

        assert isinstance(ts_tds["fn_zero_t"], FunctionPointer)
        assert ts_tds["fn_zero_t"].is_pointer is False

        assert isinstance(ts_tds["fn_one_t"], FunctionPointer)
        assert ts_tds["fn_one_t"].is_pointer is True

        assert isinstance(ts_tds["fn_two_t"], Pointer)
        assert isinstance(ts_tds["fn_two_t"].pointee, FunctionPointer)
        assert ts_tds["fn_two_t"].pointee.is_pointer is True

        lc = LibclangBackend()
        if lc.is_available():
            lc_header = lc.parse(code, "fp.h")
            lc_tds = {d.name: d.underlying_type for d in lc_header.declarations if isinstance(d, Typedef)}
            assert isinstance(lc_tds["fn_zero_t"], FunctionPointer)
            assert lc_tds["fn_zero_t"].is_pointer is False
            assert str(lc_tds["fn_zero_t"]) == str(ts_tds["fn_zero_t"])

            assert isinstance(lc_tds["fn_one_t"], FunctionPointer)
            assert lc_tds["fn_one_t"].is_pointer is True
            assert str(lc_tds["fn_one_t"]) == str(ts_tds["fn_one_t"])

            assert isinstance(lc_tds["fn_two_t"], Pointer)
            assert isinstance(lc_tds["fn_two_t"].pointee, FunctionPointer)
            assert lc_tds["fn_two_t"].pointee.is_pointer is True
            assert str(lc_tds["fn_two_t"]) == str(ts_tds["fn_two_t"])

    def test_libclang_static_function_and_recursive_included_headers(self, tmp_path) -> None:
        from headerkit.backends.libclang import LibclangBackend
        from headerkit.ir import Function

        lc = LibclangBackend()
        if not lc.is_available():
            pytest.skip("libclang not available")

        sub = tmp_path / "sub_inner.h"
        mid = tmp_path / "mid.h"
        top = tmp_path / "top.h"
        sub.write_text("static inline int inner_static(int x) { return x + 1; }\n", encoding="utf-8")
        mid.write_text('#include "sub_inner.h"\nint mid_exported(int y);\n', encoding="utf-8")
        top.write_text('#include "mid.h"\n', encoding="utf-8")

        header = lc.parse(
            top.read_text(encoding="utf-8"),
            str(top),
            include_dirs=[str(tmp_path)],
            recursive_includes=True,
            project_prefixes=(str(tmp_path),),
        )
        # Transitive included_headers must include both mid.h and sub_inner.h
        resolved_includes = {os.path.realpath(p) for p in header.included_headers}
        assert os.path.realpath(str(mid)) in resolved_includes
        assert os.path.realpath(str(sub)) in resolved_includes

        funcs = {d.name: d for d in header.declarations if isinstance(d, Function)}
        assert funcs["inner_static"].is_static is True
        assert funcs["mid_exported"].is_static is False
