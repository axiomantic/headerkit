"""Tests for LLVM version detection logic."""

import os
from unittest.mock import MagicMock, patch

from cir._clang._version import detect_llvm_version


class TestEnvVarOverride:
    def test_cir_clang_version_env_var(self):
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            assert detect_llvm_version() == "18"

    def test_cir_clang_version_env_var_takes_precedence(self):
        """Env var should take precedence over llvm-config."""
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "20"}):
            assert detect_llvm_version() == "20"


class TestLlvmConfig:
    def test_llvm_config_full_version(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "18.1.0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("cir._clang._version.shutil.which", return_value="/usr/bin/llvm-config"),
            patch("cir._clang._version.subprocess.run", return_value=mock_result),
        ):
            # Clear env var if set
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "18"

    def test_llvm_config_not_found(self):
        """When llvm-config is not found, fall through to clang detection."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 19\n#define __clang_minor__ 0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "cir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("cir._clang._version.subprocess.run", return_value=mock_result),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "19"


class TestClangPreprocessor:
    def test_clang_major_define(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __clang_major__ 20\n#define __clang_minor__ 1\n#define __clang_patchlevel__ 0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "cir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("cir._clang._version.subprocess.run", return_value=mock_result),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "20"


class TestFallback:
    def test_all_methods_fail_returns_none(self):
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("cir._clang._version.shutil.which", return_value=None),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() is None
