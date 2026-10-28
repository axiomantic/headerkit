"""PEP 484 Python type stub (.pyi) writer for HeaderKit IR."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from keyword import iskeyword
from typing import Any, ClassVar

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
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers import WriterBackend, register_writer
from headerkit.writers.base import BaseWriter, WriterOption

_BOOL_TYPES: frozenset[str] = frozenset({"bool", "_Bool", "NIM_BOOL"})

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
        "NI",
        "NU",
        "NI8",
        "NI16",
        "NI32",
        "NI64",
        "NU8",
        "NU16",
        "NU32",
        "NU64",
        "NIM_CHAR",
    }
)

_FLOAT_TYPES: frozenset[str] = frozenset(
    {
        "float",
        "double",
        "long double",
        "NF",
        "NF32",
        "NF64",
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
        cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", cleaned)
        if cleaned and cleaned[0].isdigit():
            cleaned = f"_{cleaned}"
    if not cleaned:
        return fallback
    if iskeyword(cleaned) or cleaned in ("None", "True", "False"):
        return f"{cleaned}_"
    return cleaned


def _strip_elaborated_prefix(name: str) -> str:
    """Strip leading C/C++ aggregate keywords from a type name."""
    text = name.strip()
    for prefix in ("struct ", "union ", "enum ", "class "):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _normalize_ctype_key(ctype: CType) -> str:
    """Build normalized C type lookup key combining signedness qualifiers and base name."""
    signed_quals = [q for q in ctype.qualifiers if q in ("signed", "unsigned")]
    base = _strip_elaborated_prefix(ctype.name)
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
    cache_version: str = "1"
    writer_options: ClassVar[list[WriterOption]] = [
        WriterOption(
            name="exclude_patterns",
            description="Regex patterns for declaration names to exclude",
            default=None,
            type=list,
        ),
    ]

    def __init__(self, exclude_patterns: Sequence[str] | None = None) -> None:
        self.exclude_patterns: list[str] = list(exclude_patterns) if exclude_patterns else []
        self._compiled_excludes = [re.compile(p) for p in self.exclude_patterns]
        self._needs_enum = False
        self._needs_callable = False
        self._needs_any = False

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

    def _format_type(self, type_expr: TypeExpr, *, is_param: bool = False) -> str:
        if isinstance(type_expr, CType):
            key = _normalize_ctype_key(type_expr)
            if not type_expr.is_elaborated:
                if key == "void":
                    return "None"
                if key in _BOOL_TYPES:
                    return "bool"
                if key in _INT_TYPES:
                    return "int"
                if key in _FLOAT_TYPES:
                    return "float"
            res = _sanitize_identifier(key, fallback="Any")
            if res == "Any":
                self._needs_any = True
            return res

        if isinstance(type_expr, Pointer):
            pointee = type_expr.pointee
            if isinstance(pointee, FunctionPointer):
                return self._format_function_pointer(pointee)
            if isinstance(pointee, CType):
                p_key = _normalize_ctype_key(pointee)
                if not pointee.is_elaborated:
                    if p_key == "char":
                        return "bytes | str" if is_param else "bytes"
                    if p_key == "void":
                        self._needs_any = True
                        return "Any"
                if pointee.is_elaborated or (
                    p_key not in _BOOL_TYPES and p_key not in _INT_TYPES and p_key not in _FLOAT_TYPES
                ):
                    res = _sanitize_identifier(p_key, fallback="Any")
                    if res == "Any":
                        self._needs_any = True
                    return res
            self._needs_any = True
            return "Any"

        if isinstance(type_expr, Reference):
            return self._format_type(type_expr.target, is_param=is_param)

        if isinstance(type_expr, Array):
            if (
                isinstance(type_expr.element_type, CType)
                and not type_expr.element_type.is_elaborated
                and _normalize_ctype_key(type_expr.element_type) == "char"
            ):
                return "bytes"
            elem = self._format_type(type_expr.element_type, is_param=False)
            return f"list[{elem}]"

        if isinstance(type_expr, FunctionPointer):
            return self._format_function_pointer(type_expr)

        self._needs_any = True
        return "Any"

    def _format_function_pointer(self, fp: FunctionPointer) -> str:
        self._needs_callable = True
        ret = self._format_type(fp.return_type, is_param=False)
        if fp.is_variadic:
            return f"Callable[..., {ret}]"
        arg_types = [
            self._format_type(p.type, is_param=False)
            for p in fp.parameters
            if not (
                isinstance(p.type, CType)
                and not p.type.is_elaborated
                and _normalize_ctype_key(p.type) == "void"
                and not p.name
            )
        ]
        return f"Callable[[{', '.join(arg_types)}], {ret}]"

    def _format_constant(self, const: Constant) -> list[str]:
        name = _sanitize_identifier(const.name)
        if const.value is not None:
            if isinstance(const.value, bool):
                line = f"{name}: bool = {const.value}"
            elif isinstance(const.value, int):
                line = f"{name}: int = {const.value}"
            elif isinstance(const.value, float):
                line = f"{name}: float = {const.value}"
            else:
                val_str = str(const.value).strip()
                if (val_str.startswith('"') and val_str.endswith('"')) or (
                    val_str.startswith("'") and val_str.endswith("'")
                ):
                    val_out = val_str if self._is_valid_python_expr(val_str) else "..."
                    line = f"{name}: str = {val_out}"
                else:
                    val_out = val_str if self._is_valid_python_expr(val_str) else "..."
                    line = f"{name}: int = {val_out}"
        elif const.type is not None:
            t_str = self._format_type(const.type, is_param=False)
            line = f"{name}: {t_str}"
        else:
            self._needs_any = True
            line = f"{name}: Any"
        lines = [line]
        doc = getattr(const, "doc", None)
        if isinstance(doc, str) and doc.strip():
            lines.extend(self._format_docstring(doc, indent=""))
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
                    lines.append(f"    {v_name}: int = {val_repr}")
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
                lines.append(f"{v_name}: int = {val_repr}")
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
            f_type = self._format_type(fld.type, is_param=False)
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
            p_type = self._format_type(param.type, is_param=True)
            if param.default_value is not None:
                param_parts.append(f"{p_name}: {p_type} = ...")
            else:
                param_parts.append(f"{p_name}: {p_type}")

        if func.is_variadic:
            self._needs_any = True
            param_parts.append("*args: Any")

        ret_str = self._format_type(func.return_type, is_param=False)
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

    def _format_function(self, func: Function) -> list[str]:
        fn_name = _sanitize_identifier(func.name)
        sig = self._format_function_params_and_return(func, include_self=False)
        doc = getattr(func, "doc", None)
        if isinstance(doc, str) and doc.strip():
            lines = [f"def {fn_name}{sig}:"]
            lines.extend(self._format_docstring(doc, indent="    "))
            lines.append("    ...")
            return lines
        return [f"def {fn_name}{sig}: ..."]

    def _render(self, unit: SourceUnit | Header) -> str:
        """Convert Header IR into PEP 484 ``.pyi`` stub declarations."""
        self._needs_enum = False
        self._needs_callable = False
        self._needs_any = False

        defined_record_names: set[str] = set()
        for decl in unit.declarations:
            if isinstance(decl, (Struct, Enum)) and decl.name and not self._is_excluded(decl.name):
                defined_record_names.add(_sanitize_identifier(decl.name))

        body_blocks: list[str] = []
        for decl in unit.declarations:
            decl_name = getattr(decl, "name", None)
            if self._is_excluded(decl_name):
                continue

            if isinstance(decl, Constant):
                const_lines = self._format_constant(decl)
                body_blocks.append("\n".join(const_lines))
            elif isinstance(decl, Enum):
                enum_lines = self._format_enum(decl)
                if enum_lines:
                    body_blocks.append("\n".join(enum_lines))
            elif isinstance(decl, Struct):
                struct_lines = self._format_struct(decl)
                if struct_lines:
                    body_blocks.append("\n".join(struct_lines))
            elif isinstance(decl, Typedef):
                alias_name = _sanitize_identifier(decl.name)
                target_str = self._format_type(decl.underlying_type, is_param=False)
                if alias_name == target_str and alias_name in defined_record_names:
                    continue
                if alias_name != target_str:
                    td_lines = [f"{alias_name} = {target_str}"]
                    doc = getattr(decl, "doc", None)
                    if isinstance(doc, str) and doc.strip():
                        td_lines.extend(self._format_docstring(doc, indent=""))
                    body_blocks.append("\n".join(td_lines))
            elif isinstance(decl, Variable):
                v_name = _sanitize_identifier(decl.name)
                v_type = self._format_type(decl.type, is_param=False)
                var_lines = [f"{v_name}: {v_type}"]
                doc = getattr(decl, "doc", None)
                if isinstance(doc, str) and doc.strip():
                    var_lines.extend(self._format_docstring(doc, indent=""))
                body_blocks.append("\n".join(var_lines))
            elif isinstance(decl, Function):
                fn_lines = self._format_function(decl)
                body_blocks.append("\n".join(fn_lines))

        header_lines: list[str] = ["# Auto-generated by headerkit - do not edit"]
        import_lines: list[str] = []
        if self._needs_callable:
            import_lines.append("from collections.abc import Callable")
        if self._needs_enum:
            import_lines.append("import enum")
        if self._needs_any:
            import_lines.append("from typing import Any")

        sections: list[str] = ["\n".join(header_lines)]
        if import_lines:
            sections.append("\n".join(import_lines))
        if body_blocks:
            sections.append("\n\n".join(body_blocks))

        return "\n\n".join(sections) + "\n"

    def _write_custom_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Generate a PEP 484 type stub file layout."""
        stub_code = self._render(unit)
        pkg = options.package_name.replace("-", "_")
        files = [
            OutputFile(
                path=f"src/{pkg}/{pkg}.pyi",
                content=stub_code,
            ),
        ]
        return ProjectLayout(files=files)


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
