"""Packaging infrastructure and templates for compiling and distributing foreign extensions."""

from __future__ import annotations

from headerkit.packaging.cffi import generate_cffi_wheel_layout
from headerkit.packaging.common import (
    generate_pyproject_toml,
    generate_pytest_tripwire,
    generate_pytest_unit_tests,
)
from headerkit.packaging.cython import generate_cython_wheel_layout
from headerkit.packaging.nim import (
    generate_nim_cmake,
    generate_nim_pyproject,
    generate_nim_python_wrapper,
    generate_nim_source,
    generate_nim_wheel_layout,
)

__all__ = [
    "generate_cffi_wheel_layout",
    "generate_cython_wheel_layout",
    "generate_nim_cmake",
    "generate_nim_pyproject",
    "generate_nim_python_wrapper",
    "generate_nim_source",
    "generate_nim_wheel_layout",
    "generate_pyproject_toml",
    "generate_pytest_tripwire",
    "generate_pytest_unit_tests",
]
