from __future__ import annotations


def hello() -> str:
    """Return greeting string."""
    return "hello from headerkit"


def add(a: int, b: int) -> int:
    """Add two integers."""
    if not isinstance(a, int) or not isinstance(b, int):
        raise TypeError("Arguments must be integers")
    return a + b


def version() -> str:
    """Return package version."""
    return "0.0.1"


__all__ = ["add", "hello", "version"]
