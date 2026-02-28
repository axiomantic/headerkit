# API Reference

The clangir API is organized into three layers:

**[IR Types](ir.md)** -- The core data model. Dataclasses representing C/C++ declarations
(structs, functions, enums, typedefs, etc.) and type expressions (pointers, arrays,
function pointers). All backends produce these types; all writers consume them.

**[Backends](backends.md)** -- Parsers that read C/C++ source code and produce IR.
The [`ParserBackend`][clangir.ir.ParserBackend] protocol defines the interface;
the registry functions let you discover and instantiate backends.

**[Writers](writers.md)** -- Output generators that consume IR and produce target formats.
The [`WriterBackend`][clangir.writers.WriterBackend] protocol defines the interface;
the registry functions mirror the backend API.

## Writer Implementations

| Writer | Module | Description |
|--------|--------|-------------|
| [CFFI](cffi.md) | `clangir.writers.cffi` | Generates `ffibuilder.cdef()` strings |
| [JSON](json.md) | `clangir.writers.json` | Serializes IR to JSON for inspection and tooling |

## CLI Tools

| Tool | Module | Description |
|------|--------|-------------|
| [Install Libclang](install-libclang.md) | `clangir.install_libclang` | Installs the libclang system dependency for the current platform |

## Quick Example

```python
from clangir.backends import get_backend
from clangir.writers import get_writer

# Parse a C header
backend = get_backend()
header = backend.parse("int add(int a, int b);", "math.h")

# Write CFFI output
writer = get_writer("cffi")
print(writer.write(header))
# => int add(int a, int b);

# Write JSON output
json_writer = get_writer("json")
print(json_writer.write(header))
```
