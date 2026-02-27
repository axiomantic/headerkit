# Tutorial: Building a C Header Cleanup Writer

This tutorial builds a clangir writer that takes a parsed header and emits a clean, simplified C header. The writer strips preprocessor artifacts, normalizes typedefs, and filters declarations to produce a minimal public API header.

## Why Clean Up Headers?

Real-world C headers accumulate complexity over time:

- Preprocessor conditionals (`#ifdef`, `#ifndef`) leave behind confusing structure
- System includes pull in platform-specific types and macros
- Internal implementation details get mixed with public API
- Inconsistent formatting makes headers hard to read

By parsing a header into clangir's IR and writing it back as clean C, you get a normalized, minimal version that contains only the declarations you care about.

## Step 1: Type Conversion

First, write a function to convert IR type expressions back into C syntax:

```python
# header_cleanup_writer.py
"""Generate clean, minimal C headers from clangir IR."""

from __future__ import annotations

from clangir.ir import (
    Array,
    Constant,
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


def type_to_c(t: TypeExpr) -> str:
    """Convert an IR type expression to a C type string."""
    if isinstance(t, CType):
        if t.qualifiers:
            return f"{' '.join(t.qualifiers)} {t.name}"
        return t.name

    elif isinstance(t, Pointer):
        if isinstance(t.pointee, FunctionPointer):
            # Function pointer -- handled specially by callers
            return _funcptr_to_c(t.pointee, name=None)
        inner = type_to_c(t.pointee)
        quals = f" {' '.join(t.qualifiers)}" if t.qualifiers else ""
        return f"{inner} *{quals}"

    elif isinstance(t, Array):
        size_str = str(t.size) if t.size is not None else ""
        return f"{type_to_c(t.element_type)}[{size_str}]"

    elif isinstance(t, FunctionPointer):
        return _funcptr_to_c(t, name=None)

    return str(t)


def _funcptr_to_c(fp: FunctionPointer, name: str | None) -> str:
    """Convert a function pointer to C syntax."""
    ret = type_to_c(fp.return_type)
    params = _format_params(fp.parameters, fp.is_variadic)
    name_str = name or ""
    return f"{ret} (*{name_str})({params})"


def _format_params(parameters: list[Parameter], is_variadic: bool) -> str:
    """Format a parameter list as a C string."""
    if not parameters and not is_variadic:
        return "void"
    parts = []
    for p in parameters:
        type_str = type_to_c(p.type)
        if p.name:
            if isinstance(p.type, Array):
                size_str = str(p.type.size) if p.type.size is not None else ""
                parts.append(f"{type_to_c(p.type.element_type)} {p.name}[{size_str}]")
            elif isinstance(p.type, FunctionPointer):
                parts.append(_funcptr_to_c(p.type, name=p.name))
            else:
                parts.append(f"{type_str} {p.name}")
        else:
            parts.append(type_str)
    if is_variadic:
        parts.append("...")
    return ", ".join(parts)
```

## Step 2: Declaration Handlers

Write handlers for each declaration type, producing clean C syntax:

```python
def _emit_struct(decl: Struct) -> list[str]:
    """Emit a struct or union declaration."""
    if decl.name is None:
        return []

    kind = "union" if decl.is_union else "struct"

    if not decl.fields:
        # Opaque type -- forward declaration
        if decl.is_typedef:
            return [f"typedef {kind} {decl.name} {decl.name};"]
        return [f"{kind} {decl.name};"]

    lines = []
    if decl.is_typedef:
        lines.append(f"typedef {kind} {decl.name} {{")
    else:
        lines.append(f"{kind} {decl.name} {{")

    for field in decl.fields:
        if isinstance(field.type, Array):
            size_str = str(field.type.size) if field.type.size is not None else ""
            lines.append(f"    {type_to_c(field.type.element_type)} {field.name}[{size_str}];")
        elif isinstance(field.type, FunctionPointer):
            lines.append(f"    {_funcptr_to_c(field.type, name=field.name)};")
        else:
            lines.append(f"    {type_to_c(field.type)} {field.name};")

    if decl.is_typedef:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return lines


def _emit_enum(decl: Enum) -> list[str]:
    """Emit an enum declaration."""
    if not decl.values:
        return []

    lines = []
    if decl.is_typedef and decl.name:
        lines.append(f"typedef enum {{")
    elif decl.name:
        lines.append(f"enum {decl.name} {{")
    else:
        lines.append("enum {")

    for v in decl.values:
        if v.value is not None:
            lines.append(f"    {v.name} = {v.value},")
        else:
            lines.append(f"    {v.name},")

    if decl.is_typedef and decl.name:
        lines.append(f"}} {decl.name};")
    else:
        lines.append("};")

    return lines


def _emit_function(decl: Function) -> list[str]:
    """Emit a function prototype."""
    params = _format_params(decl.parameters, decl.is_variadic)
    return [f"{type_to_c(decl.return_type)} {decl.name}({params});"]


def _emit_typedef(decl: Typedef) -> list[str]:
    """Emit a typedef."""
    underlying = decl.underlying_type

    if isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
        fp = underlying.pointee
        params = _format_params(fp.parameters, fp.is_variadic)
        return [f"typedef {type_to_c(fp.return_type)} (*{decl.name})({params});"]

    if isinstance(underlying, FunctionPointer):
        params = _format_params(underlying.parameters, underlying.is_variadic)
        return [f"typedef {type_to_c(underlying.return_type)} (*{decl.name})({params});"]

    if isinstance(underlying, Array):
        size_str = str(underlying.size) if underlying.size is not None else ""
        return [f"typedef {type_to_c(underlying.element_type)} {decl.name}[{size_str}];"]

    return [f"typedef {type_to_c(underlying)} {decl.name};"]


def _emit_variable(decl: Variable) -> list[str]:
    """Emit an extern variable declaration."""
    return [f"extern {type_to_c(decl.type)} {decl.name};"]


def _emit_constant(decl: Constant) -> list[str]:
    """Emit a constant definition."""
    if decl.is_macro and decl.value is not None:
        return [f"#define {decl.name} {decl.value}"]
    elif decl.type is not None and decl.value is not None:
        return [f"const {type_to_c(decl.type)} {decl.name} = {decl.value};"]
    return []
```

## Step 3: Filtering and the Writer Class

The real value of a cleanup writer is filtering. Add options to control which declarations make it into the output:

```python
import re
from clangir.writers import register_writer


class HeaderCleanupWriter:
    """Writer that produces clean, minimal C headers.

    Options:
        include_patterns: Only include declarations matching these patterns.
        exclude_patterns: Exclude declarations matching these patterns.
        strip_prefixes: Remove these prefixes from declaration names.
        add_header_guard: Wrap output in #ifndef/#define/#endif.
    """

    def __init__(
        self,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        strip_prefixes: list[str] | None = None,
        add_header_guard: bool = True,
    ) -> None:
        self._include = [re.compile(p) for p in (include_patterns or [])]
        self._exclude = [re.compile(p) for p in (exclude_patterns or [])]
        self._strip_prefixes = strip_prefixes or []
        self._add_guard = add_header_guard

    def _should_include(self, name: str | None) -> bool:
        """Check if a declaration should be included in output."""
        if name is None:
            return False
        # Exclude patterns take priority
        for pat in self._exclude:
            if pat.search(name):
                return False
        # If include patterns are specified, name must match at least one
        if self._include:
            return any(pat.search(name) for pat in self._include)
        return True

    def write(self, header: Header) -> str:
        lines: list[str] = []

        # Header guard
        guard_name = ""
        if self._add_guard:
            guard_name = re.sub(r"[^A-Z0-9]", "_", header.path.upper()) + "_H"
            lines.append(f"#ifndef {guard_name}")
            lines.append(f"#define {guard_name}")
            lines.append("")

        # Emit filtered declarations
        for decl in header.declarations:
            name = getattr(decl, "name", None)
            if not self._should_include(name):
                continue

            decl_lines = self._emit(decl)
            if decl_lines:
                lines.extend(decl_lines)
                lines.append("")

        # Close header guard
        if self._add_guard:
            lines.append(f"#endif /* {guard_name} */")
            lines.append("")

        return "\n".join(lines)

    def _emit(self, decl) -> list[str]:
        if isinstance(decl, Struct):
            return _emit_struct(decl)
        elif isinstance(decl, Enum):
            return _emit_enum(decl)
        elif isinstance(decl, Function):
            return _emit_function(decl)
        elif isinstance(decl, Typedef):
            return _emit_typedef(decl)
        elif isinstance(decl, Variable):
            return _emit_variable(decl)
        elif isinstance(decl, Constant):
            return _emit_constant(decl)
        return []

    @property
    def name(self) -> str:
        return "header-cleanup"

    @property
    def format_description(self) -> str:
        return "Clean, minimal C header files"


register_writer(
    "header-cleanup",
    HeaderCleanupWriter,
    description="Clean, minimal C header files",
)
```

## Step 4: Try It Out

```python
from clangir import get_backend, get_writer
import header_cleanup_writer  # noqa: F401

code = """
#define _INTERNAL_FLAG 1
#define API_VERSION 3

typedef struct {
    int _private_field;
    double x;
    double y;
} Point;

typedef struct {
    void *_impl;
} _InternalHandle;

Point point_create(double x, double y);
void _internal_init(void);
double point_distance(Point a, Point b);
"""

backend = get_backend()
header = backend.parse(code, "point.h")

writer = get_writer(
    "header-cleanup",
    exclude_patterns=["^_"],  # Exclude names starting with underscore
    add_header_guard=True,
)
print(writer.write(header))
```

Expected output:

```c
#ifndef POINT_H_H
#define POINT_H_H

#define API_VERSION 3

typedef struct Point {
    int _private_field;
    double x;
    double y;
} Point;

Point point_create(double x, double y);

double point_distance(Point a, Point b);

#endif /* POINT_H_H */
```

Notice that `_InternalHandle`, `_internal_init`, and `_INTERNAL_FLAG` were all filtered out because their names start with an underscore.

## Use Case: Public API Extraction

For libraries with large internal headers, use `include_patterns` to extract only the public API:

```python
writer = get_writer(
    "header-cleanup",
    include_patterns=["^mylib_"],  # Only keep functions/types with mylib_ prefix
    exclude_patterns=["_internal", "_private"],
)
```

## Use Case: API Surface Documentation

Combine with the [JSON writer](json-export.md) to create a CI pipeline that tracks your library's public API:

```python
from clangir import get_backend, get_writer

backend = get_backend()

with open("mylib.h") as f:
    header = backend.parse(f.read(), "mylib.h")

# Write clean header for documentation
cleanup = get_writer("header-cleanup", exclude_patterns=["^_"])
with open("docs/api.h", "w") as f:
    f.write(cleanup.write(header))

# Write JSON for machine processing
json_writer = get_writer("json", indent=2)
with open("docs/api.json", "w") as f:
    f.write(json_writer.write(header))
```

## What's Next

- [Writing Custom Writers](../guides/custom-writers.md) -- general guide for writer development
- [JSON Export Tutorial](json-export.md) -- serialize IR for tooling and CI/CD
- [PXD Writer Tutorial](pxd-writer.md) -- building a Cython declaration writer
