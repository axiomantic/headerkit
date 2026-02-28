# Writing Custom Writers

clangir writers convert IR into output strings. You can create custom writers for any target format: Cython `.pxd` files, ctypes bindings, documentation, or anything else you need.

## The WriterBackend Protocol

Every writer must implement the [`WriterBackend`][clangir.writers.WriterBackend] protocol:

```python
from clangir.ir import Header
from clangir.writers import WriterBackend

class WriterBackend(Protocol):
    def write(self, header: Header) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def format_description(self) -> str: ...
```

### Method and Property Details

**`write(header)`** -- Convert a [`Header`][clangir.ir.Header] IR object into the target format string. Writers should produce best-effort output, silently skipping declarations they cannot represent. Writers must not raise exceptions for valid `Header` input.

**`name`** -- A human-readable name for the writer (e.g., `"markdown"`). This is the string users pass to `get_writer()`.

**`format_description`** -- A short description of the output format (e.g., `"Markdown API documentation"`). Used by `get_writer_info()`.

### Writer-Specific Options

Configuration options belong on the writer's `__init__()`, not on `write()`. This keeps the protocol simple and type-safe:

```python
class MarkdownWriter:
    def __init__(self, include_source_locations: bool = False) -> None:
        self._include_locations = include_source_locations

    def write(self, header: Header) -> str:
        # Use self._include_locations here
        ...
```

Users pass options through `get_writer()`:

```python
writer = get_writer("markdown", include_source_locations=True)
```

## Registering a Writer

Use [`register_writer()`][clangir.writers.register_writer] to add your writer to the global registry:

```python
from clangir.writers import register_writer

register_writer(
    "markdown",
    MarkdownWriter,
    description="Markdown API documentation",
)
```

Parameters:

- `name` -- The lookup key for `get_writer(name)`
- `writer_class` -- The class implementing `WriterBackend`
- `is_default` -- If `True`, this becomes the default writer
- `description` -- Short description; falls back to the class docstring's first line if not provided

!!! warning "Unique names"
    `register_writer()` raises `ValueError` if a writer with the same name is already registered. Choose a unique name for your writer.

## Complete Example: Markdown Documentation Writer

Here is a complete writer that generates Markdown documentation from a parsed C header:

```python
"""Generate Markdown API documentation from clangir IR."""

from __future__ import annotations

from clangir.ir import (
    Constant,
    Declaration,
    Enum,
    Function,
    Header,
    Struct,
    Typedef,
    Variable,
)
from clangir.writers import register_writer


class MarkdownWriter:
    """Writer that generates Markdown API documentation."""

    def __init__(self, include_source_locations: bool = False) -> None:
        self._include_locations = include_source_locations

    def write(self, header: Header) -> str:
        lines = [f"# API Reference: `{header.path}`", ""]

        # Group declarations by kind
        structs = [d for d in header.declarations if isinstance(d, Struct)]
        enums = [d for d in header.declarations if isinstance(d, Enum)]
        functions = [d for d in header.declarations if isinstance(d, Function)]
        typedefs = [d for d in header.declarations if isinstance(d, Typedef)]
        constants = [d for d in header.declarations if isinstance(d, Constant)]

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

    @property
    def name(self) -> str:
        return "markdown"

    @property
    def format_description(self) -> str:
        return "Markdown API documentation"


# Self-register
register_writer("markdown", MarkdownWriter, description="Markdown API documentation")
```

## Using Your Writer

Once registered, your writer is available through the standard API:

```python
from clangir import get_backend, get_writer, list_writers

# List all available writers
print(list_writers())  # ['cffi', 'json', 'markdown']

# Use your writer
backend = get_backend()
header = backend.parse(code, "mylib.h")

writer = get_writer("markdown", include_source_locations=True)
docs = writer.write(header)
print(docs)
```

## Handling IR Types

When writing a custom writer, you need to handle the various IR types. Here is a reference for the type-dispatch pattern:

```python
from clangir.ir import (
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
from clangir.writers import register_writer
from mywriter.core import MarkdownWriter

register_writer("markdown", MarkdownWriter)
```

Users install your package and the writer becomes available:

```bash
pip install clangir-markdown-writer
```

```python
# The import triggers registration
import mywriter  # noqa: F401

from clangir import get_writer
writer = get_writer("markdown")
```
