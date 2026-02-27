"""Tests for LLVM version detection logic."""

import os
import subprocess
from unittest.mock import MagicMock, patch

from clangir._clang._version import detect_llvm_version


class TestEnvVarOverride:
    def test_cir_clang_version_env_var(self):
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "18"}):
            assert detect_llvm_version() == "18"

    def test_cir_clang_version_env_var_takes_precedence(self):
        """Env var should take precedence over llvm-config."""
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "20"}):
            assert detect_llvm_version() == "20"

    def test_env_var_with_whitespace_is_stripped(self):
        """Env var value with leading/trailing whitespace is stripped."""
        with patch.dict(os.environ, {"CIR_CLANG_VERSION": "  19  "}):
            assert detect_llvm_version() == "19"


class TestLlvmConfig:
    def test_llvm_config_full_version(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "18.1.0\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("clangir._clang._version.shutil.which", return_value="/usr/bin/llvm-config"),
            patch("clangir._clang._version.subprocess.run", return_value=mock_result),
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
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("clangir._clang._version.subprocess.run", return_value=mock_result),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "19"

    def test_llvm_config_returns_non_zero_exit(self):
        """When llvm-config returns non-zero, fall through to clang."""
        llvm_result = MagicMock()
        llvm_result.returncode = 1
        llvm_result.stdout = ""
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 21\n"

        def run_side_effect(cmd, **kwargs):
            if "llvm-config" in cmd[0]:
                return llvm_result
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch("clangir._clang._version.subprocess.run", side_effect=run_side_effect),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "21"

    def test_llvm_config_returns_garbage(self):
        """When llvm-config returns non-numeric output, fall through to clang."""
        llvm_result = MagicMock()
        llvm_result.returncode = 0
        llvm_result.stdout = "not-a-version\n"
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 20\n"

        def run_side_effect(cmd, **kwargs):
            if "llvm-config" in cmd[0]:
                return llvm_result
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch("clangir._clang._version.subprocess.run", side_effect=run_side_effect),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "20"

    def test_llvm_config_subprocess_timeout(self):
        """When llvm-config times out, fall through to clang."""
        clang_result = MagicMock()
        clang_result.returncode = 0
        clang_result.stdout = "#define __clang_major__ 19\n"

        def run_side_effect(cmd, **kwargs):
            if "llvm-config" in cmd[0]:
                raise subprocess.TimeoutExpired(cmd="llvm-config", timeout=5)
            return clang_result

        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch("clangir._clang._version.subprocess.run", side_effect=run_side_effect),
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
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("clangir._clang._version.subprocess.run", return_value=mock_result),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "20"

    def test_apple_clang_version_detected(self):
        """Apple clang reports its own __clang_major__ which differs from LLVM version.

        Apple Clang 16.x ships __clang_major__ = 16. This should be detected
        and returned as the version string.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "#define __APPLE_CC__ 6000\n"
            "#define __apple_build_version__ 16000026\n"
            "#define __clang__ 1\n"
            "#define __clang_major__ 16\n"
            "#define __clang_minor__ 0\n"
            "#define __clang_patchlevel__ 0\n"
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("clangir._clang._version.subprocess.run", return_value=mock_result),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() == "16"

    def test_clang_preprocessor_no_clang_major(self):
        """When clang output has no __clang_major__, returns None."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "#define __STDC__ 1\n#define __STDC_VERSION__ 201710L\n"
        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("clangir._clang._version.subprocess.run", return_value=mock_result),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() is None

    def test_clang_subprocess_oserror(self):
        """When clang subprocess raises OSError, returns None."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch(
                "clangir._clang._version.shutil.which",
                side_effect=lambda cmd: None if cmd == "llvm-config" else "/usr/bin/clang",
            ),
            patch("clangir._clang._version.subprocess.run", side_effect=OSError("Permission denied")),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() is None


class TestFallback:
    def test_all_methods_fail_returns_none(self):
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("clangir._clang._version.shutil.which", return_value=None),
        ):
            os.environ.pop("CIR_CLANG_VERSION", None)
            assert detect_llvm_version() is None
