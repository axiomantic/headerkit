"""Parameterized configuration matrix and cross-target probing."""

from headerkit.matrix.discovery import discover_dimensions
from headerkit.matrix.matrix import (
    MAX_MATRIX_CONFIGURATIONS,
    ConfigMatrix,
    MatrixExplosionError,
)
from headerkit.matrix.models import ConfigCondition, MatrixDimension
from headerkit.matrix.probe import ProbeOptions, SysrootWarning, probe_configuration

__all__ = [
    "MAX_MATRIX_CONFIGURATIONS",
    "ConfigCondition",
    "ConfigMatrix",
    "MatrixDimension",
    "MatrixExplosionError",
    "ProbeOptions",
    "SysrootWarning",
    "discover_dimensions",
    "probe_configuration",
]
