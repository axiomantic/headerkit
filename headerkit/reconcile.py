"""Topological AST reconciliation engine for multi-platform headers."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from headerkit.ir import Declaration, Header, SourceLocation, SourceUnit

TARGET_TO_PREDICATE: dict[str, PlatformCondition] = {}


@dataclass(frozen=True)
class PlatformCondition:
    """Platform condition predicate for code generation."""

    tags: frozenset[str]
    nim_predicate: str


TARGET_TO_PREDICATE["darwin"] = PlatformCondition(frozenset({"darwin", "macosx", "apple"}), "defined(macosx)")
TARGET_TO_PREDICATE["linux"] = PlatformCondition(frozenset({"linux"}), "defined(linux)")
TARGET_TO_PREDICATE["windows"] = PlatformCondition(frozenset({"windows", "msvc"}), "defined(windows)")


def get_platform_condition(target_or_os: str) -> PlatformCondition:
    """Get the canonical PlatformCondition for a given target triple or OS name."""
    lower = target_or_os.lower()
    if lower in TARGET_TO_PREDICATE:
        return TARGET_TO_PREDICATE[lower]
    if "darwin" in lower or "apple" in lower or "mac" in lower:
        return TARGET_TO_PREDICATE["darwin"]
    if "linux" in lower:
        return TARGET_TO_PREDICATE["linux"]
    if "windows" in lower or "win32" in lower or "msvc" in lower:
        return TARGET_TO_PREDICATE["windows"]
    return PlatformCondition(frozenset({lower}), f"defined({lower})")


@dataclass
class PlatformGuardedBlock:
    """A collection of platform-guarded declaration branches."""

    branches: list[tuple[PlatformCondition, list[Declaration]]]


@dataclass
class ReconciledUnit:
    """Topologically ordered and platform-reconciled unit."""

    path: str
    items: list[Declaration | PlatformGuardedBlock] = field(default_factory=list)


def strip_locations(obj: object) -> object:
    """Recursively strip SourceLocation from AST objects, dataclasses, and collections."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, SourceLocation):
        return None
    if isinstance(obj, list):
        return [strip_locations(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(strip_locations(item) for item in obj)
    if isinstance(obj, set):
        return {strip_locations(item) for item in obj}
    if isinstance(obj, dict):
        return {k: strip_locations(v) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        changes: dict[str, object] = {}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            if f.name == "location":
                changes["location"] = None
            else:
                changes[f.name] = strip_locations(val)
        return replace(obj, **changes)
    return obj


def declarations_equal(a: Declaration, b: Declaration) -> bool:
    """Report whether two declarations are structurally identical, ignoring SourceLocation recursively."""
    if type(a) is not type(b):
        return False
    try:
        a_clean = strip_locations(a)
        b_clean = strip_locations(b)
        return bool(a_clean == b_clean)
    except Exception:
        return False


def reconcile_units(units: Sequence[tuple[str, Header | SourceUnit]]) -> ReconciledUnit:
    """Reconcile multiple platform-tagged compilation units into a single ReconciledUnit.

    Preserves topological declaration sequence, emits common declarations unconditionally,
    and groups divergent declarations into PlatformGuardedBlock branches.
    """
    if not units:
        return ReconciledUnit(path="")

    path = units[0][1].path
    normalized: list[tuple[PlatformCondition, list[Declaration]]] = [
        (get_platform_condition(tag), list(unit.declarations)) for tag, unit in units
    ]

    if len(normalized) == 1:
        return ReconciledUnit(path=path, items=list(normalized[0][1]))

    # Find common anchors across all units
    # An anchor is an entry that appears in all units with identical structure and monotonic order
    first_decls = normalized[0][1]
    other_units_decls = [decls for _, decls in normalized[1:]]

    anchors_in_units: list[list[int]] = []  # List of [idx_0, idx_1, ..., idx_k-1]
    curr_search_indices = [0] * len(other_units_decls)

    for i, d0 in enumerate(first_decls):
        matched_indices: list[int] = []
        possible = True
        for u_idx, u_decls in enumerate(other_units_decls):
            found_idx = -1
            for j in range(curr_search_indices[u_idx], len(u_decls)):
                if declarations_equal(d0, u_decls[j]):
                    found_idx = j
                    break
            if found_idx == -1:
                possible = False
                break
            matched_indices.append(found_idx)

        if possible:
            anchors_in_units.append([i] + matched_indices)
            for u_idx, m_idx in enumerate(matched_indices):
                curr_search_indices[u_idx] = m_idx + 1

    # Now construct the reconciled sequence using slices between anchors
    reconciled_items: list[Declaration | PlatformGuardedBlock] = []
    prev_indices = [0] * len(normalized)

    for anchor_pos in anchors_in_units:
        # Check if any unit has declarations between prev_index and anchor_pos
        branches: list[tuple[PlatformCondition, list[Declaration]]] = []
        for u_idx, (cond, decls) in enumerate(normalized):
            end_idx = anchor_pos[u_idx]
            slice_decls = decls[prev_indices[u_idx] : end_idx]
            if slice_decls:
                branches.append((cond, slice_decls))
            prev_indices[u_idx] = end_idx + 1

        if branches:
            reconciled_items.append(PlatformGuardedBlock(branches=branches))

        # Emit the common anchor declaration unconditionally
        anchor_decl = normalized[0][1][anchor_pos[0]]
        reconciled_items.append(anchor_decl)

    # Check for remaining declarations after the last anchor
    trailing_branches: list[tuple[PlatformCondition, list[Declaration]]] = []
    for u_idx, (cond, decls) in enumerate(normalized):
        slice_decls = decls[prev_indices[u_idx] :]
        if slice_decls:
            trailing_branches.append((cond, slice_decls))

    if trailing_branches:
        reconciled_items.append(PlatformGuardedBlock(branches=trailing_branches))

    return ReconciledUnit(path=path, items=reconciled_items)
