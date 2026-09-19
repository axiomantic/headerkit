"""Shared packaging primitives and test generators for turnkey binary wheel layouts."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from headerkit.ir import Function


def validate_pkg_name(pkg: str) -> None:
    """Validate package name according to PEP 508 / identifier rules."""
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", pkg):
        msg = f"Invalid package name '{pkg}': must be a valid Python identifier"
        raise ValueError(msg)


def generate_pyproject_toml(
    pkg: str,
    *,
    version: str = "0.1.0",
    description: str = "",
    build_requires: Sequence[str],
    build_backend: str,
    dependencies: Sequence[str] = (),
    extra_sections: str = "",
) -> str:
    """Generate pyproject.toml compliant with PEP 517 / PEP 621."""
    validate_pkg_name(pkg)
    desc = description or f"Bindings for {pkg} packaged with HeaderKit"
    reqs_str = ", ".join(f'"{r}"' for r in build_requires)
    deps_str = ", ".join(f'"{d}"' for d in dependencies)

    lines = [
        "[build-system]",
        f"requires = [{reqs_str}]",
        f'build-backend = "{build_backend}"',
        "",
        "[project]",
        f'name = "{pkg}"',
        f'version = "{version}"',
        f'description = "{desc}"',
        'readme = "README.md"',
        'requires-python = ">=3.10"',
    ]
    if dependencies:
        lines.append(f"dependencies = [{deps_str}]")

    if extra_sections.strip():
        lines.append("")
        lines.append(extra_sections.strip())

    return "\n".join(lines).rstrip() + "\n"


def generate_pytest_tripwire(
    pkg: str,
    functions: Sequence[Function],
    *,
    loader_kind: Literal["ctypes_cdll", "cython_extension", "cffi_backend"],
    module_target: str | None = None,
    library_var: str = "_lib",
) -> str:
    """Generate tests/test_tripwire.py ensuring native binary and entrypoint resolution."""
    validate_pkg_name(pkg)
    mod = module_target or pkg
    funcs = [f for f in functions if f.name]

    lines = [
        '"""Tripwire test verifying native dynamic library resolution and ABI entrypoints."""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
    ]

    if loader_kind == "ctypes_cdll":
        lines.extend(
            [
                f"import {mod}",
                "",
                "",
                "@pytest.mark.tripwire",
                "def test_native_library_loaded() -> None:",
                '    """Tripwire: verify that the compiled native dynamic library is present and loaded."""',
                f"    assert {mod}.{library_var} is not None, \"Native dynamic library '{pkg}' not found in package directory\"",
                "",
            ]
        )
        for fn in funcs:
            lines.extend(
                [
                    "",
                    "@pytest.mark.tripwire",
                    f"def test_entrypoint_{fn.name}() -> None:",
                    f'    assert hasattr({mod}.{library_var}, "{fn.name}"), "Entry point \'{fn.name}\' missing from compiled library"',
                    f'    assert callable(getattr({mod}, "{fn.name}"))',
                ]
            )

    elif loader_kind == "cython_extension":
        lines.extend(
            [
                f"import {pkg}",
                f"from {pkg} import {pkg} as _ext",
                "",
                "",
                "@pytest.mark.tripwire",
                "def test_cython_binary_extension_loaded() -> None:",
                '    """Tripwire: verify that the compiled Cython binary extension (.so/.pyd) is loaded."""',
                '    assert hasattr(_ext, "__file__") and _ext.__file__ is not None, "Extension has no __file__"',
                "    file_path = _ext.__file__",
                '    valid_exts = (".so", ".pyd", ".dylib")',
                '    assert file_path.endswith(valid_exts), f"Not a compiled binary extension: {file_path}"',
                "",
            ]
        )
        for fn in funcs:
            lines.extend(
                [
                    "",
                    "@pytest.mark.tripwire",
                    f"def test_cython_entrypoint_{fn.name}() -> None:",
                    f'    assert hasattr(_ext, "{fn.name}"), "Entry point \'{fn.name}\' missing from Cython extension"',
                    f'    assert callable(getattr(_ext, "{fn.name}"))',
                ]
            )

    elif loader_kind == "cffi_backend":
        lines.extend(
            [
                f"import {pkg}",
                "",
                "",
                "@pytest.mark.tripwire",
                "def test_cffi_native_backend_loaded() -> None:",
                '    """Tripwire: verify CFFI compiled C extension is present and loaded."""',
                f'    lib = getattr({pkg}, "lib", None)',
                "    assert lib is not None, \"CFFI native backend 'lib' failed to load\"",
                "",
            ]
        )
        for fn in funcs:
            lines.extend(
                [
                    "",
                    "@pytest.mark.tripwire",
                    f"def test_cffi_entrypoint_{fn.name}() -> None:",
                    f'    lib = getattr({pkg}, "lib", None)',
                    '    assert lib is not None, "CFFI lib not loaded"',
                    f'    assert hasattr(lib, "{fn.name}"), "Entry point \'{fn.name}\' missing from CFFI extension"',
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def generate_pytest_unit_tests(
    pkg: str,
    functions: Sequence[Function],
    *,
    binding_model: Literal["wrapper_functions", "ctypes_lib", "cffi_lib"] = "wrapper_functions",
    extra_init_callables: Sequence[str] = (),
) -> str:
    """Generate tests/test_{pkg}.py verifying wrapper callables and signatures."""
    validate_pkg_name(pkg)
    funcs = [f for f in functions if f.name]

    lines = [
        f'"""Unit tests for {pkg} Python wrapper."""',
        "",
        "from __future__ import annotations",
        "",
        "import inspect",
        f"import {pkg}",
        "",
        "",
        "def test_module_structure() -> None:",
        f"    assert inspect.ismodule({pkg})",
    ]
    for init_fn in extra_init_callables:
        lines.extend(
            [
                f'    assert hasattr({pkg}, "{init_fn}")',
                f'    assert callable(getattr({pkg}, "{init_fn}"))',
            ]
        )

    for fn in funcs:
        param_names = [p.name or f"arg{idx}" for idx, p in enumerate(fn.parameters)]
        if binding_model == "wrapper_functions":
            lines.extend(
                [
                    "",
                    "",
                    f"def test_{fn.name}_wrapper_signature() -> None:",
                    f'    assert hasattr({pkg}, "{fn.name}")',
                    f'    fn_obj = getattr({pkg}, "{fn.name}")',
                    "    assert callable(fn_obj)",
                    "    sig = inspect.signature(fn_obj)",
                    f"    assert len(sig.parameters) == {len(fn.parameters)}",
                    f"    assert list(sig.parameters.keys()) == {repr(param_names)}",
                ]
            )
        elif binding_model == "ctypes_lib":
            lines.extend(
                [
                    "",
                    "",
                    f"def test_{fn.name}_binding() -> None:",
                    f'    assert hasattr({pkg}, "{fn.name}")',
                    f'    fn_obj = getattr({pkg}, "{fn.name}")',
                    "    assert callable(fn_obj)",
                    f'    assert hasattr({pkg}._lib, "{fn.name}")',
                ]
            )
        elif binding_model == "cffi_lib":
            lines.extend(
                [
                    "",
                    "",
                    f"def test_{fn.name}_cffi_entrypoint() -> None:",
                    f'    assert hasattr({pkg}.lib, "{fn.name}"), "Expected entrypoint \'{fn.name}\' in {pkg}.lib"',
                    f'    entry = getattr({pkg}.lib, "{fn.name}")',
                    "    assert entry is not None",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"
