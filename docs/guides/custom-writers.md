# Writing Custom Writers

HeaderKit writers convert Intermediate Representation (IR) into target code, documentation, or configuration files. HeaderKit ships with built-in writers for Python (`ctypes`, `cffi`, `cython`), systems languages (`mojo`, `nim`), C shims (`cshim`), `lua` (LuaJIT FFI), `diff`, `json`, and `prompt` (LLM context). You can easily create custom writers for any additional target language, documentation generator, or code-generation pipeline.

## The BaseWriter Class

The standard and recommended way to create a writer is by inheriting from [`BaseWriter`][headerkit.writers.BaseWriter].

`BaseWriter` handles single-string rendering (`writer.write(unit)`), layout scaffolding (`writer.write_layout(unit, options)`), layout validation, and option declarations out of the box:

```python
from headerkit.ir import SourceUnit
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers import BaseWriter, WriterOption, register_writer


class MarkdownWriter(BaseWriter):
    """Writer that generates Markdown API documentation."""

    name: str = "markdown"
    format_description: str = "Markdown API documentation"
    default_output_pattern: str = "{dir}/{stem}.md"
    default_extension: str = ".md"

    # Declare supported layout modes and options
    supported_layouts: tuple[str, ...] = ("file", "package")
    supported_options: tuple[WriterOption, ...] = (
        WriterOption(
            name="include_source_locations",
            description="Include source file and line info in documentation",
            default=False,
        ),
    )

    def __init__(self, include_source_locations: bool = False) -> None:
        self._include_locations = include_source_locations

    def _render(self, unit: SourceUnit) -> str:
        """Render the primary documentation string from the parsed unit."""
        lines = [f"# API Reference: {unit.path}", ""]
        for decl in unit.declarations:
            lines.append(f"- **{decl.name}** ({type(decl).__name__})")
        return "\n".join(lines) + "\n"

    def _write_package_layout(
        self,
        unit: SourceUnit,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        """Optional: generate a multi-file documentation site layout."""
        pkg = options.package_name or "api_docs"
        return ProjectLayout(
            files=[
                OutputFile(path="mkdocs.yml", content=f"site_name: {pkg}\n"),
                OutputFile(path="docs/index.md", content=self._render(unit)),
            ]
        )
```

### What `BaseWriter` Provides

Inheriting from `BaseWriter` gives you:

1. **`write(unit)`**: Returns the rendered string output.
2. **`write_layout(unit, options)`**: Produces single-file or multi-file package layouts according to `options.layout`.
3. **CLI Integration**: Works immediately with the CLI for single files or scaffolded packages:
   ```bash
   # Single-file output (default)
   headerkit mylib.h -w markdown -o docs.md

   # Package scaffolding
   headerkit mylib.h -w markdown --layout package --package-name mydocs -o ./docs_site
   ```
4. **Introspection**: Supported options and layouts are automatically queryable via `list_writer_options("markdown")` and `list_writer_layouts("markdown")`.

---

## Registering a Writer

Use [`register_writer()`][headerkit.writers.register_writer] to add your writer to HeaderKit's registry:

```python
from headerkit.writers import register_writer

register_writer(
    "markdown",
    MarkdownWriter,
    description="Markdown API documentation",
)
```

Parameters:

- `name` -- The lookup key for `get_writer(name)` and CLI `-w <name>`
- `writer_class` -- The writer class inheriting from `BaseWriter`
- `is_default` -- If `True`, this becomes the default writer for `get_writer()`
- `description` -- Short description; falls back to the class docstring's first line if not provided

!!! warning "Unique names"
    `register_writer()` raises `ValueError` if a writer with the same name is already registered. Choose a unique name for your writer.

## Complete Example: Markdown Documentation Writer

Here is a complete writer that generates Markdown documentation from a parsed C header:

```python
"""Generate Markdown API documentation from headerkit IR."""

from __future__ import annotations

from headerkit.ir import (
    Constant,
    Declaration,
    Enum,
    Function,
    Header,
    Struct,
    Typedef,
    Variable,
from headerkit.ir import (
    Constant,
    Declaration,
    Enum,
    Function,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers import BaseWriter, WriterOption, register_writer


class MarkdownWriter(BaseWriter):
    """Writer that generates Markdown API documentation."""

    name: str = "markdown"
    format_description: str = "Markdown API documentation"
    default_output_pattern: str = "{dir}/{stem}.md"
    default_extension: str = ".md"

    supported_options: tuple[WriterOption, ...] = (
        WriterOption(
            name="include_source_locations",
            description="Include source file and line info in documentation",
            default=False,
        ),
    )

    def __init__(self, include_source_locations: bool = False) -> None:
        self._include_locations = include_source_locations

    def _render(self, unit: SourceUnit) -> str:
        lines = [f"# API Reference: `{unit.path}`", ""]

        # Group declarations by kind
        structs = [d for d in unit.declarations if isinstance(d, Struct)]
        enums = [d for d in unit.declarations if isinstance(d, Enum)]
        functions = [d for d in unit.declarations if isinstance(d, Function)]
        typedefs = [d for d in unit.declarations if isinstance(d, Typedef)]
        constants = [d for d in unit.declarations if isinstance(d, Constant)]

        if structs:
            lines.append("## Structures")
            lines.append("")
            for s in structs:
                lines.extend(self._format_struct(s))

        if enums:
            lines.append("## Enumerations")
            lines.append("")
            for e in enums:
                lines.extend(self._format_enum(e))

        if functions:
            lines.append("## Functions")
            lines.append("")
            for f in functions:
                lines.extend(self._format_function(f))

        if typedefs:
            lines.append("## Type Aliases")
            lines.append("")
            for t in typedefs:
                lines.append(f"- `{t.name}` -- alias for `{t.underlying_type}`")
            lines.append("")

        if constants:
            lines.append("## Constants")
            lines.append("")
            for c in constants:
                if c.value is not None:
                    lines.append(f"- `{c.name}` = `{c.value}`")
                else:
                    lines.append(f"- `{c.name}`")
            lines.append("")

        return "\n".join(lines)

    def _format_struct(self, s: Struct) -> list[str]:
        kind = "Union" if s.is_union else "Struct"
        lines = [f"### `{s.name}` ({kind})", ""]
        if s.fields:
            lines.append("| Field | Type |")
            lines.append("|-------|------|")
            for field in s.fields:
                lines.append(f"| `{field.name}` | `{field.type}` |")
        else:
            lines.append("*Opaque type*")
        lines.append("")
        return lines

    def _format_enum(self, e: Enum) -> list[str]:
        name = e.name or "(anonymous)"
        lines = [f"### `{name}`", ""]
        if e.values:
            lines.append("| Constant | Value |")
            lines.append("|----------|-------|")
            for v in e.values:
                val = str(v.value) if v.value is not None else "(auto)"
                lines.append(f"| `{v.name}` | {val} |")
        lines.append("")
        return lines

    def _format_function(self, f: Function) -> list[str]:
        params = ", ".join(
            f"{p.type} {p.name}" if p.name else str(p.type)
            for p in f.parameters
        )
        if f.is_variadic:
            params = f"{params}, ..." if params else "..."
        lines = [
            f"### `{f.name}`",
            "",
            f"```c",
            f"{f.return_type} {f.name}({params});",
            f"```",
            "",
        ]
        if self._include_locations and f.location:
            lines.append(
                f"*Defined at {f.location.file}:{f.location.line}*"
            )
            lines.append("")
        return lines


# Self-register
register_writer("markdown", MarkdownWriter, description="Markdown API documentation")
```

## Using Your Writer

Once registered, your writer is available through the standard API:

```python
from headerkit import get_backend, get_writer, list_writers

# List all available writers
print(list_writers())

# Use your writer
backend = get_backend()
unit = backend.parse(code, "mylib.h")

writer = get_writer("markdown", include_source_locations=True)
docs = writer.write(unit)
print(docs)
```

## Advanced: The WriterBackend Protocol

Under the hood, HeaderKit uses the [`WriterBackend`][headerkit.writers.WriterBackend] protocol (from `typing.Protocol`) to define the minimal structural contract for any writer:

```python
from typing import Protocol
from headerkit.ir import SourceUnit

class WriterBackend(Protocol):
    def write(self, header: SourceUnit) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def format_description(self) -> str: ...
```

Because [`BaseWriter`][headerkit.writers.BaseWriter] already implements `WriterBackend` while seamlessly providing layout scaffolding, option validation, and hook integration, you should virtually always inherit from `BaseWriter`.

## Handling IR Types

When writing a custom writer, you need to handle the various IR types. Here is a reference for the type-dispatch pattern:

```python
from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    Struct,
    Typedef,
    Variable,
)

def convert_type(t):
    """Convert a TypeExpr to your target format."""
    if isinstance(t, CType):
        # Base type: t.name, t.qualifiers
        ...
    elif isinstance(t, Pointer):
        # Pointer: t.pointee (recursive TypeExpr), t.qualifiers
        inner = convert_type(t.pointee)
        ...
    elif isinstance(t, Array):
        # Array: t.element_type (TypeExpr), t.size (int | str | None)
        elem = convert_type(t.element_type)
        ...
    elif isinstance(t, FunctionPointer):
        # Function pointer: t.return_type, t.parameters, t.is_variadic
        ...

def convert_declaration(decl):
    """Convert a Declaration to your target format."""
    if isinstance(decl, Struct):
        # decl.name, decl.fields, decl.is_union, decl.is_typedef
        ...
    elif isinstance(decl, Enum):
        # decl.name, decl.values (list of EnumValue)
        ...
    elif isinstance(decl, Function):
        # decl.name, decl.return_type, decl.parameters, decl.is_variadic
        ...
    elif isinstance(decl, Typedef):
        # decl.name, decl.underlying_type
        ...
    elif isinstance(decl, Variable):
        # decl.name, decl.type
        ...
    elif isinstance(decl, Constant):
        # decl.name, decl.value, decl.is_macro
        ...
```

## Packaging as a Plugin

To distribute your writer as a separate package, register it in your package's `__init__.py`:

```python
# mywriter/__init__.py
from headerkit.writers import register_writer
from mywriter.core import MarkdownWriter

register_writer("markdown", MarkdownWriter)
```

Users install your package and the writer becomes available:

```bash
pip install headerkit-markdown-writer
```

```python
# The import triggers registration
import mywriter  # noqa: F401

from headerkit import get_writer
writer = get_writer("markdown")
```
