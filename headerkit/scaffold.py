"""Polyglot project scaffolding engine with unified layout and BYOScaffolder architecture."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from headerkit.hooks import HookDispatcher, Priority, hook
from headerkit.ir import Constant, Declaration, Function, Header, SourceUnit


@dataclass(frozen=True)
class OutputFile:
    """A single file to be written in a project layout."""

    path: str
    content: str
    is_executable: bool = False
    #: Never clobber this file if it already exists on disk. Set on artifacts a human
    #: is expected to edit -- work-order test stubs, the work order itself -- because a
    #: generator that eats the work it asked for is worse than no generator.
    preserve_existing: bool = False
    #: Merge strategy when file already exists on disk.
    #: Supported: None, "preserve", "append_new_tests".
    merge_strategy: str | None = None


@dataclass
class ProjectLayout:
    """A collection of output files comprising a project or package layout."""

    files: list[OutputFile] = field(default_factory=list)

    def get_file(self, path: str) -> OutputFile | None:
        """Find an output file by its relative project path."""
        for f in self.files:
            if f.path == path:
                return f
        return None

    def write_to_disk(self, target_dir: Path | str, *, overwrite: bool = True) -> list[Path]:
        """Write all files in this layout to the destination directory."""
        from headerkit.hooks import HookDispatcher, PipelineContext

        written: list[Path] = []
        base = Path(target_dir).resolve()
        dispatcher = HookDispatcher()
        for f in self.files:
            p = (base / f.path).resolve()
            if not p.is_relative_to(base):
                raise ValueError(f"Path traversal detected: {f.path}")
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                if f.merge_strategy:
                    existing = p.read_text(encoding="utf-8")
                    merged = dispatcher.first_result(
                        "merge_file",
                        existing,
                        f.content,
                        f.path,
                        context=PipelineContext(target=f.path),
                    )
                    if merged is None and f.merge_strategy in ("append_new_tests", "canonical_merge"):
                        lang = "nim" if f.path.endswith(".nim") else "python"
                        canonical = f.merge_strategy == "canonical_merge"
                        merged = merge_incremental_tests(existing, f.content, language=lang, canonicalize=canonical)
                    if merged is not None:
                        p.write_text(merged, encoding="utf-8")
                        written.append(p)
                        continue
                if f.preserve_existing or not overwrite:
                    continue
            p.write_text(f.content, encoding="utf-8")
            if f.is_executable:
                p.chmod(p.stat().st_mode | 0o111)
            written.append(p)
        return written


def extract_function_names(unit: SourceUnit | Header) -> list[str]:
    """Extract top-level function names for test stubs and tripwires."""
    decls: list[Declaration] = getattr(unit, "declarations", [])
    return [d.name for d in decls if isinstance(d, Function) and d.name]


def extract_header_version(unit: SourceUnit | Header) -> str | None:
    """Extract library version from header macros or constants without regex."""
    decls: list[Declaration] = getattr(unit, "declarations", [])
    constants = {d.name: d for d in decls if isinstance(d, Constant)}

    # Check for combined version strings e.g. FOO_VERSION "1.2.3"
    for name, c in constants.items():
        if name.endswith(("_VERSION", "_VERSION_STRING", "_VERSION_STR")):
            val = str(c.evaluated_value if c.evaluated_value is not None else (c.value or ""))
            val = val.strip("\"'")
            parts = val.split(".")
            if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
                return val

    # Check for component macros e.g. FOO_MAJOR_VERSION, FOO_MINOR_VERSION
    prefixes: set[str] = set()
    for name in constants:
        for suffix in ("_MAJOR_VERSION", "_VERSION_MAJOR"):
            if name.endswith(suffix):
                prefixes.add(name[: -len(suffix)])

    for prefix in sorted(prefixes):
        maj = constants.get(f"{prefix}_MAJOR_VERSION") or constants.get(f"{prefix}_VERSION_MAJOR")
        min_ = constants.get(f"{prefix}_MINOR_VERSION") or constants.get(f"{prefix}_VERSION_MINOR")
        patch = (
            constants.get(f"{prefix}_PATCH_LEVEL")
            or constants.get(f"{prefix}_PATCHLEVEL")
            or constants.get(f"{prefix}_VERSION_PATCH")
            or constants.get(f"{prefix}_BUILD_NUMBER")
        )
        if maj is not None and min_ is not None:
            maj_val = str(maj.evaluated_value if maj.evaluated_value is not None else maj.value)
            min_val = str(min_.evaluated_value if min_.evaluated_value is not None else min_.value)
            patch_val = (
                str(patch.evaluated_value if patch.evaluated_value is not None else patch.value)
                if patch is not None
                else "0"
            )
            return f"{maj_val}.{min_val}.{patch_val}"

    return None


def _extract_nim_tests(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Extract (preamble, [(title, block_text), ...]) for tests in Nim source without regex."""
    lines = content.splitlines(keepends=True)
    tests: list[tuple[str, str]] = []
    preamble_lines: list[str] = []
    first_test_found = False
    current_when_guard: tuple[int, str] | None = None
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            if not first_test_found:
                preamble_lines.append(line)
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        # Check if an active when guard expired due to outdent
        if current_when_guard is not None and indent <= current_when_guard[0]:
            current_when_guard = None

        # Check for when guard e.g. "when declared(...) :"
        if stripped.startswith("when ") and stripped.endswith(":"):
            current_when_guard = (indent, line)
            if not first_test_found:
                # Look ahead to see if the next non-empty line starts a test
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and lines[j].strip().startswith("test "):
                    first_test_found = True
                else:
                    preamble_lines.append(line)
            i += 1
            continue

        if stripped.startswith("test ") and ":" in stripped:
            first_test_found = True
            after_test = stripped[5:]
            colon_idx = after_test.rfind(":")
            title_part = after_test[:colon_idx].strip()
            if "{" in title_part and title_part.endswith("}"):
                title_part = title_part[: title_part.find("{")].strip()
            test_title = title_part.strip("\"'")

            test_indent = indent
            guard_line = (
                current_when_guard[1]
                if current_when_guard is not None and test_indent > current_when_guard[0]
                else None
            )
            block_lines = [guard_line] if guard_line is not None else []
            block_lines.append(line)
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.strip()
                if not next_stripped:
                    block_lines.append(next_line)
                    i += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= test_indent or (next_stripped.startswith("test ") and ":" in next_stripped):
                    break
                block_lines.append(next_line)
                i += 1
            tests.append((test_title, "".join(block_lines)))
            continue

        if not first_test_found:
            preamble_lines.append(line)
        i += 1

    preamble = "".join(preamble_lines)
    return preamble, tests


def _extract_python_tests(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Extract (preamble, [(func_name, block_text), ...]) for tests in Python source without regex."""
    lines = content.splitlines(keepends=True)
    tests: list[tuple[str, str]] = []
    preamble_lines: list[str] = []
    first_test_found = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("def test_") and "(" in stripped:
            first_test_found = True
            title = stripped[4 : stripped.find("(")].strip()
            indent = len(line) - len(line.lstrip())
            block_lines = [line]
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.strip()
                if not next_stripped:
                    block_lines.append(next_line)
                    i += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent:
                    break
                block_lines.append(next_line)
                i += 1
            tests.append((title, "".join(block_lines)))
            continue
        if not first_test_found:
            preamble_lines.append(line)
        i += 1
    preamble = "".join(preamble_lines)
    return preamble, tests


def merge_incremental_tests(
    existing: str,
    new_incoming: str,
    language: str = "nim",
    *,
    canonicalize: bool = False,
) -> str:
    """Merge newly discovered test cases into an existing test file without clobbering.

    Preserves all existing human edits, custom assertions, and test structure.
    When canonicalize=True, test cases within each suite/module are canonically ordered
    by test title, ensuring deterministic, order-independent output across multiple runs.
    """
    if language == "nim":
        existing_preamble, existing_tests = _extract_nim_tests(existing)
        new_preamble, new_tests = _extract_nim_tests(new_incoming)
    elif language in ("python", "py"):
        existing_preamble, existing_tests = _extract_python_tests(existing)
        new_preamble, new_tests = _extract_python_tests(new_incoming)
    else:
        return existing

    existing_titles = {t[0] for t in existing_tests}
    missing_tests = [t for t in new_tests if t[0] not in existing_titles]

    if not missing_tests and not canonicalize:
        return existing

    if canonicalize:
        # Collect all tests, preserving human-edited existing versions
        all_tests: dict[str, str] = {}
        for title, block in existing_tests:
            all_tests[title] = block
        for title, block in new_tests:
            if title not in all_tests:
                all_tests[title] = block

        preamble = existing_preamble if existing_preamble.strip() else new_preamble
        result = preamble.rstrip() + "\n\n"
        sorted_titles = sorted(all_tests.keys())
        result += "\n\n".join(all_tests[t].rstrip() for t in sorted_titles)
        return result.rstrip() + "\n"

    result = existing.rstrip() + "\n\n"
    for _, block in missing_tests:
        result += block.rstrip() + "\n\n"
    return result.rstrip() + "\n"


@dataclass
class ScaffoldOptions:
    """Configuration options for project scaffolding."""

    package_name: str = "bindings"
    target_language: str = "nim"
    layout: str = "file"  # Configurable layout (e.g. "file", "package", "project")
    options: dict[str, Any] = field(default_factory=dict)
    interactive: bool = False
    extra_context: dict[str, Any] = field(default_factory=dict)
    test_type: str = "both"  # Backwards compatibility alias for options["test_type"]
    test_runner: str = "tripwire"  # Backwards compatibility alias
    test_strategy: str = "incremental"  # "incremental", "preserve", "regenerate"
    version_guard: str = "declared"  # "declared", "version_gte", "compiles", "none"
    version_constant: str | None = None

    def __post_init__(self) -> None:
        if "test_type" not in self.options and self.test_type != "both":
            self.options["test_type"] = self.test_type
        elif "test_type" in self.options:
            self.test_type = str(self.options["test_type"])

    def get_option(self, name: str, default: Any = None) -> Any:
        """Get a writer-specific option value."""
        if name in self.options:
            return self.options[name]
        if hasattr(self, name):
            return getattr(self, name)
        return default


class BYOScaffolder:
    """Protocol and base class for Bring-Your-Own-Scaffolder plugins."""

    def scaffold(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        """Generate a ProjectLayout for the given SourceUnit and options."""
        raise NotImplementedError


class StdlibScaffolder(BYOScaffolder):
    """Zero-dependency standard library project scaffolder."""

    _EXT_MAP: dict[str, str] = {
        "nim": ".nim",
        "mojo": ".mojo",
        "ctypes": ".py",
        "cffi": ".py",
        "cython": ".pyx",
        "cshim": ".cpp",
        "json": ".json",
        "lua": ".lua",
        "luajit": ".lua",
        "python": ".py",
    }

    def _resolve_target(self, target_language: str) -> str:
        target = target_language.lower()
        if target == "python":
            return "ctypes"
        if target == "luajit":
            return "lua"
        return target

    def scaffold(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        """Generate project layout using built-in templates."""
        from headerkit.writers import get_writer

        writer_target = self._resolve_target(options.target_language)
        writer = get_writer(writer_target)
        if hasattr(writer, "write_layout"):
            return writer.write_layout(unit, options)

        return self._scaffold_single_file(unit, options, writer_target)

    def _scaffold_single_file(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
        target: str,
    ) -> ProjectLayout:
        from headerkit.writers import get_writer

        writer_target = self._resolve_target(target)
        writer = get_writer(writer_target)
        rendered = writer.write(unit)
        ext = self._EXT_MAP.get(target.lower(), ".txt")
        filename = f"{options.package_name}{ext}"
        return ProjectLayout(files=[OutputFile(path=filename, content=rendered)])


@hook("scaffold_project", priority=Priority.STANDARD)
def _default_scaffold(
    unit: SourceUnit | Header,
    options: ScaffoldOptions,
    _context: Any = None,
    **_kwargs: Any,
) -> ProjectLayout | None:
    """Default standard library project scaffolder hook."""
    return StdlibScaffolder().scaffold(unit, options)


def scaffold(
    unit: SourceUnit | Header,
    options: ScaffoldOptions,
    context: Any = None,
) -> ProjectLayout:
    """Scaffold a project layout for a source unit, dispatching to registered hooks."""
    from headerkit.hooks import PipelineContext

    if context is None:
        ctx = PipelineContext(
            writer=options.target_language,
            target=options.target_language,
            layout=options.layout,
            options=options.options,
        )
    else:
        if getattr(context, "layout", None) is None:
            ctx = PipelineContext(
                backend=getattr(context, "backend", None),
                writer=getattr(context, "writer", None) or options.target_language,
                target=getattr(context, "target", None) or options.target_language,
                layout=options.layout,
                language=getattr(context, "language", None),
                classification=getattr(context, "classification", None),
                runtime=getattr(context, "runtime", None),
                options=getattr(context, "options", None) or options.options,
            )
        else:
            ctx = context

    dispatcher = HookDispatcher()
    unit = dispatcher.waterfall("transform_unit", unit, context=ctx)
    result = dispatcher.first_result("scaffold_project", unit, options, context=ctx)
    if isinstance(result, ProjectLayout):
        layout = result
    else:
        layout = StdlibScaffolder().scaffold(unit, options)

    # Detect or resolve version if not already present
    if "library_version" not in options.extra_context:
        detected_version = dispatcher.first_result("resolve_version", unit, context=ctx)
        if detected_version is None:
            detected_version = extract_header_version(unit)
        if detected_version is not None:
            options.extra_context["library_version"] = detected_version

    if options.version_constant and "version_constant" not in options.extra_context:
        options.extra_context["version_constant"] = options.version_constant

    layout = dispatcher.waterfall("scaffold_tests", layout, unit, options, context=ctx)
    layout = dispatcher.waterfall("transform_layout", layout, unit, options, context=ctx)
    return layout


def prompt_scaffold_options(
    defaults: ScaffoldOptions | None = None,
    *,
    is_tty: bool | None = None,
) -> ScaffoldOptions:
    """TTY-aware interactive prompt wizard for scaffolding options."""
    opts = defaults if defaults is not None else ScaffoldOptions()
    tty_active = is_tty if is_tty is not None else (sys.stdin.isatty() and sys.stdout.isatty())

    if not tty_active:
        return opts

    try:
        raw_pkg = input(f"Package name [{opts.package_name}]: ").strip()
        pkg_name = raw_pkg or opts.package_name

        raw_lang = input(f"Target language (nim, mojo, ctypes, cffi) [{opts.target_language}]: ").strip()
        target_lang = raw_lang or opts.target_language

        raw_layout = input(f"Layout (file, package) [{opts.layout}]: ").strip()
        layout = raw_layout or opts.layout

        raw_test = input(f"Test generation (both, tripwire, unit, none) [{opts.test_type}]: ").strip()
        test_type = raw_test or opts.test_type

        return ScaffoldOptions(
            package_name=pkg_name,
            target_language=target_lang,
            layout=layout,
            options=dict(opts.options),
            test_type=test_type,
            test_runner=opts.test_runner,
            interactive=True,
            extra_context=opts.extra_context,
        )
    except (EOFError, KeyboardInterrupt):
        return opts
