# Mojo Writer

The Mojo writer (`headerkit.writers.mojo`) generates idiomatic Mojo bindings with `sys.ffi.DLHandle` and CShim bridge support for consuming C and C++ libraries from Modular's Mojo language.

## Features

- **Standard C FFI with `sys.ffi.DLHandle`**: Generates a dynamic library wrapper struct with typed method signatures for loaded shared objects (`.so`, `.dylib`, `.dll`).
- **Mojo Type Mappings**: Automatically maps C primitives and pointers to Mojo equivalents (`Int32`, `UInt64`, `Float32`, `Float64`, `Bool`, `Int`, `UnsafePointer[T]`, `UnsafePointer[NoneType]`).
- **Structs & Enums**: Emits C-compatible `@value @register_passable("trivial") struct` types.
- **C++ CShim Bridge**: Bridges C++ classes shimmed via Headerkit's C-ABI shims into high-level Mojo OOP wrapper structs with constructor, destructor, and method forwarders.
- **Identifier Escaping**: Automatically escapes reserved Mojo keywords (`fn`, `var`, `struct`, `let`, `mut`, `inout`, etc.).

## Usage

=== "CLI"

    ```bash
    headerkit mylib.h -w mojo -o mojo:mylib.mojo
    ```

=== "Python API"

    ```python
    from headerkit.writers import get_writer

    writer = get_writer("mojo", library_name="FastMath")
    mojo_code = writer.write(unit)
    ```

## Example Output

Given a C header:

```c
typedef struct {
    float x;
    float y;
} Point;

int add(int a, int b);
```

The Mojo writer produces:

```mojo
# Auto-generated Mojo bindings by HeaderKit
from sys.ffi import DLHandle
from memory import UnsafePointer

@value
@register_passable("trivial")
struct Point:
    var x: Float32
    var y: Float32

struct Library:
    var handle: DLHandle

    fn __init__(out self, path: String) raises:
        self.handle = DLHandle(path)

    fn close(mut self):
        self.handle.close()

    fn add(self, a: Int32, b: Int32) -> Int32:
        var f = self.handle.get_function[fn(Int32, Int32) -> Int32]("add")
        return f(a, b)
```

## API Reference

::: headerkit.writers.mojo.MojoWriter
    options:
      show_source: false

::: headerkit.writers.mojo.write_mojo
    options:
      show_source: false
