# Backends

Parser backends convert C/C++ source code into the headerkit
[IR](ir.md). The [`ParserBackend`][headerkit.ir.ParserBackend] protocol defines
the interface that all backends implement.

Backends are accessed through a registry. Use [`get_backend()`][headerkit.backends.get_backend]
to obtain an instance and [`list_backends()`][headerkit.backends.list_backends] to discover
what is available.

## Available Backends

| Backend | Description | C++ Support | Macro Extraction | Languages | Classifications |
|---------|-------------|:-----------:|:----------------:|:---------:|:---------------:|
| `libclang` | LLVM clang-based parser | Yes | Yes | `c`, `cpp` | `header` |
| `tree-sitter` | Zero-dependency C parser using `tree-sitter-c` | No | No | `c` | `header` |

## Protocol

See [`ParserBackend`][headerkit.ir.ParserBackend] on the IR Types page for the
full protocol definition including `parse()`, `name`, `supports_macros`,
`supports_cpp`, `supported_languages`, and `supported_classifications`.

## Exceptions

::: headerkit.backends.LibclangUnavailableError
    options:
      show_source: false

## Registry Functions

::: headerkit.backends.get_backend
    options:
      show_source: false

::: headerkit.backends.get_default_backend
    options:
      show_source: false

::: headerkit.backends.list_backends
    options:
      show_source: false

::: headerkit.backends.is_backend_available
    options:
      show_source: false

::: headerkit.backends.register_backend
    options:
      show_source: false

::: headerkit.backends.get_backend_info
    options:
      show_source: false
