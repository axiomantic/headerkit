"""Tests for Quine-McCluskey boolean optimization, interval analysis, and platform deduplication."""

from __future__ import annotations

from headerkit.matrix.models import ConfigCondition
from headerkit.reconcile import (
    canonical_platform_tag,
    minimize_conditions,
    quine_mccluskey_minimize,
)
from headerkit.writers.cffi import CffiWriter
from headerkit.writers.cshim import CShimWriter
from headerkit.writers.cython import CythonWriter
from headerkit.writers.nim import NimWriter


def test_quine_mccluskey_adjacent_minterm_merging() -> None:
    c1 = ConfigCondition({"A": True, "B": True})
    c2 = ConfigCondition({"A": True, "B": False})

    implicants = quine_mccluskey_minimize([c1, c2], ["A", "B"])
    assert len(implicants) == 1
    assert implicants[0] == (True, None)


def test_minimize_conditions_parenthesized_disjunction() -> None:
    c1 = ConfigCondition({"target": "x86_64-apple-darwin", "A": True})
    c2 = ConfigCondition({"target": "aarch64-unknown-linux-gnu", "A": True})
    domain = {
        "target": {"x86_64-apple-darwin", "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc"},
        "A": {True, False},
    }

    cond = minimize_conditions([c1, c2], domain)
    assert cond.platforms == ("darwin", "linux")
    assert cond.feature_implicants == ((("A", True),),)

    pred_nim = NimWriter().format_matrix_condition(cond)
    assert pred_nim == "(defined(linux) or defined(macosx)) and defined(A)"
    assert CythonWriter().format_matrix_condition(cond) == 'UNAME_SYSNAME in ("Darwin", "Linux") and defined(A)'
    assert CShimWriter().format_matrix_condition(cond) == "(defined(__APPLE__) || defined(__linux__)) && defined(A)"
    assert CffiWriter().format_matrix_condition(cond) == "sys.platform in ('darwin', 'linux') and A"


def test_minimize_conditions_monotonic_version_suffix() -> None:
    c1 = ConfigCondition({"version": (2, 0)})
    c2 = ConfigCondition({"version": (2, 1)})
    c3 = ConfigCondition({"version": (2, 2)})
    domain = {"version": {(1, 0), (2, 0), (2, 1), (2, 2)}}

    cond = minimize_conditions([c1, c2, c3], domain)
    assert cond.version_op == ">="
    assert cond.version_min == (2, 0)
    assert NimWriter().format_matrix_condition(cond) == "version >= (2, 0)"


def test_platform_target_mapping() -> None:
    assert canonical_platform_tag("x86_64-apple-darwin") == "darwin"
    assert canonical_platform_tag("x86_64-unknown-linux-gnu") == "linux"
    assert canonical_platform_tag("x86_64-pc-windows-msvc") == "windows"
    assert canonical_platform_tag("macosx") == "darwin"
    assert canonical_platform_tag("win32") == "windows"
