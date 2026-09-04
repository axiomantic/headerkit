# Architecture Overview

headerkit is organized around a unified, hook-driven pipeline: **backends** parse source units into an **IR** (Intermediate Representation) rooted at `SourceUnit`, optional **transform hooks** apply AST mutations or dialect adaptations, and **writers** consume the IR to generate target output.

## The Pipeline

```mermaid
graph TD
    A["Input Source / InputSpec"] --> B
    B["Backend: parse_unit<br>(ParserBackend protocol)"] --> C
    C["IR<br>(SourceUnit, Declaration, TypeExpr)"] --> D
    D["Transformations: transform_unit<br>(Waterfall hook pipeline)"] --> E
    E["Writer: write_output<br>(WriterBackend protocol)"] --> F
    F["Output String<br>(CFFI cdef, ctypes, Cython .pxd, Nim, ...)"]

    B -.- B1["e.g., LibclangBackend, TreeSitterBackend"]
    E -.- E1["e.g., CffiWriter, CtypesWriter,<br>CythonWriter, NimWriter, LuaWriter, ..."]
```

Each stage is decoupled through the unified hook engine (`headerkit.hooks`). Backends know nothing about writers. Writers know nothing about backends. The IR is the contract between them.

## Layer 1: Backends (Parsing)

A backend implements the [`ParserBackend`][headerkit.ir.ParserBackend] protocol and converts C/C++ source code into IR.

```python
from headerkit import ParserBackend
from headerkit.ir import Header

class ParserBackend(Protocol):
    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
    ) -> Header: ...

    @property
    def name(self) -> str: ...

    @property
    def supports_macros(self) -> bool: ...

    @property
    def supports_cpp(self) -> bool: ...
```

### Built-in Backend: LibclangBackend

The `LibclangBackend` uses LLVM's libclang to parse headers. It provides:

- Full C and C++ support (templates, namespaces, classes)
- Preprocessor handling (`#include`, `#define`, `#ifdef`)
- Source location tracking for error reporting
- Recursive include processing for umbrella headers

```python
from headerkit import get_backend

backend = get_backend("libclang")
header = backend.parse(code, "myheader.h")
```

### Backend Registry

Backends register themselves using `register_backend()`:

```python
from headerkit.backends import register_backend

register_backend("mybackend", MyBackendClass, is_default=False)
```

Registry functions:

| Function | Description |
|----------|-------------|
| `get_backend(name=None)` | Get a backend instance (default if `name` is `None`) |
| `list_backends()` | List all registered backend names |
| `is_backend_available(name)` | Check if a backend is usable (real load test for libclang) |
| `register_backend(name, cls)` | Register a new backend |

See [Writing Custom Backends](custom-backends.md) for a complete guide.

## Layer 2: IR (Intermediate Representation)

The IR is a tree of Python dataclasses rooted at [`Header`][headerkit.ir.Header]. It is designed to be parser-agnostic: any backend that can parse C/C++ can produce the same IR.

### Type Expressions

Type expressions (`TypeExpr`) represent C types as composable trees:

```mermaid
classDiagram
    class TypeExpr {
        <<protocol>>
    }
    class CType {
        name: str
        qualifiers: list[str]
    }
    class Pointer {
        pointee: TypeExpr
        qualifiers: list[str]
    }
    class Array {
        element_type: TypeExpr
        size: int | None
    }
    class FunctionPointer {
        return_type: TypeExpr
        parameters: list[Parameter]
        is_variadic: bool
    }

    TypeExpr <|-- CType
    TypeExpr <|-- Pointer
    TypeExpr <|-- Array
    TypeExpr <|-- FunctionPointer
    Pointer --> TypeExpr : pointee
    Array --> TypeExpr : element_type
    FunctionPointer --> TypeExpr : return_type
```

| Class | Represents | Example |
|-------|-----------|---------|
| [`CType`][headerkit.ir.CType] | Base type with qualifiers | `int`, `const char`, `unsigned long` |
| [`Pointer`][headerkit.ir.Pointer] | Pointer to another type | `int*`, `const char*`, `void**` |
| [`Array`][headerkit.ir.Array] | Fixed or flexible array | `int[10]`, `char[]` |
| [`FunctionPointer`][headerkit.ir.FunctionPointer] | Function pointer | `void (*)(int, char*)` |

Types compose naturally:

```python
from headerkit import CType, Pointer, Array

# const char*
const_char_ptr = Pointer(CType("char", ["const"]))

# int**
int_ptr_ptr = Pointer(Pointer(CType("int")))

# const char*[]
string_array = Array(Pointer(CType("char", ["const"])))
```

### Declarations

Declarations (`Declaration`) represent top-level C/C++ constructs:

```mermaid
classDiagram
    class Declaration {
        <<protocol>>
        name: str | None
        location: SourceLocation | None
    }
    class Struct {
        fields: list[Field]
        is_union: bool
        is_typedef: bool
    }
    class Enum {
        values: list[EnumValue]
        is_typedef: bool
    }
    class Function {
        return_type: TypeExpr
        parameters: list[Parameter]
        is_variadic: bool
    }
    class Typedef {
        underlying_type: TypeExpr
    }
    class Variable {
        type: TypeExpr
    }
    class Constant {
        value: int | str | None
        is_macro: bool
    }

    Declaration <|-- Struct
    Declaration <|-- Enum
    Declaration <|-- Function
    Declaration <|-- Typedef
    Declaration <|-- Variable
    Declaration <|-- Constant
```

| Class | Represents |
|-------|-----------|
| [`Struct`][headerkit.ir.Struct] | Structs, unions, and C++ classes |
| [`Enum`][headerkit.ir.Enum] | Enumerations with named constants |
| [`Function`][headerkit.ir.Function] | Function prototypes |
| [`Typedef`][headerkit.ir.Typedef] | Type aliases |
| [`Variable`][headerkit.ir.Variable] | Global/extern variables |
| [`Constant`][headerkit.ir.Constant] | `#define` macros and `const` values |

### The SourceUnit Container

[`SourceUnit`][headerkit.ir.SourceUnit] (with backward-compatible alias [`Header`][headerkit.ir.Header]) is the top-level container returned by all backends:

```python
from headerkit.ir import SourceUnit

# SourceUnit fields:
#   path: str                        -- original file path or synthetic name
#   declarations: list[Declaration]  -- all extracted declarations
#   included_headers: set[str]       -- basenames of included headers
#   language: str                    -- source language (e.g., "c", "cpp")
#   classification: str              -- classification (e.g., "header", "source")
```

## Layer 3: Writers (Output)

Writers convert [`SourceUnit`][headerkit.ir.SourceUnit] IR into code, definitions, or packages. Concrete writers inherit from [`BaseWriter`][headerkit.writers.BaseWriter] (which satisfies the underlying [`WriterBackend`][headerkit.writers.WriterBackend] protocol).

`BaseWriter` unifies single-string rendering (`write()`) and multi-file package scaffolding (`write_layout()`):

```python
from headerkit.ir import SourceUnit
from headerkit.scaffold import ProjectLayout, ScaffoldOptions
from headerkit.writers import BaseWriter, WriterOption

class MyWriter(BaseWriter):
    name: str = "mywriter"
    format_description: str = "My custom bindings"

    def _render(self, unit: SourceUnit) -> str:
        # Generate the primary output string
        ...
```

Writers declare their supported layout modes (`supported_layouts`) and configuration options (`supported_options`). Writer-specific options are passed to the constructor or via `--writer-opt`.

### Built-in Writers

| Writer | Registry Name | Output | Primary Options |
|---|---|---|---|
| [`CffiWriter`][headerkit.writers.cffi.CffiWriter] | `cffi` (default) | CFFI `cdef` strings | `exclude_patterns: list[str] \| None` |
| [`CshimWriter`][headerkit.writers.cshim.CShimWriter] | `cshim` | Pure C-ABI (`extern "C"`) wrappers for C++ | `wrapper_header_name: str`, `catch_exceptions: bool` |
| [`CtypesWriter`][headerkit.writers.ctypes.CtypesWriter] | `ctypes` | Python ctypes binding modules | `lib_name: str` |
| [`CythonWriter`][headerkit.writers.cython.CythonWriter] | `cython` | Cython `.pxd` declarations | -- |
| [`DiffWriter`][headerkit.writers.diff.DiffWriter] | `diff` | API compatibility diff reports (JSON or Markdown) | `baseline: SourceUnit \| None`, `format: str` |
| [`JsonWriter`][headerkit.writers.json.JsonWriter] | `json` | JSON serialization of IR | `indent: int \| None` |
| [`LuaWriter`][headerkit.writers.lua.LuaWriter] | `lua` | LuaJIT FFI bindings | -- |
| [`MojoWriter`][headerkit.writers.mojo.MojoWriter] | `mojo` | Idiomatic Mojo FFI bindings (`sys.ffi.DLHandle`) | `lib_name: str` |
| [`NimWriter`][headerkit.writers.nim.NimWriter] | `nim` | Native Nim modules with `{.importc.}` pragmas | `header_file: str`, `cdecl: bool` |
| [`PromptWriter`][headerkit.writers.prompt.PromptWriter] | `prompt` | Token-optimized output for LLM context | `verbosity: str` |

### Writer Registry

Writers use the same registry pattern as backends:

```python
from headerkit.writers import register_writer

register_writer("mywriter", MyWriterClass, description="My custom output format")
```

Registry functions:

| Function | Description |
|----------|-------------|
| `get_writer(name=None, **kwargs)` | Get a writer instance; kwargs forwarded to constructor |
| `list_writers()` | List all registered writer names |
| `is_writer_available(name)` | Check if a writer is registered |
| `register_writer(name, cls)` | Register a new writer |
| `get_writer_info()` | Get metadata for all writers |

See [Writing Custom Writers](custom-writers.md) for a complete guide.

## Design Principles

**Parser-agnostic IR.** The IR does not leak backend-specific details. A `Struct` from libclang looks exactly the same as a `Struct` from any other backend. This means writers work identically regardless of which backend produced the IR.

**Composable types.** Type expressions are recursive dataclasses that mirror how C types actually compose. `const char**` is `Pointer(Pointer(CType("char", ["const"])))` -- no string parsing needed.

**Best-effort output.** Writers silently skip declarations they cannot represent rather than raising exceptions. This makes the pipeline robust against headers with exotic constructs.

**Self-registering plugins.** Both backends and writers register themselves at import time. Adding a new backend or writer requires zero changes to headerkit's core code. Just implement the protocol, call `register_backend()` or `register_writer()`, and your plugin is available through `get_backend()` or `get_writer()`.
