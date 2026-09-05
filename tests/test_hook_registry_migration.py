from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headerkit.backends import get_backend, list_backends
from headerkit.hooks import HookDispatcher, HookRegistry, PipelineContext, Priority, hook
from headerkit.ir import CType, Function, Header, InputSpec, SourceUnit
from headerkit.writers import get_writer, list_writers


class TestHookRegistryMigration:
    """Test suite for unified hook architecture and polyglot IR evolution."""

    @pytest.fixture(autouse=True)
    def clean_registry(self):
        from headerkit.backends import _ensure_backends_loaded
        from headerkit.writers import _ensure_writers_loaded

        _ensure_backends_loaded()
        _ensure_writers_loaded()
        saved = HookRegistry.snapshot()
        yield
        HookRegistry.restore(saved)

    def test_input_spec_creation_and_inference(self):
        spec_h = InputSpec.from_path("include/foo.h")
        assert spec_h.language == "c"
        assert spec_h.classification == "header"
        assert spec_h.path == "include/foo.h"

        spec_hpp = InputSpec.from_path("include/foo.hpp")
        assert spec_hpp.language == "cpp"
        assert spec_hpp.classification == "header"

        spec_c = InputSpec.from_path("src/foo.c")
        assert spec_c.language == "c"
        assert spec_c.classification == "source"

        spec_cpp = InputSpec.from_path("src/foo.cpp")
        assert spec_cpp.language == "cpp"
        assert spec_cpp.classification == "source"

        spec_nim = InputSpec.from_path("src/bridge.nim")
        assert spec_nim.language == "nim"
        assert spec_nim.classification == "source"

        spec_rs = InputSpec.from_path("src/lib.rs")
        assert spec_rs.language == "rust"
        assert spec_rs.classification == "interface"

        spec_custom = InputSpec.from_path(
            "custom.txt",
            language="zig",
            classification="source",
            content="pub fn main() void {}",
        )
        assert spec_custom.language == "zig"
        assert spec_custom.classification == "source"
        assert spec_custom.content == "pub fn main() void {}"

    def test_source_unit_ir_and_header_backward_compatibility(self):
        assert Header is SourceUnit

        unit = SourceUnit(path="my.h", language="c", classification="header")
        assert unit.path == "my.h"
        assert unit.language == "c"
        assert unit.classification == "header"
        assert str(unit) == "SourceUnit(my.h, 0 declarations)"

        # Creating through Header alias preserves behavior
        hdr = Header(path="my.h")
        assert isinstance(hdr, SourceUnit)
        assert hdr.language == "c"
        assert hdr.classification == "header"

    @pytest.mark.treesitter
    def test_backend_hook_registration_and_dispatch(self):
        backends = list_backends()
        assert "tree-sitter" in backends

        dispatcher = HookDispatcher()
        ctx = PipelineContext(backend="tree-sitter", language="c")
        result = dispatcher.first_result(
            "parse_unit",
            "int add(int a, int b);",
            "math.h",
            context=ctx,
        )

        assert isinstance(result, SourceUnit)
        assert len(result.declarations) == 1
        assert isinstance(result.declarations[0], Function)
        assert result.declarations[0].name == "add"

        backend_inst = get_backend("tree-sitter")
        assert backend_inst.name == "tree-sitter"

    def test_all_standard_writers_registered_in_hook_dispatcher(self):
        expected_writers = ["cffi", "ctypes", "cython", "nim", "cshim", "lua", "json", "prompt", "diff"]
        available = list_writers()
        for w in expected_writers:
            assert w in available, f"Writer {w} missing from list_writers"

        unit = SourceUnit(path="math.h")
        dispatcher = HookDispatcher()

        for w_name in expected_writers:
            ctx = PipelineContext(writer=w_name)
            output = dispatcher.first_result("write_output", unit, context=ctx)
            assert output is not None, f"Dispatcher returned None for writer {w_name}"
            assert isinstance(output, str), f"Expected str output for writer {w_name}"

            writer_inst = get_writer(w_name)
            assert writer_inst is not None

    def test_project_priority_overrides_standard_writer(self):
        @hook("write_output", writer="cffi", priority=Priority.PROJECT)
        def custom_cffi_writer(unit: SourceUnit, context: PipelineContext) -> str:
            _ = context
            return f"/* CUSTOM OVERRIDE FOR {unit.path} */"

        dispatcher = HookDispatcher()
        ctx = PipelineContext(writer="cffi")
        unit = SourceUnit(path="test.h")
        output = dispatcher.first_result("write_output", unit, context=ctx)

        assert output == "/* CUSTOM OVERRIDE FOR test.h */"

    def test_project_priority_overrides_standard_backend(self):
        @hook("parse_unit", backend="tree-sitter", priority=Priority.PROJECT)
        def custom_parser(code: str, filename: str, context: PipelineContext, **kwargs) -> SourceUnit:
            _ = (code, context, kwargs)
            return SourceUnit(
                path=filename,
                declarations=[Function(name="mock_override", return_type=CType("void"), parameters=[])],
            )

        dispatcher = HookDispatcher()
        ctx = PipelineContext(backend="tree-sitter", language="c")
        unit = dispatcher.first_result("parse_unit", "int dummy();", "test.h", context=ctx)

        assert isinstance(unit, SourceUnit)
        assert len(unit.declarations) == 1
        assert unit.declarations[0].name == "mock_override"

    def test_lower_priority_fallback_does_not_override_standard_writer(self):
        from headerkit.writers import _ensure_writers_loaded

        _ensure_writers_loaded()

        @hook("write_output", writer="json", priority=Priority.FALLBACK)
        def fallback_writer(unit: SourceUnit, context: PipelineContext) -> str:
            _ = (unit, context)
            return "/* SHOULD NOT RUN */"

        dispatcher = HookDispatcher()
        ctx = PipelineContext(writer="json")
        unit = SourceUnit(path="test.h")
        output = dispatcher.first_result("write_output", unit, context=ctx)

        assert output != "/* SHOULD NOT RUN */"
        assert output is not None

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown backend: 'nonexistent'"):
            get_backend("nonexistent")

    def test_unknown_writer_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown writer: 'nonexistent'"):
            get_writer("nonexistent")

    def test_project_priority_overrides_generate_output(self, tmp_path: Path):
        from headerkit._generate import generate

        @hook("write_output", writer="ctypes", priority=Priority.PROJECT)
        def custom_output(unit: SourceUnit, context: PipelineContext, **kwargs: Any) -> str:
            _ = (context, kwargs)
            return f"# CUSTOM HOOK GENERATED {unit.path}"

        header_file = tmp_path / "sample.h"
        header_file.write_text("int foo(void);")
        (tmp_path / ".git").mkdir()

        out = generate(
            header_path=header_file,
            writer_name="ctypes",
            no_cache=True,
        )
        assert f"# CUSTOM HOOK GENERATED {header_file}" in out

    def test_scaffold_runs_transform_unit_hook(self):
        from headerkit.scaffold import ScaffoldOptions, scaffold

        @hook("transform_unit", priority=Priority.PROJECT)
        def add_custom_decl(unit: SourceUnit, context: PipelineContext) -> SourceUnit:
            _ = context
            return SourceUnit(
                path=unit.path,
                declarations=list(unit.declarations)
                + [Function(name="scaffold_injected_func", return_type=CType("void"), parameters=[])],
            )

        unit = SourceUnit(path="test.h", declarations=[])
        opts = ScaffoldOptions(package_name="testpkg", target_language="ctypes", layout="package")
        layout = scaffold(unit, opts)
        tripwire = layout.get_file("tests/test_tripwire.py")
        assert tripwire is not None
        assert "scaffold_injected_func" in tripwire.content
