"""Cache key computation for headerkit's two-layer cache.

IR cache key: SHA-256 of (ir_schema_version, backend, target,
header content, defines, includes, other_args).

Output cache key: SHA-256 of (ir_cache_key, writer_name, writer_options,
writer_cache_version, rename_fingerprint).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from headerkit.ir import InputSpec

# Bump when IR dataclass structure changes in a way that invalidates
# cached JSON. This is NOT the headerkit version.
#
# Adding a field counts, and the reason is asymmetric enough to be worth stating:
# the deserialiser fills an absent key with the dataclass default, so a document
# written before the field existed comes back carrying that default as though a
# parser had reported it. Where the default refuses -- ``CType.is_elaborated`` is
# ``None`` -- that is safe. Where it resolves -- ``Enum.underlying_type_known`` is
# ``True`` -- a warm cache turns a refusal into a silently wrong width.
#
# ``test_cache_key.py`` fingerprints the IR dataclasses and fails when this
# constant has not moved with them, so the reminder is a check rather than a
# comment nobody reads.
_IR_SCHEMA_VERSION = "9"


@dataclass
class ParsedArgs:
    """Structured representation of extra_args."""

    defines: list[str]
    includes: list[str]
    other_args: list[str]


def parse_extra_args(
    extra_args: list[str] | None,
    include_dirs: list[str] | None = None,
    defines: list[str] | None = None,
) -> ParsedArgs:
    """Parse extra_args into structured components.

    Merges explicit include_dirs and defines with those found in extra_args.
    All include paths are resolved to absolute. All lists are sorted and
    deduplicated for determinism.

    :param extra_args: Raw extra_args from backend.parse() call.
    :param include_dirs: Explicit include directories.
    :param defines: Explicit defines (without -D prefix).
    :returns: Parsed and sorted args.
    """
    found_defines: set[str] = set()
    found_includes: set[str] = set()
    found_other: set[str] = set()

    if defines:
        for d in defines:
            found_defines.add(d)

    if include_dirs:
        for inc in include_dirs:
            found_includes.add(str(Path(inc).resolve()))

    if extra_args:
        args_iter = iter(extra_args)
        for arg in args_iter:
            if arg.startswith("-D"):
                found_defines.add(arg[2:])
            elif arg == "-I":
                try:
                    path = next(args_iter)
                    found_includes.add(str(Path(path).resolve()))
                except StopIteration:
                    pass
            elif arg.startswith("-I"):
                found_includes.add(str(Path(arg[2:]).resolve()))
            else:
                found_other.add(arg)

    return ParsedArgs(
        defines=sorted(found_defines),
        includes=sorted(found_includes),
        other_args=sorted(found_other),
    )


def _relative_header_path(header_path: Path, project_root: Path) -> str:
    """Return header path relative to the project root for cache key hashing.

    If header_path is outside project_root (e.g. system or external header),
    returns the resolved absolute POSIX path.

    :param header_path: Absolute or relative path to the header file.
    :param project_root: Project root directory (contains .headerkit/ or is git root).
    :returns: POSIX-style relative path string, or absolute POSIX path if external.
    """
    resolved = header_path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def compute_ir_cache_key(
    *,
    backend_name: str,
    target: str,
    header_path: Path,
    project_root: Path,
    parsed_args: ParsedArgs,
    code: str | None = None,
    input_spec: InputSpec | None = None,
    headers: tuple[Path, ...] = (),
    project_prefixes: tuple[str, ...] = (),
    recursive_includes: bool = True,
    max_depth: int | None = None,
    use_default_includes: bool = True,
) -> str:
    """Compute SHA-256 cache key for IR layer.

    :param backend_name: Parser backend name.
    :param target: Normalized LLVM target triple.
    :param header_path: Path to the C/C++ header file.
    :param project_root: Project root directory (contains .headerkit/ or is git root).
    :param parsed_args: Structured representation of extra_args.
    :param code: Raw code string. If provided, used instead of reading header_path.
    :param input_spec: Optional InputSpec carrying traversal parameters.
    :param headers: Additional header paths in the parse unit.
    :param project_prefixes: Project prefix directories for non-system classification.
    :param recursive_includes: Whether recursive include traversal is enabled.
    :param max_depth: Maximum recursion depth for include traversal.
    :param use_default_includes: Whether system default include dirs are enabled.
    :returns: Hex digest string.
    """
    if input_spec is not None:
        if not headers and input_spec.headers:
            headers = input_spec.headers
        if not project_prefixes and input_spec.project_prefixes:
            project_prefixes = input_spec.project_prefixes
        if recursive_includes is True and not input_spec.recursive_includes:
            recursive_includes = input_spec.recursive_includes
        if max_depth is None and input_spec.max_depth is not None:
            max_depth = input_spec.max_depth
        if use_default_includes is True and not input_spec.use_default_includes:
            use_default_includes = input_spec.use_default_includes

    hasher = hashlib.sha256()

    hasher.update(f"ir-schema:{_IR_SCHEMA_VERSION}\0".encode())
    hasher.update(f"backend:{backend_name}\0".encode())
    hasher.update(f"target:{target}\0".encode())

    # Header content -- use path relative to project root for portability
    content = code if code is not None else header_path.read_text(encoding="utf-8")
    rel_path = _relative_header_path(header_path, project_root)
    hasher.update(f"header:{rel_path}\0".encode())
    hasher.update(content.encode("utf-8"))
    hasher.update(b"\0")

    for extra_hdr in headers:
        extra_p = Path(extra_hdr)
        extra_rel = _relative_header_path(extra_p, project_root)
        extra_content = extra_p.read_text(encoding="utf-8") if extra_p.exists() else ""
        hasher.update(f"extra-header:{extra_rel}\0".encode())
        hasher.update(extra_content.encode("utf-8"))
        hasher.update(b"\0")

    for prefix in sorted(project_prefixes):
        hasher.update(f"project-prefix:{prefix}\0".encode())

    hasher.update(f"recursive-includes:{recursive_includes}\0".encode())
    hasher.update(f"max-depth:{max_depth}\0".encode())
    hasher.update(f"use-default-includes:{use_default_includes}\0".encode())

    for d in sorted(parsed_args.defines):
        hasher.update(f"define:{d}\0".encode())

    for inc in sorted(parsed_args.includes):
        hasher.update(f"include:{inc}\0".encode())

    for arg in sorted(parsed_args.other_args):
        hasher.update(f"arg:{arg}\0".encode())

    return hasher.hexdigest()


def compute_output_cache_key(
    *,
    ir_cache_key: str,
    writer_name: str,
    writer_options: dict[str, object] | None = None,
    writer_cache_version: str | None = None,
    rename_fingerprint: str | None = None,
    transforms_fingerprint: Sequence[Mapping[str, object]] | None = None,
) -> str:
    """Compute SHA-256 cache key for output layer.

    :param rename_fingerprint: Digest of the registered ``rename_symbol`` and
        ``resolve_collision`` hooks, from
        :func:`headerkit._rename.rename_cache_fingerprint`. Renaming changes the
        identifiers in the generated output, so leaving it out of the key makes
        a changed rename rule read back the previous output under the previous
        names and report a hit.
    :param transforms_fingerprint: Ordered list of ``cache_fingerprint()`` dicts
        from the Layer-2 ``transforms`` pipeline.
    :returns: Hex digest string.
    """
    hasher = hashlib.sha256()

    hasher.update(f"ir-key:{ir_cache_key}\0".encode())
    hasher.update(f"writer:{writer_name}\0".encode())

    if writer_options:
        for key in sorted(writer_options.keys()):
            canonical = json.dumps(writer_options[key], sort_keys=True, separators=(",", ":"))
            hasher.update(f"writer-opt:{key}={canonical}\0".encode())

    if writer_cache_version is not None:
        hasher.update(f"writer-version:{writer_cache_version}\0".encode())

    if rename_fingerprint is not None:
        hasher.update(f"rename:{rename_fingerprint}\0".encode())

    if transforms_fingerprint:
        for fp in transforms_fingerprint:
            canonical = json.dumps(dict(fp), sort_keys=True, separators=(",", ":"))
            hasher.update(f"transform:{canonical}\0".encode())

    return hasher.hexdigest()
