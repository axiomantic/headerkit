# Reconciliation API Reference

The `headerkit.reconcile` module provides topological declaration sequence alignment, boolean condition minimization, and polyglot guard generation across multiple compilation units.

---

## Data Models

### `MatrixCondition`
Represents an algebraic boolean predicate governing a guarded AST block.

```python
@dataclass(frozen=True)
class MatrixCondition:
    predicate: str
    raw_conditions: tuple[ConfigCondition, ...]
```

- **`predicate`**: Rendered condition string in target language syntax (e.g. `(defined(linux) or defined(macosx))`).
- **`raw_conditions`**: Tuple of underlying `ConfigCondition` coordinates satisfied by this branch.

---

### `MatrixGuardedBlock`
Represents a divergent block containing mutually exclusive declaration branches.

```python
@dataclass
class MatrixGuardedBlock:
    branches: list[tuple[MatrixCondition, list[Declaration]]]
```

---

### `MultiDimensionalReconciledUnit`
Represents an aligned compilation unit containing unconditionally common declarations interleaved with matrix-guarded blocks.

```python
@dataclass
class MultiDimensionalReconciledUnit:
    path: str
    items: list[Declaration | MatrixGuardedBlock] = field(default_factory=list)
```

---

## Reconciliation Functions

### `reconcile_matrix(matrix_units, matrix=None, target_lang="nim")`
Reconciles $N$ AST compilation units across a multi-dimensional configuration matrix.

```python
def reconcile_matrix(
    matrix_units: Sequence[tuple[ConfigCondition, SourceUnit]],
    matrix: ConfigMatrix | None = None,
    target_lang: str = "nim",
) -> MultiDimensionalReconciledUnit: ...
```

#### Behavioral Guarantees
- **Anchor Alignment**: Emits common declarations unconditionally in monotonic topological order.
- **Divergence Slicing**: Slices between anchors are partitioned into equivalence classes of identical declarations.
- **Condition Minimization**: Merged equivalence classes are simplified using `minimize_conditions()`.

---

### `reconcile_units(units: Sequence[tuple[str, Header | SourceUnit]]) -> ReconciledUnit`
Legacy 1D platform reconciliation function. Projects single-dimension platform inputs onto `reconcile_matrix()` to uphold the Zero-Dual-System Rule.

---

## Algebraic Minimization Functions

### `minimize_conditions(configs, domain_matrix, target_lang="nim") -> str`
Algebraically simplifies a set of configuration coordinates into a minimal boolean guard expression.

```python
def minimize_conditions(
    configs: Sequence[ConfigCondition],
    domain_matrix: dict[str, set[Any]],
    target_lang: str = "nim",
) -> str: ...
```

#### Reduction Rules
1. **Universal Dimension Elimination**: If all values in a dimension's domain are present, that dimension is eliminated.
2. **Platform Grouping**: Categorical platform sets are combined into parenthesized disjunctions.
3. **Version Interval Reduction**: Contiguous version ranges are compressed into monotonic comparisons (`>=`, `==`, or bounded ranges).
4. **Quine-McCluskey Reduction**: Boolean feature flags are minimized into prime implicants.

---

### `quine_mccluskey_minimize(configs, bool_features) -> list[tuple[bool | None, ...]]`
Solves prime implicants for boolean feature flags using the tabular Quine-McCluskey algorithm.

```python
def quine_mccluskey_minimize(
    configs: Sequence[ConfigCondition],
    bool_features: Sequence[str],
) -> list[tuple[bool | None, ...]]: ...
```
