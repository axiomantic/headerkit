# Agents

Instructions for AI coding agents working on this project.

## Changelog

Keep `CHANGELOG.md` up to date using [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Every user-facing change should have a changelog entry.

## Versioning

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Bump the version in `pyproject.toml` whenever creating a branch:

- **Major** (X.0.0): Breaking changes to public API
- **Minor** (0.X.0): New features, new public API surface
- **Patch** (0.0.X): Bug fixes, documentation-only changes, internal refactors

## Vendored clang bindings

`headerkit/_clang/` contains vendored upstream clang Python bindings for LLVM 18-23. These are excluded from ruff and mypy. Do not modify, refactor, or lint them.

## Registry pattern & unified hooks

Backends and writers use the unified hook engine (`headerkit.hooks`) as their underlying registry, preserving the managed circular import pattern for self-registration. Both `headerkit/backends/__init__.py` and `headerkit/writers/__init__.py` lazily import their concrete modules via `_ensure_*_loaded()`, and each concrete module self-registers via hooks and `register_backend`/`register_writer` at the bottom of the file. Do not restructure these imports.

When adding a new backend or writer, follow the pattern: define the class, register hooks on `parse_unit`/`get_backend` or `write_output`/`get_writer`, and call `register_*()` at the bottom of the module file.

## Strict prohibition against regex-based AST extraction (CANNOT REGEX FOR CONTEXT-FREE GRAMMARS)

**NEVER use regular expressions to parse, tokenize, or extract Abstract Syntax Tree (AST) structures from source code in context-free or structured programming languages (C, C++, Rust, Zig, Nim, etc.).**

Context-free grammars cannot be parsed by regular languages. Attempting to extract function declarations, type definitions, structs, interfaces, or language scopes with regex is fundamentally flawed and strictly forbidden:
- It fails on nested braces, generic type parameters, closures, macro invocations, block comments, multiline attributes, and string literals.
- It produces fragile green mirages—tests that pass on trivial happy-path snippets while failing on any non-trivial real-world syntax.

All parser backends and AST extractors in HeaderKit **MUST** use formal parser grammars or compiler toolchains:
- **Tree-sitter grammars** (e.g. `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-rust`, `tree-sitter-zig`) traversing concrete syntax trees.
- **Compiler frontend ASTs** (e.g. LLVM `libclang`).
- **Formal compiler / AST bindings** provided by the language ecosystem.

Any code introducing regex-based AST extraction, source scanning, or signature scraping will be rejected immediately.

## Architectural coherence & anti-islanding (Phase 0 scope gate)

Before writing code or tests for any task, perform an explicit Phase 0 scope audit:

- **Zero-Dual-System Rule**: When introducing a new engine, pipeline, or abstraction, existing built-in components must immediately adopt it in the foundational PR. Never build a new subsystem as an isolated island alongside the legacy mechanism it was designed to replace.
- **Foundational Noun Priority**: When core IR containers, domain models, or input classifications evolve (e.g. `Header` $\rightarrow$ `SourceUnit`, `InputSpec`), establish the new nouns in the base PR so new features are never built on deprecated models.
- **Consumer Trace**: Verify that standard callers (`get_backend()`, `get_writer()`, CLI) transparently flow through the new architecture rather than requiring special-case entry points.

## Public API

`headerkit/__init__.py` defines `__all__` with the full public surface. Backends and writers are accessed via registry functions (`get_backend()`, `get_writer()`), not by importing concrete classes directly. When adding new public symbols, add them to both the imports and `__all__` in `__init__.py`.

## Quality gates

All code must pass before committing:

- `ruff check .` and `ruff format --check .`
- `mypy --strict` on `headerkit/`
- `pytest` across Python 3.10-3.14 on Linux, macOS, and Windows

Pre-commit hooks enforce these automatically.

## Testing

Registry tests (`test_backends/test_registry.py`, `test_writers/test_registry.py`) must save and restore global registry state via fixtures. Each test should see a clean registry to prevent test pollution.

Tests requiring a system libclang installation use the `@pytest.mark.libclang` marker.

## Multi-line string literals

Use `textwrap.dedent` with a triple-quoted string instead of concatenating fragments with `+` and `"\n"`. Use an `f`-string prefix for interpolation. The backslash after the opening `"""` suppresses the leading newline.

```python
# Preferred
import textwrap

assert output == textwrap.dedent(f"""\
    {_PREAMBLE}
    # ========================
    # Typedefs
    # ========================

    callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)
""")

# Avoid
assert output == (
    _PREAMBLE
    + "\n"
    + "# ========================\n"
    + "# Typedefs\n"
    + "# ========================\n"
    + "\n"
    + "callback_fn = ctypes.CFUNCTYPE(None, ctypes.c_int)\n"
)
```

This applies anywhere a multi-line string literal appears: assertions, expected-value variables, template strings, error messages.

## Runtime dependencies

The project has one conditional runtime dependency: `tomli` for Python 3.10 (before `tomllib` was added to stdlib). All other functionality is zero-dependency. If a feature needs an external package, make it an optional dependency with graceful degradation when absent.
