# clangir

[![CI](https://github.com/axiomantic/clangir/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/clangir/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/clangir)](https://pypi.org/project/clangir/)
[![Python](https://img.shields.io/pypi/pyversions/clangir)](https://pypi.org/project/clangir/)

C Intermediate Representation - a Python library for parsing C/C++ headers.

Provides an IR data model for parsed C/C++ declarations, a libclang parser backend, and a CFFI cdef writer.

## Installation

Requires Python 3.10+.

```bash
pip install clangir
```

## System Requirements

libclang shared library must be installed:

- macOS: `brew install llvm` or Xcode Command Line Tools
- Ubuntu: `sudo apt install libclang-dev`
- Fedora: `sudo dnf install clang-devel`

Supports LLVM 18, 19, 20, and 21. The appropriate version is detected automatically.

## Usage

```python
from clangir.backends import get_backend
from clangir.writers.cffi import header_to_cffi

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
git clone https://github.com/axiomantic/clangir.git
cd clangir
pip install -e '.[dev]'
pytest
```
