"""Hash-based cache staleness detection for headerkit generated output.

This module provides functions to compute deterministic hashes of
generation inputs, store those hashes alongside output files, and
check whether outputs are up-to-date with respect to their inputs.

All functions work without libclang installed, making them suitable
for validation on platforms where libclang is unavailable.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("headerkit.cache")


# =============================================================================
# Public API
# =============================================================================


def compute_hash(
    *,
    header_paths: Sequence[str | Path],
    writer_name: str,
    writer_options: dict[str, Any] | None = None,
    extra_inputs: Sequence[str | Path] | None = None,
) -> str:
    """Compute a deterministic SHA-256 hash of all inputs affecting generation.

    :param header_paths: Paths to C/C++ header files to include in hash.
        Relative paths are resolved against CWD via ``Path(p).resolve()``.
    :param writer_name: Name of the writer (e.g., ``"cffi"``, ``"ctypes"``).
    :param writer_options: Writer configuration options (sorted for determinism).
    :param extra_inputs: Additional file paths to include in the hash.
        Relative paths are resolved against CWD via ``Path(p).resolve()``.
    :returns: Hex-encoded SHA-256 digest string.
    :raises FileNotFoundError: If any header or extra input file does not exist.
    :raises ValueError: If header_paths is empty.
    """
    return _compute_hash_digest(
        header_paths=header_paths,
        writer_name=writer_name,
        writer_options=writer_options,
        extra_inputs=extra_inputs,
    )


# =============================================================================
# Internal Functions
# =============================================================================


def _compute_hash_digest(
    *,
    header_paths: Sequence[str | Path],
    writer_name: str,
    writer_options: dict[str, Any] | None,
    extra_inputs: Sequence[str | Path] | None,
) -> str:
    """Core hash computation. Shared by compute_hash, save_hash, is_up_to_date."""
    if not header_paths:
        raise ValueError("header_paths must not be empty")

    # Resolve and validate header paths
    resolved_headers: list[Path] = []
    for p in header_paths:
        rp = Path(p).resolve()
        if not rp.exists():
            raise FileNotFoundError(f"Header not found: {p}")
        resolved_headers.append(rp)

    # Resolve and validate extra input paths
    resolved_extras: list[Path] = []
    if extra_inputs:
        for p in extra_inputs:
            rp = Path(p).resolve()
            if not rp.exists():
                raise FileNotFoundError(f"Extra input not found: {p}")
            resolved_extras.append(rp)

    hasher = hashlib.sha256()

    # Feed headerkit version
    try:
        version = importlib.metadata.version("headerkit")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
        logger.warning("Could not determine headerkit version; using 'unknown'")
    hasher.update(b"headerkit-version:")
    hasher.update(version.encode("utf-8"))
    hasher.update(b"\x00")

    # Feed writer name
    hasher.update(b"writer:")
    hasher.update(writer_name.encode("utf-8"))
    hasher.update(b"\x00")

    # Feed writer options (sorted by key)
    if writer_options:
        for key in sorted(writer_options.keys()):
            value = writer_options[key]
            hasher.update(b"option:")
            hasher.update(f"{key}={value}".encode())
            hasher.update(b"\x00")

    # Feed header file contents (sorted by resolved absolute path)
    for hp in sorted(resolved_headers, key=lambda p: str(p)):
        content = _read_file_normalized(hp)
        hasher.update(b"header:")
        hasher.update(str(hp).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
        hasher.update(b"\x00")

    # Feed extra input contents (sorted by resolved absolute path)
    for ep in sorted(resolved_extras, key=lambda p: str(p)):
        content = _read_file_normalized(ep)
        hasher.update(b"extra:")
        hasher.update(str(ep).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
        hasher.update(b"\x00")

    return hasher.hexdigest()


def _read_file_normalized(path: Path) -> bytes:
    """Read file, strip BOM, normalize line endings to LF, return bytes."""
    raw = path.read_bytes()
    # Strip UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    # Normalize line endings: CRLF -> LF, then CR -> LF
    raw = raw.replace(b"\r\n", b"\n")
    raw = raw.replace(b"\r", b"\n")
    return raw
