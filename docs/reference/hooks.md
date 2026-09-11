# Hooks Pipeline

Headerkit provides a unified, priority-ordered hook pipeline (`headerkit.hooks`) for intercepting and customizing parsing, IR transformation, and output generation.

## Priority Tiers

Hooks execute according to integer priority tiers defined in [`Priority`][headerkit.hooks.Priority]. Higher numeric values execute first:

| Tier | Value | Intended Usage |
|---|---|---|
| `Priority.FALLBACK` | `10` | Default fallback handlers (e.g. Tree-sitter fallback when system libclang is unavailable) |
| `Priority.STANDARD` | `50` | Built-in backends and writers |
| `Priority.PROJECT` | `100` | Local repository customizations and `pyproject.toml` extensions |
| `Priority.OVERRIDE` | `1000` | Explicit hard overrides that take absolute precedence |

## Execution Modes

The [`HookDispatcher`][headerkit.hooks.HookDispatcher] supports two execution modes:

- **First-Result Dispatch** (`first_result`): Queries matching candidate hooks sorted from highest priority to lowest. The first hook to return a non-`None` value wins. If a hook returns `None`, the dispatcher cascades to the next candidate.
- **Waterfall Pipeline** (`waterfall`): Passes an initial value sequentially through all matching hooks in priority order, threading the transformed output through each stage.

## Pattern Matching with Globs

Hooks can filter invocation contexts by specifying attribute matchers:

```python
from headerkit.hooks import hook, Priority, PipelineContext
from headerkit.ir import SourceUnit

@hook("parse_unit", backend="tree-sitter", priority=Priority.STANDARD)
def custom_parser(code: str, filename: str, context: PipelineContext) -> SourceUnit | None:
    ...

@hook("write_output", writer="ctypes", priority=Priority.STANDARD)
def write_ctypes(unit: SourceUnit, context: PipelineContext) -> str:
    ...

@hook("write_output", writer="*", target="*windows*", priority=Priority.PROJECT)
def windows_override(unit: SourceUnit, context: PipelineContext) -> str:
    ...
```

## Backend and Writer Unification

All parser backends and output writers register into the unified hook pipeline:
- Backends register at `parse_unit` and `get_backend`.
- Writers register at `write_output` and `get_writer`.
- Calling `get_backend()` and `get_writer()` queries the highest-priority matching hook.
- Custom plugins can override built-in backends or writers by registering hooks at `Priority.PROJECT` (100) or `Priority.OVERRIDE` (1000).

## 3-Stage Pipeline: Ingestion to Output

The pipeline executes in three stages:
1. **`parse_unit`** (`first_result`): Parses raw source into a [`SourceUnit`][headerkit.ir.SourceUnit] Intermediate Representation.
2. **`transform_unit`** (`waterfall`): Passes the `SourceUnit` through sequential AST transformations (such as runtime lifecycle injections, macro expansion, or dialect conversions).
3. **`write_output`** (`first_result`): Generates code for the requested writer target.

[`execute_pipeline`][headerkit.hooks.execute_pipeline] automates this three-stage flow:

```python
from headerkit.hooks import execute_pipeline, PipelineContext
from headerkit.ir import InputSpec

spec = InputSpec.from_path("api.h", content="int compute(int x);")
ctx = PipelineContext(backend="tree-sitter", writer="json", runtime="nim")
unit, output = execute_pipeline(spec, context=ctx)
```

## Symbol Renaming: `rename_symbol` and `resolve_collision`

Two hook points govern the identifiers a writer emits.

**`rename_symbol`** (`waterfall`) transforms one symbol's name:

```python
def renamer(name: str, *, context: PipelineContext, kind: str) -> str: ...
```

`kind` is one of `function`, `struct`, `union`, `enum`, `enumerator`, `field`,
`typedef`, `param`, `macro`. It is what makes a case rule expressible at all:
Nim spells types in PascalCase and procs in camelCase, which is one rule per
kind and not one rule for the module.

A waterfall runs **highest priority first**, and that ordering is the design.
A project renamer registers at `Priority.PROJECT`; a writer registers its
language's grammar rules at `Priority.FALLBACK` and therefore runs **last**.
Whatever a project renames a symbol to must still survive the target language's
lexer, and no configuration can bypass that floor. The Nim writer registers
[`nim_legality_renamer`][headerkit.writers.nim.nim_legality_renamer] exactly the
way it registers `write_output`.

**`resolve_collision`** (`first_result`) decides what to do when two source
symbols collapse onto one identifier:

```python
def resolver(
    collided: tuple[Symbol, ...],
    target: str,
    *,
    context: PipelineContext,
) -> dict[Symbol, str] | None: ...
```

Return an identifier for **every** member of `collided`, or `None` to decline.
The default is to **raise**, naming both source symbols. An automatic
disambiguator -- a counter suffix, first-wins, longest-wins -- is the writer
deciding which symbol the caller meant, and it fails silently: the caller gets
bindings that compile and call the wrong function.

The returned mapping is re-checked rather than trusted. A partial mapping is an
error, every returned identifier is re-validated against the language grammar,
and the results are re-tested for collisions under the target language's own
identity function. A resolver that answers a collision with another collision
fails loudly.

Renaming is not injective, and neither is the C-to-Nim spelling: `__sig` and
`_sig` collapse under underscore repair, and to Nim `fooBar` and `foo_bar` are
one identifier whatever the header called them
([`nim_ident_identity`][headerkit.writers.nim.nim_ident_identity] is the
function that says so). Equality on the emitted string is the wrong test.

### Known limitation: collision checking is module-scope

Collision checking covers the flat module-level namespace -- functions,
structs, unions, enums, enumerators, typedefs and macros. It does **not** yet
cover two fields of one record, or two parameters of one proc.

That is a gap, not a safe exclusion. Nim rejects a colliding field or parameter
exactly as it rejects a colliding module-level symbol: an object declaring both
`fooBar` and `foo_bar` is `attempt to redefine: 'foo_bar'`, and so is a proc
taking both as parameters. So a header whose collision sits at field or
parameter level still produces a module its compiler refuses -- you get the
error from Nim rather than from headerkit, without the source symbol names.

Do not read a module-level refusal as evidence that the writer checks
collisions generally. Detecting field- and parameter-level collisions needs the
per-record and per-proc symbol identity that the IR contract work introduces.
Both cases are pinned in `tests/test_rename.py` as strict xfails, so they turn
red on the day that lands.

### Declarative configuration

`[rename]` in `.headerkit.toml` covers the common cases without code:

```toml
[rename]
collision_policy = "error"          # the default; see below for the others

[[rename.rules]]
kinds = ["function"]                # omit to apply to every kind
strip_prefix = "juce_"
case = "camel"                      # snake | camel | pascal | preserve
collapse_underscores = true
add_prefix = ""
add_suffix = ""
```

Steps within one rule apply in a fixed order: `strip_prefix`, `case`,
`collapse_underscores`, `add_prefix`, `add_suffix`. Rules apply in declared
order. A rename that needs the symbol's type, its header, or a lookup table is
not expressible here -- that is what the Python hook is for.

`collision_policy` is one of `error` (default), `suffix_header_stem`,
`prefer_shortest`, `prefer_longest`, `prefer_first_declared`. Every one but
`error` is opt-in, each is deterministic given the same input set, and each is
re-checked like any other resolver. `suffix_header_stem` separates symbols that
came from different headers and cannot separate two declarations in one header:
appending the same stem twice resolves nothing, and the re-check says so rather
than emitting a module the compiler rejects.

### Renaming and the cache

Renaming changes generated output, so the registered hooks enter the **output
cache key** via [`rename_cache_fingerprint`][headerkit.rename_cache_fingerprint].
A hook built from declarative config carries its config's digest, so two
different rename configs cannot share a key. A hook written in Python is
identified by module and qualified name: editing that function's body does not
move the key, the same limitation writer plugins have with `cache_version`.

## API Reference

::: headerkit.hooks.Priority
    options:
      show_source: false

::: headerkit.hooks.PipelineContext
    options:
      show_source: false

::: headerkit.hooks.HookRegistry
    options:
      show_source: false

::: headerkit.hooks.hook
    options:
      show_source: false

::: headerkit.hooks.HookDispatcher
    options:
      show_source: false

::: headerkit.hooks.HookCaller
    options:
      show_source: false

::: headerkit.hooks.execute_pipeline
    options:
      show_source: false

::: headerkit.Symbol
    options:
      show_source: false

::: headerkit.RenameConfig
    options:
      show_source: false

::: headerkit.RenameRule
    options:
      show_source: false

::: headerkit.enforce_injectivity
    options:
      show_source: false

::: headerkit.rename_cache_fingerprint
    options:
      show_source: false
