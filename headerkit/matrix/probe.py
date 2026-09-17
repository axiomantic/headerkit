"""Target probing and compiler evaluation engine."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

from headerkit.backends import get_backend
from headerkit.ir import SourceUnit
from headerkit.matrix.models import ConfigCondition


class SysrootWarning(UserWarning):
    """Warning emitted when cross-compiling or probing without complete target sysroots."""


@dataclass(frozen=True)
class ProbeOptions:
    """Options governing cross-target compiler probing."""

    sysroot: str | None = None
    extra_include_dirs: tuple[str, ...] = ()
    allow_missing_sysroot: bool = True
    compiler_flags: tuple[str, ...] = ()


def probe_configuration(
    header_path: str,
    condition: ConfigCondition,
    options: ProbeOptions | None = None,
) -> SourceUnit:
    """Invokes libclang backend with target flags and catches sysroot diagnostics."""
    opts = options or ProbeOptions()
    flags = list(condition.to_compiler_flags())

    if opts.sysroot:
        flags.append(f"--sysroot={opts.sysroot}")
    for inc in opts.extra_include_dirs:
        flags.extend(["-isystem", inc])
    flags.extend(opts.compiler_flags)

    target = condition.get("target")
    if target and ("msvc" in target or "windows" in target) and not opts.sysroot:
        if opts.allow_missing_sysroot:
            warnings.warn(
                f"Probing foreign target '{target}' without sysroot; using intrinsic fallback.",
                category=SysrootWarning,
                stacklevel=2,
            )
            flags.extend(["-nobuiltininc", "-nostdinc"])

    backend = get_backend("libclang")
    code = Path(header_path).read_text(encoding="utf-8")
    return backend.parse(code, filename=header_path, extra_args=flags)
