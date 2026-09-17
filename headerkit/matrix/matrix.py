"""Configuration matrix builder, constraint validator, and Cartesian product solver."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from headerkit.matrix.models import ConfigCondition, MatrixDimension

MAX_MATRIX_CONFIGURATIONS: int = 32


class MatrixExplosionError(RuntimeError):
    """Raised when the resolved Cartesian product exceeds the safe evaluation threshold."""


@dataclass
class ConfigMatrix:
    """Manages matrix dimensions and evaluation constraints."""

    dimensions: list[MatrixDimension] = field(default_factory=list)
    constraints: list[Callable[[ConfigCondition], bool]] = field(default_factory=list)
    max_configurations: int = MAX_MATRIX_CONFIGURATIONS
    allow_unbounded: bool = False

    def add_dimension(
        self,
        name: str,
        values: Sequence[Any],
        dimension_type: str = "feature",
        define_template: str | None = None,
    ) -> None:
        self.dimensions.append(
            MatrixDimension(
                name=name,
                values=tuple(values),
                dimension_type=dimension_type,  # type: ignore[arg-type]
                define_template=define_template,
            )
        )

    def add_constraint(self, constraint: Callable[[ConfigCondition], bool]) -> None:
        """Register a predicate that must return True for valid configurations."""
        self.constraints.append(constraint)

    def resolve(self) -> list[ConfigCondition]:
        """Compute the constrained Cartesian product."""
        if not self.dimensions:
            return [ConfigCondition({})]

        dim_names = [d.name for d in self.dimensions]
        dim_values = [d.values for d in self.dimensions]

        raw_combinations = itertools.product(*dim_values)
        resolved: list[ConfigCondition] = []

        for combo in raw_combinations:
            coords = dict(zip(dim_names, combo, strict=True))
            cond = ConfigCondition(coords)
            if all(c(cond) for c in self.constraints):
                resolved.append(cond)

        if len(resolved) > self.max_configurations and not self.allow_unbounded:
            raise MatrixExplosionError(
                f"Resolved configuration count ({len(resolved)}) exceeds safe threshold "
                f"({self.max_configurations}). Register constraints to prune invalid combinations, "
                f"or pass allow_unbounded=True to bypass."
            )

        return resolved
