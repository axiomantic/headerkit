from __future__ import annotations

import pytest

from headerkit.backends import get_backend
from headerkit.hooks import (
    HookDispatcher,
    HookRegistry,
    PipelineContext,
    Priority,
    execute_pipeline,
    hook,
)
from headerkit.ir import CType, Function, InputSpec, SourceUnit


class TestPolyglotPipeline:
    """Test suite for polyglot input classification, capability discovery, and transform_unit pipeline."""

    @pytest.fixture(autouse=True)
    def clean_registry(self):
        from headerkit.backends import _ensure_backends_loaded
        from headerkit.writers import _ensure_writers_loaded

        _ensure_backends_loaded()
        _ensure_writers_loaded()
        saved = HookRegistry.snapshot()
        yield
        HookRegistry.restore(saved)

    def test_pipeline_context_supports_runtime(self):
        ctx = PipelineContext(
            backend="tree-sitter",
            writer="ctypes",
            language="c",
            classification="header",
            runtime="nim",
        )
        assert ctx.runtime == "nim"
        assert ctx.language == "c"
        assert ctx.classification == "header"

    def test_backends_expose_cheap_static_capabilities(self):
        ts = get_backend("tree-sitter")
        assert "c" in ts.supported_languages
        assert "header" in ts.supported_classifications

        lc = get_backend("libclang")
        assert "c" in lc.supported_languages
        assert "cpp" in lc.supported_languages

    def test_transform_unit_waterfall_in_pipeline(self):
        @hook("transform_unit", priority=Priority.STANDARD)
        def add_lifecycle_decl(unit: SourceUnit, context: PipelineContext) -> SourceUnit:
            if context.runtime == "nim":
                new_decls = list(unit.declarations)
                new_decls.append(Function(name="NimMain", return_type=CType("void"), parameters=[]))
                return SourceUnit(
                    path=unit.path,
                    declarations=new_decls,
                    language=unit.language,
                    classification=unit.classification,
                )
            return unit

        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="c", runtime="nim")
        unit = SourceUnit(
            path="fastmath.h",
            declarations=[Function(name="add", return_type=CType("int"), parameters=[])],
        )

        transformed = dispatcher.waterfall("transform_unit", unit, context=ctx)
        names = [d.name for d in transformed.declarations if isinstance(d, Function)]
        assert names == ["add", "NimMain"]

    def test_execute_pipeline_end_to_end(self):
        spec = InputSpec.from_path(
            "math.h",
            content="int multiply(int a, int b);",
        )
        ctx = PipelineContext(
            backend="tree-sitter",
            writer="json",
            language=spec.language,
            classification=spec.classification,
        )

        unit, output = execute_pipeline(spec, context=ctx)
        assert isinstance(unit, SourceUnit)
        assert len(unit.declarations) == 1
        assert unit.declarations[0].name == "multiply"
        assert output is not None
        assert '"multiply"' in output

    def test_generate_accepts_input_spec(self, tmp_path):
        from headerkit._generate import generate

        h_file = tmp_path / "calc.h"
        h_file.write_text("int compute(int x);")

        spec = InputSpec.from_path(str(h_file))
        result = generate(spec, writer_name="json", backend_name="tree-sitter", no_cache=True)
        assert '"compute"' in result

    def test_generate_runs_transform_unit_waterfall(self, tmp_path):
        from headerkit._generate import generate

        @hook("transform_unit", priority=Priority.STANDARD)
        def inject_nim_init(unit: SourceUnit, context: PipelineContext) -> SourceUnit:
            if context.runtime == "nim":
                new_decls = list(unit.declarations)
                new_decls.append(Function(name="NimMain", return_type=CType("void"), parameters=[]))
                return SourceUnit(
                    path=unit.path,
                    declarations=new_decls,
                    language=unit.language,
                    classification=unit.classification,
                )
            return unit

        h_file = tmp_path / "lib.h"
        h_file.write_text("void do_something(void);")

        # Without runtime: only do_something
        res_plain = generate(str(h_file), writer_name="json", backend_name="tree-sitter", no_cache=True)
        assert '"do_something"' in res_plain
        assert '"NimMain"' not in res_plain

        # With runtime="nim": transform_unit hook runs and injects NimMain
        res_nim = generate(
            str(h_file),
            writer_name="json",
            backend_name="tree-sitter",
            runtime="nim",
            no_cache=True,
        )
        assert '"do_something"' in res_nim
        assert '"NimMain"' in res_nim

    def test_cli_accepts_runtime_and_language_options(self, tmp_path, monkeypatch, capsys):
        from headerkit._cli import main

        h_file = tmp_path / "test.h"
        h_file.write_text("int run(void);")

        @hook("transform_unit", priority=Priority.STANDARD)
        def inject_cli_hook(unit: SourceUnit, context: PipelineContext) -> SourceUnit:
            if context.runtime == "mojo":
                new_decls = list(unit.declarations)
                new_decls.append(Function(name="MojoEntry", return_type=CType("void"), parameters=[]))
                return SourceUnit(
                    path=unit.path,
                    declarations=new_decls,
                    language=unit.language,
                    classification=unit.classification,
                )
            return unit

        rc = main(
            [
                str(h_file),
                "--writer",
                "json",
                "--backend",
                "tree-sitter",
                "--runtime",
                "mojo",
                "--no-cache",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert '"run"' in captured.out
        assert '"MojoEntry"' in captured.out
