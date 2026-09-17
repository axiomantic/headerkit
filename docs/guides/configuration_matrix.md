# Parameterized Configuration Matrices

HeaderKit provides a multi-dimensional configuration matrix framework to model variations across operating systems, CPU architectures, library versions, and feature toggles.

---

## 1. Core Concepts

A configuration matrix defines variation axes over which C headers are parsed and evaluated:

- **`MatrixDimension`**: Represents an individual axis of variation, such as `target` (`x86_64-apple-darwin`, `aarch64-unknown-linux-gnu`, `x86_64-pc-windows-msvc`), `version` (`(2, 0)`, `(2, 1)`), or boolean feature flags (`ENABLE_SSL`, `USE_VORBIS`).
- **`ConfigCondition`**: An immutable, hashable coordinate dictionary specifying an exact slice through the matrix.
- **`ConfigMatrix`**: Computes the Cartesian product of all dimensions, applies pruning constraints, and guards against combinatorial explosion.

```
                  +---------------------------+
                  |       ConfigMatrix        |
                  +-------------+-------------+
                                |
          +---------------------+---------------------+
          |                     |                     |
          v                     v                     v
   target dimension      version dimension     feature dimension
   - x86_64-apple-darwin  - 2.0.0               - ENABLE_SSL (True/False)
   - aarch64-linux-gnu   - 2.1.0               - USE_VORBIS (True/False)
   - x86_64-pc-windows
          |                     |                     |
          +---------------------+---------------------+
                                |
                                v Cartesian Product + Constraints
                 [ConfigCondition_1, ConfigCondition_2, ...]
```

---

## 2. Automated Dimension Discovery

Instead of manually configuring matrices, HeaderKit can automatically inspect an exploratory AST parse and discover version macros and feature flags:

```python
from headerkit.backends import get_backend
from headerkit.matrix import discover_dimensions

backend = get_backend("libclang")
unit = backend.parse_source("include/miniaudio.h")

# Automatically discover version macros and up to 2 active feature toggles
matrix = discover_dimensions(
    unit,
    default_targets=("x86_64-apple-darwin", "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc"),
    max_features=2,
)

for dim in matrix.dimensions:
    print(f"Discovered {dim.name} ({dim.dimension_type}): {dim.values}")
```

### Discovery Heuristics
- **Version triples**: Scans for `LIB_VERSION_MAJOR`, `MINOR`, `PATCH` constants and groups them into version tuples.
- **Integer API versions**: Detects monolithic integer version constants such as `SQLITE_VERSION_NUMBER` (e.g. `34200`).
- **Feature toggles**: Identifies macros matching prefixes `ENABLE_`, `USE_`, `HAS_`, `WITH_`, `DISABLE_`, and `NO_`.

---

## 3. Pruning Invalid Combinations with Constraints

In real-world C libraries, certain configurations are invalid or mutually exclusive (e.g. DirectSound is Windows-only, while CoreAudio is macOS-only).

You can register constraint predicates on `ConfigMatrix`:

```python
from headerkit.matrix import ConfigCondition, ConfigMatrix

matrix = ConfigMatrix()
matrix.add_dimension("target", ["x86_64-apple-darwin", "x86_64-pc-windows-msvc"], dimension_type="target")
matrix.add_dimension("USE_DIRECTSOUND", [True, False], dimension_type="feature")

# Constraint: DirectSound cannot be enabled on non-Windows targets
def validate_audio_backend(cond: ConfigCondition) -> bool:
    if cond.get("USE_DIRECTSOUND") and "windows" not in cond.get("target", ""):
        return False
    return True

matrix.add_constraint(validate_audio_backend)
valid_configs = matrix.resolve()
```

---

## 4. Combinatorial Explosion Protection

Evaluating full Cartesian products can grow exponentially ($2^N \times M \times P$). HeaderKit safeguards against accidental freezes:

- **Safe Threshold**: `ConfigMatrix` enforces `MAX_MATRIX_CONFIGURATIONS = 32` by default.
- **Explosion Guard**: If `.resolve()` exceeds this threshold, HeaderKit raises a `MatrixExplosionError` with recommendations to register constraint filters.
- **Explicit Override**: For comprehensive batch compilation runs with known high capacity, pass `allow_unbounded=True`.

```python
from headerkit.matrix import ConfigMatrix, MatrixExplosionError

matrix = ConfigMatrix(max_configurations=16)
# Adding too many unconstrained dimensions:
# matrix.resolve() -> raises MatrixExplosionError

# To bypass when intentional:
matrix.allow_unbounded = True
configs = matrix.resolve()
```

---

## 5. Probing Configurations Across Targets

HeaderKit's `probe_configuration()` function compiles each matrix coordinate using target compiler flags (`--target=...`, `-DFEATURE=1`, `-UFEATURE`):

```python
from headerkit.matrix import ConfigCondition, ProbeOptions, probe_configuration

cond = ConfigCondition({"target": "aarch64-unknown-linux-gnu", "ENABLE_SSL": True})
options = ProbeOptions(allow_missing_sysroot=True)

# Probe cross-target header without requiring full foreign sysroots locally
unit = probe_configuration("include/network.h", cond, options=options)
```

When probing cross-compilation targets on a host without foreign sysroot headers (e.g. MSVC headers on macOS), HeaderKit emits a helpful `SysrootWarning` diagnostic and falls back to intrinsic headers rather than crashing.
