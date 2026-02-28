# Cython Writer

The Cython writer generates `.pxd` declaration files from headerkit IR. It
supports the full range of C and C++ declarations including structs, enums,
functions, typedefs, namespaces, templates, and operator aliasing. Python and
Cython keywords are automatically escaped with a `_` suffix and a C name alias.

## Writer Class

::: headerkit.writers.cython.CythonWriter
    options:
      show_source: false

## Convenience Function

::: headerkit.writers.cython.write_pxd
    options:
      show_source: false

## Internal Writer

The `PxdWriter` class handles the actual conversion logic. It is created
internally by [`CythonWriter`][headerkit.writers.cython.CythonWriter] and
[`write_pxd`][headerkit.writers.cython.write_pxd].

::: headerkit.writers.cython.PxdWriter
    options:
      show_source: false
      members:
        - write

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

int distance(const Point* a, const Point* b);
""", "geometry.h")

writer = get_writer("cython")
print(writer.write(header))
```

Output:

```cython
cdef extern from "geometry.h":

    ctypedef struct Point:
        int x
        int y

    int distance(const Point* a, const Point* b)
```
