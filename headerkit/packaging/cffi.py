"""CFFI wheel packaging template and layout generator."""

from __future__ import annotations

import textwrap

from headerkit.ir import Function, SourceUnit
from headerkit.packaging.common import (
    generate_pyproject_toml,
    generate_pytest_tripwire,
    generate_pytest_unit_tests,
    validate_pkg_name,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.cffi import CffiWriter


def generate_cffi_wheel_layout(
    unit: SourceUnit,
    options: ScaffoldOptions | None = None,
) -> ProjectLayout:
    """Generate a complete CFFI wheel package layout."""
    opts = options or ScaffoldOptions()
    pkg = opts.package_name or "bindings"
    validate_pkg_name(pkg)

    test_type = getattr(opts, "test_type", "both") or "both"
    version = getattr(opts, "version", "0.1.0") or "0.1.0"
    header_path = getattr(unit, "path", None) or f"{pkg}.h"

    files: list[OutputFile] = []

    # 1. pyproject.toml
    extra_sections = textwrap.dedent("""\
        [tool.setuptools.packages.find]
        where = ["src"]
    """)
    pyproject = generate_pyproject_toml(
        pkg,
        version=version,
        description=f"CFFI bindings for {pkg} packaged with HeaderKit",
        build_requires=["setuptools>=61.0", "cffi>=1.15.0", "wheel"],
        build_backend="setuptools.build_meta",
        dependencies=["cffi>=1.15.0"],
        extra_sections=extra_sections,
    )
    files.append(OutputFile(path="pyproject.toml", content=pyproject))

    # 2. setup.py
    setup_py = textwrap.dedent("""\
        from setuptools import setup

        setup(
            cffi_modules=["build_ffi.py:ffibuilder"],
        )
    """)
    files.append(OutputFile(path="setup.py", content=setup_py))

    # 3. build_ffi.py
    writer = CffiWriter()
    cdef_code = writer.write(unit)
    build_ffi = textwrap.dedent(f"""\
        from cffi import FFI

        ffibuilder = FFI()
        ffibuilder.cdef(\"\"\"
        {cdef_code.strip()}
        \"\"\")
        ffibuilder.set_source(
            "{pkg}._cffi_backend",
            '#include "{header_path}"',
        )

        if __name__ == "__main__":
            ffibuilder.compile(verbose=True)
    """)
    files.append(OutputFile(path="build_ffi.py", content=build_ffi))

    # 4. src/{pkg}/__init__.py
    init_py = textwrap.dedent(f"""\
        \"\"\"{pkg} CFFI bindings.\"\"\"
        try:
            from {pkg}._cffi_backend import ffi, lib
        except ImportError:
            ffi = None
            lib = None

        __all__ = ["ffi", "lib"]
    """)
    files.append(OutputFile(path=f"src/{pkg}/__init__.py", content=init_py))

    # 5. README.md
    readme = textwrap.dedent(f"""\
        # {pkg}

        CFFI out-of-line extension module packaged as a distributable Python binary wheel.

        ## Building the Wheel

        ```bash
        pip install build
        python -m build --wheel
        ```

        ## Installation

        ```bash
        pip install .
        ```
    """)
    files.append(OutputFile(path="README.md", content=readme))

    # 6. Tests
    funcs = [d for d in getattr(unit, "declarations", []) if isinstance(d, Function) and d.name]
    if test_type in ("tripwire", "both"):
        tripwire = generate_pytest_tripwire(pkg, funcs, loader_kind="cffi_backend")
        files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

    if test_type in ("unit", "both"):
        unit_test = generate_pytest_unit_tests(pkg, funcs, binding_model="cffi_lib")
        files.append(OutputFile(path=f"tests/test_{pkg}.py", content=unit_test))

    return ProjectLayout(files=files)
