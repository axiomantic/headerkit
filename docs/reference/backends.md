# Backends

Parser backends convert C/C++ source code into the clangir
[IR](ir.md). The [`ParserBackend`][clangir.ir.ParserBackend] protocol defines
the interface that all backends implement.

Backends are accessed through a registry. Use [`get_backend()`][clangir.backends.get_backend]
to obtain an instance and [`list_backends()`][clangir.backends.list_backends] to discover
what is available.

## Available Backends

| Backend | Description | C++ Support | Macro Extraction |
|---------|-------------|:-----------:|:----------------:|
| `libclang` | LLVM clang-based parser | Yes | Yes |

## Protocol

See [`ParserBackend`][clangir.ir.ParserBackend] on the IR Types page for the
full protocol definition including `parse()`, `name`, `supports_macros`, and
`supports_cpp`.

## Registry Functions

::: clangir.backends.get_backend
    options:
      show_source: false

::: clangir.backends.get_default_backend
    options:
      show_source: false

::: clangir.backends.list_backends
    options:
      show_source: false

::: clangir.backends.is_backend_available
    options:
      show_source: false

::: clangir.backends.register_backend
    options:
      show_source: false

::: clangir.backends.get_backend_info
    options:
      show_source: false
