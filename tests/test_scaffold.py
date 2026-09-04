"""Tests for project scaffolding, BYOScaffolder, and unified layout engine."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from headerkit.hooks import HookRegistry, Priority, hook
from headerkit.ir import CType, Function, Header, Parameter
from headerkit.scaffold import (
    BYOScaffolder,
    OutputFile,
    ProjectLayout,
    ScaffoldOptions,
    prompt_scaffold_options,
    scaffold,
)


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = HookRegistry.snapshot()
    yield
    HookRegistry.restore(snapshot)


@pytest.fixture
def sample_unit() -> Header:
    fn = Function(
        name="compute_hash",
        return_type=CType("uint32_t"),
        parameters=[Parameter("seed", CType("uint32_t"))],
    )
    return Header(path="hasher.h", declarations=[fn])


class TestProjectLayout:
    def test_output_file_immutability(self) -> None:
        f = OutputFile(path="src/lib.py", content="x = 1")
        assert f.path == "src/lib.py"
        assert f.content == "x = 1"
        assert not f.is_executable

    def test_write_to_disk(self, tmp_path: Path) -> None:
        layout = ProjectLayout(
            files=[
                OutputFile(path="README.md", content="# My Lib"),
                OutputFile(path="src/lib.py", content="def foo(): pass"),
                OutputFile(path="bin/run.sh", content="#!/bin/sh\necho ok", is_executable=True),
            ]
        )
        written = layout.write_to_disk(tmp_path)
        assert len(written) == 3
        assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# My Lib"
        assert (tmp_path / "src/lib.py").read_text(encoding="utf-8") == "def foo(): pass"
        assert (tmp_path / "bin/run.sh").exists()
        assert (tmp_path / "bin/run.sh").stat().st_mode & 0o111

    def test_get_file(self) -> None:
        layout = ProjectLayout(
            files=[
                OutputFile(path="config.toml", content="key = 'val'"),
            ]
        )
        assert layout.get_file("config.toml") is not None
        assert layout.get_file("missing.toml") is None


class TestStdlibScaffolderNim:
    def test_nim_single_file_layout(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(package_name="hasher", target_language="nim", layout="file")
        layout = scaffold(sample_unit, opts)
        assert len(layout.files) == 1
        assert layout.files[0].path == "hasher.nim"
        assert "proc compute_hash*" in layout.files[0].content

    def test_nim_package_both_tests(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(
            package_name="hasher",
            target_language="nim",
            layout="package",
            test_type="both",
        )
        layout = scaffold(sample_unit, opts)

        paths = {f.path for f in layout.files}
        assert "hasher.nimble" in paths
        assert "src/hasher.nim" in paths
        assert "src/hasher/bindings.nim" in paths
        assert "nim.cfg" in paths
        assert "tests/test_tripwire.nim" in paths
        assert "tests/test_hasher.nim" in paths

        nimble_content = layout.get_file("hasher.nimble").content
        assert 'packageName   = "hasher"' in nimble_content

        tripwire_content = layout.get_file("tests/test_tripwire.nim").content
        assert "compute_hash" in tripwire_content
        assert "tripwire" in tripwire_content.lower()

    def test_nim_package_test_type_filtering(self, sample_unit: Header) -> None:
        opts_none = ScaffoldOptions(package_name="hasher", target_language="nim", layout="package", test_type="none")
        layout_none = scaffold(sample_unit, opts_none)
        paths_none = {f.path for f in layout_none.files}
        assert "tests/test_tripwire.nim" not in paths_none
        assert "tests/test_hasher.nim" not in paths_none

        opts_tripwire = ScaffoldOptions(
            package_name="hasher", target_language="nim", layout="package", test_type="tripwire"
        )
        layout_tw = scaffold(sample_unit, opts_tripwire)
        paths_tw = {f.path for f in layout_tw.files}
        assert "tests/test_tripwire.nim" in paths_tw
        assert "tests/test_hasher.nim" not in paths_tw


class TestStdlibScaffolderMojo:
    def test_mojo_package_layout(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(
            package_name="fastmath",
            target_language="mojo",
            layout="package",
            test_type="both",
        )
        layout = scaffold(sample_unit, opts)
        paths = {f.path for f in layout.files}

        assert "mojoproject.toml" in paths
        assert "src/fastmath/__init__.mojo" in paths
        assert "src/fastmath/bindings.mojo" in paths
        assert "tests/test_tripwire.mojo" in paths
        assert "tests/test_fastmath.mojo" in paths

        bindings = layout.get_file("src/fastmath/bindings.mojo").content
        assert "compute_hash" in bindings


class TestStdlibScaffolderPython:
    def test_ctypes_package_layout(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(
            package_name="hashkit",
            target_language="ctypes",
            layout="package",
            test_type="both",
        )
        layout = scaffold(sample_unit, opts)
        paths = {f.path for f in layout.files}

        assert "pyproject.toml" in paths
        assert "src/hashkit/__init__.py" in paths
        assert "src/hashkit/_bindings.py" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_bindings.py" in paths

        pyproject = layout.get_file("pyproject.toml").content
        assert 'name = "hashkit"' in pyproject

        tripwire = layout.get_file("tests/test_tripwire.py").content
        assert "pytest.mark.tripwire" in tripwire or "tripwire" in tripwire.lower()


class TestBYOScaffolderHook:
    def test_custom_scaffolder_plugin(self, sample_unit: Header) -> None:
        class CustomCopierScaffolder(BYOScaffolder):
            def scaffold(self, unit: Header, options: ScaffoldOptions) -> ProjectLayout:
                return ProjectLayout(
                    files=[
                        OutputFile(path="copier.generated", content=f"copier template for {options.package_name}"),
                    ]
                )

        custom = CustomCopierScaffolder()

        @hook("scaffold_project", priority=Priority.OVERRIDE)
        def custom_hook(
            unit: Header,
            options: ScaffoldOptions,
            _context: Any = None,
            **_kwargs: Any,
        ) -> ProjectLayout:
            return custom.scaffold(unit, options)

        opts = ScaffoldOptions(package_name="mycustom", target_language="nim", layout="package")
        layout = scaffold(sample_unit, opts)

        assert len(layout.files) == 1
        assert layout.files[0].path == "copier.generated"
        assert layout.files[0].content == "copier template for mycustom"


class TestTTYPromptWizard:
    def test_non_interactive_preserves_defaults(self) -> None:
        opts = ScaffoldOptions(package_name="default_pkg", target_language="nim")
        resolved = prompt_scaffold_options(opts, is_tty=False)
        assert resolved.package_name == "default_pkg"
        assert resolved.target_language == "nim"

    def test_interactive_wizard_reads_inputs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inputs = io.StringIO("custom_pkg\nmojo\npackage\nboth\n")
        monkeypatch.setattr("sys.stdin", inputs)

        resolved = prompt_scaffold_options(is_tty=True)
        assert resolved.package_name == "custom_pkg"
        assert resolved.target_language == "mojo"
        assert resolved.layout == "package"
        assert resolved.test_type == "both"


class TestCLIScaffolding:
    def test_cli_scaffold_package(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from headerkit._cli import main

        header = tmp_path / "math_lib.h"
        header.write_text("int add(int a, int b);\n", encoding="utf-8")
        out_dir = tmp_path / "nim_math"

        test_args = [
            "headerkit",
            str(header),
            "-w",
            "nim",
            "--layout",
            "package",
            "--package-name",
            "nim_math",
            "-o",
            f"nim:{out_dir}",
            "--no-input",
        ]
        monkeypatch.setattr("sys.argv", test_args)

        ret = main()
        assert ret == 0
        assert (out_dir / "nim_math.nimble").exists()
        assert (out_dir / "src/nim_math.nim").exists()
        assert (out_dir / "src/nim_math/bindings.nim").exists()
        assert (out_dir / "tests/test_tripwire.nim").exists()
        assert (out_dir / "tests/test_nim_math.nim").exists()
