"""Tests for PipelineFilter composition and right-shift operator."""

from __future__ import annotations

from dataclasses import replace

from headerkit.ir import CType, Function, SourceUnit
from headerkit.transforms.filter import ChainedFilter, PipelineFilter


class AppendSuffixFilter(PipelineFilter):
    def __init__(self, suffix: str) -> None:
        self.suffix = suffix

    def visit_Function(self, node: Function, context: object | None = None) -> Function:
        return replace(node, name=f"{node.name}{self.suffix}")


def test_pipeline_filter_transform() -> None:
    fn = Function(name="init", return_type=CType("void"))
    unit = SourceUnit(path="test.h", declarations=[fn])

    filt = AppendSuffixFilter("_v1")
    res = filt.transform(unit)

    assert isinstance(res, SourceUnit)
    assert res.declarations[0].name == "init_v1"


def test_pipeline_filter_chaining_operator() -> None:
    filt1 = AppendSuffixFilter("_step1")
    filt2 = AppendSuffixFilter("_step2")
    chained = filt1 >> filt2

    assert isinstance(chained, ChainedFilter)
    assert len(chained.filters) == 2

    fn = Function(name="run", return_type=CType("void"))
    unit = SourceUnit(path="test.h", declarations=[fn])
    res = chained.transform(unit)

    assert res.declarations[0].name == "run_step1_step2"


def test_pipeline_filter_unit_rrshift() -> None:
    fn = Function(name="compute", return_type=CType("void"))
    unit = SourceUnit(path="test.h", declarations=[fn])

    filt1 = AppendSuffixFilter("_a")
    filt2 = AppendSuffixFilter("_b")
    res = unit >> filt1 >> filt2

    assert isinstance(res, SourceUnit)
    assert res.declarations[0].name == "compute_a_b"
