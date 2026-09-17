"""Tests for NimWriter.write_reconciled output."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from headerkit.ir import CType, Field, Function, Struct, Typedef
from headerkit.reconcile import (
    PlatformCondition,
    PlatformGuardedBlock,
    ReconciledUnit,
)
from headerkit.writers.nim import NimWriter


class TestNimWriterReconciled:
    """Tests for rendering ReconciledUnit to Nim source."""

    def test_renders_common_and_platform_guarded_branches(self) -> None:
        td = Typedef(name="CustomId", underlying_type=CType("int"))
        mac_cond = PlatformCondition(frozenset({"darwin"}), "defined(macosx)")
        linux_cond = PlatformCondition(frozenset({"linux"}), "defined(linux)")

        mac_struct = Struct(name="PlatformContext", fields=[Field(name="audio_unit", type=CType("pointer"))])
        linux_struct = Struct(name="PlatformContext", fields=[Field(name="alsa_handle", type=CType("pointer"))])

        block = PlatformGuardedBlock(
            branches=[
                (mac_cond, [mac_struct]),
                (linux_cond, [linux_struct]),
            ]
        )
        cleanup_func = Function(name="cleanup", return_type=CType("void"))

        unit = ReconciledUnit(path="platform.h", items=[td, block, cleanup_func])
        writer = NimWriter(header_path="platform.h")
        output = writer.write_reconciled(unit)

        assert "type" in output
        assert "CustomId* = cint" in output
        assert "when defined(macosx):" in output
        assert 'PlatformContext* {.importc: "struct PlatformContext", header: "platform.h", bycopy.} = object' in output
        assert "audio_unit*: pointer" in output
        assert "elif defined(linux):" in output
        assert "alsa_handle*: pointer" in output
        assert 'proc cleanup*() {.importc: "cleanup", header: "platform.h", cdecl.}' in output

    def test_reconciled_nim_code_compiles_under_active_host(self) -> None:
        if not shutil.which("nim"):
            pytest.skip("nim compiler is not installed on PATH")

        with tempfile.TemporaryDirectory() as td:
            h_path = Path(td) / "audio.h"
            h_path.write_text(
                "#ifdef __APPLE__\n"
                "typedef struct { void *queue; } AudioEngine;\n"
                "#else\n"
                "typedef struct { int pcm_fd; } AudioEngine;\n"
                "#endif\n"
                "void audio_init(AudioEngine *eng);\n"
            )

            mac_cond = PlatformCondition(frozenset({"darwin"}), "defined(macosx)")
            linux_cond = PlatformCondition(frozenset({"linux"}), "defined(linux)")

            mac_struct = Struct(name="AudioEngine", fields=[Field(name="queue", type=CType("pointer"))])
            linux_struct = Struct(name="AudioEngine", fields=[Field(name="pcm_fd", type=CType("int"))])

            block = PlatformGuardedBlock(
                branches=[
                    (mac_cond, [mac_struct]),
                    (linux_cond, [linux_struct]),
                ]
            )
            init_func = Function(
                name="audio_init",
                return_type=CType("void"),
            )

            unit = ReconciledUnit(path=str(h_path), items=[block, init_func])
            writer = NimWriter(header_path=str(h_path))
            nim_code = writer.write_reconciled(unit)

            nim_file = Path(td) / "test_reconciled.nim"
            nim_file.write_text(
                nim_code
                + "\n\n"
                + "var eng: AudioEngine\n"
                + "when defined(macosx):\n"
                + "  eng.queue = nil\n"
                + "elif defined(linux):\n"
                + "  eng.pcm_fd = 0\n"
            )

            cmd = ["nim", "check", "--hints:off", str(nim_file)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0, f"nim check failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
