"""Tests for ConfigCondition, MatrixDimension, ConfigMatrix, and explosion guards."""

from __future__ import annotations

import pytest

from headerkit.matrix.matrix import (
    ConfigMatrix,
    MatrixExplosionError,
)
from headerkit.matrix.models import ConfigCondition


def test_config_condition_hash_and_immutability() -> None:
    c1 = ConfigCondition({"target": "x86_64-apple-darwin", "version": (1, 0, 0)})
    c2 = ConfigCondition({"version": (1, 0, 0), "target": "x86_64-apple-darwin"})

    assert c1 == c2
    assert hash(c1) == hash(c2)
    assert c1.target == "x86_64-apple-darwin"
    assert c1.version == (1, 0, 0)

    with pytest.raises(TypeError):
        c1["target"] = "something_else"  # type: ignore[index]


def test_config_condition_to_compiler_flags() -> None:
    c = ConfigCondition(
        {
            "target": "x86_64-apple-darwin",
            "ENABLE_WASAPI": False,
            "ENABLE_ALSA": True,
            "BUFFER_SIZE": 1024,
        }
    )
    flags = c.to_compiler_flags()
    assert "--target=x86_64-apple-darwin" in flags
    assert "-UENABLE_WASAPI" in flags
    assert "-DENABLE_ALSA=1" in flags
    assert "-DBUFFER_SIZE=1024" in flags


def test_config_matrix_cartesian_resolution_and_constraints() -> None:
    matrix = ConfigMatrix()
    matrix.add_dimension("target", ["darwin", "linux"], dimension_type="target")
    matrix.add_dimension("MA_ENABLE_ALSA", [True, False], dimension_type="feature")

    # ALSA only on linux
    matrix.add_constraint(lambda c: not (c["target"] == "darwin" and c.get("MA_ENABLE_ALSA") is True))

    resolved = matrix.resolve()
    assert len(resolved) == 3
    for c in resolved:
        if c["target"] == "darwin":
            assert c.get("MA_ENABLE_ALSA") is False


def test_config_matrix_explosion_guard() -> None:
    matrix = ConfigMatrix(max_configurations=4)
    matrix.add_dimension("d1", [1, 2], dimension_type="custom")
    matrix.add_dimension("d2", [1, 2], dimension_type="custom")
    matrix.add_dimension("d3", [1, 2], dimension_type="custom")

    with pytest.raises(MatrixExplosionError, match="exceeds safe threshold"):
        matrix.resolve()

    matrix.allow_unbounded = True
    assert len(matrix.resolve()) == 8
