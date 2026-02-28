"""LLVM version detection for vendored clang bindings.

Detection strategy (in order):
1. CIR_CLANG_VERSION env var (explicit user override)
2. llvm-config --version (tries versioned names like llvm-config-18)
3. pkg-config --modversion clang (reliable on Linux with libclang-dev)
4. clang -dM -E -x c /dev/null to get __clang_major__ (tries clang-18 etc.)
5. Windows registry HKLM\\SOFTWARE\\LLVM\\LLVM (win32 only)
6. Windows Program Files scan (win32 only)
7. /usr/lib/llvm-{N}/ directory presence (Debian/Ubuntu, Linux only)
8. Homebrew llvm prefix (macOS only, for non-PATH brew installs)
9. Return None if all methods fail
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

    # Strategy 5: Windows registry (win32 only)
    version = _try_windows_registry()
    if version is not None:
        return version

    # Strategy 6: Windows Program Files (win32 only)
    version = _try_windows_program_files()
    if version is not None:
        return version

    # Strategy 7: /usr/lib/llvm-{N}/ directories (Linux)
    version = _try_llvm_dir()
    if version is not None:
        return version

    # Strategy 8: Homebrew llvm prefix (macOS)
    version = _try_homebrew_llvm()
    if version is not None:
        return version

    # Strategy 9: All methods failed
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


def _get_version_from_clang_exe(clang_exe: str) -> str | None:
    """Run clang.exe and extract the major version number."""
    try:
        result = subprocess.run(
            [clang_exe, "-dM", "-E", "-x", "c", "NUL"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(
                r"#define\s+__clang_major__\s+(\d+)",
                result.stdout,
            )
            if match:
                return match.group(1)
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _try_windows_registry() -> str | None:
    """Try to detect LLVM version from the Windows registry.

    The official LLVM installer writes its install directory to:
    HKLM\\SOFTWARE\\LLVM\\LLVM (default value)

    Since LLVM 16, llvm-config.exe is NOT included in the official
    Windows installer. Instead, we run clang.exe from the install dir
    to extract __clang_major__.
    """
    if sys.platform != "win32":
        return None

    try:
        import winreg
    except ImportError:
        return None

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\LLVM\LLVM",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            install_dir, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return None

    if not install_dir or not os.path.isdir(install_dir):
        return None

    # Run clang.exe -dM -E -x c NUL to extract __clang_major__
    clang_exe = os.path.join(install_dir, "bin", "clang.exe")
    if not os.path.isfile(clang_exe):
        return None

    return _get_version_from_clang_exe(clang_exe)


def _try_windows_program_files() -> str | None:
    """Try to detect LLVM version from Program Files directories.

    Checks standard LLVM installation paths using environment variables
    rather than hardcoded drive letters.
    """
    if sys.platform != "win32":
        return None

    candidates = []

    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(os.path.join(program_files, "LLVM", "bin", "clang.exe"))

    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    if program_files_x86:
        candidates.append(os.path.join(program_files_x86, "LLVM", "bin", "clang.exe"))

    for clang_exe in candidates:
        if not os.path.isfile(clang_exe):
            continue

        version = _get_version_from_clang_exe(clang_exe)
        if version is not None:
            return version

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
