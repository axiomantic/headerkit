r"""IR to Nim binding writer.

This module converts the headerkit IR (Intermediate Representation) to
Nim declaration files using ``{.importc.}`` and ``{.importcpp.}`` pragmas.

Features
--------
* C and C++ Interop -- Emits ``{.importc.}`` for C and ``{.importcpp: "...".}`` for C++
* C++ Classes & Inheritance -- Maps classes/structs with bases, methods, and constructors
* Function & Type Generics -- Maps templates to Nim generics: ``type Foo[T] = object``, ``proc bar[T](x: T)``
* References & Pointers -- Maps ``Reference`` to ``var T`` / ``byref`` and ``Pointer`` to ``ptr T``
* Identifier Escaping & Style -- Handles Nim keywords with accent quotes (e.g. ``\`type\```)
"""

from __future__ import annotations

import textwrap
from dataclasses import replace
from typing import ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
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
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions, extract_function_names
from headerkit.workorder import build_work_order_files
from headerkit.writers.base import DEDENT_BLOCK, BaseWriter, WriterOption, render_block_template

NIM_KEYWORDS: set[str] = {
    "addr",
    "and",
    "array",
    "as",
    "asm",
    "bind",
    "block",
    "break",
    "case",
    "cast",
    "concept",
    "const",
    "continue",
    "converter",
    "defer",
    "discard",
    "distinct",
    "div",
    "do",
    "elif",
    "else",
    "end",
    "enum",
    "except",
    "export",
    "finally",
    "for",
    "from",
    "func",
    "if",
    "import",
    "in",
    "include",
    "interface",
    "is",
    "isnot",
    "iterator",
    "lent",
    "let",
    "macro",
    "method",
    "mixin",
    "mod",
    "nil",
    "not",
    "notin",
    "object",
    "of",
    "or",
    "out",
    "proc",
    "ptr",
    "raise",
    "ref",
    "return",
    "seq",
    "set",
    "shl",
    "shr",
    "sink",
    "static",
    "template",
    "try",
    "tuple",
    "type",
    "typed",
    "typedesc",
    "untyped",
    "using",
    "var",
    "when",
    "while",
    "xor",
    "yield",
}

C_TO_NIM_PRIMITIVES: dict[str, str] = {
    "void": "void",
    "char": "cchar",
    "signed char": "cschar",
    "unsigned char": "uint8",
    "short": "cshort",
    "short int": "cshort",
    "signed short": "cshort",
    "signed short int": "cshort",
    "unsigned short": "cushort",
    "unsigned short int": "cushort",
    "int": "cint",
    "signed int": "cint",
    "signed": "cint",
    "unsigned": "cuint",
    "unsigned int": "cuint",
    "long": "clong",
    "long int": "clong",
    "signed long": "clong",
    "signed long int": "clong",
    "unsigned long": "culong",
    "unsigned long int": "culong",
    "long long": "clonglong",
    "long long int": "clonglong",
    "signed long long": "clonglong",
    "signed long long int": "clonglong",
    "unsigned long long": "culonglong",
    "unsigned long long int": "culonglong",
    "float": "cfloat",
    "double": "cdouble",
    "long double": "clongdouble",
    "bool": "bool",
    "_Bool": "bool",
    "size_t": "csize_t",
    "ssize_t": "int",
    "intptr_t": "int",
    "uintptr_t": "uint",
    "ptrdiff_t": "int",
    "int8_t": "int8",
    "int16_t": "int16",
    "int32_t": "int32",
    "int64_t": "int64",
    "uint8_t": "uint8",
    "uint16_t": "uint16",
    "uint32_t": "uint32",
    "uint64_t": "uint64",
    "cstring": "cstring",
    "void*": "pointer",
}


CPP_OPERATOR_MAP: dict[str, str] = {
    "operator[]": "`[]`",
    "operator()": "`()`",
    "operator+": "`+`",
    "operator-": "`-`",
    "operator*": "`*`",
    "operator/": "`/`",
    "operator==": "`==`",
    "operator!=": "`!=`",
    "operator<": "`<`",
    "operator<=": "`<=`",
    "operator>": "`>`",
    "operator>=": "`>=`",
    "operator+=": "`+=`",
    "operator-=": "`-=`",
    "operator*=": "`*=`",
    "operator/=": "`/=`",
    "operator%=": "`%=`",
    "operator&=": "`&=`",
    "operator|=": "`|=`",
    "operator^=": "`^=`",
    "operator<<=": "`shl=`",
    "operator>>=": "`shr=`",
    "operator=": "assign",
    "operator%": "`%`",
    "operator&": "`&`",
    "operator|": "`|`",
    "operator^": "`^`",
    "operator~": "`~`",
    "operator!": "`!`",
    "operator&&": "`and`",
    "operator||": "`or`",
    "operator<<": "`shl`",
    "operator>>": "`shr`",
    "operator++": "`inc`",
    "operator--": "`dec`",
    "operator->": "`->`",
}


STANDARD_NIM_TYPES: set[str] = {
    "int",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "cint",
    "cuint",
    "cshort",
    "cushort",
    "clong",
    "culong",
    "clonglong",
    "culonglong",
    "cfloat",
    "cdouble",
    "csize_t",
    "cchar",
    "cschar",
    "cuchar",
    "cstring",
    "bool",
    "char",
    "byte",
    "pointer",
    "void",
    "auto",
    "ptr",
    "var",
    "sink",
    "string",
    "openArray",
    "seq",
    "array",
    "tuple",
    "RootObj",
    "UncheckedArray",
    "proc",
    "c_type",
    "c_set",
    "std_false_type",
    "std_true_type",
    "std_map",
    "std_unordered_map",
    "std_pair",
    "duration",
    "typedesc",
    "Atomic",
    "SharedPtr",
    "UniquePtr",
    "WeakPtr",
    "CppVector",
    "std_optional",
    "initializer_list",
}


def _extract_type_identifiers(type_str: str) -> set[str]:
    """Extract individual identifier tokens from a formatted Nim type expression."""
    cleaned = (
        type_str.replace("[", " ")
        .replace("]", " ")
        .replace(",", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("*", " ")
        .replace("`", " ")
        .replace(":", " ")
        .replace("{", " ")
        .replace("}", " ")
        .replace(".", " ")
    )
    tokens: set[str] = set()
    for part in cleaned.split():
        p = part.strip()
        if p and not p.isdigit() and p not in ("cdecl", "inline"):
            tokens.add(p)
    return tokens


def _c_type_spelling(name: str, is_typedef: bool, tag_keyword: str) -> str:
    """Return the C spelling ``importc`` must use to name this tag.

    ``typedef enum { ... } Flags;`` declares no ``enum Flags`` tag, so an
    ``importc: "enum Flags"`` emits C that names an incomplete type and the
    build fails with ``cast to incomplete type 'enum Flags'``. The same is true
    of a tag-less ``typedef struct { ... } Rec;``.

    ``is_typedef`` is the discriminator the other writers already use for this
    -- ``headerkit.writers.cffi._find_typedef_enum_pairs`` documents it. When it
    is set, the bare name is a valid C type spelling; without it the tag keyword
    is required, since a plain ``struct Rec { ... };`` declares no bare ``Rec``.

    It is *not* simply "a typedef of the same name exists", and the rule differs
    by declaration kind:

    - a **record** sets it when the bare name spells the type -- no tag at all,
      or an alias repeating the tag;
    - an **enum** sets it only when there is no tag at all, because a tagged
      ``typedef enum Switch { ... } Switch;`` really does declare
      ``enum Switch``, and the cffi and Cython writers re-emit that tag.

    So a tagged typedef'd enum has a same-named typedef and ``is_typedef=False``.
    ``tests/test_regression_backend_parity.py`` pins both halves of that split.
    """
    return name if is_typedef else f"{tag_keyword} {name}"


def _escape_ident(name: str, is_type: bool = False) -> str:
    """Escape Nim keywords, operators, and invalid identifier characters.

    In Nim, identifiers cannot begin or end with underscores, nor contain
    consecutive underscores ('__').
    """
    if not name:
        return "anon"
    if name in CPP_OPERATOR_MAP:
        return CPP_OPERATOR_MAP[name]
    if name.startswith('operator""'):
        suffix_str = name[10:].strip().lstrip("_")
        return f"op_lit_{suffix_str}" if suffix_str else "op_lit"
    if "::" in name:
        name = name.replace("::", "_")

    num_leading = len(name) - len(name.lstrip("_"))
    num_trailing = len(name) - len(name.rstrip("_"))
    if num_leading == len(name):
        return f"u{num_leading}"

    core = name[num_leading : len(name) - num_trailing]
    while "__" in core:
        core = core.replace("__", "_")

    if num_leading == 1:
        prefix = "u_"
    elif num_leading == 2:
        prefix = "uu_"
    elif num_leading > 2:
        prefix = f"u{num_leading}_"
    else:
        prefix = ""

    if num_trailing == 1:
        suffix = "_u"
    elif num_trailing == 2:
        suffix = "_uu"
    elif num_trailing > 2:
        suffix = f"_u{num_trailing}"
    else:
        suffix = ""

    clean = prefix + core + suffix
    while "__" in clean:
        clean = clean.replace("__", "_")
    if clean.startswith("_"):
        clean = "u" + clean.lstrip("_")
    if clean.endswith("_"):
        clean = clean.rstrip("_") + "u"

    if is_type:
        if clean == "set":
            return "c_set"
        if clean == "type":
            return "c_type"

    if clean.lower() in NIM_KEYWORDS:
        return f"`{clean}`"
    return clean


def _normalize_nim_ident(name: str) -> str:
    """Normalize a Nim identifier according to Nim's case- and underscore-insensitivity rules.

    In Nim, identifiers are compared case-insensitively and underscore-insensitively
    (except for the first character which differentiates type vs non-type).
    """
    clean = name.strip("`")
    if not clean:
        return ""
    first = clean[0]
    rest = clean[1:].replace("_", "").lower()
    return first + rest


def _split_template_args(arg_str: str) -> list[str]:
    """Split top-level comma-separated template arguments respecting nested <...> and (...)."""
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in arg_str:
        if char in "<(":
            depth += 1
            current.append(char)
        elif char in ">)":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        args.append("".join(current).strip())
    return [a for a in args if a]


def _format_default_value(val: str | None, type_name: str | None = None) -> str | None:
    """Format a default parameter value for Nim or return None if unrepresentable in Nim."""
    if not val:
        return None
    v = val.strip()
    if v in ("nullptr", "NULL"):
        if type_name:
            clean_type = type_name.strip("`")
            if (
                clean_type.startswith("ptr ")
                or clean_type.startswith("ref ")
                or clean_type.startswith("pointer")
                or clean_type.startswith("cstring")
                or clean_type.startswith("proc ")
                or clean_type == "pointer"
            ):
                return "nil"
            return None
        return "nil"
    if v in ("true", "false"):
        return v
    # Check if integer with optional suffix (u, U, l, L)
    v_int = v.rstrip("uUlL")
    if v_int:
        try:
            int_val = int(v_int, 0) if v_int.startswith(("0x", "0X", "0b", "0B", "0o", "0O")) else int(v_int)
            if type_name:
                clean_type = type_name.strip("`")
                if clean_type in ("uint32", "cuint"):
                    return f"{v_int}'u32"
                elif clean_type in ("uint64", "culonglong"):
                    return f"{v_int}'u64"
                elif clean_type in ("uint16", "cushort"):
                    return f"{v_int}'u16"
                elif clean_type in ("uint8", "cuchar", "byte"):
                    return f"{v_int}'u8"
                elif clean_type in ("int64", "clonglong") or (clean_type in ("int32", "cint") and int_val > 2147483647):
                    return f"{v_int}'i64"
            return v_int
        except ValueError:
            pass
    # Check if float with optional f/F suffix
    v_float = v.removesuffix("f").removesuffix("F")
    if "." in v_float or "e" in v_float or "E" in v_float:
        try:
            float(v_float)
            return v_float
        except ValueError:
            pass
    if v.startswith("'") and v.endswith("'"):
        if type_name in ("char", "cchar"):
            return v
        return f"ord({v})"
    if v.startswith('"') and v.endswith('"'):
        if type_name in ("string", "cstring", "cchar", "ptr cchar", "pointer"):
            return v
        # Non-string types in C++ (like StringRef or complex types) cannot take a Nim string literal
        return None
    return None


class NimWriter(BaseWriter):
    """Writer that converts headerkit IR into Nim binding modules."""

    name: str = "nim"
    format_description: str = "Nim bindings with C and C++ interop"
    default_output_pattern: str = "{dir}/{stem}.nim"
    default_extension: str = ".nim"
    min_access_floor: ClassVar[str | None] = "public"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project", "wheel", "scikit-build")
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
        WriterOption(
            name="header_path",
            description="Header path to reference in {.header.} pragmas",
            default=None,
            type=str,
        ),
    )

    def __init__(self, *, header_path: str | None = None) -> None:
        self.header_path = header_path

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Nim comments."""
        return "# {line}"

    def _render(self, unit: SourceUnit | Header) -> str:
        """Convert parsed header IR to Nim source code."""
        header = unit
        lines: list[str] = []

        if not header.declarations:
            return "# Generated by headerkit\n"

        header_file = self.header_path or header.path or "header.h"
        lines.append("# Generated by headerkit")
        lines.append("")

        has_call_operator = any(
            isinstance(decl, Struct) and any(m.name == "operator()" for m in decl.methods)
            for decl in header.declarations
        )
        has_pointer_iterator = False
        for decl in header.declarations:
            if isinstance(decl, Struct):
                has_begin = any(m.name == "begin" for m in decl.methods if m.access not in ("private", "protected"))
                has_end = any(m.name == "end" for m in decl.methods if m.access not in ("private", "protected"))
                if has_begin and has_end:
                    has_pointer_iterator = True
                    break

        if has_call_operator:
            lines.append('{.experimental: "callOperator".}')
            lines.append("")

        # Collect function names and type names upfront for collision detection
        func_names: set[str] = {decl.name for decl in header.declarations if isinstance(decl, Function) and decl.name}
        for decl in header.declarations:
            if isinstance(decl, Struct):
                for m in decl.methods:
                    if m.name:
                        func_names.add(m.name)
                        func_names.add(_escape_ident(m.name))

        def _collect_bases(st: Struct) -> list[str]:
            res: list[str] = []
            for b in st.bases:
                if b.name:
                    clean_b = b.name.split("<")[0].strip()
                    res.append(clean_b)
                    res.append(clean_b.replace("::", "_"))
                    res.append(clean_b.split("::")[-1])
                    res.append(b.name)
                    res.append(b.name.replace("::", "_"))
                    res.append(b.name.split("::")[-1])
            for nr in st.nested_records:
                res.extend(_collect_bases(nr))
            return res

        def _collect_polymorphic_and_base_names(st: Struct, parent_prefix: str = "") -> list[str]:
            res: list[str] = _collect_bases(st)
            st_name = f"{parent_prefix}_{st.name}" if parent_prefix and st.name else (st.name or "")
            is_poly = (st.destructor and st.destructor.is_virtual) or any(m.is_virtual for m in st.methods)
            if is_poly:
                if st.name:
                    res.append(st.name)
                    res.append(st.name.split("::")[-1])
                if st_name:
                    res.append(st_name)
                    res.append(st_name.replace("::", "_"))
                if st.cpp_name:
                    res.append(st.cpp_name)
                    res.append(st.cpp_name.replace("::", "_"))
                    res.append(st.cpp_name.split("::")[-1])
            for nr in st.nested_records:
                res.extend(_collect_polymorphic_and_base_names(nr, parent_prefix=st_name or st.name or ""))
            return res

        all_base_names: set[str] = set()
        for decl in header.declarations:
            if isinstance(decl, Struct):
                all_base_names.update(_collect_polymorphic_and_base_names(decl))
        known_base_classes: set[str] = all_base_names
        self._normalized_base_names = {_normalize_nim_ident(b) for b in all_base_names}
        emitted_types: set[str] = set()
        types_section: list[str] = []
        procs_section: list[str] = []
        consts_section: list[str] = []

        self._struct_type_param_counts: dict[str, int] = {}

        def _collect_struct_param_counts(s: Struct) -> None:
            if s.name:
                tps = self._get_struct_type_params(s)
                self._struct_type_param_counts[s.name] = len(tps)
                if s.cpp_name:
                    self._struct_type_param_counts[s.cpp_name] = len(tps)
            for n in s.nested_records:
                _collect_struct_param_counts(n)

        for decl in header.declarations:
            if isinstance(decl, Struct):
                _collect_struct_param_counts(decl)

        has_std_exception = any(
            isinstance(decl, Struct) and any("std::exception" in b.name for b in decl.bases)
            for decl in header.declarations
        )

        if has_std_exception:
            types_section.append(
                'std_exception* {.importcpp: "std::exception", header: "<exception>", inheritable.} = object'
            )
            emitted_types.add("std_exception")

        def _type_contains(t: TypeExpr, target_prefix: str) -> bool:
            """Check structurally whether a TypeExpr contains target_prefix in its type names."""
            if isinstance(t, CType):
                return target_prefix in t.name
            elif isinstance(t, Pointer):
                return _type_contains(t.pointee, target_prefix)
            elif isinstance(t, Reference):
                return _type_contains(t.target, target_prefix)
            elif isinstance(t, Array):
                return _type_contains(t.element_type, target_prefix)
            elif isinstance(t, FunctionPointer):
                if _type_contains(t.return_type, target_prefix):
                    return True
                return any(_type_contains(p.type, target_prefix) for p in t.parameters)
            return False

        def _decl_contains(d: object, target_prefix: str) -> bool:
            """Check structurally whether a Declaration references target_prefix."""
            if isinstance(d, Struct):
                for b in d.bases:
                    if b.name and target_prefix in b.name:
                        return True
                for inner_k, inner_v in d.inner_typedefs.items():
                    if target_prefix in inner_k or target_prefix in inner_v:
                        return True
                for nested in d.nested_records:
                    if _decl_contains(nested, target_prefix):
                        return True
                for f in d.fields:
                    if _type_contains(f.type, target_prefix):
                        return True
                for m in d.methods + d.constructors:
                    if _type_contains(m.return_type, target_prefix):
                        return True
                    if any(_type_contains(p.type, target_prefix) for p in m.parameters):
                        return True
                if d.destructor and any(_type_contains(p.type, target_prefix) for p in d.destructor.parameters):
                    return True
            elif isinstance(d, Function):
                if _type_contains(d.return_type, target_prefix):
                    return True
                if any(_type_contains(p.type, target_prefix) for p in d.parameters):
                    return True
            elif isinstance(d, Typedef):
                if _type_contains(d.underlying_type, target_prefix):
                    return True
            elif isinstance(d, Variable):
                if _type_contains(d.type, target_prefix):
                    return True
            return False

        has_cpp_string = any(_decl_contains(decl, "string") for decl in header.declarations)
        if has_cpp_string and "CppString" not in emitted_types:
            types_section.append('CppString* {.importcpp: "std::string", header: "<string>".} = object')
            emitted_types.add("CppString")

        has_unique_ptr = any(_decl_contains(decl, "unique_ptr") for decl in header.declarations)
        if has_unique_ptr and "UniquePtr" not in emitted_types:
            types_section.append('UniquePtr*[T] {.importcpp: "std::unique_ptr<\'0>", header: "<memory>".} = object')
            emitted_types.add("UniquePtr")
            procs_section.extend(
                [
                    "",
                    'proc `=copy`*[T](dst: var UniquePtr[T], src: UniquePtr[T]) {.error: "std::unique_ptr cannot be copied in Nim; use std/moves.move() or sink".}',
                    "",
                    'proc move*[T](p: var UniquePtr[T]): UniquePtr[T] {.importcpp: "std::move(@)", header: "<utility>".}',
                    "",
                    'proc get*[T](p: UniquePtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
                    "",
                    'proc reset*[T](p: var UniquePtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
                ]
            )

        has_shared_ptr = any(_decl_contains(decl, "shared_ptr") for decl in header.declarations)
        if has_shared_ptr and "SharedPtr" not in emitted_types:
            types_section.append('SharedPtr*[T] {.importcpp: "std::shared_ptr<\'0>", header: "<memory>".} = object')
            emitted_types.add("SharedPtr")
            procs_section.extend(
                [
                    "",
                    'proc get*[T](p: SharedPtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
                    "",
                    'proc reset*[T](p: var SharedPtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
                    "",
                    'proc useCount*[T](p: SharedPtr[T]): clong {.importcpp: "#.use_count()", header: "<memory>".}',
                ]
            )

        has_atomic = any(_decl_contains(decl, "atomic") for decl in header.declarations)
        if has_atomic and "Atomic" not in emitted_types:
            types_section.append('Atomic*[T] {.importcpp: "std::atomic<\'0>", header: "<atomic>".} = object')
            emitted_types.add("Atomic")

        has_wchar = any(_decl_contains(decl, "wchar") for decl in header.declarations)
        if has_wchar and "wchar_t" not in emitted_types:
            types_section.append('wchar_t* {.importc: "wchar_t".} = cint')
            emitted_types.add("wchar_t")

        has_optional = any(_decl_contains(decl, "optional") for decl in header.declarations)
        if has_optional and "std_optional" not in emitted_types:
            types_section.append('std_optional*[T] {.importcpp: "std::optional<\'0>", header: "<optional>".} = object')
            types_section.append("optional*[T] = std_optional[T]")
            emitted_types.update({"std_optional", "optional"})

        has_vector = any(_decl_contains(decl, "vector") for decl in header.declarations)
        if has_vector and "CppVector" not in emitted_types:
            types_section.append('CppVector*[T] {.importcpp: "std::vector<\'0>", header: "<vector>".} = object')
            emitted_types.add("CppVector")

        has_map = any(_decl_contains(decl, "map") for decl in header.declarations)
        if has_map and "std_map" not in emitted_types:
            types_section.append('std_map*[K, V] {.importcpp: "std::map<\'0, \'1>", header: "<map>".} = object')
            emitted_types.add("std_map")

        has_unordered_map = any(_decl_contains(decl, "unordered_map") for decl in header.declarations)
        if has_unordered_map and "std_unordered_map" not in emitted_types:
            types_section.append(
                'std_unordered_map*[K, V] {.importcpp: "std::unordered_map<\'0, \'1>", header: "<unordered_map>".} = object'
            )
            types_section.append("unordered_map*[K, V] = std_unordered_map[K, V]")
            emitted_types.update({"std_unordered_map", "unordered_map"})

        has_pair = any(_decl_contains(decl, "pair") for decl in header.declarations)
        if has_pair and "std_pair" not in emitted_types:
            types_section.append('std_pair*[T1, T2] {.importcpp: "std::pair<\'0, \'1>", header: "<utility>".} = object')
            emitted_types.add("std_pair")

        has_traits = any(
            _decl_contains(decl, "type_traits")
            or _decl_contains(decl, "false_type")
            or _decl_contains(decl, "true_type")
            for decl in header.declarations
        )
        if has_traits and "std_false_type" not in emitted_types:
            types_section.append(
                'std_false_type* {.importcpp: "std::false_type", header: "<type_traits>", inheritable.} = object'
            )
            types_section.append(
                'std_true_type* {.importcpp: "std::true_type", header: "<type_traits>", inheritable.} = object'
            )
            emitted_types.update({"std_false_type", "std_true_type"})

        has_iterator_tags = any(_decl_contains(decl, "iterator_tag") for decl in header.declarations)
        if has_iterator_tags and "std_input_iterator_tag" not in emitted_types:
            types_section.append(
                'std_input_iterator_tag* {.importcpp: "std::input_iterator_tag", header: "<iterator>".} = object'
            )
            types_section.append(
                'std_output_iterator_tag* {.importcpp: "std::output_iterator_tag", header: "<iterator>".} = object'
            )
            types_section.append(
                'std_forward_iterator_tag* {.importcpp: "std::forward_iterator_tag", header: "<iterator>".} = object'
            )
            types_section.append(
                'std_bidirectional_iterator_tag* {.importcpp: "std::bidirectional_iterator_tag", header: "<iterator>".} = object'
            )
            types_section.append(
                'std_random_access_iterator_tag* {.importcpp: "std::random_access_iterator_tag", header: "<iterator>".} = object'
            )
            emitted_types.update(
                {
                    "std_input_iterator_tag",
                    "std_output_iterator_tag",
                    "std_forward_iterator_tag",
                    "std_bidirectional_iterator_tag",
                    "std_random_access_iterator_tag",
                }
            )

        has_init_list = any(_decl_contains(decl, "initializer_list") for decl in header.declarations)
        if has_init_list and "initializer_list" not in emitted_types:
            types_section.append(
                'initializer_list*[T] {.importcpp: "std::initializer_list", header: "<initializer_list>".} = object'
            )
            emitted_types.add("initializer_list")

        has_duration = any(_decl_contains(decl, "duration") for decl in header.declarations)
        if has_duration and "duration" not in emitted_types:
            types_section.append(
                'duration*[Rep, Period = pointer] {.importcpp: "std::chrono::duration", header: "<chrono>".} = object'
            )
            types_section.append(
                'std_ratio*[Num = pointer, Denom = pointer] {.importcpp: "std::ratio", header: "<ratio>".} = object'
            )
            types_section.append('std_milli* {.importcpp: "std::milli", header: "<ratio>".} = object')
            types_section.append('std_micro* {.importcpp: "std::micro", header: "<ratio>".} = object')
            types_section.append('std_nano* {.importcpp: "std::nano", header: "<ratio>".} = object')
            emitted_types.update({"duration", "std_ratio", "std_milli", "std_micro", "std_nano"})

        # Collect opaque pointer targets referenced across declarations
        known_decl_names = {
            decl.name for decl in header.declarations if isinstance(decl, (Struct, Enum, Typedef)) and decl.name
        }
        for decl in header.declarations:
            if isinstance(decl, Struct):
                known_decl_names.update(decl.template_params)
                for m in decl.methods + decl.constructors:
                    known_decl_names.update(m.template_params)
            elif isinstance(decl, Function):
                known_decl_names.update(decl.template_params)
        known_decl_names.update(emitted_types)
        known_decl_names.update(C_TO_NIM_PRIMITIVES.keys())

        def _collect_opaque_pointer_names(t: TypeExpr) -> set[str]:
            names: set[str] = set()
            if isinstance(t, Pointer):
                if isinstance(t.pointee, CType):
                    raw = t.pointee.name.removeprefix("struct ").removeprefix("union ").strip()
                    if raw and raw not in known_decl_names and raw != "void" and "<" not in raw and "::" not in raw:
                        names.add(raw)
                else:
                    names.update(_collect_opaque_pointer_names(t.pointee))
            elif isinstance(t, Reference):
                names.update(_collect_opaque_pointer_names(t.target))
            elif isinstance(t, Array):
                names.update(_collect_opaque_pointer_names(t.element_type))
            elif isinstance(t, FunctionPointer):
                names.update(_collect_opaque_pointer_names(t.return_type))
                for p in t.parameters:
                    names.update(_collect_opaque_pointer_names(p.type))
            return names

        opaque_targets: set[str] = set()
        for decl in header.declarations:
            if isinstance(decl, Struct):
                for f in decl.fields:
                    opaque_targets.update(_collect_opaque_pointer_names(f.type))
                for m in decl.methods + decl.constructors:
                    opaque_targets.update(_collect_opaque_pointer_names(m.return_type))
                    for p in m.parameters:
                        opaque_targets.update(_collect_opaque_pointer_names(p.type))
            elif isinstance(decl, Typedef):
                opaque_targets.update(_collect_opaque_pointer_names(decl.underlying_type))
            elif isinstance(decl, Function):
                opaque_targets.update(_collect_opaque_pointer_names(decl.return_type))
                for p in decl.parameters:
                    opaque_targets.update(_collect_opaque_pointer_names(p.type))

        self._emitted_proc_sigs: set[tuple[str, tuple[str, ...]]] = set()
        self._emitted_normalized_types: dict[str, str] = {_normalize_nim_ident(st): st for st in STANDARD_NIM_TYPES}
        self._type_renames: dict[str, str] = {}
        self._type_aliases: dict[str, str] = {}

        for raw_opaque in sorted(opaque_targets):
            clean_opaque = _escape_ident(raw_opaque, is_type=True)
            norm = _normalize_nim_ident(clean_opaque)
            if (
                clean_opaque not in emitted_types
                and raw_opaque not in emitted_types
                and norm not in self._emitted_normalized_types
            ):
                inheritable_pragma = (
                    ", inheritable" if (raw_opaque in known_base_classes or clean_opaque in known_base_classes) else ""
                )
                types_section.append(
                    f'{clean_opaque}* {{.importc: "struct {raw_opaque}", header: "{header_file}", bycopy{inheritable_pragma}.}} = object'
                )
                emitted_types.add(clean_opaque)
                emitted_types.add(raw_opaque)
                self._emitted_normalized_types[norm] = clean_opaque
        known_types = set(STANDARD_NIM_TYPES)
        for decl in header.declarations:
            if hasattr(decl, "name") and decl.name:
                if isinstance(decl, Enum):
                    if decl.name.lower() not in ("type", "set", "pointer", "object", "string", "int", "bool"):
                        known_types.add(decl.name)
                        known_types.add(_escape_ident(decl.name, is_type=True))
                else:
                    known_types.add(decl.name)
                    known_types.add(_escape_ident(decl.name, is_type=True))
            if isinstance(decl, Enum) and decl.cpp_name:
                q = decl.cpp_name
                if decl.namespace and q.startswith(f"{decl.namespace}::"):
                    q = q[len(decl.namespace) + 2 :]
                if "::" in q:
                    known_types.add(_escape_ident(q.replace("::", "_"), is_type=True))
            if isinstance(decl, Struct):
                for nr in decl.nested_records:
                    if nr.name and nr.access not in ("private", "protected"):
                        known_types.add(nr.name)
                        known_types.add(f"{decl.name}_{nr.name}")
                        known_types.add(_escape_ident(f"{decl.name}_{nr.name}", is_type=True))
        for raw_opaque in opaque_targets:
            known_types.add(raw_opaque)
            known_types.add(_escape_ident(raw_opaque, is_type=True))
        self._known_types = known_types

        for decl in header.declarations:
            if isinstance(decl, Typedef) and decl.name:
                underlying = self._format_type(decl.underlying_type)
                base_alias = underlying.split("[")[0].strip("`")
                self._type_aliases[decl.name] = base_alias
                self._type_aliases[_escape_ident(decl.name, is_type=True)] = base_alias
            elif isinstance(decl, Struct):
                for in_name, in_underlying in decl.inner_typedefs.items():
                    u_fmt = self._format_type(CType(in_underlying))
                    b_alias = u_fmt.split("[")[0].strip("`")
                    self._type_aliases[in_name] = b_alias
                    self._type_aliases[f"{decl.name}_{in_name}"] = b_alias

        for decl in header.declarations:
            if isinstance(decl, Struct):
                name = decl.name or "AnonObject"
                if name not in emitted_types:
                    emitted_types.add(name)
                    t_lines, m_lines = self._write_struct(
                        decl, header_file, known_base_classes, emitted_types=emitted_types
                    )
                    types_section.extend(t_lines)
                    procs_section.extend(m_lines)
            elif isinstance(decl, Enum):
                t_lines, c_lines = self._write_enum(decl, header_file, func_names)
                types_section.extend(t_lines)
                consts_section.extend(c_lines)
            elif isinstance(decl, Typedef):
                t_lines = self._write_typedef(decl, emitted_types)
                if t_lines:
                    emitted_types.add(decl.name)
                    types_section.extend(t_lines)
            elif isinstance(decl, Function):
                procs_section.extend(self._write_function(decl, header_file))
            elif isinstance(decl, Constant):
                consts_section.extend(self._write_constant(decl))
            elif isinstance(decl, Variable):
                procs_section.extend(self._write_variable(decl, header_file))

        if types_section:
            lines.append("type")
            for t_line in types_section:
                lines.append(f"  {t_line}" if t_line else "")
            lines.append("")

        if consts_section:
            lines.append("const")
            for c_line in consts_section:
                lines.append(f"  {c_line}" if c_line else "")
            lines.append("")

        if has_pointer_iterator:
            inc_helper = [
                "",
                "proc inc*[T](p: var ptr T) {.inline.} =",
                "  p = cast[ptr T](cast[uint](p) + sizeof(T).uint)",
            ]
            procs_section = inc_helper + procs_section

        if procs_section:
            lines.extend(procs_section)
            lines.append("")

        output = "\n".join(lines).rstrip() + "\n"
        return output

    def _format_type(self, t: TypeExpr, *, in_param: bool = False) -> str:
        """Convert IR TypeExpr to a Nim type representation."""
        if isinstance(t, CType):
            name = t.name.strip()
            while True:
                prev = name
                for prefix in ("const ", "struct ", "union ", "enum ", "class ", "typename "):
                    if name.startswith(prefix):
                        name = name[len(prefix) :].strip()
                if name == prev:
                    break
            if name.endswith("&"):
                name = name.rstrip("&").strip()
            if name.endswith("*"):
                pointee_str = name.rstrip("*").strip()
                if not pointee_str or pointee_str == "void":
                    return "pointer"
                return f"ptr {self._format_type(CType(pointee_str))}"

            if hasattr(self, "_current_inner_typedefs") and name in self._current_inner_typedefs:
                underlying = self._current_inner_typedefs[name]
                return self._format_type(CType(underlying), in_param=in_param)

            if hasattr(self, "_current_nested_records") and name in self._current_nested_records:
                return _escape_ident(self._current_nested_records[name])

            if hasattr(self, "_current_struct_template_params"):
                if any(
                    name.startswith(f"{tp}_") or name.startswith(f"{tp}::")
                    for tp in self._current_struct_template_params
                ):
                    return "auto"

            # JUCE / C++ ParameterType type traits
            if any(
                name.startswith(p)
                for p in ("TypeHelpers::ParameterType<", "TypeHelpers_ParameterType<", "ParameterType<")
            ):
                inner = name[name.index("<") + 1 : name.index(">")].strip()
                return self._format_type(CType(inner), in_param=in_param)

            if "(anonymous" in name or "(unnamed" in name:
                return "pointer"

            if "type-parameter-" in name:
                return "pointer"

            if name.endswith("...") or name.endswith("&&..."):
                return "varargs[pointer]" if in_param else "pointer"

            if (
                "::*" in name
                or "(*" in name
                or ") const" in name
                or name.endswith("::")
                or ("::" in name and " " in name)
            ):
                return "pointer"

            if name == "decltype(auto)" or (name.startswith("decltype(") and name.endswith(")")):
                if name == "decltype(nullptr)":
                    return "pointer"
                return "auto"

            # C++ type traits
            if any(
                name.startswith(p)
                for p in (
                    "std::remove_cv_t<",
                    "remove_cv_t<",
                    "std::remove_reference_t<",
                    "remove_reference_t<",
                    "std::decay_t<",
                    "decay_t<",
                )
            ):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return self._format_type(CType(inner))
            elif any(
                name.startswith(p)
                for p in ("std::enable_if_t<", "enable_if_t<", "std::underlying_type_t<", "underlying_type_t<")
            ):
                return "auto"

            # C++ Smart Pointers & Containers mapping
            if name.startswith("std::shared_ptr<") or name.startswith("shared_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"SharedPtr[{self._format_type(CType(inner))}]"
            elif name.startswith("std::unique_ptr<") or name.startswith("unique_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"UniquePtr[{self._format_type(CType(inner))}]"
            elif name.startswith("std::weak_ptr<") or name.startswith("weak_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"WeakPtr[{self._format_type(CType(inner))}]"
            elif name.startswith("std::vector<") or name.startswith("vector<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"CppVector[{self._format_type(CType(inner))}]"
            elif name in ("std::vector", "vector"):
                return "CppVector[pointer]"
            elif name.startswith("std::array<") or name.startswith("array<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                args = _split_template_args(inner)
                if len(args) >= 2:
                    elem_type = self._format_type(CType(args[0]))
                    size_str = args[1].strip()
                    return f"array[{size_str}, {elem_type}]"
                return "pointer"
            elif name.startswith("std::map<") or name.startswith("map<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                args = _split_template_args(inner)
                if len(args) >= 2:
                    k_type = self._format_type(CType(args[0]))
                    v_type = self._format_type(CType(args[1]))
                    return f"std_map[{k_type}, {v_type}]"
                return "std_map[pointer, pointer]"
            elif name.startswith("std::pair<") or name.startswith("pair<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                args = _split_template_args(inner)
                if len(args) >= 2:
                    t1_type = self._format_type(CType(args[0]))
                    t2_type = self._format_type(CType(args[1]))
                    return f"std_pair[{t1_type}, {t2_type}]"
                return "std_pair[pointer, pointer]"
            elif name.startswith("std::optional<") or name.startswith("optional<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"std_optional[{self._format_type(CType(inner))}]"
            elif name in ("std::optional", "optional"):
                return "std_optional[pointer]"
            elif name.startswith("std::initializer_list<") or name.startswith("initializer_list<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"initializer_list[{self._format_type(CType(inner))}]"
            elif name in ("std::initializer_list", "initializer_list"):
                return "initializer_list[pointer]"
            elif name.startswith("std::atomic<") or name.startswith("atomic<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                if inner.endswith("*"):
                    inner_t: TypeExpr = Pointer(CType(inner[:-1].strip()))
                elif inner.endswith("&"):
                    inner_t = Reference(CType(inner[:-1].strip()))
                else:
                    inner_t = CType(inner)
                return f"Atomic[{self._format_type(inner_t)}]"
            elif name.startswith("std::function<") or name.startswith("function<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                l_paren = inner.find("(")
                r_paren = inner.rfind(")")
                if l_paren != -1 and r_paren > l_paren:
                    ret_str = inner[:l_paren].strip()
                    params_str = inner[l_paren + 1 : r_paren].strip()
                    p_list = _split_template_args(params_str)
                    p_formatted: list[str] = []
                    for i, p_item in enumerate(p_list):
                        p_item = p_item.strip()
                        if p_item.startswith("const "):
                            p_item = p_item[6:].strip()
                        if p_item.endswith("*"):
                            pt: TypeExpr = Pointer(CType(p_item[:-1].strip()))
                        elif p_item.endswith("&"):
                            pt = Reference(CType(p_item[:-1].strip()))
                        else:
                            pt = CType(p_item)
                        p_formatted.append(f"a{i}: {self._format_type(pt, in_param=True)}")
                    ret_type = self._format_type(CType(ret_str))
                    ret_part = f": {ret_type}" if ret_type != "void" else ""
                    return f"proc({', '.join(p_formatted)}){ret_part}"
            elif name.startswith("std::unordered_map<") or name.startswith("unordered_map<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                args = _split_template_args(inner)
                if len(args) >= 2:
                    k_type = self._format_type(CType(args[0]))
                    v_type = self._format_type(CType(args[1]))
                    return f"std_unordered_map[{k_type}, {v_type}]"
                return "std_unordered_map[pointer, pointer]"
            elif name in ("std::unordered_map", "unordered_map"):
                return "std_unordered_map[pointer, pointer]"
            elif name.startswith("std::string_view") or name == "string_view":
                return "cstring"
            elif name in ("std::nullopt_t", "nullopt_t", "std::nullptr_t", "nullptr_t", "decltype(nullptr)"):
                return "pointer"
            elif name.startswith("std::string") or name == "string":
                return "CppString"
            elif (
                name in ("std::function", "function")
                or name.startswith("std::chrono::time_point")
                or name.startswith("time_point")
            ):
                return "pointer"
            elif name.startswith("std::chrono::duration") or name.startswith("duration<"):
                return "duration[pointer, pointer]"
            elif "<" in name and name.endswith(">"):
                idx = name.index("<")
                r_idx = name.rindex(">")
                base = name[:idx].strip()
                inner = name[idx + 1 : r_idx].strip()
                if hasattr(self, "_current_struct_name") and self._current_struct_name:
                    curr_s = self._current_struct_name
                    if base == "Iterator" and curr_s.endswith("_Iterator"):
                        base = curr_s
                if "::" in base:
                    base = base.replace("::", "_")
                args = _split_template_args(inner)
                formatted_args: list[str] = []
                for a in args:
                    a = a.strip()
                    if "type-parameter-" in a or a.endswith("..."):
                        formatted_args.append("pointer")
                        continue
                    if a.startswith("const "):
                        a = a[6:].strip()
                    if a.endswith("*"):
                        at: TypeExpr = Pointer(CType(a[:-1].strip()))
                    elif a.endswith("&"):
                        at = Reference(CType(a[:-1].strip()))
                    else:
                        at = CType(a)
                    formatted_args.append(self._format_type(at))
                if hasattr(self, "_struct_type_param_counts") and base in self._struct_type_param_counts:
                    expected_count = self._struct_type_param_counts[base]
                    if expected_count == 0:
                        return _escape_ident(base, is_type=True)
                    if len(formatted_args) > expected_count:
                        formatted_args = formatted_args[:expected_count]
                return f"{_escape_ident(base, is_type=True)}[{', '.join(formatted_args)}]"

            if "<" in name and not name.endswith(">"):
                r_gt = name.rfind(">")
                if r_gt != -1 and "::" in name[r_gt:]:
                    member_suffix = name[r_gt + 1 :].strip().lstrip(":").strip()
                    tmpl_prefix = name[: r_gt + 1].strip()
                    lt_idx = tmpl_prefix.find("<")
                    if lt_idx != -1:
                        base = tmpl_prefix[:lt_idx].strip()
                        inner = tmpl_prefix[lt_idx + 1 : -1].strip()
                        if base.endswith("Helper") or base.endswith("Traits") or "Helper" in base or "Traits" in base:
                            return "auto"
                        combined = f"{base}_{member_suffix}".replace("::", "_")
                        args = _split_template_args(inner)
                        f_args: list[str] = []
                        for a in args:
                            a = a.strip()
                            if "type-parameter-" in a or a.endswith("..."):
                                f_args.append("pointer")
                            elif a.startswith("const "):
                                a = a[6:].strip()
                                f_args.append(self._format_type(CType(a)))
                            else:
                                f_args.append(self._format_type(CType(a)))
                        return f"{_escape_ident(combined, is_type=True)}[{', '.join(f_args)}]"

            if "<" in name or ">" in name:
                return "auto"

            if "::" in name:
                name = name.replace("::", "_")

            if hasattr(self, "_current_nested_records") and name in self._current_nested_records:
                return str(self._current_nested_records[name])

            if hasattr(self, "_current_struct_name") and self._current_struct_name:
                scoped = f"{self._current_struct_name}_{name}"
                if scoped in getattr(self, "_known_types", set()):
                    return _escape_ident(scoped, is_type=True)

            if hasattr(self, "_struct_name_stack"):
                for parent in reversed(self._struct_name_stack[:-1]):
                    scoped = f"{parent}_{name}"
                    if scoped in getattr(self, "_known_types", set()):
                        return _escape_ident(scoped, is_type=True)

            if hasattr(self, "_type_renames"):
                name = self._type_renames.get(name, name)

            if name in C_TO_NIM_PRIMITIVES:
                return C_TO_NIM_PRIMITIVES[name]
            return _escape_ident(name, is_type=True)

        elif isinstance(t, Pointer):
            if isinstance(t.pointee, CType):
                raw = t.pointee.name
                if (
                    raw == "void"
                    or "::*" in raw
                    or raw.endswith("::")
                    or ("::" in raw and " " in raw)
                    or "type-parameter-" in raw
                ):
                    return "pointer"
                if raw == "char" and "const" in t.pointee.qualifiers:
                    return "cstring"
            elif isinstance(t.pointee, FunctionPointer):
                return self._format_type(t.pointee)
            target_type = self._format_type(t.pointee)
            if target_type in ("auto", "pointer"):
                return "pointer"
            return f"ptr {target_type}"

        elif isinstance(t, Reference):
            if isinstance(t.target, CType):
                raw = t.target.name
                if raw.endswith("...") or raw.endswith("&&...") or "type-parameter-" in raw:
                    return "varargs[pointer]" if in_param else "pointer"
            target_type = self._format_type(t.target)
            if target_type == "auto":
                return "auto"
            if t.is_rvalue:
                # C++ rvalue reference (move semantics): var or sink in Nim
                return f"sink {target_type}" if in_param else f"var {target_type}"
            return f"var {target_type}"

        elif isinstance(t, Array):
            elem = self._format_type(t.element_type)
            if t.size is not None:
                return f"array[{t.size}, {elem}]"
            return f"UncheckedArray[{elem}]"

        elif isinstance(t, FunctionPointer):
            ret = self._format_type(t.return_type)
            params = [
                f"{_escape_ident(p.name or f'a{i}')}: {self._format_type(p.type, in_param=True)}"
                for i, p in enumerate(t.parameters)
            ]
            params_str = f"({', '.join(params)})" if params else "()"
            ret_str = f": {ret}" if ret != "void" else ""
            return f"proc{params_str}{ret_str} {{.cdecl.}}"

        return "pointer"

    def _get_struct_type_params(self, s: Struct) -> list[str]:
        """Return the list of generic type parameter names for a struct in Nim."""
        type_params = [tp.name for tp in s.template_parameters if tp.is_type and tp.name]
        if not type_params and s.template_params:
            type_params = [tp for tp in s.template_params if tp]
        return type_params

    def _get_struct_nim_type(self, s: Struct) -> str:
        """Return the Nim type string for a struct, including generic arguments."""
        s_name = s.name or "Object"
        if hasattr(self, "_type_renames"):
            s_name = self._type_renames.get(s_name, s_name)
        tps = self._get_struct_type_params(s)
        if tps:
            return f"{_escape_ident(s_name)}[{', '.join(_escape_ident(tp) for tp in tps)}]"
        return self._format_type(CType(s_name))

    def _write_struct(
        self,
        s: Struct,
        header_file: str,
        known_base_classes: set[str] | None = None,
        emitted_types: set[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Render a Struct or class as a Nim type declaration and its methods."""
        type_lines: list[str] = []
        methods_lines: list[str] = []

        name = s.name or "AnonObject"
        t_name = _escape_ident(name, is_type=True)
        norm = _normalize_nim_ident(t_name)
        if hasattr(self, "_emitted_normalized_types"):
            if norm in self._emitted_normalized_types:
                clean_base = t_name.strip("`")
                suffix_idx = 2
                candidate = _escape_ident(f"{clean_base}_{suffix_idx}", is_type=True)
                while _normalize_nim_ident(candidate) in self._emitted_normalized_types:
                    suffix_idx += 1
                    candidate = _escape_ident(f"{clean_base}_{suffix_idx}", is_type=True)
                if hasattr(self, "_type_renames"):
                    self._type_renames[name] = candidate
                t_name = candidate
                norm = _normalize_nim_ident(t_name)
            self._emitted_normalized_types[norm] = t_name

        old_inner = getattr(self, "_current_inner_typedefs", None)
        old_nested = getattr(self, "_current_nested_records", None)
        old_struct_tps = getattr(self, "_current_struct_template_params", None)
        old_struct_name = getattr(self, "_current_struct_name", None)

        self._current_inner_typedefs = dict(s.inner_typedefs)
        self._current_struct_template_params = set(self._get_struct_type_params(s))
        self._current_struct_name = name

        if not hasattr(self, "_struct_name_stack"):
            self._struct_name_stack = []
        self._struct_name_stack.append(name)

        self._current_nested_records = dict(old_nested or {})
        s_tps = self._get_struct_type_params(s)
        tps = s_tps
        for n in s.nested_records:
            if n.name:
                n_nim = f"{name}_{n.name}"
                if s_tps:
                    tp_args = f"[{', '.join(_escape_ident(tp) for tp in s_tps)}]"
                    self._current_nested_records[n.name] = f"{n_nim}{tp_args}"
                else:
                    self._current_nested_records[n.name] = n_nim

        if s.nested_records:
            parent_cpp = s.cpp_name or (f"{s.namespace}::{s.name}" if s.namespace else s.name)
            for nested in s.nested_records:
                if nested.access in ("private", "protected"):
                    continue
                nested_name = nested.name or "Nested"
                nested_nim_name = f"{name}_{nested_name}"
                nested_cpp = f"{parent_cpp}::{nested_name}"
                combined_tpl_params = list(s.template_params) + [
                    tp for tp in nested.template_params if tp not in s.template_params
                ]
                outer_param_names = {tp.name for tp in s.template_parameters if tp.name}
                combined_tpl_parameters = list(s.template_parameters) + [
                    tp for tp in nested.template_parameters if tp.name and tp.name not in outer_param_names
                ]
                nested_copy = replace(
                    nested,
                    name=nested_nim_name,
                    cpp_name=nested_cpp,
                    template_params=combined_tpl_params,
                    template_parameters=combined_tpl_parameters,
                )
                n_t, n_m = self._write_struct(
                    nested_copy,
                    header_file,
                    known_base_classes,
                    emitted_types=emitted_types,
                )
                type_lines.extend(n_t)
                is_dangerous_nested = (
                    nested_name.lower() in NIM_KEYWORDS
                    or nested_name.lower() in C_TO_NIM_PRIMITIVES
                    or nested_name.lower() in STANDARD_NIM_TYPES
                    or nested_name.lower()
                    in {
                        "iterator",
                        "const_iterator",
                        "node",
                        "entry",
                        "header",
                        "type",
                        "state",
                    }
                )
                nested_norm = _normalize_nim_ident(nested_name)
                is_norm_colliding = (
                    hasattr(self, "_emitted_normalized_types") and nested_norm in self._emitted_normalized_types
                )
                if (
                    emitted_types is not None
                    and not is_dangerous_nested
                    and nested_name not in emitted_types
                    and not is_norm_colliding
                ):
                    nested_tps = self._get_struct_type_params(nested_copy)
                    if nested_tps:
                        n_params = f"[{', '.join(_escape_ident(tp) for tp in nested_tps)}]"
                        type_lines.append(
                            f"{_escape_ident(nested_name, is_type=True)}*{n_params} = {_escape_ident(nested_nim_name, is_type=True)}{n_params}"
                        )
                    else:
                        type_lines.append(
                            f"{_escape_ident(nested_name, is_type=True)}* = {_escape_ident(nested_nim_name, is_type=True)}"
                        )
                    emitted_types.add(nested_name)
                    emitted_types.add(nested_nim_name)
                    if hasattr(self, "_emitted_normalized_types"):
                        self._emitted_normalized_types[nested_norm] = nested_name
            self._current_struct_name = name

        if s.inner_typedefs:
            for inner_name, inner_underlying in s.inner_typedefs.items():
                if emitted_types is not None and inner_name in emitted_types:
                    continue
                underlying_nim = self._format_type(CType(inner_underlying))
                if underlying_nim in ("auto", inner_name, f"`{inner_name}`"):
                    continue
                tps = self._get_struct_type_params(s)
                tokens = _extract_type_identifiers(underlying_nim)
                all_known = getattr(self, "_known_types", set()) | set(tps)
                if any(tok not in all_known for tok in tokens):
                    continue
                has_tp = bool(
                    tps
                    and (
                        any(tp in inner_underlying for tp in tps)
                        or any(nr in inner_underlying for nr in self._current_nested_records)
                    )
                )
                t_params = f"[{', '.join(_escape_ident(tp) for tp in tps)}]" if has_tp else ""
                is_dangerous_bare = (
                    inner_name.lower() in NIM_KEYWORDS
                    or inner_name.lower() in C_TO_NIM_PRIMITIVES
                    or inner_name.lower()
                    in {
                        "pointer",
                        "reference",
                        "value_type",
                        "difference_type",
                        "iterator_category",
                        "element",
                        "iterator",
                        "const_iterator",
                        "size_type",
                        "key_type",
                        "mapped_type",
                        "type",
                    }
                    or any(tp.lower() == inner_name.lower() for tp in tps)
                )
                alias_name = f"{name}_{inner_name}" if is_dangerous_bare else inner_name
                clean_alias = _escape_ident(alias_name, is_type=True)
                norm = _normalize_nim_ident(clean_alias)
                if hasattr(self, "_emitted_normalized_types"):
                    if norm in self._emitted_normalized_types:
                        alias_name = f"{name}_{inner_name}"
                        clean_alias = _escape_ident(alias_name, is_type=True)
                        norm = _normalize_nim_ident(clean_alias)
                        if norm in self._emitted_normalized_types:
                            continue
                    self._emitted_normalized_types[norm] = clean_alias
                type_lines.append(f"{clean_alias}*{t_params} = {underlying_nim}")
                if emitted_types is not None:
                    emitted_types.add(alias_name)
                    emitted_types.add(clean_alias)
                    emitted_types.add(inner_name)
                if hasattr(self, "_known_types"):
                    self._known_types.add(alias_name)
                    self._known_types.add(clean_alias)
                    self._known_types.add(inner_name)
                if hasattr(self, "_type_aliases"):
                    base_alias = underlying_nim.split("[")[0].strip("`")
                    self._type_aliases[inner_name] = base_alias
                    self._type_aliases[clean_alias] = base_alias
                    self._type_aliases[alias_name] = base_alias

        # Generics (only emit type parameters in Nim generic object declarations)
        type_tpl_params = [tp for tp in s.template_parameters if tp.is_type and tp.name]
        if type_tpl_params:
            tp_parts: list[str] = []
            param_names = {p.name for p in s.template_parameters if p.name}
            for tp in type_tpl_params:
                e_name = _escape_ident(tp.name)
                has_sibling_ref = bool(tp.default_value and any(p in tp.default_value for p in param_names))
                if tp.default_value and not has_sibling_ref:
                    def_val = self._format_type(CType(tp.default_value))
                    tp_parts.append(f"{e_name} = {def_val}")
                else:
                    tp_parts.append(e_name)
            gen_params = f"[{', '.join(tp_parts)}]"
        elif s.template_params:
            valid_tps = [tp for tp in s.template_params if tp]
            gen_params = f"[{', '.join(_escape_ident(tp) for tp in valid_tps)}]" if valid_tps else ""
        else:
            gen_params = ""
        if gen_params == "[]":
            gen_params = ""

        pragma_parts: list[str] = []
        is_cpp = s.is_cppclass or bool(s.methods or s.bases or s.constructors or s.destructor)

        if is_cpp:
            cpp_pattern = s.cpp_name or (f"{s.namespace}::{s.name}" if s.namespace else s.name)
            pragma_parts.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')
            pragma_parts.append("bycopy")
        else:
            pragma_parts.append(
                f'importc: "{_c_type_spelling(name, s.is_typedef, "union" if s.is_union else "struct")}", header: "{header_file}"'
            )
            if s.is_union:
                pragma_parts.append("union")
            else:
                pragma_parts.append("bycopy")

        if s.is_packed:
            pragma_parts.append("packed")

        # Inheritance
        base_str = ""
        if s.bases:
            # Single primary base in Nim object inheritance (skip private/protected bases)
            public_bases = [b for b in s.bases if b.access not in ("private", "protected")]
            tps = self._get_struct_type_params(s)
            valid_bases = [b for b in public_bases if b.name and b.name not in tps]
            if valid_bases:
                raw_base = valid_bases[0].name.strip("`")
                if hasattr(self, "_current_nested_records") and raw_base in self._current_nested_records:
                    raw_base = self._current_nested_records[raw_base]
                elif hasattr(self, "_current_struct_name") and self._current_struct_name:
                    scoped = f"{self._current_struct_name}_{raw_base}"
                    if scoped in getattr(self, "_known_types", set()):
                        raw_base = scoped
                if hasattr(self, "_struct_name_stack"):
                    for parent in reversed(self._struct_name_stack[:-1]):
                        scoped = f"{parent}_{raw_base}"
                        if scoped in getattr(self, "_known_types", set()):
                            raw_base = scoped
                            break
                base_t = self._format_type(CType(raw_base))
                if base_t not in ("auto", "pointer", "void"):
                    base_str = f" of {base_t}"
        is_inheritable = not base_str and (
            (s.destructor and s.destructor.is_virtual)
            or any(m.is_virtual for m in s.methods)
            or (
                known_base_classes
                and s.name
                and (
                    s.name in known_base_classes
                    or s.name.split("_")[-1] in known_base_classes
                    or (
                        hasattr(self, "_normalized_base_names")
                        and _normalize_nim_ident(s.name) in self._normalized_base_names
                    )
                    or (s.cpp_name and s.cpp_name.split("::")[-1] in known_base_classes)
                )
            )
        )
        if is_inheritable:
            if is_cpp:
                pragma_parts.append("inheritable")
            else:
                base_str = " of RootObj"

        pragma_str = f" {{.{', '.join(pragma_parts)}.}}" if pragma_parts else ""
        lines = [f"{t_name}*{gen_params}{pragma_str} = object{base_str}"]

        visible_fields = [f for f in s.fields if not f.is_static and f.access not in ("private", "protected")]
        if not visible_fields:
            lines[0] += ""
        else:
            for f in visible_fields:
                f_name = _escape_ident(f.name)
                f_type = self._format_type(f.type)
                tokens = _extract_type_identifiers(f_type)
                all_known = getattr(self, "_known_types", set()) | set(tps)
                if any(tok not in all_known for tok in tokens):
                    f_type = "pointer"
                lines.append(f"  {f_name}*: {f_type}")

        # Static member variables
        static_fields = [f for f in s.fields if f.is_static and f.access not in ("private", "protected")]
        for f in static_fields:
            struct_cpp = s.cpp_name or (f"{s.namespace}::{s.name}" if s.namespace else s.name)
            cpp_target = f"{struct_cpp}::{f.name}"
            var_name = _escape_ident(f"{name}_{f.name}")
            var_type = self._format_type(f.type)
            tokens = _extract_type_identifiers(var_type)
            all_known = getattr(self, "_known_types", set()) | set(tps)
            if var_type in ("`type`", "type", "auto") or any(tok not in all_known for tok in tokens):
                var_type = "pointer"
            methods_lines.extend(
                ["", f'var {var_name}* {{.importcpp: "{cpp_target}", header: "{header_file}".}}: {var_type}']
            )

        # Methods / Constructors / Iterators attached to struct
        for m in s.methods:
            if m.access in ("private", "protected") or m.is_deleted:
                continue
            methods_lines.extend(self._write_method(s, m, header_file))

        for ctor in s.constructors:
            if ctor.access in ("private", "protected") or ctor.is_deleted:
                continue
            methods_lines.extend(self._write_constructor(s, ctor, header_file))

        if s.destructor and s.destructor.access not in ("private", "protected") and not s.destructor.is_deleted:
            methods_lines.extend(self._write_destructor(s, s.destructor, header_file))

        # Iterators helper if begin()/end() are available and return raw pointer
        has_begin = any(m.name == "begin" for m in s.methods if m.access not in ("private", "protected"))
        has_end = any(m.name == "end" for m in s.methods if m.access not in ("private", "protected"))
        if has_begin and has_end:
            begin_methods = [m for m in s.methods if m.name == "begin" and m.access not in ("private", "protected")]
            has_const_begin = any(m.is_const for m in begin_methods)
            matching_begin = [m for m in begin_methods if m.is_const == has_const_begin]
            if matching_begin:
                ret_nim = self._format_type(matching_begin[0].return_type)
                if ret_nim.startswith("ptr ") and ret_nim != "ptr void":
                    tps = self._get_struct_type_params(s)
                    struct_type = self._get_struct_nim_type(s)
                    t_params = f"[{', '.join(_escape_ident(tp) for tp in tps)}]" if tps else ""
                    this_param = f"this: {struct_type}" if has_const_begin else f"this: var {struct_type}"
                    methods_lines.extend(
                        [
                            "",
                            f"iterator items*{t_params}({this_param}): auto {{.inline.}} =",
                            "  var it = this.begin()",
                            "  while it != this.end():",
                            "    yield it[]",
                            "    inc it",
                        ]
                    )

        # Idiomatic stringifier helper for String types exposing toRawUTF8
        if s.name == "String" and any(
            m.name == "toRawUTF8" for m in s.methods if m.access not in ("private", "protected")
        ):
            struct_type = self._get_struct_nim_type(s)
            methods_lines.extend(
                [
                    "",
                    f'proc toCString*(this: {struct_type}): cstring {{.importcpp: "(char*)#.toRawUTF8()", header: "{header_file}".}}',
                    f"proc `$`*(this: {struct_type}): string =",
                    "  let c = this.toCString()",
                    '  if c == nil: "" else: $c',
                ]
            )

        if hasattr(self, "_struct_name_stack") and self._struct_name_stack:
            self._struct_name_stack.pop()

        if old_inner is not None:
            self._current_inner_typedefs = old_inner
        else:
            self._current_inner_typedefs = {}

        if old_nested is not None:
            self._current_nested_records = old_nested
        else:
            self._current_nested_records = {}

        if old_struct_tps is not None:
            self._current_struct_template_params = old_struct_tps
        elif hasattr(self, "_current_struct_template_params"):
            delattr(self, "_current_struct_template_params")

        if old_struct_name is not None:
            self._current_struct_name = old_struct_name
        elif hasattr(self, "_current_struct_name"):
            delattr(self, "_current_struct_name")

        type_lines.extend(lines)
        return type_lines, methods_lines

    def _canonicalize_proc_param_type(self, pt: str) -> str:
        clean = pt.strip()
        prefix = ""
        for pfx in ("var ", "ptr ", "lent ", "sink ", "typedesc["):
            if clean.startswith(pfx):
                prefix = pfx
                clean = clean[len(pfx) :]
                if pfx == "typedesc[" and clean.endswith("]"):
                    clean = clean[:-1]
                break

        if "[" in clean and clean.endswith("]"):
            open_b = clean.find("[")
            clean_base = clean[:open_b].strip("`")
            inner_str = clean[open_b + 1 : -1]
            canon_base = clean_base
            if hasattr(self, "_type_aliases") and clean_base in self._type_aliases:
                canon_base = self._type_aliases[clean_base]
            elif hasattr(self, "_type_renames") and clean_base in self._type_renames:
                canon_base = self._type_renames[clean_base]
            inner_args = _split_template_args(inner_str)
            canon_args = [self._canonicalize_proc_param_type(arg.strip()) for arg in inner_args]
            clean = f"{canon_base}[{', '.join(canon_args)}]"
        else:
            clean_base = clean.strip("`")
            if hasattr(self, "_type_aliases") and clean_base in self._type_aliases:
                clean = self._type_aliases[clean_base]
            elif hasattr(self, "_type_renames") and clean_base in self._type_renames:
                clean = self._type_renames[clean_base]
            else:
                clean = clean_base

        if prefix == "typedesc[":
            return f"typedesc[{clean}]"
        return f"{prefix}{clean}"

    def _write_method(self, s: Struct, m: Function, header_file: str) -> list[str]:
        """Render a C++ member method or operator in Nim."""
        if m.is_deleted:
            return []
        old_struct_name = getattr(self, "_current_struct_name", None)
        if s.name:
            self._current_struct_name = s.name
        m_name = _escape_ident(m.name)
        params: list[str] = []

        # 'this' parameter or typedesc for static methods
        tps = self._get_struct_type_params(s)
        struct_type = self._get_struct_nim_type(s)

        if m.is_static:
            params.append(f"self_type: typedesc[{struct_type}]")
        elif m.is_const:
            params.append(f"this: {struct_type}")
        else:
            params.append(f"this: var {struct_type}")

        # Generic parameters (combine struct and method template parameters)
        all_tp = list(tps)
        m_tps = [tp.name for tp in m.template_parameters if tp.is_type and tp.name] or [
            tp for tp in m.template_params if tp
        ]
        for mtp in m_tps:
            if mtp not in all_tp:
                all_tp.append(mtp)
        t_params = f"[{', '.join(_escape_ident(tp) for tp in all_tp)}]" if all_tp else ""
        all_known = getattr(self, "_known_types", set()) | set(all_tp) | set(tps)

        for i, p in enumerate(m.parameters):
            p_name = _escape_ident(p.name or f"a{i}")
            p_type = self._format_type(p.type, in_param=True)
            tokens = _extract_type_identifiers(p_type)
            if any(tok not in all_known for tok in tokens):
                p_type = "auto"
            formatted_default = _format_default_value(p.default_value, p_type)
            default_str = f" = {formatted_default}" if formatted_default is not None else ""
            params.append(f"{p_name}: {p_type}{default_str}")

        param_types = tuple(
            self._canonicalize_proc_param_type(p.split(":", 1)[1].split("=")[0].strip() if ":" in p else p)
            for p in params
        )
        sig = (m_name, param_types)
        if hasattr(self, "_emitted_proc_sigs"):
            if sig in self._emitted_proc_sigs:
                if old_struct_name is not None:
                    self._current_struct_name = old_struct_name
                elif hasattr(self, "_current_struct_name"):
                    delattr(self, "_current_struct_name")
                return []
            self._emitted_proc_sigs.add(sig)

        ret_type = self._format_type(m.return_type)
        if ret_type != "void":
            tokens = _extract_type_identifiers(ret_type)
            if any(tok not in all_known for tok in tokens):
                ret_type = "auto"
        ret_str = f": {ret_type}" if ret_type != "void" else ""

        # Pragmas
        pragmas: list[str] = []
        if m.is_static:
            struct_cpp = s.cpp_name or (f"{s.namespace}::{s.name}" if s.namespace else s.name)
            cpp_pattern = f"{struct_cpp}::{m.name}(@)"
        elif m.name == "operator[]":
            cpp_pattern = "#[@]"
        elif m.name.startswith("operator"):
            op_sym = m.name[8:]
            if len(params) == 2:
                cpp_pattern = f"(# {op_sym} @)"
            else:
                cpp_pattern = f"{op_sym}(#)"
        else:
            cpp_pattern = f"#.{(m.name)}(@)"
        clean_cpp_pattern = cpp_pattern.replace('"', '\\"')
        pragmas.append(f'importcpp: "{clean_cpp_pattern}", header: "{header_file}"')

        decl = f"proc {m_name}*{t_params}({', '.join(params)}){ret_str} {{.{', '.join(pragmas)}.}}"
        if old_struct_name is not None:
            self._current_struct_name = old_struct_name
        elif hasattr(self, "_current_struct_name"):
            delattr(self, "_current_struct_name")
        return ["", decl]

    def _write_destructor(self, s: Struct, dtor: Function, header_file: str) -> list[str]:
        """Render a C++ destructor as a Nim destroy proc."""
        s_name = s.name or "Object"
        raw_cpp_name = s.cpp_name or s_name
        if hasattr(self, "_type_renames"):
            s_name = self._type_renames.get(s_name, s_name)
        tps = self._get_struct_type_params(s)
        struct_type = self._get_struct_nim_type(s)
        t_params = f"[{', '.join(_escape_ident(tp) for tp in tps)}]" if tps else ""
        decl = f'proc destroy*{t_params}(this: var {struct_type}) {{.importcpp: "#.~{raw_cpp_name}()", header: "{header_file}".}}'
        return ["", decl]

    def _write_constructor(self, s: Struct, ctor: Function, header_file: str) -> list[str]:
        """Render a C++ constructor as a Nim constructProc."""
        if ctor.is_deleted:
            return []
        s_name = s.name or "Object"
        raw_cpp_name = s.cpp_name or s_name
        if hasattr(self, "_type_renames"):
            s_name = self._type_renames.get(s_name, s_name)
        proc_name = _escape_ident(f"construct{s_name}")
        params: list[str] = []

        tps = self._get_struct_type_params(s)
        ret_type = self._get_struct_nim_type(s)

        all_tps = list(tps)
        ctor_tps = [tp.name for tp in ctor.template_parameters if tp.is_type and tp.name] or [
            tp for tp in ctor.template_params if tp
        ]
        for ctp in ctor_tps:
            if ctp not in all_tps:
                all_tps.append(ctp)

        all_known = getattr(self, "_known_types", set()) | set(all_tps)

        for i, p in enumerate(ctor.parameters):
            p_name = _escape_ident(p.name or f"a{i}")
            p_type = self._format_type(p.type, in_param=True)
            tokens = _extract_type_identifiers(p_type)
            if any(tok not in all_known for tok in tokens):
                p_type = "auto"
            formatted_default = _format_default_value(p.default_value, p_type)
            default_str = f" = {formatted_default}" if formatted_default is not None else ""
            params.append(f"{p_name}: {p_type}{default_str}")

        param_types = tuple(
            self._canonicalize_proc_param_type(p.split(":", 1)[1].split("=")[0].strip() if ":" in p else p)
            for p in params
        )
        sig = (proc_name, param_types)
        if hasattr(self, "_emitted_proc_sigs"):
            if sig in self._emitted_proc_sigs:
                return []
            self._emitted_proc_sigs.add(sig)

        if all_tps:
            t_params = f"[{', '.join(_escape_ident(tp) for tp in all_tps)}]"
            t_args = ", ".join(f"'*{i}" for i in range(len(tps)))
            cpp_pattern = f"{raw_cpp_name}<{t_args}>(@)" if t_args else f"{raw_cpp_name}(@)"
        else:
            t_params = ""
            cpp_pattern = f"{raw_cpp_name}(@)"

        pragma = f'importcpp: "{cpp_pattern}", header: "{header_file}", constructor'

        return ["", f"proc {proc_name}*{t_params}({', '.join(params)}): {ret_type} {{.{pragma}.}}"]

    def _write_enum(self, e: Enum, header_file: str, func_names: set[str] | None = None) -> tuple[list[str], list[str]]:
        """Render an Enum declaration, returning (type_lines, const_lines)."""
        name = e.name or ""
        is_anonymous = not name or "(unnamed" in name or "(anonymous" in name or name.startswith("enum (")

        if is_anonymous:
            # Emit anonymous enum values as constants
            const_lines: list[str] = []
            ns_prefix = ""
            if e.namespace:
                ns_parts = [p.strip() for p in e.namespace.split("::") if p.strip()]
                if len(ns_parts) > 1 and ns_parts[-1] != "std":
                    ns_prefix = f"{_escape_ident(ns_parts[-1])}_"
            for v in e.values:
                v_name = _escape_ident(v.name)
                if func_names and (v.name in func_names or v_name in func_names):
                    if ns_prefix:
                        v_name = f"{ns_prefix}{v_name}"
                    else:
                        v_name = f"{v_name}_val"
                if v.value is not None:
                    const_lines.append(f"{v_name}* = {v.value}")
                else:
                    const_lines.append(f"{v_name}* = 0")
            return [], const_lines

        qualified_name = None
        if e.cpp_name:
            q = e.cpp_name
            if e.namespace and q.startswith(f"{e.namespace}::"):
                q = q[len(e.namespace) + 2 :]
            if "::" in q:
                qualified_name = _escape_ident(q.replace("::", "_"))

        # Disambiguate if enum name collides with a function
        nim_name = f"{name}_enum" if func_names and name in func_names else name
        if name.lower() in ("type", "set", "pointer", "object", "string", "int", "bool") and qualified_name:
            nim_name = qualified_name
        e_name = _escape_ident(nim_name, is_type=True)
        norm = _normalize_nim_ident(e_name)
        if hasattr(self, "_emitted_normalized_types"):
            if norm in self._emitted_normalized_types:
                if qualified_name and _normalize_nim_ident(qualified_name) not in self._emitted_normalized_types:
                    e_name = qualified_name
                else:
                    clean_base = e_name.strip("`")
                    suffix_idx = 2
                    candidate = _escape_ident(f"{clean_base}_{suffix_idx}", is_type=True)
                    while _normalize_nim_ident(candidate) in self._emitted_normalized_types:
                        suffix_idx += 1
                        candidate = _escape_ident(f"{clean_base}_{suffix_idx}", is_type=True)
                    if hasattr(self, "_type_renames"):
                        self._type_renames[name] = candidate
                    e_name = candidate
                norm = _normalize_nim_ident(e_name)
            self._emitted_normalized_types[norm] = e_name

        if e.cpp_name:
            import_pragma = f'importcpp: "{e.cpp_name}", header: "{header_file}"'
        else:
            spelling = _c_type_spelling(name, e.is_typedef, "enum")
            import_pragma = f'importc: "{spelling}", header: "{header_file}"'
        lines = [f"{e_name}* {{.size: sizeof(cint), {import_pragma}.}} = enum"]
        const_lines = []
        seen_values: dict[int | str, str] = {}
        for v in e.values:
            v_name = _escape_ident(v.name)
            if v.value is not None:
                if v.value in seen_values:
                    first_name = seen_values[v.value]
                    const_lines.append(f"{v_name}* = {e_name}.{first_name}")
                else:
                    seen_values[v.value] = v_name
                    lines.append(f"  {v_name} = {v.value}")
            else:
                lines.append(f"  {v_name}")
        if qualified_name and qualified_name != e_name:
            q_norm = _normalize_nim_ident(qualified_name)
            if hasattr(self, "_emitted_normalized_types"):
                if q_norm not in self._emitted_normalized_types:
                    lines.append(f"{qualified_name}* = {e_name}")
                    self._emitted_normalized_types[q_norm] = qualified_name
            else:
                lines.append(f"{qualified_name}* = {e_name}")
        return lines, const_lines

    def _write_typedef(self, t: Typedef, emitted_types: set[str] | None = None) -> list[str]:
        """Render a Typedef declaration."""
        if not t.name:
            return []

        if t.name in STANDARD_NIM_TYPES or t.name.lower() in STANDARD_NIM_TYPES:
            return []

        if emitted_types and t.name in emitted_types:
            return []

        # Check self-referential typedefs (e.g. typedef struct foo foo;)
        if isinstance(t.underlying_type, CType):
            raw = t.underlying_type.name
            clean = raw.removeprefix("struct ").removeprefix("union ").removeprefix("enum ").strip()
            if clean == t.name:
                return []

        t_name = _escape_ident(t.name)
        underlying = self._format_type(t.underlying_type)
        if t_name == underlying or t.name == underlying:
            return []

        if _normalize_nim_ident(t_name) == _normalize_nim_ident(underlying):
            return []

        norm = _normalize_nim_ident(t_name)
        if hasattr(self, "_emitted_normalized_types"):
            if norm in self._emitted_normalized_types:
                return []
            self._emitted_normalized_types[norm] = t_name

        if emitted_types is not None:
            emitted_types.add(t.name)
            emitted_types.add(t_name)

        return [f"{t_name}* = {underlying}"]

    def _write_function(self, f: Function, header_file: str) -> list[str]:
        """Render a function declaration."""
        if f.access in ("private", "protected") or f.is_deleted:
            return []
        f_name = _escape_ident(f.name)
        params: list[str] = []
        all_tp = set(
            [tp.name for tp in f.template_parameters if tp.is_type and tp.name]
            or [tp for tp in f.template_params if tp]
        )
        all_known = getattr(self, "_known_types", set()) | all_tp

        for i, p in enumerate(f.parameters):
            p_name = _escape_ident(p.name or f"a{i}")
            p_type = self._format_type(p.type, in_param=True)
            tokens = _extract_type_identifiers(p_type)
            if any(tok not in all_known for tok in tokens):
                p_type = "auto"
            formatted_default = _format_default_value(p.default_value, p_type)
            default_str = f" = {formatted_default}" if formatted_default is not None else ""
            params.append(f"{p_name}: {p_type}{default_str}")

        param_types = tuple(
            self._canonicalize_proc_param_type(p.split(":", 1)[1].split("=")[0].strip() if ":" in p else p)
            for p in params
        )
        sig = (f_name, param_types)
        if hasattr(self, "_emitted_proc_sigs"):
            if sig in self._emitted_proc_sigs:
                return []
            self._emitted_proc_sigs.add(sig)

        ret_type = self._format_type(f.return_type)
        if ret_type != "void":
            tokens = _extract_type_identifiers(ret_type)
            if any(tok not in all_known for tok in tokens):
                ret_type = "auto"
        ret_str = f": {ret_type}" if ret_type != "void" else ""

        pragmas: list[str] = []
        if f.namespace:
            cpp_pattern = f"{f.namespace}::{f.name}(@)".replace('"', '\\"')
            pragmas.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')
        elif f.template_params or '"' in f.name:
            cpp_pattern = f"{f.name}(@)".replace('"', '\\"')
            pragmas.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')
        else:
            pragmas.append(f'importc: "{f.name}", header: "{header_file}"')

        if f.is_variadic:
            pragmas.append("varargs")

        if f.calling_convention:
            pragmas.append(f.calling_convention)
        else:
            pragmas.append("cdecl")

        t_params = f"[{', '.join(_escape_ident(tp) for tp in f.template_params)}]" if f.template_params else ""
        return [f"proc {f_name}*{t_params}({', '.join(params)}){ret_str} {{.{', '.join(pragmas)}.}}"]

    def _write_variable(self, v: Variable, header_file: str) -> list[str]:
        """Render an extern global variable."""
        v_name = _escape_ident(v.name)
        v_type = self._format_type(v.type)
        if v_type in ("`type`", "type", "c_type", "auto"):
            v_type = "pointer"
        return [f'var {v_name}* {{.importc: "{v.name}", header: "{header_file}".}}: {v_type}']

    def _write_constant(self, c: Constant) -> list[str]:
        """Render a constant."""
        if c.value is None:
            return []
        c_name = _escape_ident(c.name)
        return [f"{c_name}* = {c.value}"]

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        bindings_code = self._render(unit)
        fn_names = extract_function_names(unit)

        files: list[OutputFile] = []

        # 1. Nimble package spec
        nimble = textwrap.dedent(f"""\
            # Package
            version       = "0.1.0"
            author        = "HeaderKit"
            description   = "Nim bindings for {pkg}"
            license       = "MIT"
            srcDir        = "src"
            packageName   = "{pkg}"

            # Dependencies
            requires "nim >= 2.0.0"

            task test, "Run tests":
              exec "nim c -r tests/test_tripwire.nim"
        """)
        files.append(OutputFile(path=f"{pkg}.nimble", content=nimble))

        # 2. Main package re-export
        main_src = textwrap.dedent(f"""\
            # Primary export module for {pkg}
            import {pkg}/bindings
            export bindings
        """)
        files.append(OutputFile(path=f"src/{pkg}.nim", content=main_src))

        # 3. Generated bindings module
        files.append(OutputFile(path=f"src/{pkg}/bindings.nim", content=bindings_code))

        # 4. nim.cfg compiler flags
        nim_cfg = textwrap.dedent("""\
            --mm:orc
            --threads:on
            --styleCheck:hint
        """)
        files.append(OutputFile(path="nim.cfg", content=nim_cfg))

        # 5. Tests
        if test_type in ("tripwire", "both"):
            stub_lines = []
            for fn in fn_names:
                stub_lines.append(
                    f"    if lib.symAddr(\"{fn}\") == nil:\n      checkpoint \"Entry point '{fn}' missing from native library '{pkg}'\"\n      fail()"
                )
            stubs = "\n".join(stub_lines) if stub_lines else f"    checkpoint \"Verified native library '{pkg}' loads\""

            tripwire = render_block_template(
                f"""\
                import std/[unittest, dynlib]
                import {pkg}

                suite "Tripwire Symbol & ABI Verification":
                  test "verify foreign library entrypoints exist and link":
                    let lib = loadLib("{pkg}")
                    if lib == nil:
                      checkpoint "Native dynamic library '{pkg}' not found in system library path"
                      fail()
                {DEDENT_BLOCK}
            """,
                stubs,
            )
            files.append(OutputFile(path="tests/test_tripwire.nim", content=tripwire))

        if test_type in ("unit", "both"):
            decl_checks = (
                "\n".join(f"    check declared({fn})" for fn in fn_names[:10])
                if fn_names
                else f"    check declared({pkg})"
            )
            unit_test = render_block_template(
                f"""\
                import std/unittest
                import {pkg} except TestResult

                suite "{pkg} Unit Tests":
                  test "module exports expected declarations":
                {DEDENT_BLOCK}
            """,
                decl_checks,
            )
            files.append(OutputFile(path=f"tests/test_{pkg}.nim", content=unit_test))

        if test_type in ("unit", "both"):
            files.extend(build_work_order_files(unit, pkg, "nim"))

        return ProjectLayout(files=files)

    def _write_custom_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        if options.layout in ("wheel", "scikit-build"):
            from headerkit.packaging.nim import generate_nim_wheel_layout

            return generate_nim_wheel_layout(unit, options)
        return self._write_package_layout(unit, options)


def write_nim(header: Header | SourceUnit, *, header_path: str | None = None) -> str:
    """Convenience function to generate Nim bindings from a Header IR."""
    return NimWriter(header_path=header_path).write(header)


from headerkit.writers import register_writer  # noqa: E402

register_writer("nim", NimWriter, description="Nim bindings with C and C++ interop (importc, importcpp)")
