from __future__ import annotations

from typing import Any

import pytest

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.scaffold import ProjectLayout, ScaffoldOptions, scaffold
from headerkit.writers import get_writer, list_writer_layouts, list_writer_options, list_writers


@pytest.fixture
def sample_header() -> Header:
    return Header(
        path="sample.h",
        declarations=[
            Function(
                name="multiply",
                return_type=CType("int"),
                parameters=[
                    Parameter(name="a", type=CType("int")),
                    Parameter(name="b", type=CType("int")),
                ],
            )
        ],
    )


class TestUnifiedWriterScaffolding:
    """Test suite asserting all writers implement write_layout and conform to scaffolding."""

    def test_all_writers_support_write_layout_single_file(self, sample_header: Header) -> None:
        """Every registered writer must support write_layout(layout='file') and match write()."""
        for writer_name in list_writers():
            writer = get_writer(writer_name)
            assert hasattr(writer, "write_layout"), f"{writer_name} must implement write_layout"

            opts = ScaffoldOptions(package_name="test_pkg", target_language=writer_name, layout="file")
            layout = writer.write_layout(sample_header, opts)
            assert isinstance(layout, ProjectLayout)
            assert len(layout.files) >= 1

            # Legacy write() must produce the same content as the primary output file
            rendered = writer.write(sample_header)
            assert rendered == layout.files[0].content

    def test_cython_package_scaffolding(self, sample_header: Header) -> None:
        """Cython writer must produce .pxd, .pyx, pyproject.toml, and tripwire tests in package mode."""
        writer = get_writer("cython")
        opts = ScaffoldOptions(package_name="mathkit", target_language="cython", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "src/mathkit/__init__.py" in paths
        assert "src/mathkit/mathkit.pxd" in paths
        assert "src/mathkit/mathkit.pyx" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_wrapper.py" in paths

        pxd_file = layout.get_file("src/mathkit/mathkit.pxd")
        assert pxd_file is not None
        assert "int multiply(int a, int b)" in pxd_file.content

        tw_file = layout.get_file("tests/test_tripwire.py")
        assert tw_file is not None
        assert "test_cython_tripwire" in tw_file.content

        unit_file = layout.get_file("tests/test_wrapper.py")
        assert unit_file is not None
        assert "test_mathkit_package_structure" in unit_file.content
        assert "inspect.ismodule" in unit_file.content

    def test_cffi_package_scaffolding(self, sample_header: Header) -> None:
        """CFFI writer must produce build_ffi.py, _bindings.py, pyproject.toml, and tripwires."""
        writer = get_writer("cffi")
        opts = ScaffoldOptions(package_name="cffikit", target_language="cffi", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "build_ffi.py" in paths
        assert "src/cffikit/__init__.py" in paths
        assert "src/cffikit/_bindings.py" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_bindings.py" in paths

        build_file = layout.get_file("build_ffi.py")
        assert build_file is not None
        assert "int multiply(int a, int b);" in build_file.content

        tw_file = layout.get_file("tests/test_tripwire.py")
        assert tw_file is not None
        assert "multiply" in tw_file.content

        unit_file = layout.get_file("tests/test_bindings.py")
        assert unit_file is not None
        assert "test_cffikit_ffi_defined" in unit_file.content
        assert "isinstance(ffi, FFI)" in unit_file.content

    def test_cshim_package_scaffolding(self, sample_header: Header) -> None:
        """CShim writer must produce CMakeLists.txt, include header, src bridge, and test harness."""
        writer = get_writer("cshim")
        opts = ScaffoldOptions(package_name="bridge", target_language="cshim", layout="package")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "CMakeLists.txt" in paths
        assert "include/bridge_cshim.h" in paths
        assert "src/bridge_cshim.cpp" in paths
        assert "tests/test_cshim.c" in paths

        header_file = layout.get_file("include/bridge_cshim.h")
        assert header_file is not None
        assert "int multiply(int a, int b);" in header_file.content

        cpp_file = layout.get_file("src/bridge_cshim.cpp")
        assert cpp_file is not None
        assert '#include "bridge_cshim.h"' in cpp_file.content
        assert "multiply(a, b);" in cpp_file.content

        test_file = layout.get_file("tests/test_cshim.c")
        assert test_file is not None
        assert '#include "bridge_cshim.h"' in test_file.content
        assert "assert((void*)multiply != NULL);" in test_file.content

    def test_cshim_package_scaffolding_no_tests(self, sample_header: Header) -> None:
        """CShim writer with test_type='none' must omit test artifacts."""
        writer = get_writer("cshim")
        opts = ScaffoldOptions(package_name="bridge", target_language="cshim", layout="package", test_type="none")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "CMakeLists.txt" in paths
        assert "include/bridge_cshim.h" in paths
        assert "src/bridge_cshim.cpp" in paths
        assert "tests/test_cshim.c" not in paths

        cmake = layout.get_file("CMakeLists.txt")
        assert cmake is not None
        assert "enable_testing()" not in cmake.content
        assert "add_test" not in cmake.content

    def test_luajit_package_scaffolding(self, sample_header: Header) -> None:
        """LuaJIT writer must produce rockspec, src module, and tripwire tests."""
        writer = get_writer("lua")
        opts = ScaffoldOptions(package_name="luabridge", target_language="lua", layout="package")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "luabridge-scm-1.rockspec" in paths
        assert "src/luabridge.lua" in paths
        assert "tests/test_tripwire.lua" in paths
        assert "tests/test_luabridge.lua" in paths

        rockspec = layout.get_file("luabridge-scm-1.rockspec")
        assert rockspec is not None
        assert 'package = "luabridge"' in rockspec.content

        src_file = layout.get_file("src/luabridge.lua")
        assert src_file is not None
        assert "int multiply(int a, int b);" in src_file.content

        tw_file = layout.get_file("tests/test_tripwire.lua")
        assert tw_file is not None
        assert "pcall(ffi.load" in tw_file.content
        assert "lib.multiply" in tw_file.content

        unit_file = layout.get_file("tests/test_luabridge.lua")
        assert unit_file is not None
        assert "luabridge.multiply ~= nil" in unit_file.content

    def test_nim_package_scaffolding(self, sample_header: Header) -> None:
        """Nim writer must produce .nimble, src module, bindings, nim.cfg, and tripwires."""
        writer = get_writer("nim")
        opts = ScaffoldOptions(package_name="nimkit", target_language="nim", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "nimkit.nimble" in paths
        assert "src/nimkit.nim" in paths
        assert "src/nimkit/bindings.nim" in paths
        assert "nim.cfg" in paths
        assert "tests/test_tripwire.nim" in paths
        assert "tests/test_nimkit.nim" in paths

        nimble = layout.get_file("nimkit.nimble")
        assert nimble is not None
        assert 'packageName   = "nimkit"' in nimble.content

        bindings = layout.get_file("src/nimkit/bindings.nim")
        assert bindings is not None
        assert "proc multiply*(a: cint, b: cint): cint" in bindings.content

        tw_file = layout.get_file("tests/test_tripwire.nim")
        assert tw_file is not None
        assert 'loadLib("nimkit")' in tw_file.content
        assert 'lib.symAddr("multiply")' in tw_file.content

        unit_file = layout.get_file("tests/test_nimkit.nim")
        assert unit_file is not None
        assert "check declared(multiply)" in unit_file.content

    def test_mojo_package_scaffolding(self, sample_header: Header) -> None:
        """Mojo writer must produce mojoproject.toml, src module, bindings, and tripwires."""
        writer = get_writer("mojo")
        opts = ScaffoldOptions(package_name="mojokit", target_language="mojo", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "mojoproject.toml" in paths
        assert "src/mojokit/__init__.mojo" in paths
        assert "src/mojokit/bindings.mojo" in paths
        assert "tests/test_tripwire.mojo" in paths
        assert "tests/test_mojokit.mojo" in paths

        mojo_proj = layout.get_file("mojoproject.toml")
        assert mojo_proj is not None
        assert 'name = "mojokit"' in mojo_proj.content

        bindings = layout.get_file("src/mojokit/bindings.mojo")
        assert bindings is not None
        assert "multiply" in bindings.content

        tw_file = layout.get_file("tests/test_tripwire.mojo")
        assert tw_file is not None
        assert 'DLHandle("mojokit")' in tw_file.content
        assert "handle.get_function" in tw_file.content
        assert '"multiply"' in tw_file.content

        unit_file = layout.get_file("tests/test_mojokit.mojo")
        assert unit_file is not None
        assert "sizeof[Library]()" in unit_file.content

    def test_writer_layouts_introspection(self) -> None:
        """All writers must support at least 'file' layout, and list_writer_layouts must return it."""
        for writer_name in list_writers():
            layouts = list_writer_layouts(writer_name)
            assert isinstance(layouts, tuple)
            assert "file" in layouts

        # Multi-file writers
        assert "package" in list_writer_layouts("ctypes")
        assert "package" in list_writer_layouts("cffi")
        assert "package" in list_writer_layouts("cython")
        assert "package" in list_writer_layouts("nim")
        assert "package" in list_writer_layouts("mojo")
        assert "package" in list_writer_layouts("lua")
        assert "cmake" in list_writer_layouts("cshim")

        # Single-file only writers
        assert list_writer_layouts("json") == ("file",)
        assert list_writer_layouts("diff") == ("file",)
        assert list_writer_layouts("prompt") == ("file",)

        with pytest.raises(ValueError, match="Unknown writer: 'unknown_writer'"):
            list_writer_layouts("unknown_writer")

    def test_writer_options_introspection(self) -> None:
        """Writers must declare their options via supported_options and list_writer_options."""
        ctypes_opts = list_writer_options("ctypes")
        assert any(opt.name == "test_type" for opt in ctypes_opts)

        cshim_opts = list_writer_options("cshim")
        assert any(opt.name == "catch_exceptions" for opt in cshim_opts)
        assert any(opt.name == "test_type" for opt in cshim_opts)

        json_opts = list_writer_options("json")
        assert any(opt.name == "indent" for opt in json_opts)

        diff_opts = list_writer_options("diff")
        assert any(opt.name == "format" for opt in diff_opts)

        prompt_opts = list_writer_options("prompt")
        assert any(opt.name == "verbosity" for opt in prompt_opts)

        with pytest.raises(ValueError, match="Unknown writer: 'unknown_writer'"):
            list_writer_options("unknown_writer")

    def test_unsupported_layout_raises_value_error(self, sample_header: Header) -> None:
        """Requesting an unsupported layout must raise ValueError with available choices."""
        writer = get_writer("json")
        opts = ScaffoldOptions(package_name="data", target_language="json", layout="package")
        with pytest.raises(
            ValueError, match=r"Writer 'json' does not support layout 'package'\. Supported layouts: \['file'\]"
        ):
            writer.write_layout(sample_header, opts)

    def test_target_language_python_and_luajit_aliases(self, sample_header: Header) -> None:
        """Scaffolder must support target_language='python' and 'luajit' seamlessly."""
        opts_py = ScaffoldOptions(package_name="pybridge", target_language="python", layout="file")
        layout_py = scaffold(sample_header, opts_py)
        assert len(layout_py.files) == 1
        assert layout_py.files[0].path == "pybridge.py"

        opts_lua = ScaffoldOptions(package_name="luabridge", target_language="luajit", layout="file")
        layout_lua = scaffold(sample_header, opts_lua)
        assert len(layout_lua.files) == 1
        assert layout_lua.files[0].path == "luabridge.lua"

    def test_ctypes_package_scaffolding(self, sample_header: Header) -> None:
        """Ctypes writer must produce pyproject.toml, package module, and tripwires."""
        writer = get_writer("ctypes")
        opts = ScaffoldOptions(package_name="ctypeskit", target_language="ctypes", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "src/ctypeskit/__init__.py" in paths
        assert "src/ctypeskit/_bindings.py" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_bindings.py" in paths

        bindings_file = layout.get_file("src/ctypeskit/_bindings.py")
        assert bindings_file is not None
        assert "multiply" in bindings_file.content

        tw_file = layout.get_file("tests/test_tripwire.py")
        assert tw_file is not None
        assert 'hasattr(_bindings, "multiply")' in tw_file.content

        unit_file = layout.get_file("tests/test_bindings.py")
        assert unit_file is not None
        assert "test_ctypeskit_declarations" in unit_file.content
        assert 'hasattr(_bindings, "multiply")' in unit_file.content
        assert "inspect.ismodule" in unit_file.content

    def test_ctypes_package_scaffolding_no_tests(self, sample_header: Header) -> None:
        """Ctypes writer with test_type='none' must omit tests directory."""
        writer = get_writer("ctypes")
        opts = ScaffoldOptions(package_name="ctypeskit", target_language="ctypes", layout="package", test_type="none")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "src/ctypeskit/__init__.py" in paths
        assert "src/ctypeskit/_bindings.py" in paths
        assert "tests/test_tripwire.py" not in paths
        assert "tests/test_bindings.py" not in paths

    def test_cshim_cmake_layout(self, sample_header: Header) -> None:
        """CShim writer must support 'cmake' layout."""
        writer = get_writer("cshim")
        opts = ScaffoldOptions(package_name="shimming", target_language="cshim", layout="cmake")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "CMakeLists.txt" in paths
        assert "include/shimming_cshim.h" in paths
        assert "src/shimming_cshim.cpp" in paths
        assert "tests/test_cshim.c" in paths

    def test_json_writer_options(self, sample_header: Header) -> None:
        """Json writer must respect indent option in ScaffoldOptions."""
        writer = get_writer("json")
        opts_4 = ScaffoldOptions(package_name="out", layout="file", options={"indent": 4})
        layout_4 = writer.write_layout(sample_header, opts_4)
        assert "    " in layout_4.files[0].content

    def test_diff_writer_options(self, sample_header: Header) -> None:
        """Diff writer must respect format='markdown' in ScaffoldOptions."""
        writer = get_writer("diff")
        opts_md = ScaffoldOptions(package_name="report", layout="file", options={"format": "markdown"})
        layout_md = writer.write_layout(sample_header, opts_md)
        assert layout_md.files[0].path == "report.md"
        assert "# API Diff:" in layout_md.files[0].content

    def test_prompt_writer_options(self, sample_header: Header) -> None:
        """Prompt writer must respect verbosity option in ScaffoldOptions."""
        writer = get_writer("prompt")
        opts_v = ScaffoldOptions(package_name="llm_ctx", layout="file", options={"verbosity": "verbose"})
        layout_v = writer.write_layout(sample_header, opts_v)
        assert layout_v.files[0].path == "llm_ctx.txt"
        assert "{" in layout_v.files[0].content  # verbose emits JSON

    def test_scaffold_hook_layout_filtering(self, sample_header: Header) -> None:
        """Hooks can match specifically on layout and override layout generation."""
        from headerkit.hooks import HookRegistry, Priority
        from headerkit.scaffold import OutputFile

        # Register layout-specific hook for ctypes package layout only
        def custom_package_hook(_unit: Header, _options: ScaffoldOptions, **_kwargs: Any) -> ProjectLayout:
            return ProjectLayout(files=[OutputFile(path="custom_pkg.txt", content="custom package layout")])

        # Save registry snapshot
        snap = HookRegistry.snapshot()
        try:
            HookRegistry.register_global(
                "scaffold_project",
                custom_package_hook,
                priority=Priority.OVERRIDE,
                writer="ctypes",
                layout="package",
            )

            # layout="package" should use custom hook
            opts_pkg = ScaffoldOptions(package_name="test", target_language="ctypes", layout="package")
            layout_pkg = scaffold(sample_header, opts_pkg)
            assert len(layout_pkg.files) == 1
            assert layout_pkg.files[0].path == "custom_pkg.txt"

            # layout="file" should NOT use custom hook because of layout filter
            opts_file = ScaffoldOptions(package_name="test", target_language="ctypes", layout="file")
            layout_file = scaffold(sample_header, opts_file)
            assert layout_file.files[0].path != "custom_pkg.txt"
            assert "ctypes" in layout_file.files[0].content
        finally:
            HookRegistry.restore(snap)
