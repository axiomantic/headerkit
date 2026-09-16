"""Scaffolding generator for HeaderKit extensions (writers, backends, and plugins)."""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from headerkit.scaffold import OutputFile, ProjectLayout

_KNOWN_EXTENSIONS: dict[str, str] = {
    "zig": ".zig",
    "rust": ".rs",
    "rs": ".rs",
    "go": ".go",
    "swift": ".swift",
    "odin": ".odin",
    "d": ".d",
    "julia": ".jl",
    "jl": ".jl",
    "nim": ".nim",
    "mojo": ".mojo",
    "python": ".py",
    "py": ".py",
    "csharp": ".cs",
    "cs": ".cs",
    "java": ".java",
    "kotlin": ".kt",
    "kt": ".kt",
    "ruby": ".rb",
    "rb": ".rb",
    "lua": ".lua",
    "cpp": ".cpp",
    "c": ".c",
    "json": ".json",
}


@dataclass
class PluginScaffoldOptions:
    """Configuration options for scaffolding a HeaderKit extension project."""

    plugin_type: str = "writer"  # "writer", "backend", "plugin"
    name: str = "headerkit-writer-custom"
    target: str = ""
    package_name: str = ""
    description: str = ""
    author: str = "Author Name"
    author_email: str = "author@example.com"
    output_extension: str = ".txt"
    destination: Path = field(default_factory=lambda: Path("."))
    interactive: bool = False

    def __post_init__(self) -> None:
        self.normalize()

    def normalize(self) -> None:
        """Normalize names, identifiers, and default values."""
        self.plugin_type = self.plugin_type.strip().lower()
        if self.plugin_type not in ("writer", "backend", "plugin"):
            raise ValueError(f"Unsupported plugin_type '{self.plugin_type}'. Must be 'writer', 'backend', or 'plugin'.")

        raw_name = self.name.strip()
        if not raw_name:
            raw_name = f"headerkit-{self.plugin_type}-custom"

        # Determine target if not specified
        if not self.target:
            prefix = f"headerkit-{self.plugin_type}-"
            if raw_name.startswith(prefix):
                self.target = raw_name[len(prefix) :]
            elif raw_name.startswith("headerkit-"):
                self.target = raw_name[len("headerkit-") :]
            else:
                self.target = raw_name

        self.target = re.sub(r"[^a-zA-Z0-9_]", "_", self.target).strip("_").lower()
        if not self.target:
            self.target = "custom"

        # Ensure project name follows convention
        if not raw_name.startswith("headerkit-"):
            self.name = f"headerkit-{self.plugin_type}-{self.target.replace('_', '-')}"
        else:
            self.name = raw_name

        # Ensure package_name is a valid Python identifier
        if not self.package_name:
            self.package_name = self.name.replace("-", "_")
        self.package_name = re.sub(r"[^a-zA-Z0-9_]", "_", self.package_name).strip("_").lower()

        # Default output extension for writers
        if self.plugin_type == "writer":
            if not self.output_extension or self.output_extension == ".txt":
                self.output_extension = _KNOWN_EXTENSIONS.get(self.target, ".txt")
            if not self.output_extension.startswith("."):
                self.output_extension = f".{self.output_extension}"

        # Default description
        if not self.description:
            if self.plugin_type == "writer":
                self.description = f"HeaderKit writer backend generating {self.target.title()} bindings"
            elif self.plugin_type == "backend":
                self.description = f"HeaderKit parser backend for {self.target.title()}"
            else:
                self.description = f"HeaderKit plugin for {self.target.title()}"


def prompt_plugin_scaffold_options(
    defaults: PluginScaffoldOptions | None = None,
    is_tty: bool | None = None,
) -> PluginScaffoldOptions:
    """Prompt user interactively for plugin scaffolding configuration."""
    opts = defaults or PluginScaffoldOptions()
    tty_active = is_tty if is_tty is not None else (sys.stdin.isatty() and sys.stdout.isatty())

    if not tty_active:
        opts.normalize()
        return opts

    try:
        raw_type = input(f"Plugin type (writer, backend, plugin) [{opts.plugin_type}]: ").strip()
        plugin_type = raw_type or opts.plugin_type

        raw_target = input(f"Target identifier or language [{opts.target or 'custom'}]: ").strip()
        target = raw_target or opts.target or "custom"

        default_name = f"headerkit-{plugin_type}-{target.replace('_', '-')}"
        raw_name = input(f"Project name [{opts.name or default_name}]: ").strip()
        name = raw_name or opts.name or default_name

        raw_desc = input(f"Description [{opts.description or f'HeaderKit {plugin_type} for {target}'}]: ").strip()
        description = raw_desc or opts.description

        raw_author = input(f"Author name [{opts.author}]: ").strip()
        author = raw_author or opts.author

        raw_email = input(f"Author email [{opts.author_email}]: ").strip()
        author_email = raw_email or opts.author_email

        output_ext = opts.output_extension
        if plugin_type == "writer":
            def_ext = _KNOWN_EXTENSIONS.get(target.lower(), ".txt")
            raw_ext = input(f"Default file extension [{def_ext}]: ").strip()
            output_ext = raw_ext or def_ext

        res = PluginScaffoldOptions(
            plugin_type=plugin_type,
            name=name,
            target=target,
            description=description,
            author=author,
            author_email=author_email,
            output_extension=output_ext,
            destination=opts.destination,
            interactive=True,
        )
        res.normalize()
        return res
    except (EOFError, KeyboardInterrupt):
        opts.normalize()
        return opts


def _class_name_for(target: str, plugin_type: str) -> str:
    words = [w.capitalize() for w in re.split(r"[^a-zA-Z0-9]", target) if w]
    base = "".join(words) or "Custom"
    suffix = plugin_type.capitalize()
    if not base.endswith(suffix):
        base += suffix
    return base


def _render_pyproject_toml(options: PluginScaffoldOptions) -> str:
    entry_group = {
        "writer": "headerkit.writers",
        "backend": "headerkit.backends",
        "plugin": "headerkit.hooks",
    }[options.plugin_type]

    module_entry = {
        "writer": f"{options.package_name}.writer",
        "backend": f"{options.package_name}.backend",
        "plugin": f"{options.package_name}.plugin",
    }[options.plugin_type]

    return textwrap.dedent(f"""\
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [project]
        name = "{options.name}"
        version = "0.1.0"
        description = "{options.description}"
        readme = "README.md"
        requires-python = ">=3.10"
        license = "MIT"
        authors = [
            {{ name = "{options.author}", email = "{options.author_email}" }},
        ]
        dependencies = [
            "headerkit>=0.44.0",
        ]

        [project.optional-dependencies]
        test = [
            "pytest>=7.0.0",
            "pytest-timeout>=2.3.0",
        ]
        lint = [
            "ruff>=0.4.0",
            "mypy>=1.10.0",
        ]

        [project.entry-points."{entry_group}"]
        {options.target} = "{module_entry}"

        [tool.ruff]
        line-length = 120
        target-version = "py310"

        [tool.mypy]
        python_version = "3.10"
        strict = true
        warn_return_any = true
        warn_unused_configs = true
    """)


def _render_writer_impl(options: PluginScaffoldOptions) -> str:
    cls_name = _class_name_for(options.target, "writer")
    return textwrap.dedent(f"""\
        \"\"\"{options.description}.\"\"\"

        from __future__ import annotations

        from typing import ClassVar

        from headerkit.hooks import Priority, hook
        from headerkit.ir import Function, Header, SourceUnit, Struct
        from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
        from headerkit.writers import register_writer
        from headerkit.writers.base import BaseWriter, WriterOption


        class {cls_name}(BaseWriter):
            \"\"\"Writer backend generating {options.target} code from HeaderKit IR.\"\"\"

            name: str = "{options.target}"
            format_description: str = "{options.target.title()} source code"
            default_output_pattern: str = "{{dir}}/{{stem}}{options.output_extension}"
            default_extension: str = "{options.output_extension}"
            supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package")
            supported_options: ClassVar[tuple[WriterOption, ...]] = (
                WriterOption(
                    "package_name",
                    type=str,
                    default="{options.target}_bindings",
                    description="Name of the generated package or module",
                ),
            )

            def _render(self, unit: SourceUnit | Header) -> str:
                \"\"\"Render single-file bindings for the parsed source unit.\"\"\"
                lines: list[str] = [
                    f"// Generated by HeaderKit {options.target} writer",
                    f"// Source: {{unit.path}}",
                    "",
                ]
                for decl in unit.declarations:
                    if isinstance(decl, Struct):
                        lines.append(f"// Struct: {{decl.name}}")
                    elif isinstance(decl, Function):
                        lines.append(f"// Function: {{decl.name}}")
                return "\\n".join(lines) + "\\n"

            def _write_package_layout(
                self,
                unit: SourceUnit | Header,
                options: ScaffoldOptions,
            ) -> ProjectLayout:
                \"\"\"Generate full package layout including library and build files.\"\"\"
                code = self._render(unit)
                src_file = f"src/{{options.package_name}}{{self.default_extension}}"
                return ProjectLayout(
                    files=[
                        OutputFile(path=src_file, content=code),
                    ]
                )


        @hook("write_output", priority=Priority.STANDARD)
        def _write_hook(
            unit: SourceUnit | Header,
            options: ScaffoldOptions | None = None,
            **kwargs: object,
        ) -> str | None:
            if options and options.target_language == "{options.target}":
                writer = {cls_name}()
                return writer.write(unit)
            return None


        @hook("get_writer", priority=Priority.STANDARD)
        def _get_writer_hook(name: str, **kwargs: object) -> BaseWriter | None:
            if name == "{options.target}":
                return {cls_name}()
            return None


        # Register writer in registry on import
        register_writer({cls_name}())
    """)


def _render_backend_impl(options: PluginScaffoldOptions) -> str:
    cls_name = _class_name_for(options.target, "backend")
    return textwrap.dedent(f"""\
        \"\"\"{options.description}.\"\"\"

        from __future__ import annotations

        from pathlib import Path

        from headerkit.backends import register_backend
        from headerkit.hooks import Priority, hook
        from headerkit.ir import CType, Function, Header, Parameter, ParserBackend, SourceUnit


        class {cls_name}(ParserBackend):
            \"\"\"Parser backend converting {options.target} headers into HeaderKit IR.\"\"\"

            name: str = "{options.target}"

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
                allowlist: list[str] | None = None,
                denylist: list[str] | None = None,
            ) -> Header:
                \"\"\"Parse code into HeaderKit IR.

                NOTE: HeaderKit strictly prohibits regex-based AST extraction.
                Implement this using formal grammar parsers (e.g. Tree-sitter, libclang, compiler bindings).
                \"\"\"
                # Minimal starter demonstration returning SourceUnit container
                declarations = []
                if code.strip():
                    declarations.append(
                        Function(
                            name="init",
                            return_type=CType(name="void"),
                            parameters=[],
                        )
                    )
                return SourceUnit(
                    path=filename,
                    declarations=declarations,
                    language="c",
                    classification="header",
                )


        @hook("parse_unit", priority=Priority.STANDARD)
        def _parse_hook(
            spec: object,
            context: object = None,
            **kwargs: object,
        ) -> SourceUnit | None:
            backend_name = getattr(context, "backend", None) if context else None
            if backend_name == "{options.target}":
                path_str = getattr(spec, "path", str(spec))
                code = Path(path_str).read_text(encoding="utf-8") if Path(path_str).exists() else ""
                backend = {cls_name}()
                return backend.parse(code, str(path_str))
            return None


        @hook("get_backend", priority=Priority.STANDARD)
        def _get_backend_hook(name: str, **kwargs: object) -> ParserBackend | None:
            if name == "{options.target}":
                return {cls_name}()
            return None


        # Register backend in registry on import
        register_backend("{options.target}", {cls_name})
    """)


def _render_plugin_impl(options: PluginScaffoldOptions) -> str:
    return textwrap.dedent(f"""\
        \"\"\"{options.description}.\"\"\"

        from __future__ import annotations

        from headerkit.hooks import PipelineContext, Priority, hook
        from headerkit.ir import Header, SourceUnit
        from headerkit.scaffold import ProjectLayout, ScaffoldOptions


        @hook("transform_unit", priority=Priority.STANDARD)
        def transform_unit_hook(
            unit: SourceUnit | Header,
            context: PipelineContext | None = None,
            **kwargs: object,
        ) -> SourceUnit | Header:
            \"\"\"Post-process or enrich SourceUnit IR before writer emission.\"\"\"
            # Add custom transformations or metadata annotations here
            return unit


        @hook("transform_layout", priority=Priority.STANDARD)
        def transform_layout_hook(
            layout: ProjectLayout,
            unit: SourceUnit | Header,
            options: ScaffoldOptions,
            context: PipelineContext | None = None,
            **kwargs: object,
        ) -> ProjectLayout:
            \"\"\"Post-process scaffolded project layout files.\"\"\"
            # Add or modify files in layout.files before writing to disk
            return layout
    """)


def _render_init_py(options: PluginScaffoldOptions) -> str:
    if options.plugin_type == "writer":
        cls_name = _class_name_for(options.target, "writer")
        return textwrap.dedent(f"""\
            from {options.package_name}.writer import {cls_name}

            __all__ = ["{cls_name}"]
        """)
    elif options.plugin_type == "backend":
        cls_name = _class_name_for(options.target, "backend")
        return textwrap.dedent(f"""\
            from {options.package_name}.backend import {cls_name}

            __all__ = ["{cls_name}"]
        """)
    else:
        return textwrap.dedent(f"""\
            # HeaderKit plugin: {options.name}
            from {options.package_name} import plugin

            __all__ = ["plugin"]
        """)


def _render_conftest_py() -> str:
    return textwrap.dedent("""\
        from typing import Generator

        import pytest
        from headerkit.hooks import HookRegistry


        @pytest.fixture(autouse=True)
        def isolate_registry() -> Generator[None, None, None]:
            \"\"\"Save and restore global HookRegistry state for clean test isolation.\"\"\"
            snap = HookRegistry.snapshot()
            try:
                yield
            finally:
                HookRegistry.restore(snap)
    """)


def _render_tests(options: PluginScaffoldOptions) -> str:
    if options.plugin_type == "writer":
        cls_name = _class_name_for(options.target, "writer")
        return textwrap.dedent(f"""\
            from __future__ import annotations

            import pytest
            from headerkit.ir import CType, Function, Parameter, SourceUnit, Struct
            from headerkit.writers import get_writer
            from {options.package_name}.writer import {cls_name}


            def _sample_unit() -> SourceUnit:
                return SourceUnit(
                    path="sample.h",
                    declarations=[
                        Function(
                            name="add_numbers",
                            return_type=CType(name="int"),
                            parameters=[
                                Parameter(name="a", type=CType(name="int")),
                                Parameter(name="b", type=CType(name="int")),
                            ],
                        ),
                        Struct(name="Point"),
                    ],
                )


            def test_writer_registered() -> None:
                \"\"\"Verify the writer is registered and retrievable via get_writer.\"\"\"
                writer = get_writer("{options.target}")
                assert isinstance(writer, {cls_name})
                assert writer.name == "{options.target}"


            def test_writer_renders_symbols() -> None:
                \"\"\"Verify rendering output includes expected symbol definitions.\"\"\"
                writer = {cls_name}()
                output = writer.write(_sample_unit())
                assert len(output) > 0
                assert "add_numbers" in output
                assert "Point" in output


            def test_writer_write_layout() -> None:
                \"\"\"Verify write_layout emits files matching default extension.\"\"\"
                writer = {cls_name}()
                layout = writer.write_layout(_sample_unit())
                assert len(layout.files) >= 1
                assert any(f.path.endswith(writer.default_extension) for f in layout.files)
        """)
    elif options.plugin_type == "backend":
        cls_name = _class_name_for(options.target, "backend")
        return textwrap.dedent(f"""\
            from __future__ import annotations

            import pytest
            from headerkit.backends import get_backend
            from {options.package_name}.backend import {cls_name}


            def test_backend_registered() -> None:
                \"\"\"Verify backend is retrievable via get_backend.\"\"\"
                backend = get_backend("{options.target}")
                assert isinstance(backend, {cls_name})
                assert backend.name == "{options.target}"


            def test_backend_parses_simple_code() -> None:
                \"\"\"Verify backend parses code and produces a valid SourceUnit.\"\"\"
                backend = {cls_name}()
                unit = backend.parse("void init(void);", "test.h")
                assert unit is not None
                assert unit.path == "test.h"
                assert len(unit.declarations) > 0
        """)
    else:
        return textwrap.dedent(f"""\
            from __future__ import annotations

            import pytest
            from headerkit.hooks import HookDispatcher, PipelineContext
            from headerkit.ir import SourceUnit
            from {options.package_name} import plugin


            def test_plugin_hooks_registered() -> None:
                \"\"\"Verify plugin transform_unit hook is executed by dispatcher.\"\"\"
                unit = SourceUnit(path="test.h", declarations=[])
                dispatcher = HookDispatcher()
                transformed = dispatcher.waterfall("transform_unit", unit)
                assert transformed is not None
                assert transformed.path == "test.h"
        """)


def _render_agents_md(options: PluginScaffoldOptions) -> str:
    cls_name = _class_name_for(options.target, options.plugin_type)
    return textwrap.dedent(f"""\
        # Agents

        Instructions for AI coding agents working on `{options.name}`.

        ## Overview

        `{options.name}` is a HeaderKit {options.plugin_type} extension providing `{options.target}` support.
        Primary implementation class: `{cls_name}`.

        ## Quality Invariants & Anti-Green-Mirage Discipline

        1. **Zero Tautological Assertions**:
           Never write or accept tests with vacuous assertions like `assert True` or bare prints.
           Every test must inspect generated code, parsed AST symbols, types, or return values.
        2. **Strict Prohibition Against Regex AST Extraction** (for backends):
           Context-free grammars cannot be parsed by regular languages.
           All parser backends MUST use formal parser grammars (Tree-sitter, libclang, or compiler bindings).
        3. **Registry Pattern & Self-Registration**:
           HeaderKit uses the unified hook engine (`headerkit.hooks`) for backend and writer discovery.
           Maintain the managed self-registration pattern (`register_{options.plugin_type}(...)`).

        ## Quality Gates

        All code must pass:
        - `bash scripts/ci/lint.sh` (`ruff check .`, `ruff format --check .`, `mypy --strict {options.package_name}`)
        - `bash scripts/ci/test.sh` (`pytest -rs`)
    """)


def _render_readme_md(options: PluginScaffoldOptions) -> str:
    return textwrap.dedent(f"""\
        # {options.name}

        {options.description}.

        ## Installation

        ```bash
        pip install -e '.[test,lint]'
        ```

        ## Usage

        Once installed, HeaderKit will automatically discover this {options.plugin_type} via Python entry points.

        ```bash
        # Command-line usage
        headerkit input.h --{options.plugin_type} {options.target}
        ```

        ```python
        # Python API usage
        from headerkit.{options.plugin_type}s import get_{options.plugin_type}

        ext = get_{options.plugin_type}("{options.target}")
        ```

        ## Testing

        ```bash
        bash scripts/ci/lint.sh
        bash scripts/ci/test.sh
        ```
    """)


def _render_ci_lint(options: PluginScaffoldOptions) -> str:
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        echo "=== Running Lint Checks ==="
        ruff check .
        ruff format --check .
        mypy --strict {options.package_name}
        echo "=== All Lint Checks Passed ==="
    """)


def _render_ci_test() -> str:
    return textwrap.dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail

        echo "=== Running Test Suite ==="
        pytest -rs "${@}"
        echo "=== All Tests Passed ==="
    """)


def _render_ci_workflow(_options: PluginScaffoldOptions) -> str:
    return textwrap.dedent("""\
        name: CI

        on:
          push:
            branches: [main]
          pull_request:

        jobs:
          lint:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12"
                  cache: "pip"
              - run: pip install -e '.[lint]'
              - run: bash scripts/ci/lint.sh

          test:
            runs-on: ${{ matrix.os }}
            strategy:
              fail-fast: false
              matrix:
                os: [ubuntu-latest, macos-latest]
                python-version: ["3.10", "3.12", "3.14"]
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
                with:
                  python-version: ${{ matrix.python-version }}
                  cache: "pip"
              - run: pip install -e '.[test]'
              - run: bash scripts/ci/test.sh
    """)


def scaffold_plugin(options: PluginScaffoldOptions) -> ProjectLayout:
    """Generate complete project layout for a HeaderKit extension."""
    options.normalize()

    impl_filename = {
        "writer": "writer.py",
        "backend": "backend.py",
        "plugin": "plugin.py",
    }[options.plugin_type]

    impl_renderer = {
        "writer": _render_writer_impl,
        "backend": _render_backend_impl,
        "plugin": _render_plugin_impl,
    }[options.plugin_type]

    files: list[OutputFile] = [
        OutputFile(path="pyproject.toml", content=_render_pyproject_toml(options)),
        OutputFile(path=f"{options.package_name}/__init__.py", content=_render_init_py(options)),
        OutputFile(path=f"{options.package_name}/{impl_filename}", content=impl_renderer(options)),
        OutputFile(path="tests/conftest.py", content=_render_conftest_py()),
        OutputFile(path=f"tests/test_{options.target}.py", content=_render_tests(options)),
        OutputFile(path="AGENTS.md", content=_render_agents_md(options)),
        OutputFile(path="README.md", content=_render_readme_md(options)),
        OutputFile(path="scripts/ci/lint.sh", content=_render_ci_lint(options), is_executable=True),
        OutputFile(path="scripts/ci/test.sh", content=_render_ci_test(), is_executable=True),
        OutputFile(path=".github/workflows/ci.yml", content=_render_ci_workflow(options)),
    ]

    return ProjectLayout(files=files)


def plugin_scaffold_main(argv: list[str] | None = None) -> int:
    """CLI entry point for scaffolding a new HeaderKit extension."""
    parser = argparse.ArgumentParser(
        prog="headerkit new",
        description="Scaffold a new HeaderKit extension project (writer, backend, or hook plugin).",
    )
    parser.add_argument(
        "--type",
        dest="plugin_type",
        choices=["writer", "backend", "plugin"],
        default="writer",
        help="Type of extension to scaffold (default: writer)",
    )
    parser.add_argument(
        "--name",
        dest="name",
        default="",
        help="Project repository name (e.g. headerkit-writer-zig)",
    )
    parser.add_argument(
        "--target",
        dest="target",
        default="",
        help="Target language or backend identifier (e.g. zig)",
    )
    parser.add_argument(
        "--desc",
        dest="description",
        default="",
        help="One-line project description",
    )
    parser.add_argument(
        "--author",
        dest="author",
        default="Author Name",
        help="Author name",
    )
    parser.add_argument(
        "--email",
        dest="author_email",
        default="author@example.com",
        help="Author email",
    )
    parser.add_argument(
        "--ext",
        dest="output_extension",
        default=".txt",
        help="Default output file extension (for writers)",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_dir",
        default="",
        help="Destination directory for scaffolded files (default: ./<project-name>)",
    )
    parser.add_argument(
        "--no-input",
        dest="no_input",
        action="store_true",
        help="Disable interactive prompts and accept CLI/default values",
    )

    args = parser.parse_args(argv)

    raw_opts = PluginScaffoldOptions(
        plugin_type=args.plugin_type,
        name=args.name,
        target=args.target,
        description=args.description,
        author=args.author,
        author_email=args.author_email,
        output_extension=args.output_extension,
    )

    resolved_opts = prompt_plugin_scaffold_options(raw_opts, is_tty=False if args.no_input else None)

    dest_dir = Path(args.output_dir) if args.output_dir else Path(resolved_opts.name)
    layout = scaffold_plugin(resolved_opts)
    written = layout.write_to_disk(dest_dir)

    print(f"headerkit: scaffolded {len(written)} files into {dest_dir}")
    return 0
