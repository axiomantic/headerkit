"""Tests for the vendored cindex module loader."""

import os
from unittest.mock import patch

import pytest

from clangir._clang import LATEST_VENDORED, OLDEST_VENDORED, VENDORED_VERSIONS, get_cindex


class TestGetCindex:
    def setup_method(self):
        """Reset the cached module before each test."""
        import clangir._clang

        clangir._clang._cached_cindex = None

    def test_returns_module_with_config(self):
        """get_cindex should return a module with Config class."""
        cindex = get_cindex()
        assert hasattr(cindex, "Config")

    def test_returns_module_with_index(self):
        """get_cindex should return a module with Index class."""
        cindex = get_cindex()
        assert hasattr(cindex, "Index")

    def test_returns_module_with_cursor_kind(self):
        """get_cindex should return a module with CursorKind."""
        cindex = get_cindex()
        assert hasattr(cindex, "CursorKind")

    def test_caching(self):
        """Second call should return the exact same module object."""
        first = get_cindex()
        second = get_cindex()
        assert first is second

    def test_env_var_override(self):
        """CIR_CLANG_VERSION env var should select the version."""
        import clangir._clang

        clangir._clang._cached_cindex = None
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            cindex = get_cindex()
            assert cindex is not None
            assert hasattr(cindex, "Config")

    def test_vendored_versions_tuple(self):
        assert isinstance(VENDORED_VERSIONS, tuple)
        assert "18" in VENDORED_VERSIONS
        assert "19" in VENDORED_VERSIONS
        assert "20" in VENDORED_VERSIONS
        assert "21" in VENDORED_VERSIONS

    def test_latest_and_oldest(self):
        assert OLDEST_VENDORED == "18"
        assert LATEST_VENDORED == "21"

    def test_fallback_to_latest_when_detection_fails(self):
        """When version detection returns None, fall back to latest with warning."""
        import clangir._clang

        clangir._clang._cached_cindex = None
        with (
            patch("clangir._clang._version.detect_llvm_version", return_value=None),
            pytest.warns(UserWarning, match="Could not detect LLVM version"),
        ):
            cindex = get_cindex()
            assert cindex is not None

    def test_fallback_to_oldest_for_old_version(self):
        """When detected version is below oldest, fall back to oldest with warning."""
        import clangir._clang

        clangir._clang._cached_cindex = None
        with (
            patch("clangir._clang._version.detect_llvm_version", return_value="15"),
            pytest.warns(UserWarning, match="older than oldest vendored"),
        ):
            cindex = get_cindex()
            assert cindex is not None

    def test_fallback_to_latest_for_new_version(self):
        """When detected version is above latest, fall back to latest with warning."""
        import clangir._clang

        clangir._clang._cached_cindex = None
        with (
            patch("clangir._clang._version.detect_llvm_version", return_value="25"),
            pytest.warns(UserWarning, match="newer than latest vendored"),
        ):
            cindex = get_cindex()
            assert cindex is not None

    def test_each_vendored_version_loads(self):
        """Every vendored version can be imported and has required attributes."""
        import importlib

        for version in VENDORED_VERSIONS:
            module = importlib.import_module(f"clangir._clang.v{version}.cindex")
            assert hasattr(module, "Config"), f"v{version} missing Config"
            assert hasattr(module, "Index"), f"v{version} missing Index"
            assert hasattr(module, "CursorKind"), f"v{version} missing CursorKind"
            assert hasattr(module, "TypeKind"), f"v{version} missing TypeKind"
            assert hasattr(module, "TranslationUnit"), f"v{version} missing TranslationUnit"

    def test_exact_match_no_warning(self):
        """When detected version exactly matches a vendored version, no warning is emitted."""
        import clangir._clang

        clangir._clang._cached_cindex = None
        import warnings
        with (
            patch("clangir._clang._version.detect_llvm_version", return_value="20"),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error")  # Turn warnings into errors
            cindex = get_cindex()
            assert cindex is not None

    def test_cache_is_cleared_correctly(self):
        """After clearing cache, next call re-detects version."""
        import clangir._clang

        # Load once
        first = get_cindex()
        assert first is not None

        # Clear cache
        clangir._clang._cached_cindex = None

        # Load again with different version override
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            second = get_cindex()
            assert second is not None
            # Module names should reflect the version
            assert "v18" in second.__name__
