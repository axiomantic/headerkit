"""Command-line interface for headerkit."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from headerkit._config import (
    HeaderkitConfig,
    find_config_file,
    load_config,
    merge_config,
)
from headerkit._generate import batch_generate, generate
from headerkit._rename import register_config_hooks as register_rename_config
from headerkit.backends import _load_backend_plugins
from headerkit.hooks import _load_hook_plugins
from headerkit.writers import _load_writer_plugins, coerce_writer_options, get_default_writer


def parse_writer_options(
    raw_opts: list[str],
    command_name: str = "headerkit",
) -> dict[str, dict[str, object]] | None:
    """Parse ``--writer-opt WRITER:KEY=VALUE`` arguments into a nested dict.

    Aggregates duplicate keys into lists and collapses single-element lists
    to plain strings. Values are coerced according to each writer's
    ``supported_options`` declarations.

    :param raw_opts: Raw ``--writer-opt`` values from argparse.
    :param command_name: Program name for error messages.
    :returns: Nested dict ``{writer: {key: value_or_list}}``, or ``None``
        when *raw_opts* is empty.
    :raises ValueError: On malformed input (missing ``:`` scope or ``=``).
    """
    if not raw_opts:
        return None

    options_lists: dict[str, dict[str, list[str]]] = {}
    for item in raw_opts:
        writer_name, sep, rest = item.partition(":")
        if not sep:
            raise ValueError(f"{command_name}: malformed --writer-opt: {item!r}; use WRITER:KEY=VALUE format")

        key, sep, value = rest.partition("=")
        if not sep:
            raise ValueError(f"{command_name}: malformed --writer-opt: {item!r}; expected WRITER:KEY=VALUE")

        options_lists.setdefault(writer_name, {}).setdefault(key, []).append(value)

    raw_dict = {
        w_name: {k: v[0] if len(v) == 1 else v for k, v in opts.items()} for w_name, opts in options_lists.items()
    }
    return {w_name: coerce_writer_options(w_name, opts) for w_name, opts in raw_dict.items()}


def _env_bool(name: str, *, default: bool = False) -> bool:
    """Read an environment variable as a boolean.

    Truthy: '1', 'true', 'yes' (case-insensitive).
    Falsy: '0', 'false', 'no', '' (case-insensitive).
    Unset: returns *default*.
    """
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")


class HeaderkitArgumentParser(argparse.ArgumentParser):
    """Argument parser supporting subcommands alongside top-level positional inputs."""

    def parse_args(  # type: ignore[override]
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        raw = sys.argv[1:] if args is None else list(args)
        if raw and raw[0] == "add-target":
            p = argparse.ArgumentParser(prog="headerkit add-target")
            p.add_argument("command", choices=["add-target"])
            p.add_argument("output_file", help="Path to existing output file containing manifest")
            p.add_argument("--target", required=True, help="Target architecture triple to add")
            p.add_argument("--lang", default=None, help="Target language")
            return p.parse_args(raw, namespace)
        res = super().parse_args(args, namespace)
        if not hasattr(res, "command"):
            res.command = None
        return res


def add_target_command(args: argparse.Namespace) -> int:
    """CLI entrypoint for `headerkit add-target <output_file> --target <triple>`."""
    output_path = Path(args.output_file)
    if not output_path.exists():
        print(f"Error: target file not found: {output_path}", file=sys.stderr)
        return 1

    lang = args.lang
    if not lang:
        suffix = output_path.suffix.lstrip(".")
        if suffix in ("nim", "nimble"):
            lang = "nim"
        elif suffix in ("pyx", "pxd"):
            lang = "cython"
        elif suffix in ("py", "pyi"):
            lang = "python"
        elif suffix in ("c", "h"):
            lang = "c"
        elif suffix in ("cpp", "hpp", "cc"):
            lang = "cpp"
        else:
            lang = "nim"

    existing_content = output_path.read_text(encoding="utf-8")
    from headerkit.ir import SourceUnit
    from headerkit.matrix.manifest import (
        emit_manifest_comment,
        extract_manifest,
        extract_overrides,
        inject_overrides,
    )
    from headerkit.matrix.models import ConfigCondition
    from headerkit.matrix.probe import probe_configuration
    from headerkit.reconcile import reconcile_matrix
    from headerkit.writers import get_writer

    try:
        overrides = extract_overrides(existing_content, language=lang)
        manifest = extract_manifest(existing_content, language=lang)
    except Exception as exc:
        print(f"Error reading existing manifest or overrides: {exc}", file=sys.stderr)
        return 1

    new_triple = args.target
    existing_targets = (
        list(manifest.matrix.get_dimension("target").values) if manifest.matrix.has_dimension("target") else []
    )
    if new_triple not in existing_targets:
        existing_targets.append(new_triple)
        manifest.matrix.add_dimension("target", existing_targets, dimension_type="target")

    matrix_units: list[tuple[ConfigCondition, SourceUnit]] = []
    for cond in manifest.matrix.resolve():
        unit = probe_configuration(manifest.header_path, cond)
        matrix_units.append((cond, unit))

    reconciled = reconcile_matrix(matrix_units, matrix=manifest.matrix, target_lang=lang)
    writer = get_writer(lang)
    generated_code = writer.write(reconciled)  # type: ignore[arg-type]

    manifest_comment = emit_manifest_comment(manifest, language=lang)
    code_with_manifest = manifest_comment + "\n" + generated_code
    final_output = inject_overrides(code_with_manifest, overrides, language=lang)

    output_path.write_text(final_output, encoding="utf-8")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Construct and return the argument parser."""
    parser = HeaderkitArgumentParser(
        prog="headerkit",
        description="Parse C/C++ header files and emit output via configurable writers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Subcommands:\n  add-target          Add target to existing generated binding matrix\n  new, new-plugin     Scaffold a new extension project (writer, backend, plugin)\n  install-libclang    Install libclang for the current platform (run with --help for options)\n  cache                Cache management (status, clear, rebuild-index, populate)\n  store                Store operations (merge)",
    )
    parser.add_argument(
        "input_files",
        nargs="*",
        metavar="HEADER_OR_GLOB",
        help="Header file paths or glob patterns (e.g., 'include/**/*.h')",
    )
    parser.add_argument(
        "--backend",
        dest="backend",
        metavar="NAME",
        default=None,
        help="Backend name (default: libclang)",
    )
    parser.add_argument(
        "-I",
        "--include-dir",
        dest="include_dirs",
        action="append",
        metavar="DIR",
        help="Add include directory",
    )
    parser.add_argument(
        "-D",
        dest="defines",
        action="append",
        metavar="DEFINE",
        help="Add preprocessor define",
    )
    parser.add_argument(
        "--backend-arg",
        dest="backend_args",
        action="append",
        metavar="ARG",
        help="Extra backend argument",
    )
    parser.add_argument(
        "-w",
        "--writer",
        dest="writers",
        action="append",
        metavar="WRITER",
        help="Writer to use (repeatable)",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_specs",
        action="append",
        metavar="[WRITER:]TEMPLATE",
        help="Output path template (e.g., -o librime.pxd or -o cython:librime.pxd)",
    )
    parser.add_argument(
        "--exclude",
        dest="exclude_patterns",
        action="append",
        metavar="PATTERN",
        help="Exclude headers matching glob pattern (repeatable)",
    )
    parser.add_argument(
        "--writer-opt",
        dest="writer_opts_raw",
        action="append",
        metavar="WRITER:KEY=VALUE",
        help="Writer option",
    )
    parser.add_argument(
        "--layout",
        dest="layout",
        choices=["file", "package", "project", "wheel", "scikit-build", "cmake"],
        default=None,
        help="Layout mode for output (default: file, or package if directory output)",
    )
    parser.add_argument(
        "--package-name",
        "--pkg",
        dest="package_name",
        default=None,
        help="Package name when scaffolding a package layout",
    )
    parser.add_argument(
        "--test-type",
        dest="test_type",
        choices=["both", "tripwire", "unit", "none"],
        default="both",
        help="Type of test stubs to generate when scaffolding (default: both)",
    )
    parser.add_argument(
        "--no-input",
        dest="no_input",
        action="store_true",
        default=False,
        help="Disable interactive prompting in terminal",
    )
    parser.add_argument(
        "--config",
        dest="config",
        metavar="PATH",
        default=None,
        help="Explicit config file path",
    )
    parser.add_argument(
        "--no-config",
        dest="no_config",
        action="store_true",
        default=False,
        help="Disable config file loading",
    )
    parser.add_argument(
        "--no-cache",
        dest="no_cache",
        action="store_true",
        default=False,
        help="Disable all caching",
    )
    parser.add_argument(
        "--no-ir-cache",
        dest="no_ir_cache",
        action="store_true",
        default=False,
        help="Disable IR cache only",
    )
    parser.add_argument(
        "--no-output-cache",
        dest="no_output_cache",
        action="store_true",
        default=False,
        help="Disable output cache only",
    )
    parser.add_argument(
        "--store-dir",
        dest="store_dir",
        metavar="DIR",
        default=None,
        help=("Store directory (default: .headerkit/ in project root). Env: HEADERKIT_STORE_DIR"),
    )
    parser.add_argument(
        "--target",
        dest="target",
        metavar="TRIPLE",
        default=None,
        help=(
            "Target triple for cross-compilation "
            "(e.g., aarch64-apple-darwin). "
            "Default: auto-detect host. "
            "Env: HEADERKIT_TARGET"
        ),
    )
    parser.add_argument(
        "--runtime",
        dest="runtime",
        metavar="NAME",
        default=None,
        help="Target runtime environment (e.g., nim, mojo). Env: HEADERKIT_RUNTIME",
    )
    parser.add_argument(
        "--language",
        dest="language",
        metavar="LANG",
        default=None,
        help="Input source language (e.g., c, cpp). Env: HEADERKIT_LANGUAGE",
    )
    parser.add_argument(
        "--classification",
        dest="classification",
        metavar="KIND",
        default=None,
        help="Input classification (e.g., header, source). Env: HEADERKIT_CLASSIFICATION",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('headerkit')}",
    )
    parser.set_defaults(
        backend="libclang",
        runtime=None,
        language=None,
        classification=None,
        include_dirs=[],
        defines=[],
        backend_args=[],
        writers=[],
        writer_opts_raw=[],
        plugins=[],
    )
    return parser


@dataclass
class WriterSpec:
    """Parsed writer specification from -w and --writer-opt flags."""

    name: str
    output_template: str | None  # None means use config/default
    options: dict[str, list[str]] = field(default_factory=dict)


def _parse_writer_specs(
    raw_writers: list[str],
    raw_opts: list[str],
) -> list[WriterSpec]:
    """Parse -w and --writer-opt arguments into WriterSpec objects.

    Raises ValueError on:
    - --writer-opt without WRITER: scope prefix
    - Malformed --writer-opt (missing KEY=VALUE)
    """
    specs: list[WriterSpec] = []
    spec_by_name: dict[str, WriterSpec] = {}

    for item in raw_writers:
        spec = WriterSpec(name=item, output_template=None)
        specs.append(spec)
        spec_by_name[item] = spec

    for item in raw_opts:
        writer_name, sep, rest = item.partition(":")
        if not sep:
            raise ValueError(f"unscoped --writer-opt: {item!r}; use WRITER:KEY=VALUE format")
        key, sep, value = rest.partition("=")
        if not sep:
            raise ValueError(f"malformed --writer-opt: {item!r}; expected WRITER:KEY=VALUE")
        if writer_name not in spec_by_name:
            print(
                f"headerkit: warning: --writer-opt for unknown writer {writer_name!r}; ignoring",
                file=sys.stderr,
            )
            continue
        spec_by_name[writer_name].options.setdefault(key, []).append(value)

    return specs


_EXTENSION_TO_WRITER: dict[str, str] = {
    ".pxd": "cython",
    ".pyx": "cython",
    ".json": "json",
    ".lua": "lua",
    ".txt": "prompt",
    ".nim": "nim",
    ".py": "cffi",
    ".pyi": "cffi",
}


def _infer_writer_from_path(path: str) -> str | None:
    """Infer writer name from output file extension."""
    suffix = Path(path).suffix.lower()
    return _EXTENSION_TO_WRITER.get(suffix)


def _parse_output_specs(
    raw_specs: list[str],
    active_writers: list[str] | None = None,
) -> dict[str, str]:
    """Parse -o arguments into a writer->template dict.

    Supports both ``WRITER:TEMPLATE`` syntax and bare ``TEMPLATE`` syntax when
    the writer can be unambiguously determined (either from a single active writer
    or inferred from the file extension).

    :param raw_specs: Raw ``-o`` arguments from the command line.
    :param active_writers: List of active writers specified via ``-w``.
    :returns: Dict mapping writer name to output path template.
    :raises ValueError: If a spec is malformed or ambiguous.
    """
    result: dict[str, str] = {}
    for item in raw_specs:
        has_writer_prefix = ":" in item
        if has_writer_prefix:
            prefix, _, rest = item.partition(":")
            # On Windows, drive letters like "C:\path" or "C:/path" are paths, not writer prefixes.
            if len(prefix) == 1 and prefix.isalpha() and (rest.startswith("\\") or rest.startswith("/")):
                has_writer_prefix = False

        if has_writer_prefix:
            writer_name, sep, template = item.partition(":")
            if not template:
                raise ValueError(f"headerkit: malformed --output: {item!r}; use WRITER:TEMPLATE format")
            result[writer_name] = template
        else:
            if active_writers and len(active_writers) == 1:
                writer_name = active_writers[0]
            elif active_writers and len(active_writers) > 1:
                raise ValueError(
                    f"headerkit: bare --output {item!r} requires exactly one active writer; "
                    "use WRITER:TEMPLATE when multiple writers are active"
                )
            else:
                inferred = _infer_writer_from_path(item)
                if inferred is not None:
                    writer_name = inferred
                else:
                    raise ValueError(f"headerkit: malformed --output: {item!r}; use WRITER:TEMPLATE format")
            result[writer_name] = item
    return result


def _parse_defines(defines: list[str]) -> list[str]:
    """Convert -D FOO and -D FOO=BAR into extra_args items.

    -D FOO   -> "-DFOO"
    -D FOO=BAR -> "-DFOO=BAR"
    """
    return [f"-D{d.strip()}" for d in defines]


def _build_umbrella(input_files: list[str]) -> tuple[str, str, tuple[str, ...]]:
    """Build synthetic umbrella header for multiple input files.

    Returns (code, filename, project_prefixes).

    - code: '#include "/abs/path/a.h"\\n#include "/abs/path/b.h"\\n...'
    - filename: basename for single file, "_umbrella.h" for multiple files
    - project_prefixes: tuple of unique parent directories of resolved input paths

    CRITICAL: project_prefixes contains PARENT DIRECTORIES, not file paths.
    """
    resolved: list[Path] = []
    for f in input_files:
        p = Path(f).resolve()
        resolved.append(p)

    project_prefixes: tuple[str, ...]
    if len(resolved) == 1:
        p = resolved[0]
        code = f'#include "{p}"'
        filename = p.name
        project_prefixes = (str(p.parent),)
    else:
        code = "".join(f'#include "{p}"\n' for p in resolved)
        filename = "_umbrella.h"
        project_prefixes = tuple(dict.fromkeys(str(p.parent) for p in resolved))

    return code, filename, project_prefixes


def _write_output(spec: WriterSpec, content: str) -> None:
    """Write output to stdout or file per spec.output_template."""
    if spec.output_template is None:
        print(content, end="")
    else:
        p = Path(spec.output_template)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _load_explicit_plugins(plugins: list[str]) -> None:
    """Import plugin modules listed explicitly in config.

    Each string is a dotted module path. ImportError warns to stderr.
    """
    for module_name in plugins:
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            print(
                f"headerkit: warning: failed to import plugin {module_name!r}: {exc}",
                file=sys.stderr,
            )


def _merge_config_writer_opts(
    config: HeaderkitConfig | None,
    specs: list[WriterSpec],
) -> list[WriterSpec]:
    """Merge config writer options into specs. Reads config.writer_options directly.

    NOTE: This reads config.writer_options, NOT args.writer_opts (which exists for
    test assertions only). CLI options in specs already take precedence (set before
    this call).
    """
    if config is None:
        return specs
    for spec in specs:
        if spec.name in config.writer_options:
            wc = config.writer_options[spec.name]
            for key, val in wc.options.items():
                if key not in spec.options:  # CLI wins
                    spec.options[key] = [str(item) for item in val] if isinstance(val, list) else [str(val)]
    return specs


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0 = success, 1 = error)."""
    raw_args = sys.argv[1:] if argv is None else argv
    if raw_args and raw_args[0] == "add-target":
        parser = _build_parser()
        args = parser.parse_args(raw_args)
        return add_target_command(args)

    # Subcommand dispatch: `headerkit new [args]` / `headerkit new-plugin [args]`
    if raw_args and raw_args[0] in ("new", "new-plugin"):
        from headerkit.scaffold_plugin import plugin_scaffold_main

        return plugin_scaffold_main(raw_args[1:])

    # Subcommand dispatch: `headerkit install-libclang [args]`
    if raw_args and raw_args[0] == "install-libclang":
        from headerkit.install_libclang import main as _install_main

        return _install_main(raw_args[1:])

    if raw_args and raw_args[0] == "cache":
        from headerkit._cache_cli import (
            cache_clear_main,
            cache_populate_main,
            cache_rebuild_index_main,
            cache_status_main,
        )

        sub_argv = raw_args[1:]
        if sub_argv and sub_argv[0] == "status":
            return cache_status_main(sub_argv[1:])
        if sub_argv and sub_argv[0] == "clear":
            return cache_clear_main(sub_argv[1:])
        if sub_argv and sub_argv[0] == "rebuild-index":
            return cache_rebuild_index_main(sub_argv[1:])
        if sub_argv and sub_argv[0] == "populate":
            return cache_populate_main(sub_argv[1:])
        print(
            "headerkit cache: unknown subcommand. Available: status, clear, rebuild-index, populate",
            file=sys.stderr,
        )
        return 1

    if raw_args and raw_args[0] == "store":
        from headerkit._cache_cli import store_merge_main

        sub_argv = raw_args[1:]
        if sub_argv and sub_argv[0] == "merge":
            return store_merge_main(sub_argv[1:])
        print(
            "headerkit store: unknown subcommand. Available: merge",
            file=sys.stderr,
        )
        return 1

    parser = _build_parser()
    args = parser.parse_args(raw_args)

    # Reject mutually exclusive flags
    if args.no_config and args.config is not None:
        print("headerkit: --config and --no-config are mutually exclusive", file=sys.stderr)
        return 1

    # typed locals (F2: use correct dest= names, not design doc section 10.2 names)
    backend_name: str = args.backend
    include_dirs: list[str] = args.include_dirs
    defines: list[str] = args.defines
    backend_args: list[str] = args.backend_args
    writers_raw: list[str] = args.writers
    writer_opts_raw: list[str] = args.writer_opts_raw
    config_path_raw: str | None = args.config
    no_config: bool = args.no_config
    input_files: list[str] = args.input_files

    # Load config
    config: HeaderkitConfig | None = None
    if not no_config:
        try:
            if config_path_raw is not None:
                explicit_config_path = Path(config_path_raw)
                if not explicit_config_path.exists():
                    print(f"headerkit: config file not found: {config_path_raw}", file=sys.stderr)
                    return 1
                config = load_config(explicit_config_path)
            else:
                discovered_config_path = find_config_file()
                if discovered_config_path is not None:
                    config = load_config(discovered_config_path)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    # Merge config into args (CLI wins)
    args = merge_config(config, args)
    # re-extract after merge
    backend_name = args.backend
    include_dirs = args.include_dirs
    defines = args.defines
    backend_args = args.backend_args
    writers_raw = args.writers
    writer_opts_raw = args.writer_opts_raw

    # Resolve cache settings (priority: CLI > env > config > defaults)
    resolved_no_cache: bool = args.no_cache or _env_bool("HEADERKIT_NO_CACHE")
    resolved_no_ir_cache: bool = args.no_ir_cache or _env_bool("HEADERKIT_NO_IR_CACHE")
    resolved_no_output_cache: bool = args.no_output_cache or _env_bool("HEADERKIT_NO_OUTPUT_CACHE")
    resolved_store_dir: str | None = args.store_dir
    # Env var takes priority over config (but not over CLI flag)
    if resolved_store_dir is None:
        env_store_dir = os.environ.get("HEADERKIT_STORE_DIR")
        if env_store_dir:
            resolved_store_dir = env_store_dir
    if not resolved_no_cache and config is not None:
        resolved_no_cache = config.no_cache
        if not resolved_no_ir_cache:
            resolved_no_ir_cache = config.no_ir_cache
        if not resolved_no_output_cache:
            resolved_no_output_cache = config.no_output_cache
        if resolved_store_dir is None:
            resolved_store_dir = config.store_dir

    # Load plugins (F3/F7: no --plugins flag; config.plugins loaded here)
    _load_backend_plugins()
    _load_writer_plugins()
    _load_hook_plugins()
    if config is not None and config.plugins:
        _load_explicit_plugins(config.plugins)
    if config is not None:
        register_rename_config(config.rename)

    resolved_runtime = getattr(args, "runtime", None) or os.environ.get("HEADERKIT_RUNTIME")
    resolved_language = getattr(args, "language", None) or os.environ.get("HEADERKIT_LANGUAGE")
    resolved_classification = getattr(args, "classification", None) or os.environ.get("HEADERKIT_CLASSIFICATION")

    # Parse writer specs
    try:
        specs = _parse_writer_specs(writers_raw, writer_opts_raw)
    except ValueError as exc:
        print(f"headerkit: {exc}", file=sys.stderr)
        return 1

    # Parse --output specs and apply to writer specs
    output_specs_raw: list[str] = getattr(args, "output_specs", None) or []
    try:
        output_templates = _parse_output_specs(output_specs_raw, active_writers=writers_raw or None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Auto-activate any writers specified in output_templates that weren't in -w
    existing_spec_names = {s.name for s in specs}
    for writer_name, template in output_templates.items():
        if writer_name not in existing_spec_names:
            specs.append(WriterSpec(name=writer_name, output_template=template, options={}))
            existing_spec_names.add(writer_name)

    if not specs:
        specs = [WriterSpec(name=get_default_writer(), output_template=None, options={})]

    for spec in specs:
        if spec.name in output_templates:
            spec.output_template = output_templates[spec.name]

    # Validate at most one writer sends to stdout
    stdout_count = sum(1 for s in specs if s.output_template is None)
    if stdout_count > 1:
        print(
            "Error: at most one writer may omit an output path (send to stdout)",
            file=sys.stderr,
        )
        return 1

    # Merge config writer options into specs
    specs = _merge_config_writer_opts(config, specs)

    # Resolve input files: CLI positional args win, else fall back to config
    use_config_headers = False
    if not input_files:
        if config is not None and config.headers:
            input_files = list(config.headers)
            use_config_headers = True
        else:
            print(
                "headerkit: no input files provided (pass header paths or configure "
                "[[tool.headerkit.headers]] in pyproject.toml)",
                file=sys.stderr,
            )
            return 1

    # Build merged output_templates: config output < CLI -o specs
    merged_output_templates: dict[str, str] = {}
    if config is not None:
        merged_output_templates.update(config.output)
    merged_output_templates.update(output_templates)

    # Check for package / project layout scaffolding
    layout_mode = getattr(args, "layout", None)
    pkg_name_arg = getattr(args, "package_name", None)
    test_type = getattr(args, "test_type", "both")
    no_input = getattr(args, "no_input", False)

    if (layout_mode is not None and layout_mode != "file") or (layout_mode is None and pkg_name_arg is not None):
        from headerkit.backends import get_backend
        from headerkit.scaffold import ScaffoldOptions, prompt_scaffold_options, scaffold

        backend = get_backend(backend_name)
        backend_args_list = _parse_defines(defines) + backend_args

        if not input_files:
            print("Error: no input files provided for scaffolding", file=sys.stderr)
            return 1

        first_path = Path(input_files[0]).resolve()
        unit = backend.parse(
            first_path.read_text(encoding="utf-8"),
            str(first_path),
            include_dirs or None,
            backend_args_list or None,
            project_prefixes=(str(first_path.parent),),
        )

        for spec in specs:
            pkg_name = pkg_name_arg or (Path(spec.output_template).name if spec.output_template else first_path.stem)
            target_dir = Path(spec.output_template) if spec.output_template else Path(pkg_name)

            scaffold_wopts: dict[str, object] = {}
            if spec.options:
                for key, values in spec.options.items():
                    scaffold_wopts[key] = values[0] if len(values) == 1 else values

            # The include paths and defines the parse was given, carried to the
            # writer so a generated build configuration can state the flags the
            # header was actually parsed with. Without this the fields existed and
            # the writer read them, but nothing outside the tests ever filled them:
            # a package scaffolded with `-I`/`-D` was generated with neither.
            defaults = ScaffoldOptions(
                package_name=pkg_name,
                target_language=spec.name,
                layout=layout_mode or "package",
                test_type=test_type,
                options=scaffold_wopts,
                extra_context={"include_dirs": list(include_dirs), "defines": list(defines)},
            )
            scaffold_opts = prompt_scaffold_options(defaults, is_tty=False if no_input else None)
            try:
                project_layout = scaffold(unit, scaffold_opts)
            except (ValueError, RuntimeError, FileNotFoundError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            written = project_layout.write_to_disk(target_dir)
            print(
                f"headerkit: scaffolded {len(written)} files into {target_dir}",
                file=sys.stderr,
            )
        return 0

    # Determine whether to use batch_generate() or direct generate()
    # Use direct generate() only for: single explicit file, single writer, no output template
    writer_names = [s.name for s in specs]
    has_any_output_template = bool(merged_output_templates) or any(s.output_template is not None for s in specs)
    has_globs = any(any(ch in f for ch in ("*", "?", "[")) for f in input_files)
    has_header_overrides = config is not None and bool(config.header_overrides)
    use_batch = (
        len(input_files) > 1 or has_globs or use_config_headers or has_any_output_template or has_header_overrides
    )

    if use_batch:
        # Batch generation path
        extra_args_list = _parse_defines(defines) + backend_args

        # Build writer_options dict from specs
        batch_writer_options: dict[str, dict[str, object]] = {}
        for spec in specs:
            if spec.options:
                wopts: dict[str, object] = {}
                for key, values in spec.options.items():
                    wopts[key] = values[0] if len(values) == 1 else values
                batch_writer_options[spec.name] = coerce_writer_options(spec.name, wopts)

        # Build header_overrides from config
        batch_header_overrides: dict[str, dict[str, object]] | None = None
        if config is not None and config.header_overrides:
            batch_header_overrides = config.header_overrides

        exclude_pats: list[str] = list(getattr(args, "exclude_patterns", None) or [])

        try:
            batch_result = batch_generate(
                patterns=input_files,
                exclude_patterns=exclude_pats or None,
                writers=writer_names,
                backend_name=backend_name,
                runtime=resolved_runtime,
                language=resolved_language,
                classification=resolved_classification,
                include_dirs=include_dirs or None,
                defines=defines or None,
                extra_args=extra_args_list or None,
                writer_options=batch_writer_options or None,
                output_templates=merged_output_templates or None,
                store_dir=resolved_store_dir,
                no_cache=resolved_no_cache,
                no_ir_cache=resolved_no_ir_cache,
                no_output_cache=resolved_no_output_cache,
                auto_install_libclang=None,
                target=args.target,
                header_overrides=batch_header_overrides,
            )
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        # Print summary to stderr
        print(
            f"headerkit: processed {batch_result.headers_processed} headers, {len(batch_result.results)} outputs",
            file=sys.stderr,
        )
        for r in batch_result.results:
            if r.output_path is not None:
                cached_tag = " (cached)" if r.from_cache else ""
                print(f"  {r.writer_name}: {r.output_path}{cached_tag}", file=sys.stderr)

        return 0

    # Single-file direct generation path (preserves stdout behavior)
    # Validate input file exists
    for f in input_files:
        if not Path(f).exists():
            print(f"Error: input file not found: {f}", file=sys.stderr)
            return 1

    # Build umbrella
    project_prefixes: tuple[str, ...]
    if len(input_files) == 1 and backend_name != "libclang":
        p = Path(input_files[0]).resolve()
        code = None
        filename = str(p)
        project_prefixes = (str(p.parent),)
    else:
        code, filename, project_prefixes = _build_umbrella(input_files)

    # Generate outputs via cache-aware pipeline
    extra_args = _parse_defines(defines) + backend_args
    for spec in specs:
        writer_kwargs: dict[str, object] = {}
        for key, values in spec.options.items():
            writer_kwargs[key] = values[0] if len(values) == 1 else values
        writer_kwargs = coerce_writer_options(spec.name, writer_kwargs)

        if spec.name == "diff" and "baseline" not in writer_kwargs:
            print(
                "Warning: diff writer used without a baseline; outputting full current header state"
                " (not a true diff). Use the Python API to supply a baseline Header.",
                file=sys.stderr,
            )

        try:
            content = generate(
                header_path=filename,
                writer_name=spec.name,
                code=code,
                backend_name=backend_name,
                runtime=resolved_runtime,
                language=resolved_language,
                classification=resolved_classification,
                include_dirs=include_dirs or None,
                extra_args=extra_args or None,
                writer_options=writer_kwargs or None,
                store_dir=resolved_store_dir,
                no_cache=resolved_no_cache,
                no_ir_cache=resolved_no_ir_cache,
                no_output_cache=resolved_no_output_cache,
                project_prefixes=project_prefixes or None,
                target=args.target,
            )
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        _write_output(spec, content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
