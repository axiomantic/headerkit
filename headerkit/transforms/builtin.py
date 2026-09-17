"""Built-in IR filters with type cross-reference rewriting."""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

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
)
from headerkit.transforms.filter import PipelineFilter


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
        targets: tuple[str, ...] = ("functions", "structs", "enums", "typedefs", "constants", "macros"),
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
    """Filters declarations based on source file location patterns."""

    def __init__(self, patterns: Sequence[str], mode: Literal["include", "exclude"] = "include") -> None:
        self.patterns = list(patterns)
        self.mode = mode

    def visit_SourceUnit(self, node: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        filtered_decls: list[Declaration] = []
        for decl in node.declarations:
            loc = getattr(decl, "location", None)
            file_path = loc.file if loc and loc.file else ""
            matched = any(fnmatch.fnmatch(file_path, p) or fnmatch.fnmatch(f"/{file_path}", p) for p in self.patterns)
            if (self.mode == "include" and matched) or (self.mode == "exclude" and not matched) or not file_path:
                filtered_decls.append(decl)
        return replace(node, declarations=filtered_decls)


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
