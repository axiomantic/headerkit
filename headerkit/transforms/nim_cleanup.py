"""Nim C header cleanup transform for HeaderKit IR."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from headerkit.hooks import PipelineContext
from headerkit.ir import (
    Array,
    Constant,
    CType,
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

_NIM_INTERNAL_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "NimMain",
        "NimMainInner",
        "NimMainModule",
        "PreMain",
        "PreMainInner",
        "cmdCount",
        "cmdLine",
        "gEnv",
        "signalHandler",
        "nimFrame",
        "popFrame",
        "TFrame",
        "TFrame_",
        "nimfr_",
        "nimln_",
        "nimlf_",
        "NIM_INTBITS",
        "NIM_EmulateOverflowChecks",
    }
)

_NIM_INTERNAL_PREFIXES: tuple[str, ...] = (
    "nimGC_",
    "nimRegister",
    "nimErrorFlag",
    "nimZeroMem",
    "nimCopyMem",
    "nimSetMem",
    "nimAdd",
    "nimSub",
    "nimMul",
    "eqdestroy_",
    "eqcopy_",
    "eqsink_",
    "eqwasMoved_",
    "eqdup_",
    "atmdotdotats",
    "callDepthLimitReached__",
    "framePtr__",
)

_NIM_SCALAR_TYPE_MAP: dict[str, str] = {
    "NI": "intptr_t",
    "NU": "uintptr_t",
    "NI8": "int8_t",
    "NI16": "int16_t",
    "NI32": "int32_t",
    "NI64": "int64_t",
    "NU8": "uint8_t",
    "NU16": "uint16_t",
    "NU32": "uint32_t",
    "NU64": "uint64_t",
    "NF": "double",
    "NF64": "double",
    "NF32": "float",
    "NIM_BOOL": "bool",
    "_Bool": "bool",
    "NIM_CHAR": "char",
}

_NIM_BUILTIN_TYPEDEFS: frozenset[str] = frozenset({"NCSTRING", *_NIM_SCALAR_TYPE_MAP})

_NIM_COLON_MANGLE_RE = re.compile(r"_colon_([A-Za-z_][A-Za-z0-9_]*?)(?:_[0-9]+)?$")


def _unmangle_nim_colon_name(name: str) -> str:
    """Unmangle Nim compiler ``_colon_<name>`` C identifier suffixes."""
    match = _NIM_COLON_MANGLE_RE.search(name)
    if match:
        prefix = name[: match.start()]
        if prefix:
            return prefix
    return name


def _clean_nim_identifier(name: str | None) -> str | None:
    """Strip Nim C parameter disambiguation suffixes (``_p0``, ``_p1``) and template gensyms (```gensym0``)."""
    if not name:
        return name
    cleaned = _unmangle_nim_colon_name(name)
    while True:
        prev = cleaned
        p_idx = cleaned.rfind("_p")
        if p_idx > 0 and cleaned[p_idx + 2 :].isdigit():
            cleaned = cleaned[:p_idx]
        for sep in ("`gensym", "_gensym"):
            g_idx = cleaned.rfind(sep)
            if g_idx > 0 and cleaned[g_idx + len(sep) :].isdigit():
                cleaned = cleaned[:g_idx]
        cleaned = _unmangle_nim_colon_name(cleaned)
        if cleaned == prev:
            break
    return cleaned


def _clean_nim_field_name(name: str) -> str:
    """Clean a Nim struct field name without stripping numeric suffixes like ``_0`` or ``_1``."""
    cleaned = _unmangle_nim_colon_name(name)
    while True:
        prev = cleaned
        for sep in ("`gensym", "_gensym"):
            g_idx = cleaned.rfind(sep)
            if g_idx > 0 and cleaned[g_idx + len(sep) :].isdigit():
                cleaned = cleaned[:g_idx]
        cleaned = _unmangle_nim_colon_name(cleaned)
        if cleaned == prev:
            break
    return cleaned


def _rewrite_nim_type(type_expr: TypeExpr) -> TypeExpr:
    """Recursively normalize Nim ``nimbase.h`` C types into standard C99 ``TypeExpr`` nodes."""
    if isinstance(type_expr, CType):
        if type_expr.name == "NCSTRING":
            return Pointer(pointee=CType(name="char", qualifiers=list(type_expr.qualifiers), is_elaborated=False))
        mapped = _NIM_SCALAR_TYPE_MAP.get(type_expr.name)
        if mapped is not None:
            return replace(type_expr, name=mapped)
        return type_expr
    if isinstance(type_expr, Pointer):
        return replace(type_expr, pointee=_rewrite_nim_type(type_expr.pointee))
    if isinstance(type_expr, Reference):
        return replace(type_expr, target=_rewrite_nim_type(type_expr.target))
    if isinstance(type_expr, Array):
        return replace(type_expr, element_type=_rewrite_nim_type(type_expr.element_type))
    if isinstance(type_expr, FunctionPointer):
        return replace(
            type_expr,
            return_type=_rewrite_nim_type(type_expr.return_type),
            parameters=[
                replace(
                    p,
                    name=_clean_nim_identifier(p.name),
                    type=_rewrite_nim_type(p.type),
                )
                for p in type_expr.parameters
            ],
        )
    return type_expr


class NimCleanupTransform(PipelineFilter):
    """Clean up Nim ``--header`` C declarations.

    1. Strips Nim C parameter disambiguation suffixes (``_p0``, ``_p1``, etc.)
       and template gensym suffixes (```gensym0``, ``_gensym0``).
    2. Filters out Nim internal ``signalHandler(int sign_p0)`` and runtime symbols if present.
    3. Rewrites ``nimbase.h`` C types (``NCSTRING`` -> ``char*``, ``NI`` -> ``intptr_t``, etc.).
    """

    def __init__(
        self,
        filtered_functions: Sequence[str] | None = None,
        *,
        keep_nim_main: bool = False,
    ) -> None:
        base_filtered = set(_NIM_INTERNAL_EXACT_NAMES)
        if keep_nim_main:
            base_filtered.discard("NimMain")
        if filtered_functions is not None:
            base_filtered.update(filtered_functions)
        self.filtered_functions: frozenset[str] = frozenset(base_filtered)
        self.filtered_prefixes: tuple[str, ...] = _NIM_INTERNAL_PREFIXES

    def _is_internal_symbol(self, name: str | None) -> bool:
        if not name:
            return False
        if name in self.filtered_functions:
            return True
        return name.startswith(self.filtered_prefixes)

    def apply(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        """Apply the cleanup transform to a SourceUnit."""
        return self.transform(unit, context=context)

    def __call__(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        """Allow direct invocation as a callable."""
        return self.transform(unit, context=context)

    def visit_Function(self, node: Function, context: PipelineContext | None = None) -> Function | None:
        if self._is_internal_symbol(node.name):
            return None
        fn = self.generic_visit(node, context=context)
        return replace(fn, return_type=_rewrite_nim_type(fn.return_type))

    def visit_Parameter(self, node: Parameter, context: PipelineContext | None = None) -> Parameter:
        param = self.generic_visit(node, context=context)
        new_name = _clean_nim_identifier(param.name)
        new_type = _rewrite_nim_type(param.type)
        if new_name != param.name or new_type != param.type:
            return replace(param, name=new_name, type=new_type)
        return param

    def visit_Field(self, node: Field, context: PipelineContext | None = None) -> Field:
        fld = self.generic_visit(node, context=context)
        new_name = _clean_nim_field_name(fld.name) if fld.name else fld.name
        new_type = _rewrite_nim_type(fld.type)
        if new_name != fld.name or new_type != fld.type:
            return replace(fld, name=new_name, type=new_type)
        return fld

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct | None:
        if self._is_internal_symbol(node.name):
            return None
        return self.generic_visit(node, context=context)

    def visit_Enum(self, node: Enum, context: PipelineContext | None = None) -> Enum | None:
        if self._is_internal_symbol(node.name):
            return None
        en = self.generic_visit(node, context=context)
        if en.underlying_type and en.underlying_type in _NIM_SCALAR_TYPE_MAP:
            return replace(en, underlying_type=_NIM_SCALAR_TYPE_MAP[en.underlying_type])
        return en

    def visit_Typedef(self, node: Typedef, context: PipelineContext | None = None) -> Typedef | None:
        if self._is_internal_symbol(node.name) or node.name in _NIM_BUILTIN_TYPEDEFS:
            return None
        td = self.generic_visit(node, context=context)
        return replace(td, underlying_type=_rewrite_nim_type(td.underlying_type))

    def visit_Variable(self, node: Variable, context: PipelineContext | None = None) -> Variable | None:
        if self._is_internal_symbol(node.name):
            return None
        var = self.generic_visit(node, context=context)
        return replace(var, type=_rewrite_nim_type(var.type))

    def visit_Constant(self, node: Constant, context: PipelineContext | None = None) -> Constant | None:
        if self._is_internal_symbol(node.name):
            return None
        return node

    def visit_MacroFunction(self, node: MacroFunction, context: PipelineContext | None = None) -> MacroFunction | None:
        if self._is_internal_symbol(node.name):
            return None
        return node
