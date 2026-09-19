"""ctypes wheel packaging template and layout generator."""

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
from headerkit.writers.ctypes import CtypesWriter


def generate_ctypes_wheel_layout(
    unit: SourceUnit,
    options: ScaffoldOptions | None = None,
) -> ProjectLayout:
    """Generate a complete ctypes wheel package layout."""
    opts = options or ScaffoldOptions()
    pkg = opts.package_name or "bindings"
    validate_pkg_name(pkg)

    test_type = getattr(opts, "test_type", "both") or "both"
    version = getattr(opts, "version", "0.1.0") or "0.1.0"
    pkg_upper = pkg.upper()

    files: list[OutputFile] = []

    # 1. pyproject.toml
    extra_sections = textwrap.dedent(f"""\
        [tool.setuptools.packages.find]
        where = ["src"]

        [tool.setuptools.package-data]
        "{pkg}" = ["*.so", "*.dylib", "*.dll"]
    """)
    pyproject = generate_pyproject_toml(
        pkg,
        version=version,
        description=f"ctypes bindings for {pkg} packaged with HeaderKit",
        build_requires=["setuptools>=61.0", "wheel"],
        build_backend="setuptools.build_meta",
        dependencies=[],
        extra_sections=extra_sections,
    )
    files.append(OutputFile(path="pyproject.toml", content=pyproject))

    # 2. src/{pkg}/_loader.py
    loader_py = textwrap.dedent(f"""\
        \"\"\"Dynamic library loader for {pkg}.\"\"\"

        from __future__ import annotations

        import ctypes
        import ctypes.util
        import os
        import sys
        from pathlib import Path


        def load_library() -> ctypes.CDLL | None:
            \"\"\"Locate and load the native shared library.\"\"\"
            pkg_dir = Path(__file__).resolve().parent

            # 1. Check environment variable override
            env_path = os.environ.get("{pkg_upper}_LIBRARY_PATH")
            if env_path and os.path.isfile(env_path):
                try:
                    return ctypes.CDLL(env_path)
                except OSError:
                    pass

            # 2. Check bundled package directory
            if sys.platform == "darwin":
                candidates = [f"lib{pkg}.dylib", f"{pkg}.dylib", f"lib{pkg}.so"]
            elif sys.platform == "win32":
                candidates = [f"{pkg}.dll", f"lib{pkg}.dll"]
            else:
                candidates = [f"lib{pkg}.so", f"{pkg}.so"]

            for candidate in candidates:
                candidate_path = pkg_dir / candidate
                if candidate_path.is_file():
                    try:
                        return ctypes.CDLL(str(candidate_path))
                    except OSError:
                        pass

            # 3. Check system path via find_library
            found = ctypes.util.find_library("{pkg}")
            if found:
                try:
                    return ctypes.CDLL(found)
                except OSError:
                    pass

            return None


        _lib = load_library()
    """)
    files.append(OutputFile(path=f"src/{pkg}/_loader.py", content=loader_py))

    # 3. src/{pkg}/_bindings.py
    writer = CtypesWriter()
    bindings_code = writer.write(unit)
    # Ensure bindings uses _lib from _loader
    if "_lib = None" in bindings_code:
        bindings_code = bindings_code.replace(
            "_lib = None",
            f"from {pkg}._loader import _lib",
        )
    elif "import ctypes" in bindings_code:
        bindings_code = f"from {pkg}._loader import _lib\n" + bindings_code

    files.append(OutputFile(path=f"src/{pkg}/_bindings.py", content=bindings_code))

    # 4. src/{pkg}/__init__.py
    init_py = textwrap.dedent(f"""\
        \"\"\"{pkg} ctypes bindings.\"\"\"

        from {pkg}._bindings import *
        from {pkg}._loader import _lib

        __version__ = "{version}"
    """)
    files.append(OutputFile(path=f"src/{pkg}/__init__.py", content=init_py))

    # 5. README.md
    readme = textwrap.dedent(f"""\
        # {pkg}

        ctypes Python bindings for {pkg}.

        ## Installation

        ```bash
        pip install .
        ```
    """)
    files.append(OutputFile(path="README.md", content=readme))

    # 6. Tests
    funcs = [d for d in getattr(unit, "declarations", []) if isinstance(d, Function) and d.name]
    if test_type in ("tripwire", "both"):
        tripwire = generate_pytest_tripwire(pkg, funcs, loader_kind="ctypes_cdll", library_var="_lib")
        files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

    if test_type in ("unit", "both"):
        unit_test = generate_pytest_unit_tests(pkg, funcs, binding_model="ctypes_lib")
        files.append(OutputFile(path=f"tests/test_{pkg}.py", content=unit_test))

    return ProjectLayout(files=files)
