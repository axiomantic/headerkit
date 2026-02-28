# Writers

Writers convert clangir [IR](ir.md) into various output formats. The
[`WriterBackend`][clangir.writers.WriterBackend] protocol defines the interface
that all writers implement.

Writers are accessed through a registry that mirrors the
[backend registry](backends.md). Use [`get_writer()`][clangir.writers.get_writer]
to obtain an instance and [`list_writers()`][clangir.writers.list_writers] to discover
what is available.

## Available Writers

| Writer | Module | Description |
|--------|--------|-------------|
| [`cffi`](cffi.md) | `clangir.writers.cffi` | CFFI cdef declarations for `ffibuilder.cdef()` |
| [`json`](json.md) | `clangir.writers.json` | JSON serialization for inspection and tooling |

## Protocol

::: clangir.writers.WriterBackend
    options:
      show_source: false

## Registry Functions

::: clangir.writers.get_writer
    options:
      show_source: false

::: clangir.writers.get_default_writer
    options:
      show_source: false

::: clangir.writers.list_writers
    options:
      show_source: false

::: clangir.writers.is_writer_available
    options:
      show_source: false

::: clangir.writers.register_writer
    options:
      show_source: false

::: clangir.writers.get_writer_info
    options:
      show_source: false
