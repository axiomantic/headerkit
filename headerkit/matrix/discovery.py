"""Automated discovery of matrix dimensions from baseline IR inspection."""

from __future__ import annotations

from collections.abc import Sequence

from headerkit.ir import Constant, MacroFunction, SourceUnit
from headerkit.matrix.matrix import ConfigMatrix

VERSION_MAJOR_PATTERNS = ("_VERSION_MAJOR", "_VER_MAJOR")
VERSION_MINOR_PATTERNS = ("_VERSION_MINOR", "_VER_MINOR")
VERSION_PATCH_PATTERNS = ("_VERSION_PATCH", "_VER_PATCH", "_VERSION_REVISION")

FEATURE_PREFIXES = ("ENABLE_", "USE_", "HAS_", "WITH_", "DISABLE_", "NO_")


def discover_dimensions(
    unit: SourceUnit,
    default_targets: tuple[str, ...] = (),
    max_features: int = 2,
    candidate_features: Sequence[str] | None = None,
) -> ConfigMatrix:
    """Inspects AST declarations to automatically construct a ConfigMatrix."""
    matrix = ConfigMatrix()

    if default_targets:
        matrix.add_dimension("target", default_targets, dimension_type="target")

    major = minor = patch = None
    api_versions: list[int] = []

    for decl in unit.declarations:
        if isinstance(decl, Constant):
            name = decl.name
            val = decl.evaluated_value if decl.evaluated_value is not None else decl.value

            if any(name.endswith(p) for p in VERSION_MAJOR_PATTERNS) and isinstance(val, int):
                major = val
            elif any(name.endswith(p) for p in VERSION_MINOR_PATTERNS) and isinstance(val, int):
                minor = val
            elif any(name.endswith(p) for p in VERSION_PATCH_PATTERNS) and isinstance(val, int):
                patch = val
            elif "API_VERSION" in name or "VERSION_NUMBER" in name:
                if isinstance(val, int):
                    api_versions.append(val)

    if major is not None:
        v_tuple = (major, minor or 0, patch or 0)
        matrix.add_dimension("version", [v_tuple], dimension_type="version")
    elif api_versions:
        matrix.add_dimension("version", sorted(set(api_versions)), dimension_type="version")

    discovered_features: set[str] = set()
    for decl in unit.declarations:
        if isinstance(decl, (Constant, MacroFunction)):
            name = decl.name
            for prefix in FEATURE_PREFIXES:
                if prefix in name:
                    discovered_features.add(name)
                    break

    active_features: list[str] = []
    if candidate_features is not None:
        active_features = [f for f in candidate_features if f in discovered_features]
    else:
        active_features = sorted(discovered_features)[:max_features]

    for feat in active_features:
        matrix.add_dimension(feat, (True, False), dimension_type="feature")

    return matrix
