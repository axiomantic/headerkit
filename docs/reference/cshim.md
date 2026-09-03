# CShim Writer

The CShim writer (`headerkit.writers.cshim`) generates pure C-ABI wrapper functions (`extern "C"`) around C++ classes, constructors, destructors, methods, and namespaced free functions using opaque pointer handles.

## Features

- **Opaque Handles**: Wraps C++ class instances as `typedef void* ClassNameHandle;`.
- **Constructors & Destructors**: Emits `ClassName_create(...)` and `ClassName_destroy(handle)`.
- **Methods**: Emits `ClassName_methodName(handle, ...)` forwarding calls to the underlying C++ instance.
- **Operators**: Maps C++ overloaded operators (e.g. `operator[]`, `operator+=`, `operator==`) to named C shim functions (e.g. `ClassName_subscript`, `ClassName_op_add_assign`, `ClassName_op_equal`).
- **Namespaces**: Flattens nested C++ namespaces into prefix conventions (`ns1_ns2_func()`).

## Usage

=== "CLI"

    ```bash
    headerkit mylib.hpp -w cshim -o cshim:mylib_cshim.cpp
    ```

=== "Python API"

    ```python
    from headerkit.backends.libclang import LibclangBackend
    from headerkit.writers.cshim import CShimWriter, write_cshim

    backend = LibclangBackend()
    header = backend.parse(code, "mylib.hpp", extra_args=["-x", "c++"])

    writer = CShimWriter(header_path="mylib.hpp")
    shim_code = writer.write(header)
    ```

## API Reference

::: headerkit.writers.cshim.CShimWriter
    options:
      show_source: false

::: headerkit.writers.cshim.write_cshim
    options:
      show_source: false
