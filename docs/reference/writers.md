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
| [`json`](json.md) | `headerkit.writers.json` | JSON serialization for inspection and tooling |

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
