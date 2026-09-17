# Configuration Matrix API Reference

The `headerkit.matrix` package models multi-dimensional configuration variations, automated AST dimension discovery, compiler probing, and manifest persistence.

---

## Data Models

### `MatrixDimension`
Represents an individual variation axis in the configuration matrix.

```python
@dataclass(frozen=True)
class MatrixDimension:
    name: str
    values: tuple[Any, ...]
    dimension_type: Literal["target", "version", "feature", "custom"]
    define_template: str | None = None
```

- **`name`**: Dimension identifier (e.g. `"target"`, `"version"`, `"ENABLE_SSL"`).
- **`values`**: Tuple of discrete coordinate values for this dimension.
- **`dimension_type`**: Classification tag (`"target"`, `"version"`, `"feature"`, `"custom"`).
- **`define_template`**: Optional compiler flag format template (e.g. `"-D{name}={val}"`).

---

### `ConfigCondition`
Immutable, hashable dictionary representing an evaluated point in the multi-dimensional parameter space.

```python
class ConfigCondition(Mapping[str, Any]):
    def __init__(self, coordinates: Mapping[str, Any]) -> None: ...
    @property
    def target(self) -> str | None: ...
    @property
    def version(self) -> Any: ...
    def to_compiler_flags(self) -> list[str]: ...
```

- **`to_compiler_flags()`**: Translates condition coordinates into Clang flags (`--target=...`, `-DFEAT=1`, `-UFEAT`).
- **Hashable**: Usable as dictionary keys and set members.

---

### `ConfigMatrix`
Manages matrix dimensions, combinatorial Cartesian resolution, constraints, and explosion safety.

```python
@dataclass
class ConfigMatrix:
    dimensions: list[MatrixDimension] = field(default_factory=list)
    constraints: list[Callable[[ConfigCondition], bool]] = field(default_factory=list)
    max_configurations: int = 32
    allow_unbounded: bool = False

    def add_dimension(
        self,
        name: str,
        values: Sequence[Any],
        dimension_type: str = "feature",
        define_template: str | None = None,
    ) -> None: ...

    def add_constraint(self, constraint: Callable[[ConfigCondition], bool]) -> None: ...
    def has_dimension(self, name: str) -> bool: ...
    def get_dimension(self, name: str) -> MatrixDimension: ...
    def resolve(self) -> list[ConfigCondition]: ...
```

---

## Probing & Discovery

### `discover_dimensions(unit: SourceUnit, ...) -> ConfigMatrix`
Scans an initial baseline AST to discover version macros and boolean feature flags.

```python
def discover_dimensions(
    unit: SourceUnit,
    default_targets: tuple[str, ...] = (),
    max_features: int = 2,
    candidate_features: Sequence[str] | None = None,
) -> ConfigMatrix: ...
```

### `probe_configuration(header_path: str, condition: ConfigCondition, ...) -> SourceUnit`
Invokes the backend parser to evaluate a header under specific compiler flags.

```python
def probe_configuration(
    header_path: str,
    condition: ConfigCondition,
    options: ProbeOptions | None = None,
) -> SourceUnit: ...
```

### `ProbeOptions`
Configuration options for compiler probing:
- **`sysroot`** (`str | None`): Path to target sysroot.
- **`extra_include_dirs`** (`tuple[str, ...]`): Additional system include search paths.
- **`allow_missing_sysroot`** (`bool`): Whether to fall back to intrinsic headers with a `SysrootWarning`.

---

## Manifest & Overrides

### `MatrixManifest`
Stores serialization metadata and matrix schemas inside generated file headers.

```python
@dataclass
class MatrixManifest:
    schema_version: int
    source_hash: str
    header_path: str
    matrix: ConfigMatrix

    def to_dict(self) -> dict[str, Any]: ...
    def to_json(self, indent: int = 2) -> str: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatrixManifest: ...
    @classmethod
    def from_json(cls, json_str: str) -> MatrixManifest: ...
```

### `extract_overrides(source_code: str, language: str = "nim") -> dict[str, str]`
Extracts manual `@headerkit:override` blocks from existing code.

### `inject_overrides(generated_code: str, overrides: dict[str, str], language: str = "nim") -> str`
Re-injects preserved manual override content into generated code.
