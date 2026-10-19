"""Parameterized configuration matrix and cross-target probing."""

from headerkit.matrix.discovery import discover_dimensions
from headerkit.matrix.manifest import (
    COMMENT_PREFIXES,
    MalformedOverrideError,
    MatrixManifest,
    emit_manifest_comment,
    extract_manifest,
    extract_overrides,
    inject_overrides,
)
from headerkit.matrix.matrix import (
    MAX_MATRIX_CONFIGURATIONS,
    ConfigMatrix,
    MatrixExplosionError,
)
from headerkit.matrix.models import ConfigCondition, MatrixDimension
from headerkit.matrix.probe import ProbeOptions, SysrootWarning, probe_configuration

__all__ = [
    "COMMENT_PREFIXES",
    "MAX_MATRIX_CONFIGURATIONS",
    "ConfigCondition",
    "ConfigMatrix",
    "MalformedOverrideError",
    "MatrixDimension",
    "MatrixExplosionError",
    "MatrixManifest",
    "ProbeOptions",
    "SysrootWarning",
    "discover_dimensions",
    "emit_manifest_comment",
    "extract_manifest",
    "extract_overrides",
    "inject_overrides",
    "probe_configuration",
]
