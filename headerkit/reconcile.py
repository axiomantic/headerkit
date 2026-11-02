"""Topological AST reconciliation engine for multi-platform headers."""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from headerkit.ir import Declaration, Header, SourceLocation, SourceUnit
from headerkit.matrix.matrix import ConfigMatrix
from headerkit.matrix.models import ConfigCondition

TARGET_TO_PREDICATE: dict[str, PlatformCondition] = {}


@dataclass(frozen=True, slots=True)
class PlatformCondition:
    """Language-agnostic platform condition represented as a DNF boolean AST of platform/architecture tags."""

    tags: frozenset[str]
    terms: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if not self.terms and self.tags:
            object.__setattr__(self, "terms", tuple((t,) for t in sorted(self.tags)))


TARGET_TO_PREDICATE["darwin"] = PlatformCondition(frozenset({"darwin", "macosx", "apple"}), (("darwin",),))
TARGET_TO_PREDICATE["linux"] = PlatformCondition(frozenset({"linux"}), (("linux",),))
TARGET_TO_PREDICATE["windows"] = PlatformCondition(frozenset({"windows", "msvc"}), (("windows",),))


def canonical_platform_tag(triple: str) -> str:
    """Normalize a target triple or OS string to a canonical OS family tag."""
    lower = triple.lower()
    if "darwin" in lower or "apple" in lower or "mac" in lower:
        return "darwin"
    if "linux" in lower:
        return "linux"
    if "windows" in lower or "msvc" in lower or "win32" in lower:
        return "windows"
    return "posix"


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
    return PlatformCondition(frozenset({lower}), ((lower,),))


@dataclass(slots=True)
class PlatformGuardedBlock:
    """A collection of platform-guarded declaration branches."""

    branches: list[tuple[PlatformCondition, list[Declaration]]]


@dataclass(frozen=True, slots=True)
class MatrixCondition:
    """Language-agnostic multi-dimensional boolean condition AST for guarded blocks."""

    raw_conditions: tuple[ConfigCondition, ...] = ()
    platforms: tuple[str, ...] = ()
    version_op: str | None = None
    version_min: Any = None
    version_max: Any = None
    feature_implicants: tuple[tuple[tuple[str, bool], ...], ...] = ()

    @property
    def is_tautology(self) -> bool:
        """Return True if all dimensions reduce to an unconditional tautology."""
        return not self.platforms and self.version_op is None and not self.feature_implicants


@dataclass(slots=True)
class MatrixGuardedBlock:
    """A block of declaration branches guarded by multi-dimensional conditions."""

    branches: list[tuple[MatrixCondition, list[Declaration]]]


@dataclass(slots=True)
class ReconciledUnit:
    """Topologically ordered and platform-reconciled unit."""

    path: str
    items: list[Declaration | PlatformGuardedBlock] = field(default_factory=list)


@dataclass(slots=True)
class MultiDimensionalReconciledUnit:
    """Topologically ordered unit containing common declarations and matrix-guarded blocks."""

    path: str
    items: list[Declaration | MatrixGuardedBlock] = field(default_factory=list)


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


def quine_mccluskey_minimize(
    configs: Sequence[ConfigCondition],
    bool_features: Sequence[str],
) -> list[tuple[bool | None, ...]]:
    """Minimizes boolean feature terms using the Quine-McCluskey prime implicant algorithm."""
    if not bool_features or not configs:
        return []

    minterms: set[tuple[bool | None, ...]] = set()
    for c in configs:
        t: tuple[bool | None, ...] = tuple(bool(c.get(f, False)) for f in bool_features)
        minterms.add(t)

    current_terms = minterms
    prime_implicants: set[tuple[bool | None, ...]] = set()

    while current_terms:
        next_terms: set[tuple[bool | None, ...]] = set()
        combined: set[tuple[bool | None, ...]] = set()
        terms_list = list(current_terms)

        for i in range(len(terms_list)):
            for j in range(i + 1, len(terms_list)):
                t1, t2 = terms_list[i], terms_list[j]
                diff_idx = -1
                match = True
                for k in range(len(t1)):
                    if t1[k] != t2[k]:
                        if diff_idx == -1 and t1[k] is not None and t2[k] is not None:
                            diff_idx = k
                        else:
                            match = False
                            break
                if match and diff_idx != -1:
                    new_term = list(t1)
                    new_term[diff_idx] = None
                    next_terms.add(tuple(new_term))
                    combined.add(t1)
                    combined.add(t2)

        for t in current_terms:
            if t not in combined:
                prime_implicants.add(t)

        current_terms = next_terms

    return sorted(prime_implicants, key=lambda term: (term.count(None), str(term)))


def minimize_conditions(
    configs: Sequence[ConfigCondition],
    domain_matrix: dict[str, set[Any]],
) -> MatrixCondition:
    """Algebraically reduce configuration conditions into a language-agnostic MatrixCondition AST."""
    if not configs:
        return MatrixCondition()

    active_dimensions: set[str] = set()
    for dim, all_values in domain_matrix.items():
        observed_values = {c[dim] for c in configs if dim in c}
        if observed_values != all_values and len(all_values) > 1:
            active_dimensions.add(dim)

    if not active_dimensions:
        return MatrixCondition(raw_conditions=tuple(configs))

    platforms: tuple[str, ...] = ()
    raw_platforms = {str(c["target"]) for c in configs if "target" in c}
    if "target" in active_dimensions and raw_platforms:
        platforms = tuple(sorted({canonical_platform_tag(p) for p in raw_platforms}))

    version_op: str | None = None
    version_min: Any = None
    version_max: Any = None
    if "version" in active_dimensions:
        obs_versions = sorted({c["version"] for c in configs if "version" in c})
        all_versions = sorted(domain_matrix.get("version", set()))
        if obs_versions:
            min_v = obs_versions[0]
            max_v = obs_versions[-1]
            if len(obs_versions) == 1:
                version_op = "=="
                version_min = min_v
                version_max = min_v
            elif min_v in all_versions and obs_versions == all_versions[all_versions.index(min_v) :]:
                version_op = ">="
                version_min = min_v
            else:
                version_op = "range"
                version_min = min_v
                version_max = max_v

    bool_features = sorted(
        dim
        for dim in active_dimensions
        if dim not in ("target", "version") and all(isinstance(v, bool) for v in domain_matrix[dim])
    )
    feature_implicants: tuple[tuple[tuple[str, bool], ...], ...] = ()
    if bool_features:
        implicants = quine_mccluskey_minimize(configs, bool_features)
        imp_list: list[tuple[tuple[str, bool], ...]] = []
        for imp in implicants:
            term = tuple((feat, val) for feat, val in zip(bool_features, imp, strict=True) if val is not None)
            if term:
                imp_list.append(term)
        feature_implicants = tuple(imp_list)

    return MatrixCondition(
        raw_conditions=tuple(configs),
        platforms=platforms,
        version_op=version_op,
        version_min=version_min,
        version_max=version_max,
        feature_implicants=feature_implicants,
    )


def reconcile_matrix(
    matrix_units: Sequence[tuple[ConfigCondition, SourceUnit]],
    matrix: ConfigMatrix | None = None,
) -> MultiDimensionalReconciledUnit:
    """Reconciles N compilation units across a configuration matrix."""
    if not matrix_units:
        return MultiDimensionalReconciledUnit(path="")

    path = matrix_units[0][1].path
    normalized: list[tuple[ConfigCondition, list[Declaration]]] = [
        (cond, list(unit.declarations)) for cond, unit in matrix_units
    ]

    if len(normalized) == 1:
        return MultiDimensionalReconciledUnit(path=path, items=list(normalized[0][1]))

    domain_matrix: dict[str, set[Any]] = defaultdict(set)
    if matrix is not None:
        for dim in matrix.dimensions:
            domain_matrix[dim.name] = set(dim.values)
    else:
        for cond, _ in normalized:
            for k, v in cond.items():
                domain_matrix[k].add(v)

    first_decls = normalized[0][1]
    other_units_decls = [decls for _, decls in normalized[1:]]

    anchors_in_units: list[list[int]] = []
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

    reconciled_items: list[Declaration | MatrixGuardedBlock] = []
    prev_indices = [0] * len(normalized)

    def process_interval(end_indices: list[int]) -> None:
        slices: list[tuple[ConfigCondition, list[Declaration]]] = []
        for u_idx, (cond, decls) in enumerate(normalized):
            end_idx = end_indices[u_idx]
            slice_decls = decls[prev_indices[u_idx] : end_idx]
            prev_indices[u_idx] = end_idx + 1 if end_idx < len(decls) else end_idx
            if slice_decls:
                slices.append((cond, slice_decls))

        if not slices:
            return

        equiv_classes: list[tuple[list[ConfigCondition], list[Declaration]]] = []
        for cond, decls in slices:
            matched = False
            for class_configs, class_decls in equiv_classes:
                if len(decls) == len(class_decls) and all(
                    declarations_equal(d1, d2) for d1, d2 in zip(decls, class_decls, strict=True)
                ):
                    class_configs.append(cond)
                    matched = True
                    break
            if not matched:
                equiv_classes.append(([cond], decls))

        if len(equiv_classes) == 1 and len(equiv_classes[0][0]) == len(normalized):
            reconciled_items.extend(equiv_classes[0][1])
            return

        branches: list[tuple[MatrixCondition, list[Declaration]]] = []
        for class_configs, class_decls in equiv_classes:
            mat_cond = minimize_conditions(class_configs, domain_matrix)
            branches.append((mat_cond, class_decls))

        reconciled_items.append(MatrixGuardedBlock(branches=branches))

    for anchor_pos in anchors_in_units:
        process_interval(anchor_pos)
        reconciled_items.append(normalized[0][1][anchor_pos[0]])

    process_interval([len(decls) for _, decls in normalized])

    return MultiDimensionalReconciledUnit(path=path, items=reconciled_items)


def reconcile_units(units: Sequence[tuple[str, Header | SourceUnit]]) -> ReconciledUnit:
    """Reconcile multiple platform-tagged compilation units into a single ReconciledUnit.

    Preserves topological declaration sequence, emits common declarations unconditionally,
    and groups divergent declarations into PlatformGuardedBlock branches.
    Projects 1D platform inputs onto reconcile_matrix.
    """
    if not units:
        return ReconciledUnit(path="")

    targets = tuple(tag for tag, _ in units)
    matrix = ConfigMatrix()
    matrix.add_dimension("target", targets, dimension_type="target")

    matrix_units: list[tuple[ConfigCondition, SourceUnit]] = []
    for tag, u in units:
        if isinstance(u, Header):
            su = SourceUnit(path=u.path, declarations=list(u.declarations))
        else:
            su = u
        matrix_units.append((ConfigCondition({"target": tag}), su))

    md_unit = reconcile_matrix(matrix_units, matrix=matrix)

    items: list[Declaration | PlatformGuardedBlock] = []
    for item in md_unit.items:
        if isinstance(item, MatrixGuardedBlock):
            branches: list[tuple[PlatformCondition, list[Declaration]]] = []
            for cond, decls in item.branches:
                if len(cond.raw_conditions) == 1:
                    raw_c = cond.raw_conditions[0]
                    plat_tag = str(raw_c.get("target", "posix"))
                    plat_cond = get_platform_condition(plat_tag)
                else:
                    sub_conds = [get_platform_condition(str(c.get("target", "posix"))) for c in cond.raw_conditions]
                    tags = frozenset.union(*(sc.tags for sc in sub_conds))
                    terms = tuple(sorted({term for sc in sub_conds for term in sc.terms}))
                    plat_cond = PlatformCondition(tags=tags, terms=terms)
                branches.append((plat_cond, list(decls)))
            items.append(PlatformGuardedBlock(branches=branches))
        else:
            items.append(item)

    return ReconciledUnit(path=md_unit.path, items=items)
