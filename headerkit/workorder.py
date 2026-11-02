"""Minimal workorder compatibility stub retained for writer imports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from headerkit.scaffold import OutputFile


def build_work_order_files(*_args: object, **_kwargs: object) -> list[OutputFile]:
    """Compatibility stub; work order scaffolding has been removed from headerkit."""
    return []


def render_agents_md(*_args: object, **_kwargs: object) -> str:
    """Compatibility stub; work order scaffolding has been removed from headerkit."""
    return ""
