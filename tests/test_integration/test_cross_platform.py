"""Integration tests for cross-platform preprocessor isolation, AST reconciliation, and Nim generation."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from headerkit.backends.libclang import LibclangBackend
from headerkit.ir import Function, Header, MacroFunction, Struct, Typedef
from headerkit.reconcile import PlatformGuardedBlock, ReconciledUnit, reconcile_units
from headerkit.writers.nim import NimWriter

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "cross_platform.h"


@pytest.mark.libclang
class TestCrossPlatformIntegration:
    """End-to-end tests parsing cross-platform headers with target triples and reconciling."""

    @pytest.fixture
    def parsed_platforms(self) -> tuple[Header, Header, Header]:
        backend = LibclangBackend()
        code = FIXTURE_PATH.read_text()

        u_mac = backend.parse(code, str(FIXTURE_PATH), extra_args=["-target", "arm64-apple-darwin"])
        u_linux = backend.parse(code, str(FIXTURE_PATH), extra_args=["-target", "x86_64-unknown-linux-gnu"])
        u_win = backend.parse(code, str(FIXTURE_PATH), extra_args=["-target", "x86_64-pc-windows-msvc"])
        return u_mac, u_linux, u_win

    def test_in_process_target_triples_isolate_preprocessor(
        self, parsed_platforms: tuple[Header, Header, Header]
    ) -> None:
        u_mac, u_linux, u_win = parsed_platforms

        mac_names = {getattr(d, "name", "") for d in u_mac.declarations}
        linux_names = {getattr(d, "name", "") for d in u_linux.declarations}
        win_names = {getattr(d, "name", "") for d in u_win.declarations}

        assert "mac_audio_start" in mac_names
        assert "linux_audio_start" not in mac_names
        assert "win_audio_start" not in mac_names

        assert "linux_audio_start" in linux_names
        assert "mac_audio_start" not in linux_names
        assert "win_audio_start" not in linux_names

        assert "win_audio_start" in win_names
        assert "mac_audio_start" not in win_names
        assert "linux_audio_start" not in win_names

    def test_reconciliation_topological_order(self, parsed_platforms: tuple[Header, Header, Header]) -> None:
        u_mac, u_linux, u_win = parsed_platforms
        reconciled = reconcile_units(
            [
                ("darwin", u_mac),
                ("linux", u_linux),
                ("windows", u_win),
            ]
        )

        assert isinstance(reconciled, ReconciledUnit)
        assert len(reconciled.items) == 9

        # Macros at top
        macro_names = [d.name for d in reconciled.items[:4] if isinstance(d, MacroFunction)]
        assert macro_names == ["MAKE_FLAG", "CALC_OFFSET", "SAFE_FREE", "CONCAT_SYM"]

        # Status typedef
        assert isinstance(reconciled.items[4], Typedef)
        assert reconciled.items[4].name == "Status"

        # Platform guarded block
        block = reconciled.items[5]
        assert isinstance(block, PlatformGuardedBlock)
        assert len(block.branches) == 3
        predicates = [cond.nim_predicate for cond, _ in block.branches]
        assert predicates == ["defined(macosx)", "defined(linux)", "defined(windows)"]

        # Trailing common struct and functions
        assert isinstance(reconciled.items[6], Struct)
        assert reconciled.items[6].name == "AppContext"
        assert isinstance(reconciled.items[7], Function)
        assert reconciled.items[7].name == "app_init"
        assert isinstance(reconciled.items[8], Function)
        assert reconciled.items[8].name == "app_shutdown"

    def test_nim_writer_renders_cross_platform_guards(self, parsed_platforms: tuple[Header, Header, Header]) -> None:
        u_mac, u_linux, u_win = parsed_platforms
        reconciled = reconcile_units(
            [
                ("darwin", u_mac),
                ("linux", u_linux),
                ("windows", u_win),
            ]
        )

        writer = NimWriter(header_path="cross_platform.h")
        nim_code = writer.write_reconciled(reconciled)

        # Macro bindings
        assert (
            'proc c_MAKE_FLAG*[T](bit: T): T {.importc: "MAKE_FLAG", header: "cross_platform.h", nodecl.}' in nim_code
        )
        assert (
            'proc c_CALC_OFFSET*[T1, T2](base: T1, idx: T2): T1 {.importc: "CALC_OFFSET", header: "cross_platform.h", nodecl.}'
            in nim_code
        )
        assert "template make_flag*(bit: untyped): untyped =" in nim_code
        assert (
            'proc c_SAFE_FREE*[T](p: T): void {.importc: "SAFE_FREE", header: "cross_platform.h", nodecl.}' in nim_code
        )
        assert "# Macro CONCAT_SYM skipped: contains preprocessor token-pasting (##) operator" in nim_code

        # Platform guarded blocks
        assert "when defined(macosx):" in nim_code
        assert "audio_queue*: pointer" in nim_code
        assert "proc mac_audio_start*(audio: ptr PlatformAudio): cint" in nim_code

        assert "elif defined(linux):" in nim_code
        assert "alsa_handle*: pointer" in nim_code
        assert "proc linux_audio_start*(audio: ptr PlatformAudio): cint" in nim_code

        assert "elif defined(windows):" in nim_code
        assert "wasapi_client*: pointer" in nim_code
        assert "proc win_audio_start*(audio: ptr PlatformAudio): cint" in nim_code

        # Common trailing declarations
        assert 'AppContext* {.importc: "AppContext", header: "cross_platform.h", bycopy.} = object' in nim_code
        assert "proc app_init*(ctx: ptr AppContext):" in nim_code
        assert "proc app_shutdown*(ctx: ptr AppContext)" in nim_code

    def test_reconciled_bindings_typecheck_on_host(self, parsed_platforms: tuple[Header, Header, Header]) -> None:
        if not shutil.which("nim"):
            pytest.skip("nim compiler not found on PATH")

        u_mac, u_linux, u_win = parsed_platforms
        reconciled = reconcile_units(
            [
                ("darwin", u_mac),
                ("linux", u_linux),
                ("windows", u_win),
            ]
        )

        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td)
            (src_dir / "cross_platform.h").write_text(FIXTURE_PATH.read_text())
            nim_file = src_dir / "bindings.nim"
            test_file = src_dir / "test_host.nim"

            writer = NimWriter(header_path="cross_platform.h")
            nim_code = writer.write_reconciled(reconciled)
            nim_file.write_text(nim_code)

            test_file.write_text("""
import bindings

var audio: PlatformAudio
when defined(macosx):
  audio.audio_queue = nil
  audio.sample_rate = 44100
  let res = mac_audio_start(addr audio)
elif defined(linux):
  audio.alsa_handle = nil
  let res = linux_audio_start(addr audio)
elif defined(windows):
  audio.wasapi_client = nil
  let res = win_audio_start(addr audio)

var ctx: AppContext
let init_res = app_init(addr ctx)
app_shutdown(addr ctx)
""")

            cmd = ["nim", "check", "--hints:off", str(test_file)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0, f"nim check failed on host:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    @pytest.mark.docker
    def test_reconciled_bindings_typecheck_in_docker(self, parsed_platforms: tuple[Header, Header, Header]) -> None:
        if not shutil.which("docker"):
            pytest.skip("docker CLI not found on PATH")

        # Verify docker daemon is alive and supports Linux containers
        try:
            probe = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pytest.skip("docker daemon is not responsive or not running")

        if probe.returncode != 0:
            pytest.skip("docker daemon is not running")
        if probe.stdout.strip().lower() != "linux":
            pytest.skip("docker daemon is not in Linux container mode")

        u_mac, u_linux, u_win = parsed_platforms
        reconciled = reconcile_units(
            [
                ("darwin", u_mac),
                ("linux", u_linux),
                ("windows", u_win),
            ]
        )

        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td)
            (src_dir / "cross_platform.h").write_text(FIXTURE_PATH.read_text())
            nim_file = src_dir / "bindings.nim"
            test_file = src_dir / "test_docker.nim"

            writer = NimWriter(header_path="cross_platform.h")
            nim_code = writer.write_reconciled(reconciled)
            nim_file.write_text(nim_code)

            test_file.write_text("""
import bindings

var audio: PlatformAudio
when defined(linux):
  audio.alsa_handle = nil
  audio.buffer_size = 1024
  let res = linux_audio_start(addr audio)
elif defined(macosx):
  audio.audio_queue = nil

var ctx: AppContext
let init_res = app_init(addr ctx)
app_shutdown(addr ctx)
""")

            cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{src_dir}:/workspace",
                "-w",
                "/workspace",
                "nimlang/nim:2.2.8",
                "nim",
                "check",
                "--hints:off",
                "test_docker.nim",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0, f"nim check failed in docker:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
