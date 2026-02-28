# Using the CFFI Writer

The CFFI writer converts clangir IR into C declaration strings compatible with [CFFI](https://cffi.readthedocs.io/)'s `ffibuilder.cdef()`. This is the most common workflow: parse a C header, generate cdef declarations, and use them to build Python bindings for a C library.

## Basic Usage

### Using get_writer()

```python
from clangir import get_backend, get_writer

backend = get_backend()
header = backend.parse(open("mylib.h").read(), "mylib.h")

writer = get_writer("cffi")
cdef_source = writer.write(header)
print(cdef_source)
```

### Using header_to_cffi() Directly

The [`header_to_cffi()`][clangir.writers.cffi.header_to_cffi] function provides the same functionality without going through the writer registry:

```python
from clangir import get_backend
from clangir.writers.cffi import header_to_cffi

backend = get_backend()
header = backend.parse(open("mylib.h").read(), "mylib.h")

cdef_source = header_to_cffi(header)
```

## Complete CFFI Example

Here is a full example that parses a header, generates CFFI bindings, and compiles them into a usable Python module.

Given a C header `calculator.h`:

```c
typedef enum {
    OP_ADD,
    OP_SUB,
    OP_MUL,
    OP_DIV,
} Operation;

typedef struct {
    double result;
    int error;
} CalcResult;

CalcResult calculate(double a, double b, Operation op);
const char *calc_error_string(int error_code);
```

Generate and use the bindings:

```python
from cffi import FFI
from clangir import get_backend, get_writer

# Step 1: Parse the header
backend = get_backend()
with open("calculator.h") as f:
    code = f.read()
header = backend.parse(code, "calculator.h")

# Step 2: Generate CFFI cdef
writer = get_writer("cffi")
cdef_source = writer.write(header)

# Step 3: Set up CFFI
ffibuilder = FFI()
ffibuilder.cdef(cdef_source)
ffibuilder.set_source(
    "_calculator",
    '#include "calculator.h"',
    sources=["calculator.c"],
)

# Step 4: Compile
ffibuilder.compile(verbose=True)
```

After compilation, you can import and use the module:

```python
from _calculator import ffi, lib

result = lib.calculate(10.0, 3.0, lib.OP_ADD)
print(f"Result: {result.result}, Error: {result.error}")
```

## Excluding Declarations

The CFFI writer supports `exclude_patterns` to filter out declarations by name. Patterns are Python regular expressions matched against declaration names.

```python
writer = get_writer("cffi", exclude_patterns=[
    "__.*",           # Skip compiler builtins (__builtin_*, __attribute__, etc.)
    "_private_.*",    # Skip private API functions
    "internal_.*",    # Skip internal functions
])
cdef_source = writer.write(header)
```

You can also pass patterns directly to `header_to_cffi()`:

```python
from clangir.writers.cffi import header_to_cffi

cdef_source = header_to_cffi(header, exclude_patterns=["__.*", "test_.*"])
```

!!! tip "Pattern matching"
    Patterns use `re.search()`, so they match anywhere in the name. Use `^` and `$` anchors for exact matching: `"^_private$"` matches only the name `_private`, while `"_private"` matches anything containing `_private`.

## How the Writer Handles C Constructs

### Structs and Unions

Structs with fields are emitted with all their members. Opaque structs (no fields) are emitted as forward declarations:

```c
/* With fields */
struct Point {
    double x;
    double y;
};

/* Opaque */
struct OpaqueHandle { ...; };
```

### Typedefs

Typedefs are preserved, including function pointer typedefs:

```c
typedef unsigned long size_t;
typedef void (*Callback)(int status, void *data);
```

### Enums

Enum values are emitted with their explicit values when available:

```c
typedef enum {
    RED = 0,
    GREEN = 1,
    BLUE = 2,
} Color;
```

### Constants

Only integer `#define` constants are emitted, since CFFI's cdef parser only supports integer constant macros:

```c
#define BUFFER_SIZE 1024
```

!!! warning "Non-integer macros"
    String macros, expression macros, and macros with unknown values are silently skipped. If you need access to these, use the [JSON writer](../tutorials/json-export.md) to inspect the full IR.

### Variadic Functions

Variadic functions are fully supported:

```c
int printf(const char *fmt, ...);
```
