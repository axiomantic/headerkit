"""Generate CFFI cdef strings from headerkit IR declarations.

This module converts headerkit IR (Intermediate Representation) objects into
CFFI-compatible C declaration strings suitable for passing to
``ffibuilder.cdef()``.

The IR types come from ``headerkit.ir`` and represent parsed C headers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Declaration,
    Enum,
    Field,
    Function,
    FunctionPointer,
    Header,
    MacroFunction,
    Parameter,
    Pointer,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.reconcile import (
    MatrixGuardedBlock,
    MultiDimensionalReconciledUnit,
    PlatformGuardedBlock,
    ReconciledUnit,
    condition_to_python,
)
from headerkit.writers.base import (
    BaseWriter,
    WriterOption,
)

# Maps bare tag names to their kind ("struct", "union", "enum"), or "" for
# tag-less typedef declarations (e.g. `typedef struct { ... } name;`).
# Populated by header_to_cffi before emitting declarations.
TagKinds = dict[str, str]

_CFFI_ALLOWED_QUALIFIERS: frozenset[str] = frozenset({"const", "volatile"})


def _filter_cffi_qualifiers(qualifiers: Sequence[str]) -> list[str]:
    """Filter qualifiers to those supported by CFFI's pycparser (`const`, `volatile`)."""
    return [q for q in qualifiers if q in _CFFI_ALLOWED_QUALIFIERS]


def _qualify_ctype_name(name: str, tag_kinds: TagKinds, *, is_elaborated: bool | None = None) -> str:
    """Add or strip struct/union/enum prefix based on `tag_kinds`.

    CFFI's cdef parser (backed by pycparser) requires struct/union/enum
    tags to be prefixed with their kind when declared with a tag, and
    requires bare typedef names (no tag prefix) when declared as a
    tag-less ``typedef struct { ... } name;`` or ``typedef enum { ... } name;``.
    """
    if name.startswith(("struct ", "union ", "enum ")):
        bare = name.split(" ", 1)[1]
        if bare in tag_kinds and tag_kinds[bare] == "":
            return bare
        return name
    kind = tag_kinds.get(name)
    if kind:
        return f"{kind} {name}"
    if is_elaborated is True:
        elab_kind = tag_kinds.get(f"@{name}")
        if elab_kind:
            return f"{elab_kind} {name}"
    return name


def _unwrap_array(t: TypeExpr) -> tuple[TypeExpr, str]:
    """Unwrap one or more nested Array nodes into ``(innermost_element_type, '[d1][d2]...')``."""
    dims: list[str] = []
    curr: TypeExpr = t
    while isinstance(curr, Array):
        size_str = str(curr.size) if curr.size is not None else ""
        dims.append(f"[{size_str}]")
        curr = curr.element_type
    return curr, "".join(dims)


def _unwrap_function_pointer(t: TypeExpr) -> tuple[FunctionPointer, int] | None:
    """Unwrap a FunctionPointer (and any outer Pointer layers) into (fp, star_count)."""
    ptr_count = 0
    curr: TypeExpr = t
    while isinstance(curr, Pointer):
        ptr_count += 1
        curr = curr.pointee
    if isinstance(curr, FunctionPointer):
        stars = ptr_count + (1 if curr.is_pointer else 0)
        return curr, stars
    return None


def type_to_cffi(t: TypeExpr, tag_kinds: TagKinds | None = None) -> str:
    """Convert a type expression to its CFFI string representation."""
    tags = tag_kinds or {}
    if isinstance(t, CType):
        quals = _filter_cffi_qualifiers(t.qualifiers)
        qualified_name = _qualify_ctype_name(t.name, tags, is_elaborated=t.is_elaborated)
        if quals:
            return f"{' '.join(quals)} {qualified_name}"
        return _qualify_ctype_name(str(t), tags, is_elaborated=t.is_elaborated)
    elif isinstance(t, (Pointer, FunctionPointer)):
        unwrapped = _unwrap_function_pointer(t)
        if unwrapped is not None:
            fp, stars = unwrapped
            params = _format_params(fp.parameters, fp.is_variadic, tags)
            ret = type_to_cffi(fp.return_type, tags)
            if stars == 0:
                return f"{ret}({params})"
            return f"{ret}({'*' * stars})({params})"

        # Collect consecutive pointer levels from outermost to innermost
        ptr_quals_outer_to_inner: list[list[str]] = []
        inner: TypeExpr = t
        while isinstance(inner, Pointer):
            ptr_quals_outer_to_inner.append(_filter_cffi_qualifiers(inner.qualifiers))
            inner = inner.pointee
        base = type_to_cffi(inner, tags)
        suffix_parts: list[str] = []
        for quals in reversed(ptr_quals_outer_to_inner):
            if quals:
                if suffix_parts and not suffix_parts[-1].endswith("*"):
                    suffix_parts.append(f" * {' '.join(quals)}")
                else:
                    suffix_parts.append(f"* {' '.join(quals)}")
            else:
                if suffix_parts and not suffix_parts[-1].endswith("*"):
                    suffix_parts.append(" *")
                else:
                    suffix_parts.append("*")
        return f"{base} {''.join(suffix_parts)}"
    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{type_to_cffi(t.element_type, tags)}[{size_str}]"
    else:
        return str(t)


def _format_params(
    parameters: list[Parameter],
    is_variadic: bool,
    tag_kinds: TagKinds | None = None,
) -> str:
    """Format function parameter list as a string."""
    tags = tag_kinds or {}
    if not parameters and not is_variadic:
        return "void"
    parts = []
    for p in parameters:
        type_str = type_to_cffi(p.type, tags)
        if p.name:
            # Arrays: dimensions go after the name
            if isinstance(p.type, Array):
                elem_type, dims_str = _unwrap_array(p.type)
                parts.append(f"{type_to_cffi(elem_type, tags)} {p.name}{dims_str}")
            else:
                unwrapped = _unwrap_function_pointer(p.type)
                if unwrapped is not None:
                    fp, stars = unwrapped
                    fp_params = _format_params(fp.parameters, fp.is_variadic, tags)
                    ret = type_to_cffi(fp.return_type, tags)
                    if stars == 0:
                        parts.append(f"{ret} {p.name}({fp_params})")
                    else:
                        parts.append(f"{ret} ({'*' * stars}{p.name})({fp_params})")
                else:
                    parts.append(f"{type_str} {p.name}")
        else:
            parts.append(type_str)
    if is_variadic:
        parts.append("...")
    return ", ".join(parts)


def _format_field(f: Field, tag_kinds: TagKinds | None = None) -> str:
    """Format a struct/union field declaration."""
    tags = tag_kinds or {}

    if f.anonymous_struct is not None:
        return _format_anonymous_struct_field(f.anonymous_struct, tags)

    bit_suffix = f" : {f.bit_width}" if f.bit_width is not None else ""

    if isinstance(f.type, Array):
        elem_type, dims_str = _unwrap_array(f.type)
        return f"    {type_to_cffi(elem_type, tags)} {f.name}{dims_str}{bit_suffix};"

    unwrapped = _unwrap_function_pointer(f.type)
    if unwrapped is not None:
        fp, stars = unwrapped
        fp_params = _format_params(fp.parameters, fp.is_variadic, tags)
        ret = type_to_cffi(fp.return_type, tags)
        if stars == 0:
            return f"    {ret} {f.name}({fp_params});"
        return f"    {ret} ({'*' * stars}{f.name})({fp_params});"

    return f"    {type_to_cffi(f.type, tags)} {f.name}{bit_suffix};"


def _format_anonymous_struct_field(anon: Struct, tag_kinds: TagKinds | None = None) -> str:
    """Format an anonymous struct/union as an inline field block."""
    tags = tag_kinds or {}
    kind = "union" if anon.is_union else "struct"
    lines = [f"    {kind} {{"]
    for inner_f in anon.fields:
        inner_line = _format_field(inner_f, tags)
        lines.append(f"    {inner_line}")
    lines.append("    };")
    return "\n".join(lines)


def decl_to_cffi(
    decl: Declaration,
    exclude_patterns: Sequence[re.Pattern[str]] | None = None,
    tag_kinds: TagKinds | None = None,
) -> str | None:
    """Convert a single IR declaration to a CFFI cdef string.

    Returns None if the declaration should be excluded.
    """
    # Check exclusion patterns against declaration name
    if exclude_patterns:
        name = getattr(decl, "name", None)
        if name:
            for pat in exclude_patterns:
                if pat.search(name):
                    return None

    if isinstance(decl, Struct):
        return _struct_to_cffi(decl, tag_kinds)
    elif isinstance(decl, Enum):
        return _enum_to_cffi(decl)
    elif isinstance(decl, Function):
        return _function_to_cffi(decl, exclude_patterns, tag_kinds)
    elif isinstance(decl, Typedef):
        return _typedef_to_cffi(decl, tag_kinds)
    elif isinstance(decl, Constant):
        return _constant_to_cffi(decl)
    elif isinstance(decl, Variable):
        return _variable_to_cffi(decl, tag_kinds)
    elif isinstance(decl, MacroFunction):
        return _macro_function_to_cffi(decl)
    else:
        return None


def _macro_function_to_cffi(decl: MacroFunction) -> str | None:
    """Convert a MacroFunction to CFFI cdef comment stub.

    Function-like preprocessor macros do not possess C ABI symbols in compiled
    libraries and cannot be safely represented as function prototypes without
    knowing parameter and return types. Emit a descriptive comment stub.
    """
    params_str = ", ".join(str(p) for p in decl.parameters)
    sig = f"{decl.name}({params_str})"
    return f"/* Macro function '{sig}' skipped (preprocessor macro) */"


def _type_references_name(t: TypeExpr, target: str) -> bool:
    """Return True if *t* references *target* or ``struct/union/enum <target>``."""
    if isinstance(t, CType):
        bare = t.name.split(" ", 1)[1] if t.name.startswith(("struct ", "union ", "enum ")) else t.name
        return bare == target
    if isinstance(t, Pointer):
        return _type_references_name(t.pointee, target)
    if isinstance(t, Array):
        return _type_references_name(t.element_type, target)
    if isinstance(t, FunctionPointer):
        return _type_references_name(t.return_type, target) or any(
            _type_references_name(p.type, target) for p in t.parameters
        )
    return False


def _struct_is_self_referential(decl: Struct) -> bool:
    """Return True if any field of *decl* references *decl.name*."""
    if not decl.name:
        return False
    for f in decl.fields:
        if f.anonymous_struct is not None:
            if any(_type_references_name(inner.type, decl.name) for inner in f.anonymous_struct.fields):
                return True
        elif _type_references_name(f.type, decl.name):
            return True
    return False


def _struct_to_cffi(decl: Struct, tag_kinds: TagKinds | None = None) -> str | None:
    """Convert a Struct/Union IR node to CFFI cdef."""
    if decl.name is None or _is_anonymous_name(decl.name):
        return None

    kind = "union" if decl.is_union else "struct"

    if not decl.fields:
        # Opaque type
        if decl.is_typedef:
            return f"typedef {kind} {decl.name} {decl.name};"
        else:
            return f"{kind} {decl.name} {{ ...; }};"

    lines = []
    if decl.is_packed:
        # cffi has no in-source spelling for packing: its cdef parser rejects
        # ``__attribute__((packed))`` in either position, and the only
        # mechanism it offers is ``ffi.cdef(..., packed=True)``, which applies
        # to every record in the call rather than to one. Measured with cffi:
        # this record is 6 bytes in C and cdef lays it out at 12 without that
        # argument. The comment therefore has to say what the reader must do,
        # because nothing else will fail -- the wrong layout is silent.
        lines.append("/* HEADERKIT: packed record -- pass packed=True to ffi.cdef() for this")
        lines.append("   declaration, or cffi will lay it out with padding the header does not")
        lines.append("   have. packed=True applies to the whole cdef() call, so a header mixing")
        lines.append("   packed and unpacked records needs them split across separate calls. */")
    self_ref = _struct_is_self_referential(decl)
    if decl.is_typedef:
        if self_ref:
            lines.append(f"typedef {kind} {decl.name} {{")
        else:
            lines.append(f"typedef {kind} {{")
    else:
        lines.append(f"{kind} {decl.name} {{")

    field_tags = dict(tag_kinds) if tag_kinds else {}
    if self_ref and decl.name:
        field_tags[decl.name] = kind

    for f in decl.fields:
        lines.append(_format_field(f, field_tags))

    if decl.is_typedef:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return "\n".join(lines)


def _is_anonymous_name(name: str | None) -> bool:
    """Check if a name is a synthesized anonymous name from libclang."""
    if name is None:
        return True
    # libclang synthesizes names like "(unnamed at /path/to/file.h:123:1)"
    return "(unnamed" in name or "(anonymous" in name


def _enum_to_cffi(decl: Enum) -> str | None:
    """Convert an Enum IR node to CFFI cdef."""
    if not decl.values:
        return None

    is_anon = _is_anonymous_name(decl.name)

    lines = []
    if decl.is_typedef and not is_anon:
        # Tag-less `typedef enum { ... } Name;` -- emitting `enum Name` here
        # would invent a tag the real header does not declare.
        lines.append("typedef enum {")
    elif not is_anon:
        lines.append(f"enum {decl.name} {{")
    else:
        lines.append("enum {")

    value_lines = []
    for v in decl.values:
        if v.value is not None:
            value_lines.append(f"    {v.name} = {v.value},")
        else:
            value_lines.append(f"    {v.name},")

    lines.extend(value_lines)

    if decl.is_typedef and not is_anon:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return "\n".join(lines)


def _function_to_cffi(
    decl: Function,
    exclude_patterns: Sequence[re.Pattern[str]] | None = None,
    tag_kinds: TagKinds | None = None,
) -> str | None:
    """Convert a Function IR node to CFFI cdef."""
    if exclude_patterns:
        for pat in exclude_patterns:
            if pat.search(decl.name):
                return None

    params = _format_params(decl.parameters, decl.is_variadic, tag_kinds)
    return f"{type_to_cffi(decl.return_type, tag_kinds)} {decl.name}({params});"


def _typedef_to_cffi(decl: Typedef, tag_kinds: TagKinds | None = None) -> str | None:
    """Convert a Typedef IR node to CFFI cdef."""
    underlying = decl.underlying_type

    unwrapped = _unwrap_function_pointer(underlying)
    if unwrapped is not None:
        fp, stars = unwrapped
        fp_params = _format_params(fp.parameters, fp.is_variadic, tag_kinds)
        ret = type_to_cffi(fp.return_type, tag_kinds)
        if stars == 0:
            return f"typedef {ret} {decl.name}({fp_params});"
        return f"typedef {ret} ({'*' * stars}{decl.name})({fp_params});"

    # Array typedef: typedef int name[10]
    if isinstance(underlying, Array):
        elem_type, dims_str = _unwrap_array(underlying)
        return f"typedef {type_to_cffi(elem_type, tag_kinds)} {decl.name}{dims_str};"

    # Regular typedef
    if isinstance(underlying, CType) and underlying.name == decl.name and tag_kinds:
        elab_kind = tag_kinds.get(f"@{decl.name}")
        if elab_kind:
            quals = _filter_cffi_qualifiers(underlying.qualifiers)
            qual_prefix = f"{' '.join(quals)} " if quals else ""
            return f"typedef {qual_prefix}{elab_kind} {decl.name} {decl.name};"
    return f"typedef {type_to_cffi(underlying, tag_kinds)} {decl.name};"


def _constant_to_cffi(decl: Constant) -> str | None:
    """Convert a Constant IR node to CFFI cdef.

    CFFI's #define only supports integer constants or '...' (resolved at
    compile time as an integer). String macros, expression macros, and
    macros with unknown values are skipped entirely since CFFI cannot
    handle non-integer constant macros.
    """
    if decl.value is not None and isinstance(decl.value, int) and not isinstance(decl.value, bool):
        return f"#define {decl.name} {decl.value}"
    return None


def _variable_to_cffi(decl: Variable, tag_kinds: TagKinds | None = None) -> str | None:
    """Convert a Variable IR node to CFFI cdef."""
    if isinstance(decl.type, Array):
        elem_type, dims_str = _unwrap_array(decl.type)
        return f"{type_to_cffi(elem_type, tag_kinds)} {decl.name}{dims_str};"
    return f"{type_to_cffi(decl.type, tag_kinds)} {decl.name};"


def _build_tag_kinds(declarations: list[Declaration]) -> TagKinds:
    """Build a mapping of struct/union/enum tag names to their kind.

    This is needed because the libclang backend may emit typedefs that
    reference struct tags by bare name (without the struct/union/enum prefix).
    CFFI's pycparser-based cdef parser requires the prefix.

    We only add the tag prefix when the tag name differs from any typedef name.
    When a name is a tag-less typedef (e.g., ``typedef enum { ... } nng_pipe_ev;``
    or ``typedef struct { ... } nng_init_params;`` where ``is_typedef=True``),
    the real C header has no tag, so we record ``""`` in ``tag_kinds`` to strip
    any synthesized ``struct ``/``union ``/``enum `` prefix.
    """
    tags: TagKinds = {}
    tagless_typedefs: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Struct) and decl.name and not _is_anonymous_name(decl.name):
            if decl.is_typedef:
                tagless_typedefs.add(decl.name)
            else:
                kind = "union" if decl.is_union else "struct"
                tags[decl.name] = kind
        elif isinstance(decl, Enum) and decl.name and not _is_anonymous_name(decl.name):
            if decl.is_typedef:
                tagless_typedefs.add(decl.name)
            else:
                tags[decl.name] = "enum"

    typedef_names: set[str] = set()
    for decl in declarations:
        if decl.name and (isinstance(decl, Typedef) or (isinstance(decl, (Enum, Struct)) and decl.is_typedef)):
            typedef_names.add(decl.name)

    tag_kinds: TagKinds = {}
    for name, kind in tags.items():
        if name not in typedef_names:
            tag_kinds[name] = kind
        else:
            tag_kinds[f"@{name}"] = kind
    for name in tagless_typedefs:
        if name not in tags:
            tag_kinds[name] = ""
    return tag_kinds


def _find_opaque_typedef_structs(declarations: list[Declaration]) -> set[str]:
    """Find opaque struct names that also have matching typedefs.

    For opaque structs (no fields) with a same-name typedef, we should only
    emit the typedef to avoid CFFI trying to compute the struct's size.
    """
    opaque_structs: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Struct) and decl.name and not decl.fields and not decl.is_typedef:
            if not _is_anonymous_name(decl.name):
                opaque_structs.add(decl.name)

    typedef_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            typedef_names.add(decl.name)

    return opaque_structs & typedef_names


def _find_typedef_enum_pairs(declarations: list[Declaration]) -> set[str]:
    """Find tag-less enum names that also appear as typedefs."""
    enum_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Enum) and decl.name and not _is_anonymous_name(decl.name):
            if decl.is_typedef:
                enum_names.add(decl.name)

    typedef_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            typedef_names.add(decl.name)

    return enum_names & typedef_names


def _find_typedef_struct_pairs(declarations: list[Declaration]) -> set[str]:
    """Find tag-less typedef struct/union names that also have redundant Typedef nodes."""
    struct_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Struct) and decl.name and not _is_anonymous_name(decl.name):
            if decl.is_typedef and decl.fields:
                struct_names.add(decl.name)

    typedef_names: set[str] = set()
    for decl in declarations:
        if isinstance(decl, Typedef):
            typedef_names.add(decl.name)

    return struct_names & typedef_names


def header_to_cffi(
    header: Header,
    exclude_patterns: list[str] | None = None,
) -> str:
    """Convert all declarations in a Header to a CFFI cdef string.

    :param header: Parsed header IR from headerkit.
    :param exclude_patterns: List of regex patterns. Declarations with names
        matching any pattern will be excluded.
    :returns: A string suitable for passing to ``ffibuilder.cdef()``.
    """
    compiled_patterns = None
    if exclude_patterns:
        compiled_patterns = [re.compile(p) for p in exclude_patterns]

    tag_kinds = _build_tag_kinds(header.declarations)
    combined_enum_pairs = _find_typedef_enum_pairs(header.declarations)
    combined_struct_pairs = _find_typedef_struct_pairs(header.declarations)
    opaque_typedef_structs = _find_opaque_typedef_structs(header.declarations)

    lines = []
    for decl in header.declarations:
        if isinstance(decl, Enum) and decl.name in combined_enum_pairs:
            result = _enum_to_cffi_as_typedef(decl)
            if result is not None:
                lines.append(result)
            continue

        if isinstance(decl, Typedef) and (decl.name in combined_enum_pairs or decl.name in combined_struct_pairs):
            continue

        if isinstance(decl, Struct) and decl.name in opaque_typedef_structs:
            continue

        result = decl_to_cffi(decl, compiled_patterns, tag_kinds)
        if result is not None:
            lines.append(result)

    return "\n".join(lines)


def _enum_to_cffi_as_typedef(decl: Enum) -> str | None:
    """Emit an enum as ``typedef enum { ... } name;`` (no tag name).

    Used when the original header had an anonymous enum with a typedef,
    but libclang assigned the typedef name to the enum tag.
    """
    if not decl.values:
        return None

    lines = ["typedef enum {"]

    for v in decl.values:
        if v.value is not None:
            lines.append(f"    {v.name} = {v.value},")
        else:
            lines.append(f"    {v.name},")

    lines.append(f"}} {decl.name};")
    return "\n".join(lines)


def _format_extern_python_entry(
    entry: str,
    declarations: Sequence[Declaration],
    tag_kinds: TagKinds,
) -> str:
    """Format a single `extern_python` entry into an `extern "Python" ...;` line."""
    stripped = entry.strip()
    if stripped.startswith('extern "Python"'):
        return stripped if stripped.endswith(";") else f"{stripped};"
    if "(" in stripped:
        proto = stripped if stripped.endswith(";") else f"{stripped};"
        return f'extern "Python" {proto}'
    for decl in declarations:
        if isinstance(decl, Function) and decl.name == stripped:
            params = _format_params(decl.parameters, decl.is_variadic, tag_kinds)
            ret = type_to_cffi(decl.return_type, tag_kinds)
            return f'extern "Python" {ret} {decl.name}({params});'
        if isinstance(decl, Typedef) and decl.name == stripped:
            unwrapped = _unwrap_function_pointer(decl.underlying_type)
            if unwrapped is not None:
                fp, _ = unwrapped
                params = _format_params(fp.parameters, fp.is_variadic, tag_kinds)
                ret = type_to_cffi(fp.return_type, tag_kinds)
                return f'extern "Python" {ret} {decl.name}({params});'
    return f'extern "Python" void {stripped}(void);'


class CffiWriter(BaseWriter):
    """Writer that generates CFFI cdef strings from headerkit IR.

    Options
    -------
    exclude_patterns : list[str] | None
        Regex patterns. Declarations with names matching any pattern
        are excluded from output.
    define_patterns : Sequence[str] | None
        Regex patterns matched against ``Constant`` declaration names in the IR.
        Matching macro constants not already emitted as integer literals are
        appended as ``#define NAME ...`` lines (CFFI's verify-time syntax).
    extra_cdef : list[str] | None
        Arbitrary cdef lines appended verbatim after all generated output.
    module_var : str | None
        When set (e.g. ``"CDEF"``), wraps the output as a valid Python module
        exporting ``<module_var>: str = ...`` and ``__all__ = [<module_var>]``.
    extern_python : Sequence[str] | None
        C function prototypes, ``extern "Python"`` lines, or declaration names
        to append as ``extern "Python"`` callback declarations.
    """

    name: str = "cffi"
    format_description: str = "CFFI cdef declarations for ffibuilder.cdef()"
    default_output_pattern: str = "{dir}/{stem}.cdef.txt"
    default_extension: str = ".py"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="exclude_patterns",
            description="Patterns of symbols to exclude from cdef",
            default=None,
            type=list,
        ),
        WriterOption(
            name="define_patterns",
            description="Patterns of preprocessor macros to include in cdef",
            default=None,
            type=list,
        ),
        WriterOption(
            name="extra_cdef",
            description="Additional custom cdef strings to append",
            default=None,
            type=list,
        ),
        WriterOption(
            name="module_var",
            description="Wrap output as a Python module exporting this variable name",
            default=None,
            type=str,
        ),
        WriterOption(
            name="extern_python",
            description='Function prototypes or names to emit as extern "Python" callbacks',
            default=None,
            type=list,
        ),
    )

    def __init__(
        self,
        exclude_patterns: list[str] | None = None,
        define_patterns: Sequence[str] | None = None,
        extra_cdef: list[str] | None = None,
        module_var: str | None = None,
        extern_python: Sequence[str] | None = None,
    ) -> None:
        self._exclude_patterns = exclude_patterns
        self.define_patterns = list(define_patterns) if define_patterns is not None else None
        self.extra_cdef = extra_cdef
        self.module_var = module_var
        self.extern_python = list(extern_python) if extern_python is not None else None
        self._matched_defines: list[str] | None = None

    def _collect_matched_defines(self, header: Header) -> list[str]:
        """Collect macro constant names matching `define_patterns`, deduplicated against emitted ints."""
        exclude_pats = [re.compile(p) for p in self._exclude_patterns] if self._exclude_patterns else []
        emitted_ints: set[str] = set()
        for decl in header.declarations:
            if (
                isinstance(decl, Constant)
                and decl.value is not None
                and isinstance(decl.value, int)
                and not isinstance(decl.value, bool)
            ):
                if not exclude_pats or not any(p.search(decl.name) for p in exclude_pats):
                    emitted_ints.add(decl.name)

        matched: list[str] = []
        seen: set[str] = set(emitted_ints)

        if self._matched_defines:
            for name in self._matched_defines:
                if name not in seen and (not exclude_pats or not any(p.search(name) for p in exclude_pats)):
                    seen.add(name)
                    matched.append(name)

        if self.define_patterns:
            define_pats = [re.compile(p) for p in self.define_patterns]
            for decl in header.declarations:
                if isinstance(decl, Constant) and decl.name not in seen:
                    if exclude_pats and any(p.search(decl.name) for p in exclude_pats):
                        continue
                    if any(p.search(decl.name) for p in define_pats):
                        seen.add(decl.name)
                        matched.append(decl.name)

        return matched

    def _render(self, unit: SourceUnit | Header) -> str:
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        output = header_to_cffi(header, exclude_patterns=self._exclude_patterns)

        matched_defines = self._collect_matched_defines(header)
        if matched_defines:
            define_lines = "\n".join(f"#define {name} ..." for name in matched_defines)
            output = output + "\n" + define_lines if output else define_lines

        if self.extern_python:
            tag_kinds = _build_tag_kinds(header.declarations)
            extern_lines = "\n".join(
                _format_extern_python_entry(entry, header.declarations, tag_kinds) for entry in self.extern_python
            )
            output = output + "\n" + extern_lines if output else extern_lines

        if self.extra_cdef:
            extra = "\n".join(self.extra_cdef)
            output = output + "\n" + extra if output else extra

        return output

    def _wrap_module_var(self, cdef_str: str, source_path: str | None) -> str:
        source_name = source_path or "header.h"
        lines = [
            f'"""Auto-generated CFFI cdef declarations by headerkit from {source_name}."""',
            "",
            "from __future__ import annotations",
            "",
            f"{self.module_var}: str = {cdef_str!r}",
            "",
            f"__all__ = [{self.module_var!r}]",
            "",
        ]
        return "\n".join(lines)

    def write(self, header: Any) -> str:
        """Convert header IR to CFFI cdef string, Python module, or reconciled script."""
        if isinstance(header, (ReconciledUnit, MultiDimensionalReconciledUnit)):
            return self.write_reconciled(header)
        prepared = self._prepare(header)
        rendered = self._render(prepared)
        if self.module_var:
            return self._wrap_module_var(rendered, getattr(prepared, "path", None))
        return rendered

    def write_reconciled(
        self,
        unit: ReconciledUnit | MultiDimensionalReconciledUnit,
    ) -> str:
        """Render a ReconciledUnit containing common declarations and platform-guarded cdef blocks."""
        header_path = unit.path or "header.h"
        sections: list[str] = []
        decl_accumulator: list[Declaration] = []

        def flush_accumulator() -> None:
            if decl_accumulator:
                rendered = self._render(Header(path=header_path, declarations=decl_accumulator)).strip()
                if rendered:
                    cdef_lines = ['ffi.cdef("""']
                    cdef_lines.extend(rendered.splitlines())
                    cdef_lines.append('""")')
                    sections.append("\n".join(cdef_lines))
                decl_accumulator.clear()

        domain_matrix: dict[str, set[Any]] = defaultdict(set)
        for item in unit.items:
            if isinstance(item, MatrixGuardedBlock):
                for m_cond, _ in item.branches:
                    if hasattr(m_cond, "raw_conditions"):
                        for rc in m_cond.raw_conditions:
                            for k, v in rc.items():
                                domain_matrix[k].add(v)

        for item in unit.items:
            if isinstance(item, (PlatformGuardedBlock, MatrixGuardedBlock)):
                flush_accumulator()
                block_lines: list[str] = []
                branches: Sequence[tuple[Any, list[Declaration]]] = item.branches
                for idx, (b_cond, b_decls) in enumerate(branches):
                    py_cond = condition_to_python(b_cond, domain_matrix)
                    kw = "if" if idx == 0 else "elif"
                    block_lines.append(f"{kw} {py_cond}:")
                    rendered_b = self._render(Header(path=header_path, declarations=b_decls)).strip()
                    if not rendered_b:
                        block_lines.append("    pass")
                    else:
                        block_lines.append('    ffi.cdef("""')
                        for line in rendered_b.splitlines():
                            block_lines.append(f"    {line}")
                        block_lines.append('    """)')
                block_lines.append("else:")
                block_lines.append('    raise OSError(f"Unsupported platform: {sys.platform}")')
                sections.append("\n".join(block_lines))
            else:
                decl_accumulator.append(item)

        flush_accumulator()

        output_lines: list[str] = [
            '"""CFFI bindings generated by headerkit."""',
            "",
            "from __future__ import annotations",
            "",
            "import sys",
            "from cffi import FFI",
            "",
            "ffi = FFI()",
            "",
        ]

        if sections:
            output_lines.append("\n\n".join(sections))

        return "\n".join(output_lines).rstrip() + "\n"

    def hash_comment_format(self) -> str:
        """Return format string for wrapping cache metadata in comments."""
        if self.module_var:
            return "# {line}"
        return "/* {line} */"


# Uses bottom-of-module self-registration. Unlike backends (which import
# register_backend at the top and conditionally register at the bottom),
# writers have no external dependencies so import and registration are
# co-located.
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "cffi",
    CffiWriter,
    is_default=True,
    description="CFFI cdef declarations for ffibuilder.cdef()",
)
