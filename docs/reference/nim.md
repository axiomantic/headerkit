# Nim Writer

The Nim writer (`headerkit.writers.nim`) converts headerkit [IR](ir.md) into native Nim binding modules supporting both C and C++ FFI.

## Features

- **C Interoperability**: Generates `{.importc.}` pragmas with `struct`/`union`/`enum` tag specifiers, `cdecl` calling conventions, and type mappings.
- **C++ Interoperability**: Generates `{.importcpp.}` pragmas for C++ classes, single and polymorphic inheritance (`object of RootObj`), methods, constructors, destructors, and operators.
- **Templates & Generics**: Emits parametric Nim object types and generic procs (e.g. `Container[T]*`).
- **Smart Pointers & Containers**: Maps `std::shared_ptr<T>`, `std::unique_ptr<T>`, `std::vector<T>`, and `std::string` to Nim wrapper types.
- **Iterators**: Generates idiomatic `items` iterators for C++ containers with `begin()`/`end()` methods.
- **Keyword & Identifier Escaping**: Escapes Nim reserved keywords and formats identifiers safely.

## Usage

=== "CLI"

    ```bash
    headerkit mylib.h -w nim -o nim:mylib.nim
    ```

=== "Python API"

    ```python
    from headerkit.backends.libclang import LibclangBackend
    from headerkit.writers.nim import NimWriter, write_nim

    backend = LibclangBackend()
    header = backend.parse(code, "mylib.h")

    # Using NimWriter class
    writer = NimWriter(header_path="mylib.h")
    nim_code = writer.write(header)

    # Or convenience helper
    nim_code = write_nim(header, header_path="mylib.h")
    ```

## Type Mappings

| C / C++ Type | Nim Type |
|---|---|
| `void` | `void` |
| `bool` / `_Bool` | `bool` |
| `char` / `signed char` | `cchar` |
| `unsigned char` | `cuchar` / `uint8` |
| `short` / `unsigned short` | `cshort` / `cushort` |
| `int` / `unsigned int` | `cint` / `cuint` |
| `long` / `unsigned long` | `clong` / `culong` |
| `long long` / `unsigned long long` | `clonglong` / `culonglong` |
| `size_t` / `ssize_t` | `csize_t` / `csSize` |
| `float` / `double` | `cfloat` / `cdouble` |
| `const char *` | `cstring` |
| `void *` | `pointer` |
| `T *` | `ptr T` |
| `T &` | `var T` |
| `T &&` (rvalue ref) | `sink T` |
| `T[N]` | `array[N, T]` |

## API Reference

::: headerkit.writers.nim.NimWriter
    options:
      show_source: false

::: headerkit.writers.nim.write_nim
    options:
      show_source: false
