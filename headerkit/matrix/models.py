"""Data models for parameterized configuration matrices."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal


@dataclass(frozen=True)
class MatrixDimension:
    """Definition of a variation axis in the configuration matrix."""

    name: str
    values: tuple[Any, ...]
    dimension_type: Literal["target", "version", "feature", "custom"]
    define_template: str | None = None


class ConfigCondition(Mapping[str, Any]):
    """Immutable, hashable configuration coordinate tuple representing an evaluated slice."""

    def __init__(self, coordinates: Mapping[str, Any]) -> None:
        self._data = dict(coordinates)
        self._proxy = MappingProxyType(self._data)
        self._hash = hash(tuple(sorted(self._data.items())))

    def __getitem__(self, key: str) -> Any:
        return self._proxy[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._proxy)

    def __len__(self) -> int:
        return len(self._proxy)

    def __hash__(self) -> int:
        return self._hash

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ConfigCondition):
            return self._data == other._data
        if isinstance(other, Mapping):
            return self._data == dict(other)
        return False

    def __repr__(self) -> str:
        return f"ConfigCondition({self._data!r})"

    @property
    def target(self) -> str | None:
        return self._data.get("target")

    @property
    def version(self) -> Any:
        return self._data.get("version")

    def to_compiler_flags(self) -> list[str]:
        """Convert coordinates into Clang command line compiler flags."""
        flags: list[str] = []
        if "target" in self._data:
            flags.append(f"--target={self._data['target']}")

        for k, v in self._data.items():
            if k in ("target", "version"):
                continue
            if isinstance(v, bool):
                flags.append(f"-D{k}=1" if v else f"-U{k}")
            elif isinstance(v, (int, str)):
                flags.append(f"-D{k}={v}")
        return flags
