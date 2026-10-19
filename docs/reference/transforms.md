# IR Transforms API Reference

The `headerkit.transforms` package provides classes, interfaces, and built-in filters for transforming and manipulating Intermediate Representation (IR) AST structures.

---

## Classes & Protocols

### `IRTransformer`
Base visitor class for inspecting and transforming HeaderKit IR trees.

```python
class IRTransformer:
    def transform(self, unit: SourceUnit) -> SourceUnit: ...
    def visit(self, node: Any) -> Any: ...
    def generic_visit(self, node: Any) -> Any: ...
```

#### Visitor Methods
- `visit_Enum(self, node: Enum) -> Enum | list[Declaration] | None`
- `visit_Struct(self, node: Struct) -> Struct | list[Declaration] | None`
- `visit_Function(self, node: Function) -> Function | list[Declaration] | None`
- `visit_Typedef(self, node: Typedef) -> Typedef | list[Declaration] | None`
- `visit_Variable(self, node: Variable) -> Variable | list[Declaration] | None`
- `visit_Constant(self, node: Constant) -> Constant | list[Declaration] | None`
- `visit_MacroFunction(self, node: MacroFunction) -> MacroFunction | list[Declaration] | None`

#### Return Semantics
- **New or modified node**: Replaces the visited node.
- **`None`**: Deletes the visited node from its enclosing container.
- **`list[Declaration]`**: Splices multiple declarations in place of the single original node.

---

### `PipelineFilter`
Protocol defining a composable pipeline transformation stage.

```python
class PipelineFilter(Protocol):
    def transform(self, unit: SourceUnit) -> SourceUnit: ...
    def __rshift__(self, other: PipelineFilter) -> ChainedFilter: ...
```

### `ChainedFilter`
Represents two or more filters composed together via the `>>` operator.

---

## Built-in Filters

### `StripPrefix(prefix: str)`
Removes a specified identifier prefix from symbols and automatically rewrites referencing type expressions across the entire AST.

- **`prefix`** (`str`): The naming convention prefix to strip (e.g. `"ma_"`).
- **Type rewriting**: Invokes `rewrite_type_references()` across all parameter types, return types, field types, and nested types.

### `MakeOpaque(type_names: str | Sequence[str])`
Converts specified struct definitions into opaque types by clearing their fields list.

- **`type_names`** (`str | Sequence[str]`): Name or sequence of struct names to make opaque.

### `RenameTypes(type_map: Mapping[str, str])`
Renames type declarations according to a dictionary mapping and updates all AST cross-references.

- **`type_map`** (`Mapping[str, str]`): Dictionary mapping original type names to replacement type names.

### `StripPaddingFields()`
Detects and removes compiler-generated or artificial padding fields (such as `_pad`, `_padding`, `__pad`) from structs.

### `FilterByHeader(allowed_headers: Sequence[str])`
Filters declarations to keep only those defined in the specified list of source files or headers.

- **`allowed_headers`** (`Sequence[str]`): Collection of filenames or path substrings to retain.

---

## Functions

### `rewrite_type_references(type_expr: TypeExpr, mapping: Mapping[str, str]) -> TypeExpr`
Recursively traverses a `TypeExpr` hierarchy (`CType`, `Pointer`, `Reference`, `Array`, `FunctionPointer`) and replaces type names that match keys in `mapping`.
