"""Parameterized configuration matrix and cross-target probing."""

from headerkit.matrix.matrix import (
    MAX_MATRIX_CONFIGURATIONS,
    ConfigMatrix,
    MatrixExplosionError,
)
from headerkit.matrix.models import ConfigCondition, MatrixDimension

__all__ = [
    "MAX_MATRIX_CONFIGURATIONS",
    "ConfigCondition",
    "ConfigMatrix",
    "MatrixDimension",
    "MatrixExplosionError",
]
