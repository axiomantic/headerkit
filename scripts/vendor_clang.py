#!/usr/bin/env python3
"""Vendor clang Python bindings for a new LLVM version."""

import argparse
import hashlib
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def find_nearest_version(clang_dir: Path, target: int) -> str:
    """Find the nearest existing vendored version to the target.

    Prefers the highest version below the target. Falls back to the
    nearest version above if none exist below.

    Raises RuntimeError if no vendored versions exist.
    """
    existing: list[int] = []
    for entry in clang_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("v"):
            try:
                existing.append(int(entry.name[1:]))
            except ValueError:
                continue

    if not existing:
        raise RuntimeError(f"No existing vendored versions found in {clang_dir}")

    existing.sort()

    # Prefer highest version below target
    below = [v for v in existing if v < target]
    if below:
        return str(below[-1])

    # Fall back to nearest above
    above = [v for v in existing if v > target]
    if above:
        return str(above[0])

    # Exact match only (shouldn't reach here if directory already exists check ran)
    return str(existing[0])


def download_cindex(tag: str) -> bytes:
    """Download cindex.py from the LLVM GitHub repository.

    Returns the raw bytes of the downloaded file.
    """
    url = f"https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-{tag}/clang/bindings/python/clang/cindex.py"
    print(f"Downloading cindex.py from {url}")
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def write_provenance(provenance_path: Path, tag: str, sha256_hex: str) -> None:
    """Write a PROVENANCE file matching the existing format."""
    today = datetime.now(timezone.utc).date().isoformat()
    source_url = f"https://github.com/llvm/llvm-project/blob/llvmorg-{tag}/clang/bindings/python/clang/cindex.py"
    content = (
        f"source: {source_url}\n"
        f"sha256: {sha256_hex}\n"
        f"llvm_version: {tag}\n"
        f"vendored_date: {today}\n"
        f"license: Apache-2.0 WITH LLVM-exception\n"
    )
    provenance_path.write_text(content)


def copy_pyi_stubs(source_dir: Path, dest_dir: Path) -> list[str]:
    """Copy .pyi stub files from source version directory to dest.

    Returns list of copied filenames.
    """
    copied = []
    for pyi_file in sorted(source_dir.glob("*.pyi")):
        shutil.copy2(pyi_file, dest_dir / pyi_file.name)
        copied.append(pyi_file.name)
    return copied


def update_init_py(init_path: Path, major: str) -> None:
    """Update VENDORED_VERSIONS and LATEST_VENDORED in __init__.py."""
    content = init_path.read_text()

    # Parse current VENDORED_VERSIONS tuple
    match = re.search(r"VENDORED_VERSIONS\s*=\s*\(([^)]+)\)", content)
    if not match:
        raise RuntimeError("Could not find VENDORED_VERSIONS in __init__.py")

    raw_versions = match.group(1)
    versions = [v.strip().strip('"').strip("'") for v in raw_versions.split(",") if v.strip()]

    if major not in versions:
        versions.append(major)
        versions.sort(key=int)

    new_tuple = "(" + ", ".join(f'"{v}"' for v in versions) + ")"
    content = re.sub(
        r"VENDORED_VERSIONS\s*=\s*\([^)]+\)",
        f"VENDORED_VERSIONS = {new_tuple}",
        content,
    )

    # Update LATEST_VENDORED if this is the newest
    latest = max(versions, key=int)
    content = re.sub(
        r'LATEST_VENDORED\s*=\s*"[^"]+"',
        f'LATEST_VENDORED = "{latest}"',
        content,
    )

    init_path.write_text(content)


def vendor(major: str, tag: str, repo_root: Path) -> None:
    """Vendor clang bindings for a new LLVM version.

    Creates the version directory, downloads cindex.py, writes
    PROVENANCE and __init__.py, copies .pyi stubs from the nearest
    version, and updates the package __init__.py.

    Raises FileExistsError if the version directory already exists.
    """
    clang_dir = repo_root / "headerkit" / "_clang"
    version_dir = clang_dir / f"v{major}"

    if version_dir.exists():
        raise FileExistsError(f"Version directory already exists: {version_dir}")

    # Download cindex.py
    cindex_bytes = download_cindex(tag)
    sha256_hex = hashlib.sha256(cindex_bytes).hexdigest()

    # Create directory structure
    version_dir.mkdir(parents=True)
    print(f"Created {version_dir}")

    # Write cindex.py
    (version_dir / "cindex.py").write_bytes(cindex_bytes)
    print(f"Wrote cindex.py ({len(cindex_bytes)} bytes, sha256: {sha256_hex})")

    # Write empty __init__.py
    (version_dir / "__init__.py").write_text("")

    # Write PROVENANCE
    provenance_path = version_dir / "PROVENANCE"
    write_provenance(provenance_path, tag, sha256_hex)
    print(f"Wrote {provenance_path}")

    # Copy .pyi stubs from nearest version
    nearest = find_nearest_version(clang_dir, int(major))
    nearest_dir = clang_dir / f"v{nearest}"
    copied = copy_pyi_stubs(nearest_dir, version_dir)
    if copied:
        print(f"Copied .pyi stubs from v{nearest}: {', '.join(copied)}")
    else:
        print(f"No .pyi stubs found in v{nearest} to copy")

    # Update __init__.py
    init_path = clang_dir / "__init__.py"
    update_init_py(init_path, major)
    print(f"Updated {init_path}")

    print(f"Successfully vendored LLVM {tag} as v{major}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on error."""
    parser = argparse.ArgumentParser(description="Vendor clang Python bindings for a new LLVM version.")
    parser.add_argument("major", help="LLVM major version (e.g. 22)")
    parser.add_argument("tag", help="Full LLVM tag (e.g. 22.1.0)")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to parent of scripts/ dir)",
    )
    args = parser.parse_args(argv)

    if args.repo_root is None:
        args.repo_root = Path(__file__).resolve().parent.parent

    try:
        vendor(args.major, args.tag, args.repo_root)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
