# ctypes Writer

The ctypes writer generates Python source code that uses the `ctypes` standard
library to define C type bindings. The output is a runnable Python module
containing struct/union classes, enum constants, type aliases, callback types,
and function prototype annotations.

## Writer Class

::: headerkit.writers.ctypes.CtypesWriter
    options:
      show_source: false

## Convenience Function

::: headerkit.writers.ctypes.header_to_ctypes
    options:
      show_source: false

## Low-Level Functions

These functions are used internally by [`header_to_ctypes`][headerkit.writers.ctypes.header_to_ctypes]
and can be useful when working with individual type expressions.

::: headerkit.writers.ctypes.type_to_ctypes
    options:
      show_source: false

## Example

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend()
header = backend.parse("""
typedef struct {
    int x;
    int y;
} Point;

int distance(Point* a, Point* b);
""", "geometry.h")

writer = get_writer("ctypes", lib_name="_geometry")
print(writer.write(header))
```

Output:

```python
"""ctypes bindings generated from geometry.h."""

import ctypes
import ctypes.util
import sys

# ============================================================
# Structures and Unions
# ============================================================

class Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
    ]

# ============================================================
# Function Prototypes
# ============================================================

_geometry.distance.argtypes = [ctypes.POINTER(Point), ctypes.POINTER(Point)]
_geometry.distance.restype = ctypes.c_int
```
