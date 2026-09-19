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


@dataclass(frozen=True)
class MatrixCondition:
    """General multi-dimensional boolean condition for guarded blocks."""

    predicate: str
    raw_conditions: tuple[ConfigCondition, ...]


@dataclass
class MatrixGuardedBlock:
    """A block of declaration branches guarded by multi-dimensional conditions."""

    branches: list[tuple[MatrixCondition, list[Declaration]]]


@dataclass
class ReconciledUnit:
    """Topologically ordered and platform-reconciled unit."""

    path: str
    items: list[Declaration | PlatformGuardedBlock] = field(default_factory=list)


@dataclass
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


def get_nim_platform(triple: str) -> str:
    """Map target triple or OS string to Nim platform identifier."""
    lower = triple.lower()
    if "darwin" in lower or "apple" in lower or "mac" in lower:
        return "macosx"
    elif "linux" in lower:
        return "linux"
    elif "windows" in lower or "msvc" in lower or "win32" in lower:
        return "windows"
    return "posix"


def get_c_platform(triple: str) -> str:
    """Map target triple or OS string to C/C++ preprocessor platform macro."""
    lower = triple.lower()
    if "darwin" in lower or "apple" in lower or "mac" in lower:
        return "__APPLE__"
    elif "linux" in lower:
        return "__linux__"
    elif "windows" in lower or "msvc" in lower or "win32" in lower:
        return "_WIN32"
    return "__unix__"


def get_py_platform(triple: str) -> str:
    """Map target triple or OS string to sys.platform identifier."""
    lower = triple.lower()
    if "darwin" in lower or "apple" in lower or "mac" in lower:
        return "darwin"
    elif "linux" in lower:
        return "linux"
    elif "windows" in lower or "msvc" in lower or "win32" in lower:
        return "win32"
    return "posix"


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
    target_lang: str = "nim",
) -> str:
    """Algebraically reduces configuration conditions into a minimal polyglot guard string."""
    if not configs:
        return "true"

    # Identify dimensions where observed values are a strict subset of domain values
    active_dimensions: set[str] = set()
    for dim, all_values in domain_matrix.items():
        observed_values = {c[dim] for c in configs if dim in c}
        if observed_values != all_values and len(all_values) > 1:
            active_dimensions.add(dim)

    if not active_dimensions:
        return "true" if target_lang != "cython" else "1"

    clauses: list[str] = []

    # 1. Platform set reduction (with deduplication and parentheses)
    platforms = {c["target"] for c in configs if "target" in c}
    if "target" in active_dimensions and platforms:
        if target_lang == "nim":
            plat_strs = sorted({get_nim_platform(p) for p in platforms})
            if len(plat_strs) > 1:
                clauses.append(f"({' or '.join(f'defined({p})' for p in plat_strs)})")
            elif plat_strs:
                clauses.append(f"defined({plat_strs[0]})")
        elif target_lang == "cython":
            plat_strs = sorted({get_c_platform(p) for p in platforms})
            if len(plat_strs) > 1:
                clauses.append(f"({' || '.join(f'defined({p})' for p in plat_strs)})")
            elif plat_strs:
                clauses.append(f"defined({plat_strs[0]})")
        elif target_lang == "python":
            py_plats = tuple(sorted({get_py_platform(p) for p in platforms}))
            if len(py_plats) > 1:
                clauses.append(f"sys.platform in {py_plats!r}")
            elif py_plats:
                clauses.append(f"sys.platform == {py_plats[0]!r}")

    # 2. Ordered version interval analysis
    if "version" in active_dimensions:
        obs_versions = sorted({c["version"] for c in configs if "version" in c})
        all_versions = sorted(domain_matrix.get("version", set()))
        if obs_versions:
            min_v = obs_versions[0]
            max_v = obs_versions[-1]
            if len(obs_versions) == 1:
                if target_lang in ("nim", "python"):
                    clauses.append(f"version == {min_v!r}")
            elif obs_versions == all_versions[all_versions.index(min_v) :]:
                # Suffix interval covering up to latest version
                if target_lang in ("nim", "python"):
                    clauses.append(f"version >= {min_v!r}")
            else:
                if target_lang == "nim":
                    clauses.append(f"({min_v!r} <= version and version <= {max_v!r})")
                elif target_lang == "python":
                    clauses.append(f"({min_v!r} <= version <= {max_v!r})")

    # 3. Quine-McCluskey minimization for boolean feature flags
    bool_features = sorted(
        dim
        for dim in active_dimensions
        if dim not in ("target", "version") and all(isinstance(v, bool) for v in domain_matrix[dim])
    )
    if bool_features:
        implicants = quine_mccluskey_minimize(configs, bool_features)
        imp_clauses: list[str] = []
        for imp in implicants:
            term_clauses: list[str] = []
            for feat, val in zip(bool_features, imp, strict=True):
                if val is True:
                    term_clauses.append(f"defined({feat})" if target_lang != "python" else feat)
                elif val is False:
                    term_clauses.append(f"not defined({feat})" if target_lang != "python" else f"not {feat}")
            if term_clauses:
                joiner = " and " if target_lang != "cython" else " && "
                imp_clauses.append(joiner.join(term_clauses))

        if imp_clauses:
            or_joiner = " or " if target_lang != "cython" else " || "
            if len(imp_clauses) > 1:
                clauses.append(f"({or_joiner.join(f'({c})' if ' ' in c else c for c in imp_clauses)})")
            else:
                clauses.append(imp_clauses[0])

    and_joiner = " and " if target_lang != "cython" else " && "
    if clauses:
        return and_joiner.join(clauses)
    return "true" if target_lang != "cython" else "1"


def condition_to_python(cond: Any, domain_matrix: dict[str, set[Any]] | None = None) -> str:
    """Translate a PlatformCondition or MatrixCondition into a valid Python expression."""
    if isinstance(cond, PlatformCondition):
        for tag in cond.tags:
            py_p = get_py_platform(tag)
            if py_p:
                return f"sys.platform == {py_p!r}"
        return "True"

    if isinstance(cond, MatrixCondition):
        if cond.raw_conditions:
            if domain_matrix:
                minimized = minimize_conditions(cond.raw_conditions, domain_matrix, target_lang="python")
                if minimized and minimized != "true":
                    return minimized
            platforms = {c["target"] for c in cond.raw_conditions if "target" in c}
            if platforms:
                py_plats = tuple(sorted({get_py_platform(p) for p in platforms}))
                if len(py_plats) > 1:
                    return f"sys.platform in {py_plats!r}"
                elif py_plats:
                    return f"sys.platform == {py_plats[0]!r}"

        # Fallback to predicate string mapping
        pred = getattr(cond, "predicate", "")
        if pred:
            if pred.startswith("sys.platform"):
                return pred
            pred_mapped = pred
            pred_mapped = pred_mapped.replace("defined(macosx)", "sys.platform == 'darwin'")
            pred_mapped = pred_mapped.replace("defined(darwin)", "sys.platform == 'darwin'")
            pred_mapped = pred_mapped.replace("defined(linux)", "sys.platform == 'linux'")
            pred_mapped = pred_mapped.replace("defined(windows)", "sys.platform == 'win32'")
            pred_mapped = pred_mapped.replace("defined(_WIN32)", "sys.platform == 'win32'")
            pred_mapped = pred_mapped.replace(" && ", " and ")
            pred_mapped = pred_mapped.replace(" || ", " or ")
            return pred_mapped

    return "True"


def reconcile_matrix(
    matrix_units: Sequence[tuple[ConfigCondition, SourceUnit]],
    matrix: ConfigMatrix | None = None,
    target_lang: str = "nim",
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
            predicate = minimize_conditions(class_configs, domain_matrix, target_lang=target_lang)
            mat_cond = MatrixCondition(predicate=predicate, raw_conditions=tuple(class_configs))
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

    md_unit = reconcile_matrix(matrix_units, matrix=matrix, target_lang="nim")

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
                    tags = frozenset.union(
                        *(get_platform_condition(str(c.get("target", "posix"))).tags for c in cond.raw_conditions)
                    )
                    plat_cond = PlatformCondition(tags=tags, nim_predicate=cond.predicate)
                branches.append((plat_cond, list(decls)))
            items.append(PlatformGuardedBlock(branches=branches))
        else:
            items.append(item)

    return ReconciledUnit(path=md_unit.path, items=items)
