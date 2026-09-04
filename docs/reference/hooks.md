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
