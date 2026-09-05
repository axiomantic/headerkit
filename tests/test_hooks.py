from __future__ import annotations

import pytest

from headerkit.hooks import (
    HookCaller,
    HookDispatcher,
    HookRegistry,
    PipelineContext,
    Priority,
    hook,
)


class TestHooksCore:
    @pytest.fixture(autouse=True)
    def clean_registry(self):
        saved = HookRegistry.snapshot()
        HookRegistry.clear()
        yield
        HookRegistry.restore(saved)

    def test_priority_ordering_and_numeric_values(self):
        assert int(Priority.FALLBACK) == 10
        assert int(Priority.STANDARD) == 50
        assert int(Priority.PROJECT) == 100
        assert int(Priority.OVERRIDE) == 1000

        assert Priority.FALLBACK < Priority.STANDARD < Priority.PROJECT < Priority.OVERRIDE

    def test_first_result_dispatch_executes_highest_priority(self):
        events: list[str] = []

        @hook("parse_unit", priority=Priority.STANDARD)
        def standard_parser(code: str, context: PipelineContext) -> str | None:
            events.append(f"standard:{context.language}")
            return f"standard:{code}"

        @hook("parse_unit", priority=Priority.OVERRIDE)
        def override_parser(code: str, context: PipelineContext) -> str | None:
            events.append(f"override:{context.language}")
            return f"override:{code}"

        @hook("parse_unit", priority=Priority.FALLBACK)
        def fallback_parser(code: str, context: PipelineContext) -> str | None:
            events.append(f"fallback:{context.language}")
            return f"fallback:{code}"

        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="c")
        result = dispatcher.first_result("parse_unit", "int x;", context=ctx)

        assert result == "override:int x;"
        assert events == ["override:c"]

    def test_first_result_cascades_on_none(self):
        events: list[str] = []

        @hook("parse_unit", priority=Priority.OVERRIDE)
        def override_parser(code: str, context: PipelineContext) -> str | None:
            events.append(f"override_skipped:{code}:{context.language}")
            return None

        @hook("parse_unit", priority=Priority.STANDARD)
        def standard_parser(code: str, context: PipelineContext) -> str | None:
            events.append(f"standard_executed:{code}:{context.language}")
            return "standard_result"

        @hook("parse_unit", priority=Priority.FALLBACK)
        def fallback_parser(code: str, context: PipelineContext) -> str | None:
            events.append(f"fallback_unreached:{code}:{context.language}")
            return "fallback_result"

        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="c")
        result = dispatcher.first_result("parse_unit", "int y;", context=ctx)

        assert result == "standard_result"
        assert events == ["override_skipped:int y;:c", "standard_executed:int y;:c"]

    def test_first_result_returns_none_if_all_hooks_yield_none(self):
        @hook("parse_unit", priority=Priority.STANDARD)
        def skipping_parser(code: str, context: PipelineContext) -> str | None:
            _ = (code, context)
            return None

        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="c")
        result = dispatcher.first_result("parse_unit", "int z;", context=ctx)
        assert result is None

    def test_glob_matching_on_context_attributes(self):
        @hook("write_output", writer="ctypes", priority=Priority.STANDARD)
        def write_ctypes(ast_repr: str, context: PipelineContext) -> str:
            return f"ctypes:{ast_repr}:{context.writer}"

        @hook("write_output", writer="cffi*", priority=Priority.STANDARD)
        def write_cffi_wildcard(ast_repr: str, context: PipelineContext) -> str:
            return f"cffi_wildcard:{ast_repr}:{context.writer}"

        @hook("write_output", writer="*", priority=Priority.FALLBACK)
        def write_fallback(ast_repr: str, context: PipelineContext) -> str:
            return f"generic:{ast_repr}:{context.writer}"

        dispatcher = HookDispatcher()

        res_ctypes = dispatcher.first_result("write_output", "AST", context=PipelineContext(writer="ctypes"))
        assert res_ctypes == "ctypes:AST:ctypes"

        res_cffi = dispatcher.first_result("write_output", "AST", context=PipelineContext(writer="cffi_custom"))
        assert res_cffi == "cffi_wildcard:AST:cffi_custom"

        res_unknown = dispatcher.first_result("write_output", "AST", context=PipelineContext(writer="rust_unknown"))
        assert res_unknown == "generic:AST:rust_unknown"

    def test_glob_matching_with_negative_controls(self):
        @hook("write_output", writer="nim", target="*windows*", priority=Priority.PROJECT)
        def write_nim_windows(ast_repr: str, context: PipelineContext) -> str:
            return f"nim_win:{ast_repr}:{context.target}"

        @hook("write_output", writer="nim", target="*", priority=Priority.STANDARD)
        def write_nim_generic(ast_repr: str, context: PipelineContext) -> str:
            return f"nim_generic:{ast_repr}:{context.target}"

        dispatcher = HookDispatcher()

        linux_ctx = PipelineContext(writer="nim", target="x86_64-unknown-linux-gnu")
        assert (
            dispatcher.first_result("write_output", "AST", context=linux_ctx)
            == "nim_generic:AST:x86_64-unknown-linux-gnu"
        )

        win_ctx = PipelineContext(writer="nim", target="x86_64-pc-windows-msvc")
        assert dispatcher.first_result("write_output", "AST", context=win_ctx) == "nim_win:AST:x86_64-pc-windows-msvc"

    def test_waterfall_pipeline_threads_modifications(self):
        @hook("transform_unit", priority=Priority.STANDARD)
        def step_one(tokens: list[str], context: PipelineContext) -> list[str]:
            return [*tokens, f"step1_{context.language}"]

        @hook("transform_unit", priority=Priority.PROJECT)
        def step_two(tokens: list[str], context: PipelineContext) -> list[str]:
            return [*tokens, f"step2_before_one_{context.language}"]

        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="c")
        result = dispatcher.waterfall("transform_unit", ["initial"], context=ctx)

        assert result == ["initial", "step2_before_one_c", "step1_c"]

    def test_deterministic_tie_breaking_exact_vs_wildcard(self):
        @hook("write_output", writer="*", priority=Priority.STANDARD)
        def wildcard_writer(data: str, context: PipelineContext) -> str:
            return f"wildcard:{data}:{context.writer}"

        @hook("write_output", writer="exact_name", priority=Priority.STANDARD)
        def exact_writer(data: str, context: PipelineContext) -> str:
            return f"exact:{data}:{context.writer}"

        dispatcher = HookDispatcher()
        ctx = PipelineContext(writer="exact_name")
        assert dispatcher.first_result("write_output", "data", context=ctx) == "exact:data:exact_name"

    def test_hook_caller_functional_interface(self):
        registry = HookRegistry()

        @hook("parse_unit", writer="json", priority=Priority.STANDARD, registry=registry)
        def my_parser(code: str, context: PipelineContext) -> str:
            return f"parsed:{code}:{context.writer}"

        caller = HookCaller("parse_unit", registry=registry)
        ctx = PipelineContext(writer="json")
        assert caller.first_result("code", context=ctx) == "parsed:code:json"
