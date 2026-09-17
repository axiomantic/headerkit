"""Tests for HookRegistry fast-path point indexing and snapshot isolation."""

import pytest

from headerkit.hooks import HookRegistry, PipelineContext, hook


@pytest.fixture(autouse=True)
def clean_registry():
    saved = HookRegistry.snapshot()
    HookRegistry.clear()
    yield
    HookRegistry.restore(saved)


def test_hook_registry_has_point_fast_path() -> None:
    assert HookRegistry.has_point("transform:CustomNode") is False

    @hook("transform:CustomNode")
    def dummy_hook(node: object, _context: PipelineContext) -> object:
        return node

    assert HookRegistry.has_point("transform:CustomNode") is True
    assert HookRegistry.has_point("transform:OtherNode") is False


def test_hook_registry_snapshot_and_restore_synchronization() -> None:
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
