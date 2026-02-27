# Writing Custom Backends

clangir's backend system is pluggable. You can write your own parser backend to support alternative parsing strategies (tree-sitter, pycparser, hand-written parsers) and register it alongside the built-in `LibclangBackend`.

## The ParserBackend Protocol

Every backend must implement the [`ParserBackend`][clangir.ir.ParserBackend] protocol:

```python
from clangir.ir import Header, ParserBackend

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

### Method and Property Details

**`parse(code, filename, ...)`** -- Parse C/C++ source code and return a [`Header`][clangir.ir.Header] containing all extracted declarations. The `code` parameter is the source text (not a file path). The `filename` is used for error messages and `#line` directives; it does not need to exist on disk.

**`name`** -- A human-readable name for the backend (e.g., `"tree-sitter"`). This is the string users pass to `get_backend()`.

**`supports_macros`** -- Whether this backend can extract `#define` constants as [`Constant`][clangir.ir.Constant] declarations.

**`supports_cpp`** -- Whether this backend can parse C++ code (classes, templates, namespaces).

## Registering a Backend

Use [`register_backend()`][clangir.backends.register_backend] to add your backend to the global registry:

```python
from clangir.backends import register_backend

register_backend("mybackend", MyBackend, is_default=False)
```

Parameters:

- `name` -- The lookup key for `get_backend(name)`
- `backend_class` -- The class implementing `ParserBackend`
- `is_default` -- If `True`, this becomes the default backend returned by `get_backend()` with no arguments

!!! warning "Registration timing"
    Registration must happen at import time (module level), not inside a function. The registry is populated lazily when `get_backend()` or `list_backends()` is first called.

## Example: Tree-sitter Backend Skeleton

Here is a skeleton for a backend that uses [tree-sitter](https://tree-sitter.github.io/) to parse C headers:

```python
"""Tree-sitter based parser backend for clangir."""

from __future__ import annotations

from clangir.backends import register_backend
from clangir.ir import (
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    Header,
    Parameter,
    Pointer,
    SourceLocation,
    Struct,
    Typedef,
)


class TreeSitterBackend:
    """Parser backend using tree-sitter-c."""

    @property
    def name(self) -> str:
        return "tree-sitter"

    @property
    def supports_macros(self) -> bool:
        return False  # Would need separate macro handling

    @property
    def supports_cpp(self) -> bool:
        return False  # tree-sitter-c handles C only

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
    ) -> Header:
        import tree_sitter_c as tsc
        from tree_sitter import Language, Parser

        parser = Parser(Language(tsc.language()))
        tree = parser.parse(code.encode())

        declarations = []
        for node in tree.root_node.children:
            decl = self._convert_node(node, filename)
            if decl is not None:
                declarations.append(decl)

        return Header(path=filename, declarations=declarations)

    def _convert_node(self, node, filename):
        """Convert a tree-sitter node to an IR declaration.

        This is where the bulk of the work goes: mapping tree-sitter's
        concrete syntax tree nodes to clangir IR types.
        """
        # Implementation would handle:
        #   - "struct_specifier" -> Struct
        #   - "enum_specifier" -> Enum
        #   - "function_definition" / "declaration" -> Function
        #   - "type_definition" -> Typedef
        # Each node type needs its own conversion logic.
        return None


# Self-register at import time
try:
    import tree_sitter_c  # noqa: F401

    register_backend("tree-sitter", TreeSitterBackend)
except ImportError:
    pass  # tree-sitter-c not installed; backend not available
```

## Using Your Backend

Once registered, your backend is available through the standard API:

```python
from clangir import get_backend, list_backends

# List all available backends
print(list_backends())  # ['libclang', 'tree-sitter']

# Use your backend explicitly
backend = get_backend("tree-sitter")
header = backend.parse(code, "example.h")
```

## Producing Correct IR

When implementing a backend, pay attention to these IR conventions:

### Typedefs vs. Tagged Types

When C code uses `typedef struct { ... } Name;`, the IR should produce a [`Struct`][clangir.ir.Struct] with `is_typedef=True`. This tells writers to emit the typedef form rather than a bare struct declaration.

### Anonymous Types

Set `name=None` for truly anonymous structs, enums, or unions. The built-in writers will skip anonymous types that cannot be referenced.

### Source Locations

Populate [`SourceLocation`][clangir.ir.SourceLocation] on declarations when possible. This enables filtering by file (to exclude system headers) and better error messages:

```python
loc = SourceLocation(file=filename, line=node.start_point[0] + 1)
```

### Type Composition

Build types from the inside out. For `const char **`:

```python
from clangir import CType, Pointer

const_char = CType("char", ["const"])
const_char_ptr = Pointer(const_char)
const_char_ptr_ptr = Pointer(const_char_ptr)
```

## Testing Your Backend

Test your backend by comparing its output against the built-in `LibclangBackend` for the same input:

```python
from clangir import get_backend

code = """
struct Point {
    int x;
    int y;
};

int add(int a, int b);
"""

libclang = get_backend("libclang")
custom = get_backend("my-backend")

expected = libclang.parse(code, "test.h")
actual = custom.parse(code, "test.h")

# Compare declaration counts and types
assert len(actual.declarations) == len(expected.declarations)
for exp, act in zip(expected.declarations, actual.declarations):
    assert type(exp) == type(act)
    assert exp.name == act.name
```
