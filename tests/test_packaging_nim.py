"""Tests for Nim scikit-build wheel packaging template and layout generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.scaffold import ProjectLayout, ScaffoldOptions, scaffold
from headerkit.writers import list_writer_layouts


@pytest.fixture
def sample_header() -> Header:
    return Header(
        path="fastmath.h",
        declarations=[
            Function(
                name="add_numbers",
                return_type=CType("int"),
                parameters=[
                    Parameter(name="a", type=CType("int")),
                    Parameter(name="b", type=CType("int")),
                ],
            ),
            Function(
                name="compute_pi",
                return_type=CType("double"),
                parameters=[],
            ),
        ],
    )


class TestNimWheelPackaging:
    """Test suite for Nim scikit-build wheel packaging template."""

    def test_nim_writer_declares_wheel_layouts(self) -> None:
        layouts = list_writer_layouts("nim")
        assert "wheel" in layouts
        assert "scikit-build" in layouts

    def test_nim_wheel_layout_files_presence(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(
            package_name="fastmath",
            target_language="nim",
            layout="wheel",
            test_type="both",
        )
        layout = scaffold(sample_header, opts)
        assert isinstance(layout, ProjectLayout)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "CMakeLists.txt" in paths
        assert "src/fastmath.nim" in paths
        assert "fastmath/__init__.py" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_fastmath.py" in paths
        assert "README.md" in paths

    def test_nim_scikit_build_alias(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(
            package_name="fastmath",
            target_language="nim",
            layout="scikit-build",
            test_type="both",
        )
        layout = scaffold(sample_header, opts)
        cmake = layout.get_file("CMakeLists.txt")
        assert cmake is not None
        assert "project(fastmath_pkg LANGUAGES C)" in cmake.content
        assert "--mm:orc" in cmake.content

        pyproj = layout.get_file("pyproject.toml")
        assert pyproj is not None
        assert 'name = "fastmath"' in pyproj.content
        assert "scikit_build_core.build" in pyproj.content

        wrapper = layout.get_file("fastmath/__init__.py")
        assert wrapper is not None
        assert "def add_numbers" in wrapper.content
        assert "init_nim" in wrapper.content

    def test_pyproject_toml_structure(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel")
        layout = scaffold(sample_header, opts)
        pyproj = layout.get_file("pyproject.toml")
        assert pyproj is not None
        content = pyproj.content

        assert 'build-backend = "scikit_build_core.build"' in content
        assert "scikit-build-core" in content
        assert 'name = "fastmath"' in content
        assert "[tool.scikit-build]" in content
        assert 'wheel.packages = ["fastmath"]' in content

    def test_cmake_lists_structure(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel")
        layout = scaffold(sample_header, opts)
        cmake = layout.get_file("CMakeLists.txt")
        assert cmake is not None
        content = cmake.content

        assert "cmake_minimum_required(VERSION 3.18)" in content
        assert "project(fastmath_pkg LANGUAGES C)" in content
        assert "find_program(NIM_EXECUTABLE nim REQUIRED)" in content
        assert "--app:lib" in content
        assert "--mm:orc" in content
        assert "--threads:on" in content
        assert "-d:release" in content
        assert 'DESTINATION "fastmath"' in content

    def test_nim_source_structure(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel")
        layout = scaffold(sample_header, opts)
        nim_src = layout.get_file("src/fastmath.nim")
        assert nim_src is not None
        content = nim_src.content

        assert "proc NimMain*() {.cdecl, importc.}" in content
        assert "add_numbers" in content
        assert "compute_pi" in content
        assert "{.exportc, dynlib, cdecl.}" in content

    def test_python_wrapper_structure(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel")
        layout = scaffold(sample_header, opts)
        wrapper = layout.get_file("fastmath/__init__.py")
        assert wrapper is not None
        content = wrapper.content

        assert "ctypes" in content
        assert "def init_nim" in content
        assert "NimMain" in content
        assert "add_numbers" in content
        assert "compute_pi" in content
        assert "argtypes" in content
        assert "restype" in content

    def test_test_type_filtering(self, sample_header: Header) -> None:
        opts_none = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel", test_type="none")
        layout_none = scaffold(sample_header, opts_none)
        paths_none = {f.path for f in layout_none.files}
        assert "tests/test_tripwire.py" not in paths_none
        assert "tests/test_fastmath.py" not in paths_none

        opts_tw = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel", test_type="tripwire")
        layout_tw = scaffold(sample_header, opts_tw)
        paths_tw = {f.path for f in layout_tw.files}
        assert "tests/test_tripwire.py" in paths_tw
        assert "tests/test_fastmath.py" not in paths_tw

        opts_unit = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel", test_type="unit")
        layout_unit = scaffold(sample_header, opts_unit)
        paths_unit = {f.path for f in layout_unit.files}
        assert "tests/test_tripwire.py" not in paths_unit
        assert "tests/test_fastmath.py" in paths_unit

    def test_tripwire_and_unit_no_tautological_assertions(self, sample_header: Header) -> None:
        opts = ScaffoldOptions(package_name="fastmath", target_language="nim", layout="wheel", test_type="both")
        layout = scaffold(sample_header, opts)
        tw = layout.get_file("tests/test_tripwire.py")
        assert tw is not None
        assert "assert True" not in tw.content
        assert "pytest.mark.tripwire" in tw.content
        assert "add_numbers" in tw.content

        unit = layout.get_file("tests/test_fastmath.py")
        assert unit is not None
        assert "assert True" not in unit.content
        assert "test_add_numbers_wrapper_signature" in unit.content
        assert "inspect.signature" in unit.content
        assert "len(sig.parameters) == 2" in unit.content
        assert "['a', 'b']" in unit.content


class TestNimWheelCLI:
    def test_cli_scaffold_wheel(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from headerkit._cli import main

        header = tmp_path / "libtest.h"
        header.write_text("int run_computation(int x);\n", encoding="utf-8")
        out_dir = tmp_path / "nim_wheel_pkg"

        test_args = [
            "headerkit",
            str(header),
            "-w",
            "nim",
            "--layout",
            "wheel",
            "--package-name",
            "nim_wheel_pkg",
            "-o",
            f"nim:{out_dir}",
            "--no-input",
        ]
        monkeypatch.setattr("sys.argv", test_args)

        ret = main()
        assert ret == 0

        pyproj = out_dir / "pyproject.toml"
        assert pyproj.exists()
        assert "scikit_build_core.build" in pyproj.read_text(encoding="utf-8")
        assert 'name = "nim_wheel_pkg"' in pyproj.read_text(encoding="utf-8")

        cmake = out_dir / "CMakeLists.txt"
        assert cmake.exists()
        assert "find_program(NIM_EXECUTABLE nim REQUIRED)" in cmake.read_text(encoding="utf-8")
        assert "--mm:orc" in cmake.read_text(encoding="utf-8")

        nim_src = out_dir / "src/nim_wheel_pkg.nim"
        assert nim_src.exists()
        assert "proc NimMain" in nim_src.read_text(encoding="utf-8")
        assert "proc run_computation" in nim_src.read_text(encoding="utf-8")

        wrapper = out_dir / "nim_wheel_pkg/__init__.py"
        assert wrapper.exists()
        assert "def init_nim" in wrapper.read_text(encoding="utf-8")
        assert "def run_computation" in wrapper.read_text(encoding="utf-8")

        tw = out_dir / "tests/test_tripwire.py"
        assert tw.exists()
        assert "test_native_library_loaded" in tw.read_text(encoding="utf-8")
        assert "test_entrypoint_run_computation" in tw.read_text(encoding="utf-8")

        unit = out_dir / "tests/test_nim_wheel_pkg.py"
        assert unit.exists()
        assert "test_run_computation_wrapper_signature" in unit.read_text(encoding="utf-8")
        assert "inspect.signature" in unit.read_text(encoding="utf-8")

    def test_complex_type_conversions(self) -> None:
        from headerkit.ir import Array, CType, Function, FunctionPointer, Parameter, Pointer, Reference
        from headerkit.packaging.nim import _ir_type_to_ctypes, _ir_type_to_nim

        # Pointer to int
        ptr_int = Pointer(CType("int"))
        assert _ir_type_to_nim(ptr_int) == "ptr cint"
        assert _ir_type_to_ctypes(ptr_int) == "ctypes.POINTER(ctypes.c_int)"

        # Pointer to char (cstring)
        ptr_char = Pointer(CType("char"))
        assert _ir_type_to_nim(ptr_char) == "cstring"
        assert _ir_type_to_ctypes(ptr_char) == "ctypes.c_char_p"

        # Pointer to void
        ptr_void = Pointer(CType("void"))
        assert _ir_type_to_nim(ptr_void) == "pointer"
        assert _ir_type_to_ctypes(ptr_void) == "ctypes.c_void_p"

        # Reference
        ref_double = Reference(CType("double"))
        assert _ir_type_to_nim(ref_double) == "var cdouble"
        assert _ir_type_to_ctypes(ref_double) == "ctypes.POINTER(ctypes.c_double)"

        # Array
        arr_float = Array(CType("float"), size=4)
        assert _ir_type_to_nim(arr_float) == "array[4, cfloat]"
        assert _ir_type_to_ctypes(arr_float) == "(ctypes.c_float * 4)"

        # FunctionPointer
        fn_ptr = FunctionPointer(return_type=CType("void"), parameters=[Parameter("x", CType("int"))])
        assert _ir_type_to_nim(fn_ptr) == "pointer"
        assert _ir_type_to_ctypes(fn_ptr) == "ctypes.c_void_p"

        # Test in generated Nim source and Python wrapper
        h = Header(
            path="complex.h",
            declarations=[
                Function(
                    name="process_buffer",
                    return_type=Pointer(CType("char")),
                    parameters=[
                        Parameter("buf", Pointer(CType("int"))),
                        Parameter("count", Reference(CType("int"))),
                        Parameter("fixed", Array(CType("float"), size=8)),
                    ],
                )
            ],
        )
        opts = ScaffoldOptions(package_name="complex_pkg", target_language="nim", layout="wheel")
        layout = scaffold(h, opts)
        src = layout.get_file("src/complex_pkg.nim")
        assert src is not None
        assert "proc process_buffer*(buf: ptr cint, count: var cint, fixed: array[8, cfloat]): cstring" in src.content

        wrapper = layout.get_file("complex_pkg/__init__.py")
        assert wrapper is not None
        assert (
            "_lib.process_buffer.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), (ctypes.c_float * 8)]"
            in wrapper.content
        )
        assert "_lib.process_buffer.restype = ctypes.c_char_p" in wrapper.content

    def test_package_name_validation(self, sample_header: Header) -> None:
        import pytest

        from headerkit.packaging.nim import (
            generate_nim_cmake,
            generate_nim_pyproject,
            generate_nim_python_wrapper,
            generate_nim_source,
            generate_nim_wheel_layout,
        )

        for invalid in ["my-pkg", "123pkg", "pkg.name", "pkg/name", ""]:
            with pytest.raises(ValueError, match="Invalid package name"):
                generate_nim_cmake(invalid)
            with pytest.raises(ValueError, match="Invalid package name"):
                generate_nim_pyproject(invalid)
            with pytest.raises(ValueError, match="Invalid package name"):
                generate_nim_source(invalid, sample_header)
            with pytest.raises(ValueError, match="Invalid package name"):
                generate_nim_python_wrapper(invalid, sample_header)
            with pytest.raises(ValueError, match="Invalid package name"):
                generate_nim_wheel_layout(
                    sample_header, ScaffoldOptions(package_name=invalid, target_language="nim", layout="wheel")
                )
