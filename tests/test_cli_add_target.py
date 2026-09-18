"""Tests for headerkit add-target CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest

from headerkit._cli import _build_parser, add_target_command
from headerkit.matrix.manifest import MatrixManifest, emit_manifest_comment
from headerkit.matrix.matrix import ConfigMatrix


def test_cli_parser_has_add_target() -> None:
    parser = _build_parser()
    args = parser.parse_args(["add-target", "output.nim", "--target", "x86_64-pc-windows-msvc"])
    assert args.command == "add-target"
    assert args.output_file == "output.nim"
    assert args.target == "x86_64-pc-windows-msvc"


def test_add_target_command_updates_manifest(tmp_path: Path) -> None:
    header_file = tmp_path / "audio.h"
    header_file.write_text("void play_sound(void);\n")

    matrix = ConfigMatrix()
    matrix.add_dimension("target", ["x86_64-apple-darwin"], dimension_type="target")
    manifest = MatrixManifest(
        schema_version=1,
        source_hash="hash123",
        header_path=str(header_file),
        matrix=matrix,
    )

    out_file = tmp_path / "audio.nim"
    initial_content = (
        emit_manifest_comment(manifest, language="nim")
        + """\
# @headerkit:override user_custom
proc customHelper*() = nil
# @headerkit:end-override user_custom

proc play_sound*() {.importc.}
"""
    )
    out_file.write_text(initial_content)

    parser = _build_parser()
    args = parser.parse_args(["add-target", str(out_file), "--target", "x86_64-unknown-linux-gnu"])
    exit_code = add_target_command(args)
    assert exit_code == 0

    updated_text = out_file.read_text()
    assert "proc customHelper*() = nil" in updated_text
    assert "x86_64-unknown-linux-gnu" in updated_text


def test_add_target_nonexistent_file_returns_error(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_parser()
    args = parser.parse_args(["add-target", "/non/existent/file.nim", "--target", "x86_64-unknown-linux-gnu"])
    exit_code = add_target_command(args)
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Error: target file not found" in err


def test_add_target_missing_manifest_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad_file = tmp_path / "plain.nim"
    bad_file.write_text("proc foo*() = nil\n")

    parser = _build_parser()
    args = parser.parse_args(["add-target", str(bad_file), "--target", "x86_64-unknown-linux-gnu"])
    exit_code = add_target_command(args)
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Error reading existing manifest" in err


def test_add_target_multidimensional_matrix(tmp_path: Path) -> None:
    header_file = tmp_path / "audio.h"
    header_file.write_text("#ifdef ENABLE_ALSA\nvoid play_alsa(void);\n#endif\n")

    matrix = ConfigMatrix()
    matrix.add_dimension("target", ["x86_64-apple-darwin"], dimension_type="target")
    matrix.add_dimension("ENABLE_ALSA", [True, False], dimension_type="feature")
    manifest = MatrixManifest(
        schema_version=1,
        source_hash="hash456",
        header_path=str(header_file),
        matrix=matrix,
    )

    out_file = tmp_path / "audio.nim"
    initial_content = emit_manifest_comment(manifest, language="nim") + "\n# initial\n"
    out_file.write_text(initial_content)

    parser = _build_parser()
    args = parser.parse_args(["add-target", str(out_file), "--target", "x86_64-unknown-linux-gnu"])
    exit_code = add_target_command(args)
    assert exit_code == 0

    updated_text = out_file.read_text()
    assert "x86_64-unknown-linux-gnu" in updated_text
    assert "ENABLE_ALSA" in updated_text
