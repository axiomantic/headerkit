# Roadmap

This document outlines the planned direction, active initiatives, architectural evolutions, and future horizons for **Headerkit**.

> **Status & Priorities**: Priorities are reviewed regularly and evolve based on community feedback, LLVM/clang upstream developments, and ecosystem needs.

---

## Project Philosophy: "The Devil is in the Details"

Raw code generation is only half the battle. Binding tools often fail users not on syntax translation, but at the seams—runtime lifecycle quirks, packaging discrepancies, ABI edge cases, and missing glue code.

Headerkit prioritizes **complete, working projects** that "just work" out of the box:
- **End-to-End Ergonomics**: Don't just emit isolated binding fragments; generate turnkey modules and packages with all the scaffolding required to build, test, import, package, and publish.
- **Runtime-Aware Lifecycle**: Automatically handle runtime-specific requirements (e.g. initialization hooks like `NimMain()`, thread-attaching hooks, and exception safety across foreign function boundaries).
- **Zero-Footgun Memory Management & Concurrency**:
  - Dual-runtime boundaries are rife with leaks, premature deallocations, and race conditions.
  - Mitigate conflicts between Python's memory manager (reference counting, cyclic GC, and PEP 703 free-threaded / no-GIL Python 3.13+) and foreign memory models (such as Nim's `--mm:orc` / ARC).
  - Explicit ownership semantics: generated wrappers must define clear destruction hooks (Python finalizers / capsules) and foreign thread registration (`setupForeignThreadGc()`) so threads spawned across Python and foreign runtimes never corrupt memory or race.
- **Batteries-Included Packaging**: Provide first-class build hooks, wheel packaging configurations (PEP 517, `scikit-build-core`, `cibuildwheel`), and compiler flag handling so downstream consumers do not have to become build-system experts.

---

## Engineering Discipline & Execution Ceremony

To uphold rigorous quality standards and keep the codebase pristine across all initiatives, every task follows an explicit ceremony from design to completion:

### 1. Phased Ceremony for Each Task
0. **Holistic Scope & Architectural Coherence Gate (Pre-Implementation)**:
   - **Subsystem Islanding & Subsumption Audit**:
     - Does this work introduce a new engine, pipeline, registry, or abstraction?
     - If yes: Does the legacy mechanism still exist alongside it?
     - *Zero-Dual-System Rule*: New engines must immediately subsume existing built-ins. Existing components must "eat the dogfood" in the foundational PR—never defer migration to a follow-up if that leaves dual registries or duplicate architectural paths in the codebase.
   - **Core Noun & Domain Model Invariant Audit**:
     - Are core IR containers, domain models, or protocols evolving (e.g. `Header` $\rightarrow$ `SourceUnit`, `InputSpec`)?
     - If yes: Are new components built on the *new* nouns or the *old* nouns? Foundational nouns must be established in the base PR so subsequent work is never built on deprecated models that immediately need refactoring.
   - **End-to-End Consumer Invariant Trace**:
     - Trace the caller's path from public entry points (`get_backend()`, `get_writer()`, CLI `headerkit`). Does the existing public API transparently flow through the new architecture, or is the new capability only reachable via an obscure new entry point?
   - **The Scope Triad (Written Checkpoint)**:
     - Explicitly define:
       - `[Added]`: New capabilities, classes, and modules.
       - `[Subsumed/Migrated]`: Existing modules rewritten to use the new paradigm.
       - `[Aliased/Bridged]`: Backward-compatibility shims preserving public API stability.
   - *Checkpoint*: Verify Phase 0 alignment before writing functional code or test suites.
1. **Design & Scope Alignment**:
   - Explicitly define interfaces, types, signatures, and failure modes before writing functional code.
   - Clarify edge cases, fallback behaviors, and cross-platform implications (Linux, macOS, Windows).
   - *Checkpoint*: Verify alignment against roadmap pillars before touching code. Do not proceed with ambiguous requirements.
2. **Test-Driven Development (TDD)**:
   - **Red**: Write focused, failing tests first that assert expected behaviors and edge cases. Verify that tests fail for the intended reason (not import or syntax typos).
   - *Checkpoint (Red)*: Confirm failure output specifically matches the missing capability.
   - **Green**: Write the minimal, clean implementation to make tests pass.
   - *Checkpoint (Green)*: Confirm all target tests pass without modifying assertions to fit broken code.
   - **Refactor**: Clean up the implementation while keeping tests strictly green.
   - *Checkpoint (Refactor)*: Re-run full test suite to guarantee zero regressions.
3. **Quality Gates & Verification**:
   - `ruff check .` and `ruff format --check .`
   - `mypy --strict` on `headerkit/` (no untyped code, no unannotated helpers).
   - `pytest` across relevant test tiers.
   - **Green Mirage Audit (Anti-Tautology & Escape Analysis)**:
     - Audit every new test to verify it cannot pass vacuously or report green on broken code.
     - *Consumption Validates*: Assertions must consume and validate concrete structures, not just check boolean truthiness, non-emptiness, or exit status 0.
     - *Negative Controls & Mutation Check*: Verify tests fail when intentional bugs are introduced (e.g. inverted priority, mismatched globs, broken parsing).
     - *No Blind Mocks / Tautological Asserts*: Never mock away the logic under test.
   - Fact-check implementation against documentation claims and architectural specs.
   - *Checkpoint*: All gates must pass with exit code 0. No suppressing warnings or skipping checks.
4. **Documentation Audit, Remediation & Examples (Mandatory Quality Gate)**:
   - **Proactive Documentation Audit & Remediation**:
     - After implementing any behavior or feature, conduct an explicit audit of existing docs for drift.
     - Remediate and update all affected guides, overview tables (e.g. available backends in `docs/reference/backends.md`), and tutorials so documentation never lags behind code.
   - **Zero Undocumented Features**: Every new module, backend, writer, hook, CLI flag, or public symbol must have dedicated reference documentation in `docs/` and be linked in `mkdocs.yml` before completion.
   - **Executable Working Examples**: Provide real, executable examples (in `examples/` or docstrings) demonstrating the end-to-end user workflow, not just unit fragments. Run and verify them.
   - **Strict Build Verification**: Execute `mkdocs build --strict` to verify zero broken links, no missing cross-references, and error-free markdown formatting.
   - *Checkpoint*: Documentation and examples must be written, audited for drift, remediated, and build cleanly with `--strict`. No task can proceed to review without passing this checkpoint.
5. **Structured Review & Verification (Zero Completion Bias)**:
   - **Anti-Completion Bias Audit**: Actively inspect the change with adversarial skepticism. Resist declaring victory prematurely just because a test passed. Check:
     - Did we fulfill the full requirement or just a convenient subset?
     - Are error paths and edge cases actually handled or swept under the rug?
     - Are any temporary hacks or debugging remnants left behind?
     - Are docs complete, accurate, and matching the exact implementation?
   - Check against regressions, test pollution (ensure global registries/hooks are isolated via fixtures), and leakages.
   - Keep `CHANGELOG.md` up to date with user-facing changes.
   - *Final Checkpoint*: Verify all ceremony gates from steps 1–5 are fully satisfied before marking the task complete.

### 2. Comment & Docstring Discipline
- **No Extraneous or Obvious Comments**:
  - Code should be self-documenting through precise naming, type annotations, and clean structure.
  - Never add comments that restate the code (e.g. `# loop over items`, `# set variable to true`).
  - Comments must only explain non-obvious rationale, subtle hardware/ABI quirks, or algorithmic constraints.
- **No Work Tracking Metadata in Code**:
  - **Zero references** to task IDs, issue numbers, ticket tracking numbers, sprint names, or roadmap work-item indices in code comments, docstrings, or test names. The codebase must read as a timeless, professional open-source project.
- **Docstrings as Contracts, Not Diary Entries**:
  - Docstrings must strictly describe the contract: purpose, parameters, return types, exceptions raised, and reproducible usage examples.
  - No transient notes or historical narratives in docstrings.

---

## Strategic Pillars

1. **Polyglot Interface Extraction**: Expanding beyond C/C++ headers to general source code units and interfaces (Rust, Zig, Nim, C/C++ source) using normalized IR.
2. **Unified Hook-Driven Architecture**: Transitioning from separate backend and writer registries to a single, priority-ordered hook pipeline with glob-matching capabilities.
3. **Zero-Overhead Packaging & Build Pipelines**: Seamless integration into standard Python packaging tools (PEP 517, scikit-build-core, cibuildwheel) to eliminate manual binding compilation friction.
4. **Robust C++ Surface Extraction**: Expanding `cshim` and AST transformations to demangle, flatten, and adapt complex C++20/23 constructs into standard C ABI targets.

---

## Horizons

### 🟢 Now (Active Focus)

- **CShim C++ Flattening Polish**:
  - Broaden standard library container mappings (`std::string`, `std::vector`) across shims.
  - Expand operator mapping and inheritance hierarchy flattening.
- **Enhanced Platform & Compiler Triple Detection**:
  - Target triple normalization improvements for cross-compilation environments.

---

### 🟡 Next (Planned Initiatives)

#### 1. Unified Hook-Based Pipeline Architecture
- **Concept**: Refactor Headerkit so that the plugin and backend systems *are* the hook system, rather than stacking hooks on top of rigid legacy registries.
- **Mechanism**:
  - **Single Pipeline Lifecycle**:
    ```
    Source Input (path / code)
        │
        ▼
    [hook: parse_unit]            <-- libclang, tree-sitter, custom parsers
        │
        ▼
    [hook: transform_unit]        <-- IR cleanup, macro expansion, typedef inference
        │
        ▼
    [hook: write_output]          <-- ctypes, cffi, cython, nim, mojo, etc.
        │
        ▼
    Output Code / File
    ```
  - **Importable Priority Constants**: Explicit precedence levels to guarantee deterministic execution without relying on import order:
    - `Priority.FALLBACK = 10`: Default fallbacks (e.g. tree-sitter fallback when libclang is missing).
    - `Priority.STANDARD = 50`: Built-in backends and writers (default).
    - `Priority.PROJECT = 100`: User, local repo, or `pyproject.toml` extensions.
    - `Priority.OVERRIDE = 1000`: Explicit hard overrides that take absolute precedence.
  - **Execution Semantics**:
    - *First-Result Dispatch* (`parse_unit`, `write_output`): The pipeline queries matching hooks from highest to lowest priority. The first hook to return non-`None` wins; returning `None` cascades to the next candidate.
    - *Waterfall Pipeline* (`transform_unit`): Each hook receives the unit and returns a modified unit passed to the next hook.
  - **Glob Pattern Matching**:
    - Match on backend names (`backend="tree-sitter*"`), target triples (`target="*windows*"`), writer names (`writer="ctypes*"`), and languages (`language="c*"`).

#### 2. Polyglot Input & Classification System (Core IR Renaming)
- **Concept**: Headerkit is not limited to C/C++ headers. It can extract interface surfaces, declarations, and metadata from any language supported by AST or Tree-sitter parsers.
- **Core IR Evolution (`Header` $\rightarrow$ `SourceUnit` / `InterfaceUnit`)**:
  - Direct rename of the core `Header` container class to `SourceUnit` (or `InterfaceUnit`) across the core IR for the next major release, clarifying that it represents any compilation unit, interface, or source file.
- **Input Classification**:
  - An input is tagged with both a **language** (`c`, `cpp`, `rust`, `zig`, `nim`) and a **classification** (`header`, `source`, `interface`, `idl`).
  - Example: `c:header` (`.h`), `c:source` (`.c`), `cpp:header` (`.hpp`), `rust:interface` (`.rs` with `extern "C"`).
- **Zero-Overhead Capability Discovery (No Premature Installs)**:
  - Backends separate **cheap static declarations** (`supported_languages`, `supported_classifications`) from **dynamic availability probes** (`is_available()`).
  - Probing for a language like `rust` immediately matches `TreeSitterBackend` without ever touching `libclang`, eliminating spurious warnings, disk searches, or auto-install prompts.

#### 3. Tree-sitter Parser Backend (`headerkit.backends.treesitter`)
- **Concept**: Provide a lightweight, zero-system-dependency parser backend for C headers and polyglot sources that works out of the box without requiring LLVM or `libclang` installed on the host.
- **User Experience (Zero libclang requirement)**:
  - Users who select the Tree-sitter backend (`--backend tree-sitter`) or install the optional extra (`pip install "headerkit[treesitter]"`) require **no system LLVM, no Xcode command line tools, and no shared library discovery** (`libclang.so`/`.dylib`/`.dll`).
  - Enables Headerkit to run seamlessly in stripped-down CI containers, Docker alpine images, PyPy, or locked-down environments where installing system LLVM packages is prohibited or difficult.
  - Automatic fallback: In the unified hook pipeline, if `libclang` is absent and the user has not forced `--backend libclang`, the pipeline cascades down to Tree-sitter at `Priority.FALLBACK`.
- **Mechanism**:
  - Implement `parse_unit` using `tree-sitter` and language grammars (`tree-sitter-c`, etc., distributed as self-contained precompiled PyPI wheels).
  - Map concrete syntax tree nodes into normalized Headerkit IR (`Struct`, `Function`, `Typedef`, `Enum`, `CType`).
  - Lightweight preprocessor handling for macro and `#define` constant extraction where feasible.

#### 4. Nim $\rightarrow$ C Header $\rightarrow$ Python Bridge
- **Concept**: Enable writing high-performance modules in Nim and consuming them natively in Python with zero hand-written FFI boilerplate.
- **Mechanism**:
  - Ingest C headers generated by Nim (`nim c --app:lib --header:mylib.h ...`).
  - Generate clean Python `ctypes` or `cffi` wrappers.
  - **Runtime & Memory Lifecycle**:
    - Automatically manage the Nim runtime lifecycle by emitting safe initialization hooks (`NimMain()` called idempotently on module load).
    - Align with Nim's `--mm:orc` (deterministic ARC + cycle collector) so Python object lifecycles can tie cleanly into Nim destructors via wrapper finalizers (`__del__` / capsule destructors) without GC deadlock.
    - Safe cross-thread invocation: emit `setupForeignThreadGc()` / `tearDownForeignThreadGc()` guards for calls originating from Python threads, ensuring compatibility with multi-threaded runtimes and Python 3.13+ free-threading (PEP 703).

#### 5. Scikit-build / Wheel Packaging Template
- **Concept**: Provide end-to-end packaging infrastructure for compiling Nim-based Python extensions into distributable binary wheels.
- **Mechanism**:
  - Build-backend helper / template (leveraging `scikit-build-core` or standard PEP 517 hooks).
  - Automate calling `nim c`, linking necessary runtime libraries, exporting C symbols, and tagging platform wheels correctly across Linux, macOS, and Windows.

#### 6. Mojo C++ Interoperability Bridge (CShim + Mojo FFI)
- **Concept**: Open C++ ecosystems to Modular's Mojo without requiring manual C wrapper maintenance.
- **Mechanism**:
  - Mojo natively supports standard C calling conventions (`sys.ffi.DLHandle`), but cannot directly bind complex C++ classes, templates, or mangled symbols.
  - Leverage Headerkit's `cshim` writer to generate an `extern "C"` flat ABI shim for C++ headers.
  - Concurrently emit corresponding Mojo struct definitions and `sys.ffi` wrapper calls to consume the shimmed library cleanly in Mojo code.

#### 7. Comprehensive Documentation Sweep & Verification
- **Concept**: With foundational architectural changes (hooks, polyglot inputs, IR renaming), ensure that documentation and real-world examples never lag behind implementation.
- **Plan**:
  - Author dedicated documentation guides for the unified hook system, custom hook registration, priority ordering, and glob matching.
  - Full documentation sweep to fact-check all existing tutorials, guides, and API references against new behaviors.
  - Update all example projects and tests to adopt the new `SourceUnit` conventions.

---

### 🔵 Later (Exploratory / Research)

- **Two-Way Python Acceleration (Mojo $\leftrightarrow$ Python)**:
  - Use Headerkit to generate the interface layer between Python and Mojo-compiled shared objects (`.so` / `.dylib` / `.dll`), creating seamless ctypes/cffi wrappers for fast Mojo acceleration in existing Python codebases.
- **Native Mojo Writer Backend (`headerkit.writers.mojo`)**:
  - Ingest pure C headers directly and output idiomatic Mojo FFI bindings (`struct`, `fn`, `UnsafePointer`).
- **`nimpy` Direct Wrapper Generation**:
  - Generate idiomatic `nimpy` wrapper modules directly from C/C++ headers to build native `.so` / `.pyd` CPython extensions implemented in Nim.
- **Multi-Target Wheel Bundling**:
  - Headerkit CLI tools to bundle pre-parsed store headers across diverse target triples directly into release artifacts.

---

## Completed Milestones

- [x] **Core IR & Clang Parser Backend**: High-fidelity libclang parsing into standardized `Header`, `CType`, `Declaration`, and `TypeExpr` models.
- [x] **Initial Language Writers**:
  - `ctypes` writer (pure Python runtime loading).
  - `cffi` writer (`cdef` header definition output).
  - `cython` writer (`.pxd` extern declarations).
  - `luajit` writer (LuaJIT `ffi.cdef` strings).
  - `nim` writer (`{.importc.}` and `{.importcpp.}` wrappers).
  - `cshim` writer (`extern "C"` flat wrappers with opaque handles for C++).
  - `json` writer (IR serialization).
- [x] **PEP 517 Build Backend**: `headerkit.build` for automated header code generation during `pip install` / `build`.
- [x] **Cache & CI Store Generation**: Offline AST parsing and cross-platform cached header generation.
