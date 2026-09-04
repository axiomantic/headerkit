"""Generate C-ABI shim wrappers (extern "C") around C++ libraries from headerkit IR.

This module converts C++ structs, classes, member methods, constructors,
destructors, and free functions into a C-compatible API with ``extern "C"``
guards and opaque handles (``typedef struct Foo Foo;``).
"""

from __future__ import annotations

import re
import textwrap

from headerkit.ir import (
    Array,
    CType,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    SourceUnit,
    Struct,
    TypeExpr,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.base import BaseWriter


def _sanitize_name(name: str) -> str:
    """Convert C++ symbols (e.g. namespaces, operators, templates) into safe C identifiers."""
    name = name.replace("::", "_")
    # Compound & multi-char operators first
    name = name.replace("operator[]", "get_item")
    name = name.replace("operator()", "call")
    name = name.replace("operator->*", "arrow_star")
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
    name = name.replace("operator<=>", "spaceship")
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
    # Templates, whitespace, pointer/reference markers in type names
    name = re.sub(r"[<>, \t]+", "_", name)
    name = name.replace("&", "")
    name = name.replace("*", "Ptr")
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
        param_strs = [_type_to_c(p.type) for p in t.parameters]
        if getattr(t, "is_variadic", False):
            param_strs.append("...")
        params = ", ".join(param_strs) if param_strs else "void"
        return f"{_type_to_c(t.return_type)} (*)({params})"
    return str(t)


def _format_param(p: Parameter, param_name: str | None = None) -> str:
    """Format a single C parameter."""
    name = param_name or p.name or "arg"
    if isinstance(p.type, Array):
        size_str = str(p.type.size) if p.type.size is not None else ""
        return f"{_type_to_c(p.type.element_type)} {name}[{size_str}]"
    elif isinstance(p.type, FunctionPointer):
        params_list = [_format_param(param) for param in p.type.parameters]
        if getattr(p.type, "is_variadic", False):
            params_list.append("...")
        params = ", ".join(params_list) if params_list else "void"
        return f"{_type_to_c(p.type.return_type)} (*{name})({params})"
    type_str = _type_to_c(p.type)
    return f"{type_str} {name}"


class CShimWriter(BaseWriter):
    """Writer that generates C-ABI wrapper shims around C++ headers.

    Emits two parts (or combined header/source):
    - ``extern "C"`` C API declarations
    - C++ implementation wrapping member calls, constructors, and destructors.
    """

    name: str = "cshim"
    format_description: str = "C-ABI shim wrapper generator (extern C)"
    default_output_pattern: str = "{dir}/{stem}_cshim.cpp"
    default_extension: str = ".cpp"

    def __init__(self, *, catch_exceptions: bool = False) -> None:
        self.catch_exceptions = catch_exceptions

    def _render(self, unit: SourceUnit | Header) -> str:
        """Generate C-ABI shim source code from headerkit IR."""
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        lines: list[str] = [
            "// Auto-generated C-ABI shim by HeaderKit",
            "#pragma once",
            "",
            "#ifdef __cplusplus",
            'extern "C" {',
            "#endif",
            "",
        ]

        def _make_catch_stmt(ret_type: str) -> str:
            if ret_type == "void":
                return "return;"
            elif ret_type.endswith("*"):
                return "return nullptr;"
            return f"return static_cast<{ret_type}>(0);"

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
                    params_call = [p.name or f"arg{i}" for i, p in enumerate(ctor.parameters)]
                    params_c = [_format_param(p, params_call[i]) for i, p in enumerate(ctor.parameters)]

                    # Header prototype
                    proto = f"{safe_cls_name}_t* {fn_name}({', '.join(params_c) if params_c else 'void'});"
                    lines.append(proto)

                    # C++ wrapper
                    new_call = f"return reinterpret_cast<{safe_cls_name}_t*>(new (std::nothrow) {cls_full_name}({', '.join(params_call)}));"
                    if self.catch_exceptions:
                        body = textwrap.dedent(f"""\
                            {safe_cls_name}_t* {fn_name}({", ".join(params_c) if params_c else "void"}) {{
                                try {{
                                    {new_call}
                                }} catch (...) {{
                                    return nullptr;
                                }}
                            }}
                        """)
                    else:
                        body = textwrap.dedent(f"""\
                            {safe_cls_name}_t* {fn_name}({", ".join(params_c) if params_c else "void"}) {{
                                {new_call}
                            }}
                        """)
                    cpp_lines.append(body)

                # Destructor
                fn_dtor = f"{safe_cls_name}_destroy"
                lines.append(f"void {fn_dtor}({safe_cls_name}_t* self);")
                if self.catch_exceptions:
                    dtor_body = textwrap.dedent(f"""\
                        void {fn_dtor}({safe_cls_name}_t* self) {{
                            if (self) {{
                                try {{
                                    delete reinterpret_cast<{cls_full_name}*>(self);
                                }} catch (...) {{
                                }}
                            }}
                        }}
                    """)
                else:
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
                    if method.access in ("private", "protected"):
                        continue
                    m_safe_name = _sanitize_name(method.name)
                    fn_method_name = f"{safe_cls_name}_{m_safe_name}"
                    if fn_method_name == fn_dtor:
                        fn_method_name = f"{safe_cls_name}_method_{m_safe_name}"

                    ret_type_c = _type_to_c(method.return_type)
                    params_c = []
                    if not method.is_static:
                        params_c.append(f"{safe_cls_name}_t* self")
                    call_args = [p.name or f"arg{i}" for i, p in enumerate(method.parameters)]
                    for i, p in enumerate(method.parameters):
                        params_c.append(_format_param(p, call_args[i]))
                    if method.is_variadic:
                        params_c.append("...")

                    proto = f"{ret_type_c} {fn_method_name}({', '.join(params_c) if params_c else 'void'});"
                    lines.append(proto)

                    # Implement method
                    if method.is_static:
                        call_expr = f"{cls_full_name}::{method.name}({', '.join(call_args)})"
                    else:
                        call_expr = f"reinterpret_cast<{cls_full_name}*>(self)->{method.name}({', '.join(call_args)})"

                    if ret_type_c == "void":
                        raw_stmt = f"{call_expr};"
                    else:
                        raw_stmt = f"return {call_expr};"

                    if self.catch_exceptions:
                        catch_stmt = _make_catch_stmt(ret_type_c)
                        m_body = textwrap.dedent(f"""\
                            {ret_type_c} {fn_method_name}({", ".join(params_c) if params_c else "void"}) {{
                                try {{
                                    {raw_stmt}
                                }} catch (...) {{
                                    {catch_stmt}
                                }}
                            }}
                        """)
                    else:
                        m_body = textwrap.dedent(f"""\
                            {ret_type_c} {fn_method_name}({", ".join(params_c) if params_c else "void"}) {{
                                {raw_stmt}
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
                    safe_fn_name = _sanitize_name(decl.name)

                ret_c = _type_to_c(decl.return_type)
                call_args = [p.name or f"arg{i}" for i, p in enumerate(decl.parameters)]
                params_c = [_format_param(p, call_args[i]) for i, p in enumerate(decl.parameters)]
                if decl.is_variadic:
                    params_c.append("...")

                proto = f"{ret_c} {safe_fn_name}({', '.join(params_c) if params_c else 'void'});"
                lines.append(proto)

                if ret_c == "void":
                    raw_fn_stmt = f"{fn_full_name}({', '.join(call_args)});"
                else:
                    raw_fn_stmt = f"return {fn_full_name}({', '.join(call_args)});"

                if self.catch_exceptions:
                    catch_stmt = _make_catch_stmt(ret_c)
                    fn_body = textwrap.dedent(f"""\
                        {ret_c} {safe_fn_name}({", ".join(params_c) if params_c else "void"}) {{
                            try {{
                                {raw_fn_stmt}
                            }} catch (...) {{
                                {catch_stmt}
                            }}
                        }}
                    """)
                else:
                    fn_body = textwrap.dedent(f"""\
                        {ret_c} {safe_fn_name}({", ".join(params_c) if params_c else "void"}) {{
                            {raw_fn_stmt}
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
        if self.catch_exceptions:
            lines.append("#include <exception>")
        if header.path:
            lines.append(f'#include "{header.path}"')
        lines.append("")
        lines.extend(cpp_lines)
        lines.append("#endif")
        lines.append("")

        return "\n".join(lines)

    def write(self, header: Header) -> str:
        """Convert header IR to C-ABI shim source code."""
        return self._render(header)

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        cpp_code = self._render(unit)

        cmake = textwrap.dedent(f"""\
            cmake_minimum_required(VERSION 3.15)
            project({pkg}_cshim C CXX)

            set(CMAKE_CXX_STANDARD 17)
            set(CMAKE_CXX_STANDARD_REQUIRED ON)

            add_library({pkg}_cshim SHARED
                src/{pkg}_cshim.cpp
            )

            target_include_directories({pkg}_cshim PUBLIC
                include
            )

            enable_testing()
            add_executable(test_{pkg}_cshim tests/test_cshim.c)
            target_link_libraries(test_{pkg}_cshim PRIVATE {pkg}_cshim)
            add_test(NAME test_{pkg}_cshim COMMAND test_{pkg}_cshim)
        """)

        header_content = textwrap.dedent(f"""\
            // C-ABI Header for {pkg}
            #pragma once

            #ifdef __cplusplus
            extern "C" {{
            #endif

            // See src/{pkg}_cshim.cpp for implementations

            #ifdef __cplusplus
            }}
            #endif
        """)

        test_c = textwrap.dedent(f"""\
            #include <stdio.h>
            #include <assert.h>

            int main(void) {{
                printf("Testing {pkg} C-ABI shim...\\n");
                return 0;
            }}
        """)

        files = [
            OutputFile(path="CMakeLists.txt", content=cmake),
            OutputFile(path=f"include/{pkg}_cshim.h", content=header_content),
            OutputFile(path=f"src/{pkg}_cshim.cpp", content=cpp_code),
            OutputFile(path="tests/test_cshim.c", content=test_c),
        ]
        return ProjectLayout(files=files)


def write_cshim(header: Header, *, catch_exceptions: bool = False) -> str:
    """Convenience function to generate C-ABI shims from a Header IR."""
    return CShimWriter(catch_exceptions=catch_exceptions).write(header)


# Self-register
from headerkit.writers import register_writer  # noqa: E402

register_writer("cshim", CShimWriter, description="C-ABI shim wrapper generator (extern C)")
