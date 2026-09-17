"""Tests for topological AST reconciliation engine."""

from __future__ import annotations

from headerkit.ir import (
    CType,
    Field,
    Function,
    Header,
    Parameter,
    SourceLocation,
    Struct,
    Typedef,
)
from headerkit.reconcile import (
    PlatformCondition,
    PlatformGuardedBlock,
    ReconciledUnit,
    declarations_equal,
    reconcile_units,
)


class TestDeclarationsEqual:
    """Tests for structural equality ignoring source location."""

    def test_ignores_source_location(self) -> None:
        func1 = Function(
            name="add",
            return_type=CType("int"),
            parameters=[Parameter(name="a", type=CType("int")), Parameter(name="b", type=CType("int"))],
            location=SourceLocation(file="a.h", line=10, column=5),
        )
        func2 = Function(
            name="add",
            return_type=CType("int"),
            parameters=[Parameter(name="a", type=CType("int")), Parameter(name="b", type=CType("int"))],
            location=SourceLocation(file="b.h", line=42, column=1),
        )
        assert declarations_equal(func1, func2) is True

    def test_ignores_nested_source_locations_in_struct_methods(self) -> None:
        m1 = Function(
            name="process",
            return_type=CType("void"),
            location=SourceLocation(file="mac.h", line=12, column=4),
        )
        m2 = Function(
            name="process",
            return_type=CType("void"),
            location=SourceLocation(file="linux.h", line=88, column=2),
        )
        s1 = Struct(
            name="Engine",
            methods=[m1],
            location=SourceLocation(file="mac.h", line=10, column=1),
        )
        s2 = Struct(
            name="Engine",
            methods=[m2],
            location=SourceLocation(file="linux.h", line=85, column=1),
        )
        assert declarations_equal(s1, s2) is True

    def test_detects_structural_difference(self) -> None:
        func1 = Function(
            name="add",
            return_type=CType("int"),
            parameters=[Parameter(name="a", type=CType("int"))],
        )
        func2 = Function(
            name="add",
            return_type=CType("int"),
            parameters=[Parameter(name="a", type=CType("double"))],
        )
        assert declarations_equal(func1, func2) is False

    def test_different_declaration_types(self) -> None:
        func = Function(name="X", return_type=CType("void"))
        td = Typedef(name="X", underlying_type=CType("int"))
        assert declarations_equal(func, td) is False


class TestReconcileUnits:
    """Tests for multi-platform unit reconciliation."""

    def test_all_common_declarations(self) -> None:
        common_td = Typedef(name="Int32", underlying_type=CType("int"))
        common_func = Function(name="get_zero", return_type=CType("int"))

        u1 = Header(path="test.h", declarations=[common_td, common_func])
        u2 = Header(path="test.h", declarations=[common_td, common_func])

        reconciled = reconcile_units([("darwin", u1), ("linux", u2)])
        assert isinstance(reconciled, ReconciledUnit)
        assert len(reconciled.items) == 2
        assert isinstance(reconciled.items[0], Typedef)
        assert isinstance(reconciled.items[1], Function)

    def test_platform_guarded_divergent_declarations(self) -> None:
        mac_func = Function(name="mac_audio_init", return_type=CType("void"))
        linux_func = Function(name="alsa_audio_init", return_type=CType("void"))

        u_mac = Header(path="audio.h", declarations=[mac_func])
        u_linux = Header(path="audio.h", declarations=[linux_func])

        reconciled = reconcile_units([("darwin", u_mac), ("linux", u_linux)])
        assert isinstance(reconciled, ReconciledUnit)
        assert len(reconciled.items) == 1
        block = reconciled.items[0]
        assert isinstance(block, PlatformGuardedBlock)
        assert len(block.branches) == 2
        cond_mac, decls_mac = block.branches[0]
        cond_linux, decls_linux = block.branches[1]
        assert isinstance(cond_mac, PlatformCondition)
        assert cond_mac.nim_predicate == "defined(macosx)"
        assert decls_mac == [mac_func]
        assert cond_linux.nim_predicate == "defined(linux)"
        assert decls_linux == [linux_func]

    def test_same_name_different_layout_reconciled_into_guard(self) -> None:
        struct_linux = Struct(name="DeviceHandle", fields=[Field(name="fd", type=CType("int"))])
        struct_win = Struct(name="DeviceHandle", fields=[Field(name="hDevice", type=CType("pointer"))])

        u_linux = Header(path="dev.h", declarations=[struct_linux])
        u_win = Header(path="dev.h", declarations=[struct_win])

        reconciled = reconcile_units([("linux", u_linux), ("windows", u_win)])
        assert len(reconciled.items) == 1
        block = reconciled.items[0]
        assert isinstance(block, PlatformGuardedBlock)
        assert len(block.branches) == 2
        assert block.branches[0][0].nim_predicate == "defined(linux)"
        assert block.branches[0][1] == [struct_linux]
        assert block.branches[1][0].nim_predicate == "defined(windows)"
        assert block.branches[1][1] == [struct_win]

    def test_preserves_topological_sequence(self) -> None:
        td = Typedef(name="AudioFormat", underlying_type=CType("int"))
        mac_func = Function(name="mac_play", return_type=CType("void"))
        linux_func = Function(name="linux_play", return_type=CType("void"))
        common_cleanup = Function(name="audio_close", return_type=CType("void"))

        u_mac = Header(path="audio.h", declarations=[td, mac_func, common_cleanup])
        u_linux = Header(path="audio.h", declarations=[td, linux_func, common_cleanup])

        reconciled = reconcile_units([("darwin", u_mac), ("linux", u_linux)])
        assert len(reconciled.items) == 3
        assert isinstance(reconciled.items[0], Typedef)
        assert reconciled.items[0].name == "AudioFormat"
        assert isinstance(reconciled.items[1], PlatformGuardedBlock)
        assert isinstance(reconciled.items[2], Function)
        assert reconciled.items[2].name == "audio_close"
