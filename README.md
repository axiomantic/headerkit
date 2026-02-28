# headerkit

[![CI](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/headerkit/actions/workflows/ci.yml)
[![Docs](https://github.com/axiomantic/headerkit/actions/workflows/docs.yml/badge.svg)](https://axiomantic.github.io/headerkit/)
[![PyPI](https://img.shields.io/pypi/v/headerkit)](https://pypi.org/project/headerkit/)
[![Python](https://img.shields.io/pypi/pyversions/headerkit)](https://pypi.org/project/headerkit/)

A Python toolkit for parsing C/C++ headers with pluggable backends and writers.

Full documentation: https://axiomantic.github.io/headerkit/

Provides an IR data model for parsed C/C++ declarations, a libclang parser backend, and a CFFI cdef writer.

## Installation

Requires Python 3.10+.

```bash
pip install headerkit
```

## System Requirements

libclang shared library must be installed:

- macOS: `brew install llvm` or Xcode Command Line Tools
- Ubuntu: `sudo apt install libclang-dev`
- Fedora: `sudo dnf install clang-devel`
- Windows: Download the [LLVM installer](https://github.com/llvm/llvm-project/releases) or `winget install LLVM.LLVM`

Supports LLVM 18, 19, 20, and 21. The appropriate version is detected automatically.

## Usage

```python
from headerkit.backends import get_backend
from headerkit.writers.cffi import header_to_cffi

backend = get_backend("libclang")
header = backend.parse('#include "mylib.h"', "wrapper.h", include_dirs=["/path/to/include"])
cdef = header_to_cffi(header)
print(cdef)
```

This produces CFFI-compatible C declarations that can be passed to `ffi.cdef()`:

```c
struct MyStruct {
    int field;
    ...
};
void my_function(int arg);
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
