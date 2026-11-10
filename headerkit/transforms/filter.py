"""Composable pipeline filters and registry for AST transformations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from headerkit.hooks import PipelineContext
from headerkit.ir import SourceUnit
from headerkit.transforms.base import IRTransformer


@dataclass(frozen=True)
class TransformOption:
    """Specification of a transform-configurable option or parameter."""

    name: str
    description: str
    default: Any = None
    choices: tuple[str, ...] | None = None
    type: type = str

    def coerce(self, val: Any) -> Any:
        """Coerce a raw value (e.g. from CLI string) to this option's expected type."""
        if val is None:
            return None
        if self.type is bool:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                lower = val.strip().lower()
                if lower in ("true", "1", "yes", "on"):
                    return True
                if lower in ("false", "0", "no", "off"):
                    return False
                raise ValueError(f"Cannot coerce {val!r} to bool for option {self.name!r} (expected 'true'/'false')")
            if isinstance(val, list) and val:
                return self.coerce(val[-1])
            return bool(val)
        if self.type is int:
            if isinstance(val, int) and not isinstance(val, bool):
                return val
            if isinstance(val, list) and val:
                return int(val[-1])
            return int(val)
        if self.type is float:
            if isinstance(val, int | float) and not isinstance(val, bool):
                return float(val)
            if isinstance(val, list) and val:
                return float(val[-1])
            return float(val)
        if self.type is str:
            if isinstance(val, list):
                return str(val[-1]) if val else ""
            return str(val) if not isinstance(val, str) else val
        if self.type is list:
            if isinstance(val, list):
                return val
            if isinstance(val, set | frozenset):
                return sorted(val, key=str)
            if isinstance(val, tuple):
                return list(val)
            return [val]
        if self.type is dict:
            if isinstance(val, dict):
                return dict(val)
            raise ValueError(f"Cannot coerce {val!r} to dict for option {self.name!r}")
        return self.type(val)


def coerce_transform_options(
    options: dict[str, Any],
    supported_options: Sequence[TransformOption],
) -> dict[str, Any]:
    """Coerce option values using the given supported TransformOption specifications."""
    specs = {opt.name: opt for opt in supported_options}
    result: dict[str, Any] = {}
    for k, v in options.items():
        if k in specs:
            opt = specs[k]
            if opt.type is list:
                result[k] = opt.coerce(v)
            elif isinstance(v, list) and opt.type not in (bool, int, float, str):
                result[k] = [opt.coerce(elem) for elem in v]
            else:
                result[k] = opt.coerce(v)
        else:
            result[k] = v[0] if isinstance(v, list) and len(v) == 1 else v
    return result


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

    supported_options: ClassVar[tuple[TransformOption, ...]] = ()

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


_TRANSFORM_REGISTRY: dict[str, type[PipelineFilter]] = {}
_TRANSFORM_ALIASES: dict[str, str] = {}
_TRANSFORM_DESCRIPTIONS: dict[str, str] = {}


def _normalize_transform_name(name: str) -> str:
    cleaned = name.strip().lower().replace("-", "_")
    return _TRANSFORM_ALIASES.get(cleaned, cleaned)


def register_transform(
    name: str,
    transform_class: type[PipelineFilter],
    *,
    aliases: Sequence[str] = (),
    description: str = "",
) -> None:
    """Register an IR transform class in the global transform registry."""
    canonical = name.strip().lower().replace("-", "_")
    _TRANSFORM_REGISTRY[canonical] = transform_class
    if description:
        _TRANSFORM_DESCRIPTIONS[canonical] = description
    for alias in aliases:
        _TRANSFORM_ALIASES[alias.strip().lower().replace("-", "_")] = canonical


def list_transforms() -> list[str]:
    """List names of all registered transforms."""
    return sorted(_TRANSFORM_REGISTRY.keys())


def list_transform_options(name: str) -> list[TransformOption]:
    """Return the supported TransformOption specifications for a registered transform."""
    canonical = _normalize_transform_name(name)
    if canonical not in _TRANSFORM_REGISTRY:
        available = ", ".join(list_transforms()) or "(none)"
        raise ValueError(f"Unknown transform: {name!r}. Available: {available}")
    cls = _TRANSFORM_REGISTRY[canonical]
    return list(getattr(cls, "supported_options", ()))


def get_transform(name: str, **kwargs: Any) -> PipelineFilter:
    """Instantiate a registered transform by name with coerced option kwargs."""
    canonical = _normalize_transform_name(name)
    if canonical not in _TRANSFORM_REGISTRY:
        available = ", ".join(list_transforms()) or "(none)"
        raise ValueError(f"Unknown transform: {name!r}. Available: {available}")
    cls = _TRANSFORM_REGISTRY[canonical]
    supported = getattr(cls, "supported_options", ())
    coerced = coerce_transform_options(kwargs, supported) if kwargs else {}
    return cls(**coerced)
