"""Composable pipeline filters for AST transformations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from headerkit.hooks import PipelineContext
from headerkit.ir import SourceUnit
from headerkit.transforms.base import IRTransformer


def _normalize_fingerprint_value(val: Any) -> Any:
    """Convert filter state values into deterministic JSON-serializable structures."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, Path):
        return str(val)
    if isinstance(val, (set, frozenset)):
        return sorted(_normalize_fingerprint_value(item) for item in val)
    if isinstance(val, (list, tuple)):
        return [_normalize_fingerprint_value(item) for item in val]
    if isinstance(val, dict):
        return {str(k): _normalize_fingerprint_value(v) for k, v in sorted(val.items(), key=lambda kv: str(kv[0]))}
    return str(val)


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

    def cache_fingerprint(self) -> dict[str, Any]:
        """Return a deterministic JSON-serializable fingerprint of this filter's configuration."""
        config: dict[str, Any] = {}
        for key, value in sorted(self.__dict__.items()):
            if key.startswith("_"):
                continue
            config[key] = _normalize_fingerprint_value(value)
        return {
            "filter": self.__class__.__name__,
            "config": config,
        }


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

    def cache_fingerprint(self) -> dict[str, Any]:
        """Return a deterministic fingerprint of all chained filters in order."""
        return {
            "filter": self.__class__.__name__,
            "filters": [f.cache_fingerprint() for f in self.filters],
        }

