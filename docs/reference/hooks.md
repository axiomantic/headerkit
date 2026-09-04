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

@hook("write_output", writer="ctypes", priority=Priority.STANDARD)
def write_ctypes(header, context: PipelineContext) -> str:
    ...

@hook("write_output", writer="*", target="*windows*", priority=Priority.PROJECT)
def windows_override(header, context: PipelineContext) -> str:
    ...
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
