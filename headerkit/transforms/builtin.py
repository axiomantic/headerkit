"""Built-in IR filters with type cross-reference rewriting."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence, Set
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from headerkit.hooks import PipelineContext
from headerkit.ir import (
    Array,
    Constant,
    CType,
    Declaration,
    Enum,
    Field,
    Function,
    FunctionPointer,
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
from headerkit.transforms.filter import PipelineFilter

_GLOB_METACHARACTERS = ("*", "?", "[")


def _normalize_for_match(path: str) -> str:
    """Normalize a path for cross-platform comparison (forward slashes, lowercase)."""
    return path.replace("\\", "/").lower()


def _build_search_dirs(
    header_path: str = "",
    include_dirs: Sequence[str] | None = None,
    search_dirs: Sequence[str] | None = None,
) -> list[str]:
    """Build priority-ordered search directories: hdr_dir -> include_dirs -> cwd."""
    if search_dirs is not None:
        return [str(d) for d in search_dirs]
    dirs: list[str] = []
    if header_path:
        hdr_dir = os.path.dirname(os.path.abspath(header_path)) or os.getcwd()
        dirs.append(hdr_dir)
    if include_dirs:
        dirs.extend(str(d) for d in include_dirs)
    cwd = os.getcwd()
    if cwd not in dirs:
        dirs.append(cwd)
    return dirs


class _PathSet:
    """A resolved set of allowlist/denylist entries for header path matching.

    Supports:
    - Basename matching and basename globbing when no path separator is present.
    - Relative path/glob/prefix resolution against ``search_dirs``
      (``hdr_dir`` -> ``include_dirs`` -> ``cwd``).
    - ``os.path.realpath`` symlink normalization on both pattern directories
      and declaration paths.
    - Prefix stripping against ``search_dirs`` so relative directory prefixes
      and relative globs match declarations inside include directories.
    """

    def __init__(self, entries: Sequence[str], search_dirs: Sequence[str] = ()) -> None:
        self.raw_entries: tuple[str, ...] = tuple(entries)
        self.search_dirs: tuple[str, ...] = tuple(search_dirs)
        self.basename_patterns: list[str] = []
        self.exact_paths: set[str] = set()
        self.path_patterns: list[str] = []
        self.prefix_patterns: list[str] = []

        resolved_search_dirs: list[str] = []
        for d in self.search_dirs:
            abs_d = os.path.abspath(d)
            for variant in (abs_d, os.path.realpath(abs_d)):
                norm_d = _normalize_for_match(variant).rstrip("/")
                if norm_d and norm_d not in resolved_search_dirs:
                    resolved_search_dirs.append(norm_d)
        self._resolved_search_dirs = resolved_search_dirs

        for entry in entries:
            if not entry:
                continue
            norm_entry = entry.replace("\\", "/")
            has_glob = any(ch in norm_entry for ch in _GLOB_METACHARACTERS)
            is_dir_prefix = norm_entry.endswith("/") and not has_glob

            if "/" not in norm_entry:
                # Basename exact or glob pattern; also resolve against search_dirs
                self.basename_patterns.append(_normalize_for_match(norm_entry))
                for candidate in self._resolve_candidates(entry):
                    if has_glob:
                        if candidate not in self.path_patterns:
                            self.path_patterns.append(candidate)
                    else:
                        self.exact_paths.add(candidate)
            elif is_dir_prefix:
                stripped = norm_entry.rstrip("/")
                self.prefix_patterns.append(_normalize_for_match(stripped))
                for candidate in self._resolve_candidates(stripped):
                    if candidate.rstrip("/") not in self.prefix_patterns:
                        self.prefix_patterns.append(candidate.rstrip("/"))
            else:
                norm_match = _normalize_for_match(norm_entry)
                if has_glob:
                    if norm_match not in self.path_patterns:
                        self.path_patterns.append(norm_match)
                    for candidate in self._resolve_glob_candidates(entry):
                        if candidate not in self.path_patterns:
                            self.path_patterns.append(candidate)
                else:
                    self.exact_paths.add(norm_match)
                    self.prefix_patterns.append(norm_match.rstrip("/"))
                    for candidate in self._resolve_candidates(entry):
                        self.exact_paths.add(candidate)
                        if candidate.rstrip("/") not in self.prefix_patterns:
                            self.prefix_patterns.append(candidate.rstrip("/"))

    def _resolve_candidates(self, pattern: str) -> list[str]:
        if os.path.isabs(pattern):
            abs_p = os.path.abspath(pattern)
            return list(
                dict.fromkeys(
                    [
                        _normalize_for_match(abs_p),
                        _normalize_for_match(os.path.realpath(abs_p)),
                    ]
                )
            )
        candidates: list[str] = []
        dirs = list(self.search_dirs) if self.search_dirs else [os.getcwd()]
        for directory in dirs:
            joined = os.path.abspath(os.path.join(directory, pattern))
            for variant in (joined, os.path.realpath(joined)):
                normalized = _normalize_for_match(variant)
                if normalized not in candidates:
                    candidates.append(normalized)
        return candidates

    def _resolve_glob_candidates(self, pattern: str) -> list[str]:
        norm = pattern.replace("\\", "/")
        if norm.startswith("*/"):
            return [_normalize_for_match(norm)]
        head, tail = os.path.split(pattern)
        if not head:
            dirs = list(self.search_dirs) if self.search_dirs else [os.getcwd()]
            bases: list[str] = []
            for d in dirs:
                bases.extend(self._resolve_candidates(d))
            return [f"{b.rstrip('/')}/{_normalize_for_match(tail)}" for b in bases]
        if any(ch in head for ch in _GLOB_METACHARACTERS):
            candidates = [_normalize_for_match(norm)]
            dirs = list(self.search_dirs) if self.search_dirs else [os.getcwd()]
            for d in dirs:
                for base in self._resolve_candidates(d):
                    candidates.append(f"{base.rstrip('/')}/{_normalize_for_match(norm)}")
            return list(dict.fromkeys(candidates))
        bases = self._resolve_candidates(head)
        return [f"{b.rstrip('/')}/{_normalize_for_match(tail)}" for b in bases]

    def matches(self, file_path: str | None) -> bool:
        """Return True if ``file_path`` matches any entry in this path set."""
        if not file_path:
            return False

        raw_norm = _normalize_for_match(file_path)
        decl_abs = os.path.abspath(file_path)
        decl_real = os.path.realpath(decl_abs)
        abs_norm = _normalize_for_match(decl_abs)
        real_norm = _normalize_for_match(decl_real)

        decl_forms: set[str] = {
            raw_norm,
            f"/{raw_norm.lstrip('/')}",
            abs_norm,
            real_norm,
        }

        # Strip search_dirs prefixes so relative patterns/prefixes match
        for sdir in self._resolved_search_dirs:
            prefix = f"{sdir}/"
            for form in (abs_norm, real_norm, raw_norm):
                if form.startswith(prefix):
                    rel = form[len(prefix) :]
                    if rel:
                        decl_forms.add(rel)
                        decl_forms.add(f"/{rel}")

        decl_base = _normalize_for_match(os.path.basename(decl_abs))
        if any(fnmatch.fnmatchcase(decl_base, pat) for pat in self.basename_patterns):
            return True

        if decl_forms & self.exact_paths:
            return True

        if any(fnmatch.fnmatchcase(form, pat) for form in decl_forms for pat in self.path_patterns):
            return True

        return any(form == pref or form.startswith(f"{pref}/") for form in decl_forms for pref in self.prefix_patterns)

    def cache_fingerprint(self) -> dict[str, Any]:
        return {
            "entries": list(self.raw_entries),
            "search_dirs": list(self.search_dirs),
        }




def rewrite_type_references(type_expr: TypeExpr, mapping: dict[str, str]) -> TypeExpr:
    """Recursively rewrite type names in a TypeExpr according to mapping."""
    if isinstance(type_expr, CType):
        if type_expr.name in mapping:
            return replace(type_expr, name=mapping[type_expr.name])
        return type_expr
    elif isinstance(type_expr, Pointer):
        return replace(type_expr, pointee=rewrite_type_references(type_expr.pointee, mapping))
    elif isinstance(type_expr, Reference):
        return replace(type_expr, target=rewrite_type_references(type_expr.target, mapping))
    elif isinstance(type_expr, Array):
        return replace(type_expr, element_type=rewrite_type_references(type_expr.element_type, mapping))
    elif isinstance(type_expr, FunctionPointer):
        return replace(
            type_expr,
            return_type=rewrite_type_references(type_expr.return_type, mapping),
            parameters=[replace(p, type=rewrite_type_references(p.type, mapping)) for p in type_expr.parameters],
        )
    return type_expr


class RenameTypes(PipelineFilter):
    """Rename types and update all CType references across the entire AST."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = dict(mapping)

    def visit_CType(self, node: CType, context: PipelineContext | None = None) -> CType:
        if node.name in self.mapping:
            return replace(node, name=self.mapping[node.name])
        return node

    def visit_Typedef(self, node: Typedef, context: PipelineContext | None = None) -> Typedef:
        new_name = self.mapping.get(node.name, node.name)
        new_underlying = rewrite_type_references(node.underlying_type, self.mapping)
        return replace(node, name=new_name, underlying_type=new_underlying)

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        new_name = self.mapping.get(node.name, node.name) if node.name is not None else None
        st = self.generic_visit(node, context=context)
        return replace(st, name=new_name)

    def visit_Enum(self, node: Enum, context: PipelineContext | None = None) -> Enum:
        new_name = self.mapping.get(node.name, node.name) if node.name is not None else None
        en = self.generic_visit(node, context=context)
        return replace(en, name=new_name)

    def visit_Field(self, node: Field, context: PipelineContext | None = None) -> Field:
        f = self.generic_visit(node, context=context)
        return replace(f, type=rewrite_type_references(f.type, self.mapping))

    def visit_Parameter(self, node: Parameter, context: PipelineContext | None = None) -> Parameter:
        p = self.generic_visit(node, context=context)
        return replace(p, type=rewrite_type_references(p.type, self.mapping))

    def visit_Function(self, node: Function, context: PipelineContext | None = None) -> Function:
        fn = self.generic_visit(node, context=context)
        return replace(fn, return_type=rewrite_type_references(fn.return_type, self.mapping))


class StripPrefix(PipelineFilter):
    """Strip a symbol prefix from declarations and rewrite cross-referenced types."""

    def __init__(
        self,
        prefix: str,
        targets: tuple[str, ...] = (
            "functions",
            "structs",
            "enums",
            "typedefs",
            "constants",
            "macros",
            "variables",
        ),
    ) -> None:
        self.prefix = prefix
        self.targets = tuple(targets)

    def transform(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        type_mapping: dict[str, str] = {}
        for decl in unit.declarations:
            if isinstance(decl, (Struct, Enum, Typedef)) and decl.name and decl.name.startswith(self.prefix):
                target_category = (
                    "structs" if isinstance(decl, Struct) else ("enums" if isinstance(decl, Enum) else "typedefs")
                )
                if target_category in self.targets:
                    type_mapping[decl.name] = decl.name[len(self.prefix) :]

        transformed = self.visit(unit, context=context)
        if type_mapping and isinstance(transformed, SourceUnit):
            renamer = RenameTypes(type_mapping)
            return renamer.transform(transformed, context=context)
        return transformed if isinstance(transformed, SourceUnit) else unit

    def _strip(self, name: str | None) -> str | None:
        if name is None:
            return None
        return name[len(self.prefix) :] if name.startswith(self.prefix) else name

    def visit_Function(self, node: Function, context: PipelineContext | None = None) -> Function:
        fn = self.generic_visit(node, context=context)
        if "functions" in self.targets and fn.name:
            stripped = self._strip(fn.name)
            if stripped is not None:
                return replace(fn, name=stripped)
        return fn

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        st = self.generic_visit(node, context=context)
        if "structs" in self.targets and st.name:
            return replace(st, name=self._strip(st.name))
        return st

    def visit_Enum(self, node: Enum, context: PipelineContext | None = None) -> Enum:
        en = self.generic_visit(node, context=context)
        if "enums" in self.targets and en.name:
            return replace(en, name=self._strip(en.name))
        return en

    def visit_Typedef(self, node: Typedef, context: PipelineContext | None = None) -> Typedef:
        td = self.generic_visit(node, context=context)
        if "typedefs" in self.targets and td.name:
            stripped = self._strip(td.name)
            if stripped is not None:
                return replace(td, name=stripped)
        return td

    def visit_Constant(self, node: Constant, context: PipelineContext | None = None) -> Constant:
        cn = self.generic_visit(node, context=context)
        if "constants" in self.targets and cn.name:
            stripped = self._strip(cn.name)
            if stripped is not None:
                return replace(cn, name=stripped)
        return cn

    def visit_MacroFunction(self, node: MacroFunction, context: PipelineContext | None = None) -> MacroFunction:
        mf = self.generic_visit(node, context=context)
        if "macros" in self.targets and mf.name:
            stripped = self._strip(mf.name)
            if stripped is not None:
                return replace(mf, name=stripped)
        return mf

    def visit_Variable(self, node: Variable, context: PipelineContext | None = None) -> Variable:
        v = self.generic_visit(node, context=context)
        if "variables" in self.targets and v.name:
            stripped = self._strip(v.name)
            if stripped is not None:
                return replace(v, name=stripped)
        return v


class MakeOpaque(PipelineFilter):
    """Converts named struct definitions into opaque handles by stripping fields."""

    def __init__(self, names: str | Sequence[str]) -> None:
        self.names = {names} if isinstance(names, str) else set(names)

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        st = self.generic_visit(node, context=context)
        if st.name in self.names:
            return replace(st, fields=[], nested_records=[])
        return st


class FilterByHeader(PipelineFilter):
    """Filter declarations based on source file location allowlist/denylist patterns.

    Supports both ``FilterByHeader(patterns, mode="include" | "exclude")`` and
    ``FilterByHeader(allowlist=..., denylist=..., header_path=..., include_dirs=..., search_dirs=...)``.
    Deny rules always take precedence over allow rules (``deny wins over allow``).
    When an allowlist is active, declarations with ``location is None`` are dropped.
    """

    def __init__(
        self,
        patterns: Sequence[str] | None = None,
        mode: Literal["include", "exclude"] = "include",
        *,
        allowlist: Sequence[str] | None = None,
        denylist: Sequence[str] | None = None,
        allow: Sequence[str] | None = None,
        deny: Sequence[str] | None = None,
        header_path: str = "",
        hdrname: str = "",
        include_dirs: Sequence[str] | None = None,
        search_dirs: Sequence[str] | None = None,
    ) -> None:
        if allow is not None and allowlist is None:
            allowlist = allow
        if deny is not None and denylist is None:
            denylist = deny
        if patterns is not None:
            if mode == "include" and allowlist is None:
                allowlist = list(patterns)
            elif mode == "exclude" and denylist is None:
                denylist = list(patterns)
        self.patterns: list[str] = list(patterns) if patterns is not None else list(allowlist or denylist or ())
        self.mode: Literal["include", "exclude"] = mode
        self.allowlist: list[str] = list(allowlist) if allowlist is not None else []
        self.denylist: list[str] = list(denylist) if denylist is not None else []
        self.header_path: str = header_path or hdrname
        self.include_dirs: list[str] = [str(d) for d in include_dirs] if include_dirs is not None else []
        self._explicit_search_dirs: list[str] | None = (
            [str(d) for d in search_dirs] if search_dirs is not None else None
        )

    def visit_SourceUnit(self, node: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        effective_hdr = self.header_path or node.path
        sdirs = _build_search_dirs(
            header_path=effective_hdr,
            include_dirs=self.include_dirs,
            search_dirs=self._explicit_search_dirs,
        )
        allow_set = _PathSet(self.allowlist, sdirs) if self.allowlist else None
        deny_set = _PathSet(self.denylist, sdirs) if self.denylist else None

        filtered_decls: list[Declaration] = []
        for decl in node.declarations:
            loc = getattr(decl, "location", None)
            file_path = loc.file if loc and loc.file else None

            if allow_set is not None:
                if file_path is None or not allow_set.matches(file_path):
                    continue

            if deny_set is not None and file_path is not None and deny_set.matches(file_path):
                continue

            filtered_decls.append(decl)
        return replace(node, declarations=filtered_decls)

    def cache_fingerprint(self) -> dict[str, Any]:
        return {
            "filter": self.__class__.__name__,
            "config": {
                "allowlist": list(self.allowlist),
                "denylist": list(self.denylist),
                "header_path": self.header_path,
                "include_dirs": list(self.include_dirs),
                "search_dirs": list(self._explicit_search_dirs) if self._explicit_search_dirs is not None else None,
            },
        }


def _matches_symbol_pattern(name: str, pattern: str) -> bool:
    """Match a symbol name against a glob or regex pattern."""
    if not pattern:
        return False
    if pattern.startswith("^") or pattern.endswith("$") or any(ch in pattern for ch in ("(", "|", "+")):
        try:
            return re.search(pattern, name) is not None
        except re.error:
            pass
    return fnmatch.fnmatchcase(name, pattern)


def _strip_elaborated_prefix(type_name: str) -> str:
    """Strip leading 'struct ', 'union ', or 'enum ' from a CType name."""
    for prefix in ("struct ", "union ", "enum "):
        if type_name.startswith(prefix):
            return type_name[len(prefix) :].strip()
    return type_name.strip()


def _collect_referenced_type_names(type_expr: TypeExpr) -> set[str]:
    """Collect all bare type names referenced inside a TypeExpr."""
    if isinstance(type_expr, CType):
        clean = _strip_elaborated_prefix(type_expr.name)
        return {clean, type_expr.name} if clean else {type_expr.name}
    if isinstance(type_expr, Pointer):
        return _collect_referenced_type_names(type_expr.pointee)
    if isinstance(type_expr, Reference):
        return _collect_referenced_type_names(type_expr.target)
    if isinstance(type_expr, Array):
        return _collect_referenced_type_names(type_expr.element_type)
    if isinstance(type_expr, FunctionPointer):
        names = _collect_referenced_type_names(type_expr.return_type)
        for p in type_expr.parameters:
            names.update(_collect_referenced_type_names(p.type))
        return names
    return set()


class FilterBySymbol(PipelineFilter):
    """Filter declarations by symbol name/glob/regex patterns.

    When ``cascade_excluded_types=True`` (the default), any ``Function``,
    ``Variable``, ``Typedef``, or ``Struct`` field that references an excluded
    type (such as an internal opaque struct excluded by pattern) is also pruned.
    """

    def __init__(
        self,
        patterns: Sequence[str] | None = None,
        mode: Literal["include", "exclude"] = "include",
        *,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        cascade_excluded_types: bool = True,
    ) -> None:
        if patterns is not None:
            if mode == "include" and include_patterns is None:
                include_patterns = list(patterns)
            elif mode == "exclude" and exclude_patterns is None:
                exclude_patterns = list(patterns)
        self.include_patterns: list[str] = list(include_patterns) if include_patterns is not None else []
        self.exclude_patterns: list[str] = list(exclude_patterns) if exclude_patterns is not None else []
        self.cascade_excluded_types: bool = cascade_excluded_types

    def _is_symbol_kept(self, name: str | None) -> bool:
        if name is None:
            return not bool(self.include_patterns)
        if self.include_patterns and not any(_matches_symbol_pattern(name, p) for p in self.include_patterns):
            return False
        return not (self.exclude_patterns and any(_matches_symbol_pattern(name, p) for p in self.exclude_patterns))

    def _is_type_name_excluded(self, type_name: str, excluded_types: set[str]) -> bool:
        clean = _strip_elaborated_prefix(type_name)
        if type_name in excluded_types or clean in excluded_types:
            return True
        return bool(self.exclude_patterns and any(_matches_symbol_pattern(clean, p) for p in self.exclude_patterns))

    def _type_expr_references_excluded(self, type_expr: TypeExpr, excluded_types: set[str]) -> bool:
        return any(
            self._is_type_name_excluded(ref_name, excluded_types)
            for ref_name in _collect_referenced_type_names(type_expr)
        )

    def visit_SourceUnit(self, node: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        all_declared_types: set[str] = set()
        kept_decls: list[Declaration] = []
        kept_declared_types: set[str] = set()

        for decl in node.declarations:
            decl_name = getattr(decl, "name", None)
            if isinstance(decl, (Struct, Enum, Typedef)) and decl_name:
                all_declared_types.add(decl_name)
            if self._is_symbol_kept(decl_name):
                kept_decls.append(decl)
                if isinstance(decl, (Struct, Enum, Typedef)) and decl_name:
                    kept_declared_types.add(decl_name)

        if not self.cascade_excluded_types:
            return replace(node, declarations=kept_decls)

        excluded_types: set[str] = all_declared_types - kept_declared_types

        # Compute transitive closure over Typedef declarations that reference excluded types
        changed = True
        while changed:
            changed = False
            next_decls: list[Declaration] = []
            remaining_type_defs: set[str] = set()
            for decl in kept_decls:
                if isinstance(decl, Typedef) and self._type_expr_references_excluded(
                    decl.underlying_type, excluded_types
                ):
                    changed = True
                    continue
                next_decls.append(decl)
                if isinstance(decl, (Struct, Enum, Typedef)) and decl.name:
                    remaining_type_defs.add(decl.name)
            newly_excluded = (all_declared_types - remaining_type_defs) - excluded_types
            if newly_excluded:
                excluded_types.update(newly_excluded)
                changed = True
            kept_decls = next_decls

        final_decls: list[Declaration] = []
        for decl in kept_decls:
            if isinstance(decl, Function):
                if self._type_expr_references_excluded(decl.return_type, excluded_types):
                    continue
                if any(self._type_expr_references_excluded(p.type, excluded_types) for p in decl.parameters):
                    continue
                final_decls.append(decl)
            elif isinstance(decl, Variable):
                if self._type_expr_references_excluded(decl.type, excluded_types):
                    continue
                final_decls.append(decl)
            elif isinstance(decl, Struct):
                clean_fields = [
                    f for f in decl.fields if not self._type_expr_references_excluded(f.type, excluded_types)
                ]
                final_decls.append(replace(decl, fields=clean_fields) if clean_fields != decl.fields else decl)
            else:
                final_decls.append(decl)

        return replace(node, declarations=final_decls)


class FilterDeprecated(PipelineFilter):
    """Filter out declarations, struct fields, and methods marked ``is_deprecated=True``."""

    def visit_SourceUnit(self, node: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        filtered: list[Declaration] = []
        for decl in node.declarations:
            if getattr(decl, "is_deprecated", False):
                continue
            if isinstance(decl, Struct):
                clean_fields = [f for f in decl.fields if not getattr(f, "is_deprecated", False)]
                clean_methods = [m for m in decl.methods if not getattr(m, "is_deprecated", False)]
                if clean_fields != decl.fields or clean_methods != decl.methods:
                    decl = replace(decl, fields=clean_fields, methods=clean_methods)
            filtered.append(decl)
        return replace(node, declarations=filtered)


def normalize_binary_symbol(raw_symbol: str) -> set[str]:
    """Normalize a raw binary symbol across Mach-O, ELF, 32-bit COFF, and PE formats.

    Handles:
    - PE import thunks (``__imp_foo`` -> ``foo``, ``__imp__foo`` -> ``foo`` / ``_foo``)
    - ELF symbol versions (``foo@@GLIBC_2.2.5`` -> ``foo``)
    - 32-bit COFF / stdcall / fastcall decorations (``_foo@8`` -> ``foo``, ``@foo@8`` -> ``foo``)
    - Mach-O / COFF leading underscore (``_foo`` -> ``foo`` and ``_foo``)
    """
    sym = raw_symbol.strip()
    if not sym:
        return set()

    # Strip ELF version suffixes (e.g., @@GLIBC_2.2.5 or @VERS_1.0)
    if "@@" in sym:
        sym = sym.split("@@", 1)[0]
    elif "@" in sym:
        prefix, suffix = sym.rsplit("@", 1)
        if prefix and not suffix.isdigit():
            sym = prefix

    # Strip PE __imp_ prefix
    if sym.startswith("__imp_"):
        sym = sym[len("__imp_") :]

    # Strip fastcall leading '@' when paired with trailing '@<digits>'
    if sym.startswith("@") and re.search(r"@\d+$", sym):
        sym = sym[1:]

    # Strip 32-bit COFF stdcall/fastcall trailing '@<digits>'
    sym = re.sub(r"@\d+$", "", sym)
    if not sym:
        return set()

    results: set[str] = {sym}
    # Normalize Mach-O / COFF leading '_'
    if sym.startswith("_") and len(sym) > 1:
        results.add(sym[1:])
        if sym.startswith("__") and len(sym) > 2:
            results.add(sym[2:])
    return results


_NM_DEFINED_CODES = frozenset({"T", "D", "B", "R", "S", "C", "W", "V", "A", "G"})


def _parse_symbol_output(output: str) -> set[str]:
    """Parse symbol names from nm, objdump -T, dumpbin /EXPORTS, or plain symbol list text."""
    symbols: set[str] = set()
    in_dumpbin_exports = False

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.endswith(":"):
            continue

        # dumpbin /EXPORTS table detection
        if "ordinal" in line.lower() and "hint" in line.lower() and "rva" in line.lower():
            in_dumpbin_exports = True
            continue
        if in_dumpbin_exports:
            if line.startswith("Summary"):
                in_dumpbin_exports = False
                continue
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                sym_candidate = parts[3].split("=", 1)[0]
                symbols.update(normalize_binary_symbol(sym_candidate))
                continue

        parts = line.split()
        # Standard nm output: [address] <type_code> <symbol>
        if len(parts) >= 2 and len(parts[-2]) == 1 and parts[-2] in _NM_DEFINED_CODES:
            symbols.update(normalize_binary_symbol(parts[-1]))
            continue
        if len(parts) == 2 and len(parts[0]) == 1 and parts[0] in _NM_DEFINED_CODES:
            symbols.update(normalize_binary_symbol(parts[1]))
            continue

        # objdump -T output: <addr> <flags> <section> <size> [version] <symbol>
        if len(parts) >= 5 and re.fullmatch(r"[0-9a-fA-F]+", parts[0]):
            if "*UND*" in parts:
                continue
            symbols.update(normalize_binary_symbol(parts[-1]))
            continue

        # Plain 1-symbol-per-line list
        if len(parts) == 1 and re.fullmatch(r"[@_A-Za-z0-9.$]+", parts[0]):
            symbols.update(normalize_binary_symbol(parts[0]))

    return symbols


def extract_exported_symbols(binary_path: Path | str) -> set[str]:
    """Extract normalized exported C symbols from a shared library or symbol file."""
    path = Path(binary_path)
    if not path.is_file():
        return set()

    commands: list[list[str]] = []
    if sys.platform == "darwin":
        if shutil.which("nm"):
            commands.append(["nm", "-gU", str(path)])
            commands.append(["nm", "-g", str(path)])
    elif sys.platform == "win32":
        if shutil.which("dumpbin"):
            commands.append(["dumpbin", "/EXPORTS", str(path)])
        if shutil.which("llvm-nm"):
            commands.append(["llvm-nm", "-g", "--defined-only", str(path)])
        if shutil.which("nm"):
            commands.append(["nm", "-g", "--defined-only", str(path)])
    else:
        if shutil.which("nm"):
            commands.append(["nm", "-D", "--defined-only", str(path)])
            commands.append(["nm", "-g", "--defined-only", str(path)])
        if shutil.which("objdump"):
            commands.append(["objdump", "-T", str(path)])

    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = _parse_symbol_output(proc.stdout)
                if parsed:
                    return parsed
        except (OSError, subprocess.SubprocessError):
            continue

    # Fallback: if binary_path is a text file containing nm/objdump/dumpbin output or symbol names
    try:
        text = path.read_text(encoding="utf-8")
        return _parse_symbol_output(text)
    except (OSError, UnicodeDecodeError):
        return set()


class FilterUnexportedSymbolsTransform(PipelineFilter):
    """Filter external-linkage Functions and Variables against a compiled shared library's exports.

    Only external-linkage declarations where ``isinstance(d, (Function, Variable))``
    and ``not d.is_static and not d.is_inline`` are checked against ``exported_symbols``.
    All ``static inline`` header functions, structs, enums, typedefs, and constants
    are unconditionally preserved.
    """

    def __init__(self, exported_symbols: Set[str] | Sequence[str] | Path | str) -> None:
        self.binary_path: Path | None = None
        raw_symbols: set[str] = set()
        if isinstance(exported_symbols, (Path, str)):
            self.binary_path = Path(exported_symbols)
            raw_symbols = extract_exported_symbols(self.binary_path)
        else:
            for s in exported_symbols:
                raw_symbols.update(normalize_binary_symbol(str(s)))
        self.exported_symbols: frozenset[str] = frozenset(raw_symbols)

    def visit_SourceUnit(self, node: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        filtered: list[Declaration] = []
        for decl in node.declarations:
            if (
                isinstance(decl, (Function, Variable))
                and not getattr(decl, "is_static", False)
                and not getattr(decl, "is_inline", False)
                and decl.name not in self.exported_symbols
            ):
                continue
            filtered.append(decl)
        return replace(node, declarations=filtered)

    def cache_fingerprint(self) -> dict[str, Any]:
        binary_sha256: str | None = None
        if self.binary_path is not None and self.binary_path.is_file():
            try:
                binary_sha256 = hashlib.sha256(self.binary_path.read_bytes()).hexdigest()
            except OSError:
                binary_sha256 = None
        return {
            "filter": self.__class__.__name__,
            "config": {
                "binary_path": str(self.binary_path) if self.binary_path is not None else None,
                "binary_sha256": binary_sha256,
                "exported_symbols": sorted(self.exported_symbols),
            },
        }


FilterUnexportedSymbols = FilterUnexportedSymbolsTransform


class StripPaddingFields(PipelineFilter):
    """Pipeline filter replacing the legacy procedural strip_padding_fields function."""

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        new_fields = [
            replace(f, anonymous_struct=self.visit_Struct(f.anonymous_struct, context=context))
            if f.anonymous_struct
            else f
            for f in node.fields
            if not f.is_padding
        ]
        new_nested = [self.visit_Struct(n, context=context) for n in node.nested_records]
        return replace(node, fields=new_fields, nested_records=new_nested)

