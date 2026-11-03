"""Nim C header cleanup transform for HeaderKit IR."""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, ClassVar

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
from headerkit.transforms.filter import PipelineFilter, TransformOption, register_transform

_NIM_STORAGE_MACROS: frozenset[str] = frozenset(
    {
        "N_LIB_PRIVATE",
        "N_LIB_EXPORT",
        "N_LIB_IMPORT",
        "N_LIB_EXPORT_VAR",
        "NIM_EXTERNC",
        "N_NIM_DEPRECATED",
    }
)

_NIM_CALLCONV_MACROS: frozenset[str] = frozenset(
    {
        "N_CDECL",
        "N_NOCONV",
        "N_STDCALL",
        "N_FASTCALL",
        "N_INLINE",
        "N_NOINLINE",
        "N_NIMCALL",
        "N_SYSCALL",
        "N_THISCALL",
        "N_SAFECALL",
        "N_CLOSURE",
    }
)

_NIM_CALLCONV_PTR_MACROS: frozenset[str] = frozenset(
    {
        "N_CDECL_PTR",
        "N_NOCONV_PTR",
        "N_STDCALL_PTR",
        "N_FASTCALL_PTR",
        "N_INLINE_PTR",
        "N_NOINLINE_PTR",
        "N_NIMCALL_PTR",
        "N_SYSCALL_PTR",
        "N_THISCALL_PTR",
        "N_SAFECALL_PTR",
        "N_CLOSURE_PTR",
    }
)


@functools.lru_cache(maxsize=1)
def _find_nim_libpath() -> str | None:
    """Locate the Nim standard library directory containing ``nimbase.h``."""
    nim_bin = shutil.which("nim")
    if not nim_bin:
        return None
    try:
        res = subprocess.run(
            ["nim", "dump", "--dump.format:json", "."],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            start = res.stdout.find("{")
            end = res.stdout.rfind("}")
            if start != -1 and end > start:
                data = json.loads(res.stdout[start : end + 1])
                libpath = data.get("libpath")
                if isinstance(libpath, str) and os.path.isfile(os.path.join(libpath, "nimbase.h")):
                    return libpath
    except (subprocess.SubprocessError, OSError, ValueError):
        pass

    candidate = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(nim_bin)), "..", "lib"))
    if os.path.isfile(os.path.join(candidate, "nimbase.h")):
        return candidate
    return None


def _has_nimbase_in_dirs(
    include_dirs: list[str] | None,
    extra_args: list[str] | None,
    filename: str,
) -> bool:
    """Return True if ``nimbase.h`` already exists in user-supplied include directories."""
    search_dirs: list[str] = []
    file_dir = os.path.dirname(os.path.abspath(filename))
    if file_dir:
        search_dirs.append(file_dir)
    if include_dirs:
        search_dirs.extend(include_dirs)
    if extra_args:
        i = 0
        while i < len(extra_args):
            arg = extra_args[i]
            if arg in ("-I", "-isystem") and i + 1 < len(extra_args):
                search_dirs.append(extra_args[i + 1])
                i += 2
                continue
            for prefix in ("-I", "-isystem"):
                if arg.startswith(prefix) and len(arg) > len(prefix):
                    search_dirs.append(arg[len(prefix) :])
                    break
            i += 1
    return any(os.path.isfile(os.path.join(d, "nimbase.h")) for d in search_dirs)


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
        "NimSeqV2",
        "NimStrPayload",
        "NimStringV2",
        "RefHeader",
        "CellSeq",
        "GcStat",
        "TNimTypeV2",
        "RootObj",
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
    "tySequence__",
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
_NIM_PARAM_SUFFIX_RE = re.compile(r"_p\d+$")
_NIM_TYOBJECT_RE = re.compile(r"^(struct\s+|union\s+)?tyObject_([A-Za-z_][A-Za-z0-9_]*)__[A-Za-z0-9_]+$")
_NIM_TYENUM_RE = re.compile(r"^(enum\s+)?tyEnum_([A-Za-z_][A-Za-z0-9_]*)__[A-Za-z0-9_]+$")
_NIM_TYPROC_RE = re.compile(r"^tyProc__[A-Za-z0-9_]+$")


def _unmangle_nim_colon_name(name: str) -> str:
    """Unmangle Nim compiler ``_colon_<name>`` C identifier suffixes."""
    match = _NIM_COLON_MANGLE_RE.search(name)
    if match:
        prefix = name[: match.start()]
        if prefix:
            return prefix
    return name


def _unmangle_nim_type_name(name: str) -> str:
    """Unmangle Nim ``ccgtypes.nim`` ``tyObject_<Name>__<hash>`` and ``tyEnum_<Name>__<hash>`` names."""
    obj_match = _NIM_TYOBJECT_RE.match(name)
    if obj_match:
        prefix = obj_match.group(1) or ""
        return f"{prefix}{obj_match.group(2)}"
    enum_match = _NIM_TYENUM_RE.match(name)
    if enum_match:
        prefix = enum_match.group(1) or ""
        return f"{prefix}{enum_match.group(2)}"
    return name


def _clean_nim_identifier(name: str | None) -> str | None:
    """Strip at most one trailing Nim C parameter suffix (``_p0``) and template gensyms (```gensym0``)."""
    if not name:
        return name
    cleaned = _unmangle_nim_colon_name(name)
    # Strip at most ONE trailing _p<digits> disambiguation suffix
    p_match = _NIM_PARAM_SUFFIX_RE.search(cleaned)
    if p_match and p_match.start() > 0:
        cleaned = cleaned[: p_match.start()]
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


def _is_direct_nim_bool(type_expr: TypeExpr) -> bool:
    """Return True if ``type_expr`` is directly ``CType('NIM_BOOL')``."""
    return isinstance(type_expr, CType) and type_expr.name == "NIM_BOOL"


def _parse_c_type_str(type_str: str) -> TypeExpr:
    """Parse a C type string (such as ``const char **`` or ``NI32 *``) into a ``TypeExpr``."""
    s = type_str.strip()
    m_ptr = re.search(r"\*\s*((?:(?:const|volatile|restrict|__restrict)\b\s*)*)$", s)
    if m_ptr:
        prefix = s[: m_ptr.start()].strip()
        qual_str = m_ptr.group(1).strip()
        ptr_quals = [q for q in qual_str.split() if q]
        return Pointer(pointee=_parse_c_type_str(prefix), qualifiers=ptr_quals)

    quals: list[str] = []
    tokens = s.split()
    base_tokens: list[str] = []
    for tok in tokens:
        if tok in ("const", "volatile", "restrict", "__restrict"):
            quals.append(tok)
        else:
            base_tokens.append(tok)
    base_name = " ".join(base_tokens) if base_tokens else s
    if base_name == "NCSTRING":
        return Pointer(pointee=CType(name="char", qualifiers=quals, is_elaborated=False))
    mapped = _NIM_SCALAR_TYPE_MAP.get(base_name)
    if mapped is not None:
        return CType(name=mapped, qualifiers=quals)
    unmangled = _unmangle_nim_type_name(base_name)
    return CType(name=unmangled, qualifiers=quals)


def _rewrite_nim_type(
    type_expr: TypeExpr,
    typroc_map: dict[str, FunctionPointer] | None = None,
) -> TypeExpr:
    """Recursively normalize Nim ``nimbase.h`` and ``ccgtypes.nim`` C types into standard C99 ``TypeExpr`` nodes."""
    if isinstance(type_expr, CType):
        if "*" in type_expr.name:
            qual_prefix = f"{' '.join(type_expr.qualifiers)} " if type_expr.qualifiers else ""
            return _parse_c_type_str(f"{qual_prefix}{type_expr.name}")
        if typroc_map and type_expr.name in typroc_map:
            return typroc_map[type_expr.name]
        if type_expr.name == "NCSTRING":
            return Pointer(pointee=CType(name="char", qualifiers=list(type_expr.qualifiers), is_elaborated=False))
        mapped = _NIM_SCALAR_TYPE_MAP.get(type_expr.name)
        if mapped is not None:
            return replace(type_expr, name=mapped)
        unmangled = _unmangle_nim_type_name(type_expr.name)
        if unmangled != type_expr.name:
            return replace(type_expr, name=unmangled)
        return type_expr
    if isinstance(type_expr, Pointer):
        return replace(type_expr, pointee=_rewrite_nim_type(type_expr.pointee, typroc_map))
    if isinstance(type_expr, Reference):
        return replace(type_expr, target=_rewrite_nim_type(type_expr.target, typroc_map))
    if isinstance(type_expr, Array):
        return replace(type_expr, element_type=_rewrite_nim_type(type_expr.element_type, typroc_map))
    if isinstance(type_expr, FunctionPointer):
        new_params: list[Parameter] = []
        for p in type_expr.parameters:
            was_nim_bool = _is_direct_nim_bool(p.type)
            p_clean = replace(
                p,
                name=_clean_nim_identifier(p.name),
                type=_rewrite_nim_type(p.type, typroc_map),
            )
            if was_nim_bool and hasattr(p_clean, "type_overrides"):
                overrides = dict(getattr(p_clean, "type_overrides", {}) or {})
                overrides.setdefault("pyi", "bool")
                p_clean = replace(p_clean, type_overrides=overrides)
            new_params.append(p_clean)
        return replace(
            type_expr,
            return_type=_rewrite_nim_type(type_expr.return_type, typroc_map),
            parameters=new_params,
        )
    return type_expr


class NimCleanupTransform(PipelineFilter):
    """Clean up Nim ``--header`` C declarations.

    1. Strips at most one trailing Nim C parameter disambiguation suffix (``_p0``, ``_p1``, etc.)
       and template gensym suffixes (```gensym0``, ``_gensym0``).
    2. Filters out Nim internal ``signalHandler(int sign_p0)`` and runtime symbols if present.
    3. Rewrites ``nimbase.h`` C types (``NCSTRING`` -> ``char*``, ``NI`` -> ``intptr_t``, etc.),
       unmangles ``tyObject_<Name>__<hash>`` and ``tyEnum_<Name>__<hash>``, and inlines
       compiler-synthesized ``tyProc__<hash>`` typedefs into their underlying ``FunctionPointer``.
    """

    supported_options: ClassVar[tuple[TransformOption, ...]] = (
        TransformOption(
            "filtered_functions",
            "Additional function or symbol names to filter out",
            default=[],
            type=list,
        ),
        TransformOption(
            "keep_nim_main",
            "Retain NimMain in the cleaned output",
            default=False,
            type=bool,
        ),
    )

    @classmethod
    def backend_options(cls) -> dict[str, Any]:
        """Return backend parse options required for Nim ``--header`` C files."""
        return {
            "storage_macros": sorted(_NIM_STORAGE_MACROS),
            "callconv_macros": sorted(_NIM_CALLCONV_MACROS),
            "callconv_ptr_macros": sorted(_NIM_CALLCONV_PTR_MACROS),
            "system_headers": ["nimbase.h"],
            "skip_function_bodies": True,
        }

    def prepare_parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str],
        extra_args: list[str],
    ) -> tuple[list[str], list[str], dict[str, Any]]:
        """Resolve ``nimbase.h`` include path, ``NIM_INTBITS``, and CST macro sets before parsing."""
        inc = list(include_dirs)
        args = list(extra_args)
        if "NIM_INTBITS" not in code and not any("NIM_INTBITS" in a for a in args):
            args.append(f"-DNIM_INTBITS={struct.calcsize('P') * 8}")
        if not _has_nimbase_in_dirs(inc, args, filename):
            nim_libpath = _find_nim_libpath()
            if nim_libpath is not None:
                args.append(f"-isystem{nim_libpath}")
        return inc, args, self.backend_options()

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
        self._active_typroc_map: dict[str, FunctionPointer] = {}

    def _is_internal_symbol(self, name: str | None) -> bool:
        if not name:
            return False
        unmangled = _unmangle_nim_type_name(name)
        for candidate in (name, unmangled):
            if candidate in self.filtered_functions:
                return True
            if candidate.startswith(self.filtered_prefixes):
                return True
        return False

    def apply(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        """Apply the cleanup transform to a SourceUnit."""
        return self.transform(unit, context=context)

    def __call__(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        """Allow direct invocation as a callable."""
        return self.transform(unit, context=context)

    def transform(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        typroc_map: dict[str, FunctionPointer] = {}
        for decl in unit.declarations:
            if isinstance(decl, Typedef) and decl.name and _NIM_TYPROC_RE.match(decl.name):
                underlying = decl.underlying_type
                fp: FunctionPointer | None = None
                if isinstance(underlying, FunctionPointer):
                    fp = underlying
                elif isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
                    fp = underlying.pointee
                if fp is not None:
                    cleaned_fp = _rewrite_nim_type(fp, typroc_map)
                    if isinstance(cleaned_fp, FunctionPointer):
                        typroc_map[decl.name] = cleaned_fp

        prev_map = self._active_typroc_map
        self._active_typroc_map = typroc_map
        try:
            transformed = super().transform(unit, context=context)
        finally:
            self._active_typroc_map = prev_map

        # Deduplicate redundant self-typedefs created by tyObject_/tyEnum_ unmangling
        seen_typedefs: set[tuple[str, str, bool | None]] = set()
        deduped_decls = []
        for decl in transformed.declarations:
            if isinstance(decl, Typedef):
                is_elab = decl.underlying_type.is_elaborated if isinstance(decl.underlying_type, CType) else None
                if isinstance(decl.underlying_type, CType) and decl.underlying_type.name == decl.name and not is_elab:
                    continue
                key = (decl.name, str(decl.underlying_type), is_elab)
                if key in seen_typedefs:
                    continue
                seen_typedefs.add(key)
            deduped_decls.append(decl)
        return replace(transformed, declarations=deduped_decls)

    def visit_Function(self, node: Function, context: PipelineContext | None = None) -> Function | None:
        if self._is_internal_symbol(node.name):
            return None
        was_nim_bool = _is_direct_nim_bool(node.return_type)
        fn = self.generic_visit(node, context=context)
        new_ret = _rewrite_nim_type(fn.return_type, self._active_typroc_map)
        fn = replace(fn, return_type=new_ret)
        if was_nim_bool and hasattr(fn, "type_overrides"):
            overrides = dict(getattr(fn, "type_overrides", {}) or {})
            overrides.setdefault("pyi", "bool")
            fn = replace(fn, type_overrides=overrides)
        return fn

    def visit_Parameter(self, node: Parameter, context: PipelineContext | None = None) -> Parameter:
        was_nim_bool = _is_direct_nim_bool(node.type)
        param = self.generic_visit(node, context=context)
        new_name = _clean_nim_identifier(param.name)
        new_type = _rewrite_nim_type(param.type, self._active_typroc_map)
        if new_name != param.name or new_type != param.type:
            param = replace(param, name=new_name, type=new_type)
        if was_nim_bool and hasattr(param, "type_overrides"):
            overrides = dict(getattr(param, "type_overrides", {}) or {})
            overrides.setdefault("pyi", "bool")
            param = replace(param, type_overrides=overrides)
        return param

    def visit_Field(self, node: Field, context: PipelineContext | None = None) -> Field:
        was_nim_bool = _is_direct_nim_bool(node.type)
        fld = self.generic_visit(node, context=context)
        new_name = _clean_nim_field_name(fld.name) if fld.name else fld.name
        new_type = _rewrite_nim_type(fld.type, self._active_typroc_map)
        if new_name != fld.name or new_type != fld.type:
            fld = replace(fld, name=new_name, type=new_type)
        if was_nim_bool and hasattr(fld, "type_overrides"):
            overrides = dict(getattr(fld, "type_overrides", {}) or {})
            overrides.setdefault("pyi", "bool")
            fld = replace(fld, type_overrides=overrides)
        return fld

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct | None:
        if self._is_internal_symbol(node.name):
            return None
        st = self.generic_visit(node, context=context)
        if st.name:
            unmangled = _unmangle_nim_type_name(st.name)
            if unmangled != st.name:
                return replace(st, name=unmangled)
        return st

    def visit_Enum(self, node: Enum, context: PipelineContext | None = None) -> Enum | None:
        if self._is_internal_symbol(node.name):
            return None
        en = self.generic_visit(node, context=context)
        new_name = _unmangle_nim_type_name(en.name) if en.name else en.name
        new_underlying = (
            _NIM_SCALAR_TYPE_MAP.get(en.underlying_type, en.underlying_type) if en.underlying_type else None
        )
        if new_name != en.name or new_underlying != en.underlying_type:
            return replace(en, name=new_name, underlying_type=new_underlying)
        return en

    def visit_Typedef(self, node: Typedef, context: PipelineContext | None = None) -> Typedef | None:
        if (
            self._is_internal_symbol(node.name)
            or node.name in _NIM_BUILTIN_TYPEDEFS
            or node.name in self._active_typroc_map
        ):
            return None
        was_nim_bool = _is_direct_nim_bool(node.underlying_type)
        td = self.generic_visit(node, context=context)
        new_name = _unmangle_nim_type_name(td.name)
        new_underlying = _rewrite_nim_type(td.underlying_type, self._active_typroc_map)
        td = replace(td, name=new_name, underlying_type=new_underlying)
        if was_nim_bool and hasattr(td, "type_overrides"):
            overrides = dict(getattr(td, "type_overrides", {}) or {})
            overrides.setdefault("pyi", "bool")
            td = replace(td, type_overrides=overrides)
        return td

    def visit_Variable(self, node: Variable, context: PipelineContext | None = None) -> Variable | None:
        if self._is_internal_symbol(node.name):
            return None
        was_nim_bool = _is_direct_nim_bool(node.type)
        var = self.generic_visit(node, context=context)
        var = replace(var, type=_rewrite_nim_type(var.type, self._active_typroc_map))
        if was_nim_bool and hasattr(var, "type_overrides"):
            overrides = dict(getattr(var, "type_overrides", {}) or {})
            overrides.setdefault("pyi", "bool")
            var = replace(var, type_overrides=overrides)
        return var

    def visit_Constant(self, node: Constant, context: PipelineContext | None = None) -> Constant | None:
        if self._is_internal_symbol(node.name):
            return None
        return node

    def visit_MacroFunction(self, node: MacroFunction, context: PipelineContext | None = None) -> MacroFunction | None:
        if self._is_internal_symbol(node.name):
            return None
        return node


register_transform(
    "nim_cleanup",
    NimCleanupTransform,
    description="Clean up Nim --header C declarations and nimbase.h types",
)
