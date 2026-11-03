"""Base class and common helpers for Headerkit writers."""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from headerkit.ir import (
    Declaration,
    Enum,
    EnumValue,
    Function,
    Header,
    SourceUnit,
    Struct,
    filter_access_floor,
)
from headerkit.transforms.builtin import StripPaddingFields

if TYPE_CHECKING:
    from headerkit.reconcile import MatrixCondition, PlatformCondition


@dataclass(frozen=True, slots=True)
class OutputFile:
    """A single file to be written in a project layout."""

    path: str
    content: str
    is_executable: bool = False
    preserve_existing: bool = False
    merge_strategy: str | None = None


@dataclass(slots=True)
class ProjectLayout:
    """A collection of output files comprising a single-file or project layout."""

    files: list[OutputFile] = field(default_factory=list)

    def get_file(self, path: str) -> OutputFile | None:
        """Find an output file by its relative project path."""
        for f in self.files:
            if f.path == path:
                return f
        return None

    def write_to_disk(self, target_dir: Path | str, *, overwrite: bool = True) -> list[Path]:
        """Write all files in this layout to the destination directory."""
        written: list[Path] = []
        base = Path(target_dir).resolve()
        for f in self.files:
            p = (base / f.path).resolve()
            if not p.is_relative_to(base):
                raise ValueError(f"Path traversal detected: {f.path}")
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and (f.preserve_existing or not overwrite):
                continue
            p.write_text(f.content, encoding="utf-8")
            if f.is_executable:
                p.chmod(p.stat().st_mode | 0o111)
            written.append(p)
        return written


@dataclass(slots=True)
class ScaffoldOptions:
    """Configuration options for writer layout generation."""

    package_name: str = "bindings"
    target_language: str = ""
    layout: str = "file"
    options: dict[str, Any] = field(default_factory=dict)
    interactive: bool = False
    extra_context: dict[str, Any] = field(default_factory=dict)
    test_type: str = "both"
    test_runner: str = "tripwire"
    test_strategy: str = "incremental"
    version_guard: str = "declared"
    version_constant: str | None = None
    macro_types: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if "test_type" not in self.options and self.test_type != "both":
            self.options["test_type"] = self.test_type
        elif "test_type" in self.options:
            self.test_type = str(self.options["test_type"])

        if "macro_types" not in self.options and self.macro_types:
            self.options["macro_types"] = self.macro_types
        elif "macro_types" in self.options and not self.macro_types:
            self.macro_types = dict(self.options["macro_types"])

    def get_option(self, name: str, default: Any = None) -> Any:
        """Get a writer-specific option value."""
        if name in self.options:
            return self.options[name]
        if hasattr(self, name):
            return getattr(self, name)
        return default


#: One-line stand-in for a generated multi-line block inside a dedented
#: template. See :func:`render_block_template` for why the substitution cannot
#: be an ordinary f-string interpolation.
DEDENT_BLOCK = "_HK_BLOCK_"


def module_level_bindings(source: str) -> frozenset[str]:
    """Return every module-level name ``source`` binds.

    A generated Python module's export block is emitted last, so any name the
    module already binds must be reserved before that block re-exports a C
    symbol over it. Deriving the set from the emitted text keeps it correct by
    construction: a preamble that starts binding a new name reserves it without
    a second list needing to be updated in step.

    Every binding form a preamble can plausibly use is covered, not only the
    ones present today. Under ``mypy --strict`` an annotated assignment is the
    likeliest way a new constant arrives, and it is the form a naive
    ``ast.Assign``-only walk silently misses.

    Nested bindings are deliberately not collected: a name assigned inside a
    function or a class body is not a module-level name and cannot collide with
    an export.

    :param source: Python source. Must parse; a generated preamble that does
        not is a defect worth raising on rather than silently under-reporting.
    :raises SyntaxError: if ``source`` does not parse.
    """
    bound: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                bound.add(node.target.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            # ``import a.b`` binds ``a``; ``import a.b as c`` binds ``c``.
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return frozenset(bound)


def render_block_template(template: str, *blocks: str) -> str:
    """Dedent ``template``, then substitute ``blocks`` for its placeholders.

    ``textwrap.dedent`` measures the string *after* interpolation, so dropping a
    multi-line block straight into an indented template makes the block's own
    indentation the common prefix and strips the template down to it -- the
    emitted file then begins with the template's leftover indent and does not
    parse. It only misbehaves once the block has a second line, which is why the
    single-item case looks correct and the defect survives review. Dedenting
    against a one-line token and substituting afterwards keeps the template's
    indentation and the block's independent of each other.

    :param template: An f-string carrying one :data:`DEDENT_BLOCK` token per
        block, each written at the template's own indentation.
    :param blocks: Replacement text, substituted into the tokens left to right.
        A block may be empty, which leaves the token's line blank.
    """
    rendered = textwrap.dedent(template)
    for block in blocks:
        rendered = rendered.replace(DEDENT_BLOCK, block, 1)
    return rendered


@dataclass(frozen=True)
class WriterOption:
    """Specification of a writer-configurable option or parameter."""

    name: str
    description: str
    default: Any = None
    choices: tuple[str, ...] | None = None
    type: type = str

    def coerce(self, val: Any) -> Any:
        """Coerce a raw value (e.g. from CLI string) to this option's expected type."""
        if val is None:
            return None
        if self.type is bool:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                lower = val.strip().lower()
                if lower in ("true", "1", "yes", "on"):
                    return True
                elif lower in ("false", "0", "no", "off"):
                    return False
                raise ValueError(f"Cannot coerce {val!r} to bool for option {self.name!r} (expected 'true'/'false')")
            return bool(val)
        if self.type is int:
            if isinstance(val, int) and not isinstance(val, bool):
                return val
            return int(val)
        if self.type is float:
            if isinstance(val, int | float) and not isinstance(val, bool):
                return float(val)
            return float(val)
        if self.type is str:
            return str(val) if not isinstance(val, str) else val
        if self.type is list:
            if isinstance(val, list):
                return val
            if isinstance(val, set | frozenset):
                # Sorted, not iteration-ordered: an unordered value would otherwise
                # reorder generated output between runs and defeat regenerate-and-diff.
                #
                # Sorted by the string form, because the members need not be
                # mutually comparable: `sorted({1, "a"})` is a TypeError, where
                # `list()` -- what this replaced -- accepted it and returned an
                # order that changed between runs.
                return sorted(val, key=str)
            if isinstance(val, tuple):
                return list(val)
            if isinstance(val, str):
                return [val]
            return [val]
        return self.type(val)


def coerce_writer_options(
    options: dict[str, Any],
    supported_options: tuple[WriterOption, ...] | list[WriterOption],
) -> dict[str, Any]:
    """Coerce option values using the given supported WriterOption specifications."""
    if not supported_options:
        return dict(options)
    specs = {opt.name: opt for opt in supported_options}
    result: dict[str, Any] = {}
    for k, v in options.items():
        if k in specs:
            opt = specs[k]
            if opt.type is list:
                result[k] = opt.coerce(v)
            elif isinstance(v, list):
                result[k] = [opt.coerce(elem) for elem in v]
            else:
                result[k] = opt.coerce(v)
        else:
            result[k] = v
    return result


def split_template_args(arg_str: str) -> list[str]:
    """Split top-level comma-separated template arguments respecting nested <...>, [...], and (...)."""
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in arg_str:
        if char in "<([":
            depth += 1
            current.append(char)
        elif char in ">)]":
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


def canonicalize_type_brackets(
    type_str: str,
    normalizer: Callable[[str], str] | None = None,
) -> str:
    """Recursively canonicalize generic and template brackets ([...] and <...>)."""
    clean = type_str.strip()
    if not clean:
        return clean

    for open_b, close_b in (("[", "]"), ("<", ">")):
        if open_b in clean and clean.endswith(close_b):
            idx = clean.find(open_b)
            base = clean[:idx].strip()
            if normalizer:
                base = normalizer(base)
            inner = clean[idx + 1 : -1].strip()
            if inner:
                args = split_template_args(inner)
                canon_args = [canonicalize_type_brackets(a, normalizer) for a in args]
                return f"{base}{open_b}{', '.join(canon_args)}{close_b}"
            return f"{base}{open_b}{close_b}"

    return normalizer(clean) if normalizer else clean


class BaseWriter:
    """Base class providing unified layout generation for all output writers."""

    name: str = ""
    format_description: str = ""
    default_output_pattern: str = "{dir}/{stem}.txt"
    default_extension: str = ".txt"
    file_extensions: ClassVar[tuple[str, ...] | None] = None
    comment_prefix: ClassVar[str] = "#"
    language_aliases: ClassVar[tuple[str, ...]] = ()
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = ()

    @staticmethod
    def _platform_to_python(tag: str) -> str:
        """Map a target triple or OS tag to its Python ``sys.platform`` identifier."""
        lower = tag.lower()
        if "darwin" in lower or "apple" in lower or "mac" in lower:
            return "darwin"
        if "linux" in lower:
            return "linux"
        if "windows" in lower or "msvc" in lower or "win32" in lower:
            return "win32"
        return "posix"

    def format_platform_condition(self, cond: PlatformCondition) -> str:
        """Format a language-agnostic :class:`PlatformCondition` for this writer's target language."""
        raw_tags = [tag for term in cond.terms for tag in term] if cond.terms else sorted(cond.tags)
        py_plats = tuple(sorted({self._platform_to_python(t) for t in raw_tags if t}))
        if len(py_plats) > 1:
            return f"sys.platform in {py_plats!r}"
        if py_plats:
            return f"sys.platform == {py_plats[0]!r}"
        return "True"

    def format_matrix_condition(
        self,
        cond: MatrixCondition,
        domain_matrix: dict[str, set[Any]] | None = None,
    ) -> str:
        """Format a language-agnostic :class:`MatrixCondition` for this writer's target language."""
        from headerkit.reconcile import minimize_conditions

        effective = cond
        if domain_matrix and cond.raw_conditions and cond.is_tautology:
            effective = minimize_conditions(cond.raw_conditions, domain_matrix)

        clauses: list[str] = []
        platforms = effective.platforms
        if not platforms and cond.raw_conditions and not domain_matrix:
            platforms = tuple(sorted({str(c["target"]) for c in cond.raw_conditions if "target" in c}))
        if platforms:
            py_plats = tuple(sorted({self._platform_to_python(p) for p in platforms}))
            if len(py_plats) > 1:
                clauses.append(f"sys.platform in {py_plats!r}")
            elif py_plats:
                clauses.append(f"sys.platform == {py_plats[0]!r}")

        if effective.version_op == "==":
            clauses.append(f"version == {effective.version_min!r}")
        elif effective.version_op == ">=":
            clauses.append(f"version >= {effective.version_min!r}")
        elif effective.version_op == "range":
            clauses.append(f"({effective.version_min!r} <= version <= {effective.version_max!r})")

        if effective.feature_implicants:
            imp_clauses: list[str] = []
            for imp in effective.feature_implicants:
                term_parts = [feat if val else f"not {feat}" for feat, val in imp]
                if term_parts:
                    imp_clauses.append(" and ".join(term_parts))
            if len(imp_clauses) > 1:
                clauses.append(f"({' or '.join(f'({c})' if ' ' in c else c for c in imp_clauses)})")
            elif imp_clauses:
                clauses.append(imp_clauses[0])

        return " and ".join(clauses) if clauses else "True"

    def format_guard_condition(
        self,
        cond: PlatformCondition | MatrixCondition,
        domain_matrix: dict[str, set[Any]] | None = None,
    ) -> str:
        """Format either a :class:`PlatformCondition` or :class:`MatrixCondition`."""
        from headerkit.reconcile import MatrixCondition, PlatformCondition

        if isinstance(cond, PlatformCondition):
            return self.format_platform_condition(cond)
        if isinstance(cond, MatrixCondition):
            return self.format_matrix_condition(cond, domain_matrix=domain_matrix)
        return "True"

    #: Whether this writer reconstructs record layout itself and therefore needs
    #: unnamed-bitfield padding. Only the ctypes writer does; a writer that emits
    #: C source hands layout back to the C compiler and must not gain a spurious
    #: nameless member. The safe default is to strip padding before rendering.
    consumes_padding_fields: ClassVar[bool] = False

    def write_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> ProjectLayout:
        """Convert parsed unit IR into a complete ProjectLayout."""
        opts = options or ScaffoldOptions(target_language=self.name, layout="file")
        if not opts.target_language:
            opts.target_language = self.name
        if opts.layout not in self.supported_layouts:
            raise ValueError(
                f"Writer '{self.name}' does not support layout '{opts.layout}'. "
                f"Supported layouts: {list(self.supported_layouts)}"
            )

        if self.supported_options and opts.options:
            opts.options = coerce_writer_options(opts.options, self.supported_options)

        try:
            unit = self._prepare(unit, options=opts)
        except TypeError:
            unit = self._prepare(unit)

        return self._write_single_file_layout(unit, opts)

    def _write_single_file_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Default single file layout: one file containing the rendered output."""
        content = self._render(unit)
        filename = f"{options.package_name}{self.default_extension}"
        return ProjectLayout(files=[OutputFile(path=filename, content=content)])

    min_access_floor: ClassVar[str | None] = None
    filter_bases_by_access_floor: ClassVar[bool] = True

    def _prepare(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> SourceUnit | Header:
        """Normalize IR before rendering.

        Applies access floor filtering when :attr:`min_access_floor` or
        ``options.options["access_floor"]`` is configured, and strips unnamed
        bitfield padding unless :attr:`consumes_padding_fields` is True.

        Access filtering can be overridden per invocation via:
        - ``options.options["access_floor"] = "all"`` (or ``"private"``) to keep all symbols.
        - Subclassing with ``min_access_floor = None``.
        """
        floor = self.min_access_floor
        if options and options.options and "access_floor" in options.options:
            raw_floor = options.options["access_floor"]
            floor = str(raw_floor) if raw_floor is not None else None

        if floor and floor.lower() not in ("private", "all", "none"):
            unit = filter_access_floor(
                unit,
                floor=floor,
                filter_bases=self.filter_bases_by_access_floor,
            )
        if not self.consumes_padding_fields:
            return StripPaddingFields().transform(unit)
        return unit

    def get_overload_signature(
        self,
        func: Function,
        normalizer: Callable[[str], str] | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Compute an overload-safe canonical signature tuple (name, (param_type_1, ...)).

        Recursively canonicalizes generic brackets ([...] and <...>) and applies
        the optional type normalizer to each parameter type.
        """
        param_types: list[str] = []
        for p in func.parameters:
            pt = str(p.type)
            canon_pt = canonicalize_type_brackets(pt, normalizer)
            param_types.append(canon_pt)
        return (func.name, tuple(param_types))

    def disambiguate_anonymous_enums(
        self,
        unit: SourceUnit | Header,
        func_names: set[str] | None = None,
        fallback_suffix: str = "_val",
    ) -> SourceUnit | Header:
        """Disambiguate anonymous enum constant names that collide with functions or outer scopes."""
        from dataclasses import replace

        def _process_enum(e: Enum) -> Enum:
            is_anon = not e.name or "(unnamed" in e.name or "(anonymous" in e.name or e.name.startswith("enum (")
            if not is_anon:
                return e
            ns_prefix = ""
            if e.namespace:
                ns_parts = [p.strip() for p in e.namespace.split("::") if p.strip()]
                if len(ns_parts) > 1 and ns_parts[-1] != "std":
                    ns_prefix = f"{ns_parts[-1]}_"
            new_values: list[EnumValue] = []
            for v in e.values:
                v_name = v.name
                if func_names and v_name in func_names:
                    new_name = f"{ns_prefix}{v_name}" if ns_prefix else f"{v_name}{fallback_suffix}"
                    new_values.append(replace(v, name=new_name))
                else:
                    new_values.append(v)
            return replace(e, values=new_values)

        def _process_struct(s: Struct) -> Struct:
            new_nested: list[Struct] = [_process_struct(nr) for nr in s.nested_records]
            return replace(s, nested_records=new_nested)

        new_decls: list[Declaration] = []
        for decl in unit.declarations:
            if isinstance(decl, Enum):
                new_decls.append(_process_enum(decl))
            elif isinstance(decl, Struct):
                new_decls.append(_process_struct(decl))
            else:
                new_decls.append(decl)
        return replace(unit, declarations=new_decls)

    def _render(self, unit: SourceUnit | Header) -> str:
        """Render unit to string representation. Subclasses implement this."""
        raise NotImplementedError

    def write(self, header: Header | SourceUnit) -> str:
        """Convert parsed header IR to the target output format.

        Delegates to write_layout(layout='file') to satisfy the Zero-Dual-System Rule.
        """
        layout = self.write_layout(header, ScaffoldOptions(package_name="output", layout="file"))
        return layout.files[0].content
