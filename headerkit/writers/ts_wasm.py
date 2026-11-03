"""IR to TypeScript WebAssembly (.d.ts) declaration writer.

Generates ambient TypeScript ``WebAssembly.Exports`` interface definitions
for C/C++ functions exported from a WebAssembly module.
"""

from __future__ import annotations

from typing import ClassVar

from headerkit.ir import (
    Array,
    CType,
    Declaration,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    SourceUnit,
    Typedef,
    TypeExpr,
)
from headerkit.writers.base import BaseWriter, ScaffoldOptions, WriterOption

_TS_KEYWORDS: frozenset[str] = frozenset(
    {
        "break",
        "case",
        "catch",
        "class",
        "const",
        "continue",
        "debugger",
        "default",
        "delete",
        "do",
        "else",
        "enum",
        "export",
        "extends",
        "false",
        "finally",
        "for",
        "function",
        "if",
        "import",
        "in",
        "instanceof",
        "new",
        "null",
        "return",
        "super",
        "switch",
        "this",
        "throw",
        "true",
        "try",
        "typeof",
        "var",
        "void",
        "while",
        "with",
        "as",
        "implements",
        "interface",
        "let",
        "package",
        "private",
        "protected",
        "public",
        "static",
        "yield",
    }
)

_BIGINT_CTYPES: frozenset[str] = frozenset(
    {
        "int64_t",
        "uint64_t",
        "int64",
        "uint64",
        "i64",
        "u64",
        "long long",
        "signed long long",
        "unsigned long long",
        "long long int",
        "signed long long int",
        "unsigned long long int",
        "int_least64_t",
        "uint_least64_t",
        "int_fast64_t",
        "uint_fast64_t",
        "intmax_t",
        "uintmax_t",
    }
)

_CV_PREFIXES: tuple[str, ...] = (
    "const ",
    "volatile ",
    "restrict ",
    "__restrict ",
    "__restrict__ ",
    "struct ",
    "union ",
    "enum ",
)


def _clean_ctype_name(raw_name: str, qualifiers: list[str] | tuple[str, ...] = ()) -> str:
    """Normalize a CType name + qualifiers into a canonical C type spelling."""
    type_quals = [q for q in qualifiers if q not in ("const", "volatile", "restrict", "__restrict", "__restrict__")]
    name = " ".join([*type_quals, raw_name]).strip()
    while True:
        prev = name
        for prefix in _CV_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix) :].strip()
        if name == prev:
            break
    return " ".join(name.split())


def _sanitize_ts_param_name(name: str | None, index: int) -> str:
    """Return a valid TypeScript parameter identifier."""
    if not name or name == "...":
        return f"arg{index}"
    cleaned = name.strip()
    if cleaned in _TS_KEYWORDS:
        return f"{cleaned}_"
    return cleaned


class TsWasmWriter(BaseWriter):
    """Writer that generates TypeScript ``.d.ts`` declarations for WebAssembly exports."""

    name: str = "ts_wasm"
    format_description: str = "TypeScript WebAssembly (.d.ts) declarations"
    default_output_pattern: str = "{dir}/{stem}.d.ts"
    default_extension: str = ".d.ts"
    comment_prefix: ClassVar[str] = "//"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="interface_name",
            description="Name of the exported WebAssembly.Exports TypeScript interface",
            default="WasmExports",
            type=str,
        ),
        WriterOption(
            name="include_memory",
            description="Include readonly memory: WebAssembly.Memory in the interface",
            default=True,
            type=bool,
        ),
    )

    def __init__(
        self,
        *,
        interface_name: str = "WasmExports",
        include_memory: bool = True,
    ) -> None:
        self.interface_name = interface_name or "WasmExports"
        self.include_memory = bool(include_memory)
        self._typedefs: dict[str, TypeExpr] = {}

    def _prepare(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> SourceUnit | Header:
        if options is not None:
            iface = options.get_option("interface_name")
            if iface is not None:
                self.interface_name = str(iface)
            inc_mem = options.get_option("include_memory")
            if inc_mem is not None:
                self.include_memory = bool(inc_mem)
        return super()._prepare(unit, options=options)

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in TypeScript comments."""
        return "// {line}"

    def _collect_typedefs(self, declarations: list[Declaration]) -> dict[str, TypeExpr]:
        typedefs: dict[str, TypeExpr] = {}
        for decl in declarations:
            if isinstance(decl, Typedef) and decl.name:
                typedefs[decl.name] = decl.underlying_type
        return typedefs

    def _map_type(self, type_expr: TypeExpr, *, is_return: bool = False, _seen: set[str] | None = None) -> str:
        """Map an IR TypeExpr to its WebAssembly TypeScript scalar type."""
        if isinstance(type_expr, (Pointer, Reference, FunctionPointer, Array)):
            return "number"

        if isinstance(type_expr, CType):
            if type_expr.name.strip().endswith("*"):
                return "number"
            clean = _clean_ctype_name(type_expr.name, type_expr.qualifiers)
            if clean == "void":
                return "void" if is_return else "number"
            if clean in _BIGINT_CTYPES:
                return "bigint"
            if clean in self._typedefs:
                seen = _seen or set()
                if clean not in seen:
                    seen.add(clean)
                    return self._map_type(self._typedefs[clean], is_return=is_return, _seen=seen)
            return "number"

        return "number"

    def _format_param(self, param: Parameter, index: int) -> str:
        p_name = _sanitize_ts_param_name(param.name, index)
        override = param.type_overrides.get("ts_wasm") or param.type_overrides.get("ts")
        p_type = override if override else self._map_type(param.type, is_return=False)
        return f"{p_name}: {p_type}"

    def _format_jsdoc(self, func: Function) -> list[str]:
        doc_lines: list[str] = []
        if func.doc:
            for line in func.doc.strip().splitlines():
                doc_lines.append(line.strip().replace("*/", "*\\/"))
        if func.is_deprecated:
            if not any(line.startswith("@deprecated") for line in doc_lines):
                doc_lines.append("@deprecated")
        if not doc_lines:
            return []
        if len(doc_lines) == 1:
            return [f"  /** {doc_lines[0]} */"]
        out = ["  /**"]
        for line in doc_lines:
            out.append(f"   * {line}" if line else "   *")
        out.append("   */")
        return out

    def _format_function(self, func: Function) -> list[str]:
        lines = self._format_jsdoc(func)
        params = [self._format_param(p, idx) for idx, p in enumerate(func.parameters)]
        if func.is_variadic:
            params.append("...varargs: number[]")
        ret_override = func.type_overrides.get("ts_wasm") or func.type_overrides.get("ts")
        ret_type = ret_override if ret_override else self._map_type(func.return_type, is_return=True)
        lines.append(f"  {func.name}({', '.join(params)}): {ret_type};")
        return lines

    def _render(self, unit: SourceUnit | Header) -> str:
        self._typedefs = self._collect_typedefs(list(unit.declarations))
        lines: list[str] = [
            "// Generated by headerkit",
            "",
            f"export interface {self.interface_name} extends WebAssembly.Exports {{",
        ]
        if self.include_memory:
            lines.append("  readonly memory: WebAssembly.Memory;")

        emitted_names: set[str] = set()
        for decl in unit.declarations:
            if isinstance(decl, Function) and decl.name and decl.name not in emitted_names:
                emitted_names.add(decl.name)
                lines.extend(self._format_function(decl))

        lines.append("}")
        return "\n".join(lines) + "\n"


def write_ts_wasm(
    header: Header | SourceUnit,
    *,
    interface_name: str = "WasmExports",
    include_memory: bool = True,
) -> str:
    """Convenience function to generate TypeScript WebAssembly (.d.ts) declarations."""
    writer = TsWasmWriter(interface_name=interface_name, include_memory=include_memory)
    return writer.write(header)


from headerkit.writers import register_writer  # noqa: E402

register_writer("ts_wasm", TsWasmWriter, description="TypeScript WebAssembly (.d.ts) declarations")
