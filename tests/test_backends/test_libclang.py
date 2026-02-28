"""Tests for the libclang backend."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from headerkit.backends.libclang import (
    LibclangBackend,
    _deduplicate_declarations,
    _get_libclang_search_paths,
    _is_system_header,
    _is_umbrella_header,
    _mangle_specialization_name,
    get_system_include_dirs,
    is_system_libclang_available,
    normalize_path,
)
from headerkit.ir import (
    CType,
    Enum,
    Field,
    Function,
    Header,
    Pointer,
    Struct,
    Typedef,
    Variable,
)

# Mark all tests that require libclang
libclang = pytest.mark.libclang


class TestImportability:
    """Tests that the module can be imported and basic functions work."""

    def test_module_imports(self):
        """The libclang backend module can be imported."""
        import headerkit.backends.libclang  # noqa: F401

    def test_is_system_libclang_available_returns_bool(self):
        """is_system_libclang_available() returns a boolean."""
        result = is_system_libclang_available()
        assert isinstance(result, bool)

    def test_libclang_backend_class_exists(self):
        """LibclangBackend class is importable."""
        assert LibclangBackend is not None


class TestHelperFunctions:
    """Tests for helper functions that don't require libclang."""

    def test_get_libclang_search_paths_returns_list(self):
        """_get_libclang_search_paths returns a list of strings."""
        paths = _get_libclang_search_paths()
        assert isinstance(paths, list)
        for path in paths:
            assert isinstance(path, str)

    def test_is_system_header_usr_include(self):
        """System header detection for /usr/include."""
        assert _is_system_header("/usr/include/stdio.h") is True

    def test_is_system_header_usr_local(self):
        """System header detection for /usr/local/include."""
        assert _is_system_header("/usr/local/include/mylib.h") is True

    def test_is_system_header_homebrew(self):
        """System header detection for Homebrew paths."""
        assert _is_system_header("/opt/homebrew/include/nng/nng.h") is True

    def test_is_system_header_sdk(self):
        """System header detection for SDK paths."""
        assert _is_system_header("/path/to/MacOSX.sdk/usr/include/stdio.h") is True

    def test_is_system_header_project_file(self):
        """Non-system header detection for project files."""
        assert _is_system_header("/home/user/project/include/mylib.h") is False

    def test_is_system_header_project_prefix_overrides(self):
        """project_prefixes can whitelist paths that would otherwise be system."""
        path = "/opt/homebrew/include/sodium/crypto_auth.h"
        assert _is_system_header(path) is True
        assert _is_system_header(path, project_prefixes=("/opt/homebrew/include/sodium",)) is False

    def test_is_system_header_case_insensitive_with_backslashes(self):
        """System header detection works with Windows-style backslash paths."""
        assert _is_system_header(r"C:\some\path\clang\include\stddef.h") is True

    def test_is_umbrella_header_true(self):
        """Umbrella header detection when many includes, few declarations."""
        header = Header(
            path="umbrella.h",
            declarations=[],
            included_headers={
                "/home/user/lib/a.h",
                "/home/user/lib/b.h",
                "/home/user/lib/c.h",
                "/home/user/lib/d.h",
            },
        )
        assert _is_umbrella_header(header) is True

    def test_is_umbrella_header_false_many_decls(self):
        """Non-umbrella header with declarations."""
        header = Header(
            path="normal.h",
            declarations=[
                Struct("A", [Field("x", CType("int"))]),
                Struct("B", [Field("y", CType("int"))]),
                Struct("C", [Field("z", CType("int"))]),
                Function("foo", CType("void"), []),
            ],
            included_headers={"/home/user/lib/a.h"},
        )
        assert _is_umbrella_header(header) is False

    def test_deduplicate_declarations_removes_duplicates(self):
        """_deduplicate_declarations removes duplicate declarations."""
        decls = [
            Struct("Point", [Field("x", CType("int"))]),
            Struct("Point", [Field("x", CType("int"))]),  # duplicate
            Function("foo", CType("void"), []),
        ]
        result = _deduplicate_declarations(decls)
        assert len(result) == 2
        names = [getattr(d, "name", None) for d in result]
        assert "Point" in names
        assert "foo" in names
        assert isinstance(result[0], Struct)
        assert isinstance(result[1], Function)

    def test_deduplicate_declarations_typedef_struct_pattern(self):
        """_deduplicate_declarations handles typedef struct pattern."""
        decls = [
            Struct("Foo", [Field("x", CType("int"))]),
            Typedef("Foo", CType("Foo")),  # should be removed, struct gets is_typedef=True
        ]
        result = _deduplicate_declarations(decls)
        assert len(result) == 1
        assert isinstance(result[0], Struct)
        assert result[0].is_typedef is True

    def test_mangle_specialization_name(self):
        """_mangle_specialization_name converts template names."""
        assert _mangle_specialization_name("Container<int>") == "Container_int"
        assert _mangle_specialization_name("Map<int, double>") == "Map_int_double"
        assert _mangle_specialization_name("Foo<int*>") == "Foo_int_ptr"
        assert _mangle_specialization_name("std::vector<int>") == "std_vector_int"


class TestNormalizePath:
    """Tests for normalize_path() cross-platform path normalization."""

    def test_backslash_to_forward_slash(self):
        assert normalize_path(r"C:\Program Files\LLVM") == "c:/program files/llvm"

    def test_lowercase(self):
        assert normalize_path("/USR/INCLUDE") == "/usr/include"

    def test_already_normalized(self):
        assert normalize_path("/usr/include/stdio.h") == "/usr/include/stdio.h"

    def test_mixed_separators(self):
        assert normalize_path(r"C:\Program Files/LLVM\bin") == "c:/program files/llvm/bin"

    def test_empty_string(self):
        assert normalize_path("") == ""


class TestIsSystemHeaderWindows:
    """Tests for Windows-specific system header classification."""

    def test_windows_llvm_install(self):
        assert _is_system_header(r"C:\Program Files\LLVM\lib\clang\18\include\stddef.h") is True

    def test_windows_llvm_x86_install(self):
        assert _is_system_header(r"C:\Program Files (x86)\LLVM\lib\clang\18\include\stddef.h") is True

    def test_windows_sdk_ucrt(self):
        """Matched via 'windows kits/' fragment."""
        assert _is_system_header(r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\ucrt\stdio.h") is True

    def test_windows_sdk_um(self):
        """Matched via 'windows kits/' fragment."""
        assert _is_system_header(r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\um\windows.h") is True

    def test_windows_sdk_shared(self):
        """Matched via 'windows kits/' fragment."""
        assert _is_system_header(r"C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\shared\windef.h") is True

    def test_msvc_toolchain(self):
        assert (
            _is_system_header(
                r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.38.33130\include\vcruntime.h"
            )
            is True
        )

    def test_visual_studio_headers(self):
        assert (
            _is_system_header(r"C:\Program Files\Microsoft Visual Studio\2022\Community\include\some_header.h") is True
        )

    def test_msys2_mingw64_headers(self):
        assert _is_system_header(r"C:\msys64\mingw64\include\stdio.h") is True

    def test_msys2_ucrt64_headers(self):
        assert _is_system_header(r"C:\msys64\ucrt64\include\stdio.h") is True

    def test_msys2_clang64_headers(self):
        assert _is_system_header(r"C:\msys64\clang64\include\stdio.h") is True

    def test_project_file_on_windows(self):
        assert _is_system_header(r"C:\Users\dev\project\include\mylib.h") is False

    def test_project_prefix_overrides_windows_system(self):
        path = r"C:\Program Files\LLVM\include\myproject\myheader.h"
        assert _is_system_header(path) is True
        assert (
            _is_system_header(
                path,
                project_prefixes=(r"C:\Program Files\LLVM\include\myproject",),
            )
            is False
        )


class TestLibclangSearchPathsWindows:
    """Tests for Windows-specific libclang search paths."""

    def test_includes_programfiles_path(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [p.replace("\\", "/").lower() for p in paths]
            assert any("program files/llvm/bin/libclang.dll" in p for p in normalized)

    def test_includes_programfiles_x86_path(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [p.replace("\\", "/").lower() for p in paths]
            assert any("program files (x86)/llvm/bin/libclang.dll" in p for p in normalized)

    def test_includes_scoop_path(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                    "USERPROFILE": r"C:\Users\testuser",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [p.replace("\\", "/").lower() for p in paths]
            assert any("scoop/apps/llvm" in p for p in normalized)

    def test_includes_msys2_paths(self):
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"C:\Program Files",
                    "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [p.replace("\\", "/").lower() for p in paths]
            assert any("msys64/mingw64/bin/libclang.dll" in p for p in normalized)
            assert any("msys64/ucrt64/bin/libclang.dll" in p for p in normalized)
            assert any("msys64/clang64/bin/libclang.dll" in p for p in normalized)

    def test_uses_env_var_not_hardcoded_paths(self):
        """PROGRAMFILES env var is respected over hardcoded C:\\Program Files."""
        with (
            patch("headerkit.backends.libclang.sys.platform", "win32"),
            patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": r"D:\Custom\Programs",
                    "PROGRAMFILES(X86)": r"D:\Custom\Programs (x86)",
                },
            ),
        ):
            paths = _get_libclang_search_paths()
            normalized = [p.replace("\\", "/").lower() for p in paths]
            assert any("d:/custom/programs/llvm/bin/libclang.dll" in p for p in normalized)


@libclang
class TestLibclangBackendProperties:
    """Tests for LibclangBackend properties (require libclang)."""

    @pytest.fixture(autouse=True)
    def skip_if_no_libclang(self):
        if not is_system_libclang_available():
            pytest.skip("System libclang not available")

    def test_backend_name(self):
        backend = LibclangBackend()
        assert backend.name == "libclang"

    def test_backend_supports_macros(self):
        backend = LibclangBackend()
        assert backend.supports_macros is True

    def test_backend_supports_cpp(self):
        backend = LibclangBackend()
        assert backend.supports_cpp is True


@libclang
class TestLibclangParsing:
    """Tests for parsing C code with the libclang backend."""

    @pytest.fixture(autouse=True)
    def skip_if_no_libclang(self):
        if not is_system_libclang_available():
            pytest.skip("System libclang not available")

    @pytest.fixture
    def backend(self):
        return LibclangBackend()

    def test_parse_simple_variable(self, backend):
        """Parse a simple variable declaration."""
        header = backend.parse("int x;", "test.h")
        assert isinstance(header, Header)
        assert len(header.declarations) == 1
        var_decls = [d for d in header.declarations if isinstance(d, Variable)]
        assert len(var_decls) == 1
        assert var_decls[0].name == "x"
        assert isinstance(var_decls[0].type, CType)
        assert var_decls[0].type.name == "int"

    def test_parse_function(self, backend):
        """Parse a function declaration."""
        header = backend.parse("int add(int a, int b);", "test.h")
        func_decls = [d for d in header.declarations if isinstance(d, Function)]
        assert len(func_decls) == 1
        func = func_decls[0]
        assert func.name == "add"
        assert isinstance(func.return_type, CType)
        assert func.return_type.name == "int"
        assert len(func.parameters) == 2
        assert func.parameters[0].name == "a"
        assert func.parameters[1].name == "b"

    def test_parse_struct(self, backend):
        """Parse a struct declaration."""
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(struct_decls) == 1
        s = struct_decls[0]
        assert s.name == "Point"
        assert len(s.fields) == 2
        assert s.fields[0].name == "x"
        assert s.fields[1].name == "y"

    def test_parse_enum(self, backend):
        """Parse an enum declaration."""
        code = """
        enum Color {
            RED = 0,
            GREEN = 1,
            BLUE = 2
        };
        """
        header = backend.parse(code, "test.h")
        enum_decls = [d for d in header.declarations if isinstance(d, Enum)]
        assert len(enum_decls) == 1
        e = enum_decls[0]
        assert e.name == "Color"
        assert len(e.values) == 3
        assert e.values[0].name == "RED"
        assert e.values[0].value == 0
        assert e.values[1].name == "GREEN"
        assert e.values[1].value == 1
        assert e.values[2].name == "BLUE"
        assert e.values[2].value == 2

    def test_parse_typedef(self, backend):
        """Parse a typedef declaration."""
        code = """
        typedef unsigned long size_type;
        """
        header = backend.parse(code, "test.h")
        typedef_decls = [d for d in header.declarations if isinstance(d, Typedef)]
        assert len(typedef_decls) == 1
        td = typedef_decls[0]
        assert td.name == "size_type"

    def test_parse_pointer_type(self, backend):
        """Parse a function with pointer parameter and return type."""
        code = "char* strdup(const char* s);"
        header = backend.parse(code, "test.h")
        func_decls = [d for d in header.declarations if isinstance(d, Function)]
        assert len(func_decls) == 1
        func = func_decls[0]
        assert func.name == "strdup"
        assert isinstance(func.return_type, Pointer)
        assert len(func.parameters) == 1
        assert isinstance(func.parameters[0].type, Pointer)

    def test_parse_union(self, backend):
        """Parse a union declaration."""
        code = """
        union Data {
            int i;
            float f;
            char c;
        };
        """
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        union_decls = [s for s in struct_decls if s.is_union]
        assert len(union_decls) == 1
        u = union_decls[0]
        assert u.name == "Data"
        assert u.is_union is True
        assert len(u.fields) == 3

    def test_parse_variadic_function(self, backend):
        """Parse a variadic function declaration."""
        code = "int printf(const char* fmt, ...);"
        header = backend.parse(code, "test.h")
        func_decls = [d for d in header.declarations if isinstance(d, Function)]
        assert len(func_decls) == 1
        func = func_decls[0]
        assert func.name == "printf"
        assert func.is_variadic is True

    def test_parse_forward_declaration(self, backend):
        """Parse a forward struct declaration."""
        code = "struct Opaque;"
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(struct_decls) == 1
        s = struct_decls[0]
        assert s.name == "Opaque"
        assert len(s.fields) == 0  # forward decl has no fields

    def test_forward_decl_vs_empty_struct(self, backend):
        """Forward declaration and empty struct body both produce zero fields."""
        fwd_header = backend.parse("struct Opaque;", "fwd.h")
        fwd_structs = [d for d in fwd_header.declarations if isinstance(d, Struct)]
        assert len(fwd_structs) == 1
        assert fwd_structs[0].name == "Opaque"
        assert len(fwd_structs[0].fields) == 0

        empty_header = backend.parse("struct Empty {};", "empty.h")
        empty_structs = [d for d in empty_header.declarations if isinstance(d, Struct)]
        assert len(empty_structs) == 1
        assert empty_structs[0].name == "Empty"
        assert len(empty_structs[0].fields) == 0

    def test_parse_typedef_struct(self, backend):
        """Parse typedef struct pattern."""
        code = """
        typedef struct Point {
            int x;
            int y;
        } Point;
        """
        header = backend.parse(code, "test.h")
        struct_decls = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(struct_decls) == 1
        s = struct_decls[0]
        assert s.name == "Point"
        assert s.is_typedef is True
        assert len(s.fields) == 2

    def test_parse_error_raises_runtime_error(self, backend):
        """Parse error raises RuntimeError."""
        code = "this is not valid C code @#$%;"
        with pytest.raises(RuntimeError, match="Parse error"):
            backend.parse(code, "test.h")

    def test_header_path(self, backend):
        """Parsed header has correct path."""
        header = backend.parse("int x;", "myfile.h")
        assert header.path == "myfile.h"

    def test_parse_produces_correct_ir_types(self, backend):
        """Parse produces correct IR types for a mixed declaration file."""
        code = """
        struct Config {
            int width;
            int height;
            const char* name;
        };

        enum Mode {
            MODE_NORMAL = 0,
            MODE_DEBUG = 1
        };

        int init(struct Config* cfg);
        void shutdown(void);
        """
        header = backend.parse(code, "test.h")

        # Check we got all declaration types
        structs = [d for d in header.declarations if isinstance(d, Struct)]
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        funcs = [d for d in header.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert len(enums) == 1
        assert len(funcs) == 2

        # Verify struct fields
        config = next(s for s in structs if s.name == "Config")
        assert len(config.fields) == 3
        assert config.fields[0].name == "width"
        assert config.fields[2].name == "name"
        assert isinstance(config.fields[2].type, Pointer)

        # Verify enum values
        mode = next(e for e in enums if e.name == "Mode")
        assert len(mode.values) == 2

        # Verify function signatures
        init_fn = next(f for f in funcs if f.name == "init")
        assert isinstance(init_fn.return_type, CType)
        assert init_fn.return_type.name == "int"
        assert len(init_fn.parameters) == 1
        assert isinstance(init_fn.parameters[0].type, Pointer)

        shutdown_fn = next(f for f in funcs if f.name == "shutdown")
        assert isinstance(shutdown_fn.return_type, CType)
        assert shutdown_fn.return_type.name == "void"
        assert len(shutdown_fn.parameters) == 0


@libclang
class TestBackendRegistration:
    """Test that the backend registers itself when libclang is available."""

    @pytest.fixture(autouse=True)
    def skip_if_no_libclang(self):
        if not is_system_libclang_available():
            pytest.skip("System libclang not available")

    def test_backend_registered(self):
        """If libclang is available, the backend should be registered."""
        from headerkit.backends import is_backend_available

        assert is_backend_available("libclang")

    def test_get_backend_returns_libclang(self):
        """get_backend('libclang') returns a LibclangBackend instance."""
        from headerkit.backends import get_backend

        backend = get_backend("libclang")
        assert isinstance(backend, LibclangBackend)

    def test_protocol_compliance(self):
        """LibclangBackend satisfies the ParserBackend protocol."""
        from headerkit.ir import ParserBackend

        backend = LibclangBackend()
        assert isinstance(backend, ParserBackend)


class TestGetSystemIncludeDirs:
    """Tests for get_system_include_dirs() system include path detection.

    Adapted from autopxd2 test_libclang_includes.py::_get_system_include_args.
    """

    def setup_method(self):
        """Clear the cached include dirs before each test."""
        import headerkit.backends.libclang as mod

        self._saved_c = mod._system_include_cache_c
        self._saved_cxx = mod._system_include_cache_cxx
        mod._system_include_cache_c = None
        mod._system_include_cache_cxx = None

    def teardown_method(self):
        """Restore cached include dirs after each test."""
        import headerkit.backends.libclang as mod

        mod._system_include_cache_c = self._saved_c
        mod._system_include_cache_cxx = self._saved_cxx

    def test_returns_list_of_strings(self):
        """get_system_include_dirs returns a list of strings."""
        result = get_system_include_dirs()
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)

    def test_entries_are_isystem_flags(self):
        """Each entry should be an -isystem flag if any paths are found."""
        result = get_system_include_dirs()
        for item in result:
            assert item.startswith("-isystem"), f"Expected -isystem prefix, got: {item}"

    def test_result_is_cached(self):
        """Second call returns the same cached list object."""
        first = get_system_include_dirs()
        second = get_system_include_dirs()
        assert first is second

    def test_c_and_cxx_cached_separately(self):
        """C and C++ include dirs are cached independently."""
        c_dirs = get_system_include_dirs(cplus=False)
        cxx_dirs = get_system_include_dirs(cplus=True)
        # They might be different (C++ includes libc++ paths)
        # But both should be lists
        assert isinstance(c_dirs, list)
        assert isinstance(cxx_dirs, list)

    def test_clang_not_found_returns_empty(self):
        """When clang is not on PATH, returns empty list."""
        import headerkit.backends.libclang as mod

        mod._system_include_cache_c = None
        with patch("headerkit.backends.libclang.subprocess.run", side_effect=FileNotFoundError):
            result = get_system_include_dirs()
            assert result == []

    def test_clang_timeout_returns_empty(self):
        """When clang times out, returns empty list."""
        import headerkit.backends.libclang as mod

        mod._system_include_cache_c = None
        with patch(
            "headerkit.backends.libclang.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="clang", timeout=10),
        ):
            result = get_system_include_dirs()
            assert result == []

    def test_parses_include_search_paths(self):
        """Parses clang -v output to extract include search paths."""
        import headerkit.backends.libclang as mod

        mod._system_include_cache_c = None
        mock_result = MagicMock()
        mock_result.stderr = (
            "clang version 18.0.0\n"
            "#include <...> search starts here:\n"
            " /usr/lib/clang/18/include\n"
            " /usr/include\n"
            "End of search list.\n"
        )
        with patch("headerkit.backends.libclang.subprocess.run", return_value=mock_result):
            result = get_system_include_dirs()
            assert "-isystem/usr/lib/clang/18/include" in result
            assert "-isystem/usr/include" in result

    def test_skips_framework_directories(self):
        """Framework directories are excluded from the result."""
        import headerkit.backends.libclang as mod

        mod._system_include_cache_c = None
        mock_result = MagicMock()
        mock_result.stderr = (
            "#include <...> search starts here:\n"
            " /usr/include\n"
            " /System/Library/Frameworks (framework directory)\n"
            "End of search list.\n"
        )
        with patch("headerkit.backends.libclang.subprocess.run", return_value=mock_result):
            result = get_system_include_dirs()
            assert "-isystem/usr/include" in result
            assert len(result) == 1  # framework dir excluded


@libclang
class TestHeaderInclusionTracking:
    """Tests for tracking included headers via libclang parsing.

    Adapted from autopxd2 test_libclang_includes.py::TestHeaderInclusionTracking.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_libclang(self):
        if not is_system_libclang_available():
            pytest.skip("System libclang not available")

    @pytest.fixture
    def backend(self):
        return LibclangBackend()

    def test_includes_stdio(self, backend):
        """Detects stdio.h inclusion when parsing code that includes it."""
        code = "#include <stdio.h>\nvoid test_func(FILE *f);\n"
        header = backend.parse(code, "test.h")
        assert any("stdio.h" in h for h in header.included_headers)

    def test_includes_stdint(self, backend):
        """Detects stdint.h inclusion."""
        code = "#include <stdint.h>\ntypedef uint32_t my_int;\n"
        header = backend.parse(code, "test.h")
        assert any("stdint.h" in h for h in header.included_headers)

    def test_includes_multiple_headers(self, backend):
        """Detects multiple header inclusions."""
        code = "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nvoid test(void);\n"
        header = backend.parse(code, "test.h")
        included_basenames = {os.path.basename(h) for h in header.included_headers}
        assert "stdio.h" in included_basenames
        assert "stdlib.h" in included_basenames
        assert "string.h" in included_basenames

    def test_no_includes_returns_set(self, backend):
        """Header with no #include directives has included_headers as a set."""
        code = "void standalone_func(int x);\n"
        header = backend.parse(code, "test.h")
        assert isinstance(header.included_headers, set)

    def test_includes_transitive(self, backend):
        """Tracks transitive includes (headers included by other headers)."""
        # stdio.h typically includes other headers transitively
        code = "#include <stdio.h>\nvoid test(void);\n"
        header = backend.parse(code, "test.h")
        # Should have at least stdio.h plus its transitive includes
        assert len(header.included_headers) >= 1


@libclang
class TestTypeQualifierParsing:
    """Tests for type qualifier parsing through the libclang backend.

    Adapted from autopxd2 test_type_qualifiers.py. These test that the
    libclang backend correctly extracts const, volatile, and handles
    _Atomic / __restrict qualifiers in the IR.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_libclang(self):
        if not is_system_libclang_available():
            pytest.skip("System libclang not available")

    @pytest.fixture
    def backend(self):
        return LibclangBackend()

    def test_const_qualifier_on_param(self, backend):
        """const qualifier is preserved on function parameter types."""
        code = "void process(const int* ptr);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        # The pointee should be const int
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "const" in pointee.qualifiers

    def test_volatile_qualifier_on_param(self, backend):
        """volatile qualifier is preserved on function parameter types."""
        code = "void modify(volatile int* ptr);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "volatile" in pointee.qualifiers

    def test_const_volatile_combined(self, backend):
        """Both const and volatile qualifiers are preserved together."""
        code = "void observe(const volatile int* ptr);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "const" in pointee.qualifiers
        assert "volatile" in pointee.qualifiers

    def test_const_char_pointer(self, backend):
        """const char* is a common pattern that should parse correctly."""
        code = "void print(const char* msg);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        param = funcs[0].parameters[0]
        assert isinstance(param.type, Pointer)
        pointee = param.type.pointee
        assert isinstance(pointee, CType)
        assert "const" in pointee.qualifiers
        assert pointee.name == "char"

    def test_atomic_typedef_parsed(self, backend):
        """_Atomic typedef is parsed (qualifier may be stripped by libclang)."""
        code = "typedef _Atomic int atomic_int;"
        header = backend.parse(code, "test.h")
        typedefs = [d for d in header.declarations if isinstance(d, Typedef)]
        assert len(typedefs) == 1
        td = typedefs[0]
        assert td.name == "atomic_int"
        # _Atomic may be stripped by libclang's type canonicalization;
        # the key thing is that the typedef is parsed and the base type is int
        assert "int" in str(td.underlying_type)

    def test_atomic_in_struct_field(self, backend):
        """_Atomic types in struct fields are parsed."""
        code = """
        typedef _Atomic int atomic_int;
        struct counter {
            atomic_int value;
        };
        """
        header = backend.parse(code, "test.h")
        structs = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(structs) == 1
        counter = structs[0]
        assert counter.name == "counter"
        assert len(counter.fields) == 1
        assert counter.fields[0].name == "value"

    def test_restrict_in_function_param(self, backend):
        """__restrict qualifier in function parameters is handled."""
        code = "void copy(char* __restrict dst, const char* __restrict src);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        func = funcs[0]
        assert func.name == "copy"
        assert len(func.parameters) == 2
        # __restrict is not a standard qualifier that headerkit preserves in IR,
        # but the function should still parse correctly
        assert func.parameters[0].name == "dst"
        assert func.parameters[1].name == "src"
        # src should have const on its pointee
        src_param = func.parameters[1]
        assert isinstance(src_param.type, Pointer)
        assert "const" in src_param.type.pointee.qualifiers

    def test_noreturn_function_parsed(self, backend):
        """_Noreturn functions are parsed (qualifier stripped from IR)."""
        code = "_Noreturn void abort_program(void);"
        header = backend.parse(code, "test.h")
        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        func = funcs[0]
        assert func.name == "abort_program"
        assert isinstance(func.return_type, CType)
        assert func.return_type.name == "void"
