# Project & Extension Scaffolding

HeaderKit features a unified output architecture where generating a single binding file and scaffolding a complete multi-file project are handled through the exact same pipeline:

> **Core Tenet**: *A single file output is just a project of one file.*

Rather than requiring distinct, fragmented subcommands, HeaderKit's generation engine is driven by a layout strategy (`--layout=file` vs `--layout=package` / `--layout=project`).

---

## Why Project Scaffolding?

Generating raw binding code strings is only half the battle. Downstream developers typically need:
1. **Package Metadata**: Idiomatic build files (`.nimble`, `pyproject.toml`, `mojoproject.toml`).
2. **Package Structure**: Separation between low-level generated foreign bindings (`_bindings.*`) and clean user-facing package entrypoints (`__init__.*`).
3. **Compiler / Linker Flags**: Safe memory management flags (`--mm:orc`, threading options).
4. **Failing Tripwire & Unit Tests**: Immediate TDD verification that foreign dynamic libraries can be linked and all exported C symbols resolve.

---

## Quick Start (CLI)

### 1. Generating a Single File (Minimal 1-File Layout)

By default or when targeting an output file or stdout, HeaderKit produces a standalone binding module:

```bash
headerkit include/vector.h -w nim -o vector_bindings.nim
```

### 2. Scaffolding a Full Polyglot Package

To generate a full turnkey package with build configuration and tests, specify `--layout package`:

```bash
headerkit include/vector.h \
  -w nim \
  --layout package \
  --package-name nim_vector \
  -o nim:./nim_vector \
  --no-input
```

This generates the following structure:

```text
nim_vector/
├── nim_vector.nimble          # Nimble package spec with test tasks
├── nim.cfg                    # Compiler flags (--mm:orc, --threads:on)
├── src/
│   ├── nim_vector.nim         # Public API re-export
│   └── nim_vector/
│       └── bindings.nim       # Full HeaderKit-generated Nim FFI bindings
└── tests/
    ├── test_tripwire.nim      # Failing tripwire stubs for symbol/ABI verification
    └── test_nim_vector.nim    # High-level unit test skeleton
```

### 3. TTY-Aware Interactive Wizard

When executed in an interactive terminal without explicit arguments, HeaderKit launches a terminal questionnaire:

```bash
$ headerkit include/vector.h -w mojo -o ./mojo_vector
Package name [mojo_vector]:
Target language (nim, mojo, ctypes, cffi) [mojo]:
Layout (file, package) [package]:
Test generation (both, tripwire, unit, none) [both]:
```

To bypass prompts in CI or automated scripts, pass `--no-input`.

---

## Test Generation & Tripwires

When scaffolding a package, HeaderKit generates test suites tailored to the target language via `--test-type`:

| Value | Description |
|---|---|
| `both` (Default) | Emits both failing tripwire stubs and structured unit test skeletons side-by-side. |
| `tripwire` | Generates tripwire tests verifying every C export symbol and dynamic library resolution. |
| `unit` | Generates standard assertion skeletons for verifying module interfaces. |
| `none` | Omits the `tests/` directory entirely. |

### Tripwire Verification in Python (`pytest-tripwire`)
```python
import pytest
from mypkg import _bindings

@pytest.mark.tripwire
def test_tripwire_exported_symbols():
    """Tripwire verification: asserts foreign C symbols are present in runtime bindings."""
    assert hasattr(_bindings, "vector_add"), "Missing export entrypoint vector_add"
    assert hasattr(_bindings, "vector_norm"), "Missing export entrypoint vector_norm"
```

### Tripwire Verification in Nim
```nim
import std/unittest
import mypkg

suite "Tripwire Symbol & ABI Verification":
  test "verify foreign library entrypoints exist and link":
    echo "Verifying tripwire symbol: vector_add"
    echo "Verifying tripwire symbol: vector_norm"
    checkpoint "Tripwire symbol link verification active"
```

---

## Bring-Your-Own-Scaffolder (BYOScaffolder)

HeaderKit core is 100% zero-dependency, shipping with `StdlibScaffolder` built purely on Python's standard library (`string.Template` and `pathlib`).

For corporate environments or advanced repositories needing external template engines (like [Copier](https://copier.readthedocs.io/) or [Cookiecutter](https://cookiecutter.readthedocs.io/)), HeaderKit provides the pluggable `BYOScaffolder` protocol integrated with the unified hook engine.

### Example: Copier BYOScaffolder Plugin

```python
from pathlib import Path
import tempfile
import copier
from headerkit.hooks import Priority, hook
from headerkit.ir import Header
from headerkit.scaffold import BYOScaffolder, OutputFile, ProjectLayout, ScaffoldOptions, scaffold
from headerkit.writers import get_writer

class CopierScaffolder(BYOScaffolder):
    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir

    def scaffold(self, unit: Header, options: ScaffoldOptions) -> ProjectLayout:
        writer = get_writer(options.target_language)
        bindings = writer.write(unit)

        with tempfile.TemporaryDirectory() as tmp_dir:
            copier.run_copy(
                str(self.template_dir),
                tmp_dir,
                data={
                    "package_name": options.package_name,
                    "target_language": options.target_language,
                    "bindings_code": bindings,
                },
                defaults=True,
            )
            files = [
                OutputFile(path=str(p.relative_to(tmp_dir)), content=p.read_text(encoding="utf-8"))
                for p in Path(tmp_dir).rglob("*") if p.is_file()
            ]
            return ProjectLayout(files=files)

# Register via HeaderKit hook engine
copier_plugin = CopierScaffolder(Path("./templates/custom_template"))

@hook("scaffold_project", priority=Priority.OVERRIDE)
def custom_scaffold_hook(unit: Header, options: ScaffoldOptions, **_kwargs) -> ProjectLayout:
    return copier_plugin.scaffold(unit, options)
```

See the executable example in `examples/scaffolding/copier_scaffolder.py`.
