# headerkit

[![CI](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml)
[![Docs](https://github.com/axiomantic/headerkit/actions/workflows/docs.yml/badge.svg)](https://axiomantic.github.io/headerkit/)
[![PyPI](https://img.shields.io/pypi/v/headerkit)](https://pypi.org/project/headerkit/)
[![Python](https://img.shields.io/pypi/pyversions/headerkit)](https://pypi.org/project/headerkit/)

A general-purpose C/C++ header analysis toolkit with a pluggable architecture. Parse once with libclang, output to any format through 7 built-in writers.

headerkit carries the torch for [ctypesgen2](https://github.com/ctypesgen/ctypesgen) as a ctypes binding generator and serves as the new engine behind [autopxd2](https://github.com/elijahr/autopxd2) for Cython .pxd generation.

Full documentation: [axiomantic.github.io/headerkit](https://axiomantic.github.io/headerkit/)

## Architecture

headerkit separates parsing from output generation. A parser backend (currently libclang) produces a language-neutral IR (intermediate representation), and any number of writers consume that IR to generate output. Add a backend, and all writers benefit. Add a writer, and all backends feed it.

```
C/C++ headers --> [libclang backend] --> IR --> [writer] --> output
```

## Writers

| Writer | Output | Description |
|--------|--------|-------------|
| **cffi** | CFFI cdef strings | Declarations for `ffibuilder.cdef()` |
| **ctypes** | Python modules | Complete ctypes binding modules (successor to ctypesgen2) |
| **cython** | .pxd files | Cython declaration files with C++ support (ported from autopxd2) |
| **diff** | JSON or Markdown | API compatibility reports between header versions |
| **json** | JSON | Full IR serialization for inspection and tooling |
| **lua** | LuaJIT FFI bindings | `ffi.cdef()` declarations for LuaJIT |
| **prompt** | Compact text | Token-optimized IR output for LLM context windows |

## Installation

Requires Python 3.10+. Zero runtime dependencies.

```bash
pip install headerkit
```

## System Requirements

libclang shared library must be installed:

- macOS: `brew install llvm` or Xcode Command Line Tools
- Ubuntu: `sudo apt install libclang-dev`
- Fedora: `sudo dnf install clang-devel`
- Windows: Download the [LLVM installer](https://github.com/llvm/llvm-project/releases) or `winget install LLVM.LLVM`

Or use the bundled installer:

```bash
headerkit-install-libclang
```

Supports LLVM 18, 19, 20, and 21. The appropriate version is detected automatically.

## Usage

Parse a header and generate output with any writer:

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend("libclang")
header = backend.parse('#include "mylib.h"', "wrapper.h", include_dirs=["/path/to/include"])

# CFFI bindings
cffi_writer = get_writer("cffi")
print(cffi_writer.write(header))

# ctypes bindings
ctypes_writer = get_writer("ctypes")
print(ctypes_writer.write(header))

# Cython .pxd
cython_writer = get_writer("cython")
print(cython_writer.write(header))

# JSON for tooling
json_writer = get_writer("json")
print(json_writer.write(header))
```

List available writers:

```python
from headerkit.writers import list_writers
print(list_writers())  # ['cffi', 'ctypes', 'cython', 'diff', 'json', 'lua', 'prompt']
```

## Development

```bash
git clone https://github.com/axiomantic/headerkit.git
cd headerkit
pip install -e '.[dev]'
pytest
```

## License

This project is licensed under the [MIT License](LICENSE).

The vendored clang Python bindings in `headerkit/_clang/v*/` are from the
[LLVM Project](https://llvm.org/) and are licensed under the
[Apache License v2.0 with LLVM Exceptions](headerkit/_clang/LICENSE).
