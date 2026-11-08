"""Tests for the PyPy compatibility monkey-patch in headerkit._clang."""

from __future__ import annotations

import ctypes
import importlib
import platform
import sys
import types
from typing import Any

import pytest

from headerkit._clang import _compat_c_interop_string


def _fresh_cindex(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import a private copy of the loaded bindings module for the duration of a test.

    The PyPy patches mutate the module object they are given, and re-importing
    by name returns the shared one from ``sys.modules``, so a test that patches
    it would leave every later test running against patched bindings. The
    shared module, and the parent package's reference to it, are restored
    afterwards.
    """
    import headerkit._clang
    from headerkit._clang import get_cindex

    shared = get_cindex()
    package = importlib.import_module(shared.__name__.rpartition(".")[0])
    monkeypatch.delitem(sys.modules, shared.__name__)
    monkeypatch.setattr(package, "cindex", shared)
    monkeypatch.setattr(headerkit._clang, "_cached_cindex", None)
    fresh = importlib.import_module(shared.__name__)
    assert fresh is not shared
    return fresh


class TestCompatCInteropStringInit:
    """Tests for _compat_c_interop_string construction and .value property."""

    def test_init_with_string(self) -> None:
        obj = _compat_c_interop_string("hello")
        assert obj.value == "hello"

    def test_init_with_bytes(self) -> None:
        obj = _compat_c_interop_string(b"hello")
        assert obj.value == "hello"

    def test_init_with_none(self) -> None:
        # None is coerced to empty bytes, so value is "" and a buffer is allocated
        obj = _compat_c_interop_string(None)
        assert obj.value == ""
        assert obj._buffer is not None, "Buffer must be allocated even for None input"

    def test_init_default(self) -> None:
        obj = _compat_c_interop_string()
        assert obj.value == ""


class TestCompatCInteropStringStr:
    """Tests for _compat_c_interop_string __str__ behavior."""

    def test_str(self) -> None:
        obj = _compat_c_interop_string("world")
        assert str(obj) == "world"

    def test_str_empty(self) -> None:
        obj = _compat_c_interop_string()
        assert str(obj) == ""


class TestCompatCInteropStringFromParam:
    """Tests for _compat_c_interop_string.from_param class method."""

    def test_from_param_string(self) -> None:
        result = _compat_c_interop_string.from_param("hello")
        assert isinstance(result, _compat_c_interop_string)
        assert result.value == "hello"

    def test_from_param_bytes(self) -> None:
        result = _compat_c_interop_string.from_param(b"hello")
        assert isinstance(result, _compat_c_interop_string)
        assert result.value == "hello"

    def test_from_param_none(self) -> None:
        result = _compat_c_interop_string.from_param(None)
        assert isinstance(result, _compat_c_interop_string)
        assert result.value == ""

    def test_from_param_invalid(self) -> None:
        with pytest.raises(TypeError, match="Cannot convert 'int'"):
            _compat_c_interop_string.from_param(123)  # type: ignore[arg-type]


class TestCompatCInteropStringToPython:
    """Tests for _compat_c_interop_string.to_python_string static method."""

    def test_to_python_string(self) -> None:
        # Documents the public API contract: to_python_string is a static method
        # that extracts the string value from a _compat_c_interop_string instance.
        obj = _compat_c_interop_string("result")
        assert _compat_c_interop_string.to_python_string(obj) == "result"

    def test_to_python_string_empty(self) -> None:
        obj = _compat_c_interop_string()
        assert _compat_c_interop_string.to_python_string(obj) == ""

    def test_to_python_string_errcheck_signature(self) -> None:
        # ctypes errcheck functions are called with (result, func, arguments)
        obj = _compat_c_interop_string("errcheck_result")
        assert _compat_c_interop_string.to_python_string(obj, None, ()) == "errcheck_result"

    def test_to_python_string_none(self) -> None:
        assert _compat_c_interop_string.to_python_string(None) is None
        assert _compat_c_interop_string.to_python_string(None, None, ()) is None

    def test_to_python_string_raw_pointer_int(self) -> None:
        import ctypes

        buf = ctypes.create_string_buffer(b"from_pointer")
        ptr = ctypes.cast(buf, ctypes.c_void_p).value
        assert _compat_c_interop_string.to_python_string(ptr) == "from_pointer"
        assert _compat_c_interop_string.to_python_string(0) is None


class TestCompatCInteropStringUnicode:
    """Tests for UTF-8 roundtrip with non-ASCII characters."""

    def test_utf8_roundtrip_accented(self) -> None:
        text = "caf\u00e9"
        obj = _compat_c_interop_string(text)
        assert obj.value == text

    def test_utf8_roundtrip_combining(self) -> None:
        # e followed by combining acute accent
        text = "cafe\u0301"
        obj = _compat_c_interop_string(text)
        assert obj.value == text

    def test_utf8_roundtrip_emoji(self) -> None:
        text = "hello \U0001f600"
        obj = _compat_c_interop_string(text)
        assert obj.value == text

    def test_utf8_roundtrip_cjk(self) -> None:
        text = "\u4f60\u597d\u4e16\u754c"
        obj = _compat_c_interop_string(text)
        assert obj.value == text


@pytest.mark.allow("subprocess")
class TestMonkeyPatchMechanism:
    """Tests for the _NEEDS_INTEROP_STRING_PATCH flag and monkey-patch logic."""

    def test_patch_flag_exists(self) -> None:
        import headerkit._clang

        assert hasattr(headerkit._clang, "_NEEDS_INTEROP_STRING_PATCH")
        expected = platform.python_implementation() != "CPython"
        assert expected == headerkit._clang._NEEDS_INTEROP_STRING_PATCH

    def test_patch_not_applied_on_cpython(self) -> None:
        """On CPython, c_interop_string should remain the original class."""
        if platform.python_implementation() != "CPython":
            pytest.skip("Only relevant on CPython")

        import headerkit._clang
        from headerkit._clang import get_cindex

        saved = headerkit._clang._cached_cindex
        try:
            headerkit._clang._cached_cindex = None
            cindex = get_cindex()
            assert cindex.c_interop_string is not _compat_c_interop_string
            assert cindex.c_interop_string.__name__ == "c_interop_string"
        finally:
            headerkit._clang._cached_cindex = saved

    def test_patch_applied_when_forced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _NEEDS_INTEROP_STRING_PATCH is forced True, the compat class is used."""
        import headerkit._clang
        from headerkit._clang import get_cindex

        shared = get_cindex()
        _fresh_cindex(monkeypatch)
        monkeypatch.setattr(headerkit._clang, "_NEEDS_INTEROP_STRING_PATCH", True)

        cindex = get_cindex()

        assert cindex is not shared
        assert cindex.c_interop_string is _compat_c_interop_string


class TestPyPyPatches:
    """Tests for PyPy struct-by-value and callback isolation patches."""

    def test_patch_pypy_struct_pass_by_value(self) -> None:
        from headerkit._clang import _patch_pypy_struct_pass_by_value, get_cindex

        cindex = get_cindex()
        _patch_pypy_struct_pass_by_value(cindex)

        # Verify Cursor.from_param returns an independent copy
        c = cindex.Cursor()
        c._kind_id = 2
        c.xdata = 0
        c.data[0] = 100
        c.data[1] = 1
        c.data[2] = 300

        c_param = cindex.Cursor.from_param(c)
        assert c_param is not c
        assert c_param._kind_id == c._kind_id
        assert c_param.data[0] == c.data[0]
        assert c_param.data[1] == c.data[1]
        assert c_param.data[2] == c.data[2]

        # Mutating c_param must not affect c
        c_param.data[1] = 0
        assert c.data[1] == 1


_CALLBACK_NAMES = ("cursor_visit", "fields_visit", "translation_unit_includes")


def _passthrough(visitor: object) -> object:
    """Stand-in for a CFUNCTYPE: hands back the visitor so the test can call it directly."""
    return visitor


def _bindings_module(shape: str) -> types.ModuleType:
    """A module shaped like the vendored bindings, with pass-through callback types.

    ``attributes`` is the LLVM 20+ layout (``cursor_visit_callback`` and so on);
    ``dict`` is the LLVM 14-19 layout (``callbacks["cursor_visit"]`` and so on).
    The real ``Cursor`` and ``c_object_p`` are used, so the copies are real copies.
    """
    from headerkit._clang import get_cindex

    cindex = get_cindex()
    module = types.ModuleType(f"fake_cindex_{shape}")
    module.Cursor = cindex.Cursor  # type: ignore[attr-defined]
    module.c_object_p = cindex.c_object_p  # type: ignore[attr-defined]
    if shape == "attributes":
        for name in _CALLBACK_NAMES:
            setattr(module, f"{name}_callback", _passthrough)
    else:
        module.callbacks = dict.fromkeys(_CALLBACK_NAMES, _passthrough)  # type: ignore[attr-defined]
    return module


def _callback_type(module: types.ModuleType, name: str) -> Any:
    if hasattr(module, f"{name}_callback"):
        return getattr(module, f"{name}_callback")
    return module.callbacks[name]


@pytest.mark.parametrize("shape", ["attributes", "dict"])
class TestPyPyCallbackPatch:
    """``_patch_pypy_callbacks`` must hand every visitor a copy, in both binding layouts."""

    def test_cursor_visitor_receives_an_independent_copy(self, shape: str) -> None:
        from headerkit._clang import _patch_pypy_callbacks

        module = _bindings_module(shape)
        _patch_pypy_callbacks(module)
        received: list[Any] = []
        visitor = _callback_type(module, "cursor_visit")(lambda child, _parent, _data: received.append(child) or 1)

        child = module.Cursor()
        child._kind_id = 2
        child.data[0] = 100
        child.data[1] = 1
        child.data[2] = 300
        assert visitor(child, None, None) == 1

        [copy] = received
        assert copy is not child
        assert bytes(copy) == bytes(child)
        child.data[1] = 0
        assert copy.data[1] == 1

    def test_fields_visitor_receives_an_independent_copy(self, shape: str) -> None:
        from headerkit._clang import _patch_pypy_callbacks

        module = _bindings_module(shape)
        _patch_pypy_callbacks(module)
        received: list[Any] = []
        visitor = _callback_type(module, "fields_visit")(lambda field, _data: received.append(field) or 1)

        field = module.Cursor()
        field.data[1] = 7
        assert visitor(field, None) == 1

        [copy] = received
        assert copy is not field
        assert bytes(copy) == bytes(field)
        field.data[1] = 0
        assert copy.data[1] == 7

    def test_includes_visitor_receives_a_fresh_pointer_to_the_same_file(self, shape: str) -> None:
        from headerkit._clang import _patch_pypy_callbacks

        module = _bindings_module(shape)
        _patch_pypy_callbacks(module)
        received: list[Any] = []
        visitor = _callback_type(module, "translation_unit_includes")(
            lambda fobj, _lptr, depth, _data: received.append((fobj, depth))
        )

        fobj = ctypes.cast(0x1234, module.c_object_p)
        visitor(fobj, None, 3, None)

        [(copy, depth)] = received
        assert copy is not fobj
        assert ctypes.cast(copy, ctypes.c_void_p).value == 0x1234
        assert depth == 3


class TestPyPyCallbackPatchOnLoadedBindings:
    """The patch must reach the callback types of whichever bindings are actually loaded."""

    def test_every_callback_type_is_wrapped_and_keeps_its_ctypes_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from headerkit._clang import _patch_pypy_callbacks

        cindex = _fresh_cindex(monkeypatch)
        original = {name: _callback_type(cindex, name) for name in _CALLBACK_NAMES}

        _patch_pypy_callbacks(cindex)

        visitors = {
            "cursor_visit": lambda _child, _parent, _data: 1,
            "fields_visit": lambda _field, _data: 1,
            "translation_unit_includes": lambda _fobj, _lptr, _depth, _data: None,
        }
        for name, orig_type in original.items():
            patched = _callback_type(cindex, name)
            assert patched is not orig_type, f"{name} was left unpatched"
            # libclang's registered argtypes name the original CFUNCTYPE, so the
            # wrapper must still produce an instance of it.
            assert isinstance(patched(visitors[name]), orig_type)
