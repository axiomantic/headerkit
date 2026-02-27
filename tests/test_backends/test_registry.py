"""Tests for the backend registry."""

import pytest

from clangir.backends import (
    get_backend,
    get_backend_info,
    get_default_backend,
    is_backend_available,
    list_backends,
    register_backend,
)
from clangir.ir import Header


class MockBackend:
    """A minimal mock backend for testing the registry."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def parse(self, code: str, filename: str, **kwargs) -> Header:
        return Header(path=filename, declarations=[])


class TestBackendRegistry:
    def setup_method(self):
        """Reset registry state before each test."""
        import clangir.backends as b

        self._saved_registry = dict(b._BACKEND_REGISTRY)
        self._saved_default = b._DEFAULT_BACKEND
        self._saved_loaded = b._BACKENDS_LOADED
        b._BACKEND_REGISTRY.clear()
        b._DEFAULT_BACKEND = None
        b._BACKENDS_LOADED = False

    def teardown_method(self):
        """Restore registry state after each test."""
        import clangir.backends as b

        b._BACKEND_REGISTRY.clear()
        b._BACKEND_REGISTRY.update(self._saved_registry)
        b._DEFAULT_BACKEND = self._saved_default
        b._BACKENDS_LOADED = self._saved_loaded

    def test_register_and_get_backend(self):
        register_backend("mock", MockBackend, is_default=True)
        backend = get_backend("mock")
        assert isinstance(backend, MockBackend)
        assert backend.name == "mock"

    def test_get_default_backend(self):
        register_backend("mock", MockBackend, is_default=True)
        import clangir.backends as b

        b._BACKENDS_LOADED = True  # Prevent lazy loading
        name = get_default_backend()
        assert name == "mock"

    def test_list_backends(self):
        register_backend("mock", MockBackend)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        names = list_backends()
        assert "mock" in names

    def test_is_backend_available(self):
        register_backend("mock", MockBackend)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        assert is_backend_available("mock")
        assert not is_backend_available("nonexistent")

    def test_get_nonexistent_backend_raises(self):
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("nonexistent")

    def test_get_backend_none_returns_default(self):
        register_backend("mock", MockBackend, is_default=True)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        backend = get_backend(None)
        assert isinstance(backend, MockBackend)

    def test_get_backend_no_default_raises(self):
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        with pytest.raises(ValueError, match="No backends available"):
            get_backend(None)

    def test_get_backend_info(self):
        register_backend("mock", MockBackend)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        info = get_backend_info()
        assert isinstance(info, list)
        assert len(info) == 1
        # get_backend_info iterates the registry
        entry = info[0]
        assert entry["name"] == "mock"
        assert entry["available"] is True

    def test_first_registered_becomes_default(self):
        register_backend("first", MockBackend)
        register_backend("second", MockBackend)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        assert get_default_backend() == "first"

    def test_is_default_overrides(self):
        register_backend("first", MockBackend)
        register_backend("second", MockBackend, is_default=True)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        assert get_default_backend() == "second"

    def test_duplicate_registration_second_class_wins(self):
        """Registering the same name twice replaces the first class."""

        class MockBackend2:
            @property
            def name(self) -> str:
                return "mock2"

            @property
            def supports_macros(self) -> bool:
                return True

            @property
            def supports_cpp(self) -> bool:
                return True

            def parse(self, code: str, filename: str, **kwargs) -> Header:
                return Header(path=filename, declarations=[])

        register_backend("mock", MockBackend)
        register_backend("mock", MockBackend2)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        backend = get_backend("mock")
        assert isinstance(backend, MockBackend2)

    def test_get_backend_creates_new_instances(self):
        """Each call to get_backend should return a new instance."""
        register_backend("mock", MockBackend, is_default=True)
        import clangir.backends as b

        b._BACKENDS_LOADED = True
        first = get_backend("mock")
        second = get_backend("mock")
        assert first is not second
