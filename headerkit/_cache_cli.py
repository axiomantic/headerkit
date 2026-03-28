"""CLI subcommands for cache operations.

Provides ``cache-check``, ``cache-save``, ``cache status``,
``cache clear``, and ``cache rebuild-index`` subcommands, dispatched
from the main CLI via early dispatch (before argparse construction).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from headerkit._slug import rebuild_index, save_index
from headerkit.cache import _read_stored_hash, is_up_to_date, save_hash


def _parse_writer_options(raw: list[str] | None) -> dict[str, str] | None:
    """Parse --writer-option KEY=VALUE pairs into a dict.

    Splits on the first ``=`` so that values may contain ``=``.
    """
    if not raw:
        return None
    result: dict[str, str] = {}
    for item in raw:
        eq_pos = item.find("=")
        if eq_pos == -1:
            print(
                f"headerkit cache: malformed --writer-option: {item!r}; expected KEY=VALUE",
                file=sys.stderr,
            )
            continue
        key = item[:eq_pos]
        value = item[eq_pos + 1 :]
        result[key] = value
    return result if result else None


def _build_check_parser() -> argparse.ArgumentParser:
    """Build argument parser for cache-check."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache-check",
        description="Check if a generated output file is up-to-date.",
    )
    parser.add_argument(
        "output_file",
        metavar="OUTPUT",
        help="Path to the generated output file",
    )
    parser.add_argument(
        "--header",
        dest="headers",
        action="append",
        required=True,
        metavar="PATH",
        help="Header file path (repeatable)",
    )
    parser.add_argument(
        "--writer-name",
        dest="writer_name",
        required=True,
        metavar="NAME",
        help="Writer name used for generation",
    )
    parser.add_argument(
        "--writer-option",
        dest="writer_options_raw",
        action="append",
        metavar="KEY=VALUE",
        help="Writer option (repeatable)",
    )
    parser.add_argument(
        "--extra-input",
        dest="extra_inputs",
        action="append",
        metavar="PATH",
        help="Additional input file path (repeatable)",
    )
    return parser


def _build_save_parser() -> argparse.ArgumentParser:
    """Build argument parser for cache-save."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache-save",
        description="Save cache hash metadata for a generated output file.",
    )
    parser.add_argument(
        "output_file",
        metavar="OUTPUT",
        help="Path to the generated output file",
    )
    parser.add_argument(
        "--header",
        dest="headers",
        action="append",
        required=True,
        metavar="PATH",
        help="Header file path (repeatable)",
    )
    parser.add_argument(
        "--writer-name",
        dest="writer_name",
        required=True,
        metavar="NAME",
        help="Writer name used for generation",
    )
    parser.add_argument(
        "--writer-option",
        dest="writer_options_raw",
        action="append",
        metavar="KEY=VALUE",
        help="Writer option (repeatable)",
    )
    parser.add_argument(
        "--extra-input",
        dest="extra_inputs",
        action="append",
        metavar="PATH",
        help="Additional input file path (repeatable)",
    )
    parser.add_argument(
        "--writer",
        dest="writer_instance_name",
        metavar="NAME",
        default=None,
        help="Writer instance name for embedded comment support (uses get_writer)",
    )
    return parser


def cache_check_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache-check``."""
    parser = _build_check_parser()
    args = parser.parse_args(argv)

    writer_options = _parse_writer_options(args.writer_options_raw)
    output_path = Path(args.output_file)

    try:
        result = is_up_to_date(
            output_path=output_path,
            header_paths=args.headers,
            writer_name=args.writer_name,
            writer_options=writer_options,
            extra_inputs=args.extra_inputs,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"stale: {output_path} (reason: {exc})")
        return 1

    if result:
        print(f"up-to-date: {output_path}")
        return 0
    else:
        if not output_path.exists():
            reason = "missing output"
        else:
            stored = _read_stored_hash(output_path)
            if stored is None:
                reason = "no stored hash"
            else:
                reason = "hash mismatch"
        print(f"stale: {output_path} (reason: {reason})")
        return 1


def cache_save_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache-save``."""
    parser = _build_save_parser()
    args = parser.parse_args(argv)

    writer_options = _parse_writer_options(args.writer_options_raw)
    output_path = Path(args.output_file)

    # Optionally instantiate writer for embedded support
    writer = None
    if args.writer_instance_name is not None:
        from headerkit.writers import get_writer

        try:
            writer = get_writer(args.writer_instance_name)
        except ValueError as exc:
            print(f"headerkit cache-save: {exc}", file=sys.stderr)
            return 1

    try:
        result_path = save_hash(
            output_path=output_path,
            header_paths=args.headers,
            writer_name=args.writer_name,
            writer_options=writer_options,
            extra_inputs=args.extra_inputs,
            writer=writer,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"headerkit cache-save: {exc}", file=sys.stderr)
        return 1

    storage_type = "embedded" if result_path == output_path else "sidecar"
    print(f"saved: {result_path} ({storage_type})")
    return 0


def _count_entry_dirs(parent: Path) -> int:
    """Count subdirectories containing metadata.json."""
    if not parent.is_dir():
        return 0
    return sum(1 for p in parent.iterdir() if p.is_dir() and (p / "metadata.json").exists())


def cache_status_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache status``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache status",
        description="Show cache statistics.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        required=True,
        metavar="DIR",
        help="Cache directory path",
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(
            f"headerkit cache status: cache directory not found: {cache_dir}",
            file=sys.stderr,
        )
        return 1

    ir_count = _count_entry_dirs(cache_dir / "ir")

    output_dir = cache_dir / "output"
    writer_counts: dict[str, int] = {}
    total_output = 0
    if output_dir.is_dir():
        for writer_dir in sorted(output_dir.iterdir()):
            if writer_dir.is_dir():
                count = _count_entry_dirs(writer_dir)
                if count > 0:
                    writer_counts[writer_dir.name] = count
                    total_output += count

    print(f"Cache directory: {cache_dir}")
    print(f"IR entries: {ir_count}")
    print(f"Output entries: {total_output}")
    for writer_name, count in sorted(writer_counts.items()):
        print(f"  {writer_name}: {count}")

    return 0


def cache_clear_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache clear``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache clear",
        description="Clear cache entries.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        required=True,
        metavar="DIR",
        help="Cache directory path",
    )
    parser.add_argument(
        "--ir",
        dest="ir_only",
        action="store_true",
        default=False,
        help="Clear only IR cache entries",
    )
    parser.add_argument(
        "--output",
        dest="output_only",
        action="store_true",
        default=False,
        help="Clear only output cache entries",
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(
            f"headerkit cache clear: cache directory not found: {cache_dir}",
            file=sys.stderr,
        )
        return 1

    clear_ir = not args.output_only  # Clear IR unless --output only
    clear_output = not args.ir_only  # Clear output unless --ir only

    if clear_ir:
        ir_dir = cache_dir / "ir"
        if ir_dir.is_dir():
            shutil.rmtree(ir_dir)
            ir_dir.mkdir()

    if clear_output:
        output_dir = cache_dir / "output"
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
            output_dir.mkdir()

    if args.ir_only:
        print("Cleared IR cache entries.")
    elif args.output_only:
        print("Cleared output cache entries.")
    else:
        print("Cleared all cache entries.")

    return 0


def cache_rebuild_index_main(argv: list[str]) -> int:
    """Entry point for ``headerkit cache rebuild-index``."""
    parser = argparse.ArgumentParser(
        prog="headerkit cache rebuild-index",
        description="Rebuild index.json files from metadata.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        required=True,
        metavar="DIR",
        help="Cache directory path",
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)

    if not cache_dir.is_dir():
        print(
            f"headerkit cache rebuild-index: cache directory not found: {cache_dir}",
            file=sys.stderr,
        )
        return 1

    # Rebuild IR index
    ir_dir = cache_dir / "ir"
    if ir_dir.is_dir():
        index = rebuild_index(ir_dir)
        index_path = ir_dir / "index.json"
        save_index(index_path, index)
        print(f"Rebuilt index: {index_path} ({len(index['entries'])} entries)")

    # Rebuild output writer indexes
    output_dir = cache_dir / "output"
    if output_dir.is_dir():
        for writer_dir in sorted(output_dir.iterdir()):
            if writer_dir.is_dir():
                index = rebuild_index(writer_dir)
                index_path = writer_dir / "index.json"
                save_index(index_path, index)
                print(f"Rebuilt index: {index_path} ({len(index['entries'])} entries)")

    return 0
