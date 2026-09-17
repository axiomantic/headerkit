"""Polyglot override scanner and JSON matrix manifest manager."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from headerkit.matrix.matrix import ConfigMatrix


class MalformedOverrideError(Exception):
    """Raised when an override block tag is unclosed or malformed."""


COMMENT_PREFIXES = {
    "nim": "#",
    "python": "#",
    "cython": "//",
    "c": "//",
    "cpp": "//",
    "lua": "--",
    "mojo": "#",
}


def extract_overrides(source_code: str, language: str = "nim") -> dict[str, str]:
    """Scan existing source code for override blocks, preserving developer modifications."""
    prefix = COMMENT_PREFIXES.get(language.lower(), "#")
    start_pattern = re.compile(rf"^{re.escape(prefix)}\s*@headerkit:override\s+([\w\.-]+)\s*$")
    end_pattern = re.compile(rf"^{re.escape(prefix)}\s*@headerkit:end-override\s+([\w\.-]+)\s*$")

    overrides: dict[str, str] = {}
    current_id: str | None = None
    current_lines: list[str] = []

    for line_idx, line in enumerate(source_code.splitlines(), start=1):
        stripped = line.strip()
        start_match = start_pattern.match(stripped)
        if start_match:
            if current_id is not None:
                raise MalformedOverrideError(
                    f"Nested override tag '{start_match.group(1)}' inside '{current_id}' at line {line_idx}"
                )
            current_id = start_match.group(1)
            current_lines = []
            continue

        end_match = end_pattern.match(stripped)
        if end_match:
            if current_id is None:
                raise MalformedOverrideError(f"Orphaned end-override tag '{end_match.group(1)}' at line {line_idx}")
            if end_match.group(1) != current_id:
                raise MalformedOverrideError(
                    f"Mismatched end-override tag '{end_match.group(1)}', expected '{current_id}' at line {line_idx}"
                )
            overrides[current_id] = "\n".join(current_lines)
            current_id = None
            current_lines = []
            continue

        if current_id is not None:
            current_lines.append(line)

    if current_id is not None:
        raise MalformedOverrideError(f"Unclosed override block '{current_id}' at end of file")

    return overrides


def inject_overrides(generated_code: str, overrides: dict[str, str], language: str = "nim") -> str:
    """Re-injects preserved manual override content into matching override slots in generated code."""
    if not overrides:
        return generated_code

    prefix = COMMENT_PREFIXES.get(language.lower(), "#")
    start_pattern = re.compile(rf"^{re.escape(prefix)}\s*@headerkit:override\s+([\w\.-]+)\s*$")
    end_pattern = re.compile(rf"^{re.escape(prefix)}\s*@headerkit:end-override\s+([\w\.-]+)\s*$")

    lines = generated_code.splitlines()
    output_lines: list[str] = []
    injected_ids: set[str] = set()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        start_match = start_pattern.match(stripped)
        if start_match:
            block_id = start_match.group(1)
            injected_ids.add(block_id)
            output_lines.append(line)
            idx += 1
            while idx < len(lines):
                end_line = lines[idx]
                end_match = end_pattern.match(end_line.strip())
                if end_match and end_match.group(1) == block_id:
                    if block_id in overrides:
                        output_lines.extend(overrides[block_id].splitlines())
                    output_lines.append(end_line)
                    break
                idx += 1
        else:
            output_lines.append(line)
        idx += 1

    # Preserve any unplaced override blocks (e.g. user-defined standalone blocks)
    remaining_ids = [k for k in overrides if k not in injected_ids]
    if remaining_ids:
        insert_idx = len(output_lines)
        manifest_end_idx = -1
        for i, line_content in enumerate(output_lines):
            if "@headerkit-matrix-manifest" in line_content:
                for j in range(i + 1, len(output_lines)):
                    if "===" in output_lines[j] or "---" in output_lines[j] or "===" in output_lines[j].strip():
                        manifest_end_idx = j + 1
                        break
                break
        if manifest_end_idx != -1:
            insert_idx = manifest_end_idx

        block_lines: list[str] = []
        for r_id in remaining_ids:
            block_lines.append("")
            block_lines.append(f"{prefix} @headerkit:override {r_id}")
            block_lines.extend(overrides[r_id].splitlines())
            block_lines.append(f"{prefix} @headerkit:end-override {r_id}")

        output_lines[insert_idx:insert_idx] = block_lines

    return "\n".join(output_lines) + ("\n" if generated_code.endswith("\n") else "")


@dataclass
class MatrixManifest:
    """Metadata manifest storing source header path and multi-dimensional matrix schema."""

    schema_version: int
    source_hash: str
    header_path: str
    matrix: ConfigMatrix

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_hash": self.source_hash,
            "header_path": self.header_path,
            "matrix": {
                "dimensions": [
                    {
                        "name": dim.name,
                        "values": list(dim.values),
                        "dimension_type": dim.dimension_type,
                    }
                    for dim in self.matrix.dimensions
                ]
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatrixManifest:
        matrix = ConfigMatrix()
        for dim_data in data.get("matrix", {}).get("dimensions", []):
            matrix.add_dimension(
                name=dim_data["name"],
                values=dim_data["values"],
                dimension_type=dim_data.get("dimension_type", "feature"),
            )
        return cls(
            schema_version=data.get("schema_version", 1),
            source_hash=data.get("source_hash", ""),
            header_path=data.get("header_path", ""),
            matrix=matrix,
        )

    @classmethod
    def from_json(cls, json_str: str) -> MatrixManifest:
        return cls.from_dict(json.loads(json_str))


def emit_manifest_comment(manifest: MatrixManifest, language: str = "nim") -> str:
    """Emits formatted header comment containing serialized JSON manifest."""
    prefix = COMMENT_PREFIXES.get(language.lower(), "#")
    sep = "=" * 77
    json_lines = manifest.to_json().splitlines()
    body = "\n".join(f"{prefix} {line}" if line else prefix for line in json_lines)
    return f"{prefix} {sep}\n{prefix} @headerkit-matrix-manifest\n{body}\n{prefix} {sep}\n"


def extract_manifest(source_code: str, language: str = "nim") -> MatrixManifest:
    """Extracts and parses embedded @headerkit-matrix-manifest from source comments."""
    prefix = COMMENT_PREFIXES.get(language.lower(), "#")
    tag = "@headerkit-matrix-manifest"
    lines = source_code.splitlines()
    found = False
    json_lines: list[str] = []
    prefix_stripped = prefix.strip()

    for line in lines:
        stripped = line.strip()
        if not found:
            if tag in stripped:
                found = True
            continue
        if stripped.startswith(f"{prefix_stripped} ==="):
            break
        if stripped.startswith(prefix_stripped):
            content = stripped[len(prefix_stripped) :].strip()
            if content.startswith("==="):
                break
            json_lines.append(content)
        else:
            break

    if not found or not json_lines:
        raise ValueError("No @headerkit-matrix-manifest comment block found in source code")

    json_str = "\n".join(json_lines)
    return MatrixManifest.from_json(json_str)
