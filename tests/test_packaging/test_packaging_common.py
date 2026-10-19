from __future__ import annotations

from headerkit.ir import CType, Function, Parameter
from headerkit.packaging.common import (
    generate_pyproject_toml,
    generate_pytest_tripwire,
    generate_pytest_unit_tests,
)


def test_generate_pyproject_toml() -> None:
    content = generate_pyproject_toml(
        "my_pkg",
        version="0.2.0",
        description="Test pkg",
        build_requires=["setuptools>=61.0"],
        build_backend="setuptools.build_meta",
        dependencies=["cffi>=1.15.0"],
    )
    assert 'name = "my_pkg"' in content
    assert 'version = "0.2.0"' in content
    assert 'requires = ["setuptools>=61.0"]' in content
    assert 'dependencies = ["cffi>=1.15.0"]' in content


def test_generate_pytest_tripwire_ctypes() -> None:
    fn = Function(name="add", return_type=CType("int"), parameters=[Parameter("a", CType("int"))])
    content = generate_pytest_tripwire("my_pkg", [fn], loader_kind="ctypes_cdll")
    assert "test_native_library_loaded" in content
    assert "test_entrypoint_add" in content
    assert "my_pkg._lib is not None" in content or 'getattr(my_pkg, "_lib"' in content


def test_generate_pytest_unit_tests_wrapper_functions() -> None:
    fn = Function(name="add", return_type=CType("int"), parameters=[Parameter("a", CType("int"))])
    content = generate_pytest_unit_tests("my_pkg", [fn], binding_model="wrapper_functions")
    assert "test_add_callable_signature" in content or "test_add_wrapper_signature" in content
    assert "inspect.signature" in content
