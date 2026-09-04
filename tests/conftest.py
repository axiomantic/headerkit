# tripwire.pytest_plugin is registered automatically via the pytest11 entry point.
from __future__ import annotations

import pytest

from headerkit.backends import is_backend_available


@pytest.fixture(autouse=True)
def _skip_if_missing_backend(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("libclang"):
        from headerkit.backends.libclang import is_system_libclang_available

        if not is_system_libclang_available():
            pytest.skip("System libclang not available")
    if request.node.get_closest_marker("treesitter"):
        if not is_backend_available("tree-sitter"):
            pytest.skip("tree-sitter or tree-sitter-c optional dependency not available")
