# cir

C Intermediate Representation - a Python library for parsing C/C++ headers.

Provides an IR data model for parsed C/C++ declarations, a libclang parser backend, and a CFFI cdef writer.

## Installation

```
pip install cir
```

## System Requirements

libclang shared library must be installed:

- macOS: `brew install llvm` or Xcode Command Line Tools
- Ubuntu: `sudo apt install libclang-dev`
- Fedora: `sudo dnf install clang-devel`

## Usage

```python
from cir.backends import get_backend
from cir.writers.cffi import header_to_cffi

backend = get_backend("libclang")
header = backend.parse('#include "mylib.h"', "wrapper.h", include_dirs=["/path/to/include"])
cdef = header_to_cffi(header)
print(cdef)
```
