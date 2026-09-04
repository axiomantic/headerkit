"""Packaging infrastructure and templates for compiling and distributing foreign extensions."""

from __future__ import annotations

from headerkit.packaging.nim import (
    generate_nim_cmake,
    generate_nim_pyproject,
    generate_nim_python_wrapper,
    generate_nim_source,
    generate_nim_wheel_layout,
)

__all__ = [
    "generate_nim_cmake",
    "generate_nim_pyproject",
    "generate_nim_python_wrapper",
    "generate_nim_source",
    "generate_nim_wheel_layout",
]
