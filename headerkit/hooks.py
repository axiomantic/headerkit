"""Unified hook and extension pipeline.

Provides priority-based, pattern-matched hook execution for parsing,
transforming, and generating output across backends and writers.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, TypeVar

T = TypeVar("T")


class Priority(IntEnum):
    """Execution priority tiers for registered hooks.

    Higher numeric values execute first.
    """

    FALLBACK = 10
    STANDARD = 50
    PROJECT = 100
    OVERRIDE = 1000


@dataclass(frozen=True)
class PipelineContext:
    """Contextual metadata passed to hooks during dispatch."""

    backend: str | None = None
    writer: str | None = None
    target: str | None = None
    language: str | None = None
    classification: str | None = None
    options: dict[str, Any] | None = None


@dataclass(frozen=True)
class HookImpl:
    """Metadata and callable reference for an individual hook implementation."""

    point: str
    func: Callable[..., Any]
    priority: int
    matchers: dict[str, str]

    def matches(self, context: PipelineContext) -> bool:
        """Check whether this hook matches the provided pipeline context."""
        for attr, pattern in self.matchers.items():
            val = getattr(context, attr, None)
            if val is None:
                if pattern != "*":
                    return False
            elif not fnmatch.fnmatch(str(val), pattern):
                return False
        return True

    @property
    def specificity(self) -> int:
        """Calculate specificity score for tie-breaking hooks with identical priority.

        Exact matches without wildcards score higher than wildcard patterns.
        """
        score = 0
        for pattern in self.matchers.values():
            if "*" not in pattern and "?" not in pattern:
                score += 10
            elif pattern != "*":
                score += 1
        return score


class HookRegistry:
    """Registry maintaining registered hooks organized by hook point."""

    _global_hooks: list[HookImpl] = []

    def __init__(self) -> None:
        self._hooks: list[HookImpl] = []

    def register(
        self,
        point: str,
        func: Callable[..., Any],
        priority: int = Priority.STANDARD,
        **matchers: str,
    ) -> None:
        """Register a hook implementation."""
        impl = HookImpl(point=point, func=func, priority=priority, matchers=matchers)
        self._hooks.append(impl)

    def get_matching(self, point: str, context: PipelineContext) -> list[HookImpl]:
        """Return matching hooks sorted by priority descending and specificity descending."""
        candidates = [impl for impl in self._hooks if impl.point == point and impl.matches(context)]
        candidates.sort(key=lambda impl: (impl.priority, impl.specificity), reverse=True)
        return candidates

    @classmethod
    def get_global_matching(cls, point: str, context: PipelineContext) -> list[HookImpl]:
        candidates = [impl for impl in cls._global_hooks if impl.point == point and impl.matches(context)]
        candidates.sort(key=lambda impl: (impl.priority, impl.specificity), reverse=True)
        return candidates

    @classmethod
    def register_global(
        cls,
        point: str,
        func: Callable[..., Any],
        priority: int = Priority.STANDARD,
        **matchers: str,
    ) -> None:
        impl = HookImpl(point=point, func=func, priority=priority, matchers=matchers)
        cls._global_hooks.append(impl)

    @classmethod
    def snapshot(cls) -> list[HookImpl]:
        """Return a copy of all globally registered hooks."""
        return list(cls._global_hooks)

    @classmethod
    def restore(cls, snapshot: list[HookImpl]) -> None:
        """Restore global hooks from a snapshot."""
        cls._global_hooks = list(snapshot)

    @classmethod
    def clear(cls) -> None:
        """Clear all globally registered hooks."""
        cls._global_hooks.clear()


def hook(
    point: str,
    priority: int = Priority.STANDARD,
    registry: HookRegistry | None = None,
    **matchers: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a hook implementation."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if registry is not None:
            registry.register(point, fn, priority=priority, **matchers)
        else:
            HookRegistry.register_global(point, fn, priority=priority, **matchers)
        return fn

    return decorator


class HookDispatcher:
    """Dispatches calls across registered hooks according to execution mode."""

    def __init__(self, registry: HookRegistry | None = None) -> None:
        self._registry = registry

    def _get_hooks(self, point: str, context: PipelineContext) -> list[HookImpl]:
        if self._registry is not None:
            return self._registry.get_matching(point, context)
        return HookRegistry.get_global_matching(point, context)

    def first_result(self, point: str, *args: Any, context: PipelineContext, **kwargs: Any) -> Any:
        """Execute matching hooks in priority order until one returns a non-None value."""
        for impl in self._get_hooks(point, context):
            res = impl.func(*args, context=context, **kwargs)
            if res is not None:
                return res
        return None

    def waterfall(self, point: str, initial_value: T, *args: Any, context: PipelineContext, **kwargs: Any) -> T:
        """Pass a value sequentially through matching hooks in priority order."""
        current = initial_value
        for impl in self._get_hooks(point, context):
            current = impl.func(current, *args, context=context, **kwargs)
        return current


class HookCaller:
    """Convenience caller bound to a specific hook point."""

    def __init__(self, point: str, registry: HookRegistry | None = None) -> None:
        self._point = point
        self._dispatcher = HookDispatcher(registry=registry)

    def first_result(self, *args: Any, context: PipelineContext, **kwargs: Any) -> Any:
        return self._dispatcher.first_result(self._point, *args, context=context, **kwargs)

    def waterfall(self, initial_value: T, *args: Any, context: PipelineContext, **kwargs: Any) -> T:
        return self._dispatcher.waterfall(self._point, initial_value, *args, context=context, **kwargs)
