# CFFI Writer

The CFFI writer generates C declaration strings suitable for passing to
[`ffibuilder.cdef()`](https://cffi.readthedocs.io/en/stable/cdef.html).
It handles structs, unions, enums, functions, typedefs, variables, and
integer constants.

## Writer Class

::: headerkit.writers.cffi.CffiWriter
    options:
      show_source: false

## Convenience Function

::: headerkit.writers.cffi.header_to_cffi
    options:
      show_source: false

## Low-Level Functions

These functions are used internally by [`header_to_cffi`][headerkit.writers.cffi.header_to_cffi]
and can be useful when working with individual declarations or type expressions.

::: headerkit.writers.cffi.type_to_cffi
    options:
      show_source: false

::: headerkit.writers.cffi.decl_to_cffi
    options:
      show_source: false
