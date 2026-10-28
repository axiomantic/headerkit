"""Vendored clang Python bindings with version-aware loader.

This package vendors clang cindex.py from multiple LLVM release tags
and selects the correct version based on the system's installed LLVM.

Usage::

    from headerkit._clang import get_cindex

    cindex = get_cindex()
    # cindex is the appropriate cindex module for your system LLVM
"""

import ctypes
import importlib
import platform
import sys
import warnings
from ctypes import c_void_p
from types import ModuleType

_NEEDS_INTEROP_STRING_PATCH = platform.python_implementation() != "CPython"

_NEEDS_BIG_ENDIAN_CALLBACK_PATCH = sys.byteorder == "big" and ctypes.sizeof(ctypes.c_void_p) == 8

_cached_cindex: ModuleType | None = None


def _patch_pypy_simpletype() -> None:
    """Work around PyPy issue with subclassing primitive ctypes types.

    In PyPy, _ctypes.primitive.SimpleType.__new__ attempts to create endian-swapped
    variants (_be or _le) for any primitive subclass whose name does not end in '_p'.
    When creating the swapped variant, it recursively invokes self.__new__ passing
    the exact same class namespace dictionary `dct`. If the class definition used
    `super()` or referred to its own name, `dct` contains `__classcell__`.
    Calling `type.__new__` a second time with the already-bound `__classcell__`
    raises `TypeError: __class__ set to ... defining ... as ...`.

    Stripping `__classcell__` from `dct` when creating endian-swapped variants
    allows PyPy to define the class cleanly.
    """
    try:
        import _ctypes.primitive  # type: ignore[import-not-found]

        orig_new = _ctypes.primitive.SimpleType.__new__

        if getattr(orig_new, "_headerkit_patched", False):
            return

        def patched_new(
            self: object,
            name: str,
            bases: tuple[object, ...],
            dct: dict[str, object],
        ) -> object:
            if name.endswith(("_be", "_le")) and "__classcell__" in dct:
                clean_dct = dict(dct)
                del clean_dct["__classcell__"]
                return orig_new(self, name, bases, clean_dct)
            return orig_new(self, name, bases, dct)

        patched_new._headerkit_patched = True  # type: ignore[attr-defined]
        _ctypes.primitive.SimpleType.__new__ = patched_new
    except (ImportError, AttributeError):
        pass


if _NEEDS_INTEROP_STRING_PATCH:
    _patch_pypy_simpletype()


def _patch_pypy_callbacks(cindex: ModuleType) -> None:
    """Work around PyPy transient memory in ctypes Structure callback arguments.

    In PyPy, Structure instances passed as arguments into CFUNCTYPE callbacks
    point to transient stack-allocated memory that is reused across callback
    invocations. Because cindex AST visitors collect cursors into Python lists
    (e.g. `children.append(child)` in Cursor.get_children()), the collected
    cursors would all point to the same corrupted memory once the callback
    unwinds.

    Wrapping cursor_visit_callback, fields_visit_callback, and
    translation_unit_includes_callback to create independent copies of
    incoming structs and pointer objects before passing them to the visitor
    function ensures that AST traversal and inclusion gathering retain valid
    data across iterations.
    """
    from typing import Any

    if hasattr(cindex, "cursor_visit_callback"):
        orig_cursor_cb = cindex.cursor_visit_callback

        def cursor_cb_wrapper(visitor: Any) -> Any:
            def copying_visitor(child: Any, parent: Any, data: Any) -> Any:
                child_copy = cindex.Cursor()
                ctypes.memmove(
                    ctypes.byref(child_copy),
                    ctypes.byref(child),
                    ctypes.sizeof(child),
                )
                return visitor(child_copy, parent, data)

            return orig_cursor_cb(copying_visitor)

        cindex.cursor_visit_callback = cursor_cb_wrapper  # type: ignore[attr-defined]

    if hasattr(cindex, "fields_visit_callback"):
        orig_fields_cb = cindex.fields_visit_callback

        def fields_cb_wrapper(visitor: Any) -> Any:
            def copying_visitor(field: Any, data: Any) -> Any:
                field_copy = cindex.Cursor()
                ctypes.memmove(
                    ctypes.byref(field_copy),
                    ctypes.byref(field),
                    ctypes.sizeof(field),
                )
                return visitor(field_copy, data)

            return orig_fields_cb(copying_visitor)

        cindex.fields_visit_callback = fields_cb_wrapper  # type: ignore[attr-defined]

    if hasattr(cindex, "translation_unit_includes_callback"):
        orig_includes_cb = cindex.translation_unit_includes_callback

        def includes_cb_wrapper(visitor: Any) -> Any:
            def copying_visitor(fobj: Any, lptr: Any, depth: Any, data: Any) -> Any:
                raw_ptr = ctypes.cast(fobj, ctypes.c_void_p).value
                fobj_copy = ctypes.cast(raw_ptr or 0, cindex.c_object_p)
                return visitor(fobj_copy, lptr, depth, data)

            return orig_includes_cb(copying_visitor)

        cindex.translation_unit_includes_callback = includes_cb_wrapper  # type: ignore[attr-defined]


def _patch_pypy_struct_pass_by_value(cindex: ModuleType) -> None:
    """Ensure structs passed by value to libclang are copied on PyPy.

    In the C ABI, struct arguments passed by value belong to the callee,
    which is permitted to mutate its local parameters in place. For instance,
    clang_equalCursors(CXCursor X, CXCursor Y) zeroes out the HadErrors flag
    at offset 16 in X and Y during cursor comparison.

    CPython's ctypes creates a temporary copy on the call frame when passing
    structs by value. PyPy's _ctypes implementation, however, passes the object's
    internal buffer directly (see PyPy structure.py: _to_ffi_param). As a result,
    calling clang_equalCursors (invoked by cursor.is_null()) in-place mutates
    cursor.data[1] from 1 to 0 on PyPy, causing subsequent calls to
    cursor.is_definition() to return False.

    Overriding from_param on cindex Structure types to return an independent copy
    preserves the caller's struct state across native function calls.
    """
    from typing import Any

    for name in ("Cursor", "Type", "SourceLocation", "SourceRange", "Token"):
        cls = getattr(cindex, name, None)
        if cls is None:
            continue
        orig_from_param = cls.from_param

        def _make_copying_from_param(target_cls: Any, orig_fp: Any) -> Any:
            def pypy_from_param(_cls: Any, val: Any) -> Any:
                obj = orig_fp(val)
                if isinstance(obj, target_cls):
                    new_obj = target_cls()
                    ctypes.memmove(
                        ctypes.byref(new_obj),
                        ctypes.byref(obj),
                        ctypes.sizeof(obj),
                    )
                    if hasattr(obj, "_tu"):
                        new_obj._tu = obj._tu
                    return new_obj
                return obj

            return classmethod(pypy_from_param)

        cls.from_param = _make_copying_from_param(cls, orig_from_param)


def _patch_big_endian_callbacks(cindex: ModuleType) -> None:
    """Work around CPython issue gh-156933 on 64-bit big-endian architectures (e.g. s390x).

    On 64-bit big-endian architectures, ctypes.CFUNCTYPE callbacks returning 32-bit
    integers (such as c_int) fail to widen narrow return values per the libffi contract.
    Writing 4 bytes to an 8-byte buffer writes into the high-order bits on big-endian
    machines, leaving the low-order bits as 0 or stack garbage.
    In the s390x ELF ABI, 32-bit return values are evaluated in the low 32 bits of register
    r2. When clang_visitChildren evaluates CXChildVisitResult, it reads 0
    (CXChildVisit_Break), immediately aborting AST traversal on the very first node.

    Widening the visitor callbacks' return type to ctypes.c_ssize_t (64-bit integer)
    ensures ctypes writes all 8 bytes, correctly placing the return value in the low 32
    bits of r2 for native callers across both endiannesses.
    """
    new_cursor_cb = ctypes.CFUNCTYPE(ctypes.c_ssize_t, cindex.Cursor, cindex.Cursor, ctypes.py_object)
    new_fields_cb = ctypes.CFUNCTYPE(ctypes.c_ssize_t, cindex.Cursor, ctypes.py_object)

    if hasattr(cindex, "cursor_visit_callback"):
        cindex.cursor_visit_callback = new_cursor_cb  # type: ignore[attr-defined]
    if hasattr(cindex, "fields_visit_callback"):
        cindex.fields_visit_callback = new_fields_cb  # type: ignore[attr-defined]

    callbacks = getattr(cindex, "callbacks", None)
    if isinstance(callbacks, dict):
        if "cursor_visit" in callbacks:
            callbacks["cursor_visit"] = new_cursor_cb
        if "fields_visit" in callbacks:
            callbacks["fields_visit"] = new_fields_cb

    fn_list = getattr(cindex, "FUNCTION_LIST", getattr(cindex, "functionList", None))
    if fn_list is not None:
        for idx, entry in enumerate(fn_list):
            name = entry[0]
            if name == "clang_visitChildren":
                fn_list[idx] = (name, [cindex.Cursor, new_cursor_cb, ctypes.py_object], entry[2])
            elif name in ("clang_visitCXXBaseClasses", "clang_visitCXXMethods", "clang_Type_visitFields"):
                fn_list[idx] = (name, [cindex.Type, new_fields_cb, ctypes.py_object], entry[2])

    config_cls = getattr(cindex, "Config", None)
    if config_cls is not None and getattr(config_cls, "loaded", False) and hasattr(cindex, "conf"):
        try:
            lib = cindex.conf.lib
            for attr, cb, target_type in [
                ("clang_visitChildren", new_cursor_cb, cindex.Cursor),
                ("clang_visitCXXBaseClasses", new_fields_cb, cindex.Type),
                ("clang_visitCXXMethods", new_fields_cb, cindex.Type),
                ("clang_Type_visitFields", new_fields_cb, cindex.Type),
            ]:
                if hasattr(lib, attr):
                    getattr(lib, attr).argtypes = [target_type, cb, ctypes.py_object]
        except Exception:
            pass


VENDORED_VERSIONS = ("14", "15", "16", "17", "18", "19", "20", "21", "22", "23")
LATEST_VENDORED = "23"
OLDEST_VENDORED = "14"

_DESTRUCTOR_CLASS_NAMES = (
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
)


def _patch_shutdown_safe_destructors(cindex: ModuleType) -> None:
    """Guard vendored cindex.__del__ methods against interpreter-shutdown None globals.

    During Python interpreter finalization, module-level globals on the vendored
    ``cindex`` module (specifically ``cindex.conf``) may be set to ``None`` before
    cached ``Index`` or ``TranslationUnit`` instances are garbage-collected.
    Wrapping ``__del__`` on the vendored wrapper classes in-memory avoids mutating
    the vendored upstream files while preventing shutdown ``AttributeError`` tracebacks.
    """
    from typing import Any

    for cls_name in _DESTRUCTOR_CLASS_NAMES:
        cls = getattr(cindex, cls_name, None)
        if cls is None:
            continue
        orig_del = getattr(cls, "__del__", None)
        if orig_del is None or getattr(orig_del, "_headerkit_shutdown_patched", False):
            continue

        def _make_safe_del(fn: Any) -> Any:
            def _safe_del(self: Any) -> None:
                conf = getattr(cindex, "conf", None)
                if conf is None or getattr(conf, "_lib", True) is None:
                    return
                try:
                    fn(self)
                except AttributeError:
                    if getattr(cindex, "conf", None) is None:
                        return
                    raise

            _safe_del._headerkit_shutdown_patched = True  # type: ignore[attr-defined]
            return _safe_del

        cls.__del__ = _make_safe_del(orig_del)


class _compat_c_interop_string(c_void_p):
    """PyPy-compatible replacement for c_interop_string.

    The vendored cindex.py defines c_interop_string as a subclass of
    ctypes.c_char_p, but PyPy does not support subclassing c_char_p
    with methods calling super(), and PyPy's _SimpleCData metaclass
    forbids closure variables (__class__) and custom property descriptors.
    This replacement uses c_void_p as the base, manages string buffer
    storage without conflicting with PyPy's internal _buffer attribute,
    and implements __getattribute__ to provide string decoding across both
    CPython and PyPy.
    """

    _str_buffer: ctypes.Array[ctypes.c_char] | None = None

    def __init__(self, p: str | bytes | None = None) -> None:
        if p is None:
            p = b""
        elif isinstance(p, str):
            p = p.encode("utf8")
        buf = ctypes.create_string_buffer(p)
        c_void_p.__init__(self, ctypes.cast(buf, c_void_p).value)
        self._str_buffer = buf  # prevent GC
        if platform.python_implementation() == "CPython":
            object.__setattr__(self, "_buffer", buf)

    def __str__(self) -> str:
        return self.value or ""

    def _get_raw_ptr(self) -> int | None:
        """Read the raw void pointer value from the c_void_p base."""
        val_attr = getattr(c_void_p, "value", None)
        if val_attr is not None and hasattr(val_attr, "__get__"):
            ptr: object = val_attr.__get__(self)
        else:
            ptr = getattr(self, "_as_parameter_", None)
        return ptr if isinstance(ptr, int) else None

    @property
    def value(self) -> str | None:  # type: ignore[override]
        ptr = self._get_raw_ptr()
        if ptr is None or ptr == 0:
            return None
        return ctypes.string_at(ptr).decode("utf8")

    def __getattribute__(self, name: str) -> object:
        if name == "value":
            ptr = self._get_raw_ptr()
            if ptr is None or ptr == 0:
                return None
            return ctypes.string_at(ptr).decode("utf8")
        return object.__getattribute__(self, name)

    @classmethod
    def from_param(cls, param: str | bytes | None) -> "_compat_c_interop_string":
        if isinstance(param, str):
            return cls(param)
        if isinstance(param, bytes):
            return cls(param)
        if param is None:
            return cls(param)
        raise TypeError(f"Cannot convert '{type(param).__name__}' to '{cls.__name__}'")

    @staticmethod
    def to_python_string(x: object, *_args: object) -> str | None:
        if x is None:
            return None
        if isinstance(x, str):
            return x
        if isinstance(x, bytes):
            return x.decode("utf8")
        if isinstance(x, int):
            if x == 0:
                return None
            return ctypes.string_at(x).decode("utf8")
        if hasattr(x, "value"):
            v = x.value
            if isinstance(v, bytes):
                return v.decode("utf8")
            if isinstance(v, str):
                return v
            if isinstance(v, int):
                if v == 0:
                    return None
                return ctypes.string_at(v).decode("utf8")
        return str(x)


def get_cindex() -> ModuleType:
    """Load the appropriate vendored cindex module for the system's LLVM version.

    Detection order:
    1. CIR_CLANG_VERSION env var (explicit override)
    2. llvm-config --version (most reliable)
    3. clang -dM -E to get __clang_major__ (works for Apple clang)
    4. Fallback to latest vendored version with warning

    Returns the cindex module ready for use. The result is cached.
    """
    global _cached_cindex
    if _cached_cindex is not None:
        return _cached_cindex

    from headerkit._clang._version import detect_llvm_version

    version = detect_llvm_version()

    if version is None:
        warnings.warn(
            f"Could not detect LLVM version. Using vendored cindex for LLVM {LATEST_VENDORED}. "
            f"Set CIR_CLANG_VERSION to override.",
            stacklevel=2,
        )
        version = LATEST_VENDORED

    if version not in VENDORED_VERSIONS:
        try:
            v_int = int(version)
        except ValueError:
            warnings.warn(f"Invalid LLVM version {version!r}, falling back to {LATEST_VENDORED}", stacklevel=2)
            version = LATEST_VENDORED
            v_int = int(version)
        oldest_int = int(OLDEST_VENDORED)
        latest_int = int(LATEST_VENDORED)

        if v_int < oldest_int:
            warnings.warn(
                f"LLVM {version} is older than oldest vendored version ({OLDEST_VENDORED}). "
                f"Using {OLDEST_VENDORED}. Set CIR_CLANG_VERSION to override.",
                stacklevel=2,
            )
            version = OLDEST_VENDORED
        elif v_int > latest_int:
            warnings.warn(
                f"LLVM {version} is newer than latest vendored version ({LATEST_VENDORED}). "
                f"Using {LATEST_VENDORED}. Set CIR_CLANG_VERSION to override.",
                stacklevel=2,
            )
            version = LATEST_VENDORED

    if _NEEDS_INTEROP_STRING_PATCH:
        _patch_pypy_simpletype()

    cindex = importlib.import_module(f"headerkit._clang.v{version}.cindex")
    _patch_shutdown_safe_destructors(cindex)

    if _NEEDS_BIG_ENDIAN_CALLBACK_PATCH:
        _patch_big_endian_callbacks(cindex)

    if _NEEDS_INTEROP_STRING_PATCH:
        cindex.c_interop_string = _compat_c_interop_string  # type: ignore[attr-defined]
        _patch_pypy_callbacks(cindex)
        _patch_pypy_struct_pass_by_value(cindex)

    _cached_cindex = cindex
    return cindex
