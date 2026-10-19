"""Tests for Quine-McCluskey boolean optimization, interval analysis, and platform deduplication."""

from __future__ import annotations

from headerkit.matrix.models import ConfigCondition
from headerkit.reconcile import (
    get_c_platform,
    get_nim_platform,
    get_py_platform,
    minimize_conditions,
    quine_mccluskey_minimize,
)


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

    pred_nim = minimize_conditions([c1, c2], domain, target_lang="nim")
    assert "(defined(linux) or defined(macosx))" in pred_nim
    assert "defined(A)" in pred_nim


def test_minimize_conditions_monotonic_version_suffix() -> None:
    c1 = ConfigCondition({"version": (2, 0)})
    c2 = ConfigCondition({"version": (2, 1)})
    c3 = ConfigCondition({"version": (2, 2)})
    domain = {"version": {(1, 0), (2, 0), (2, 1), (2, 2)}}

    pred = minimize_conditions([c1, c2, c3], domain, target_lang="nim")
    assert pred == "version >= (2, 0)"


def test_platform_target_mapping() -> None:
    assert get_nim_platform("x86_64-apple-darwin") == "macosx"
    assert get_nim_platform("x86_64-unknown-linux-gnu") == "linux"
    assert get_nim_platform("x86_64-pc-windows-msvc") == "windows"
    assert get_c_platform("x86_64-apple-darwin") == "__APPLE__"
    assert get_c_platform("x86_64-unknown-linux-gnu") == "__linux__"
    assert get_c_platform("x86_64-pc-windows-msvc") == "_WIN32"
    assert get_py_platform("x86_64-apple-darwin") == "darwin"
    assert get_py_platform("x86_64-unknown-linux-gnu") == "linux"
    assert get_py_platform("x86_64-pc-windows-msvc") == "win32"
