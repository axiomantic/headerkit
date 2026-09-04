# Writers

Writers convert headerkit [IR](ir.md) into various output formats. The
[`WriterBackend`][headerkit.writers.WriterBackend] protocol defines the interface
that all writers implement.

Writers are accessed through a registry that mirrors the
[backend registry](backends.md). Use [`get_writer()`][headerkit.writers.get_writer]
to obtain an instance and [`list_writers()`][headerkit.writers.list_writers] to discover
what is available.

## Available Writers

| Writer | Module | Description |
|--------|--------|-------------|
| [`cffi`](cffi.md) | `headerkit.writers.cffi` | CFFI cdef declarations for `ffibuilder.cdef()` |
| [`cshim`](cshim.md) | `headerkit.writers.cshim` | Pure C-ABI wrappers (`extern "C"`) for C++ classes and APIs |
| [`ctypes`](ctypes.md) | `headerkit.writers.ctypes` | Python ctypes binding modules |
| [`cython`](cython.md) | `headerkit.writers.cython` | Cython `.pxd` declarations with full C++ support |
| [`diff`](diff.md) | `headerkit.writers.diff` | API compatibility reports (JSON/Markdown) |
| [`json`](json.md) | `headerkit.writers.json` | JSON serialization for inspection and tooling |
| [`lua`](lua.md) | `headerkit.writers.lua` | LuaJIT FFI bindings |
| [`mojo`](mojo.md) | `headerkit.writers.mojo` | Idiomatic Mojo FFI bindings with `sys.ffi.DLHandle` and CShim bridge |
| [`nim`](nim.md) | `headerkit.writers.nim` | Native Nim bindings with C and C++ interop |
| [`prompt`](prompt.md) | `headerkit.writers.prompt` | Token-optimized LLM context (compact/standard/verbose) |

## Protocol

::: headerkit.writers.WriterBackend
    options:
      show_source: false

## Registry Functions

::: headerkit.writers.get_writer
    options:
      show_source: false

::: headerkit.writers.get_default_writer
    options:
      show_source: false

::: headerkit.writers.list_writers
    options:
      show_source: false

::: headerkit.writers.is_writer_available
    options:
      show_source: false

::: headerkit.writers.register_writer
    options:
      show_source: false

::: headerkit.writers.get_writer_info
    options:
      show_source: false
