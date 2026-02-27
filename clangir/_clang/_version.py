"""LLVM version detection for vendored clang bindings.

Detection strategy (in order):
1. CIR_CLANG_VERSION env var (explicit user override)
2. llvm-config --version (tries versioned names like llvm-config-18)
3. pkg-config --modversion clang (reliable on Linux with libclang-dev)
4. clang -dM -E -x c /dev/null to get __clang_major__ (tries clang-18 etc.)
5. /usr/lib/llvm-{N}/ directory presence (Debian/Ubuntu, Linux only)
6. Homebrew llvm prefix (macOS only, for non-PATH brew installs)
7. Return None if all methods fail
"""

import glob
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
        stripped = env_version.strip()
        if stripped.isdigit():
            return stripped
        import logging

        logging.getLogger(__name__).warning(
            "CIR_CLANG_VERSION=%r is not a valid major version number, ignoring", env_version
        )

    # Strategy 2: llvm-config --version
    version = _try_llvm_config()
    if version is not None:
        return version

    # Strategy 3: pkg-config --modversion clang
    version = _try_pkg_config()
    if version is not None:
        return version

    # Strategy 4: clang -dM -E to get __clang_major__
    version = _try_clang_preprocessor()
    if version is not None:
        return version

    # Strategy 5: /usr/lib/llvm-{N}/ directories (Linux)
    version = _try_llvm_dir()
    if version is not None:
        return version

    # Strategy 6: Homebrew llvm prefix (macOS)
    version = _try_homebrew_llvm()
    if version is not None:
        return version

    # Strategy 7: All methods failed
    return None


def _find_versioned_binary(base_name: str) -> str | None:
    """Find a binary by name, trying unversioned first then versioned variants.

    On Debian/Ubuntu, tools are often only available as e.g. llvm-config-18
    or clang-18 without an unversioned symlink.
    """
    path = shutil.which(base_name)
    if path:
        return path

    # Try versioned names in descending order (prefer newest)
    for suffix in range(30, 13, -1):
        path = shutil.which(f"{base_name}-{suffix}")
        if path:
            return path

    return None


def _try_llvm_config() -> str | None:
    """Try to detect LLVM version via llvm-config --version."""
    llvm_config = _find_versioned_binary("llvm-config")
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


def _try_pkg_config() -> str | None:
    """Try to detect LLVM version via pkg-config.

    On Debian/Ubuntu with libclang-dev installed, pkg-config knows about clang.
    """
    pkg_config = shutil.which("pkg-config")
    if not pkg_config:
        return None

    for module in ("clang", "libclang"):
        try:
            result = subprocess.run(
                [pkg_config, "--modversion", module],
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
    clang = _find_versioned_binary("clang")
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


def _try_llvm_dir() -> str | None:
    """Try to detect LLVM version from /usr/lib/llvm-{N}/ directories.

    On Debian/Ubuntu, libclang-dev installs under /usr/lib/llvm-{major}/.
    This catches cases where no binaries or pkg-config are available.
    """
    if sys.platform != "linux":
        return None

    llvm_dirs = sorted(glob.glob("/usr/lib/llvm-*/"), reverse=True)
    for d in llvm_dirs:
        match = re.search(r"/llvm-(\d+)/", d)
        if match:
            return match.group(1)

    return None


def _try_homebrew_llvm() -> str | None:
    """Try to detect LLVM version from Homebrew's llvm installation on macOS.

    Homebrew installs llvm as a keg-only formula, so llvm-config is not
    in PATH by default. Check the Homebrew prefix directly.
    """
    if sys.platform != "darwin":
        return None

    brew = shutil.which("brew")
    if not brew:
        return None

    try:
        result = subprocess.run(
            [brew, "--prefix", "llvm"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            prefix = result.stdout.strip()
            llvm_config = os.path.join(prefix, "bin", "llvm-config")
            if os.path.isfile(llvm_config):
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
