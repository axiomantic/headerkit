"""Cache store for .headerkit/ directory management.

Manages the two-layer cache directory layout: finding/creating the
cache dir, writing and reading IR entries, writing and reading output
entries. All write operations use atomic writes for metadata
(write to .tmp, os.replace) and handle OSError gracefully.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from headerkit._cache_key import _IR_SCHEMA_VERSION
from headerkit._ir_json import json_to_header
from headerkit._slug import load_index, register_slug, save_index
from headerkit.ir import Header
from headerkit.writers.json import header_to_json_dict

logger = logging.getLogger("headerkit.cache")


def _collect_included_headers_meta(
    included_headers: set[str],
    header_path: str | None = None,
) -> dict[str, dict[str, int | str]]:
    """Record mtime_ns, size, and sha256 for existing header files on disk."""
    meta: dict[str, dict[str, int | str]] = {}
    candidates = set(included_headers)
    if header_path:
        candidates.add(header_path)
    for raw_path in sorted(candidates):
        p = Path(raw_path)
        if not p.is_file():
            continue
        try:
            st = p.stat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            meta[raw_path] = {
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
                "sha256": digest,
            }
        except OSError:
            continue
    return meta


def find_cache_dir(start_path: Path) -> Path | None:
    """Find or create .headerkit/ directory at the project root.

    Walks from start_path upward looking for a .git directory. If found,
    creates .headerkit/ at that level. Returns None if no .git root is
    found before reaching the filesystem root or home directory.

    :param start_path: Directory to start searching from.
    :returns: Absolute path to .headerkit/ directory, or None.
    """
    current = start_path.absolute()
    home = Path.home().absolute()

    while True:
        if (current / ".git").exists():
            cache_dir = current / ".headerkit"
            cache_dir.mkdir(exist_ok=True)
            return cache_dir

        if current == current.parent or current == home:
            break
        current = current.parent

    return None


def write_ir_entry(
    *,
    cache_dir: Path,
    slug: str,
    cache_key: str,
    header: Header,
    backend_name: str,
    header_path: str,
    target: str,
    defines: list[str],
    includes: list[str],
    other_args: list[str],
) -> Path:
    """Serialize IR and write to cache.

    Writes ir.json and metadata.json under cache_dir/ir/slug/.
    Updates cache_dir/ir/index.json with the slug registration.
    Uses atomic writes for metadata (write to .tmp, os.replace).

    :param cache_dir: Path to .headerkit/ directory.
    :param slug: Human-readable slug for the entry directory.
    :param cache_key: SHA-256 hex digest cache key.
    :param header: Parsed Header IR to cache.
    :param backend_name: Backend name used for parsing.
    :param header_path: Original header file path (for metadata).
    :param defines: Defines used for parsing.
    :param includes: Include dirs used for parsing.
    :param other_args: Other args used for parsing.
    :returns: Path to the entry directory.
    :raises OSError: If directory creation or file write fails.
    """
    ir_dir = cache_dir / "ir"
    ir_dir.mkdir(parents=True, exist_ok=True)

    # Register slug in index
    index_path = ir_dir / "index.json"
    index = load_index(index_path)
    actual_slug = register_slug(index, slug, cache_key)
    save_index(index_path, index)

    entry_dir = ir_dir / actual_slug
    entry_dir.mkdir(parents=True, exist_ok=True)

    # Write ir.json (serialized via the same format as the JSON writer)
    ir_data = header_to_json_dict(header)
    ir_path = entry_dir / "ir.json"
    ir_path.write_text(json.dumps(ir_data, indent=2, sort_keys=True), encoding="utf-8")

    # Write metadata.json atomically
    now = datetime.now(UTC).isoformat()
    included_headers_meta = _collect_included_headers_meta(header.included_headers, header_path)
    metadata: dict[str, Any] = {
        "cache_key": cache_key,
        "ir_schema_version": _IR_SCHEMA_VERSION,
        "backend_name": backend_name,
        "header_path": header_path,
        "target": target,
        "defines": defines,
        "includes": includes,
        "other_args": other_args,
        "included_headers": included_headers_meta,
        "created": now,
        "headerkit_version": importlib.metadata.version("headerkit"),
    }
    meta_path = entry_dir / "metadata.json"
    tmp_path = meta_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp_path), str(meta_path))

    return entry_dir


def read_ir_entry(*, cache_dir: Path, slug: str) -> Header | None:
    """Read a cached IR entry.

    Reads ir.json, validates IR schema version from metadata.json,
    and returns deserialized Header via json_to_header from _ir_json.
    Returns None on any error (missing files, corrupt JSON, schema
    version mismatch).

    :param cache_dir: Path to .headerkit/ directory.
    :param slug: Slug identifying the entry directory.
    :returns: Deserialized Header, or None on any error.
    """
    entry_dir = cache_dir / "ir" / slug
    ir_path = entry_dir / "ir.json"
    meta_path = entry_dir / "metadata.json"

    if not ir_path.exists() or not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cached_version = meta.get("ir_schema_version", "")
        if cached_version != _IR_SCHEMA_VERSION:
            logger.warning(
                "IR schema version mismatch (cached: %s, current: %s) for %s, treating as cache miss",
                cached_version,
                _IR_SCHEMA_VERSION,
                entry_dir,
            )
            return None

        included_meta = meta.get("included_headers")
        if isinstance(included_meta, dict):
            for inc_path_str, expected in included_meta.items():
                if not isinstance(expected, dict):
                    continue
                inc_path = Path(inc_path_str)
                if not inc_path.is_file():
                    logger.info(
                        "Included header missing (%s) for %s, treating as cache miss",
                        inc_path_str,
                        entry_dir,
                    )
                    return None
                st = inc_path.stat()
                if st.st_size != expected.get("size"):
                    logger.info(
                        "Included header size changed (%s) for %s, treating as cache miss",
                        inc_path_str,
                        entry_dir,
                    )
                    return None
                actual_sha = hashlib.sha256(inc_path.read_bytes()).hexdigest()
                if actual_sha != expected.get("sha256"):
                    logger.info(
                        "Included header content changed (%s) for %s, treating as cache miss",
                        inc_path_str,
                        entry_dir,
                    )
                    return None

        ir_text = ir_path.read_text(encoding="utf-8")
        ir_data = json.loads(ir_text)
        return json_to_header(ir_data)
    except (json.JSONDecodeError, ValueError, KeyError, OSError) as exc:
        logger.warning(
            "Corrupt cache entry at %s, treating as cache miss: %s",
            entry_dir,
            exc,
        )
        return None


def write_output_entry(
    *,
    cache_dir: Path,
    writer_name: str,
    slug: str,
    cache_key: str,
    ir_cache_key: str,
    output: str,
    writer_options: dict[str, object],
    writer_cache_version: str | None,
    output_extension: str,
) -> Path:
    """Write a cached output entry.

    Writes the output file and metadata.json under
    cache_dir/output/writer_name/slug/. Updates the writer's
    index.json with the slug registration.

    :param cache_dir: Path to .headerkit/ directory.
    :param writer_name: Name of the writer.
    :param slug: Human-readable slug for the entry directory.
    :param cache_key: SHA-256 hex digest output cache key.
    :param ir_cache_key: SHA-256 hex digest of the IR cache key.
    :param output: Generated output text.
    :param writer_options: Writer kwargs used for generation.
    :param writer_cache_version: Writer's cache version string.
    :param output_extension: File extension for output (e.g., ".py").
    :returns: Path to the entry directory.
    :raises OSError: If directory creation or file write fails.
    """
    writer_dir = cache_dir / "output" / writer_name
    writer_dir.mkdir(parents=True, exist_ok=True)

    # Register slug in index
    index_path = writer_dir / "index.json"
    index = load_index(index_path)
    actual_slug = register_slug(index, slug, cache_key)
    save_index(index_path, index)

    entry_dir = writer_dir / actual_slug
    entry_dir.mkdir(parents=True, exist_ok=True)

    # Write output file
    output_file = entry_dir / f"output{output_extension}"
    output_file.write_text(output, encoding="utf-8")

    # Write metadata.json atomically
    now = datetime.now(UTC).isoformat()
    metadata: dict[str, Any] = {
        "cache_key": cache_key,
        "ir_cache_key": ir_cache_key,
        "writer_name": writer_name,
        "writer_options": {k: json.dumps(v, sort_keys=True, separators=(",", ":")) for k, v in writer_options.items()},
        "writer_cache_version": writer_cache_version,
        "created": now,
        "headerkit_version": importlib.metadata.version("headerkit"),
    }
    meta_path = entry_dir / "metadata.json"
    tmp_path = meta_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp_path), str(meta_path))

    return entry_dir


def read_output_entry(
    *,
    cache_dir: Path,
    writer_name: str,
    slug: str,
    output_extension: str,
) -> str | None:
    """Read a cached output entry.

    Reads the output file from cache_dir/output/writer_name/slug/.
    Returns None on any error (missing file, read error).

    :param cache_dir: Path to .headerkit/ directory.
    :param writer_name: Name of the writer.
    :param slug: Slug identifying the entry directory.
    :param output_extension: File extension for output (e.g., ".py").
    :returns: Cached output string, or None on any error.
    """
    entry_dir = cache_dir / "output" / writer_name / slug
    output_file = entry_dir / f"output{output_extension}"

    if not output_file.exists():
        return None

    try:
        return output_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read cached output at %s: %s", output_file, exc)
        return None
