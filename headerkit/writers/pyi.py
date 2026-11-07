"""PEP 484 Python type stub (.pyi) writer for HeaderKit IR."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from keyword import iskeyword
from typing import Any, ClassVar, Literal

from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    Field,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    Reference,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.writers import WriterBackend, register_writer
from headerkit.writers.base import BaseWriter, WriterOption

_BOOL_TYPES: frozenset[str] = frozenset({"bool", "_Bool"})

_INT_TYPES: frozenset[str] = frozenset(
    {
        "char",
        "signed char",
        "unsigned char",
        "short",
        "short int",
        "signed short",
        "signed short int",
        "unsigned short",
        "unsigned short int",
        "int",
        "signed",
        "signed int",
        "unsigned",
        "unsigned int",
        "long",
        "long int",
        "signed long",
        "signed long int",
        "unsigned long",
        "unsigned long int",
        "long long",
        "long long int",
        "signed long long",
        "signed long long int",
        "unsigned long long",
        "unsigned long long int",
        "int8_t",
        "uint8_t",
        "int16_t",
        "uint16_t",
        "int32_t",
        "uint32_t",
        "int64_t",
        "uint64_t",
        "int_least8_t",
        "uint_least8_t",
        "int_least16_t",
        "uint_least16_t",
        "int_least32_t",
        "uint_least32_t",
        "int_least64_t",
        "uint_least64_t",
        "int_fast8_t",
        "uint_fast8_t",
        "int_fast16_t",
        "uint_fast16_t",
        "int_fast32_t",
        "uint_fast32_t",
        "int_fast64_t",
        "uint_fast64_t",
        "size_t",
        "ssize_t",
        "ptrdiff_t",
        "intptr_t",
        "uintptr_t",
        "intmax_t",
        "uintmax_t",
        "wchar_t",
        "char16_t",
        "char32_t",
    }
)

_FLOAT_TYPES: frozenset[str] = frozenset(
    {
        "float",
        "double",
        "long double",
    }
)


def _sanitize_identifier(name: str, fallback: str = "arg") -> str:
    """Convert a C identifier into a valid non-keyword Python identifier."""
    cleaned = name.strip()
    if not cleaned:
        return fallback
    if "`" in cleaned:
        cleaned = cleaned.replace("`", "_")
    if not cleaned.isidentifier():
        cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in cleaned)
        if cleaned and cleaned[0].isdigit():
            cleaned = f"_{cleaned}"
    if not cleaned:
        return fallback
    if iskeyword(cleaned) or cleaned in ("None", "True", "False"):
        return f"{cleaned}_"
    return cleaned


def _normalize_ctype_key(ctype: CType) -> str:
    """Build normalized C type lookup key combining signedness qualifiers and base name."""
    signed_quals = [q for q in ctype.qualifiers if q in ("signed", "unsigned")]
    base = ctype.name.strip()
    if signed_quals:
        return f"{' '.join(signed_quals)} {base}".strip()
    return base


class PyiWriter(BaseWriter):
    """PEP 484 Python type stub (.pyi) declarations."""

    name = "pyi"
    format_description = "PEP 484 Python type stub (.pyi) declarations"
    default_output_pattern = "{dir}/{stem}.pyi"
    default_extension: str = ".pyi"
    output_extension: str = ".pyi"
    cache_output: bool = True
    cache_version: str = "3"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="exclude_patterns",
            description="Regex patterns for declaration names to exclude",
            default=None,
            type=list,
        ),
        WriterOption(
            name="mode",
            description="Stub mode: 'pep484' (top-level declarations) or 'cffi' (ffi: cffi.FFI + lib: _Lib)",
            default="pep484",
            choices=("pep484", "cffi"),
            type=str,
        ),
        WriterOption(
            name="module_var",
            description="Optional module-level string variable name to declare (e.g. 'CDEF')",
            default=None,
            type=str,
        ),
        WriterOption(
            name="pointer_style",
            description="Pointer annotation style: 'any' (default) or 'ctypes' (ctypes._Pointer[T])",
            default="any",
            choices=("any", "ctypes"),
            type=str,
        ),
        WriterOption(
            name="define_patterns",
            description="Regex patterns of preprocessor macros to include on _Lib in cffi mode",
            default=None,
            type=list,
        ),
        WriterOption(
            name="extern_python",
            description=(
                "Function or function-pointer typedef symbol names in Header.declarations "
                'to emit on _Lib in cffi mode as extern "Python" callbacks'
            ),
            default=None,
            type=list,
        ),
    )
    writer_options: ClassVar[list[WriterOption]] = list(supported_options)

    def __init__(
        self,
        exclude_patterns: Sequence[str] | None = None,
        mode: Literal["pep484", "cffi"] = "pep484",
        module_var: str | None = None,
        pointer_style: Literal["any", "ctypes"] = "any",
        define_patterns: Sequence[str] | None = None,
        extern_python: Sequence[str] | None = None,
    ) -> None:
        if mode not in ("pep484", "cffi"):
            raise ValueError(f"Unsupported PyiWriter mode: {mode!r}; expected 'pep484' or 'cffi'")
        if pointer_style not in ("any", "ctypes"):
            raise ValueError(f"Unsupported pointer_style: {pointer_style!r}; expected 'any' or 'ctypes'")
        self.exclude_patterns: list[str] = list(exclude_patterns) if exclude_patterns else []
        self.mode: Literal["pep484", "cffi"] = mode
        self.module_var: str | None = module_var
        self.pointer_style: Literal["any", "ctypes"] = pointer_style
        self.define_patterns: list[str] = list(define_patterns) if define_patterns else []
        self.extern_python: list[str] = list(extern_python) if extern_python else []
        self._compiled_excludes = [re.compile(p) for p in self.exclude_patterns]
        self._compiled_defines = [re.compile(p) for p in self.define_patterns]
        self._needs_cffi = False
        self._needs_ctypes = False
        self._needs_enum = False
        self._needs_callable = False
        self._needs_any = False
        self._needs_final = False
        self._record_tags: dict[str, str] = {}
        self._enum_tags: dict[str, str] = {}
        self._typedef_map: dict[str, TypeExpr] = {}
        self._contested_tags: set[str] = set()

    def _resolve_typedef_function_pointer(
        self,
        key: str,
        _seen: frozenset[str] = frozenset(),
    ) -> FunctionPointer | None:
        if key not in self._typedef_map or key in _seen:
            return None
        curr: TypeExpr = self._typedef_map[key]
        while isinstance(curr, Pointer):
            curr = curr.pointee
        if isinstance(curr, FunctionPointer):
            return curr
        if isinstance(curr, CType):
            t_key = _normalize_ctype_key(curr)
            return self._resolve_typedef_function_pointer(t_key, _seen | {key})
        return None

    def _typedef_resolves_to_primitive_or_pointer(
        self,
        key: str,
        _seen: frozenset[str] = frozenset(),
    ) -> bool:
        if key not in self._typedef_map or key in _seen:
            return False
        underlying = self._typedef_map[key]
        if isinstance(underlying, Pointer):
            return not isinstance(underlying.pointee, FunctionPointer)
        if isinstance(underlying, CType):
            if underlying.is_elaborated is True:
                return False
            u_key = _normalize_ctype_key(underlying)
            if u_key == "void" or u_key in _BOOL_TYPES or u_key in _INT_TYPES or u_key in _FLOAT_TYPES:
                return True
            return self._typedef_resolves_to_primitive_or_pointer(u_key, _seen | {key})
        return False

    def _typedef_resolves_to_pointer_or_fnptr(
        self,
        key: str,
        _seen: frozenset[str] = frozenset(),
    ) -> bool:
        if key not in self._typedef_map or key in _seen:
            return False
        underlying = self._typedef_map[key]
        if isinstance(underlying, (FunctionPointer, Array)):
            return True
        if isinstance(underlying, Pointer):
            if isinstance(underlying.pointee, FunctionPointer):
                return True
            if isinstance(underlying.pointee, Struct):
                return bool(underlying.pointee.name and underlying.pointee.name.strip())
            if isinstance(underlying.pointee, CType):
                if underlying.pointee.is_elaborated is True:
                    return True
                p_key = _normalize_ctype_key(underlying.pointee)
                if p_key == "char":
                    return True
                return not (
                    p_key == "void"
                    or p_key in _BOOL_TYPES
                    or p_key in _INT_TYPES
                    or p_key in _FLOAT_TYPES
                    or self._typedef_resolves_to_primitive_or_pointer(p_key, _seen | {key})
                )
            return False
        if isinstance(underlying, CType) and underlying.is_elaborated is not True:
            u_key = _normalize_ctype_key(underlying)
            return self._typedef_resolves_to_pointer_or_fnptr(u_key, _seen | {key})
        return False

    def _is_excluded(self, name: str | None) -> bool:
        if not name or not self._compiled_excludes:
            return False
        return any(pat.search(name) for pat in self._compiled_excludes)

    @staticmethod
    def _is_valid_python_expr(expr: str) -> bool:
        if not expr:
            return False
        try:
            ast.parse(expr, mode="eval")
            return True
        except SyntaxError:
            return False

    def _resolve_override(self, overrides: Mapping[str, str] | None) -> str | None:
        if not overrides:
            return None
        raw = overrides.get("pyi")
        if not isinstance(raw, str) or not raw.strip():
            return None
        cleaned = raw.strip().replace("ctypes.pointer[", "ctypes._Pointer[")
        if not cleaned:
            return None
        try:
            tree = ast.parse(cleaned, mode="eval")
        except SyntaxError:
            return None
        if "cffi." in cleaned:
            self._needs_cffi = True
        if "ctypes." in cleaned:
            self._needs_ctypes = True
        names_used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        if "Any" in names_used:
            self._needs_any = True
        if "Callable" in names_used:
            self._needs_callable = True
        return cleaned

    def _format_type(
        self,
        type_expr: TypeExpr,
        *,
        is_param: bool = False,
        _seen_typedefs: frozenset[str] = frozenset(),
    ) -> str:
        if isinstance(type_expr, Struct):
            if type_expr.name and type_expr.name.strip():
                s_key = type_expr.name.strip()
                resolved = self._record_tags.get(s_key) or _sanitize_identifier(s_key, fallback="Any")
                if resolved == "Any":
                    self._needs_any = True
                return resolved
            self._needs_any = True
            return "Any"

        if isinstance(type_expr, CType):
            key = _normalize_ctype_key(type_expr)
            if type_expr.is_elaborated is True:
                resolved = self._record_tags.get(key) or self._enum_tags.get(key)
                if resolved is not None:
                    return resolved
                res = _sanitize_identifier(key, fallback="Any")
                if res == "Any":
                    self._needs_any = True
                return res
            if key == "void":
                return "None"
            if key in _BOOL_TYPES:
                return "bool"
            if key in _INT_TYPES:
                return "int"
            if key in _FLOAT_TYPES:
                return "float"
            if key in self._contested_tags and type_expr.is_elaborated is False and key not in _seen_typedefs:
                return self._format_type(
                    self._typedef_map[key],
                    is_param=is_param,
                    _seen_typedefs=_seen_typedefs | {key},
                )
            resolved = self._record_tags.get(key) or self._enum_tags.get(key)
            if resolved is not None:
                return resolved
            res = _sanitize_identifier(key, fallback="Any")
            if res == "Any":
                self._needs_any = True
                return "Any"
            if (
                is_param
                and self.mode == "cffi"
                and self.pointer_style != "ctypes"
                and self._typedef_resolves_to_pointer_or_fnptr(key)
            ):
                self._needs_cffi = True
                return f"{res} | cffi.FFI.CData"
            return res

        if isinstance(type_expr, Pointer):
            pointee = type_expr.pointee
            if isinstance(pointee, FunctionPointer) and (not pointee.is_pointer or self.pointer_style != "ctypes"):
                fn_ptr_str = self._format_function_pointer(pointee)
                if is_param and self.mode == "cffi" and self.pointer_style != "ctypes":
                    self._needs_cffi = True
                    return f"{fn_ptr_str} | cffi.FFI.CData"
                return fn_ptr_str
            if self.pointer_style == "ctypes":
                if isinstance(pointee, CType):
                    p_key = _normalize_ctype_key(pointee)
                    if pointee.is_elaborated is not True and p_key == "char":
                        return "bytes | str" if is_param else "bytes"
                    if pointee.is_elaborated is not True and p_key == "void":
                        self._needs_ctypes = True
                        return "ctypes.c_void_p"
                inner = self._format_type(pointee, is_param=False, _seen_typedefs=_seen_typedefs)
                self._needs_ctypes = True
                return f"ctypes._Pointer[{inner}]"
            if isinstance(pointee, Struct):
                if pointee.name and pointee.name.strip():
                    s_key = pointee.name.strip()
                    resolved = self._record_tags.get(s_key) or _sanitize_identifier(s_key, fallback="Any")
                    if resolved == "Any":
                        self._needs_any = True
                        return "Any"
                    if is_param and self.mode == "cffi":
                        self._needs_cffi = True
                        return f"{resolved} | cffi.FFI.CData"
                    return resolved
                if self.mode == "cffi":
                    self._needs_cffi = True
                    return "cffi.FFI.CData"
                self._needs_any = True
                return "Any"
            if isinstance(pointee, CType):
                p_key = _normalize_ctype_key(pointee)
                if pointee.is_elaborated is True:
                    resolved = self._record_tags.get(p_key) or self._enum_tags.get(p_key)
                    res = resolved if resolved is not None else _sanitize_identifier(p_key, fallback="Any")
                    if res == "Any":
                        self._needs_any = True
                        return "Any"
                    if is_param and self.mode == "cffi":
                        self._needs_cffi = True
                        return f"{res} | cffi.FFI.CData"
                    return res
                if p_key == "char":
                    if is_param:
                        if self.mode == "cffi":
                            self._needs_cffi = True
                            return "bytes | str | cffi.FFI.CData"
                        return "bytes | str"
                    return "bytes"
                if p_key == "void":
                    if self.mode == "cffi":
                        self._needs_cffi = True
                        if is_param:
                            return "cffi.FFI.CData | bytes | bytearray | memoryview"
                        return "cffi.FFI.CData"
                    self._needs_any = True
                    return "Any"
                if p_key in _BOOL_TYPES or p_key in _INT_TYPES or p_key in _FLOAT_TYPES:
                    if self.mode == "cffi":
                        self._needs_cffi = True
                        return "cffi.FFI.CData"
                    self._needs_any = True
                    return "Any"
                if p_key in self._contested_tags and pointee.is_elaborated is False and p_key not in _seen_typedefs:
                    return self._format_type(
                        Pointer(self._typedef_map[p_key]),
                        is_param=is_param,
                        _seen_typedefs=_seen_typedefs | {p_key},
                    )
                if self._typedef_resolves_to_primitive_or_pointer(p_key):
                    if self.mode == "cffi":
                        self._needs_cffi = True
                        return "cffi.FFI.CData"
                    self._needs_any = True
                    return "Any"
                resolved = self._record_tags.get(p_key) or self._enum_tags.get(p_key)
                res = resolved if resolved is not None else _sanitize_identifier(p_key, fallback="Any")
                if res == "Any":
                    self._needs_any = True
                    return "Any"
                if is_param and self.mode == "cffi":
                    self._needs_cffi = True
                    return f"{res} | cffi.FFI.CData"
                return res
            if self.mode == "cffi":
                self._needs_cffi = True
                return "cffi.FFI.CData"
            self._needs_any = True
            return "Any"

        if isinstance(type_expr, Reference):
            return self._format_type(type_expr.target, is_param=is_param)

        if isinstance(type_expr, Array):
            if (
                isinstance(type_expr.element_type, CType)
                and type_expr.element_type.is_elaborated is not True
                and _normalize_ctype_key(type_expr.element_type) == "char"
            ):
                arr_res = "bytes"
            else:
                elem = self._format_type(type_expr.element_type, is_param=False)
                arr_res = f"list[{elem}]"
            if is_param and self.mode == "cffi" and self.pointer_style != "ctypes":
                self._needs_cffi = True
                return f"{arr_res} | cffi.FFI.CData"
            return arr_res

        if isinstance(type_expr, FunctionPointer):
            fn_ptr_str = self._format_function_pointer(type_expr)
            if is_param and self.mode == "cffi" and self.pointer_style != "ctypes":
                self._needs_cffi = True
                return f"{fn_ptr_str} | cffi.FFI.CData"
            return fn_ptr_str

        self._needs_any = True
        return "Any"

    def _format_function_pointer(self, fp: FunctionPointer) -> str:
        self._needs_callable = True
        ret = self._format_type(fp.return_type, is_param=False)
        if fp.is_variadic:
            return f"Callable[..., {ret}]"
        arg_types = [
            self._resolve_override(getattr(p, "type_overrides", None)) or self._format_type(p.type, is_param=False)
            for p in fp.parameters
            if not (
                isinstance(p.type, CType)
                and not p.type.is_elaborated
                and _normalize_ctype_key(p.type) == "void"
                and not p.name
            )
        ]
        return f"Callable[[{', '.join(arg_types)}], {ret}]"

    def _format_constant(self, const: Constant, *, indent: str = "") -> list[str]:
        name = _sanitize_identifier(const.name)
        override = self._resolve_override(getattr(const, "type_overrides", None))
        if override is not None:
            line = f"{indent}{name}: {override}"
        elif const.value is not None:
            if isinstance(const.value, bool):
                line = f"{indent}{name}: bool = {const.value}"
            elif isinstance(const.value, int):
                line = f"{indent}{name}: int = {const.value}"
            elif isinstance(const.value, float):
                line = f"{indent}{name}: float = {const.value}"
            else:
                val_str = str(const.value).strip()
                if (val_str.startswith('"') and val_str.endswith('"')) or (
                    val_str.startswith("'") and val_str.endswith("'")
                ):
                    val_out = val_str if self._is_valid_python_expr(val_str) else "..."
                    line = f"{indent}{name}: str = {val_out}"
                else:
                    val_out = val_str if self._is_valid_python_expr(val_str) else "..."
                    line = f"{indent}{name}: int = {val_out}"
        elif const.type is not None:
            t_str = self._format_type(const.type, is_param=False)
            line = f"{indent}{name}: {t_str}"
        else:
            self._needs_any = True
            line = f"{indent}{name}: Any"
        lines = [line]
        doc = getattr(const, "doc", None)
        if isinstance(doc, str) and doc.strip():
            lines.extend(self._format_docstring(doc, indent=indent))
        return lines

    def _format_enum_values_as_attrs(self, enum: Enum, *, indent: str = "") -> list[str]:
        lines: list[str] = []
        next_val = 0
        for ev in enum.values:
            v_name = _sanitize_identifier(ev.name)
            if isinstance(ev.value, bool):
                val_repr = str(int(ev.value))
                next_val = int(ev.value) + 1
            elif isinstance(ev.value, int):
                val_repr = str(ev.value)
                next_val = ev.value + 1
            elif isinstance(ev.value, str) and ev.value.strip():
                raw_ev = ev.value.strip()
                val_repr = raw_ev if self._is_valid_python_expr(raw_ev) else "..."
            else:
                val_repr = str(next_val)
                next_val += 1
            lines.append(f"{indent}{v_name}: int = {val_repr}")
        return lines

    def _format_enum(self, enum: Enum) -> list[str]:
        lines: list[str] = []
        if enum.name:
            self._needs_enum = True
            cls_name = _sanitize_identifier(enum.name)
            lines.append(f"class {cls_name}(enum.IntEnum):")
            doc = getattr(enum, "doc", None)
            if isinstance(doc, str) and doc.strip():
                lines.extend(self._format_docstring(doc, indent="    "))
            if not enum.values:
                if not (isinstance(doc, str) and doc.strip()):
                    lines.append("    ...")
            else:
                next_val = 0
                for ev in enum.values:
                    v_name = _sanitize_identifier(ev.name)
                    if isinstance(ev.value, bool):
                        val_repr = str(int(ev.value))
                        next_val = int(ev.value) + 1
                    elif isinstance(ev.value, int):
                        val_repr = str(ev.value)
                        next_val = ev.value + 1
                    elif isinstance(ev.value, str) and ev.value.strip():
                        raw_ev = ev.value.strip()
                        val_repr = raw_ev if self._is_valid_python_expr(raw_ev) else "..."
                    else:
                        val_repr = str(next_val)
                        next_val += 1
                    lines.append(f"    {v_name} = {val_repr}")
        else:
            lines.extend(self._format_enum_values_as_attrs(enum, indent=""))
        return lines

    def _format_struct_fields(self, fields: Sequence[Field]) -> list[str]:
        out: list[str] = []
        for fld in fields:
            if fld.is_padding:
                continue
            if fld.is_anonymous_transparent and fld.anonymous_struct is not None:
                out.extend(self._format_struct_fields(fld.anonymous_struct.fields))
                continue
            if not fld.name:
                continue
            f_name = _sanitize_identifier(fld.name)
            f_type = self._resolve_override(getattr(fld, "type_overrides", None)) or self._format_type(
                fld.type, is_param=False
            )
            out.append(f"    {f_name}: {f_type}")
        return out

    def _format_struct(self, struct: Struct) -> list[str]:
        if not struct.name:
            return []
        cls_name = _sanitize_identifier(struct.name)
        lines: list[str] = [f"class {cls_name}:"]
        doc = getattr(struct, "doc", None)
        has_body = False
        if isinstance(doc, str) and doc.strip():
            lines.extend(self._format_docstring(doc, indent="    "))
            has_body = True

        field_lines = self._format_struct_fields(struct.fields)
        if field_lines:
            lines.extend(field_lines)
            has_body = True

        for method in struct.methods:
            if self._is_excluded(method.name):
                continue
            m_sig = self._format_function_params_and_return(method, include_self=True)
            m_name = _sanitize_identifier(method.name)
            lines.append(f"    def {m_name}{m_sig}: ...")
            has_body = True

        if not has_body:
            lines.append("    ...")
        return lines

    def _format_function_params_and_return(self, func: Function, *, include_self: bool = False) -> str:
        param_parts: list[str] = []
        if include_self and not func.is_static:
            param_parts.append("self")
        used_names: set[str] = set(param_parts)
        for idx, param in enumerate(func.parameters):
            if isinstance(param.type, CType) and _normalize_ctype_key(param.type) == "void" and not param.name:
                continue
            raw_name = param.name or f"arg{idx}"
            p_name = _sanitize_identifier(raw_name, fallback=f"arg{idx}")
            if p_name in used_names:
                p_name = f"{p_name}_{idx}"
            used_names.add(p_name)
            p_type = self._resolve_override(getattr(param, "type_overrides", None)) or self._format_type(
                param.type, is_param=True
            )
            if param.default_value is not None:
                param_parts.append(f"{p_name}: {p_type} = ...")
            else:
                param_parts.append(f"{p_name}: {p_type}")

        if func.is_variadic:
            if self.mode == "cffi" and self.pointer_style != "ctypes":
                self._needs_cffi = True
                param_parts.append("*args: cffi.FFI.CData")
            else:
                self._needs_any = True
                param_parts.append("*args: Any")

        ret_override = self._resolve_override(getattr(func, "return_type_overrides", None)) or self._resolve_override(
            getattr(func, "type_overrides", None)
        )
        ret_str = ret_override or self._format_type(func.return_type, is_param=False)
        return f"({', '.join(param_parts)}) -> {ret_str}"

    @staticmethod
    def _format_docstring(doc: str, *, indent: str = "    ") -> list[str]:
        cleaned = doc.strip().replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        if cleaned.endswith('"'):
            cleaned = cleaned[:-1] + '\\"'
        doc_lines = cleaned.splitlines()
        if len(doc_lines) == 1:
            return [f'{indent}"""{doc_lines[0]}"""']
        out = [f'{indent}"""{doc_lines[0]}']
        for dl in doc_lines[1:]:
            out.append(f"{indent}{dl}" if dl.strip() else "")
        out.append(f'{indent}"""')
        return out

    def _format_function(self, func: Function, *, indent: str = "", include_self: bool = False) -> list[str]:
        fn_name = _sanitize_identifier(func.name)
        sig = self._format_function_params_and_return(func, include_self=include_self)
        doc = getattr(func, "doc", None)
        if isinstance(doc, str) and doc.strip():
            lines = [f"{indent}def {fn_name}{sig}:"]
            lines.extend(self._format_docstring(doc, indent=f"{indent}    "))
            lines.append(f"{indent}    ...")
            return lines
        return [f"{indent}def {fn_name}{sig}: ..."]

    def _render(self, unit: SourceUnit | Header) -> str:
        """Convert Header IR into PEP 484 ``.pyi`` stub declarations."""
        self._needs_cffi = self.mode == "cffi"
        self._needs_ctypes = False
        self._needs_enum = False
        self._needs_callable = False
        self._needs_any = False
        self._needs_final = self.mode == "cffi"
        self._record_tags = {}
        self._enum_tags = {}
        self._typedef_map = {}
        self._contested_tags = set()

        defined_record_names: set[str] = set()
        raw_record_tags: set[str] = set()
        for decl in unit.declarations:
            if isinstance(decl, Struct) and decl.name and not self._is_excluded(decl.name):
                tag = decl.name.strip()
                cls_name = _sanitize_identifier(tag)
                raw_record_tags.add(tag)
                defined_record_names.add(cls_name)
                keyword = "union" if decl.is_union else "struct"
                self._record_tags[tag] = cls_name
                self._record_tags[f"{keyword} {tag}"] = cls_name
            elif isinstance(decl, Enum) and decl.name and not self._is_excluded(decl.name):
                tag = decl.name.strip()
                cls_name = _sanitize_identifier(tag)
                raw_record_tags.add(tag)
                defined_record_names.add(cls_name)
                self._enum_tags[tag] = cls_name
                self._enum_tags[f"enum {tag}"] = cls_name
            elif isinstance(decl, Typedef) and decl.name and not self._is_excluded(decl.name):
                td_name = decl.name.strip()
                self._typedef_map[td_name] = decl.underlying_type

        for td_name, underlying in self._typedef_map.items():
            if td_name in raw_record_tags:
                u_name = underlying.name.strip() if isinstance(underlying, CType) else ""
                if not (
                    isinstance(underlying, CType)
                    and (self._record_tags.get(u_name) or self._enum_tags.get(u_name)) == _sanitize_identifier(td_name)
                ):
                    self._contested_tags.add(td_name)

        body_blocks: list[str] = []
        lib_member_lines: list[str] = []
        emitted_lib_funcs: set[str] = set()

        for decl in unit.declarations:
            decl_name = getattr(decl, "name", None)
            if self._is_excluded(decl_name):
                continue

            if isinstance(decl, Constant):
                if self.mode == "cffi":
                    is_cffi_int = (
                        decl.value is not None
                        and isinstance(decl.value, int)
                        and not isinstance(decl.value, bool)
                        and not getattr(decl, "is_unsized_macro", False)
                    )
                    matches_define = bool(
                        self._compiled_defines and any(p.search(decl.name) for p in self._compiled_defines)
                    )
                    if is_cffi_int:
                        lib_member_lines.extend(self._format_constant(decl, indent="    "))
                    elif matches_define:
                        c_name = _sanitize_identifier(decl.name)
                        lib_member_lines.append(f"    {c_name}: int = ...")
                        doc = getattr(decl, "doc", None)
                        if isinstance(doc, str) and doc.strip():
                            lib_member_lines.extend(self._format_docstring(doc, indent="    "))
                else:
                    const_lines = self._format_constant(decl)
                    body_blocks.append("\n".join(const_lines))
            elif isinstance(decl, Enum):
                enum_lines = self._format_enum(decl)
                if enum_lines and (self.mode != "cffi" or decl.name):
                    body_blocks.append("\n".join(enum_lines))
                if self.mode == "cffi":
                    lib_member_lines.extend(self._format_enum_values_as_attrs(decl, indent="    "))
            elif isinstance(decl, Struct):
                struct_lines = self._format_struct(decl)
                if struct_lines:
                    body_blocks.append("\n".join(struct_lines))
            elif isinstance(decl, Typedef):
                alias_name = _sanitize_identifier(decl.name)
                target_str = self._resolve_override(getattr(decl, "type_overrides", None)) or self._format_type(
                    decl.underlying_type, is_param=False
                )
                if alias_name in defined_record_names:
                    continue
                if alias_name != target_str:
                    td_lines = [f"{alias_name} = {target_str}"]
                    doc = getattr(decl, "doc", None)
                    if isinstance(doc, str) and doc.strip():
                        td_lines.extend(self._format_docstring(doc, indent=""))
                    body_blocks.append("\n".join(td_lines))
            elif isinstance(decl, Variable):
                v_name = _sanitize_identifier(decl.name)
                v_type = self._resolve_override(getattr(decl, "type_overrides", None)) or self._format_type(
                    decl.type, is_param=False
                )
                if self.mode == "cffi":
                    var_lines = [f"    {v_name}: {v_type}"]
                    doc = getattr(decl, "doc", None)
                    if isinstance(doc, str) and doc.strip():
                        var_lines.extend(self._format_docstring(doc, indent="    "))
                    lib_member_lines.extend(var_lines)
                else:
                    var_lines = [f"{v_name}: {v_type}"]
                    doc = getattr(decl, "doc", None)
                    if isinstance(doc, str) and doc.strip():
                        var_lines.extend(self._format_docstring(doc, indent=""))
                    body_blocks.append("\n".join(var_lines))
            elif isinstance(decl, Function):
                if self.mode == "cffi":
                    emitted_lib_funcs.add(_sanitize_identifier(decl.name))
                    lib_member_lines.extend(self._format_function(decl, indent="    ", include_self=True))
                else:
                    fn_lines = self._format_function(decl)
                    body_blocks.append("\n".join(fn_lines))

        if self.mode == "cffi":
            all_func_decls = {decl.name: decl for decl in unit.declarations if isinstance(decl, Function)}
            for ext_entry in self.extern_python:
                stripped = ext_entry.strip()
                if (
                    not stripped
                    or "(" in stripped
                    or stripped.startswith('extern "Python"')
                    or not stripped.isidentifier()
                ):
                    raise ValueError(
                        f"extern_python entry {ext_entry!r} must be a symbol name of a Function or "
                        "function-pointer Typedef in Header.declarations, not a raw C prototype string"
                    )
                py_cb_name = _sanitize_identifier(stripped)
                if stripped in all_func_decls:
                    if py_cb_name not in emitted_lib_funcs:
                        emitted_lib_funcs.add(py_cb_name)
                        lib_member_lines.extend(
                            self._format_function(all_func_decls[stripped], indent="    ", include_self=True)
                        )
                    continue
                matched_fp = self._resolve_typedef_function_pointer(stripped)
                if matched_fp is not None:
                    if py_cb_name not in emitted_lib_funcs:
                        emitted_lib_funcs.add(py_cb_name)
                        synth_fn = Function(
                            stripped,
                            matched_fp.return_type,
                            list(matched_fp.parameters),
                            is_variadic=matched_fp.is_variadic,
                        )
                        lib_member_lines.extend(self._format_function(synth_fn, indent="    ", include_self=True))
                    continue
                raise ValueError(
                    f"extern_python symbol {stripped!r} not found as a Function or "
                    "function-pointer Typedef in Header.declarations"
                )

            body_blocks.append("ffi: cffi.FFI")
            lib_cls_lines = ["@final", "class _Lib:"]
            if lib_member_lines:
                lib_cls_lines.extend(lib_member_lines)
            else:
                lib_cls_lines.append("    ...")
            body_blocks.append("\n".join(lib_cls_lines))
            body_blocks.append("lib: _Lib")

        if self.module_var:
            body_blocks.append(f"{_sanitize_identifier(self.module_var)}: str")

        header_lines: list[str] = ["# Auto-generated by headerkit - do not edit"]
        std_imports: list[str] = []
        if self._needs_cffi:
            std_imports.append("import cffi")
        if self._needs_ctypes:
            std_imports.append("import ctypes")
        if self._needs_enum:
            std_imports.append("import enum")

        from_imports: list[str] = []
        if self._needs_callable:
            from_imports.append("from collections.abc import Callable")
        typing_names: list[str] = []
        if self._needs_any:
            typing_names.append("Any")
        if self._needs_final:
            typing_names.append("final")
        if typing_names:
            from_imports.append(f"from typing import {', '.join(typing_names)}")

        import_lines = std_imports + from_imports

        sections: list[str] = ["\n".join(header_lines)]
        if import_lines:
            sections.append("\n".join(import_lines))
        if body_blocks:
            sections.append("\n\n".join(body_blocks))

        return "\n\n".join(sections) + "\n"


@hook("write_code", writer="pyi", priority=Priority.STANDARD)
def _pyi_write_hook(
    unit: SourceUnit,
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> str | None:
    _ = context
    writer = PyiWriter(**kwargs)
    return writer.write(unit)


@hook("get_writer", writer="pyi", priority=Priority.STANDARD)
def _pyi_get_writer_hook(
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> WriterBackend:
    _ = context
    return PyiWriter(**kwargs)


register_writer(
    "pyi",
    PyiWriter,
    description=PyiWriter.format_description,
)
