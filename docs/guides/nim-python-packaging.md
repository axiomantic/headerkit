# Nim to Python Packaging & Memory Bridge

This guide demonstrates how to author high-performance extension modules in [Nim](https://nim-lang.org), expose a clean C-ABI interface, generate Python bindings with Headerkit, and distribute the result as standard binary Python wheels using `scikit-build-core`.

## Overview

Nim compiles directly to C and provides zero-overhead interoperability with C ABI calling conventions. To ensure seamless operation when called from Python, three critical details must be managed:
1. **Runtime Initialization**: Invoking `NimMain()` idempotently before executing any exported proc.
2. **Deterministic Memory Cleanup**: Using Nim's `--mm:orc` (deterministic ARC with cyclic collector) and tying Python wrapper destructors (`__del__` / context managers) directly to Nim deallocations.
3. **Thread Safety & Multi-Threading**: Calling `setupForeignThreadGc()` upon entering procs from foreign Python threads so that thread-local heaps and collectors function without memory corruption.

---

## 1. Writing the Nim Source

Create `src/fastmath.nim` with `{.exportc, dynlib.}` annotations:

```nim
type
  FastMatrix* = object
    rows*, cols*: int
    data*: seq[float64]

proc NimMain*() {.cdecl, importc.}

proc createMatrix*(rows, cols: int): ptr FastMatrix {.exportc, dynlib.} =
  setupForeignThreadGc()
  let m = create(FastMatrix)
  m.rows = rows
  m.cols = cols
  m.data = newSeq[float64](rows * cols)
  return m

proc destroyMatrix*(m: ptr FastMatrix) {.exportc, dynlib.} =
  if m != nil:
    setupForeignThreadGc()
    m.data = @[]
    dealloc(m)

proc addNumbers*(a, b: int): int {.exportc, dynlib.} =
  return a + b

proc computeSum*(m: ptr FastMatrix): float64 {.exportc, dynlib.} =
  if m == nil: return 0.0
  setupForeignThreadGc()
  var s = 0.0
  for v in m.data:
    s += v
  return s
```

---

## 2. Direct Nim AST Parsing with `NimBackend` (Recommended)

With HeaderKit's first-class [`NimBackend`][headerkit.backends.nim.NimBackend], you do **not** need to hand-maintain a separate `.h` file. `NimBackend` parses `.nim` source files (and `nim c --header` generated headers) directly using Nim's compiler `std/macros` AST extractor (`_nim_ast_helper.nim`), preserving parameter names, `SourceMeta`, deprecation pragmas, and docstrings (`ParsedDoc`):

```bash
# Parse .nim source directly and emit CFFI cdefs + PEP 484 / CFFI .pyi stubs
headerkit src/fastmath.nim -b nim -w cffi -w pyi \
  -o cffi:fastmath/_defs.cdef.txt \
  -o pyi:fastmath/_fastmath.pyi \
  --writer-opt pyi:mode=cffi
```

Or programmatically in Python with [`DocParserTransform`][headerkit.transforms.doc_parser.DocParserTransform]:

```python
from pathlib import Path
from headerkit.backends import get_backend
from headerkit.transforms import DocParserTransform
from headerkit.writers import get_writer

nim_backend = get_backend("nim")
unit = nim_backend.parse(Path("src/fastmath.nim").read_text(), "src/fastmath.nim")
unit = DocParserTransform(doc_format="rst").transform(unit)

cdef_code = get_writer("cffi").write(unit)
pyi_code = get_writer("pyi", mode="cffi").write(unit)
```

---

## 3. Alternative: Defining a Hand-Written C API Header

If you prefer maintaining an explicit C header file (`src/fastmath_api.h`) representing the exported C interface:

```c
#ifndef FASTMATH_API_H
#define FASTMATH_API_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct FastMatrix FastMatrix;

void NimMain(void);
void NimDestroyGlobals(void);

FastMatrix* createMatrix(int64_t rows, int64_t cols);
void destroyMatrix(FastMatrix* m);
int64_t addNumbers(int64_t a, int64_t b);
double computeSum(FastMatrix* m);

#ifdef __cplusplus
}
#endif

#endif
```

---

## 4. Parsing the C Header & Generating Bindings

Use Headerkit to parse the C API and emit Python `ctypes` bindings:

```bash
headerkit src/fastmath_api.h -w ctypes -o ctypes:fastmath/_bindings.py
```

Or programmatically in Python:

```python
from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.writers.ctypes import CtypesWriter

backend = TreeSitterBackend()
header = backend.parse(header_code, "fastmath_api.h")

writer = CtypesWriter(lib_name="_lib")
bindings_code = writer.write(header)
```

---

## 5. Packaging as a Python Wheel (`scikit-build-core`)

Define `pyproject.toml` and `CMakeLists.txt` to automate compiling the Nim library and packaging it into binary wheels:

### `pyproject.toml`
```toml
[build-system]
requires = ["scikit-build-core>=0.9.0"]
build-backend = "scikit_build_core.build"

[project]
name = "fastmath"
version = "0.1.0"
description = "Nim extension packaged with Headerkit"
requires-python = ">=3.11"

[tool.scikit-build]
cmake.version = ">=3.18"
wheel.packages = ["fastmath"]
```

### `CMakeLists.txt`
```cmake
cmake_minimum_required(VERSION 3.18)
project(fastmath_pkg LANGUAGES C)

find_program(NIM_EXECUTABLE nim REQUIRED)

set(NIM_SRC "${CMAKE_CURRENT_SOURCE_DIR}/src/fastmath.nim")
set(NIM_LIB "${CMAKE_CURRENT_BINARY_DIR}/libfastmath${CMAKE_SHARED_LIBRARY_SUFFIX}")

add_custom_command(
    OUTPUT "${NIM_LIB}"
    COMMAND "${NIM_EXECUTABLE}" c --app:lib --mm:orc --threads:on -d:release --out:"${NIM_LIB}" "${NIM_SRC}"
    DEPENDS "${NIM_SRC}"
    COMMENT "Compiling Nim module with ORC memory management"
)

add_custom_target(nim_fastmath ALL DEPENDS "${NIM_LIB}")

install(
    FILES "${NIM_LIB}"
    DESTINATION "fastmath"
)
```

### Building the Wheel
```bash
python -m build --wheel
```

Install and verify in any standard Python environment:
```bash
pip install dist/fastmath-*.whl
python -c "import fastmath; print(fastmath.add_numbers(10, 20))"
```
