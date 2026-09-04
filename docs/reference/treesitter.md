# Tree-sitter Backend

The Tree-sitter backend (`headerkit.backends.treesitter`) is a zero-dependency parser backend for C headers using `tree-sitter-c`.

## Features

- **Zero Host Tooling Required**: Does not require LLVM, Xcode Command Line Tools, or `libclang.so`/`.dylib`/`.dll` installed on the host.
- **Precompiled Wheels**: Uses prebuilt wheels from PyPI (`tree-sitter` and `tree-sitter-c`).
- **Hook Integration**: Registered at `Priority.FALLBACK` for `parse_unit`, serving as an automatic fallback when libclang is missing.

## Installation

Install the optional extra:

```bash
pip install "headerkit[treesitter]"
```

## Usage

=== "CLI"

    ```bash
    headerkit mylib.h --backend tree-sitter -w ctypes -o ctypes:bindings.py
    ```

=== "Python API"

    ```python
    from headerkit.backends.treesitter import TreeSitterBackend

    backend = TreeSitterBackend()
    header = backend.parse(code, "mylib.h")
    ```

## API Reference

::: headerkit.backends.treesitter.TreeSitterBackend
    options:
      show_source: false
