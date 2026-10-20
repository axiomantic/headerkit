"""Composable pipeline filters for AST transformations."""

from __future__ import annotations

from typing import Any

from headerkit.hooks import PipelineContext
from headerkit.ir import SourceUnit
from headerkit.transforms.base import IRTransformer


class PipelineFilter(IRTransformer):
    """Base class for composable pipeline transformations."""

    def __rshift__(self, other: PipelineFilter) -> ChainedFilter:
        return ChainedFilter(self, other)

    def __rrshift__(self, unit: SourceUnit) -> SourceUnit:
        return self.transform(unit)

    def transform(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        res = self.visit(unit, context=context)
        if isinstance(res, SourceUnit):
            return res
        raise TypeError(f"Filter {self.__class__.__name__} must return SourceUnit, got {type(res).__name__}")


class ChainedFilter(PipelineFilter):
    """Sequential combination of two or more pipeline filters."""

    def __init__(self, first: PipelineFilter, second: PipelineFilter) -> None:
        self.filters: list[PipelineFilter] = []
        for f in (first, second):
            if isinstance(f, ChainedFilter):
                self.filters.extend(f.filters)
            else:
                self.filters.append(f)

    def transform(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        current = unit
        for f in self.filters:
            current = f.transform(current, context=context)
        return current

    def visit(self, node: Any, context: PipelineContext | None = None) -> Any:
        current = node
        for f in self.filters:
            current = f.visit(current, context=context)
        return current
