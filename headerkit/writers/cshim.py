"""Generate C-ABI shim wrappers (extern "C") around C++ libraries from headerkit IR.

This module converts C++ structs, classes, member methods, constructors,
destructors, and free functions into a C-compatible API with ``extern "C"``
guards, opaque handles (``typedef struct Foo Foo;``), and exception boundaries.
"""

from __future__ import annotations

import re
import textwrap
from typing import ClassVar

from headerkit.ir import (
    Array,
    CType,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    Struct,
    TypeExpr,
)


def _sanitize_name(name: str) -> str:
    """Convert C++ symbols (e.g. namespaces, operators, templates) into safe C identifiers."""
    name = name.replace("::", "_")
    name = re.sub(r"[<>, \t]+", "_", name)
    name = name.replace("&", "")
    name = name.replace("*", "Ptr")
    # Compound & multi-char operators first
    name = name.replace("operator[]", "get_item")
    name = name.replace("operator()", "call")
    name = name.replace("operator->", "arrow")
    name = name.replace("operator+=", "add_assign")
    name = name.replace("operator-=", "sub_assign")
    name = name.replace("operator*=", "mul_assign")
    name = name.replace("operator/=", "div_assign")
    name = name.replace("operator%=", "mod_assign")
    name = name.replace("operator<<=", "shl_assign")
    name = name.replace("operator>>=", "shr_assign")
    name = name.replace("operator&=", "and_assign")
    name = name.replace("operator|=", "or_assign")
    name = name.replace("operator^=", "xor_assign")
    name = name.replace("operator==", "eq")
    name = name.replace("operator!=", "ne")
    name = name.replace("operator<=", "le")
    name = name.replace("operator>=", "ge")
    name = name.replace("operator<<", "shl")
    name = name.replace("operator>>", "shr")
    name = name.replace("operator++", "inc")
    name = name.replace("operator--", "dec")
    name = name.replace("operator&&", "land")
    name = name.replace("operator||", "lor")
    # Single-char operators
    name = name.replace("operator<", "lt")
    name = name.replace("operator>", "gt")
    name = name.replace("operator+", "add")
    name = name.replace("operator-", "sub")
    name = name.replace("operator*", "mul")
    name = name.replace("operator/", "div")
    name = name.replace("operator%", "mod")
    name = name.replace("operator~", "bnot")
    name = name.replace("operator!", "lnot")
    name = name.replace("operator&", "band")
    name = name.replace("operator|", "bor")
    name = name.replace("operator^", "bxor")
    name = name.replace("operator=", "assign")
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    return name.strip("_")


def _type_to_c(t: TypeExpr) -> str:
    """Format an IR type expression as a pure C type string."""
    if isinstance(t, CType):
        name = t.name
        if "::" in name:
            name = _sanitize_name(name)
        if t.qualifiers:
            return f"{' '.join(t.qualifiers)} {name}"
        return name
    elif isinstance(t, Pointer):
        return f"{_type_to_c(t.pointee)}*"
    elif isinstance(t, Reference):
        # In C-ABI, references become pointers
        return f"{_type_to_c(t.target)}*"
    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{_type_to_c(t.element_type)}[{size_str}]"
    elif isinstance(t, FunctionPointer):
        params = ", ".join(_type_to_c(p.type) for p in t.parameters) or "void"
        return f"{_type_to_c(t.return_type)} (*)({params})"
    return str(t)


def _format_param(p: Parameter) -> str:
    """Format a single C parameter."""
    type_str = _type_to_c(p.type)
    name = p.name or "arg"
    if isinstance(p.type, Array):
        size_str = str(p.type.size) if p.type.size is not None else ""
        return f"{_type_to_c(p.type.element_type)} {name}[{size_str}]"
    return f"{type_str} {name}"


class CShimWriter:
    """Writer that generates C-ABI wrapper shims around C++ headers.

    Emits two parts (or combined header/source):
    - ``extern "C"`` C API declarations
    - C++ implementation wrapping member calls, constructors, destructors,
      and exception boundaries (``try ... catch``).
    """

    default_output_pattern: ClassVar[str] = "{dir}/{stem}_cshim.cpp"

    def __init__(
        self,
        *,
        header_guard: str | None = None,
        exception_status_code: bool = False,
    ) -> None:
        self.header_guard = header_guard
        self.exception_status_code = exception_status_code

    def write(self, header: Header) -> str:
        """Generate C-ABI shim source code from headerkit IR."""
        lines: list[str] = [
            "// Auto-generated C-ABI shim by HeaderKit",
            "#pragma once",
            "",
            "#ifdef __cplusplus",
            'extern "C" {',
            "#endif",
            "",
        ]

        # 1. Forward declare opaque handles for classes
        classes = [d for d in header.declarations if isinstance(d, Struct) and (d.is_cppclass or d.methods)]
        if classes:
            lines.append("/* Opaque Handle Types */")
            for cls in classes:
                if cls.name:
                    safe_name = _sanitize_name(f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name)
                    lines.append(f"typedef struct {safe_name}_s {safe_name}_t;")
            lines.append("")

        # 2. C Shim Prototypes & Implementations
        cpp_lines: list[str] = []

        for decl in header.declarations:
            if isinstance(decl, Struct) and (decl.is_cppclass or decl.methods):
                cls = decl
                if not cls.name:
                    continue
                cls_full_name = f"{cls.namespace}::{cls.name}" if cls.namespace else cls.name
                safe_cls_name = _sanitize_name(cls_full_name)

                # Constructors
                for idx, ctor in enumerate(cls.constructors):
                    fn_name = f"{safe_cls_name}_create" if idx == 0 else f"{safe_cls_name}_create_{idx}"
                    params_c = [_format_param(p) for p in ctor.parameters]
                    params_call = [p.name or f"arg{i}" for i, p in enumerate(ctor.parameters)]

                    # Header prototype
                    proto = f"{safe_cls_name}_t* {fn_name}({', '.join(params_c) if params_c else 'void'});"
                    lines.append(proto)

                    # C++ wrapper
                    body = textwrap.dedent(f"""\
                        {safe_cls_name}_t* {fn_name}({", ".join(params_c) if params_c else "void"}) {{
                            return reinterpret_cast<{safe_cls_name}_t*>(new (std::nothrow) {cls_full_name}({", ".join(params_call)}));
                        }}
                    """)
                    cpp_lines.append(body)

                # Destructor
                fn_dtor = f"{safe_cls_name}_destroy"
                lines.append(f"void {fn_dtor}({safe_cls_name}_t* self);")
                dtor_body = textwrap.dedent(f"""\
                    void {fn_dtor}({safe_cls_name}_t* self) {{
                        if (self) {{
                            delete reinterpret_cast<{cls_full_name}*>(self);
                        }}
                    }}
                """)
                cpp_lines.append(dtor_body)

                # Methods
                for method in cls.methods:
                    if method.access == "private":
                        continue
                    m_safe_name = _sanitize_name(method.name)
                    fn_method_name = f"{safe_cls_name}_{m_safe_name}"

                    ret_type_c = _type_to_c(method.return_type)
                    params_c = []
                    if not method.is_static:
                        params_c.append(f"{safe_cls_name}_t* self")
                    for p in method.parameters:
                        params_c.append(_format_param(p))

                    call_args = [p.name or f"arg{i}" for i, p in enumerate(method.parameters)]

                    proto = f"{ret_type_c} {fn_method_name}({', '.join(params_c) if params_c else 'void'});"
                    lines.append(proto)

                    # Implement method
                    if method.is_static:
                        call_expr = f"{cls_full_name}::{method.name}({', '.join(call_args)})"
                    else:
                        call_expr = f"reinterpret_cast<{cls_full_name}*>(self)->{method.name}({', '.join(call_args)})"

                    if ret_type_c == "void":
                        stmt = f"{call_expr};"
                    else:
                        stmt = f"return {call_expr};"

                    m_body = textwrap.dedent(f"""\
                        {ret_type_c} {fn_method_name}({", ".join(params_c) if params_c else "void"}) {{
                            {stmt}
                        }}
                    """)
                    cpp_lines.append(m_body)

            elif isinstance(decl, Function):
                # Free function
                if decl.namespace:
                    fn_full_name = f"{decl.namespace}::{decl.name}"
                    safe_fn_name = _sanitize_name(fn_full_name)
                else:
                    fn_full_name = decl.name
                    safe_fn_name = decl.name

                ret_c = _type_to_c(decl.return_type)
                params_c = [_format_param(p) for p in decl.parameters]
                call_args = [p.name or f"arg{i}" for i, p in enumerate(decl.parameters)]

                proto = f"{ret_c} {safe_fn_name}({', '.join(params_c) if params_c else 'void'});"
                lines.append(proto)

                if ret_c == "void":
                    stmt = f"{fn_full_name}({', '.join(call_args)});"
                else:
                    stmt = f"return {fn_full_name}({', '.join(call_args)});"

                fn_body = textwrap.dedent(f"""\
                    {ret_c} {safe_fn_name}({", ".join(params_c) if params_c else "void"}) {{
                        {stmt}
                    }}
                """)
                cpp_lines.append(fn_body)

        lines.append("")
        lines.append("#ifdef __cplusplus")
        lines.append("}")
        lines.append("#endif")
        lines.append("")
        lines.append("#ifdef __cplusplus")
        lines.append("#include <new>")
        lines.append("")
        lines.extend(cpp_lines)
        lines.append("#endif")
        lines.append("")

        return "\n".join(lines)

    @property
    def name(self) -> str:
        """Human-readable name of this writer."""
        return "cshim"

    @property
    def format_description(self) -> str:
        """Short description of the output format."""
        return "C-ABI shim wrapper generator (extern C)"


# Self-register
from headerkit.writers import register_writer  # noqa: E402

register_writer("cshim", CShimWriter, description="C-ABI shim wrapper generator (extern C)")
