"""Cython wheel packaging template and layout generator."""

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
from headerkit.writers.cython import CythonWriter


def generate_cython_wheel_layout(
    unit: SourceUnit,
    options: ScaffoldOptions | None = None,
) -> ProjectLayout:
    """Generate a complete Cython wheel package layout."""
    opts = options or ScaffoldOptions()
    pkg = opts.package_name or "bindings"
    validate_pkg_name(pkg)

    test_type = getattr(opts, "test_type", "both") or "both"
    version = getattr(opts, "version", "0.1.0") or "0.1.0"

    files: list[OutputFile] = []

    # 1. pyproject.toml
    extra_sections = textwrap.dedent(f"""\
        [tool.setuptools.packages.find]
        where = ["src"]

        [tool.setuptools.package-data]
        "{pkg}" = ["*.pxd"]
    """)
    pyproject = generate_pyproject_toml(
        pkg,
        version=version,
        description=f"Cython bindings for {pkg} packaged with HeaderKit",
        build_requires=["setuptools>=61.0", "Cython>=3.0.0", "wheel"],
        build_backend="setuptools.build_meta",
        extra_sections=extra_sections,
    )
    files.append(OutputFile(path="pyproject.toml", content=pyproject))

    # 2. setup.py
    setup_py = textwrap.dedent(f"""\
        from setuptools import Extension, setup
        from Cython.Build import cythonize

        extensions = [
            Extension(
                "{pkg}.{pkg}",
                sources=["src/{pkg}/{pkg}.pyx"],
                include_dirs=["."],
            ),
        ]

        setup(
            ext_modules=cythonize(extensions, language_level=3),
        )
    """)
    files.append(OutputFile(path="setup.py", content=setup_py))

    # 3. src/{pkg}/__init__.py
    init_py = textwrap.dedent(f"""\
        \"\"\"{pkg} package.\"\"\"
        from {pkg}.{pkg} import *

        __version__ = "{version}"
    """)
    files.append(OutputFile(path=f"src/{pkg}/__init__.py", content=init_py))

    # 4. src/{pkg}/_{pkg}.pxd
    writer = CythonWriter()
    pxd_content = writer.write(unit)
    files.append(OutputFile(path=f"src/{pkg}/_{pkg}.pxd", content=pxd_content))

    # 5. src/{pkg}/{pkg}.pyx
    funcs = [d for d in getattr(unit, "declarations", []) if isinstance(d, Function) and d.name]
    pyx_lines = [
        "# cython: language_level=3",
        f"cimport {pkg}._{pkg} as _c",
        "",
    ]
    for fn in funcs:
        param_names = [p.name or f"arg{idx}" for idx, p in enumerate(fn.parameters)]
        params_str = ", ".join(param_names)
        pyx_lines.extend(
            [
                f"def {fn.name}({params_str}):",
                f'    """Python wrapper for {fn.name}."""',
                f"    return _c.{fn.name}({params_str})",
                "",
            ]
        )
    if not funcs:
        pyx_lines.append("# No functions declared in source unit\npass\n")

    files.append(OutputFile(path=f"src/{pkg}/{pkg}.pyx", content="\n".join(pyx_lines).rstrip() + "\n"))

    # 6. README.md
    readme = textwrap.dedent(f"""\
        # {pkg}

        Cython extension module packaged as a distributable Python binary wheel.

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

    # 7. Tests
    if test_type in ("tripwire", "both"):
        tripwire = generate_pytest_tripwire(pkg, funcs, loader_kind="cython_extension")
        files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

    if test_type in ("unit", "both"):
        unit_test = generate_pytest_unit_tests(pkg, funcs, binding_model="wrapper_functions")
        files.append(OutputFile(path=f"tests/test_{pkg}.py", content=unit_test))

    return ProjectLayout(files=files)
