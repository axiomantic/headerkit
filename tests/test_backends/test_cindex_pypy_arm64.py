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

    def test_llvm14_anonymous_typedef_struct_union_enum_cffi_roundtrip(self, tmp_path) -> None:
        """Verify tag-less typedef struct/union/enum on LLVM 14 (empty spelling, is_anonymous=True)."""
        import cffi

        from headerkit.backends.libclang import LibclangBackend, _get_cindex
        from headerkit.ir import CType, Enum, EnumValue, Field, Function, Header, Parameter, Pointer, Struct
        from headerkit.writers.cffi import header_to_cffi

        lc = LibclangBackend()
        if not lc.is_available():
            pytest.skip("libclang not available")

        cindex = _get_cindex()
        code = """\
typedef int nng_err;
typedef struct {
    short num_task_threads;
    short max_task_threads;
    int num_expire_threads;
} nng_init_params;
nng_err nng_init(const nng_init_params *params);
typedef union {
    int i;
    float f;
} nng_val_u;
typedef enum {
    NNG_PIPE_EV_ADD_PRE,
    NNG_PIPE_EV_ADD_POST,
} nng_pipe_ev;
void nng_pipe_notify(nng_pipe_ev ev, const nng_val_u *val);
"""

        orig_spelling = cindex.Cursor.spelling.fget
        orig_is_anon = cindex.Cursor.is_anonymous
        orig_type_spelling = cindex.Type.spelling.fget

        tag_kinds = (
            cindex.CursorKind.STRUCT_DECL,
            cindex.CursorKind.UNION_DECL,
            cindex.CursorKind.ENUM_DECL,
        )
        anon_tag_names = {"nng_init_params", "nng_val_u", "nng_pipe_ev", "wrapper", "other"}

        def llvm14_spelling(cursor_self):
            s = orig_spelling(cursor_self)
            if cursor_self.kind in tag_kinds and s in anon_tag_names:
                return ""
            return s

        def llvm14_is_anon(cursor_self):
            s = orig_spelling(cursor_self)
            if cursor_self.kind in tag_kinds and s in anon_tag_names:
                return True
            return orig_is_anon(cursor_self)

        def llvm14_type_spelling(type_self):
            s = orig_type_spelling(type_self)
            if type_self.kind == cindex.TypeKind.ENUM and s == "nng_pipe_ev":
                return "enum (unnamed at nng.h:12:9)"
            return s

        cindex.Cursor.spelling = property(llvm14_spelling)
        cindex.Cursor.is_anonymous = llvm14_is_anon
        cindex.Type.spelling = property(llvm14_type_spelling)
        try:
            header = lc.parse(code, "nng.h")
            nested_code = (
                "typedef struct { struct { int x; int y; } pt; } wrapper;\n"
                "typedef struct { struct { int u; int v; } pt; } other;\n"
            )
            nested_header = lc.parse(nested_code, "nested.h")

            inc_file = tmp_path / "nng_types.h"
            main_file = tmp_path / "nng_main.h"
            inc_file.write_text(
                "typedef struct { int threads; } nng_init_params;\n",
                encoding="utf-8",
            )
            main_file.write_text(
                '#include "nng_types.h"\nint nng_init(const nng_init_params *params);\n',
                encoding="utf-8",
            )
            dep_header = lc.parse(
                main_file.read_text(encoding="utf-8"),
                str(main_file),
                include_dirs=[str(tmp_path)],
                recursive_includes=False,
            )
        finally:
            cindex.Cursor.spelling = property(orig_spelling)
            cindex.Cursor.is_anonymous = orig_is_anon
            cindex.Type.spelling = property(orig_type_spelling)

        structs = {d.name: d for d in header.declarations if isinstance(d, Struct)}
        enums = {d.name: d for d in header.declarations if isinstance(d, Enum)}
        assert [(f.name, str(f.type)) for f in structs["nng_init_params"].fields] == [
            ("num_task_threads", "short"),
            ("max_task_threads", "short"),
            ("num_expire_threads", "int"),
        ]
        assert structs["nng_init_params"].is_typedef is True
        assert structs["nng_init_params"].is_union is False
        assert [(f.name, str(f.type)) for f in structs["nng_val_u"].fields] == [
            ("i", "int"),
            ("f", "float"),
        ]
        assert structs["nng_val_u"].is_typedef is True
        assert structs["nng_val_u"].is_union is True
        assert enums["nng_pipe_ev"].is_typedef is True
        assert enums["nng_pipe_ev"].values == [
            EnumValue(name="NNG_PIPE_EV_ADD_PRE", value=0),
            EnumValue(name="NNG_PIPE_EV_ADD_POST", value=1),
        ]

        expected_cdef = (
            "typedef int nng_err;\n"
            "typedef struct {\n"
            "    short num_task_threads;\n"
            "    short max_task_threads;\n"
            "    int num_expire_threads;\n"
            "} nng_init_params;\n"
            "nng_err nng_init(const nng_init_params * params);\n"
            "typedef union {\n"
            "    int i;\n"
            "    float f;\n"
            "} nng_val_u;\n"
            "typedef enum {\n"
            "    NNG_PIPE_EV_ADD_PRE = 0,\n"
            "    NNG_PIPE_EV_ADD_POST = 1,\n"
            "} nng_pipe_ev;\n"
            "void nng_pipe_notify(nng_pipe_ev ev, const nng_val_u * val);"
        )
        cdef_out = header_to_cffi(header)
        assert cdef_out == expected_cdef
        ffi = cffi.FFI()
        ffi.cdef(cdef_out)

        nested_names = [(d.name, d.is_typedef) for d in nested_header.declarations if isinstance(d, Struct)]
        assert nested_names == [
            ("_wrapper_pt_s", False),
            ("wrapper", True),
            ("_other_pt_s", False),
            ("other", True),
        ]

        dep_cdef = header_to_cffi(dep_header)
        assert dep_cdef == (
            "typedef struct {\n    int threads;\n} nng_init_params;\nint nng_init(const nng_init_params * params);"
        )
        cffi.FFI().cdef(dep_cdef)

        # Negative control: if Struct remains unnamed (name=None, is_typedef=False),
        # CffiWriter drops it and cffi.FFI().cdef raises CDefError on nng_init.
        broken_header = Header(
            path="broken.h",
            declarations=[
                Struct(
                    name=None,
                    fields=[Field(name="threads", type=CType("int"))],
                    is_typedef=False,
                ),
                Function(
                    name="nng_init",
                    return_type=CType("int"),
                    parameters=[
                        Parameter(
                            name="params",
                            type=Pointer(CType("nng_init_params", qualifiers=["const"])),
                        )
                    ],
                ),
            ],
        )
        broken_cdef = header_to_cffi(broken_header)
        assert broken_cdef == "int nng_init(const nng_init_params * params);"
        with pytest.raises(cffi.CDefError):
            cffi.FFI().cdef(broken_cdef)
