"""Tests for multi-dimensional reconciliation engine and Zero-Dual-System adapter."""

from __future__ import annotations

from headerkit.ir import CType, Function, SourceUnit
from headerkit.matrix.models import ConfigCondition
from headerkit.reconcile import (
    MatrixCondition,
    MatrixGuardedBlock,
    MultiDimensionalReconciledUnit,
    reconcile_matrix,
    reconcile_units,
)


def test_reconcile_matrix_monotonic_alignment() -> None:
    common_start = Function(name="init", return_type=CType("void"))
    divergent_a = Function(name="win_only", return_type=CType("void"))
    divergent_b = Function(name="posix_only", return_type=CType("void"))
    common_end = Function(name="shutdown", return_type=CType("void"))

    u1 = SourceUnit(path="lib.h", declarations=[common_start, divergent_a, common_end])
    u2 = SourceUnit(path="lib.h", declarations=[common_start, divergent_b, common_end])

    c1 = ConfigCondition({"target": "windows"})
    c2 = ConfigCondition({"target": "linux"})

    reconciled = reconcile_matrix([(c1, u1), (c2, u2)], target_lang="nim")

    assert isinstance(reconciled, MultiDimensionalReconciledUnit)
    assert len(reconciled.items) == 3
    assert reconciled.items[0] == common_start
    mid = reconciled.items[1]
    assert isinstance(mid, MatrixGuardedBlock)
    assert len(mid.branches) == 2
    assert isinstance(mid.branches[0][0], MatrixCondition)
    assert mid.branches[0][1] == [divergent_a]
    assert isinstance(mid.branches[1][0], MatrixCondition)
    assert mid.branches[1][1] == [divergent_b]
    assert reconciled.items[2] == common_end


def test_reconcile_units_zero_dual_system_delegation() -> None:
    f1 = Function(name="mac_fn", return_type=CType("void"))
    f2 = Function(name="linux_fn", return_type=CType("void"))

    u_mac = SourceUnit(path="t.h", declarations=[f1])
    u_linux = SourceUnit(path="t.h", declarations=[f2])

    rec = reconcile_units([("darwin", u_mac), ("linux", u_linux)])
    assert len(rec.items) == 1
    assert rec.items[0].branches[0][0].nim_predicate == "defined(macosx)"
