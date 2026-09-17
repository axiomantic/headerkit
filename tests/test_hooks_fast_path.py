"""Tests for HookRegistry fast-path point indexing and snapshot isolation."""

from __future__ import annotations

from headerkit.hooks import HookRegistry, PipelineContext, hook


def test_hook_registry_has_point_fast_path() -> None:
    assert HookRegistry.has_point("transform:CustomNode") is False

    @hook("transform:CustomNode")
    def dummy_hook(node: object, _context: PipelineContext) -> object:
        return node

    try:
        assert HookRegistry.has_point("transform:CustomNode") is True
        assert HookRegistry.has_point("transform:OtherNode") is False
    finally:
        HookRegistry.clear()
        assert HookRegistry.has_point("transform:CustomNode") is False


def test_hook_registry_snapshot_and_restore_synchronization() -> None:
    HookRegistry.clear()
    assert HookRegistry.has_point("transform:A") is False

    @hook("transform:A")
    def hook_a(node: object, _context: PipelineContext) -> object:
        return node

    snap = HookRegistry.snapshot()
    assert HookRegistry.has_point("transform:A") is True

    HookRegistry.clear()
    assert HookRegistry.has_point("transform:A") is False

    HookRegistry.restore(snap)
    assert HookRegistry.has_point("transform:A") is True
    assert len(HookRegistry.get_global_matching("transform:A", PipelineContext())) == 1
    HookRegistry.clear()
