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
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any, ClassVar

from headerkit._rename import (
    MODULE_SCOPE_KINDS,
    RenameError,
    Symbol,
    dispatch_rename,
    enforce_injectivity,
)
from headerkit.hooks import HookRegistry, PipelineContext, Priority
from headerkit.ir import (
    Array,
    BaseSpecifier,
    Constant,
    CType,
    Declaration,
    Enum,
    Function,
    FunctionPointer,
    Header,
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
from headerkit.reconcile import (
    MatrixGuardedBlock,
    MultiDimensionalReconciledUnit,
    PlatformGuardedBlock,
    ReconciledUnit,
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
    "CppString",
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
        raise RenameError("the empty string is not a Nim identifier")
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


def _template_param_ident(name: str) -> str:
    """Escape a template parameter and hold the result to Nim's grammar.

    A generic parameter is scoped to the declaration that introduces it, so it is
    not part of the module-scope injectivity set -- but it is still a token the
    Nim lexer has to accept, and ``_escape_ident`` alone has never proved that.
    """
    out = _escape_ident(name)
    validate_nim_ident(out)
    return out


def nim_ident_identity(name: str) -> str:
    """Return Nim's identity spelling for *name* -- what the compiler compares.

    Nim considers two identifiers the same when their first characters match
    exactly and the rest match after removing underscores and folding case. So
    ``foo_bar`` and ``fooBar`` are one identifier and redefining across them is a
    compile error, while ``Foo_bar`` and ``fooBar`` are two.

    Equality on the emitted spelling is therefore the wrong test for a name
    collision, and equality on the *source* spelling is the wrong test twice
    over. This is the function every collision check in this writer runs under.
    """
    bare = name.strip("`")
    if not bare:
        return bare
    return bare[0] + bare[1:].replace("_", "").lower()


#: The characters a Nim operator may be built from -- the Nim manual's ``OPR``
#: production. Backtick-quoted content is accepted when it is a sequence of these
#: runs and identifier runs, which covers every operator the writer emits
#: (``+``, ``==``, ``shl=``) and every keyword-as-identifier (``\`type\```).
NIM_OPERATOR_CHARS: frozenset[str] = frozenset("=+-*/<>@$~&%|!?^.:\\")

#: The bracket operators, which Nim spells with characters ``OPR`` does not
#: contain. They are an enumerated set rather than an admitted character class
#: because ``}`` is how a pragma ends: admitting it would leave the floor open to
#: exactly the payload this validator exists to refuse.
NIM_BRACKET_OPERATORS: frozenset[str] = frozenset({"[]", "[]=", "{}", "{}="})


def _validate_bare_nim_ident(name: str, bare: str) -> None:
    """Raise unless *bare* satisfies Nim's ordinary identifier grammar.

    ``name`` is the spelling to quote in the message, which differs from ``bare``
    when the caller stripped a pair of backticks off it.

    Composed characters are compared in NFC. Nim accepts ``café`` written either
    way, and the decomposed form puts a combining acute -- a mark, not a letter or
    a digit -- in the name, so the character check refused a name the compiler
    would have taken. Which normalisation a header arrives in is a property of
    the editor that wrote it, and a floor that rejects on it rejects the wrong
    thing.
    """
    bare = unicodedata.normalize("NFC", bare)
    if bare.startswith("_") or bare.endswith("_"):
        raise RenameError(f"{name!r} is not a legal Nim identifier: it may not begin or end with an underscore")
    if not bare[0].isalpha():
        raise RenameError(f"{name!r} is not a legal Nim identifier: it must start with a letter")
    if "__" in bare:
        raise RenameError(f"{name!r} is not a legal Nim identifier: it may not contain consecutive underscores")
    if not all(ch.isalnum() or ch == "_" for ch in bare):
        raise RenameError(f"{name!r} is not a legal Nim identifier: only letters, digits and underscores are allowed")


def _validate_quoted_nim_ident(name: str, bare: str) -> None:
    """Raise unless *bare* is legal between a pair of backticks.

    Nim's own accent-quoted grammar is far wider than this -- it concatenates the
    tokens between the quotes, so it accepts ``\\`foo bar\\``` and ``\\`a.}b\\```
    as identifiers. This is deliberately narrower: a **floor**, admitting the
    keyword, operator and identifier spellings a binding actually needs and
    nothing else. The characters it refuses are the ones that end a pragma, close
    a string literal or a line, which is the whole of the injection this exists to
    stop; a name outside the floor is refused rather than emitted, which is the
    conservative direction for a generator.
    """
    tokens: list[str] = []
    current = ""
    kind: str | None = None
    for ch in bare:
        if ch.isalnum() or ch == "_":
            ch_kind = "ident"
        elif ch in NIM_OPERATOR_CHARS:
            ch_kind = "operator"
        else:
            raise RenameError(
                f"{name!r} is not a legal Nim identifier: {ch!r} may not appear between backticks. "
                "A backtick-quoted name may spell a keyword, an operator or an identifier, and "
                "nothing that could end the pragma or the line it is emitted into."
            )
        if ch_kind != kind:
            if current:
                tokens.append(current)
            current, kind = ch, ch_kind
        else:
            current += ch
    if current:
        tokens.append(current)
    if bare in {".", ":", "="} or ":" in bare:
        raise RenameError(
            f"{name!r} is not a legal Nim identifier: {bare!r} may not appear between backticks. "
            f"{bare!r} is rejected by the Nim lexer."
        )
    for token in tokens:
        if token[0] in NIM_OPERATOR_CHARS or token in NIM_KEYWORDS:
            continue
        _validate_bare_nim_ident(name, token)


def validate_nim_ident(name: str) -> None:
    """Raise unless *name* is something the Nim lexer will accept.

    A backtick-quoted name is how Nim spells a keyword or an operator as an
    identifier -- ``\\`type\\```, ``\\`+\\```. The quotes suspend the *identifier*
    grammar; they do not suspend the grammar altogether, and the content between
    them is still checked here. Returning early on any backticked string is what
    made this floor bypassable: a ``rename_symbol`` hook at ``PROJECT`` -- which
    is exactly what a ``[rename]`` section of ``.headerkit.toml`` registers --
    could answer with a backticked payload carrying ``".}`` and a newline and
    have arbitrary Nim emitted into the generated module, under a validator whose
    whole purpose is that no configuration can reach past it.

    :raises RenameError: if *name* is not a legal Nim identifier.
    """
    if not name:
        raise RenameError("the empty string is not a Nim identifier")
    if name.startswith("`") and name.endswith("`") and len(name) >= 2:
        bare = name[1:-1]
        if not bare:
            raise RenameError(f"{name!r} is not a Nim identifier: the backticks quote nothing")
        if bare in NIM_BRACKET_OPERATORS:
            return
        _validate_quoted_nim_ident(name, bare)
        return
    if "`" in name:
        raise RenameError(
            f"{name!r} is not a legal Nim identifier: a backtick may only appear as the pair quoting the whole name"
        )
    if name in NIM_KEYWORDS:
        raise RenameError(f"{name!r} is a Nim keyword; it must be quoted with backticks to be used as an identifier")
    _validate_bare_nim_ident(name, name)


def nim_legality_renamer(name: str, *, context: PipelineContext, kind: str, **_: object) -> str:  # noqa: ARG001
    """The ``rename_symbol`` implementation that enforces Nim's own grammar.

    Registered at :attr:`~headerkit.hooks.Priority.FALLBACK`, which is the whole
    point of the tier choice: the waterfall runs highest priority first, so every
    project renamer at ``PROJECT`` has already had the name by the time this sees
    it. Whatever a project renames a symbol to must still survive Nim's lexer,
    and no configuration can bypass that floor.
    """
    return _escape_ident(name)


HookRegistry.register_global(
    "rename_symbol",
    nim_legality_renamer,
    priority=Priority.FALLBACK,
    writer="nim",
)


#: The Nim declaration, and any companion procs, for each C++ helper type
#: :meth:`NimWriter._format_type` can produce. The renderer records which helpers it
#: emitted and this table turns that record into declarations, so the set declared is
#: the set referenced by construction rather than by a second predicate that has to
#: agree with the first.
CPP_HELPER_DECLARATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "CppString": ('CppString* {.importcpp: "std::string", header: "<string>".} = object', ()),
    "CppVector": ('CppVector*[T] {.importcpp: "std::vector<\'0>", header: "<vector>".} = object', ()),
    "UniquePtr": (
        'UniquePtr*[T] {.importcpp: "std::unique_ptr<\'0>", header: "<memory>".} = object',
        (
            'proc `=copy`*[T](dst: var UniquePtr[T], src: UniquePtr[T]) {.error: "std::unique_ptr cannot be copied in Nim; use std/moves.move() or sink".}',
            'proc move*[T](p: var UniquePtr[T]): UniquePtr[T] {.importcpp: "std::move(@)", header: "<utility>".}',
            'proc get*[T](p: UniquePtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
            'proc reset*[T](p: var UniquePtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
        ),
    ),
    "SharedPtr": (
        'SharedPtr*[T] {.importcpp: "std::shared_ptr<\'0>", header: "<memory>".} = object',
        (
            'proc get*[T](p: SharedPtr[T]): ptr T {.importcpp: "#.get()", header: "<memory>".}',
            'proc reset*[T](p: var SharedPtr[T]) {.importcpp: "#.reset()", header: "<memory>".}',
            'proc useCount*[T](p: SharedPtr[T]): clong {.importcpp: "#.use_count()", header: "<memory>".}',
        ),
    ),
    "WeakPtr": (
        'WeakPtr*[T] {.importcpp: "std::weak_ptr<\'0>", header: "<memory>".} = object',
        ('proc lock*[T](p: WeakPtr[T]): SharedPtr[T] {.importcpp: "#.lock()", header: "<memory>".}',),
    ),
}

#: Helpers whose companion procs name another helper, which must therefore be
#: declared alongside them.
_CPP_HELPER_REQUIRES: dict[str, tuple[str, ...]] = {"WeakPtr": ("SharedPtr",)}

#: Tag keywords that mark a name as a C record or enumeration rather than a C++ one.
_C_TAG_PREFIXES: tuple[str, ...] = ("struct ", "union ", "enum ")

#: The module-scope kinds a *type reference* can denote. A use of a type carries
#: no kind -- ``status x;`` says nothing about whether ``status`` is a struct, an
#: enum or a typedef -- so a reference is resolved against these and only these.
_TYPE_KINDS: frozenset[str] = frozenset({"struct", "union", "enum", "typedef"})

#: The identifier of an anonymous record, invented by this writer rather than
#: renamed from the header. Every site that needs to name such a record uses
#: this constant so the declaration and every reference agree by construction.
ANONYMOUS_RECORD_NAME: str = "AnonObject"

# =============================================================================
# Identity
#
# Module-scope declarations and type references are resolved using the complete
# set of identity axes recorded in the IR.
# =============================================================================

#: Every axis a *module-scope declaration* is identified by. Declarations that
#: agree on all of them are one entity spelled twice -- an opaque record and its
#: definition, a forward declaration and the class. Declarations differing on any
#: of them are distinct symbols competing for one Nim identifier, and the naming
#: pass must see every one of them.
#:
#: The axes are named here and read by :func:`_decl_identity` only. Adding one is
#: an entry here and a component there; tests pin the two against each other so
#: they cannot drift.
DECL_IDENTITY_AXES: tuple[str, ...] = ("name", "kind", "namespace")

#: The identity of one module-scope declaration, in :data:`DECL_IDENTITY_AXES` order.
DeclIdentity = tuple[str, str, str | None]


def _decl_identity(name: str, kind: str, namespace: str | None) -> DeclIdentity:
    """Build the identity tuple for a module-scope declaration.

    An empty namespace and a missing one are the same fact -- global scope -- and
    are normalised to ``None`` so two spellings of it cannot key two entries.
    """
    return (name, kind, namespace or None)


def _decl_namespace(decl: object) -> str | None:
    """The enclosing C++ namespace a declaration records, or ``None``.

    ``Constant`` carries none at all, which is not an oversight in the IR: a
    macro has no C++ scope to be in.
    """
    return getattr(decl, "namespace", None) or None


#: Every axis a *type reference* is resolved by. A reference names no kind and no
#: scope, so the answer is assembled from the type expression itself plus the
#: facts the unit records around it.
TYPE_REFERENCE_AXES: tuple[str, ...] = (
    "name",
    "qualifiers",
    "is_elaborated",
    "unit language",
    "declared in this unit",
)


def _typedef_aliases_its_own_tag(t: Typedef) -> bool:
    """Whether ``t`` is the ``typedef struct foo foo;`` idiom: one entity, two kinds.

    The comparison is against the typedef's *own* underlying type, so a typedef
    that merely shares a name with an unrelated tag -- ``enum status {...};``
    beside ``typedef int status;``, two distinct entities in two C namespaces --
    is not mistaken for it.
    """
    if not t.name or not isinstance(t.underlying_type, CType):
        return False
    raw = t.underlying_type.name
    for tag in _C_TAG_PREFIXES:
        raw = raw.removeprefix(tag)
    return raw.strip() == t.name


#: Qualifiers that describe *mutability* rather than the type itself. They never
#: form part of a primitive's spelling, so they are dropped before the lookup.
_CV_QUALIFIERS: frozenset[str] = frozenset({"const", "volatile", "restrict"})


def _qualified_type_name(name: str, qualifiers: Sequence[str]) -> str:
    """Re-join type-level qualifiers with ``name``, dropping cv-qualifiers."""
    type_level = [q for q in qualifiers if q not in _CV_QUALIFIERS]
    return " ".join([*type_level, name]) if type_level else name


def _nim_string_path(path: str) -> str:
    """Render ``path`` so it survives inside a Nim string literal.

    Normalised with PureWindowsPath rather than PurePath so the result depends on
    the path rather than on the host that generated it.
    """
    return PureWindowsPath(path).as_posix()


def _cfg_path_flag(flag: str, path: str) -> str:
    """Render a ``-I``/``-L`` flag for ``nim.cfg`` so a path with spaces survives.

    Nim strips the outer quotes from a config value and passes the result to the C
    compiler after word-splitting it, so ``--passC:"-I/opt/na me"`` reaches clang as
    ``-I/opt/na`` and ``me`` and fails with ``no such file or directory: 'me'``. An
    inner quote survives that split and the compiler driver removes it.

    That inner quote must be a **double** quote, escaped. A config file is read by
    Nim's own lexer, where ``'`` opens a character literal, so a single-quoted path
    is a syntax error in the generated file rather than a flag -- ``nim.cfg(5, 18)
    Error: invalid character constant``, on every Nim invocation the package makes.

    The path is emitted with forward slashes for the same reason: a Windows path
    inside a Nim string literal would put ``\\U`` in front of ``Users`` and be read
    as an escape. Every compiler in the matrix accepts ``/`` as a separator on
    Windows, so this costs nothing and removes the escape question entirely.
    """
    if '"' in path:
        raise ValueError(
            f"cannot place {path!r} in nim.cfg: a double quote in an include or library "
            f"path cannot be quoted in Nim's config format"
        )
    # PureWindowsPath, not PurePath: the separator to normalise is a property of the
    # path, not of the host generating the config, and it leaves a POSIX path alone.
    return f'"{flag}\\"{PureWindowsPath(path).as_posix()}\\""'


def _bare_type_head(raw: str) -> str:
    """Strip qualifiers, tag keywords and template arguments down to the head name."""
    name = raw.strip()
    for qualifier in ("const ", "volatile "):
        while name.startswith(qualifier):
            name = name[len(qualifier) :].strip()
    for tag in _C_TAG_PREFIXES:
        if name.startswith(tag):
            name = name[len(tag) :].strip()
            break
    return name.split("<", 1)[0].rsplit("::", 1)[-1].strip()


#: Standard-library names the writer renders as a C++ helper type (``CppString``,
#: ``UniquePtr``, ``SharedPtr``, ``WeakPtr``, ``CppVector``) or as an ``importcpp``
#: base object.
CPP_STDLIB_MARKERS: frozenset[str] = frozenset(
    {
        "exception",
        "string",
        "wstring",
        "basic_string",
        "unique_ptr",
        "shared_ptr",
        "weak_ptr",
        "vector",
    }
)


def _type_name_requires_cpp(
    raw: str,
    *,
    unit_is_cpp: bool,
    is_elaborated: bool | None = None,
) -> bool:
    """Whether a type *name* requires the C++ backend."""
    name = raw.strip()
    if "<" in name or "::" in name:
        return True
    if not unit_is_cpp:
        return False
    if is_elaborated is True or name.startswith(_C_TAG_PREFIXES):
        return False
    return _bare_type_head(name) in CPP_STDLIB_MARKERS


def _type_requires_cpp(t: TypeExpr, *, unit_is_cpp: bool) -> bool:
    """Whether rendering ``t`` produces Nim that only the C++ backend can build."""
    if isinstance(t, Reference):
        # Rendered as `var T`, which is a C++ reference. C has no such parameter.
        return True
    if isinstance(t, CType):
        return _type_name_requires_cpp(t.name, unit_is_cpp=unit_is_cpp, is_elaborated=t.is_elaborated)
    if isinstance(t, Pointer):
        return _type_requires_cpp(t.pointee, unit_is_cpp=unit_is_cpp)
    if isinstance(t, Array):
        return _type_requires_cpp(t.element_type, unit_is_cpp=unit_is_cpp)
    if isinstance(t, FunctionPointer):
        return _type_requires_cpp(t.return_type, unit_is_cpp=unit_is_cpp) or any(
            _type_requires_cpp(p.type, unit_is_cpp=unit_is_cpp) for p in t.parameters
        )
    return False


def _signature_requires_cpp(f: Function, *, unit_is_cpp: bool) -> bool:
    """Whether a function's return type or any parameter type is C++-only."""
    return _type_requires_cpp(f.return_type, unit_is_cpp=unit_is_cpp) or any(
        _type_requires_cpp(p.type, unit_is_cpp=unit_is_cpp) for p in f.parameters
    )


def _struct_requires_cpp(s: Struct) -> bool:
    """Whether this record is rendered with ``importcpp`` rather than ``importc``.

    ``namespace`` belongs here rather than only in :func:`unit_requires_cpp`: a
    record in a namespace has no C tag to import, and the emitter already spells
    its ``importcpp`` pattern as ``ns::Name`` once it takes this branch.
    """
    return bool(
        s.is_cppclass or s.methods or s.bases or s.constructors or s.destructor or s.template_params or s.namespace
    )


def _function_requires_cpp(f: Function, *, unit_is_cpp: bool) -> bool:
    """Whether this free function is rendered with ``importcpp`` rather than ``importc``.

    A C++-only *signature* belongs here alongside namespace and template, because
    the pragma and the rendered parameter types have to agree. An ``int&``
    parameter renders as ``var cint``; under ``importc`` Nim passes ``int*`` and
    the C++ compiler rejects the call, while ``importcpp`` passes an lvalue and it
    binds.

    ``unit_is_cpp`` has no default on purpose. It defaulted to ``True``, and the
    renderer took that default while the unit said ``c``: a C function taking a
    ``typedef char string *`` came out ``importcpp``, in a package built with the
    C backend.
    """
    return bool(
        f.namespace
        or f.template_params
        or '"' in f.name
        or f.name.startswith("operator")
        or _signature_requires_cpp(f, unit_is_cpp=unit_is_cpp)
    )


def unit_requires_cpp(unit: SourceUnit | Header) -> bool:
    """Whether the Nim bindings for ``unit`` need the C++ backend to compile.

    The answer is read off the IR, never off the file extension: a ``.h`` may
    declare a class and a ``.hpp`` may declare nothing but C functions.

    This is deliberately a **superset** of the ``importc``/``importcpp`` pragma
    decision, because the two questions are different. A record whose pragma is
    ``importc`` still forces the C++ backend if one of its fields is a
    ``std::string``, since the field renders as ``CppString``; and a free function
    keeps its ``importc`` pragma while taking an ``int&``, which renders as
    ``var cint``. Both are C++-only Nim emitted under a C pragma, so asking only
    "which pragma" would answer C for a unit the C backend cannot build.

    The test is structural rather than an enumerated list of shapes: any
    reference, any template-id, any qualified name, any namespace, any scoped
    enumeration. A shape nobody thought to enumerate is still caught if it is one
    of those. The one test that spelling cannot settle -- a bare ``string`` being
    ``std::string`` or a C typedef of that name -- is settled by the language the
    parser recorded for the unit; see :func:`_type_name_requires_cpp`.
    """
    unit_is_cpp = getattr(unit, "language", None) == "cpp"
    for decl in unit.declarations:
        if isinstance(decl, Struct):
            if _struct_requires_cpp(decl):
                return True
            if any(_type_requires_cpp(f.type, unit_is_cpp=unit_is_cpp) for f in decl.fields):
                return True
            if any(_signature_requires_cpp(m, unit_is_cpp=unit_is_cpp) for m in decl.methods + decl.constructors):
                return True
        elif isinstance(decl, Function):
            if _function_requires_cpp(decl, unit_is_cpp=unit_is_cpp):
                return True
        elif isinstance(decl, Enum):
            # `enum class` has no C spelling at all, scoped or otherwise.
            if decl.is_scoped or decl.namespace:
                return True
        elif isinstance(decl, Typedef):
            if _type_requires_cpp(decl.underlying_type, unit_is_cpp=unit_is_cpp):
                return True
        elif isinstance(decl, Variable):
            if _type_requires_cpp(decl.type, unit_is_cpp=unit_is_cpp):
                return True
        if getattr(decl, "namespace", None):
            return True
    return False


_normalize_nim_ident = nim_ident_identity


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
    filter_bases_by_access_floor: ClassVar[bool] = False
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
        WriterOption(
            name="library",
            description="Native library to link, without the 'lib' prefix or extension (emits --passL:-l<name>)",
            default=None,
            type=list,
        ),
        WriterOption(
            name="library_dirs",
            description="Directories to search for the native library (emits --passL:-L<dir>)",
            default=None,
            type=list,
        ),
        WriterOption(
            name="access_floor",
            description="Minimum member access level to include (public, protected, private, or all)",
            default="public",
            choices=("public", "protected", "private", "all"),
        ),
        WriterOption(
            name="macro_types",
            description="Type hints for macro functions",
            default=None,
            type=dict,
        ),
    )

    _known_types: set[str]
    _emitted_proc_sigs: set[tuple[str, tuple[str, ...]]]
    _emitted_normalized_types: dict[str, str]
    _type_renames: dict[str, str]
    _type_aliases: dict[str, str]

    def __init__(
        self,
        *,
        header_path: str | None = None,
        context: PipelineContext | None = None,
        macro_types: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.header_path = header_path.replace("\\", "/") if header_path else None
        self._used_helpers: set[str] = set()
        # ``writer="nim"`` is what makes this writer's own legality renamer
        # match; a caller-supplied context is honoured otherwise so a rename can
        # be scoped to a backend, a target or a layout like any other hook.
        self._context = context or PipelineContext(writer="nim")
        if self._context.writer != "nim":
            self._context = replace(self._context, writer="nim")
        self._unit_is_cpp = False
        self._name_map: dict[DeclIdentity, str] = {}
        self._type_refs: dict[str, dict[str, str]] = {}
        self._identity_owners: dict[str, Symbol] = {}
        self.macro_types: dict[str, dict[str, Any]] = dict(macro_types) if macro_types else {}

    def _prepare(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> SourceUnit | Header:
        if options is not None:
            if "macro_types" in options.options and options.options["macro_types"]:
                self.macro_types.update(options.options["macro_types"])
            elif options.macro_types:
                self.macro_types.update(options.macro_types)
            if "header_path" in options.options and options.options["header_path"]:
                self.header_path = str(options.options["header_path"]).replace("\\", "/")
        return super()._prepare(unit, options=options)

    def _ident(self, name: str, *, kind: str, namespace: str | None = None) -> str:
        """Return the Nim identifier for the source symbol *name* of *kind*."""
        if kind in MODULE_SCOPE_KINDS:
            mapped = self._name_map.get(_decl_identity(name, kind, namespace))
            if mapped is not None:
                return mapped
            if namespace is None and "::" in name:
                ns, bare = name.rsplit("::", 1)
                mapped = self._name_map.get(_decl_identity(bare, kind, ns))
                if mapped is not None:
                    return mapped
            if kind in _TYPE_KINDS:
                mapped = self._resolve_type_ref(name)
                if mapped is not None:
                    return mapped
        out = dispatch_rename(name, kind=kind, context=self._context)
        validate_nim_ident(out)
        return out

    def _resolve_type_ref(self, name: str) -> str | None:
        """The identifier a bare type reference to *name* denotes, if the unit declares it."""
        candidates = self._type_refs.get(name)
        if not candidates:
            return None
        idents = set(candidates.values())
        if len(idents) == 1:
            return next(iter(idents))
        described = ", ".join(f"{kind} -> {ident!r}" for kind, ident in sorted(candidates.items()))
        raise RenameError(
            f"the type reference {name!r} is ambiguous in the generated module: the unit declares it as "
            f"{len(candidates)} distinct types ({described}), and a collision policy gave them different "
            "identifiers. headerkit will not guess which one a use of the bare name meant: rename one at "
            "the source, or add a rename_symbol rule that distinguishes them."
        )

    def _synthesized_ident(self, name: str, *, what: str) -> str:
        """Check a name the writer invented against the same floor a renamed one passes."""
        validate_nim_ident(name)
        owner = self._identity_owners.get(nim_ident_identity(name))
        if owner is not None:
            raise RenameError(
                f"the Nim identifier {name!r}, which this writer generates for {what}, is the same identifier "
                f"as the one assigned to {owner.name!r} ({owner.kind}) declared in this unit "
                f"(identity {nim_ident_identity(name)!r}). Emitting both is 'attempt to redefine' at compile "
                "time: rename the declaration at the source, or add a rename_symbol rule for it."
            )
        return name

    @staticmethod
    def _declaration_kind(decl: object) -> str | None:
        """Map an IR declaration to its :data:`~headerkit._rename.RENAME_KINDS` kind."""
        if isinstance(decl, Struct):
            return "union" if decl.is_union else "struct"
        if isinstance(decl, Enum):
            return "enum"
        if isinstance(decl, Typedef):
            return "typedef"
        if isinstance(decl, Function):
            return "function"
        if isinstance(decl, Constant | MacroFunction):
            return "macro"
        if isinstance(decl, Variable):
            return "function"
        return None

    def _build_name_map(self, header: Header | SourceUnit) -> dict[DeclIdentity, str]:
        """Rename every module-scope symbol, then prove the result is injective."""
        assigned: dict[Symbol, str] = {}
        taken: set[DeclIdentity] = set()
        index = 0

        def take(name: str, kind: str, location: object, namespace: str | None = None) -> None:
            nonlocal index
            if not name or "(anonymous" in name or "(unnamed" in name:
                return
            identity = _decl_identity(name, kind, namespace)
            if identity in taken:
                return
            taken.add(identity)
            file = getattr(location, "file", "") or ""
            sym = Symbol(index=index, name=name, kind=kind, header=str(file), namespace=namespace)
            index += 1
            assigned[sym] = dispatch_rename(name, kind=kind, context=self._context)

        declarations = list(header.declarations)
        declared_tags = {
            decl.name for decl in declarations if isinstance(decl, Struct | Enum) and decl.name and not decl.namespace
        }

        for decl in declarations:
            kind = self._declaration_kind(decl)
            if kind is None:
                continue
            ns = _decl_namespace(decl)
            if isinstance(decl, Typedef) and _typedef_aliases_its_own_tag(decl) and decl.name in declared_tags:
                continue
            take(getattr(decl, "name", "") or "", kind, getattr(decl, "location", None), namespace=ns)
            if isinstance(decl, Enum):
                for value in decl.values:
                    take(value.name, "enumerator", getattr(decl, "location", None), namespace=ns)

        for name in assigned.values():
            validate_nim_ident(name)

        resolved = enforce_injectivity(
            assigned,
            identity=nim_ident_identity,
            context=self._context,
            validate=validate_nim_ident,
        )

        self._type_refs = {}
        self._identity_owners = {}
        for sym, ident in sorted(resolved.items()):
            self._identity_owners.setdefault(nim_ident_identity(ident), sym)
            if sym.kind in _TYPE_KINDS:
                self._type_refs.setdefault(sym.name, {})[f"{sym.kind}:{sym.namespace}"] = ident
                if sym.namespace:
                    self._type_refs.setdefault(f"{sym.namespace}::{sym.name}", {})[sym.kind] = ident
        return {_decl_identity(sym.name, sym.kind, sym.namespace): ident for sym, ident in sorted(resolved.items())}

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Nim comments."""
        return "# {line}"

    def _render(self, unit: SourceUnit | Header) -> str:
        """Convert parsed header IR to Nim source code."""
        header = unit
        lines: list[str] = []

        if not header.declarations:
            return "# Generated by headerkit\n"

        header_file = _nim_string_path(self.header_path or header.path or "header.h")
        lines.append("# Generated by headerkit")
        lines.append("")

        # The language the parser recorded for the unit, read once and threaded
        # through every decision that needs it. A C header declaring
        # `typedef char string;` and a C++ header using `std::string` reach the
        # writer as the byte-identical `CType(name="string")`; the IR settles
        # which is which and no predicate over the spelling can.
        self._unit_is_cpp = getattr(unit, "language", None) == "cpp"

        # Every module-scope name is renamed and checked for collisions before a
        # single line is emitted, so a rejected unit is rejected whole rather
        # than half-written.
        self._name_map = self._build_name_map(header)

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
        emitted_types: set[DeclIdentity | str] = set()
        types_section: list[str] = []
        procs_section: list[str] = []
        consts_section: list[str] = []
        # Reset per render: the helper declarations emitted below are exactly the
        # helpers `_format_type` reports having produced while rendering this unit.
        self._used_helpers = set()

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
            self._synthesized_ident("std_exception", what="the base object for std::exception")
            types_section.append(
                'std_exception* {.importcpp: "std::exception", header: "<exception>", inheritable.} = object'
            )
            emitted_types.add(_decl_identity("std_exception", "struct", None))
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
        known_decl_names.update(t if isinstance(t, str) else t[0] for t in emitted_types)
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

        if not getattr(self, "_in_reconciled", False):
            self._emitted_proc_sigs = set()
            self._emitted_normalized_types = {}
            self._type_renames = {}
            self._type_aliases = {}
            self._known_types = set(STANDARD_NIM_TYPES)
        else:
            if not hasattr(self, "_emitted_proc_sigs"):
                self._emitted_proc_sigs = set()
            if not hasattr(self, "_emitted_normalized_types"):
                self._emitted_normalized_types = {}
            if not hasattr(self, "_type_renames"):
                self._type_renames = {}
            if not hasattr(self, "_type_aliases"):
                self._type_aliases = {}

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
        if hasattr(self, "_known_types"):
            self._known_types.update(known_types)
        else:
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
                ident = _decl_identity(
                    decl.name or ANONYMOUS_RECORD_NAME, "union" if decl.is_union else "struct", decl.namespace
                )
                if ident not in emitted_types:
                    emitted_types.add(ident)
                    if decl.name:
                        emitted_types.add(decl.name)
                    t_lines, m_lines = self._write_struct(
                        decl, header_file, known_base_classes, emitted_types=emitted_types
                    )
                    types_section.extend(t_lines)
                    procs_section.extend(m_lines)
            elif isinstance(decl, Enum):
                ident = _decl_identity(decl.name or "", "enum", decl.namespace)
                if not decl.name or ident not in emitted_types:
                    if decl.name:
                        emitted_types.add(ident)
                    t_lines, c_lines = self._write_enum(decl, header_file, func_names)
                    types_section.extend(t_lines)
                    consts_section.extend(c_lines)
            elif isinstance(decl, Typedef):
                ident = _decl_identity(decl.name, "typedef", decl.namespace)
                if ident not in emitted_types:
                    t_lines = self._write_typedef(decl)
                    if t_lines:
                        emitted_types.add(ident)
                        types_section.extend(t_lines)
            elif isinstance(decl, Function):
                procs_section.extend(self._write_function(decl, header_file))
            elif isinstance(decl, Constant):
                consts_section.extend(self._write_constant(decl))
            elif isinstance(decl, Variable):
                procs_section.extend(self._write_variable(decl, header_file))
            elif isinstance(decl, MacroFunction):
                procs_section.extend(self._write_macro_function(decl, header_file))

        # Helper declarations come from the render itself, not from a second
        # predicate over the IR: whatever `_format_type` produced is declared, and
        # nothing else. They are prepended so a helper is declared before the
        # record whose field names it.
        helper_types: list[str] = []
        helper_procs: list[str] = []
        for helper in sorted(self._used_helpers):
            ident = _decl_identity(helper, "struct", None)
            if ident in emitted_types or helper in emitted_types:
                continue
            declaration, companions = CPP_HELPER_DECLARATIONS[helper]
            helper_types.append(declaration)
            emitted_types.add(ident)
            emitted_types.add(helper)
            for companion in companions:
                helper_procs.extend(["", companion])
        types_section = helper_types + types_section
        procs_section = helper_procs + procs_section

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

    def _use_helper(self, helper: str) -> str:
        """Record that this render referenced ``helper``, and return its name.

        The declaration set is built from these records, so a helper cannot be
        referenced without also being declared.
        """
        self._used_helpers.add(helper)
        for required in _CPP_HELPER_REQUIRES.get(helper, ()):
            self._used_helpers.add(required)
            self._synthesized_ident(required, what=f"the companion of the C++ helper type {helper}")
        return self._synthesized_ident(helper, what="a C++ standard-library type")

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
            if not _type_name_requires_cpp(t.name, unit_is_cpp=self._unit_is_cpp, is_elaborated=t.is_elaborated):
                pass
            elif name.startswith("std::shared_ptr<") or name.startswith("shared_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('SharedPtr')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::unique_ptr<") or name.startswith("unique_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('UniquePtr')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::weak_ptr<") or name.startswith("weak_ptr<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('WeakPtr')}[{self._format_type(CType(inner))}]"
            elif name.startswith("std::vector<") or name.startswith("vector<"):
                inner = name[name.index("<") + 1 : name.rindex(">")].strip()
                return f"{self._use_helper('CppVector')}[{self._format_type(CType(inner))}]"
            elif name in ("std::vector", "vector"):
                return f"{self._use_helper('CppVector')}[pointer]"
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
                return self._use_helper("CppString")
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

            qualified = _qualified_type_name(name, t.qualifiers)
            if qualified in C_TO_NIM_PRIMITIVES:
                return C_TO_NIM_PRIMITIVES[qualified]

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
            return self._ident(name, kind="struct")

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
                f"{self._ident(p.name or f'a{i}', kind='param')}: {self._format_type(p.type, in_param=True)}"
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
        emitted_types: set[DeclIdentity | str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Render a Struct or class as a Nim type declaration and its methods."""
        type_lines: list[str] = []
        methods_lines: list[str] = []

        if s.name:
            name = s.name
            t_name = self._ident(name, kind="union" if s.is_union else "struct", namespace=s.namespace)
        else:
            name = ANONYMOUS_RECORD_NAME
            t_name = self._synthesized_ident(name, what="an anonymous record")
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
            gen_params = f"[{', '.join(_template_param_ident(tp) for tp in valid_tps)}]" if valid_tps else ""
        else:
            gen_params = ""
        if gen_params == "[]":
            gen_params = ""

        pragma_parts: list[str] = []
        is_cpp = _struct_requires_cpp(s)

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

        # Inheritance. Nim objects have one base, so a class with several keeps
        # its first and the rest are reported rather than dropped: the binding
        # describes the header, and a reader who cannot see that `Timer` is gone
        # will look for its members on this type and not find them.
        base_str = ""
        base_notes: list[str] = []
        if s.bases:
            # Single primary base in Nim object inheritance (skip private/protected bases)
            public_bases = [b for b in s.bases if b.access not in ("private", "protected")]
            tps = self._get_struct_type_params(s)
            valid_bases = [b for b in public_bases if b.name and b.name not in tps]
            if valid_bases:
                primary = valid_bases[0]
                raw_base = primary.name.strip("`")
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
                base_notes.extend(self._describe_base(s, primary, dropped=False))
                for extra in valid_bases[1:]:
                    base_notes.extend(self._describe_base(s, extra, dropped=True, primary_name=primary.name))
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
        lines = [*base_notes, f"{t_name}*{gen_params}{pragma_str} = object{base_str}"]

        visible_fields = [f for f in s.fields if not f.is_static and f.access not in ("private", "protected")]
        if not visible_fields:
            lines[0] += ""
        else:
            for f in visible_fields:
                f_name = self._ident(f.name, kind="field")
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
        m_name = self._ident(m.name, kind="function", namespace=s.namespace)
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
            p_name = self._ident(p.name or f"a{i}", kind="param")
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
        elif m.name == "operator()":
            cpp_pattern = "#(@)"
        elif m.name in CPP_OPERATOR_MAP:
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
        s_name = s.name or ANONYMOUS_RECORD_NAME
        raw_cpp_name = s.cpp_name or s_name
        if hasattr(self, "_type_renames"):
            s_name = self._type_renames.get(s_name, s_name)
        tps = self._get_struct_type_params(s)
        struct_type = self._get_struct_nim_type(s)
        t_params = f"[{', '.join(_template_param_ident(tp) for tp in tps)}]" if tps else ""
        decl = f'proc destroy*{t_params}(this: var {struct_type}) {{.importcpp: "#.~{raw_cpp_name}()", header: "{header_file}".}}'
        return ["", decl]

    def _constructor_proc_name(self, s: Struct) -> str:
        """The ``constructFoo`` proc name for a record, built from its *renamed* identifier."""
        if s.name:
            base = self._ident(s.name, kind="struct", namespace=s.namespace).strip("`")
            what = f"the constructor of {s.name!r}"
        else:
            base = self._synthesized_ident(ANONYMOUS_RECORD_NAME, what="an anonymous record").strip("`")
            what = "the constructor of an anonymous record"
        return self._synthesized_ident(f"construct{base}", what=what)

    def _write_constructor(self, s: Struct, ctor: Function, header_file: str) -> list[str]:
        """Render a C++ constructor as a Nim constructProc."""
        if ctor.is_deleted:
            return []
        s_name = s.name or ANONYMOUS_RECORD_NAME
        raw_cpp_name = s.cpp_name or s_name
        proc_name = self._constructor_proc_name(s)
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
            p_name = self._ident(p.name or f"a{i}", kind="param")
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
            t_params = f"[{', '.join(_template_param_ident(tp) for tp in all_tps)}]"
            t_args = ", ".join(f"'*{i}" for i in range(len(tps)))
            cpp_pattern = f"{raw_cpp_name}<{t_args}>(@)" if t_args else f"{raw_cpp_name}(@)"
        else:
            t_params = ""
            cpp_pattern = f"{raw_cpp_name}(@)"

        pragma = f'importcpp: "{cpp_pattern}", header: "{header_file}", constructor'

        return ["", f"proc {proc_name}*{t_params}({', '.join(params)}): {ret_type} {{.{pragma}.}}"]

    @staticmethod
    def _describe_base(s: Struct, base: BaseSpecifier, *, dropped: bool, primary_name: str = "") -> list[str]:
        """Report what a base contributes that the emitted declaration cannot say.

        Nim has neither multiple inheritance nor access specifiers nor virtual
        bases, so three properties the header states have nowhere to go in the
        declaration itself. Silence would make them look absent rather than
        inexpressible.
        """
        traits = []
        if base.access and base.access != "public":
            traits.append(base.access)
        if base.is_virtual:
            traits.append("virtual")
        described = f"{' '.join(traits)} {base.name}" if traits else base.name
        if dropped:
            chosen = primary_name or (s.bases[0].name if s.bases else "the first base")
            return [
                f"# UNSUPPORTED: base '{described}' of '{s.name}' is not emitted; "
                f"Nim objects have a single base and '{chosen}' takes it"
            ]
        if traits:
            return [f"# NOTE: base '{described}' of '{s.name}' is emitted as a plain Nim base"]
        return []

    def _enum_size_type(self, e: Enum) -> str:
        """The Nim type whose ``sizeof`` is the enum's width.

        ``{.size.}`` decides how many bytes every value of this enum occupies as
        it crosses the ABI, so a wrong answer is a wrong layout rather than a
        cosmetic difference. A fixed underlying type gives it exactly: `enum class
        A : unsigned char` is one byte and `: long long` is eight, where a
        hardcoded `cint` claims four for both.

        ``cint`` remains the answer when there is no underlying type to read, which
        covers two situations the width cannot distinguish: a header that declares
        none, leaving an unscoped C enum int-compatible, and a parser that failed to
        see a clause that is there. They differ in whether the answer is *known*,
        not in what it is, so that distinction is carried by
        :meth:`_enum_width_notes` rather than by a second branch here.
        """
        if not e.underlying_type:
            return "cint"
        return self._format_type(CType(e.underlying_type))

    @staticmethod
    def _enum_width_notes(e: Enum) -> list[str]:
        """Report a width the parser could not establish, rather than claiming one.

        ``underlying_type_known=False`` means the parser may have *missed* a clause
        that is there -- tree-sitter's C grammar has no production for
        ``enum E : long long``, so in a ``.h`` it parses as an ``ERROR`` node and the
        type comes back ``None``. The emitted ``sizeof(cint)`` is a guess in that
        case, and it is four bytes wide whatever the header actually said.
        """
        if e.underlying_type_known:
            return []
        return [
            f"# UNSUPPORTED: the underlying type of '{e.name}' was not established by the "
            f"parser, so its width is assumed to be that of cint; verify it against the header"
        ]

    def _write_enum(self, e: Enum, header_file: str, func_names: set[str] | None = None) -> tuple[list[str], list[str]]:
        """Render an Enum declaration, returning (type_lines, const_lines)."""
        name = e.name or ""
        is_anonymous = not name or "(unnamed" in name or "(anonymous" in name or name.startswith("enum (")

        if is_anonymous:
            # Emit anonymous enum values as constants
            const_lines: list[str] = []
            for v in e.values:
                v_name = self._ident(v.name, kind="enumerator", namespace=e.namespace)
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

        if name.lower() in ("type", "set", "pointer", "object", "string", "int", "bool") and qualified_name:
            e_name = qualified_name
        else:
            e_name = self._ident(name, kind="enum", namespace=e.namespace)

        if e.cpp_name:
            import_pragma = f'importcpp: "{e.cpp_name}", header: "{header_file}"'
        else:
            spelling = _c_type_spelling(name, e.is_typedef, "enum")
            import_pragma = f'importc: "{spelling}", header: "{header_file}"'
        size_type = self._enum_size_type(e)
        lines = [
            *self._enum_width_notes(e),
            f"{e_name}* {{.size: sizeof({size_type}), {import_pragma}.}} = enum",
        ]
        const_lines = []
        seen_values: dict[int | str, str] = {}
        for v in e.values:
            v_name = self._ident(v.name, kind="enumerator", namespace=e.namespace)
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
            lines.append(f"{qualified_name}* = {e_name}")
        return lines, const_lines

    def _write_typedef(self, t: Typedef, emitted_types: set[DeclIdentity | str] | None = None) -> list[str]:
        """Render a Typedef declaration."""
        if not t.name:
            return []

        if _typedef_aliases_its_own_tag(t):
            return []

        if t.name in (
            "uint",
            "uint8",
            "uint16",
            "uint32",
            "uint64",
            "int",
            "int8",
            "int16",
            "int32",
            "int64",
            "float32",
            "float64",
        ):
            return []

        if emitted_types and t.name in emitted_types:
            return []

        t_name = self._ident(t.name, kind="typedef", namespace=t.namespace)
        underlying = self._format_type(t.underlying_type)
        if t_name == underlying or t.name == underlying:
            return []

        if _normalize_nim_ident(t_name) == _normalize_nim_ident(underlying):
            return []

        if emitted_types is not None:
            emitted_types.add(t.name)
            emitted_types.add(t_name)

        return [f"{t_name}* = {underlying}"]

    def _write_function(self, f: Function, header_file: str) -> list[str]:
        """Render a function declaration."""
        if f.access in ("private", "protected") or f.is_deleted:
            return []
        f_name = self._ident(f.name, kind="function", namespace=f.namespace)
        params: list[str] = []
        all_tp = set(
            [tp.name for tp in f.template_parameters if tp.is_type and tp.name]
            or [tp for tp in f.template_params if tp]
        )
        all_known = getattr(self, "_known_types", set()) | all_tp

        for i, p in enumerate(f.parameters):
            p_name = self._ident(p.name or f"a{i}", kind="param")
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
        if not _function_requires_cpp(f, unit_is_cpp=self._unit_is_cpp):
            pragmas.append(f'importc: "{f.name}", header: "{header_file}"')
        elif f.namespace:
            cpp_pattern = f"{f.namespace}::{f.name}(@)".replace('"', '\\"')
            pragmas.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')
        else:
            cpp_pattern = f"{f.name}(@)".replace('"', '\\"')
            pragmas.append(f'importcpp: "{cpp_pattern}", header: "{header_file}"')

        if f.is_variadic:
            pragmas.append("varargs")

        if f.calling_convention:
            pragmas.append(f.calling_convention)
        else:
            pragmas.append("cdecl")

        t_params = f"[{', '.join(_template_param_ident(tp) for tp in f.template_params)}]" if f.template_params else ""
        return [f"proc {f_name}*{t_params}({', '.join(params)}){ret_str} {{.{', '.join(pragmas)}.}}"]

    def _write_variable(self, v: Variable, header_file: str) -> list[str]:
        """Render an extern global variable."""
        v_name = self._ident(v.name, kind="function", namespace=v.namespace)
        v_type = self._format_type(v.type)
        if v_type in ("`type`", "type", "c_type", "auto"):
            v_type = "pointer"
        return [f'var {v_name}* {{.importc: "{v.name}", header: "{header_file}".}}: {v_type}']

    def _write_constant(self, c: Constant) -> list[str]:
        """Render a constant."""
        if c.value is None:
            return []
        c_name = self._ident(c.name, kind="macro")
        return [f"{c_name}* = {c.value}"]

    def _write_macro_function(self, m: MacroFunction, header_file: str) -> list[str]:
        """Render a C function-like macro in Nim."""
        if m.has_token_pasting:
            return [f"# Macro {m.name} skipped: contains preprocessor token-pasting (##) operator"]

        if m.name in self.macro_types:
            hint = self.macro_types[m.name]
            ret_type = hint.get("return_type", "void")
            params_hint = hint.get("parameters", [])
            param_strs = [f"{_escape_ident(p['name'])}: {p['type']}" for p in params_hint]
            proc_name = _escape_ident(m.name.lower())
            params_joined = ", ".join(param_strs)
            return [
                f'proc {proc_name}*({params_joined}): {ret_type} {{.importc: "{m.name}", header: "{header_file}", nodecl.}}'
            ]

        c_bridge_name = f"c_{m.name}"
        template_name = _escape_ident(m.name.lower())
        if c_bridge_name.lower() == template_name:
            c_bridge_name = f"c_macro_{m.name}"

        if not m.parameters:
            ret_val = "void" if (m.is_statement or not m.body.strip()) else "cint"
            return [
                f'proc {c_bridge_name}*(): {ret_val} {{.importc: "{m.name}", header: "{header_file}", nodecl.}}',
                f"template {template_name}*(): untyped =",
                f"  {c_bridge_name}()",
            ]

        param_names = [_escape_ident(p.name if p.name != "__VA_ARGS__" else "args") for p in m.parameters]
        call_params = ", ".join(param_names)

        if m.is_statement:
            if len(param_names) == 1:
                type_params = "[T]"
                c_params = f"{param_names[0]}: T"
            else:
                type_params = f"[{', '.join(f'T{i + 1}' for i in range(len(param_names)))}]"
                c_params = ", ".join(f"{name}: T{i + 1}" for i, name in enumerate(param_names))
            t_params = ", ".join(f"{p}: untyped" for p in param_names)
            return [
                f'proc {c_bridge_name}*{type_params}({c_params}): void {{.importc: "{m.name}", header: "{header_file}", nodecl.}}',
                f"template {template_name}*({t_params}): untyped =",
                f"  {c_bridge_name}({call_params})",
            ]

        # Untyped expression macro
        if len(param_names) == 1:
            type_params = "[T]"
            c_params = f"{param_names[0]}: T"
            ret_type = "T"
        else:
            type_params = f"[{', '.join(f'T{i + 1}' for i in range(len(param_names)))}]"
            c_params = ", ".join(f"{name}: T{i + 1}" for i, name in enumerate(param_names))
            ret_type = "T1"
        t_params = ", ".join(f"{p}: untyped" for p in param_names)
        return [
            f'proc {c_bridge_name}*{type_params}({c_params}): {ret_type} {{.importc: "{m.name}", header: "{header_file}", nodecl.}}',
            f"template {template_name}*({t_params}): untyped =",
            f"  {c_bridge_name}({call_params})",
        ]

    @staticmethod
    def _as_list(value: object) -> list[str]:
        """Normalise a writer option that may arrive as a scalar, list, or ``None``."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, set | frozenset):
            # Sorted, not iteration-ordered: an unordered option would reorder the
            # generated flags between runs and defeat regenerate-and-diff.
            return sorted(str(v) for v in value if str(v))
        if isinstance(value, list | tuple):
            return [str(v) for v in value if str(v)]
        return [str(value)]

    def _build_nim_cfg(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> str:
        """Render ``nim.cfg`` -- the flags the generated package needs to compile.

        Every flag here is either invariant, derived from the IR, or supplied by
        the caller. Nothing is guessed: a package whose library the caller did not
        name links against nothing and says so, rather than carrying a plausible
        ``-l`` that resolves to the wrong library or to none.
        """
        lines = ["--mm:orc", "--threads:on", "--styleCheck:hint"]

        # `$config` is the directory holding this file, so the package builds with a
        # bare `nim c tests/...` and not only under `nimble`, which supplies srcDir.
        lines.append('--path:"$config/src"')

        if unit_requires_cpp(unit):
            # The bindings carry `importcpp` pragmas and `#include` a C++ header.
            # The default C backend hands both to the C compiler, which rejects
            # `class` outright. Set in nim.cfg rather than in the .nimble test task
            # because a config backend selection also overrides a plain `nim c`.
            lines.append("--backend:cpp")

        include_dirs = [str(Path(d).resolve()) for d in self._as_list(options.extra_context.get("include_dirs"))]
        # The header's own directory: the one include path the writer always knows,
        # and the one a header's quoted includes of its siblings need. Compared
        # after resolving both sides, or a relatively-passed `-I` naming the same
        # directory is emitted twice.
        unit_path = getattr(unit, "path", "") or ""
        if unit_path:
            parent = str(Path(unit_path).resolve().parent)
            if parent not in include_dirs:
                include_dirs = [*include_dirs, parent]

        lines.extend(f"--passC:{_cfg_path_flag('-I', d)}" for d in include_dirs)
        lines.extend(f'--passC:"-D{d}"' for d in self._as_list(options.extra_context.get("defines")))

        # Resolved for the same reason include_dirs are: a relative -L resolves
        # against the linker's working directory, not the package's.
        library_dirs = [str(Path(d).resolve()) for d in self._as_list(options.get_option("library_dirs"))]
        libraries = self._as_list(options.get_option("library"))
        lines.extend(f"--passL:{_cfg_path_flag('-L', d)}" for d in library_dirs)
        lines.extend(f'--passL:"-l{lib}"' for lib in libraries)
        if not libraries:
            lines.append(
                "# No native library to link: pass --writer-opt nim:library=<name> "
                "(and nim:library_dirs=<dir>) to add the -l/-L flags this package needs."
            )

        return "\n".join(lines) + "\n"

    def _probe_param(self, type_expr: TypeExpr) -> str:
        """Render ``type_expr`` as the pointer a link probe takes it by.

        A probe takes every argument by pointer so that a call site can name the
        probe with ``nil`` whatever the argument type is. Without that, probing an
        entry point would mean inventing a value of each parameter's type -- and a
        C++ class with no default constructor has no value to invent.
        """
        formatted = self._format_type(type_expr, in_param=True)
        # `ptr (var T)` is not a type; `T` is, and `x[]` on a `ptr T` is the lvalue
        # a `var T` parameter binds to.
        formatted = formatted.removeprefix("var ")
        return f"ptr {formatted}"

    def _link_probe(self, name: str, index: int, self_type: str | None, params: list[Parameter], returns: str) -> str:
        """Render one probe proc that references ``name`` so the linker must resolve it."""
        probe_params: list[str] = []
        call_args: list[str] = []
        if self_type is not None:
            probe_params.append(f"self: ptr {self_type}")
            call_args.append("self[]")
        for i, p in enumerate(params):
            arg = f"a{i}"
            probe_params.append(f"{arg}: {self._probe_param(p.type)}")
            call_args.append(f"{arg}[]")
        call = f"{name}({', '.join(call_args)})"
        body = f"discard {call}" if returns != "void" else call
        return f"proc hkLinkProbe{index}({', '.join(probe_params)}) =\n  {body}"

    @staticmethod
    def _is_probeable(f: Function) -> bool:
        """Whether a link probe may reference this member.

        A probe referencing a non-public member fails to **compile**, not to link,
        which takes the whole generated package down. The writer declares such a
        member regardless of access, so the collector declines instead. An access
        the backend left unset is public, which is what C members are.

        A template is skipped for a different reason: a generic emits no symbol
        until it is instantiated, so probing one establishes nothing.
        """
        if (f.access or "public") != "public":
            return False
        return not (f.template_params or f.name in CPP_OPERATOR_MAP)

    def _collect_link_probes(self, unit: SourceUnit | Header) -> tuple[list[str], list[str], list[str]]:
        """Return the probe definitions, their call sites, and the complete C++ types."""
        self._unit_is_cpp = getattr(unit, "language", None) == "cpp"
        if not self._name_map:
            self._name_map = self._build_name_map(unit)
        probes: list[str] = []
        calls: list[str] = []
        complete_types: list[str] = []

        def add(name: str, self_type: str | None, params: list[Parameter], returns: str) -> None:
            index = len(probes)
            probes.append(self._link_probe(name, index, self_type, params, returns))
            args = ["nil"] * (len(params) + (1 if self_type is not None else 0))
            calls.append(f"hkLinkProbe{index}({', '.join(args)})")

        for decl in unit.declarations:
            if isinstance(decl, Function) and not decl.template_params and not decl.is_variadic and decl.name:
                add(
                    self._ident(decl.name, kind="function", namespace=decl.namespace),
                    None,
                    decl.parameters,
                    self._format_type(decl.return_type),
                )
            elif isinstance(decl, Struct) and _struct_requires_cpp(decl) and decl.name and not decl.template_params:
                struct_type = self._ident(decl.name, kind="struct", namespace=decl.namespace)
                complete_types.append(struct_type)
                for m in decl.methods:
                    if not self._is_probeable(m):
                        continue
                    self_type = None if m.is_static else struct_type
                    add(
                        self._ident(m.name, kind="function", namespace=decl.namespace),
                        self_type,
                        m.parameters,
                        self._format_type(m.return_type),
                    )
                for ctor in decl.constructors:
                    if not self._is_probeable(ctor):
                        continue
                    add(self._constructor_proc_name(decl), None, ctor.parameters, struct_type)

        return probes, calls, complete_types

    def _build_cpp_tripwire(self, pkg: str, unit: SourceUnit | Header) -> str:
        """Render the tripwire for a package whose bindings use ``importcpp``.

        ``loadLib``/``symAddr`` cannot serve a C++ target. It looks for an
        unmangled name in a shared object, and an ``importcpp`` binding has neither:
        the C++ name is mangled, and a header-only or statically-linked library has
        no shared object at all. Such a tripwire fails for a reason unrelated to
        whether the bindings work.

        What this tripwire establishes instead is a build-time property, and the
        build is where it fails: the bindings compile under the C++ backend against
        the real header, every non-generic entry point resolves at link time, and
        every bound C++ class is a complete type rather than a forward declaration.
        It does not establish that a shared library is findable at run time -- a
        statically linked package has none to find.
        """
        probes, calls, complete_types = self._collect_link_probes(unit)

        if not probes and not complete_types:
            return self._build_inconclusive_tripwire(pkg)

        probe_block = "\n\n".join(probes) if probes else ""
        call_block = "\n".join(f"    {call}" for call in calls) if calls else "    discard"
        # No fallback assertion when a unit binds entry points but no complete class:
        # the probes carry the claim, and `check declared(pkg)` is true by
        # construction, so adding it would weaken the suite rather than strengthen it.
        size_checks = "\n".join(f"    check sizeof({t}) > 0" for t in complete_types)
        title = "every bound entry point compiles and links against"
        if not probes:
            title = "every bound class is a complete type in"

        # The template is dedented before the blocks go in: dedent() strips the
        # common prefix of every line it is given, and an interpolated block that
        # starts at column zero would leave it with nothing to strip.
        template = textwrap.dedent("""\
            # Tripwire for a C++ target.
            #
            # The assertion this file makes is its own build. Compiling it proves the
            # bindings are valid C++ against the real header; linking it proves every
            # bound entry point resolves against the real native library. A missing
            # library fails the link with undefined symbols and the test never runs.
            #
            # The probes below are never executed -- `hkRunLinkProbes` is false. They
            # exist so the C++ compiler must emit a reference to each entry point and
            # the linker must resolve it. Every probe argument is a pointer so a call
            # site can pass `nil` without inventing a value of a type that may have no
            # default constructor.
            import std/unittest
            import {pkg}

            var hkRunLinkProbes = false

            {probe_block}

            proc hkForceLinkage() =
              if hkRunLinkProbes:
            {call_block}

            suite "Tripwire Compile & Link Verification":
              test "{title} '{pkg}'":
                hkForceLinkage()
            {size_checks}
            """)
        return template.format(
            pkg=pkg,
            probe_block=probe_block,
            call_block=call_block,
            size_checks=size_checks,
            title=title,
        )

    @staticmethod
    def _build_inconclusive_tripwire(pkg: str) -> str:
        """Render a tripwire for a unit that offers nothing a tripwire can check.

        A unit binding only templates has no symbol to link -- a generic emits none
        until it is instantiated -- and no complete class to size.

        It **fails**, loudly, rather than reporting skipped. A skip is not a
        failure to ``std/unittest``, so the generated file exited 0, and a tripwire
        that exits 0 without establishing linkage is exactly the green mirage the
        tripwire invariant in ``AGENTS.md`` forbids: the package's own test command
        reported success while nothing anywhere had checked that the native library
        resolves. The two states a caller must be able to tell apart -- "the
        entry points link" and "nobody established that they link" -- were the same
        exit status, and the second is the one that needs a person to act.

        Acting on it is a few lines: instantiate the generics the package binds in a
        test of your own, and that test establishes the linkage this one cannot.
        """
        return textwrap.dedent(f"""\
            # Tripwire for a C++ target that binds no linkable entry point.
            #
            # Every binding in this package is a template or has no complete class
            # behind it. A generic emits no symbol until it is instantiated, so there
            # is nothing here whose linkage a tripwire could establish.
            #
            # This file therefore FAILS. It is not reporting that the bindings are
            # broken -- it is reporting that nothing has checked them, which a
            # passing or skipped tripwire would have been unable to say. A tripwire
            # exists to fail when the native library is missing or an entry point
            # does not resolve; one that exits 0 without having established either is
            # indistinguishable from one that verified them.
            #
            # To resolve it: instantiate the generics you use in a test of your own,
            # and delete this file. That test establishes the linkage this one cannot.
            import std/unittest
            import {pkg}

            suite "Tripwire Compile & Link Verification":
              test "linkage of '{pkg}' is not established by this tripwire":
                echo "TRIPWIRE INCONCLUSIVE: no non-generic entry point and no complete class is bound by '{pkg}'"
                echo "TRIPWIRE INCONCLUSIVE: nothing here establishes that the native library links"
                echo "TRIPWIRE INCONCLUSIVE: failing rather than exiting 0, so this cannot be read as verification"
                fail()
            """)

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
        files.append(OutputFile(path="nim.cfg", content=self._build_nim_cfg(unit, options)))

        # 5. Tests
        if test_type in ("tripwire", "both") and unit_requires_cpp(unit):
            files.append(OutputFile(path="tests/test_tripwire.nim", content=self._build_cpp_tripwire(pkg, unit)))
        elif test_type in ("tripwire", "both"):
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

    def write(self, header: Any) -> str:
        if isinstance(header, (ReconciledUnit, MultiDimensionalReconciledUnit)):
            return self.write_reconciled(header)
        return super().write(header)

    def write_reconciled(self, unit: ReconciledUnit | MultiDimensionalReconciledUnit) -> str:
        """Render a ReconciledUnit containing common declarations and platform-guarded blocks."""
        if not unit.items:
            return "# Generated by headerkit\n"

        self._in_reconciled = True
        try:
            return self._write_reconciled_impl(unit)
        finally:
            if hasattr(self, "_in_reconciled"):
                delattr(self, "_in_reconciled")
            if hasattr(self, "_known_types"):
                delattr(self, "_known_types")
            if hasattr(self, "_emitted_proc_sigs"):
                delattr(self, "_emitted_proc_sigs")
            if hasattr(self, "_emitted_normalized_types"):
                delattr(self, "_emitted_normalized_types")
            if hasattr(self, "_type_renames"):
                delattr(self, "_type_renames")
            if hasattr(self, "_type_aliases"):
                delattr(self, "_type_aliases")

    def _write_reconciled_impl(self, unit: ReconciledUnit | MultiDimensionalReconciledUnit) -> str:
        header_file = (self.header_path or unit.path or "header.h").replace("\\", "/")
        lines: list[str] = ["# Generated by headerkit", ""]

        all_decls: list[Declaration] = []
        for item in unit.items:
            if isinstance(item, (PlatformGuardedBlock, MatrixGuardedBlock)):
                for _, b_decls in item.branches:
                    all_decls.extend(b_decls)
            else:
                all_decls.append(item)

        # Pre-seed known types across the entire ReconciledUnit so cross-chunk references resolve
        self._emitted_proc_sigs = set()
        self._emitted_normalized_types = {}
        self._type_renames = {}
        self._type_aliases = {}
        known: set[str] = set(STANDARD_NIM_TYPES)
        for d in all_decls:
            if hasattr(d, "name") and d.name:
                known.add(d.name)
                known.add(_escape_ident(d.name, is_type=True))
            if isinstance(d, Struct):
                for nr in d.nested_records:
                    if nr.name and nr.access not in ("private", "protected"):
                        known.add(nr.name)
                        known.add(f"{d.name}_{nr.name}")
                        known.add(_escape_ident(f"{d.name}_{nr.name}", is_type=True))
        self._known_types = known

        has_call_operator = any(
            isinstance(decl, Struct) and any(m.name == "operator()" for m in decl.methods) for decl in all_decls
        )
        if has_call_operator:
            lines.append('{.experimental: "callOperator".}')
            lines.append("")

        emitted_inc_helper = False

        def _render_decl_list(decls: list[Declaration]) -> str:
            nonlocal emitted_inc_helper
            if not decls:
                return ""
            sub_header = Header(path=header_file, declarations=decls)
            rendered = self._render(sub_header)
            out_lines = rendered.splitlines()
            body_lines: list[str] = []
            skipping = True
            for line in out_lines:
                stripped = line.strip()
                if skipping:
                    if (
                        stripped.startswith("# Generated by headerkit")
                        or stripped.startswith('{.experimental: "callOperator".}')
                        or not stripped
                    ):
                        continue
                    skipping = False
                body_lines.append(line)

            result_text = "\n".join(body_lines).strip()
            if "proc inc*[T](p: var ptr T)" in result_text:
                if emitted_inc_helper:
                    lines_clean = []
                    skip_inc = False
                    for clean_line in result_text.splitlines():
                        if "proc inc*[T](p: var ptr T)" in clean_line:
                            skip_inc = True
                            continue
                        if skip_inc:
                            if clean_line.strip().startswith("p = cast[ptr T]"):
                                skip_inc = False
                                continue
                        lines_clean.append(clean_line)
                    result_text = "\n".join(lines_clean).strip()
                else:
                    emitted_inc_helper = True

            return result_text

        sections: list[str] = []
        decl_accumulator: list[Declaration] = []

        def flush_accumulator() -> None:
            if decl_accumulator:
                rendered_chunk = _render_decl_list(decl_accumulator)
                if rendered_chunk:
                    sections.append(rendered_chunk)
                decl_accumulator.clear()

        for item in unit.items:
            if isinstance(item, (PlatformGuardedBlock, MatrixGuardedBlock)):
                flush_accumulator()
                block_lines: list[str] = []

                # Snapshot state prior to guarded block so each mutually exclusive branch
                # sees a clean baseline without cross-branch type name collision mangling
                sigs_base = set(getattr(self, "_emitted_proc_sigs", set()))
                types_base = dict(getattr(self, "_emitted_normalized_types", {}))
                renames_base = dict(getattr(self, "_type_renames", {}))
                aliases_base = dict(getattr(self, "_type_aliases", {}))

                for idx, (cond, branch_decls) in enumerate(item.branches):
                    self._emitted_proc_sigs = set(sigs_base)
                    self._emitted_normalized_types = dict(types_base)
                    self._type_renames = dict(renames_base)
                    self._type_aliases = dict(aliases_base)

                    kw = "when" if idx == 0 else "elif"
                    pred = getattr(cond, "nim_predicate", None) or getattr(cond, "predicate", "")
                    block_lines.append(f"{kw} {pred}:")
                    rendered_branch = _render_decl_list(branch_decls)
                    if not rendered_branch:
                        block_lines.append("  discard")
                    else:
                        for b_line in rendered_branch.splitlines():
                            block_lines.append(f"  {b_line}" if b_line else "")

                # Reset back to base for downstream unconditional declarations
                self._emitted_proc_sigs = set(sigs_base)
                self._emitted_normalized_types = dict(types_base)
                self._type_renames = dict(renames_base)
                self._type_aliases = dict(aliases_base)

                if block_lines:
                    sections.append("\n".join(block_lines))
            else:
                decl_accumulator.append(item)

        flush_accumulator()

        if hasattr(self, "_known_types"):
            delattr(self, "_known_types")
        if hasattr(self, "_emitted_proc_sigs"):
            delattr(self, "_emitted_proc_sigs")
        if hasattr(self, "_emitted_normalized_types"):
            delattr(self, "_emitted_normalized_types")
        if hasattr(self, "_type_renames"):
            delattr(self, "_type_renames")
        if hasattr(self, "_type_aliases"):
            delattr(self, "_type_aliases")

        if sections:
            lines.append("\n\n".join(sections))

        return "\n".join(lines).rstrip() + "\n"


def write_nim(
    header: Header | SourceUnit | ReconciledUnit | MultiDimensionalReconciledUnit,
    *,
    header_path: str | None = None,
    macro_types: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Convenience function to generate Nim bindings from a Header IR or ReconciledUnit."""
    writer = NimWriter(header_path=header_path, macro_types=macro_types)
    if isinstance(header, (ReconciledUnit, MultiDimensionalReconciledUnit)):
        return writer.write_reconciled(header)
    return writer.write(header)


from headerkit.writers import register_writer  # noqa: E402

register_writer("nim", NimWriter, description="Nim bindings with C and C++ interop (importc, importcpp)")
