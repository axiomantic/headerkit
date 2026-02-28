# Quick Start

This guide walks through parsing a C header file with clangir and generating output in two formats: CFFI bindings and JSON.

## Prerequisites

Make sure you have [installed clangir and libclang](installation.md).

## 1. Create a Sample C Header

Save the following as `point.h`:

```c
#include <stddef.h>

typedef struct {
    double x;
    double y;
} Point;

Point point_add(Point a, Point b);
Point point_scale(Point p, double factor);
double point_distance(Point a, Point b);
size_t point_format(Point p, char *buf, size_t bufsize);
```

## 2. Parse the Header

Use `get_backend()` to obtain a parser backend, then call its `parse()` method with the header source code:

```python
from clangir import get_backend

backend = get_backend()

with open("point.h") as f:
    code = f.read()

header = backend.parse(code, "point.h")
print(header)
```

Output:

```
Header(point.h, 5 declarations)
```

## 3. Inspect the IR

The `header.declarations` list contains typed IR nodes. You can inspect them directly:

```python
from clangir import Struct, Function, Typedef

for decl in header.declarations:
    if isinstance(decl, Struct):
        print(f"Struct: {decl.name}")
        for field in decl.fields:
            print(f"  {field.type} {field.name}")
    elif isinstance(decl, Function):
        print(f"Function: {decl}")
    elif isinstance(decl, Typedef):
        print(f"Typedef: {decl.name} -> {decl.underlying_type}")
```

Output:

```
Struct: Point
  double x
  double y
Function: Point point_add(Point a, Point b)
Function: Point point_scale(Point p, double factor)
Function: double point_distance(Point a, Point b)
Function: size_t point_format(Point p, char *buf, size_t bufsize)
```

## 4. Generate CFFI Bindings

Use the built-in CFFI writer to produce `cdef`-compatible declarations:

```python
from clangir import get_writer

writer = get_writer("cffi")
cdef_source = writer.write(header)
print(cdef_source)
```

Output:

```c
typedef struct Point {
    double x;
    double y;
} Point;
Point point_add(Point a, Point b);
Point point_scale(Point p, double factor);
double point_distance(Point a, Point b);
size_t point_format(Point p, char *buf, size_t bufsize);
```

You can feed this directly to CFFI's `ffibuilder.cdef()`. See the [CFFI guide](cffi.md) for a complete example.

## 5. Generate JSON Output

Use the JSON writer to serialize the IR for inspection or downstream tooling:

```python
json_writer = get_writer("json", indent=2)
print(json_writer.write(header))
```

Output (abbreviated):

```json
{
  "path": "point.h",
  "declarations": [
    {
      "kind": "struct",
      "name": "Point",
      "fields": [
        {"name": "x", "type": {"kind": "ctype", "name": "double"}},
        {"name": "y", "type": {"kind": "ctype", "name": "double"}}
      ],
      "is_typedef": true
    },
    {
      "kind": "function",
      "name": "point_add",
      "return_type": {"kind": "ctype", "name": "Point"},
      "parameters": [
        {"name": "a", "type": {"kind": "ctype", "name": "Point"}},
        {"name": "b", "type": {"kind": "ctype", "name": "Point"}}
      ],
      "is_variadic": false
    }
  ]
}
```

See the [JSON Export tutorial](../tutorials/json-export.md) for advanced JSON processing techniques.

## What's Next?

- [Architecture Overview](architecture.md) -- understand the backend/IR/writer pipeline
- [Using CFFI Writer](cffi.md) -- complete CFFI integration with `ffibuilder`
- [Writing Custom Writers](custom-writers.md) -- build your own output format
- [API Reference](../reference/index.md) -- full reference for all IR types and APIs
