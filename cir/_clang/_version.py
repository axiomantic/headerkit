"""LLVM version detection for vendored clang bindings.

Detection strategy (in order):
1. CIR_CLANG_VERSION env var (explicit user override)
2. llvm-config --version (most reliable, skips Apple clang issue)
3. clang -dM -E -x c /dev/null to get __clang_major__
4. Return None if all methods fail
"""

import os
import re
import shutil
import subprocess
import sys


def detect_llvm_version() -> str | None:
    """Detect the system LLVM major version.

    Returns the major version as a string (e.g., "18") or None if detection fails.
    """
    # Strategy 1: Explicit env var override
    env_version = os.environ.get("CIR_CLANG_VERSION")
    if env_version:
        return env_version.strip()

    # Strategy 2: llvm-config --version
    version = _try_llvm_config()
    if version is not None:
        return version

    # Strategy 3: clang -dM -E to get __clang_major__
    version = _try_clang_preprocessor()
    if version is not None:
        return version

    # Strategy 4: All methods failed
    return None


def _try_llvm_config() -> str | None:
    """Try to detect LLVM version via llvm-config --version."""
    llvm_config = shutil.which("llvm-config")
    if not llvm_config:
        return None

    try:
        result = subprocess.run(
            [llvm_config, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            major = version.split(".")[0]
            if major.isdigit():
                return major
    except (subprocess.SubprocessError, OSError):
        pass

    return None


def _try_clang_preprocessor() -> str | None:
    """Try to detect LLVM version via clang -dM -E preprocessor defines.

    This works for both upstream LLVM clang and Apple clang.
    The __clang_major__ define gives the compiler's major version.
    """
    clang = shutil.which("clang")
    if not clang:
        return None

    null_file = "NUL" if sys.platform == "win32" else "/dev/null"
    try:
        result = subprocess.run(
            [clang, "-dM", "-E", "-x", "c", null_file],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"#define\s+__clang_major__\s+(\d+)", result.stdout)
            if match:
                return match.group(1)
    except (subprocess.SubprocessError, OSError):
        pass

    return None
