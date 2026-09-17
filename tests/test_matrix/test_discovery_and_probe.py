"""Tests for automated dimension discovery and compiler probing with sysroot warnings."""

from __future__ import annotations

import tempfile

import pytest

from headerkit.ir import Constant, SourceUnit
from headerkit.matrix.discovery import discover_dimensions
from headerkit.matrix.models import ConfigCondition
from headerkit.matrix.probe import ProbeOptions, SysrootWarning, probe_configuration


def test_discover_dimensions_version_and_features() -> None:
    c_maj = Constant(name="LIB_VERSION_MAJOR", type="int", value=2)
    c_min = Constant(name="LIB_VERSION_MINOR", type="int", value=1)
    c_pat = Constant(name="LIB_VERSION_PATCH", type="int", value=0)
    c_f1 = Constant(name="ENABLE_FEATURE_A", type="int", value=1)
    c_f2 = Constant(name="ENABLE_FEATURE_B", type="int", value=1)
    c_f3 = Constant(name="ENABLE_FEATURE_C", type="int", value=1)

    unit = SourceUnit(path="lib.h", declarations=[c_maj, c_min, c_pat, c_f1, c_f2, c_f3])

    matrix = discover_dimensions(unit, default_targets=("x86_64-apple-darwin",), max_features=2)
    dim_names = [d.name for d in matrix.dimensions]

    assert "target" in dim_names
    assert "version" in dim_names
    feature_dims = [d.name for d in matrix.dimensions if d.dimension_type == "feature"]
    assert len(feature_dims) == 2


def test_discover_dimensions_api_version() -> None:
    c1 = Constant(name="SQLITE_API_VERSION", type="int", value=33500)
    c2 = Constant(name="SQLITE_VERSION_NUMBER", type="int", value=34200)
    unit = SourceUnit(path="sqlite.h", declarations=[c1, c2])

    matrix = discover_dimensions(unit)
    v_dim = next((d for d in matrix.dimensions if d.name == "version"), None)
    assert v_dim is not None
    assert v_dim.values == (33500, 34200)


def test_probe_configuration_sysroot_warning() -> None:
    cond = ConfigCondition({"target": "x86_64-pc-windows-msvc"})
    opts = ProbeOptions(sysroot=None, allow_missing_sysroot=True)

    with (
        pytest.warns(SysrootWarning, match="Probing foreign target"),
        tempfile.NamedTemporaryFile(suffix=".h", mode="w", delete=False) as f,
    ):
        f.write("typedef int my_handle;\n")
        f.flush()
        unit = probe_configuration(f.name, cond, options=opts)
        assert isinstance(unit, SourceUnit)
