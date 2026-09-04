"""Base class and common helpers for Headerkit writers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from headerkit.ir import Header, SourceUnit
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions


@dataclass(frozen=True)
class WriterOption:
    """Specification of a writer-configurable option or parameter."""

    name: str
    description: str
    default: Any = None
    choices: tuple[str, ...] | None = None
    type: type = str


class BaseWriter:
    """Base class providing unified layout generation for all output writers."""

    name: str = ""
    format_description: str = ""
    default_output_pattern: str = "{dir}/{stem}.txt"
    default_extension: str = ".txt"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file",)
    supported_options: ClassVar[tuple[WriterOption, ...]] = ()

    def write_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> ProjectLayout:
        """Convert parsed unit IR into a complete ProjectLayout."""
        opts = options or ScaffoldOptions(target_language=self.name, layout="file")
        if opts.layout not in self.supported_layouts:
            raise ValueError(
                f"Writer '{self.name}' does not support layout '{opts.layout}'. "
                f"Supported layouts: {list(self.supported_layouts)}"
            )

        if opts.layout == "file":
            return self._write_single_file_layout(unit, opts)
        elif opts.layout in ("package", "project"):
            return self._write_package_layout(unit, opts)
        return self._write_custom_layout(unit, opts)

    def _write_single_file_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Default single file layout: one file containing the rendered output."""
        content = self._render(unit)
        filename = f"{options.package_name}{self.default_extension}"
        return ProjectLayout(files=[OutputFile(path=filename, content=content)])

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Generate full package layout. Subclasses should override this."""
        return self._write_single_file_layout(unit, options)

    def _write_custom_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Generate custom layout defined by subclass."""
        return self._write_package_layout(unit, options)

    def _render(self, unit: SourceUnit | Header) -> str:
        """Render unit to string representation. Subclasses implement this."""
        raise NotImplementedError

    def write(self, header: Header | SourceUnit) -> str:
        """Convert parsed header IR to the target output format.

        Delegates to write_layout(layout='file') to satisfy the Zero-Dual-System Rule.
        """
        layout = self.write_layout(header, ScaffoldOptions(package_name="output", layout="file"))
        return layout.files[0].content
