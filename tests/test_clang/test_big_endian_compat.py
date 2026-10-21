"""Tests for the 64-bit big-endian callback widening patch in headerkit._clang."""

from __future__ import annotations

import ctypes
import importlib
import sys
from typing import Any

import pytest

import headerkit._clang
from headerkit._clang import (
    _NEEDS_BIG_ENDIAN_CALLBACK_PATCH,
    _patch_big_endian_callbacks,
    get_cindex,
)


class TestBigEndianCallbackPatchFlag:
    """Tests for the _NEEDS_BIG_ENDIAN_CALLBACK_PATCH flag definition."""

    def test_flag_exists(self) -> None:
        assert hasattr(headerkit._clang, "_NEEDS_BIG_ENDIAN_CALLBACK_PATCH")
        expected = sys.byteorder == "big" and ctypes.sizeof(ctypes.c_void_p) == 8 and sys.version_info < (3, 14)
        assert expected == _NEEDS_BIG_ENDIAN_CALLBACK_PATCH


class TestPatchBigEndianCallbacksDirect:
    """Tests for applying _patch_big_endian_callbacks directly on vendored cindex modules."""

    @pytest.mark.parametrize("version", ["14", "18", "20", "23"])
    def test_patch_updates_callback_restype(self, version: str) -> None:
        cindex = importlib.import_module(f"headerkit._clang.v{version}.cindex")
        _patch_big_endian_callbacks(cindex)

        cursor_cb: Any = getattr(cindex, "cursor_visit_callback", None)
        callbacks = getattr(cindex, "callbacks", None)

        checked = False
        if cursor_cb is not None:
            assert cursor_cb._restype_ is ctypes.c_ssize_t
            checked = True

        fields_cb: Any = getattr(cindex, "fields_visit_callback", None)
        if fields_cb is not None:
            assert fields_cb._restype_ is ctypes.c_ssize_t
            checked = True

        if isinstance(callbacks, dict) and "cursor_visit" in callbacks:
            assert callbacks["cursor_visit"]._restype_ is ctypes.c_ssize_t
            assert callbacks["fields_visit"]._restype_ is ctypes.c_ssize_t
            checked = True

        assert checked, f"No callback mechanisms found in LLVM {version}"

    @pytest.mark.parametrize("version", ["14", "18", "20", "23"])
    def test_patch_updates_function_list_argtypes(self, version: str) -> None:
        cindex = importlib.import_module(f"headerkit._clang.v{version}.cindex")
        _patch_big_endian_callbacks(cindex)

        fn_list = getattr(cindex, "FUNCTION_LIST", getattr(cindex, "functionList", None))
        assert fn_list is not None

        fn_map = {entry[0]: entry for entry in fn_list}
        assert "clang_visitChildren" in fn_map
        visit_children_args = fn_map["clang_visitChildren"][1]
        assert visit_children_args[1]._restype_ is ctypes.c_ssize_t

        for name in ("clang_visitCXXBaseClasses", "clang_visitCXXMethods", "clang_Type_visitFields"):
            if name in fn_map:
                assert fn_map[name][1][1]._restype_ is ctypes.c_ssize_t


class TestMonkeyPatchIntegration:
    """Integration tests for get_cindex() when the patch is forced."""

    def test_patch_applied_when_forced(self) -> None:
        saved_cindex = headerkit._clang._cached_cindex
        saved_flag = headerkit._clang._NEEDS_BIG_ENDIAN_CALLBACK_PATCH
        try:
            headerkit._clang._cached_cindex = None
            headerkit._clang._NEEDS_BIG_ENDIAN_CALLBACK_PATCH = True
            cindex = get_cindex()
            cursor_cb: Any = getattr(cindex, "cursor_visit_callback", None) or getattr(cindex, "callbacks", {}).get(
                "cursor_visit"
            )
            assert cursor_cb is not None
            assert cursor_cb._restype_ is ctypes.c_ssize_t
        finally:
            headerkit._clang._NEEDS_BIG_ENDIAN_CALLBACK_PATCH = saved_flag
            headerkit._clang._cached_cindex = saved_cindex

    @pytest.mark.libclang
    def test_full_ast_traversal_with_patch(self) -> None:
        """Verify that libclang parses multiple declarations with patched callbacks."""
        from headerkit.backends import is_backend_available
        from headerkit.backends.libclang import LibclangBackend

        if not is_backend_available("libclang"):
            pytest.skip("libclang not available")

        saved_cindex = headerkit._clang._cached_cindex
        saved_flag = headerkit._clang._NEEDS_BIG_ENDIAN_CALLBACK_PATCH
        try:
            headerkit._clang._cached_cindex = None
            headerkit._clang._NEEDS_BIG_ENDIAN_CALLBACK_PATCH = True
            cindex = get_cindex()
            cursor_cb: Any = getattr(cindex, "cursor_visit_callback", None) or getattr(cindex, "callbacks", {}).get(
                "cursor_visit"
            )
            assert cursor_cb is not None and cursor_cb._restype_ is ctypes.c_ssize_t

            backend = LibclangBackend()
            header = backend.parse(
                "int first_var;\nint second_var;\nstruct MyRecord { int x; int y; };\n",
                "test.h",
            )
            names = [getattr(d, "name", "") for d in header.declarations]
            assert "first_var" in names
            assert "second_var" in names
            assert "MyRecord" in names
        finally:
            headerkit._clang._NEEDS_BIG_ENDIAN_CALLBACK_PATCH = saved_flag
            headerkit._clang._cached_cindex = saved_cindex
