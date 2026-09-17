"""Tests for headerkit add-target CLI command."""

from __future__ import annotations

from pathlib import Path

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
