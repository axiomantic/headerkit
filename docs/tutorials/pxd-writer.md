# Tutorial: Building a PXD Writer (Cython)

This tutorial walks through building a clangir writer that generates Cython `.pxd` declaration files from parsed C headers. By the end, you will have a working writer that handles functions, structs, enums, and typedefs.

## What Are .pxd Files?

Cython uses `.pxd` files to declare C interfaces. They are similar to C header files but use Cython syntax, allowing Python code to call C functions directly. A `.pxd` file tells Cython about the types and function signatures available in a C library.

For example, given this C header:

```c
typedef struct {
    double x;
    double y;
} Point;

double point_distance(Point a, Point b);
```

The corresponding `.pxd` file would be:

```cython
cdef extern from "point.h":
    ctypedef struct Point:
        double x
        double y

    double point_distance(Point a, Point b)
```

## Step 1: Project Setup

Create a file for the writer:

```python
# pxd_writer.py
"""Generate Cython .pxd declarations from clangir IR."""

from __future__ import annotations

from clangir.ir import (
    Array,
    CType,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
```

## Step 2: Type Conversion

The first building block is converting IR type expressions to Cython type strings. Cython type syntax is nearly identical to C, so this is straightforward:

```python
def type_to_pxd(t: TypeExpr) -> str:
    """Convert an IR type expression to Cython syntax."""
    if isinstance(t, CType):
        if t.qualifiers:
            return f"{' '.join(t.qualifiers)} {t.name}"
        return t.name

    elif isinstance(t, Pointer):
        if isinstance(t.pointee, FunctionPointer):
            # Function pointer: rendered inline
            return _function_pointer_to_pxd(t.pointee)
        return f"{type_to_pxd(t.pointee)}*"

    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{type_to_pxd(t.element_type)}[{size_str}]"

    elif isinstance(t, FunctionPointer):
        return _function_pointer_to_pxd(t)

    return str(t)


def _function_pointer_to_pxd(fp: FunctionPointer) -> str:
    """Convert a function pointer to Cython syntax."""
    params = ", ".join(_param_to_pxd(p) for p in fp.parameters)
    if fp.is_variadic:
        params = f"{params}, ..." if params else "..."
    if not params:
        params = "void"
    return f"{type_to_pxd(fp.return_type)} (*)({params})"


def _param_to_pxd(p: Parameter) -> str:
    """Convert a parameter to Cython syntax."""
    type_str = type_to_pxd(p.type)
    if p.name:
        return f"{type_str} {p.name}"
    return type_str
```

## Step 3: Declaration Handlers

Now write handlers for each declaration type. In `.pxd` files, everything lives inside a `cdef extern from` block and is indented:

```python
def _struct_to_pxd(decl: Struct, indent: str = "    ") -> list[str]:
    """Convert a struct/union to Cython .pxd lines."""
    if decl.name is None:
        return []

    keyword = "union" if decl.is_union else "struct"
    prefix = "ctypedef " if decl.is_typedef else ""

    if not decl.fields:
        # Opaque struct -- forward declaration
        return [f"{indent}{prefix}{keyword} {decl.name}"]

    lines = [f"{indent}{prefix}{keyword} {decl.name}:"]
    for field in decl.fields:
        if isinstance(field.type, Array):
            size_str = str(field.type.size) if field.type.size is not None else ""
            lines.append(
                f"{indent}    {type_to_pxd(field.type.element_type)} {field.name}[{size_str}]"
            )
        elif isinstance(field.type, FunctionPointer):
            fp = field.type
            fp_params = ", ".join(_param_to_pxd(p) for p in fp.parameters)
            if fp.is_variadic:
                fp_params = f"{fp_params}, ..." if fp_params else "..."
            lines.append(
                f"{indent}    {type_to_pxd(fp.return_type)} (*{field.name})({fp_params})"
            )
        else:
            lines.append(f"{indent}    {type_to_pxd(field.type)} {field.name}")

    return lines


def _enum_to_pxd(decl: Enum, indent: str = "    ") -> list[str]:
    """Convert an enum to Cython .pxd lines."""
    if not decl.values:
        return []

    prefix = "ctypedef " if decl.is_typedef else ""
    name = decl.name or ""

    lines = [f"{indent}{prefix}enum {name}:"]
    for v in decl.values:
        lines.append(f"{indent}    {v.name}")

    return lines


def _function_to_pxd(decl: Function, indent: str = "    ") -> list[str]:
    """Convert a function to Cython .pxd lines."""
    params = ", ".join(_param_to_pxd(p) for p in decl.parameters)
    if decl.is_variadic:
        params = f"{params}, ..." if params else "..."
    if not params:
        params = "void"

    return [f"{indent}{type_to_pxd(decl.return_type)} {decl.name}({params})"]


def _typedef_to_pxd(decl: Typedef, indent: str = "    ") -> list[str]:
    """Convert a typedef to Cython .pxd lines."""
    underlying = decl.underlying_type

    # Function pointer typedef
    if isinstance(underlying, Pointer) and isinstance(
        underlying.pointee, FunctionPointer
    ):
        fp = underlying.pointee
        fp_params = ", ".join(_param_to_pxd(p) for p in fp.parameters)
        if fp.is_variadic:
            fp_params = f"{fp_params}, ..." if fp_params else "..."
        return [
            f"{indent}ctypedef {type_to_pxd(fp.return_type)} (*{decl.name})({fp_params})"
        ]

    return [f"{indent}ctypedef {type_to_pxd(underlying)} {decl.name}"]


def _variable_to_pxd(decl: Variable, indent: str = "    ") -> list[str]:
    """Convert a variable to Cython .pxd lines."""
    return [f"{indent}{type_to_pxd(decl.type)} {decl.name}"]
```

## Step 4: The Writer Class

Assemble the declaration handlers into a full writer class:

```python
from clangir.writers import register_writer


class PxdWriter:
    """Writer that generates Cython .pxd declaration files."""

    def __init__(self, header_name: str | None = None) -> None:
        self._header_name = header_name

    def write(self, header: Header) -> str:
        """Convert header IR to Cython .pxd format."""
        # Use provided header name or fall back to the header's path
        extern_name = self._header_name or header.path

        lines: list[str] = []
        lines.append(f'cdef extern from "{extern_name}":')

        for decl in header.declarations:
            decl_lines = self._convert_declaration(decl)
            if decl_lines:
                lines.append("")  # Blank line between declarations
                lines.extend(decl_lines)

        lines.append("")  # Trailing newline
        return "\n".join(lines)

    def _convert_declaration(self, decl) -> list[str]:
        if isinstance(decl, Struct):
            return _struct_to_pxd(decl)
        elif isinstance(decl, Enum):
            return _enum_to_pxd(decl)
        elif isinstance(decl, Function):
            return _function_to_pxd(decl)
        elif isinstance(decl, Typedef):
            return _typedef_to_pxd(decl)
        elif isinstance(decl, Variable):
            return _variable_to_pxd(decl)
        return []

    @property
    def name(self) -> str:
        return "pxd"

    @property
    def format_description(self) -> str:
        return "Cython .pxd declaration files"


# Register the writer
register_writer("pxd", PxdWriter, description="Cython .pxd declaration files")
```

## Step 5: Try It Out

Test the writer with a sample header:

```python
from clangir import get_backend, get_writer

# Make sure pxd_writer is imported so it registers itself
import pxd_writer  # noqa: F401

code = """
typedef struct {
    double x;
    double y;
} Point;

typedef enum {
    COLOR_RED,
    COLOR_GREEN,
    COLOR_BLUE,
} Color;

Point point_add(Point a, Point b);
double point_distance(Point a, Point b);
void point_print(Point p, const char *fmt, ...);
"""

backend = get_backend()
header = backend.parse(code, "point.h")

writer = get_writer("pxd")
print(writer.write(header))
```

Expected output:

```cython
cdef extern from "point.h":

    ctypedef struct Point:
        double x
        double y

    ctypedef enum Color:
        COLOR_RED
        COLOR_GREEN
        COLOR_BLUE

    Point point_add(Point a, Point b)

    double point_distance(Point a, Point b)

    void point_print(Point p, const char *fmt, ...)
```

## Step 6: Edge Cases to Consider

A production-quality `.pxd` writer would also need to handle:

- **Nested struct pointers** -- when one struct contains a pointer to another struct
- **Opaque types** -- structs with no fields should emit a forward declaration only
- **Anonymous enums** -- skip the name or use a generated name
- **C++ classes** -- use `cppclass` instead of `struct` when `decl.is_cppclass` is `True`
- **Namespaces** -- wrap declarations in `namespace` blocks when `decl.namespace` is set
- **Array fields** -- emit `type name[size]` syntax inside structs
- **Const qualifiers** -- preserve `const` on pointer targets

## What's Next

- [Writing Custom Writers](../guides/custom-writers.md) -- the general guide for writer development
- [ctypes Writer Tutorial](ctypes-writer.md) -- another writer tutorial with a different target format
- [Architecture Overview](../guides/architecture.md) -- understand the full backend/IR/writer pipeline
