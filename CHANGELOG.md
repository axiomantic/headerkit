# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-02-28

### Added

- PyPy support: compatibility shim for `c_interop_string` that avoids `c_char_p` subclassing
- End-to-end integration tests for JSON writer pipeline (18 new roundtrip tests)
- Real-world library header tests: sqlite3, zlib, lua, libcurl, SDL2, CPython (21 tests)
- CI caching for downloaded test headers
- `download` pytest marker for tests requiring network access
- Unit tests for PyPy compatibility monkey-patch (20 tests)

### Changed

- Renamed package from `clangir` to `headerkit` (`pip install headerkit`)
- Console script renamed from `clangir-install-libclang` to `headerkit-install-libclang`

## [0.3.3] - 2026-02-28

### Added

- CI workflow to test `headerkit-install-libclang` across Linux, macOS, and Windows

## [0.3.2] - 2026-02-28

### Added

- `headerkit-install-libclang` CLI tool for automated platform-specific libclang installation
- Console script entry point (`headerkit-install-libclang`) in pyproject.toml
- Documentation guide and API reference for the install tool
- Support for Linux (dnf, apt-get, apk), macOS (Homebrew), Windows x64 (Chocolatey), and Windows ARM64 (direct LLVM download)

### Fixed

- `install_libclang` verification result was ignored, now returns exit code 1 on verification failure
- Narrowed broad `except Exception` to `(ImportError, OSError, RuntimeError)` in verification

## [0.3.1] - 2026-02-28

### Added

- Mermaid diagrams in documentation: pipeline flowcharts and IR class hierarchies

### Fixed

- JSON export tutorial incorrectly listed `is_union` as a JSON output field
- Quickstart guide showed wrong pointer spacing (`char *` vs `char*`)
- `header_to_cffi` docstring converted from Google-style to Sphinx-style for mkdocstrings

## [0.3.0] - 2026-02-27

### Added

- Pluggable writer protocol (`WriterBackend`) mirroring the existing backend registry pattern
- Writer registry with `register_writer()`, `get_writer()`, `list_writers()`, `is_writer_available()`, `get_default_writer()`, `get_writer_info()`
- `CffiWriter` class wrapping `header_to_cffi()` with self-registration as default writer
- `JsonWriter` with `header_to_json()` and `header_to_json_dict()` for full IR serialization
- Public API re-exports for all writer protocol symbols in `headerkit.__init__`
- MkDocs documentation site with Material theme and mkdocstrings autodoc
- 6 API reference pages auto-generated from docstrings
- 6 guide pages: installation, quickstart, architecture, CFFI usage, custom backends, custom writers
- 4 tutorial pages: PXD writer, ctypes writer, JSON export, C header cleanup
- Versioned documentation via mike with version selector dropdown
- GitHub Pages deployment workflow triggered on tagged releases
- `docs` optional dependency group in pyproject.toml

## [0.2.0] - 2026-02-27

### Added

- Windows platform support: LLVM version detection via registry and Program Files scan
- Windows system header detection (`_get_windows_system_headers()`)
- Windows DLL search paths for libclang loading
- Python 3.14 support
- Weekly `check-python.yml` workflow for Python pre-release compatibility
- Full Windows CI in test matrix (ubuntu, macos, windows x Python 3.10-3.14)

### Fixed

- Three test failures on Windows CI (path separators, platform-specific mocks)

## [0.1.0] - 2026-02-26

### Added

- IR data model: `Header`, `Function`, `Struct`, `Enum`, `Typedef`, `Variable`, `Constant`, and type expressions (`CType`, `Pointer`, `Array`, `FunctionPointer`)
- Pluggable backend registry with `ParserBackend` protocol, `register_backend()`, `get_backend()`, `list_backends()`
- Libclang backend extracted from autopxd2 with LLVM 18-21 support
- CFFI cdef writer (`header_to_cffi()`) extracted from pynng
- Vendored clang Python bindings (`cindex.py`) for LLVM 18, 19, 20, 21
- LLVM version auto-detection: env var, llvm-config, pkg-config, clang preprocessor, `/usr/lib/llvm-N/`, Homebrew
- Public API re-exports in `headerkit.__init__`
- CI/CD: GitHub Actions test matrix, lint (ruff + mypy), release workflow with PyPI trusted publishing
- Pre-commit hooks for ruff, mypy, and standard checks
- LLVM license compliance for vendored bindings

[0.4.0]: https://github.com/axiomantic/headerkit/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/axiomantic/headerkit/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/axiomantic/headerkit/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/axiomantic/headerkit/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/axiomantic/headerkit/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/axiomantic/headerkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/axiomantic/headerkit/releases/tag/v0.1.0
