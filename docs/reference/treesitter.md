# Tree-sitter Backend

The Tree-sitter backend (`headerkit.backends.treesitter`) is a zero-dependency parser backend for C and C++ headers and sources using `tree-sitter-c` and `tree-sitter-cpp`.

## Features

- **Zero Host Tooling Required**: Does not require LLVM, Xcode Command Line Tools, or `libclang.so`/`.dylib`/`.dll` installed on the host.
- **Precompiled Wheels**: Uses prebuilt wheels from PyPI (`tree-sitter`, `tree-sitter-c`, and `tree-sitter-cpp`).
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

## Limitations

Compared to the default `libclang` backend, the tree-sitter backend is syntactic and more limited:

- **No `#include` Expansion**: Parses the given file or code string only. It does not resolve or follow `#include` directives; declarations from included headers do not appear in the resulting IR.
- **No Preprocessor Macro Extraction**: Does not evaluate or extract `#define` macros into IR `Constant` or `MacroFunction` nodes.
- **No System Include Discovery**: Does not query system compilers for include search paths.
- **Single Translation Unit Context**: Operates without a compiler semantic engine or cross-file type resolution.

Use `tree-sitter` for self-contained headers, minimal/headless build environments without system LLVM libraries, or fast syntax extraction. For umbrella headers, full macro extraction, or compiler-accurate type layouts, use `libclang`.

## API Reference

::: headerkit.backends.treesitter.TreeSitterBackend
    options:
      show_source: false
