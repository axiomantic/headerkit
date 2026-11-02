"""Compare two Header objects and produce API compatibility reports.

Computes a structural diff between a baseline and target header,
classifying each change as breaking or non-breaking, and renders
the result as JSON or Markdown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Declaration,
    Enum,
    Function,
    FunctionPointer,
    Header,
    MacroFunction,
    Parameter,
    Pointer,
    Reference,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.transforms.nim_cleanup import (
    _NIM_BUILTIN_TYPEDEFS,
    _NIM_INTERNAL_EXACT_NAMES,
    _NIM_INTERNAL_PREFIXES,
    _clean_nim_identifier,
    _unmangle_nim_type_name,
)
from headerkit.writers.base import BaseWriter, WriterOption

# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class DiffEntry:
    """A single difference between baseline and target headers.

    :param kind: Category of change (e.g., ``"function_added"``,
        ``"struct_field_removed"``).
    :param severity: Either ``"breaking"`` or ``"non_breaking"``.
    :param name: Affected declaration name.
    :param detail: Human-readable description of the change.
    :param baseline: Baseline signature or value, if applicable.
    :param target: Target signature or value, if applicable.
    """

    kind: str
    severity: str
    name: str
    detail: str
    baseline: str | None = None
    target: str | None = None


@dataclass
class DiffReport:
    """Complete diff report between two headers.

    :param baseline_path: Path of the baseline header file.
    :param target_path: Path of the target header file.
    :param entries: List of detected differences.
    """

    baseline_path: str
    target_path: str
    entries: list[DiffEntry] = field(default_factory=list)

    @property
    def breaking_count(self) -> int:
        """Number of breaking changes."""
        return sum(1 for e in self.entries if e.severity == "breaking")

    @property
    def non_breaking_count(self) -> int:
        """Number of non-breaking changes."""
        return sum(1 for e in self.entries if e.severity == "non_breaking")


# =============================================================================
# Declaration key helpers
# =============================================================================

# Map IR declaration types to string kind labels used as keys.
_DECL_KIND_MAP: dict[type[Declaration], str] = {
    Function: "function",
    Struct: "struct",
    Enum: "enum",
    Typedef: "typedef",
    Variable: "variable",
    Constant: "constant",
}


def _decl_kind(decl: Declaration) -> str:
    """Return a short string label for a declaration type."""
    for cls, label in _DECL_KIND_MAP.items():
        if isinstance(decl, cls):
            return label
    return "unknown"  # pragma: no cover


def _decl_name(decl: Declaration) -> str | None:
    """Return the name of a declaration, or None for anonymous types."""
    if isinstance(decl, Function | Typedef | Variable | Constant):
        return decl.name
    if isinstance(decl, Struct | Enum):
        return decl.name
    return None  # pragma: no cover


def _decl_key(decl: Declaration) -> tuple[str, str | None]:
    """Return a (kind, name) tuple used for matching declarations."""
    return (_decl_kind(decl), _decl_name(decl))


# =============================================================================
# ABI Canonicalization Helpers
# =============================================================================

_CANONICAL_SCALAR_MAP: dict[str, str] = {
    "_Bool": "bool",
    "NIM_BOOL": "bool",
    "bool": "bool",
    "NIM_CHAR": "char",
    "signed char": "int8_t",
    "int8_t": "int8_t",
    "NI8": "int8_t",
    "unsigned char": "uint8_t",
    "uint8_t": "uint8_t",
    "NU8": "uint8_t",
    "short": "int16_t",
    "short int": "int16_t",
    "signed short": "int16_t",
    "signed short int": "int16_t",
    "int16_t": "int16_t",
    "NI16": "int16_t",
    "unsigned short": "uint16_t",
    "unsigned short int": "uint16_t",
    "uint16_t": "uint16_t",
    "NU16": "uint16_t",
    "int": "int32_t",
    "signed": "int32_t",
    "signed int": "int32_t",
    "int32_t": "int32_t",
    "NI32": "int32_t",
    "unsigned": "uint32_t",
    "unsigned int": "uint32_t",
    "uint32_t": "uint32_t",
    "NU32": "uint32_t",
    "long long": "int64_t",
    "long long int": "int64_t",
    "signed long long": "int64_t",
    "signed long long int": "int64_t",
    "int64_t": "int64_t",
    "NI64": "int64_t",
    "unsigned long long": "uint64_t",
    "unsigned long long int": "uint64_t",
    "uint64_t": "uint64_t",
    "NU64": "uint64_t",
    "long": "long",
    "long int": "long",
    "signed long": "long",
    "signed long int": "long",
    "unsigned long": "unsigned long",
    "unsigned long int": "unsigned long",
    "size_t": "uintptr_t",
    "uintptr_t": "uintptr_t",
    "NU": "uintptr_t",
    "ssize_t": "intptr_t",
    "ptrdiff_t": "intptr_t",
    "intptr_t": "intptr_t",
    "NI": "intptr_t",
    "NF": "double",
    "NF64": "double",
    "NF32": "float",
}

_NIM_PARAM_P_RE = re.compile(r"_p\d+$")


def _strip_tag_prefix(name: str) -> str:
    """Strip leading 'struct ', 'union ', or 'enum ' from a type name."""
    for prefix in ("struct ", "union ", "enum "):
        if name.startswith(prefix):
            return name[len(prefix) :].strip()
    return name.strip()


def _is_nim_c_header(unit: Header) -> bool:
    """Detect whether ``unit`` represents a ``nim c --header`` C header IR."""
    if getattr(unit, "source_origin", None) == "nim_c_header" or getattr(unit, "language", None) == "nim":
        return True
    if "nimbase.h" in getattr(unit, "included_headers", set()):
        return True
    for decl in unit.declarations:
        name = _decl_name(decl) or ""
        if name in _NIM_INTERNAL_EXACT_NAMES or name in _NIM_BUILTIN_TYPEDEFS or name.startswith(_NIM_INTERNAL_PREFIXES):
            return True
        if name.startswith(("tyObject_", "tyEnum_", "tyProc__")):
            return True
        if isinstance(decl, Function):
            for p in decl.parameters:
                if p.name and _NIM_PARAM_P_RE.search(p.name):
                    return True
                if isinstance(p.type, CType) and p.type.name in _NIM_BUILTIN_TYPEDEFS:
                    return True
            if isinstance(decl.return_type, CType) and decl.return_type.name in _NIM_BUILTIN_TYPEDEFS:
                return True
    return False


def _build_typedef_map(unit: Header) -> dict[str, TypeExpr]:
    """Build a mapping from typedef name to underlying TypeExpr for a header."""
    td_map: dict[str, TypeExpr] = {}
    for decl in unit.declarations:
        if isinstance(decl, Typedef) and decl.name:
            if isinstance(decl.underlying_type, CType) and _strip_tag_prefix(decl.underlying_type.name) == decl.name:
                continue
            td_map[decl.name] = decl.underlying_type
    return td_map


def _canonicalize_type(
    type_expr: TypeExpr,
    typedef_map: dict[str, TypeExpr],
    *,
    abi_mode: bool = True,
    ignore_pointer_const: bool = False,
    _under_pointer: bool = False,
    _seen_typedefs: frozenset[str] = frozenset(),
) -> TypeExpr:
    """Canonicalize a ``TypeExpr`` for ABI comparison."""
    if not abi_mode:
        return type_expr

    if isinstance(type_expr, CType):
        signed_quals = [q for q in type_expr.qualifiers if q in ("signed", "unsigned")]
        cv_quals = [q for q in type_expr.qualifiers if q in ("const", "volatile")]
        if _under_pointer and ignore_pointer_const:
            cv_quals = [q for q in cv_quals if q != "const"]

        raw_name = _unmangle_nim_type_name(_strip_tag_prefix(type_expr.name))
        if raw_name == "NCSTRING":
            char_quals = sorted(set(cv_quals))
            if ignore_pointer_const:
                char_quals = [q for q in char_quals if q != "const"]
            return Pointer(pointee=CType(name="char", qualifiers=char_quals, is_elaborated=None))

        if raw_name in typedef_map and raw_name not in _seen_typedefs:
            resolved = _canonicalize_type(
                typedef_map[raw_name],
                typedef_map,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
                _under_pointer=_under_pointer,
                _seen_typedefs=_seen_typedefs | {raw_name},
            )
            if isinstance(resolved, CType) and cv_quals:
                merged_quals = sorted(set(resolved.qualifiers) | set(cv_quals))
                if _under_pointer and ignore_pointer_const:
                    merged_quals = [q for q in merged_quals if q != "const"]
                return replace(resolved, qualifiers=merged_quals, is_elaborated=None)
            return resolved

        combined = f"{' '.join(signed_quals)} {raw_name}".strip() if signed_quals else raw_name
        canonical_name = _CANONICAL_SCALAR_MAP.get(combined, combined)
        return CType(name=canonical_name, qualifiers=sorted(set(cv_quals)), is_elaborated=None)

    if isinstance(type_expr, Pointer):
        if isinstance(type_expr.pointee, FunctionPointer):
            return _canonicalize_type(
                type_expr.pointee,
                typedef_map,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
                _under_pointer=True,
                _seen_typedefs=_seen_typedefs,
            )
        ptr_quals = [q for q in type_expr.qualifiers if q in ("const", "volatile")]
        if ignore_pointer_const:
            ptr_quals = [q for q in ptr_quals if q != "const"]
        return Pointer(
            pointee=_canonicalize_type(
                type_expr.pointee,
                typedef_map,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
                _under_pointer=True,
                _seen_typedefs=_seen_typedefs,
            ),
            qualifiers=sorted(set(ptr_quals)),
        )

    if isinstance(type_expr, Reference):
        ref_quals = [q for q in type_expr.qualifiers if q in ("const", "volatile")]
        return Reference(
            target=_canonicalize_type(
                type_expr.target,
                typedef_map,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
                _under_pointer=_under_pointer,
                _seen_typedefs=_seen_typedefs,
            ),
            is_rvalue=type_expr.is_rvalue,
            qualifiers=sorted(set(ref_quals)),
        )

    if isinstance(type_expr, Array):
        return Array(
            element_type=_canonicalize_type(
                type_expr.element_type,
                typedef_map,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
                _under_pointer=_under_pointer,
                _seen_typedefs=_seen_typedefs,
            ),
            size=type_expr.size,
        )

    if isinstance(type_expr, FunctionPointer):
        cc = None if type_expr.calling_convention in (None, "cdecl", "N_CDECL") else type_expr.calling_convention
        return FunctionPointer(
            return_type=_canonicalize_type(
                type_expr.return_type,
                typedef_map,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
                _under_pointer=False,
                _seen_typedefs=_seen_typedefs,
            ),
            parameters=[
                Parameter(
                    name=None,
                    type=_canonicalize_type(
                        p.type,
                        typedef_map,
                        abi_mode=abi_mode,
                        ignore_pointer_const=ignore_pointer_const,
                        _under_pointer=False,
                        _seen_typedefs=_seen_typedefs,
                    ),
                )
                for p in type_expr.parameters
            ],
            is_variadic=type_expr.is_variadic,
            calling_convention=cc,
        )

    return type_expr


def _types_equal(
    b_type: TypeExpr,
    t_type: TypeExpr,
    baseline_typedefs: dict[str, TypeExpr],
    target_typedefs: dict[str, TypeExpr],
    *,
    abi_mode: bool = True,
    ignore_pointer_const: bool = False,
) -> bool:
    """Compare two TypeExpr nodes, optionally using ABI canonicalization."""
    if not abi_mode:
        return b_type == t_type
    b_canon = _canonicalize_type(
        b_type,
        baseline_typedefs,
        abi_mode=True,
        ignore_pointer_const=ignore_pointer_const,
    )
    t_canon = _canonicalize_type(
        t_type,
        target_typedefs,
        abi_mode=True,
        ignore_pointer_const=ignore_pointer_const,
    )
    return b_canon == t_canon


def _normalize_calling_convention(cc: str | None, *, abi_mode: bool = True) -> str | None:
    if not abi_mode:
        return cc
    if cc in (None, "cdecl", "N_CDECL"):
        return None
    return cc


# =============================================================================
# Per-type change detection
# =============================================================================


def _diff_function(
    name: str,
    baseline: Function,
    target: Function,
    *,
    baseline_typedefs: dict[str, TypeExpr] | None = None,
    target_typedefs: dict[str, TypeExpr] | None = None,
    abi_mode: bool = True,
    ignore_pointer_const: bool = False,
    is_nim_comparison: bool = False,
) -> list[DiffEntry]:
    """Compare two Function declarations."""
    entries: list[DiffEntry] = []
    b_tds = baseline_typedefs or {}
    t_tds = target_typedefs or {}

    # Return type
    if not _types_equal(
        baseline.return_type,
        target.return_type,
        b_tds,
        t_tds,
        abi_mode=abi_mode,
        ignore_pointer_const=ignore_pointer_const,
    ):
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=f"return type changed from '{baseline.return_type}' to '{target.return_type}'",
                baseline=str(baseline.return_type),
                target=str(target.return_type),
            )
        )

    # Parameter count
    if len(baseline.parameters) != len(target.parameters):
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=(f"parameter count changed from {len(baseline.parameters)} to {len(target.parameters)}"),
                baseline=str(baseline),
                target=str(target),
            )
        )
    else:
        # Compare each parameter
        for i, (bp, tp) in enumerate(zip(baseline.parameters, target.parameters, strict=True)):
            if not _types_equal(
                bp.type,
                tp.type,
                b_tds,
                t_tds,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
            ):
                entries.append(
                    DiffEntry(
                        kind="function_signature_changed",
                        severity="breaking",
                        name=name,
                        detail=(f"parameter {i} type changed from '{bp.type}' to '{tp.type}'"),
                        baseline=str(bp.type),
                        target=str(tp.type),
                    )
                )
            b_pname = _clean_nim_identifier(bp.name) if (abi_mode and is_nim_comparison) else bp.name
            t_pname = _clean_nim_identifier(tp.name) if (abi_mode and is_nim_comparison) else tp.name
            if b_pname != t_pname and not (abi_mode and is_nim_comparison):
                entries.append(
                    DiffEntry(
                        kind="function_parameter_renamed",
                        severity="non_breaking",
                        name=name,
                        detail=(f"parameter {i} renamed from '{bp.name}' to '{tp.name}'"),
                        baseline=bp.name,
                        target=tp.name,
                    )
                )

    # Variadic
    if baseline.is_variadic != target.is_variadic:
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=(f"variadic changed from {baseline.is_variadic} to {target.is_variadic}"),
                baseline=str(baseline),
                target=str(target),
            )
        )

    # Calling convention
    b_cc = _normalize_calling_convention(baseline.calling_convention, abi_mode=abi_mode)
    t_cc = _normalize_calling_convention(target.calling_convention, abi_mode=abi_mode)
    if b_cc != t_cc:
        entries.append(
            DiffEntry(
                kind="function_signature_changed",
                severity="breaking",
                name=name,
                detail=(
                    f"calling convention changed from '{baseline.calling_convention}' to '{target.calling_convention}'"
                ),
                baseline=str(baseline.calling_convention),
                target=str(target.calling_convention),
            )
        )

    return entries


def _diff_struct(
    name: str,
    baseline: Struct,
    target: Struct,
    *,
    baseline_typedefs: dict[str, TypeExpr] | None = None,
    target_typedefs: dict[str, TypeExpr] | None = None,
    abi_mode: bool = True,
    ignore_pointer_const: bool = False,
) -> list[DiffEntry]:
    """Compare two Struct declarations."""
    entries: list[DiffEntry] = []
    display_name = name or "(anonymous)"
    b_tds = baseline_typedefs or {}
    t_tds = target_typedefs or {}

    b_fields_list = [f for f in baseline.fields if not f.is_padding] if abi_mode else list(baseline.fields)
    t_fields_list = [f for f in target.fields if not f.is_padding] if abi_mode else list(target.fields)

    baseline_fields = {f.name: (i, f) for i, f in enumerate(b_fields_list)}
    target_fields = {f.name: (i, f) for i, f in enumerate(t_fields_list)}

    # Removed fields
    for fname in baseline_fields:
        if fname not in target_fields:
            entries.append(
                DiffEntry(
                    kind="struct_field_removed",
                    severity="breaking",
                    name=display_name,
                    detail=f"field '{fname}' removed",
                    baseline=str(baseline_fields[fname][1]),
                )
            )

    # Added fields
    for fname in target_fields:
        if fname not in baseline_fields:
            # Added at the end is non-breaking; added in the middle is breaking
            tidx = target_fields[fname][0]
            if tidx == len(t_fields_list) - 1 or all(
                tn not in baseline_fields for tn in list(target_fields.keys())[tidx:]
            ):
                severity = "non_breaking"
            else:
                # Check if there are existing fields after this one
                has_existing_after = any(tn in baseline_fields for tn in list(target_fields.keys())[tidx + 1 :])
                severity = "breaking" if has_existing_after else "non_breaking"
            entries.append(
                DiffEntry(
                    kind="struct_field_added",
                    severity=severity,
                    name=display_name,
                    detail=f"field '{fname}' added",
                    target=str(target_fields[fname][1]),
                )
            )

    # Changed fields (present in both)
    for fname in baseline_fields:
        if fname in target_fields:
            bidx, bf = baseline_fields[fname]
            tidx, tf = target_fields[fname]

            if bf.anonymous_struct is not None and tf.anonymous_struct is not None:
                entries.extend(
                    _diff_struct(
                        f"{display_name}.{fname}" if fname else display_name,
                        bf.anonymous_struct,
                        tf.anonymous_struct,
                        baseline_typedefs=b_tds,
                        target_typedefs=t_tds,
                        abi_mode=abi_mode,
                        ignore_pointer_const=ignore_pointer_const,
                    )
                )
            elif not _types_equal(
                bf.type,
                tf.type,
                b_tds,
                t_tds,
                abi_mode=abi_mode,
                ignore_pointer_const=ignore_pointer_const,
            ):
                entries.append(
                    DiffEntry(
                        kind="struct_field_type_changed",
                        severity="breaking",
                        name=display_name,
                        detail=(f"field '{fname}' type changed from '{bf.type}' to '{tf.type}'"),
                        baseline=str(bf.type),
                        target=str(tf.type),
                    )
                )

            if abi_mode and bf.bit_width != tf.bit_width:
                entries.append(
                    DiffEntry(
                        kind="struct_layout_changed",
                        severity="breaking",
                        name=display_name,
                        detail=(f"field '{fname}' bit_width changed from {bf.bit_width} to {tf.bit_width}"),
                        baseline=str(bf.bit_width),
                        target=str(tf.bit_width),
                    )
                )

            if bidx != tidx:
                entries.append(
                    DiffEntry(
                        kind="struct_field_reordered",
                        severity="breaking",
                        name=display_name,
                        detail=(f"field '{fname}' moved from index {bidx} to {tidx}"),
                    )
                )

    # is_packed changed
    if baseline.is_packed != target.is_packed:
        entries.append(
            DiffEntry(
                kind="struct_layout_changed",
                severity="breaking",
                name=display_name,
                detail=(f"packed attribute changed from {baseline.is_packed} to {target.is_packed}"),
            )
        )

    # is_union changed
    if baseline.is_union != target.is_union:
        entries.append(
            DiffEntry(
                kind="struct_layout_changed",
                severity="breaking",
                name=display_name,
                detail=(
                    f"kind changed from "
                    f"{'union' if baseline.is_union else 'struct'} to "
                    f"{'union' if target.is_union else 'struct'}"
                ),
            )
        )

    return entries


def _diff_enum(name: str, baseline: Enum, target: Enum) -> list[DiffEntry]:
    """Compare two Enum declarations."""
    entries: list[DiffEntry] = []
    display_name = name or "(anonymous)"

    baseline_vals = {v.name: v for v in baseline.values}
    target_vals = {v.name: v for v in target.values}

    # Removed values
    for vname, bv in baseline_vals.items():
        if vname not in target_vals:
            entries.append(
                DiffEntry(
                    kind="enum_value_removed",
                    severity="breaking",
                    name=display_name,
                    detail=f"enum value '{vname}' removed",
                    baseline=str(bv),
                )
            )

    # Added values
    for vname, tv in target_vals.items():
        if vname not in baseline_vals:
            entries.append(
                DiffEntry(
                    kind="enum_value_added",
                    severity="non_breaking",
                    name=display_name,
                    detail=f"enum value '{vname}' added",
                    target=str(tv),
                )
            )

    # Changed values
    for vname in baseline_vals:
        if vname in target_vals:
            bv = baseline_vals[vname]
            tv = target_vals[vname]
            if bv.value != tv.value:
                entries.append(
                    DiffEntry(
                        kind="enum_value_changed",
                        severity="breaking",
                        name=display_name,
                        detail=(f"enum value '{vname}' changed from {bv.value} to {tv.value}"),
                        baseline=str(bv.value),
                        target=str(tv.value),
                    )
                )

    return entries


def _diff_typedef(
    name: str,
    baseline: Typedef,
    target: Typedef,
    *,
    baseline_typedefs: dict[str, TypeExpr] | None = None,
    target_typedefs: dict[str, TypeExpr] | None = None,
    abi_mode: bool = True,
    ignore_pointer_const: bool = False,
) -> list[DiffEntry]:
    """Compare two Typedef declarations."""
    entries: list[DiffEntry] = []

    if not _types_equal(
        baseline.underlying_type,
        target.underlying_type,
        baseline_typedefs or {},
        target_typedefs or {},
        abi_mode=abi_mode,
        ignore_pointer_const=ignore_pointer_const,
    ):
        entries.append(
            DiffEntry(
                kind="typedef_changed",
                severity="breaking",
                name=name,
                detail=(f"underlying type changed from '{baseline.underlying_type}' to '{target.underlying_type}'"),
                baseline=str(baseline.underlying_type),
                target=str(target.underlying_type),
            )
        )

    return entries


def _diff_variable(
    name: str,
    baseline: Variable,
    target: Variable,
    *,
    baseline_typedefs: dict[str, TypeExpr] | None = None,
    target_typedefs: dict[str, TypeExpr] | None = None,
    abi_mode: bool = True,
    ignore_pointer_const: bool = False,
) -> list[DiffEntry]:
    """Compare two Variable declarations."""
    entries: list[DiffEntry] = []

    if not _types_equal(
        baseline.type,
        target.type,
        baseline_typedefs or {},
        target_typedefs or {},
        abi_mode=abi_mode,
        ignore_pointer_const=ignore_pointer_const,
    ):
        entries.append(
            DiffEntry(
                kind="variable_type_changed",
                severity="breaking",
                name=name,
                detail=(f"type changed from '{baseline.type}' to '{target.type}'"),
                baseline=str(baseline.type),
                target=str(target.type),
            )
        )

    return entries


def _diff_constant(name: str, baseline: Constant, target: Constant) -> list[DiffEntry]:
    """Compare two Constant declarations."""
    entries: list[DiffEntry] = []

    if baseline.value != target.value:
        entries.append(
            DiffEntry(
                kind="constant_changed",
                severity="non_breaking",
                name=name,
                detail=(f"value changed from {baseline.value!r} to {target.value!r}"),
                baseline=repr(baseline.value),
                target=repr(target.value),
            )
        )

    return entries


# =============================================================================
# Main diff computation
# =============================================================================

# Severity for added/removed declarations by kind
_ADDED_SEVERITY: dict[str, str] = {
    "function": "non_breaking",
    "struct": "non_breaking",
    "enum": "non_breaking",
    "typedef": "non_breaking",
    "variable": "non_breaking",
    "constant": "non_breaking",
}

_REMOVED_SEVERITY: dict[str, str] = {
    "function": "breaking",
    "struct": "breaking",
    "enum": "breaking",
    "typedef": "breaking",
    "variable": "breaking",
    "constant": "breaking",
}


def diff_headers(
    baseline: Header,
    target: Header,
    *,
    abi_mode: bool = True,
    ignore_pointer_const: bool | None = None,
    relax_nim_pointer_const: bool = False,
) -> DiffReport:
    """Compare two Header objects and produce a DiffReport.

    Declarations are matched by (kind, name). Anonymous declarations
    (name is None) are skipped since they cannot be reliably matched.

    :param baseline: The original/old header.
    :param target: The new/updated header.
    :param abi_mode: When True (default), resolves Typedef chains to canonical
        C types, normalizes ``_Bool``/``bool`` and integer width aliases, and
        compares Struct layouts structurally.
    :param ignore_pointer_const: When True, relaxes ``const T*`` vs ``T*``
        pointee const qualifiers while still catching pointer indirection drift.
    :param relax_nim_pointer_const: Alias for relaxing pointer const qualifiers
        when comparing against ``nim c --header`` targets.
    :returns: A DiffReport with all detected changes.
    """
    entries: list[DiffEntry] = []
    is_nim_comparison = _is_nim_c_header(baseline) or _is_nim_c_header(target)
    if ignore_pointer_const is not None:
        effective_ignore_const = bool(ignore_pointer_const or relax_nim_pointer_const)
    else:
        effective_ignore_const = bool(relax_nim_pointer_const or (abi_mode and is_nim_comparison))

    baseline_typedefs = _build_typedef_map(baseline) if abi_mode else {}
    target_typedefs = _build_typedef_map(target) if abi_mode else {}

    def _should_skip_decl(decl: Declaration) -> bool:
        if isinstance(decl, MacroFunction):
            return True
        if abi_mode and is_nim_comparison:
            if isinstance(decl, Constant) and decl.is_macro:
                return True
            decl_n = _decl_name(decl) or ""
            if (
                decl_n in _NIM_INTERNAL_EXACT_NAMES
                or decl_n in _NIM_BUILTIN_TYPEDEFS
                or decl_n.startswith(_NIM_INTERNAL_PREFIXES)
            ):
                return True
            if decl_n.startswith("tyProc__"):
                return True
        return False

    # Build maps: (kind, name) -> declaration
    baseline_map: dict[tuple[str, str], Declaration] = {}
    for decl in baseline.declarations:
        if _should_skip_decl(decl):
            continue
        key = _decl_key(decl)
        if key[1] is not None:
            norm_name = _unmangle_nim_type_name(key[1]) if abi_mode else key[1]
            baseline_map[(key[0], norm_name)] = decl

    target_map: dict[tuple[str, str], Declaration] = {}
    for decl in target.declarations:
        if _should_skip_decl(decl):
            continue
        key = _decl_key(decl)
        if key[1] is not None:
            norm_name = _unmangle_nim_type_name(key[1]) if abi_mode else key[1]
            target_map[(key[0], norm_name)] = decl

    # Removed declarations
    for key, decl in baseline_map.items():
        if key not in target_map:
            kind_label, decl_name_str = key
            if abi_mode:
                if kind_label == "typedef" and is_nim_comparison:
                    continue
                if (
                    kind_label == "struct"
                    and isinstance(decl, Struct)
                    and not decl.fields
                    and ("typedef", decl_name_str) in target_map
                ):
                    continue
                if kind_label == "typedef" and ("struct", decl_name_str) in target_map:
                    continue
            severity = _REMOVED_SEVERITY.get(kind_label, "breaking")
            entries.append(
                DiffEntry(
                    kind=f"{kind_label}_removed",
                    severity=severity,
                    name=decl_name_str,
                    detail=f"{kind_label} '{decl_name_str}' removed",
                    baseline=str(decl),
                )
            )

    # Added declarations
    for key, decl in target_map.items():
        if key not in baseline_map:
            kind_label, decl_name_str = key
            if abi_mode:
                if kind_label == "typedef" and is_nim_comparison:
                    continue
                if (
                    kind_label == "struct"
                    and isinstance(decl, Struct)
                    and not decl.fields
                    and ("typedef", decl_name_str) in baseline_map
                ):
                    continue
                if kind_label == "typedef" and ("struct", decl_name_str) in baseline_map:
                    continue
            severity = _ADDED_SEVERITY.get(kind_label, "non_breaking")
            entries.append(
                DiffEntry(
                    kind=f"{kind_label}_added",
                    severity=severity,
                    name=decl_name_str,
                    detail=f"{kind_label} '{decl_name_str}' added",
                    target=str(decl),
                )
            )

    # Changed declarations
    for key in baseline_map:
        if key in target_map:
            b_decl = baseline_map[key]
            t_decl = target_map[key]
            name_str = key[1]

            if isinstance(b_decl, Function) and isinstance(t_decl, Function):
                entries.extend(
                    _diff_function(
                        name_str,
                        b_decl,
                        t_decl,
                        baseline_typedefs=baseline_typedefs,
                        target_typedefs=target_typedefs,
                        abi_mode=abi_mode,
                        ignore_pointer_const=effective_ignore_const,
                        is_nim_comparison=is_nim_comparison,
                    )
                )
            elif isinstance(b_decl, Struct) and isinstance(t_decl, Struct):
                entries.extend(
                    _diff_struct(
                        name_str,
                        b_decl,
                        t_decl,
                        baseline_typedefs=baseline_typedefs,
                        target_typedefs=target_typedefs,
                        abi_mode=abi_mode,
                        ignore_pointer_const=effective_ignore_const,
                    )
                )
            elif isinstance(b_decl, Enum) and isinstance(t_decl, Enum):
                entries.extend(_diff_enum(name_str, b_decl, t_decl))
            elif isinstance(b_decl, Typedef) and isinstance(t_decl, Typedef):
                entries.extend(
                    _diff_typedef(
                        name_str,
                        b_decl,
                        t_decl,
                        baseline_typedefs=baseline_typedefs,
                        target_typedefs=target_typedefs,
                        abi_mode=abi_mode,
                        ignore_pointer_const=effective_ignore_const,
                    )
                )
            elif isinstance(b_decl, Variable) and isinstance(t_decl, Variable):
                entries.extend(
                    _diff_variable(
                        name_str,
                        b_decl,
                        t_decl,
                        baseline_typedefs=baseline_typedefs,
                        target_typedefs=target_typedefs,
                        abi_mode=abi_mode,
                        ignore_pointer_const=effective_ignore_const,
                    )
                )
            elif isinstance(b_decl, Constant) and isinstance(t_decl, Constant):
                entries.extend(_diff_constant(name_str, b_decl, t_decl))

    return DiffReport(
        baseline_path=baseline.path,
        target_path=target.path,
        entries=entries,
    )


# =============================================================================
# Output formatters
# =============================================================================


def _entry_to_dict(entry: DiffEntry) -> dict[str, Any]:
    """Convert a DiffEntry to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "kind": entry.kind,
        "severity": entry.severity,
        "name": entry.name,
        "detail": entry.detail,
    }
    if entry.baseline is not None:
        d["baseline"] = entry.baseline
    if entry.target is not None:
        d["target"] = entry.target
    return d


def diff_to_json(report: DiffReport, indent: int | None = 2) -> str:
    """Serialize a DiffReport to JSON.

    :param report: The diff report.
    :param indent: JSON indentation level. None for compact output.
    :returns: JSON string.
    """
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "baseline": report.baseline_path,
        "target": report.target_path,
        "summary": {
            "total": len(report.entries),
            "breaking": report.breaking_count,
            "non_breaking": report.non_breaking_count,
        },
        "entries": [_entry_to_dict(e) for e in report.entries],
    }
    return json.dumps(data, indent=indent)


def diff_to_markdown(report: DiffReport) -> str:
    """Render a DiffReport as human-readable Markdown.

    :param report: The diff report.
    :returns: Markdown string.
    """
    lines: list[str] = []
    lines.append(f"# API Diff: {report.baseline_path} -> {report.target_path}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    lines.append(f"| Breaking | {report.breaking_count} |")
    lines.append(f"| Non-breaking | {report.non_breaking_count} |")
    lines.append(f"| Total | {len(report.entries)} |")
    lines.append("")

    breaking = [e for e in report.entries if e.severity == "breaking"]
    non_breaking = [e for e in report.entries if e.severity == "non_breaking"]

    if breaking:
        lines.append("## Breaking Changes")
        lines.append("")
        # Group by kind
        by_kind: dict[str, list[DiffEntry]] = {}
        for entry in breaking:
            by_kind.setdefault(entry.kind, []).append(entry)
        for kind, kind_entries in by_kind.items():
            lines.append(f"### {kind}")
            lines.append("")
            for entry in kind_entries:
                lines.append(f"- **{entry.name}**: {entry.detail}")
                if entry.baseline is not None:
                    lines.append(f"  - Baseline: `{entry.baseline}`")
                if entry.target is not None:
                    lines.append(f"  - Target: `{entry.target}`")
            lines.append("")

    if non_breaking:
        lines.append("## Non-Breaking Changes")
        lines.append("")
        by_kind_nb: dict[str, list[DiffEntry]] = {}
        for entry in non_breaking:
            by_kind_nb.setdefault(entry.kind, []).append(entry)
        for kind, kind_entries in by_kind_nb.items():
            lines.append(f"### {kind}")
            lines.append("")
            for entry in kind_entries:
                lines.append(f"- **{entry.name}**: {entry.detail}")
                if entry.baseline is not None:
                    lines.append(f"  - Baseline: `{entry.baseline}`")
                if entry.target is not None:
                    lines.append(f"  - Target: `{entry.target}`")
            lines.append("")

    if not breaking and not non_breaking:
        lines.append("No changes detected.")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# Writer class
# =============================================================================


class DiffWriter(BaseWriter):
    """Writer that compares two headers and produces API compatibility reports.

    Compares a target header against a baseline and produces a structured
    diff report in either JSON or Markdown format.

    Options
    -------
    baseline : Header | None
        The baseline header to compare against. If None, an empty header
        is used (all declarations appear as additions).
    format : str
        Output format: ``"json"`` (default) or ``"markdown"``.
    abi_mode : bool
        When True (default), resolves Typedef chains, normalizes ``_Bool``/``bool``
        and integer width typedefs, and compares Struct layouts structurally.
    ignore_pointer_const : bool | None
        When True, relaxes ``const T*`` vs ``T*`` pointee const qualifiers.
    relax_nim_pointer_const : bool
        Alias for relaxing pointer const qualifiers on ``nim c --header`` targets.

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("diff", baseline=old_header, format="markdown")
        report = writer.write(new_header)
    """

    name: str = "diff"
    format_description: str = "API compatibility diff reports (JSON or Markdown)"
    default_output_pattern: str = "{dir}/{stem}_diff.json"
    cache_output: bool = False
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption("format", "Report output format", default="json", choices=("json", "markdown")),
        WriterOption("abi_mode", "Resolve typedefs and compare canonical C ABI types", default=True),
        WriterOption("ignore_pointer_const", "Relax const T* vs T* pointer pointee const qualifiers", default=False),
        WriterOption(
            "relax_nim_pointer_const",
            "Relax const T* vs T* pointer const qualifiers on Nim C headers",
            default=False,
        ),
    )

    def __init__(
        self,
        baseline: Header | None = None,
        format: str = "json",
        *,
        abi_mode: bool = True,
        ignore_pointer_const: bool | None = None,
        relax_nim_pointer_const: bool = False,
    ) -> None:
        self._baseline = baseline
        self._format = format
        self.abi_mode = abi_mode
        self.ignore_pointer_const = ignore_pointer_const
        self.relax_nim_pointer_const = relax_nim_pointer_const
        self.default_extension = ".md" if format == "markdown" else ".json"

    def _write_single_file_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        fmt = options.get_option("format", self._format)
        if self._baseline is None:
            baseline = Header(path="(empty)", declarations=[])
        else:
            baseline = self._baseline
        target = unit if isinstance(unit, Header) else Header(path=unit.path, declarations=unit.declarations)
        report = diff_headers(
            baseline,
            target,
            abi_mode=self.abi_mode,
            ignore_pointer_const=self.ignore_pointer_const,
            relax_nim_pointer_const=self.relax_nim_pointer_const,
        )
        content = diff_to_markdown(report) if fmt == "markdown" else diff_to_json(report)
        ext = ".md" if fmt == "markdown" else ".json"
        filename = f"{options.package_name}{ext}"
        return ProjectLayout(files=[OutputFile(path=filename, content=content)])

    def _render(self, unit: SourceUnit | Header) -> str:
        if self._baseline is None:
            baseline = Header(path="(empty)", declarations=[])
        else:
            baseline = self._baseline
        target = unit if isinstance(unit, Header) else Header(path=unit.path, declarations=unit.declarations)
        report = diff_headers(
            baseline,
            target,
            abi_mode=self.abi_mode,
            ignore_pointer_const=self.ignore_pointer_const,
            relax_nim_pointer_const=self.relax_nim_pointer_const,
        )
        if self._format == "markdown":
            return diff_to_markdown(report)
        return diff_to_json(report)

    def write(self, header: Header) -> str:
        """Compare header against baseline and produce a diff report."""
        return self._render(self._prepare(header))


# Uses bottom-of-module self-registration. See headerkit/writers/json.py
# for explanation of the managed circular import pattern.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "diff",
    DiffWriter,
    description="API compatibility diff reports (JSON or Markdown)",
)

