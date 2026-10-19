"""Execution gates for scaffolded wheel packages: build them, import them, run generated test suites.

Verifies the full lifecycle of turnkey binary wheel packages:
1. ctypes: compiled shared library placed in wheel layout, package imports, entrypoints resolve,
   generated tripwire and unit tests pass; missing shared library triggers tripwire failure.
2. Cython: compiled native extension via cython CLI and C compiler, package imports, entrypoints resolve,
   generated tripwire and unit tests pass; missing extension triggers tripwire failure.
3. CFFI: compiled out-of-line ABI backend, package imports, entrypoints resolve,
   generated tripwire and unit tests pass; missing backend triggers tripwire failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.packaging.cffi import generate_cffi_wheel_layout
from headerkit.packaging.ctypes import generate_ctypes_wheel_layout
from headerkit.packaging.cython import generate_cython_wheel_layout
from headerkit.scaffold import ScaffoldOptions
from tests.native_build import (
    PYTHON_INCLUDE_DIR,
    extension_filename,
    link_extension_command,
    shared_library_command,
    shared_library_filename,
)
from tests.skip_policy import CC_INSTALL, CYTHON_INSTALL, require_program


def _find_c_compiler() -> str:
    return require_program("cc", "gcc", "clang", install=CC_INSTALL)


def test_ctypes_wheel_execution_and_tripwire(tmp_path: Path) -> None:
    """Test full execution of scaffolded ctypes wheel package and its tripwire."""
    compiler = _find_c_compiler()

    # 1. Write C library source and compile to shared object
    c_header = "int add_nums(int a, int b);\nint multiply_nums(int a, int b);\n"
    c_source = (
        '#include "mathpkg.h"\n'
        "int add_nums(int a, int b) { return a + b; }\n"
        "int multiply_nums(int a, int b) { return a * b; }\n"
    )
    (tmp_path / "mathpkg.h").write_text(c_header, encoding="utf-8")
    (tmp_path / "mathpkg.c").write_text(c_source, encoding="utf-8")

    lib_name = shared_library_filename("mathpkg")
    out_lib = tmp_path / lib_name
    cmd = shared_library_command(compiler, ["mathpkg.c"], str(out_lib), includes=["."])
    res_cc = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, check=False)  # noqa: S603
    assert res_cc.returncode == 0, f"C library compilation failed: {res_cc.stderr}"

    # 2. Scaffold ctypes wheel package
    fn_add = Function(
        name="add_nums",
        return_type=CType("int"),
        parameters=[Parameter("a", CType("int")), Parameter("b", CType("int"))],
    )
    fn_mul = Function(
        name="multiply_nums",
        return_type=CType("int"),
        parameters=[Parameter("a", CType("int")), Parameter("b", CType("int"))],
    )
    unit = Header(path="mathpkg.h", declarations=[fn_add, fn_mul])
    opts = ScaffoldOptions(layout="wheel", package_name="mathpkg")
    layout = generate_ctypes_wheel_layout(unit, opts)

    pkg_dir = tmp_path / "ctypes_wheel"
    pkg_dir.mkdir()
    for f in layout.files:
        dest = pkg_dir / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content, encoding="utf-8")

    # 3. Copy compiled shared library into package bundle
    bundled_lib = pkg_dir / "src" / "mathpkg" / lib_name
    shutil.copy(out_lib, bundled_lib)

    # 4. Execute generated pytest test suite (tripwire + unit tests)
    env = {**os.environ, "PYTHONPATH": str(pkg_dir / "src")}
    res_test = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res_test.returncode == 0, (
        f"ctypes wheel tests failed:\nSTDOUT:\n{res_test.stdout}\nSTDERR:\n{res_test.stderr}"
    )
    assert "passed" in res_test.stdout

    # 5. Tripwire Invariant: remove native library, assert tripwire fails
    bundled_lib.unlink()
    res_tw_fail = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests/test_tripwire.py"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res_tw_fail.returncode != 0, "Tripwire unexpectedly passed with native library missing!"
    assert "Native dynamic library 'mathpkg' not found" in res_tw_fail.stdout


def test_cython_wheel_execution_and_tripwire(tmp_path: Path) -> None:
    """Test full compilation and execution of scaffolded Cython wheel package and its tripwire."""
    compiler = _find_c_compiler()
    cython_bin = require_program("cython", install=CYTHON_INSTALL)

    # 1. Scaffold Cython wheel package
    fn_calc = Function(
        name="calculate",
        return_type=CType("int"),
        parameters=[Parameter("val", CType("int"))],
    )
    unit = Header(path="calcpkg.h", declarations=[fn_calc])
    opts = ScaffoldOptions(layout="wheel", package_name="calcpkg")
    layout = generate_cython_wheel_layout(unit, opts)

    pkg_dir = tmp_path / "cython_wheel"
    pkg_dir.mkdir()
    for f in layout.files:
        dest = pkg_dir / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content, encoding="utf-8")

    # 2. Write C header and implementation
    c_header = "int calculate(int val);\n"
    c_source = '#include "calcpkg.h"\nint calculate(int val) { return val * 2; }\n'
    (pkg_dir / "src" / "calcpkg" / "calcpkg.h").write_text(c_header, encoding="utf-8")
    (pkg_dir / "src" / "calcpkg" / "calcpkg_impl.c").write_text(c_source, encoding="utf-8")

    # 3. Cythonize .pyx -> .c
    cy_c_out = pkg_dir / "src" / "calcpkg" / "calcpkg_cy.c"
    res_cy = subprocess.run(  # noqa: S603
        [
            cython_bin,
            "src/calcpkg/calcpkg.pyx",
            "--output-file",
            str(cy_c_out),
            "-3",
            "-I",
            "src/calcpkg",
            "-I",
            "src",
        ],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_cy.returncode == 0, f"Cython conversion failed: {res_cy.stderr}"

    # 4. Compile binary extension (.so / .pyd)
    out_ext = pkg_dir / "src" / "calcpkg" / extension_filename("calcpkg")

    cmd = link_extension_command(
        compiler,
        inputs=[
            str(cy_c_out),
            str(pkg_dir / "src" / "calcpkg" / "calcpkg_impl.c"),
        ],
        stem=str(pkg_dir / "src" / "calcpkg" / "calcpkg"),
        includes=[PYTHON_INCLUDE_DIR, "src/calcpkg"],
    )

    res_cc = subprocess.run(cmd, cwd=pkg_dir, capture_output=True, text=True, check=False)  # noqa: S603
    assert res_cc.returncode == 0, f"Extension compilation failed: {res_cc.stderr}"
    assert out_ext.is_file()

    # 5. Run generated test suite
    env = {**os.environ, "PYTHONPATH": str(pkg_dir / "src")}
    res_test = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res_test.returncode == 0, (
        f"Cython wheel tests failed:\nSTDOUT:\n{res_test.stdout}\nSTDERR:\n{res_test.stderr}"
    )
    assert "passed" in res_test.stdout

    # 6. Tripwire Invariant: remove extension binary, assert tripwire fails
    out_ext.unlink()
    res_tw_fail = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests/test_tripwire.py"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res_tw_fail.returncode != 0, "Tripwire unexpectedly passed with extension binary missing!"


def test_cffi_wheel_execution_and_tripwire(tmp_path: Path) -> None:
    """Test full compilation and execution of scaffolded CFFI wheel package and its tripwire."""
    try:
        import cffi  # noqa: F401
        import setuptools  # noqa: F401
    except ImportError:
        pytest.skip("cffi and setuptools required for out-of-line CFFI wheel compilation")

    # 1. Scaffold CFFI wheel package
    fn_hash = Function(
        name="hash_int",
        return_type=CType("int"),
        parameters=[Parameter("x", CType("int"))],
    )
    unit = Header(path="hashpkg.h", declarations=[fn_hash])
    opts = ScaffoldOptions(layout="wheel", package_name="hashpkg")
    layout = generate_cffi_wheel_layout(unit, opts)

    pkg_dir = tmp_path / "cffi_wheel"
    pkg_dir.mkdir()
    for f in layout.files:
        dest = pkg_dir / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content, encoding="utf-8")

    # 2. Write C header and implementation
    c_header = "int hash_int(int x);\n"
    c_source = '#include "hashpkg.h"\nint hash_int(int x) { return x * 31; }\n'
    (pkg_dir / "hashpkg.h").write_text(c_header, encoding="utf-8")

    build_ffi_file = pkg_dir / "build_ffi.py"
    content = build_ffi_file.read_text(encoding="utf-8")
    content = content.replace(
        '#include "hashpkg.h"',
        c_source.strip(),
    )
    build_ffi_file.write_text(content, encoding="utf-8")

    # 3. Build CFFI extension
    res_build = subprocess.run(
        [sys.executable, "build_ffi.py"], cwd=pkg_dir, capture_output=True, text=True, check=False
    )  # noqa: S603
    assert res_build.returncode == 0, f"CFFI build failed: {res_build.stderr}"

    # Move compiled extension to src/hashpkg/
    compiled_files: list[Path] = []
    for pattern in ["*_cffi_backend*", "hashpkg.*"]:
        for so in pkg_dir.glob(pattern):
            target = pkg_dir / "src" / "hashpkg" / so.name
            shutil.move(str(so), str(target))
            compiled_files.append(target)
        for so in (pkg_dir / "hashpkg").glob(pattern):
            target = pkg_dir / "src" / "hashpkg" / so.name
            shutil.move(str(so), str(target))
            compiled_files.append(target)

    assert compiled_files, "No compiled CFFI extension binary found"

    # 4. Run generated test suite
    env = {**os.environ, "PYTHONPATH": str(pkg_dir / "src")}
    res_test = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res_test.returncode == 0, f"CFFI wheel tests failed:\nSTDOUT:\n{res_test.stdout}\nSTDERR:\n{res_test.stderr}"
    assert "passed" in res_test.stdout

    # 5. Tripwire Invariant: remove compiled binary, assert tripwire fails
    for so in compiled_files:
        if so.is_file():
            so.unlink()

    res_tw_fail = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests/test_tripwire.py"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res_tw_fail.returncode != 0, "Tripwire unexpectedly passed with CFFI extension binary missing!"
