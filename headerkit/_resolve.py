"""Header path resolution and output path template expansion."""

from __future__ import annotations

from glob import glob
from pathlib import Path


def resolve_headers(
    patterns: list[str],
    exclude_patterns: list[str],
    project_root: Path,
) -> tuple[list[Path], dict[Path, list[str]]]:
    """Resolve header file patterns into concrete paths.

    :param patterns: Glob patterns or literal paths.
    :param exclude_patterns: Glob patterns or literal paths to exclude.
    :param project_root: Project root directory.
    :returns: Tuple of (sorted paths, mapping of path -> list of matching patterns).
    :raises ValueError: If no paths remain after resolution and exclusion.
    """
    matched: set[Path] = set()
    pattern_mapping: dict[Path, list[str]] = {}

    for pattern in patterns:
        is_absolute = Path(pattern).is_absolute()
        root_dir = None if is_absolute else project_root
        hits = [
            (project_root / h).resolve() for h in glob(pattern, root_dir=root_dir, recursive=True, include_hidden=True)
        ]
        for path in hits:
            matched.add(path)
            pattern_mapping.setdefault(path, []).append(pattern)

    # Apply exclusions
    for pattern in exclude_patterns:
        is_absolute = Path(pattern).is_absolute()
        root_dir = None if is_absolute else project_root
        hits = [
            (project_root / h).resolve() for h in glob(pattern, root_dir=root_dir, recursive=True, include_hidden=True)
        ]
        for path in hits:
            matched.discard(path)
            pattern_mapping.pop(path, None)

    if not matched:
        raise ValueError(f"No headers matched patterns: {patterns!r} (excludes: {exclude_patterns!r})")

    sorted_paths = sorted(matched)
    return sorted_paths, pattern_mapping


def resolve_output_path(
    template: str,
    header_path: Path,
    project_root: Path,
) -> Path:
    """Resolve an output path template for a header file.

    :param template: Path template with {stem}, {name}, {dir} variables.
    :param header_path: Absolute path to the header file.
    :param project_root: Project root directory.
    :returns: Resolved output path.
    :raises ValueError: If template contains unknown variables.
    """
    stem = header_path.stem
    name = header_path.name
    parent = header_path.absolute().parent
    try:
        dir_part = str(parent.relative_to(project_root.absolute()))
    except ValueError:
        dir_part = ""
    # Use POSIX separators for cross-platform consistency
    dir_part = dir_part.replace("\\", "/")

    try:
        result = template.format(stem=stem, name=name, dir=dir_part)
    except KeyError as exc:
        raise ValueError(f"Unknown template variable in {template!r}: {exc}") from exc

    return Path(result)


def check_output_collisions(
    resolved_paths: dict[tuple[Path, str], Path],
) -> None:
    """Check for output path collisions across all header/writer combinations.

    :param resolved_paths: Map of (header_path, writer_name) -> resolved output path.
    :raises ValueError: If two or more header/writer combos would write to the same file.
    """
    seen: dict[Path, tuple[Path, str]] = {}
    for (header, writer), output in resolved_paths.items():
        canonical = output.resolve()
        if canonical in seen:
            prev_header, prev_writer = seen[canonical]
            raise ValueError(
                f"Output collision: {header} ({writer}) and {prev_header} ({prev_writer}) both resolve to {output}"
            )
        seen[canonical] = (header, writer)
