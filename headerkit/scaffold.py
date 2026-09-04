"""Polyglot project scaffolding engine with unified layout and BYOScaffolder architecture."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from headerkit.hooks import HookDispatcher, Priority, hook
from headerkit.ir import Declaration, Function, Header, SourceUnit


@dataclass(frozen=True)
class OutputFile:
    """A single file to be written in a project layout."""

    path: str
    content: str
    is_executable: bool = False


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
        written: list[Path] = []
        base = Path(target_dir).resolve()
        for f in self.files:
            p = (base / f.path).resolve()
            if not p.is_relative_to(base):
                raise ValueError(f"Path traversal detected: {f.path}")
            p.parent.mkdir(parents=True, exist_ok=True)
            if not overwrite and p.exists():
                continue
            p.write_text(f.content, encoding="utf-8")
            if f.is_executable:
                p.chmod(p.stat().st_mode | 0o111)
            written.append(p)
        return written


@dataclass
class ScaffoldOptions:
    """Configuration options for project scaffolding."""

    package_name: str = "bindings"
    target_language: str = "nim"
    layout: str = "file"  # "file", "package", "project"
    test_type: str = "both"  # "both", "tripwire", "unit", "none"
    test_runner: str = "tripwire"
    interactive: bool = False
    extra_context: dict[str, Any] = field(default_factory=dict)


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
    }

    def scaffold(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        """Generate project layout using built-in templates."""
        from headerkit.writers import get_writer

        target = options.target_language.lower()
        writer = get_writer(target)
        if hasattr(writer, "write_layout"):
            return writer.write_layout(unit, options)

        if options.layout == "file":
            return self._scaffold_single_file(unit, options, target)

        if target == "nim":
            return self._scaffold_nim(unit, options)
        elif target == "mojo":
            return self._scaffold_mojo(unit, options)
        elif target in ("ctypes", "cffi", "python"):
            return self._scaffold_python(unit, options)

        return self._scaffold_single_file(unit, options, target)

    def _scaffold_single_file(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
        target: str,
    ) -> ProjectLayout:
        from headerkit.writers import get_writer

        writer = get_writer(target)
        rendered = writer.write(unit)
        ext = self._EXT_MAP.get(target, ".txt")
        filename = f"{options.package_name}{ext}"
        return ProjectLayout(files=[OutputFile(path=filename, content=rendered)])

    def _extract_fn_names(self, unit: SourceUnit | Header) -> list[str]:
        decls: list[Declaration] = getattr(unit, "declarations", [])
        return [d.name for d in decls if isinstance(d, Function) and d.name]

    def _scaffold_nim(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        from headerkit.writers import get_writer

        return get_writer("nim").write_layout(unit, options)

    def _scaffold_mojo(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        from headerkit.writers import get_writer

        return get_writer("mojo").write_layout(unit, options)

    def _scaffold_python(self, unit: SourceUnit | Header, options: ScaffoldOptions) -> ProjectLayout:
        from headerkit.writers import get_writer

        target = "cffi" if options.target_language.lower() == "cffi" else "ctypes"
        return get_writer(target).write_layout(unit, options)


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

    ctx = context or PipelineContext(writer=options.target_language)
    dispatcher = HookDispatcher()
    result = dispatcher.first_result("scaffold_project", unit, options, context=ctx)
    if isinstance(result, ProjectLayout):
        return result
    return StdlibScaffolder().scaffold(unit, options)


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
            test_type=test_type,
            test_runner=opts.test_runner,
            interactive=True,
            extra_context=opts.extra_context,
        )
    except (EOFError, KeyboardInterrupt):
        return opts
