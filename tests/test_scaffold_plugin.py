"""Tests for HeaderKit plugin, writer, and backend project scaffolding."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from headerkit._cli import main as cli_main
from headerkit.scaffold_plugin import (
    PluginScaffoldOptions,
    prompt_plugin_scaffold_options,
    scaffold_plugin,
)


class TestPluginScaffolding:
    """Test suite for extension project scaffolding."""

    def test_scaffold_writer_layout(self) -> None:
        opts = PluginScaffoldOptions(
            plugin_type="writer",
            name="headerkit-writer-zig",
            target="zig",
            author="Alice Developer",
            author_email="alice@example.com",
        )
        layout = scaffold_plugin(opts)

        expected_paths = {
            "pyproject.toml",
            "headerkit_writer_zig/__init__.py",
            "headerkit_writer_zig/writer.py",
            "tests/conftest.py",
            "tests/test_zig.py",
            "AGENTS.md",
            "README.md",
            "scripts/ci/lint.sh",
            "scripts/ci/test.sh",
            ".github/workflows/ci.yml",
        }
        actual_paths = {f.path for f in layout.files}
        assert expected_paths == actual_paths

        # Verify all python files are syntactically valid
        for f in layout.files:
            if f.path.endswith(".py"):
                ast.parse(f.content, filename=f.path)

        # Verify pyproject.toml is valid TOML with proper entry points
        pyproject_file = layout.get_file("pyproject.toml")
        assert pyproject_file is not None
        parsed_toml = tomllib.loads(pyproject_file.content)
        assert parsed_toml["project"]["name"] == "headerkit-writer-zig"
        entry_points = parsed_toml["project"]["entry-points"]
        assert "headerkit.writers" in entry_points
        assert entry_points["headerkit.writers"]["zig"] == "headerkit_writer_zig.writer"

        # Verify writer class implementation
        writer_file = layout.get_file("headerkit_writer_zig/writer.py")
        assert writer_file is not None
        assert "class ZigWriter(BaseWriter):" in writer_file.content
        assert 'name: str = "zig"' in writer_file.content
        assert 'default_extension: str = ".zig"' in writer_file.content
        assert "register_writer(ZigWriter())" in writer_file.content

        # Verify anti-tautology and quality in test stub
        test_file = layout.get_file("tests/test_zig.py")
        assert test_file is not None
        assert "assert True" not in test_file.content
        assert "assert_true(" not in test_file.content
        assert 'get_writer("zig")' in test_file.content
        assert "add_numbers" in test_file.content

        # Verify AGENTS.md content
        agents_file = layout.get_file("AGENTS.md")
        assert agents_file is not None
        assert "Zero Tautological Assertions" in agents_file.content
        assert "ZigWriter" in agents_file.content

    def test_scaffold_backend_layout(self) -> None:
        opts = PluginScaffoldOptions(
            plugin_type="backend",
            name="headerkit-backend-tree-sitter-custom",
            target="tree_sitter_custom",
        )
        layout = scaffold_plugin(opts)

        # Validate syntax
        for f in layout.files:
            if f.path.endswith(".py"):
                ast.parse(f.content, filename=f.path)

        pyproject_file = layout.get_file("pyproject.toml")
        assert pyproject_file is not None
        parsed_toml = tomllib.loads(pyproject_file.content)
        entry_points = parsed_toml["project"]["entry-points"]
        assert "headerkit.backends" in entry_points
        assert "tree_sitter_custom" in entry_points["headerkit.backends"]

        backend_file = layout.get_file(f"{opts.package_name}/backend.py")
        assert backend_file is not None
        assert "ParserBackend" in backend_file.content
        assert "register_backend(" in backend_file.content

        # Verify anti-regex rule documented in AGENTS.md
        agents_file = layout.get_file("AGENTS.md")
        assert agents_file is not None
        assert "Strict Prohibition Against Regex AST Extraction" in agents_file.content

    def test_scaffold_plugin_layout(self) -> None:
        opts = PluginScaffoldOptions(
            plugin_type="plugin",
            name="headerkit-plugin-transform",
            target="transform",
        )
        layout = scaffold_plugin(opts)

        for f in layout.files:
            if f.path.endswith(".py"):
                ast.parse(f.content, filename=f.path)

        pyproject_file = layout.get_file("pyproject.toml")
        assert pyproject_file is not None
        parsed_toml = tomllib.loads(pyproject_file.content)
        entry_points = parsed_toml["project"]["entry-points"]
        assert "headerkit.hooks" in entry_points

        plugin_file = layout.get_file(f"{opts.package_name}/plugin.py")
        assert plugin_file is not None
        assert '@hook("transform_unit"' in plugin_file.content

    def test_options_normalization_bare_names(self) -> None:
        # Bare name should expand based on plugin_type
        writer_opts = PluginScaffoldOptions(plugin_type="writer", name="swift")
        assert writer_opts.name == "headerkit-writer-swift"
        assert writer_opts.package_name == "headerkit_writer_swift"
        assert writer_opts.target == "swift"
        assert writer_opts.output_extension == ".swift"

        backend_opts = PluginScaffoldOptions(plugin_type="backend", name="ast_probe")
        assert backend_opts.name == "headerkit-backend-ast-probe"
        assert backend_opts.package_name == "headerkit_backend_ast_probe"
        assert backend_opts.target == "ast_probe"

        plugin_opts = PluginScaffoldOptions(plugin_type="plugin", name="copier")
        assert plugin_opts.name == "headerkit-plugin-copier"
        assert plugin_opts.package_name == "headerkit_plugin_copier"
        assert plugin_opts.target == "copier"

    def test_invalid_plugin_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported plugin_type"):
            PluginScaffoldOptions(plugin_type="invalid_type", name="test")

    def test_prompt_scaffold_non_interactive(self) -> None:
        opts = PluginScaffoldOptions(name="headerkit-writer-go", target="go")
        resolved = prompt_plugin_scaffold_options(opts, is_tty=False)
        assert resolved.target == "go"
        assert resolved.output_extension == ".go"

    def test_prompt_scaffold_interactive_mocked(self) -> None:
        mock_inputs = [
            "writer",  # type
            "rust",  # target
            "headerkit-writer-rust",  # name
            "HeaderKit Rust binding writer",  # desc
            "Bob Coder",  # author
            "bob@example.org",  # email
            ".rs",  # ext
        ]
        with patch("builtins.input", side_effect=mock_inputs):
            resolved = prompt_plugin_scaffold_options(is_tty=True)
            assert resolved.plugin_type == "writer"
            assert resolved.target == "rust"
            assert resolved.name == "headerkit-writer-rust"
            assert resolved.output_extension == ".rs"
            assert resolved.author == "Bob Coder"

    def test_cli_new_subcommand_writes_to_disk(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "scaffolded_writer"
        code = cli_main(
            [
                "new",
                "--type",
                "writer",
                "--name",
                "headerkit-writer-odin",
                "--target",
                "odin",
                "--no-input",
                "-o",
                str(out_dir),
            ]
        )
        assert code == 0
        assert (out_dir / "pyproject.toml").is_file()
        assert (out_dir / "headerkit_writer_odin" / "writer.py").is_file()
        assert (out_dir / "tests" / "test_odin.py").is_file()
        assert (out_dir / "AGENTS.md").is_file()

        # Check file contents on disk
        pyproject_text = (out_dir / "pyproject.toml").read_text(encoding="utf-8")
        assert "headerkit-writer-odin" in pyproject_text
        assert "headerkit.writers" in pyproject_text

        # Verify all scaffolded python files byte-compile cleanly
        import py_compile

        for py_file in out_dir.rglob("*.py"):
            py_compile.compile(str(py_file), doraise=True)

    def test_cli_new_plugin_alias(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "scaffolded_hook_plugin"
        code = cli_main(
            [
                "new-plugin",
                "--type",
                "plugin",
                "--name",
                "headerkit-plugin-linter",
                "--target",
                "linter",
                "--no-input",
                "-o",
                str(out_dir),
            ]
        )
        assert code == 0
        assert (out_dir / "pyproject.toml").is_file()
        assert (out_dir / "headerkit_plugin_linter" / "plugin.py").is_file()
