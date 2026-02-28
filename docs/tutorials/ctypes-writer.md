# Tutorial: Building a ctypes Writer

This tutorial walks through building a clangir writer that generates Python `ctypes` binding code. The writer produces a standalone `.py` module that uses `ctypes` to load a shared library and expose its functions, structs, and enums as Python objects.

## What Is ctypes?

Python's built-in [`ctypes`](https://docs.python.org/3/library/ctypes.html) module provides C-compatible data types and lets you call functions in shared libraries directly from Python, without compiling any C extension code. Unlike CFFI, ctypes requires no build step.

For example, given a C header:

```c
int add(int a, int b);

typedef struct {
    double x;
    double y;
} Point;
```

The ctypes bindings would look like:

```python
import ctypes

lib = ctypes.CDLL("./libmylib.so")

lib.add.argtypes = [ctypes.c_int, ctypes.c_int]
lib.add.restype = ctypes.c_int

class Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
    ]
```

## Step 1: Type Mapping

The core challenge is mapping C types to ctypes equivalents. Create a lookup table for primitive types:

```python
# ctypes_writer.py
"""Generate ctypes bindings from clangir IR."""

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

# Mapping from C type names to ctypes type names
CTYPE_MAP = {
    "void": "None",
    "char": "ctypes.c_char",
    "signed char": "ctypes.c_byte",
    "unsigned char": "ctypes.c_ubyte",
    "short": "ctypes.c_short",
    "unsigned short": "ctypes.c_ushort",
    "int": "ctypes.c_int",
    "unsigned int": "ctypes.c_uint",
    "long": "ctypes.c_long",
    "unsigned long": "ctypes.c_ulong",
    "long long": "ctypes.c_longlong",
    "unsigned long long": "ctypes.c_ulonglong",
    "float": "ctypes.c_float",
    "double": "ctypes.c_double",
    "long double": "ctypes.c_longdouble",
    "size_t": "ctypes.c_size_t",
    "ssize_t": "ctypes.c_ssize_t",
    "int8_t": "ctypes.c_int8",
    "uint8_t": "ctypes.c_uint8",
    "int16_t": "ctypes.c_int16",
    "uint16_t": "ctypes.c_uint16",
    "int32_t": "ctypes.c_int32",
    "uint32_t": "ctypes.c_uint32",
    "int64_t": "ctypes.c_int64",
    "uint64_t": "ctypes.c_uint64",
    "_Bool": "ctypes.c_bool",
    "bool": "ctypes.c_bool",
}
```

## Step 2: Type Conversion Function

Build a recursive type converter that handles pointers, arrays, and function pointers:

```python
def type_to_ctypes(t: TypeExpr) -> str:
    """Convert an IR type expression to a ctypes type string."""
    if isinstance(t, CType):
        # Check for const char* (handled at pointer level)
        # Build the full type name including qualifiers like "unsigned"
        full_name = t.name
        type_qualifiers = []
        for q in t.qualifiers:
            if q in ("unsigned", "signed", "long", "short"):
                full_name = f"{q} {full_name}"
            else:
                type_qualifiers.append(q)

        return CTYPE_MAP.get(full_name, full_name)

    elif isinstance(t, Pointer):
        # Special case: char* -> c_char_p, const char* -> c_char_p
        if isinstance(t.pointee, CType) and t.pointee.name == "char":
            return "ctypes.c_char_p"
        # void* -> c_void_p
        if isinstance(t.pointee, CType) and t.pointee.name == "void":
            return "ctypes.c_void_p"
        # Function pointer
        if isinstance(t.pointee, FunctionPointer):
            return _funcptr_to_ctypes(t.pointee)
        # General pointer
        inner = type_to_ctypes(t.pointee)
        return f"ctypes.POINTER({inner})"

    elif isinstance(t, Array):
        elem = type_to_ctypes(t.element_type)
        if t.size is not None and isinstance(t.size, int):
            return f"({elem} * {t.size})"
        return f"ctypes.POINTER({elem})"

    elif isinstance(t, FunctionPointer):
        return _funcptr_to_ctypes(t)

    return f"ctypes.c_void_p  # unknown: {t}"


def _funcptr_to_ctypes(fp: FunctionPointer) -> str:
    """Convert a function pointer to a ctypes CFUNCTYPE expression."""
    restype = type_to_ctypes(fp.return_type)
    argtypes = [type_to_ctypes(p.type) for p in fp.parameters]
    all_types = [restype] + argtypes
    return f"ctypes.CFUNCTYPE({', '.join(all_types)})"
```

## Step 3: Declaration Handlers

Write functions to generate ctypes code for each declaration type:

```python
def _struct_to_ctypes(decl: Struct) -> list[str]:
    """Generate a ctypes Structure or Union subclass."""
    if decl.name is None:
        return []

    base = "ctypes.Union" if decl.is_union else "ctypes.Structure"

    lines = [
        f"class {decl.name}({base}):",
    ]

    if not decl.fields:
        lines.append("    pass")
        return lines

    lines.append("    _fields_ = [")
    for field in decl.fields:
        ctype = type_to_ctypes(field.type)
        lines.append(f'        ("{field.name}", {ctype}),')
    lines.append("    ]")

    return lines


def _enum_to_ctypes(decl: Enum) -> list[str]:
    """Generate enum constants as module-level integers."""
    if not decl.values:
        return []

    lines = []
    if decl.name:
        lines.append(f"# enum {decl.name}")
    auto_value = 0
    for v in decl.values:
        if v.value is not None and isinstance(v.value, int):
            lines.append(f"{v.name} = {v.value}")
            auto_value = v.value + 1
        else:
            lines.append(f"{v.name} = {auto_value}")
            auto_value += 1

    return lines


def _function_to_ctypes(decl: Function) -> list[str]:
    """Generate ctypes function binding setup code."""
    lines = []
    # argtypes
    argtypes = [type_to_ctypes(p.type) for p in decl.parameters]
    lines.append(f"lib.{decl.name}.argtypes = [{', '.join(argtypes)}]")
    # restype
    restype = type_to_ctypes(decl.return_type)
    lines.append(f"lib.{decl.name}.restype = {restype}")

    return lines


def _typedef_to_ctypes(decl: Typedef) -> list[str]:
    """Generate a type alias."""
    ctype = type_to_ctypes(decl.underlying_type)
    return [f"{decl.name} = {ctype}"]
```

## Step 4: The Writer Class

Assemble everything into a writer:

```python
from clangir.writers import register_writer


class CtypesWriter:
    """Writer that generates Python ctypes binding code."""

    def __init__(self, library_name: str = "mylib") -> None:
        self._library_name = library_name

    def write(self, header: Header) -> str:
        lines = [
            '"""Auto-generated ctypes bindings."""',
            "",
            "import ctypes",
            "import os",
            "",
            f'lib = ctypes.CDLL(os.path.join(os.path.dirname(__file__), "lib{self._library_name}.so"))',
            "",
        ]

        # Emit structs and unions first (functions may reference them)
        for decl in header.declarations:
            if isinstance(decl, Struct):
                struct_lines = _struct_to_ctypes(decl)
                if struct_lines:
                    lines.extend(struct_lines)
                    lines.append("")

        # Emit enums
        for decl in header.declarations:
            if isinstance(decl, Enum):
                enum_lines = _enum_to_ctypes(decl)
                if enum_lines:
                    lines.extend(enum_lines)
                    lines.append("")

        # Emit typedefs
        for decl in header.declarations:
            if isinstance(decl, Typedef):
                td_lines = _typedef_to_ctypes(decl)
                if td_lines:
                    lines.extend(td_lines)
                    lines.append("")

        # Emit function bindings
        for decl in header.declarations:
            if isinstance(decl, Function):
                fn_lines = _function_to_ctypes(decl)
                if fn_lines:
                    lines.extend(fn_lines)
                    lines.append("")

        return "\n".join(lines)

    @property
    def name(self) -> str:
        return "ctypes"

    @property
    def format_description(self) -> str:
        return "Python ctypes binding code"


# Register the writer
register_writer("ctypes", CtypesWriter, description="Python ctypes binding code")
```

## Step 5: Try It Out

```python
from clangir import get_backend, get_writer
import ctypes_writer  # noqa: F401  -- triggers registration

code = """
typedef struct {
    double x;
    double y;
} Point;

typedef enum {
    SHAPE_CIRCLE = 0,
    SHAPE_RECT = 1,
    SHAPE_TRIANGLE = 2,
} ShapeType;

Point point_add(Point a, Point b);
double point_distance(Point a, Point b);
void point_print(Point p, const char *label);
"""

backend = get_backend()
header = backend.parse(code, "point.h")

writer = get_writer("ctypes", library_name="point")
print(writer.write(header))
```

Expected output:

```python
"""Auto-generated ctypes bindings."""

import ctypes
import os

lib = ctypes.CDLL(os.path.join(os.path.dirname(__file__), "libpoint.so"))

class Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
    ]

# enum ShapeType
SHAPE_CIRCLE = 0
SHAPE_RECT = 1
SHAPE_TRIANGLE = 2

lib.point_add.argtypes = [Point, Point]
lib.point_add.restype = Point

lib.point_distance.argtypes = [Point, Point]
lib.point_distance.restype = ctypes.c_double

lib.point_print.argtypes = [Point, ctypes.c_char_p]
lib.point_print.restype = None
```

## Improvements for Production

A production-quality ctypes writer would benefit from:

- **Platform-aware library loading** -- use `.dylib` on macOS, `.dll` on Windows
- **Struct forward references** -- when struct A has a pointer to struct B that is declared later
- **Opaque pointers** -- emit `c_void_p` for structs with no fields
- **Callback typedefs** -- generate `CFUNCTYPE` wrappers for function pointer typedefs
- **Error checking** -- add `errcheck` hooks for functions that return error codes

## What's Next

- [PXD Writer Tutorial](pxd-writer.md) -- building a Cython writer
- [Writing Custom Writers](../guides/custom-writers.md) -- the general writer development guide
- [JSON Export Tutorial](json-export.md) -- using the built-in JSON writer
