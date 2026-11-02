"""Minimal transitional types for writers while writer scaffolding methods are retired."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from headerkit.ir import Declaration, Function, Header, SourceUnit


@dataclass(frozen=True, slots=True)
class OutputFile:
    """A single file to be written in a project layout."""

    path: str
    content: str
    is_executable: bool = False
    preserve_existing: bool = False
    merge_strategy: str | None = None


@dataclass(slots=True)
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
            if p.exists() and (f.preserve_existing or not overwrite):
                continue
            p.write_text(f.content, encoding="utf-8")
            if f.is_executable:
                p.chmod(p.stat().st_mode | 0o111)
            written.append(p)
        return written


@dataclass(slots=True)
class ScaffoldOptions:
    """Options container retained for writer layout methods."""

    package_name: str = "bindings"
    target_language: str = "nim"
    layout: str = "file"
    options: dict[str, Any] = field(default_factory=dict)
    interactive: bool = False
    extra_context: dict[str, Any] = field(default_factory=dict)
    test_type: str = "both"
    test_runner: str = "tripwire"
    test_strategy: str = "incremental"
    version_guard: str = "declared"
    version_constant: str | None = None
    macro_types: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if "test_type" not in self.options and self.test_type != "both":
            self.options["test_type"] = self.test_type
        elif "test_type" in self.options:
            self.test_type = str(self.options["test_type"])

        if "macro_types" not in self.options and self.macro_types:
            self.options["macro_types"] = self.macro_types
        elif "macro_types" in self.options and not self.macro_types:
            self.macro_types = dict(self.options["macro_types"])

    def get_option(self, name: str, default: Any = None) -> Any:
        """Get a writer-specific option value."""
        if name in self.options:
            return self.options[name]
        if hasattr(self, name):
            return getattr(self, name)
        return default


def extract_function_names(unit: SourceUnit | Header) -> list[str]:
    """Extract top-level function names."""
    decls: list[Declaration] = getattr(unit, "declarations", [])
    return [d.name for d in decls if isinstance(d, Function) and d.name]
