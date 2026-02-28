# IR Types

The Intermediate Representation (IR) is the core data model of headerkit. Parser backends
produce IR objects; writers consume them to generate output in various formats.

All IR types are Python dataclasses defined in the `headerkit.ir` module.

## Container

The top-level object returned by all parser backends.

::: headerkit.ir.Header
    options:
      show_source: false

## Type Expressions

Type expressions form a recursive tree structure representing C type syntax.
For example, `const char**` becomes `Pointer(Pointer(CType("char", ["const"])))`.

::: headerkit.ir.CType
    options:
      show_source: false

::: headerkit.ir.Pointer
    options:
      show_source: false

::: headerkit.ir.Array
    options:
      show_source: false

::: headerkit.ir.FunctionPointer
    options:
      show_source: false

## Declarations

Declaration types represent the top-level constructs found in C/C++ headers.

::: headerkit.ir.Enum
    options:
      show_source: false

::: headerkit.ir.EnumValue
    options:
      show_source: false

::: headerkit.ir.Struct
    options:
      show_source: false

::: headerkit.ir.Field
    options:
      show_source: false

::: headerkit.ir.Function
    options:
      show_source: false

::: headerkit.ir.Parameter
    options:
      show_source: false

::: headerkit.ir.Typedef
    options:
      show_source: false

::: headerkit.ir.Variable
    options:
      show_source: false

::: headerkit.ir.Constant
    options:
      show_source: false

## Union Types

These are `typing.Union` aliases used in type annotations throughout headerkit.

### `Declaration`

```python
Declaration = Union[Enum, Struct, Function, Typedef, Variable, Constant]
```

Any top-level declaration that can appear in a [`Header`][headerkit.ir.Header].

### `TypeExpr`

```python
TypeExpr = Union[CType, Pointer, Array, FunctionPointer]
```

Any type expression that can appear in a declaration's type fields.

## Source Location

::: headerkit.ir.SourceLocation
    options:
      show_source: false

## Parser Backend Protocol

The parser backend protocol is defined alongside the IR types since backends
produce IR directly. See also the [Backends](backends.md) page for registry functions.

::: headerkit.ir.ParserBackend
    options:
      show_source: false
