"""Polyglot interface extraction for Rust, Zig, and Nim source files."""

from __future__ import annotations

import contextlib
import re
from typing import Any

from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Parameter,
    Pointer,
    SourceLocation,
    SourceUnit,
    Struct,
    TypeExpr,
)

# ---------------------------------------------------------------------------
# Rust type mapping & extraction
# ---------------------------------------------------------------------------

_RUST_TYPE_MAP: dict[str, str] = {
    "c_int": "int32_t",
    "i32": "int32_t",
    "c_uint": "uint32_t",
    "u32": "uint32_t",
    "c_long": "int64_t",
    "i64": "int64_t",
    "isize": "int64_t",
    "c_ulong": "uint64_t",
    "u64": "uint64_t",
    "usize": "uint64_t",
    "c_short": "int16_t",
    "i16": "int16_t",
    "c_ushort": "uint16_t",
    "u16": "uint16_t",
    "c_char": "int8_t",
    "i8": "int8_t",
    "c_uchar": "uint8_t",
    "u8": "uint8_t",
    "c_float": "float",
    "f32": "float",
    "c_double": "double",
    "f64": "double",
    "c_void": "void",
    "bool": "bool",
    "()": "void",
}


def _map_rust_type(raw_type: str) -> TypeExpr:
    t = raw_type.strip()
    if t.startswith("*const "):
        inner = _map_rust_type(t[7:])
        if isinstance(inner, CType):
            return Pointer(CType(inner.name, qualifiers=list(set(inner.qualifiers + ["const"]))))
        return Pointer(inner)
    if t.startswith("*mut "):
        return Pointer(_map_rust_type(t[5:]))
    if t.startswith("&const "):
        inner = _map_rust_type(t[7:])
        if isinstance(inner, CType):
            return Pointer(CType(inner.name, qualifiers=list(set(inner.qualifiers + ["const"]))))
        return Pointer(inner)
    if t.startswith("&mut "):
        return Pointer(_map_rust_type(t[5:]))
    if t.startswith("&"):
        inner = _map_rust_type(t[1:])
        if isinstance(inner, CType):
            return Pointer(CType(inner.name, qualifiers=list(set(inner.qualifiers + ["const"]))))
        return Pointer(inner)

    if "fn(" in t or "fn (" in t:
        return FunctionPointer(return_type=CType("void"), parameters=[])

    c_name = _RUST_TYPE_MAP.get(t, t)
    return CType(c_name)


def _find_matching_paren(text: str, open_idx: int) -> int:
    """Find the index of the matching closing parenthesis for text[open_idx]."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_params(params_str: str) -> list[str]:
    """Split comma-separated parameters respecting nested parentheses and brackets."""
    params: list[str] = []
    current: list[str] = []
    depth = 0
    for char in params_str:
        if char in "([{<":
            depth += 1
            current.append(char)
        elif char in ")]}>":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            p = "".join(current).strip()
            if p:
                params.append(p)
            current = []
        else:
            current.append(char)
    last = "".join(current).strip()
    if last:
        params.append(last)
    return params


def extract_rust_interface(code: str, filename: str) -> SourceUnit:
    """Extract exported C interface declarations from Rust source."""
    declarations: list[Declaration] = []

    # Match repr(C) structs: #[repr(C)] pub struct Name { ... }
    struct_pat = re.compile(
        r"(?:#\[[^\n\]]*\]\s*)*#\[repr\(C\)[^\]]*\]\s*(?:#\[[^\n\]]*\]\s*)*pub\s+struct\s+([A-Za-z0-9_]+)\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    for m in struct_pat.finditer(code):
        name = m.group(1)
        body = m.group(2)
        fields: list[Field] = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("//"):
                continue
            if line.startswith("pub "):
                line = line[4:].strip()
            parts = line.split(":", 1)
            if len(parts) == 2:
                f_name = parts[0].strip()
                f_type = _map_rust_type(parts[1].strip())
                fields.append(Field(name=f_name, type=f_type))

        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(Struct(name=name, fields=fields, location=loc))

    # Match repr(C) enums: #[repr(C)] pub enum Name { ... }
    enum_pat = re.compile(
        r"(?:#\[[^\n\]]*\]\s*)*#\[repr\([^)]*(?:C|i32|u32|i64|u64|c_int)[^)]*\)\]\s*(?:#\[[^\n\]]*\]\s*)*pub\s+enum\s+([A-Za-z0-9_]+)\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    for m in enum_pat.finditer(code):
        name = m.group(1)
        body = m.group(2)
        values: list[EnumValue] = []
        cur_val = 0
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("//") or line.startswith("/*"):
                continue
            if "=" in line:
                v_name, v_val_str = line.split("=", 1)
                v_name = v_name.strip()
                with contextlib.suppress(ValueError):
                    cur_val = int(v_val_str.strip(), 0)
            else:
                v_name = line.strip()
            values.append(EnumValue(name=v_name, value=cur_val))
            cur_val += 1
        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(Enum(name=name, values=values, location=loc))

    # Match pub extern "C" fn name(...) -> ret
    fn_pat = re.compile(
        r"(?:#\[[^\n\]]*\]\s*)*pub\s+(?:unsafe\s+)?extern\s+\"C\"\s+fn\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*(?:->\s*([^\{;]+))?",
        re.MULTILINE,
    )
    for m in fn_pat.finditer(code):
        name = m.group(1)
        params_raw = m.group(2).strip()
        ret_raw = (m.group(3) or "void").strip()

        parameters: list[Parameter] = []
        if params_raw:
            for p in _split_params(params_raw):
                p = p.strip()
                if not p:
                    continue
                parts = p.split(":", 1)
                if len(parts) == 2:
                    p_name = parts[0].strip()
                    p_type = _map_rust_type(parts[1].strip())
                    parameters.append(Parameter(name=p_name, type=p_type))

        ret_type = _map_rust_type(ret_raw)
        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(
            Function(
                name=name,
                return_type=ret_type,
                parameters=parameters,
                location=loc,
            )
        )

    return SourceUnit(path=filename, declarations=declarations, language="rust", classification="interface")


# ---------------------------------------------------------------------------
# Zig type mapping & extraction
# ---------------------------------------------------------------------------

_ZIG_TYPE_MAP: dict[str, str] = {
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "usize": "uint64_t",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "isize": "int64_t",
    "f32": "float",
    "f64": "double",
    "bool": "bool",
    "void": "void",
    "c_int": "int",
    "c_uint": "unsigned int",
    "c_char": "char",
}


def _map_zig_type(raw_type: str) -> TypeExpr:
    t = raw_type.strip()
    if "fn" in t and ("*const fn" in t or "fn (" in t or "fn(" in t):
        ret_type: TypeExpr = CType("void")
        if "callconv(" in t:
            after_callconv = t.split("callconv(", 1)[1]
            if ")" in after_callconv:
                ret_part = after_callconv.split(")", 1)[1].strip()
                if ret_part:
                    ret_type = _map_zig_type(ret_part)
        elif ")" in t:
            ret_part = t.rsplit(")", 1)[1].strip()
            if ret_part:
                ret_type = _map_zig_type(ret_part)
        return FunctionPointer(return_type=ret_type, parameters=[])

    if t.startswith("[*c]") or t.startswith("?[*c]"):
        inner = _map_zig_type(t[4:] if t.startswith("[*c]") else t[5:])
        return Pointer(inner)
    if t.startswith("[*:0]const "):
        return Pointer(CType("char", qualifiers=["const"]))
    if t.startswith("*const "):
        inner = _map_zig_type(t[7:])
        if isinstance(inner, CType):
            return Pointer(CType(inner.name, qualifiers=list(set(inner.qualifiers + ["const"]))))
        return Pointer(inner)
    if t.startswith("*") or t.startswith("?*"):
        prefix_len = 2 if t.startswith("?*") else 1
        return Pointer(_map_zig_type(t[prefix_len:]))

    c_name = _ZIG_TYPE_MAP.get(t, t)
    return CType(c_name)


def extract_zig_interface(code: str, filename: str) -> SourceUnit:
    """Extract exported C interface declarations from Zig source."""
    declarations: list[Declaration] = []

    # Match pub const Name = extern struct { ... };
    struct_pat = re.compile(
        r"(?:pub\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*extern\s+struct\s*\{([^}]*)\};",
        re.MULTILINE,
    )
    for m in struct_pat.finditer(code):
        name = m.group(1)
        body = m.group(2)
        fields: list[Field] = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("//"):
                continue
            parts = line.split(":", 1)
            if len(parts) == 2:
                f_name = parts[0].strip()
                f_type = _map_zig_type(parts[1].strip())
                fields.append(Field(name=f_name, type=f_type))

        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(Struct(name=name, fields=fields, location=loc))

    # Match pub const Name = extern enum { ... }; or enum(c_int) { ... };
    enum_pat = re.compile(
        r"(?:pub\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*(?:extern\s+)?enum(?:\([^)]*\))?\s*\{([^}]*)\};",
        re.MULTILINE,
    )
    for m in enum_pat.finditer(code):
        name = m.group(1)
        body = m.group(2)
        values: list[EnumValue] = []
        cur_val = 0
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("//"):
                continue
            if "=" in line:
                v_name, v_val_str = line.split("=", 1)
                v_name = v_name.strip()
                with contextlib.suppress(ValueError):
                    cur_val = int(v_val_str.strip(), 0)
            else:
                v_name = line.strip()
            values.append(EnumValue(name=v_name, value=cur_val))
            cur_val += 1
        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(Enum(name=name, values=values, location=loc))

    # Match export fn name(...) ret { ... } using matching parentheses
    fn_start_pat = re.compile(r"export\s+fn\s+([A-Za-z0-9_]+)\s*\(", re.MULTILINE)
    for m in fn_start_pat.finditer(code):
        name = m.group(1)
        open_paren_idx = m.end() - 1
        close_paren_idx = _find_matching_paren(code, open_paren_idx)
        if close_paren_idx == -1:
            continue
        params_raw = code[open_paren_idx + 1 : close_paren_idx].strip()

        # Extract return type from close_paren_idx to '{' or ';'
        rest = code[close_paren_idx + 1 :]
        ret_end = len(rest)
        for char in ("{", ";", "\n"):
            idx = rest.find(char)
            if idx != -1 and idx < ret_end:
                ret_end = idx
        ret_raw = rest[:ret_end].strip()

        parameters: list[Parameter] = []
        if params_raw:
            for p in _split_params(params_raw):
                p = p.strip()
                if not p:
                    continue
                parts = p.split(":", 1)
                if len(parts) == 2:
                    p_name = parts[0].strip()
                    p_type = _map_zig_type(parts[1].strip())
                    parameters.append(Parameter(name=p_name, type=p_type))

        ret_type = _map_zig_type(ret_raw) if ret_raw else CType("void")
        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(
            Function(
                name=name,
                return_type=ret_type,
                parameters=parameters,
                location=loc,
            )
        )

    return SourceUnit(path=filename, declarations=declarations, language="zig", classification="source")


# ---------------------------------------------------------------------------
# Nim type mapping & extraction
# ---------------------------------------------------------------------------

_NIM_TYPE_MAP: dict[str, str] = {
    "cint": "int32_t",
    "int32": "int32_t",
    "cuint": "uint32_t",
    "uint32": "uint32_t",
    "clong": "int64_t",
    "int64": "int64_t",
    "int": "int64_t",
    "culong": "uint64_t",
    "uint64": "uint64_t",
    "uint": "uint64_t",
    "cshort": "int16_t",
    "int16": "int16_t",
    "cushort": "uint16_t",
    "uint16": "uint16_t",
    "cschar": "int8_t",
    "int8": "int8_t",
    "cuchar": "uint8_t",
    "uint8": "uint8_t",
    "byte": "uint8_t",
    "cfloat": "float",
    "float32": "float",
    "cdouble": "double",
    "float64": "double",
    "float": "double",
    "bool": "bool",
    "void": "void",
    "pointer": "void",
    "cstring": "char",
}


def _map_nim_type(raw_type: str) -> TypeExpr:
    t = raw_type.strip()
    if t.startswith("ptr "):
        return Pointer(_map_nim_type(t[4:]))
    if t == "cstring":
        return Pointer(CType("char", qualifiers=["const"]))
    if t == "pointer":
        return Pointer(CType("void"))

    c_name = _NIM_TYPE_MAP.get(t, t)
    return CType(c_name)


def extract_nim_interface(code: str, filename: str) -> SourceUnit:
    """Extract exported C interface declarations from Nim source."""
    declarations: list[Declaration] = []

    # Match exported object types: Name* = object ...
    type_pat = re.compile(
        r"([A-Za-z0-9_]+)\*\s*=\s*(?:ref\s+)?object(?:\s+of\s+[A-Za-z0-9_]+)?\s*\n((?:[ \t]+[^\n]+\n?)+)",
        re.MULTILINE,
    )
    for m in type_pat.finditer(code):
        name = m.group(1)
        body = m.group(2)
        fields: list[Field] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 1)
            if len(parts) == 2:
                f_names_raw = parts[0].strip()
                f_type_raw = parts[1].strip()
                f_type = _map_nim_type(f_type_raw)
                for fn in f_names_raw.split(","):
                    clean_name = fn.strip().rstrip("*")
                    if clean_name:
                        fields.append(Field(name=clean_name, type=f_type))

        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(Struct(name=name, fields=fields, location=loc))

    # Match exported procs: proc name*(...): ret {.exportc...}
    fn_pat = re.compile(
        r"proc\s+([A-Za-z0-9_]+)\*\s*(?:\(([^)]*)\))?\s*(?::\s*([^{=\n]+))?\s*\{\.[^}]*exportc[^}]*\.\}",
        re.MULTILINE,
    )
    for m in fn_pat.finditer(code):
        name = m.group(1)
        params_raw = (m.group(2) or "").strip()
        ret_raw = (m.group(3) or "void").strip()

        parameters: list[Parameter] = []
        if params_raw:
            for p in params_raw.split(","):
                p = p.strip()
                if not p:
                    continue
                parts = p.split(":", 1)
                if len(parts) == 2:
                    p_name = parts[0].strip()
                    p_type = _map_nim_type(parts[1].strip())
                    parameters.append(Parameter(name=p_name, type=p_type))

        ret_type = _map_nim_type(ret_raw)
        loc = SourceLocation(file=filename, line=code[: m.start()].count("\n") + 1, column=1)
        declarations.append(
            Function(
                name=name,
                return_type=ret_type,
                parameters=parameters,
                location=loc,
            )
        )

    return SourceUnit(path=filename, declarations=declarations, language="nim", classification="source")


# ---------------------------------------------------------------------------
# Hook Registrations
# ---------------------------------------------------------------------------


@hook("parse_unit", language="rust", priority=Priority.STANDARD)
def _rust_parse_hook(
    code: str,
    filename: str = "input.rs",
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    _ = (context, kwargs)
    return extract_rust_interface(code, filename)


@hook("parse_unit", language="zig", priority=Priority.STANDARD)
def _zig_parse_hook(
    code: str,
    filename: str = "input.zig",
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    _ = (context, kwargs)
    return extract_zig_interface(code, filename)


@hook("parse_unit", language="nim", priority=Priority.STANDARD)
def _nim_parse_hook(
    code: str,
    filename: str = "input.nim",
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    _ = (context, kwargs)
    return extract_nim_interface(code, filename)


class RustBackend:
    """Parser backend for extracting C ABI interface surfaces from Rust sources."""

    supported_languages: frozenset[str] = frozenset({"rust"})
    supported_classifications: frozenset[str] = frozenset({"interface", "source"})

    @property
    def name(self) -> str:
        return "rust"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def is_available(self) -> bool:
        return True

    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
    ) -> SourceUnit:
        _ = (include_dirs, extra_args, use_default_includes, recursive_includes, max_depth, project_prefixes)
        return extract_rust_interface(code, filename)


class ZigBackend:
    """Parser backend for extracting C ABI interface surfaces from Zig sources."""

    supported_languages: frozenset[str] = frozenset({"zig"})
    supported_classifications: frozenset[str] = frozenset({"source", "interface"})

    @property
    def name(self) -> str:
        return "zig"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def is_available(self) -> bool:
        return True

    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
    ) -> SourceUnit:
        _ = (include_dirs, extra_args, use_default_includes, recursive_includes, max_depth, project_prefixes)
        return extract_zig_interface(code, filename)


class NimBackend:
    """Parser backend for extracting C ABI interface surfaces from Nim sources."""

    supported_languages: frozenset[str] = frozenset({"nim"})
    supported_classifications: frozenset[str] = frozenset({"source", "interface"})

    @property
    def name(self) -> str:
        return "nim"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def is_available(self) -> bool:
        return True

    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
    ) -> SourceUnit:
        _ = (include_dirs, extra_args, use_default_includes, recursive_includes, max_depth, project_prefixes)
        return extract_nim_interface(code, filename)


from headerkit.backends import register_backend  # noqa: E402

register_backend("rust", RustBackend, is_default=False)
register_backend("zig", ZigBackend, is_default=False)
register_backend("nim", NimBackend, is_default=False)
