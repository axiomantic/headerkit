"""Target triple detection and resolution for headerkit.

Provides functions to detect the current process's target triple,
normalize user-provided triples, and resolve the effective target
using the standard headerkit config precedence.

Auto-detection uses the most direct signal per platform:

- **POSIX** (Linux, macOS, BSDs):
  ``sysconfig.get_config_var('HOST_GNU_TYPE')`` -- the ``--host``
  value from autoconf, baked into the Python build at compile time.
- **Windows**: Parses ``sysconfig.get_platform()``
  (e.g., ``win-amd64``, ``win32``).

On pre-3.13 Linux, a musl libc sniff corrects ``linux-gnu`` to
``linux-musl`` when the running Python is linked against musl.

For cross-compilation, set ``--target``, ``HEADERKIT_TARGET``, or
``[tool.headerkit] target`` explicitly.
"""

from __future__ import annotations

import os
import platform as platform_mod
import re
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

# Known OS names for short_target() disambiguation.
_OS_NAMES = frozenset({"linux", "darwin", "windows", "freebsd", "openbsd", "netbsd", "wasi", "none"})

# Windows sysconfig.get_platform() arch suffix to canonical arch.
_WINDOWS_ARCH: dict[str, str] = {
    "amd64": "x86_64",
    "arm64": "aarch64",
    "x86": "i686",
}

# Architecture aliases to canonical forms.
_ARCH_ALIASES: dict[str, str] = {
    "amd64": "x86_64",
    "x86-64": "x86_64",
    "x64": "x86_64",
    "armv8": "aarch64",
    "armv8a": "aarch64",
    "armv8-a": "aarch64",
    "x86": "i686",
    "i386": "i686",
    "i486": "i686",
    "i586": "i686",
    "riscv": "riscv64",
    "wasm": "wasm32",
    "webassembly": "wasm32",
}

# Known vendors recognized in triples.
_KNOWN_VENDORS = frozenset({"pc", "apple", "unknown", "none", "ibm", "scei", "w64"})

# 64-bit architectures
_64_BIT_ARCHES = frozenset(
    {
        "x86_64",
        "aarch64",
        "arm64",
        "riscv64",
        "wasm64",
        "s390x",
        "ppc64",
        "ppc64le",
        "mips64",
        "mips64el",
        "loongarch64",
        "sparc64",
    }
)

# 32-bit architectures
_32_BIT_ARCHES = frozenset(
    {
        "i686",
        "i386",
        "arm",
        "armv7",
        "armv7a",
        "armv7l",
        "armv7m",
        "armv7em",
        "armv6",
        "thumbv7m",
        "thumbv7em",
        "riscv32",
        "wasm32",
        "mips",
        "mipsel",
        "ppc",
        "sparc",
    }
)


@dataclass(frozen=True)
class TargetTriple:
    """Represents a normalized, structured compilation target triple."""

    arch: str
    vendor: str
    os: str
    env: str | None = None

    def __str__(self) -> str:
        if self.env:
            return f"{self.arch}-{self.vendor}-{self.os}-{self.env}"
        return f"{self.arch}-{self.vendor}-{self.os}"

    @property
    def is_windows(self) -> bool:
        """True if targeting Windows."""
        return self.os == "windows"

    @property
    def is_darwin(self) -> bool:
        """True if targeting macOS / Darwin."""
        return self.os == "darwin" or self.os.startswith("darwin")

    @property
    def is_linux(self) -> bool:
        """True if targeting Linux."""
        return self.os == "linux"

    @property
    def is_musl(self) -> bool:
        """True if targeting Linux with musl libc."""
        return self.is_linux and self.env == "musl"

    @property
    def is_wasm(self) -> bool:
        """True if targeting WebAssembly."""
        return self.arch.startswith("wasm") or self.os == "wasi"

    @property
    def is_embedded(self) -> bool:
        """True if targeting bare-metal / embedded platforms."""
        return self.vendor == "none" or self.os == "none" or (self.env is not None and "eabi" in self.env)

    @property
    def pointer_width(self) -> int:
        """Pointer width in bytes (8 for 64-bit, 4 for 32-bit, 2 for 16-bit)."""
        if self.arch in _64_BIT_ARCHES:
            return 8
        if self.arch in _32_BIT_ARCHES:
            return 4
        return 8 if "64" in self.arch else 4

    @property
    def is_64_bit(self) -> bool:
        """True if the target architecture is 64-bit."""
        return self.pointer_width == 8

    @property
    def is_32_bit(self) -> bool:
        """True if the target architecture is 32-bit."""
        return self.pointer_width == 4

    @classmethod
    def parse(cls, raw: str, *, allow_shorthand: bool = True) -> TargetTriple:
        """Parse a target triple string into a structured TargetTriple.

        Supports standard 3- and 4-component triples as well as common
        2-component shorthands when ``allow_shorthand=True``.
        """
        s = raw.strip().lower().rstrip("-")
        parts = s.split("-")

        if len(parts) == 1:
            if allow_shorthand:
                if parts[0] == "win64":
                    return cls(arch="x86_64", vendor="pc", os="windows", env="msvc")
                if parts[0] == "win32":
                    return cls(arch="i686", vendor="pc", os="windows", env="msvc")
                if parts[0] == "wasm32":
                    return cls(arch="wasm32", vendor="unknown", os="unknown", env=None)
            raise ValueError(
                f"Invalid target triple {raw!r}: expected at least 3 "
                f"hyphen-separated components (arch-vendor-os or arch-os-env)"
            )

        if len(parts) == 2:
            if not allow_shorthand:
                raise ValueError(
                    f"Invalid target triple {raw!r}: expected at least 3 "
                    f"hyphen-separated components (arch-vendor-os or arch-os-env)"
                )
            p0, p1 = parts[0], parts[1]
            if p1 in ("linux", "gnu"):
                arch = _ARCH_ALIASES.get(p0, p0)
                if arch == "arm64":
                    arch = "aarch64"
                return cls(arch=arch, vendor="unknown", os="linux", env="gnu")
            if p1 == "musl":
                arch = _ARCH_ALIASES.get(p0, p0)
                if arch == "arm64":
                    arch = "aarch64"
                return cls(arch=arch, vendor="unknown", os="linux", env="musl")
            if p1 in ("darwin", "macos", "osx"):
                arch = p0 if p0 in ("arm64", "aarch64") else _ARCH_ALIASES.get(p0, p0)
                return cls(arch=arch, vendor="apple", os="darwin", env=None)
            if p1 in ("windows", "win", "win32", "win64"):
                arch = _WINDOWS_ARCH.get(p0, _ARCH_ALIASES.get(p0, p0))
                return cls(arch=arch, vendor="pc", os="windows", env="msvc")
            if p1 == "wasi":
                return cls(arch=p0, vendor="unknown", os="wasi", env=None)
            if p0.startswith("wasm"):
                return cls(arch=p0, vendor="unknown", os=p1, env=None)
            raise ValueError(f"Invalid shorthand target triple {raw!r}: unrecognized target pair ({p0}, {p1})")

        if len(parts) == 3:
            if parts[1] == "none" and ("eabi" in parts[2] or parts[2] in ("elf", "unknown")):
                return cls(arch=parts[0], vendor="none", os="none", env=parts[2])

            if parts[1] == "apple" and parts[2].startswith("darwin"):
                return cls(arch=parts[0], vendor="apple", os=parts[2], env=None)

            if parts[1] in _KNOWN_VENDORS:
                return cls(arch=parts[0], vendor=parts[1], os=parts[2], env=None)

            arch = parts[0]
            if parts[1] == "linux" and arch == "arm64":
                arch = "aarch64"
            return cls(arch=arch, vendor="unknown", os=parts[1], env=parts[2])

        arch = parts[0]
        vendor = parts[1]
        os_name = parts[2]
        env = "-".join(parts[3:])
        if os_name == "linux" and arch == "arm64":
            arch = "aarch64"
        return cls(arch=arch, vendor=vendor, os=os_name, env=env)


def parse_triple(triple: str, *, allow_shorthand: bool = True) -> TargetTriple:
    """Parse a target triple string into a structured TargetTriple instance."""
    return TargetTriple.parse(triple, allow_shorthand=allow_shorthand)


def _is_musl_linux() -> bool:
    """Detect if the running Python process is linked against musl libc.

    Uses ``os.confstr('CS_GNU_LIBC_VERSION')`` which returns a version
    string on glibc (e.g., ``'glibc 2.35'``) and raises ``ValueError``
    or ``OSError`` on non-glibc systems. This is process-aware: it
    checks what THIS interpreter links against, not what libraries are
    installed on the system.

    :returns: True if on Linux and not linked against glibc (i.e., musl).
    """
    if sys.platform != "linux":
        return False
    try:
        os.confstr("CS_GNU_LIBC_VERSION")
        return False  # glibc responds
    except (ValueError, OSError):
        return True  # not glibc, on Linux = musl
    except AttributeError:
        return False  # os.confstr not available


def detect_process_triple() -> str:
    """Detect the target triple for the current Python process.

    Uses the most direct available signal:

    - **POSIX**: ``HOST_GNU_TYPE`` from sysconfig -- the ``--host``
      value set by autoconf when this Python was built. This is
      inherently process-aware (a 32-bit Python build has a 32-bit
      ``HOST_GNU_TYPE``). On pre-3.13 Linux where ``HOST_GNU_TYPE``
      may incorrectly report ``gnu`` on musl systems, a runtime libc
      sniff corrects it.
    - **Windows**: ``sysconfig.get_platform()`` -- returns ``win-amd64``,
      ``win32``, or ``win-arm64``.

    For cross-compilation, use ``--target``, ``HEADERKIT_TARGET``,
    or ``[tool.headerkit] target`` instead of relying on auto-detection.

    :returns: Target triple string (e.g., ``aarch64-apple-darwin``,
        ``x86_64-pc-linux-gnu``, ``x86_64-pc-windows-msvc``).
    """
    # POSIX: HOST_GNU_TYPE is the triple this Python was built for.
    # Set by autoconf's AC_CANONICAL_HOST at Python build time.
    # Includes vendor, OS, and libc flavor (on 3.13+).
    host_gnu: str | None = sysconfig.get_config_var("HOST_GNU_TYPE")
    if host_gnu:
        triple = host_gnu.strip().lower()
        # Pre-3.13 CPython may report linux-gnu on musl systems
        # (CPython issue #87278, fixed in 3.13 via #95855).
        # Correct using a runtime libc sniff.
        if "linux-gnu" in triple and _is_musl_linux():
            triple = triple.replace("linux-gnu", "linux-musl", 1)
        return triple

    # Windows: HOST_GNU_TYPE is not available (no autoconf).
    # Parse sysconfig.get_platform() which returns win-amd64, win32, etc.
    plat = sysconfig.get_platform().lower()
    if plat == "win32":
        return "i686-pc-windows-msvc"
    if plat.startswith("win"):
        parts = plat.split("-")
        if len(parts) >= 2:
            raw_arch = parts[-1]
            arch = _WINDOWS_ARCH.get(raw_arch, raw_arch)
            return f"{arch}-pc-windows-msvc"

    # Fallback: construct best-effort triple from Python platform info.
    # This path should rarely execute -- it covers non-autoconf POSIX
    # builds and any unrecognized Windows platform tags.
    arch = platform_mod.machine().lower()
    return f"{arch}-unknown-{sys.platform}"


def detect_cross_compiler_target() -> str | None:
    """Detect a cross-compilation target triple from environment signals.

    Checks:
    1. ``CARGO_BUILD_TARGET`` (e.g., from maturin / cross-compilation setups)
    2. ``LLVM_TARGET_TRIPLE`` / ``CLANG_TARGET``
    3. ``CROSS_COMPILE`` (GCC/GNU prefix like ``aarch64-linux-gnu-``)
    4. ``CC`` / ``CXX`` compiler binary names (e.g., ``/usr/bin/aarch64-linux-gnu-gcc``)

    :returns: Canonical target triple string, or None if no cross-compilation target detected.
    """
    # 1. Cargo build target
    cargo_target = os.environ.get("CARGO_BUILD_TARGET")
    if cargo_target:
        try:
            return normalize_triple(cargo_target, allow_shorthand=True)
        except ValueError:
            pass

    # 2. LLVM target triple
    llvm_target = os.environ.get("LLVM_TARGET_TRIPLE") or os.environ.get("CLANG_TARGET")
    if llvm_target:
        try:
            return normalize_triple(llvm_target, allow_shorthand=True)
        except ValueError:
            pass

    # 3. CROSS_COMPILE prefix
    cross_prefix = os.environ.get("CROSS_COMPILE")
    if cross_prefix:
        cleaned = cross_prefix.strip().rstrip("-")
        if cleaned:
            try:
                return normalize_triple(cleaned, allow_shorthand=True)
            except ValueError:
                pass

    # 4. CC / CXX executable
    for env_key in ("CC", "CXX"):
        compiler = os.environ.get(env_key)
        if not compiler:
            continue
        name = Path(compiler).name.split("\\")[-1]
        m = re.match(
            r"^([a-zA-Z0-9_.-]+-[a-zA-Z0-9_.-]+-[a-zA-Z0-9_.-]+(?:-[a-zA-Z0-9_.-]+)?)-(?:gcc|g\+\+|clang|clang\+\+)(?:-[0-9.]+)?(?:\.exe)?$",
            name,
            re.IGNORECASE,
        )
        if m:
            prefix = m.group(1)
            try:
                return normalize_triple(prefix, allow_shorthand=True)
            except ValueError:
                pass

    return None


def normalize_triple(triple: str, *, allow_shorthand: bool = False) -> str:
    """Normalize a user-provided target triple to canonical form.

    Applied only to user input (``--target``, ``HEADERKIT_TARGET``,
    config file). Auto-detected triples from :func:`detect_process_triple`
    are already canonical and do not pass through this function.

    - Lowercases all components.
    - Inserts ``unknown`` vendor for 3-component triples missing the
      vendor (e.g., ``x86_64-linux-gnu`` -> ``x86_64-unknown-linux-gnu``).
    - Optionally expands 2-component shorthands when ``allow_shorthand=True``.

    :param triple: Raw triple string.
    :param allow_shorthand: Whether to accept 2-component shorthands.
    :returns: Normalized canonical triple string.
    :raises ValueError: If triple has fewer than 3 components and allow_shorthand is False.
    """
    return str(TargetTriple.parse(triple, allow_shorthand=allow_shorthand))


def resolve_target(
    *,
    target: str | None = None,
    project_root: Path | None = None,
    allow_cross_env: bool = True,
) -> str:
    """Resolve the effective target triple with config precedence.

    Precedence (highest to lowest):

    1. *target* kwarg (explicit API parameter)
    2. ``HEADERKIT_TARGET`` environment variable
    3. ``[tool.headerkit] target`` in pyproject.toml
    4. Cross-compilation environment signals (``CARGO_BUILD_TARGET``,
       ``LLVM_TARGET_TRIPLE``, ``CROSS_COMPILE``, ``CC``/``CXX`` cross-prefix)
    5. :func:`detect_process_triple` (auto-detect host process)

    User-provided triples (sources 1-3) are normalized via
    :func:`normalize_triple`.

    :param target: Explicit target triple (highest precedence).
    :param project_root: Project root for config file lookup.
    :param allow_cross_env: Whether to check cross-compilation environment signals.
    :returns: Resolved target triple.
    """
    # 1. Explicit kwarg
    if target is not None:
        return normalize_triple(target, allow_shorthand=True)

    # 2. Environment variable
    env_val = os.environ.get("HEADERKIT_TARGET")
    if env_val:
        return normalize_triple(env_val, allow_shorthand=True)

    # 3. Config file
    if project_root is not None:
        config_target = _read_target_from_config(project_root)
        if config_target is not None:
            return normalize_triple(config_target, allow_shorthand=True)

    # 4. Cross-compilation environment
    if allow_cross_env:
        cross_val = detect_cross_compiler_target()
        if cross_val is not None:
            return cross_val

    # 5. Auto-detect host process
    return detect_process_triple()


def _read_target_from_config(project_root: Path) -> str | None:
    """Read target from pyproject.toml [tool.headerkit] section.

    :param project_root: Project root directory.
    :returns: Target string or None if not configured.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        from headerkit._config import _parse_toml

        raw = _parse_toml(pyproject.read_bytes())
    except (ImportError, OSError, ValueError, RuntimeError):
        return None

    tool = raw.get("tool", {})
    if not isinstance(tool, dict):
        return None
    hk = tool.get("headerkit", {})
    if not isinstance(hk, dict):
        return None
    target_val = hk.get("target")
    if isinstance(target_val, str):
        return target_val
    return None


def short_target(triple: str) -> str:
    """Extract arch and OS for human-readable slug components.

    Handles both 3-component (``aarch64-apple-darwin``) and
    4-component (``x86_64-pc-linux-gnu``) triples by identifying
    the OS component positionally: in a 4+ component triple it is
    ``parts[2]``; in a 3-component triple it is ``parts[1]`` if
    it looks like an OS, otherwise ``parts[2]``.

    Examples::

        >>> short_target("x86_64-pc-linux-gnu")
        'x86_64-linux'
        >>> short_target("aarch64-apple-darwin")
        'aarch64-darwin'
        >>> short_target("x86_64-pc-windows-msvc")
        'x86_64-windows'
        >>> short_target("x86_64-linux-gnu")
        'x86_64-linux'

    :param triple: Target triple.
    :returns: Short ``arch-os`` string.
    """
    try:
        t = TargetTriple.parse(triple, allow_shorthand=True)
        # Strip version suffixes (e.g., darwin25.3.0 -> darwin, freebsd14.0 -> freebsd)
        os_part = re.sub(r"[0-9.]+$", "", t.os) or t.os
        return f"{t.arch}-{os_part}"
    except ValueError:
        parts = triple.split("-")
        arch = parts[0]
        if len(parts) >= 4:
            os_part = parts[2]
        elif len(parts) == 3:
            p1_base = parts[1].rstrip("0123456789.").rstrip("-") or parts[1]
            os_part = parts[1] if p1_base in _OS_NAMES else parts[2]
        else:
            os_part = parts[-1]
        os_part = re.sub(r"[0-9.]+$", "", os_part) or os_part
        return f"{arch}-{os_part}"
