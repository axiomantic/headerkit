# IR Transformations & Composable Pipelines

HeaderKit provides an orthogonal, visitor-based Intermediate Representation (IR) transformation framework. Transformations allow plugins, extensions, and users to mutate, filter, rename, or splice IR nodes between parser extraction and code generation.

---

## 1. Architecture Overview

Transformations operate on `SourceUnit` IR trees before writers emit code. The transformation architecture consists of three complementary layers:

1. **`IRTransformer` Visitor Base Class**: Extends the classic visitor pattern (analogous to Python's `ast.NodeTransformer`) with fine-grained node dispatch, type cross-reference updating, and immutable replacement semantics.
2. **`PipelineFilter` & `>>` Chaining**: Declarative filter objects that implement `transform(unit)` and compose cleanly using the right-shift operator (`>>`).
3. **Fine-Grained Node Hooks**: Extends HeaderKit's unified hook engine (`headerkit.hooks`) with point matching on `@hook("transform:<NodeType>")` with $O(1)$ fast-path lookup.

```
       +--------------------+
       |   Header Parsing   |  (libclang / tree-sitter)
       +---------+----------+
                 |
                 v  SourceUnit
       +---------+----------+
       |   PipelineFilter   |  unit >> StripPrefix("ma_") >> MakeOpaque("device")
       +---------+----------+
                 |
                 v  Transformed SourceUnit
       +---------+----------+
       |    Writer Output   |  (Nim / Cython / CFFI / ctypes / Lua / Mojo)
       +--------------------+
```

---

## 2. Using Built-in Filters

HeaderKit includes several production-grade built-in filters:

### StripPrefix
Removes a leading C naming convention prefix from types, functions, constants, and variables. Automatically rewrites all cross-references across struct fields, function parameters, and return types.

```python
from headerkit.transforms import StripPrefix

# Strip 'ma_' prefix from all symbols and update type references
transformed_unit = unit >> StripPrefix("ma_")
```

### MakeOpaque
Converts complete struct definitions into opaque handles by stripping their fields. This is commonly used to conceal internal implementation details in foreign language bindings.

```python
from headerkit.transforms import MakeOpaque

# Turn 'struct Device' into an opaque type without visible fields
transformed_unit = unit >> MakeOpaque("Device")
```

### RenameTypes
Applies an arbitrary dictionary mapping to rename types and systematically updates all referencing pointers, references, arrays, and function pointers across the entire AST.

```python
from headerkit.transforms import RenameTypes

# Rename legacy C types to modern idiomatic identifiers
transformed_unit = unit >> RenameTypes({
    "c_int32": "int32_t",
    "old_handle": "NewHandle",
})
```

### FilterByHeader
Retains only declarations that originated from specific header files, excluding third-party system dependencies pulled in by transitive `#include` directives.

```python
from headerkit.transforms import FilterByHeader

# Exclude stdio.h and stdlib.h symbols, keeping only mylib declarations
transformed_unit = unit >> FilterByHeader(["mylib.h", "mylib_types.h"])
```

### Chaining Filters with `>>`
Multiple filters can be combined fluently using the `>>` operator:

```python
from headerkit.transforms import FilterByHeader, MakeOpaque, StripPrefix

pipeline = (
    FilterByHeader(["sound.h"])
    >> StripPrefix("ma_")
    >> MakeOpaque("device")
)

final_unit = pipeline.transform(unit)
```

---

## 3. Creating Custom `IRTransformer` Subclasses

To author a custom transformation, subclass `IRTransformer` and override visitor methods for the AST nodes you wish to inspect or modify.

### Transformation Return Semantics
- Return **a new or modified node** (or `self.generic_visit(node)`) to replace the current node.
- Return `None` to **delete** the node from the enclosing declaration list.
- Return a `list[Declaration]` to **splice** multiple declarations in place of the single original node.

### Example: Splitting Functions with Legacy Deprecated Aliases

```python
from dataclasses import replace
from headerkit.ir import Function, SourceUnit
from headerkit.transforms import IRTransformer

class DeprecateAliases(IRTransformer):
    def visit_Function(self, node: Function) -> Function | list[Function] | None:
        # If function starts with 'legacy_', emit both modern and deprecated alias
        if node.name.startswith("legacy_"):
            modern_name = node.name.removeprefix("legacy_")
            modern_func = replace(node, name=modern_name)
            deprecated_func = replace(node, docstring="[Deprecated] Use modern alias.")
            return [modern_func, deprecated_func]
        return self.generic_visit(node)

transformer = DeprecateAliases()
updated_unit = transformer.transform(unit)
```

---

## 4. Hook Integration & Fast-Path Matching

Plugins can hook into node-level transformations dynamically without modifying existing writers or backends:

```python
from headerkit.hooks import PipelineContext, hook
from headerkit.ir import Function

@hook("transform:Function", priority=10, writer="nim")
def custom_nim_proc_transform(node: Function, context: PipelineContext | None = None) -> Function:
    # Custom adjustments for Nim procs
    return node
```

HeaderKit's hook engine uses point indexing (`_by_point`), ensuring that nodes without registered hooks incur zero visitor overhead during AST traversal.
