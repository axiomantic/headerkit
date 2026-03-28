"""Integration tests for cache-check and cache-save CLI subcommands."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from headerkit._cache_cli import cache_check_main, cache_save_main
from headerkit.cache import compute_hash

_FAKE_VERSION = "0.8.4"


class TestParseWriterOptions:
    """Tests for _parse_writer_options internal helper."""

    def test_parses_single_key_value(self) -> None:
        """Single KEY=VALUE pair is parsed correctly."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["exclude=__.*"])
        assert result == {"exclude": "__.*"}

    def test_parses_multiple_key_values(self) -> None:
        """Multiple KEY=VALUE pairs are parsed correctly."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["exclude=__.*", "prefix=my_"])
        assert result == {"exclude": "__.*", "prefix": "my_"}

    def test_splits_on_first_equals(self) -> None:
        """VALUE containing = is preserved (split on first = only)."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["regex=a=b=c"])
        assert result == {"regex": "a=b=c"}

    def test_none_input_returns_none(self) -> None:
        """None input returns None."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(None)
        assert result is None

    def test_empty_list_returns_none(self) -> None:
        """Empty list returns None."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options([])
        assert result is None

    def test_malformed_option_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Malformed option (no =) is skipped with stderr warning."""
        from headerkit._cache_cli import _parse_writer_options

        result = _parse_writer_options(["noequalssign"])
        assert result is None
        captured = capsys.readouterr()
        assert captured.err == "headerkit cache: malformed --writer-option: 'noequalssign'; expected KEY=VALUE\n"


class TestCacheSaveCli:
    """Tests for the cache-save subcommand."""

    def test_saves_hash_sidecar_exits_0(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save without --writer creates sidecar and exits 0."""
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output}.hkcache (sidecar)\n"

    def test_saves_hash_embedded_with_writer_flag(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with --writer cffi uses embedded storage."""
        with patch("headerkit.cache.importlib.metadata.version", return_value=_FAKE_VERSION):
            exit_code = cache_save_main(
                [
                    str(sample_output),
                    "--header",
                    str(sample_header),
                    "--writer-name",
                    "cffi",
                    "--writer",
                    "cffi",
                ]
            )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output} (embedded)\n"

        # Verify exact embedded content
        with patch("headerkit.cache.importlib.metadata.version", return_value=_FAKE_VERSION):
            expected_hash = compute_hash(
                header_paths=[sample_header],
                writer_name="cffi",
            )
        content = sample_output.read_text(encoding="utf-8")
        expected = textwrap.dedent(f"""\
            # [headerkit-cache]
            # hash = "{expected_hash}"
            # version = "{_FAKE_VERSION}"
            # writer = "cffi"

            # generated bindings
        """)
        assert content == expected

    def test_save_missing_output_exits_1(
        self, sample_header: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with nonexistent output file exits 1."""
        missing = tmp_path / "missing.py"
        exit_code = cache_save_main(
            [
                str(missing),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache-save: Output not found: {missing}\n"

    def test_save_with_writer_options(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with --writer-option passes options to hash computation."""
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output}.hkcache (sidecar)\n"

    def test_save_with_extra_input(
        self,
        sample_header: Path,
        sample_output: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cache-save with --extra-input includes extra file in hash."""
        extra = tmp_path / "config.ini"
        extra.write_text("key=value\n", encoding="utf-8")
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--extra-input",
                str(extra),
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"saved: {sample_output}.hkcache (sidecar)\n"

    def test_save_unknown_writer_exits_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save with --writer pointing to unknown writer exits 1."""
        exit_code = cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer",
                "nonexistent_writer",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        # Writer registration order is non-deterministic across test runs,
        # so extract and sort the "Available:" list for comparison.
        assert captured.err.startswith("headerkit cache-save: Unknown writer: 'nonexistent_writer'. Available: ")
        prefix = "headerkit cache-save: Unknown writer: 'nonexistent_writer'. Available: "
        available_str = captured.err[len(prefix) :].strip()
        available = sorted(available_str.split(", "))
        assert available == ["cffi", "ctypes", "cython", "diff", "json", "lua", "prompt"]


class TestCacheCheckCli:
    """Tests for the cache-check subcommand."""

    def test_check_after_save_returns_0(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-save followed by cache-check returns exit 0 with up-to-date message."""
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        capsys.readouterr()  # Clear save output

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"up-to-date: {sample_output}\n"

    def test_check_stale_returns_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-check on file with no hash returns exit 1 with stale message."""
        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {sample_output} (reason: no stored hash)\n"

    def test_check_missing_output_returns_1(
        self, sample_header: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-check on missing output returns exit 1 with missing reason."""
        missing = tmp_path / "missing.py"
        exit_code = cache_check_main(
            [
                str(missing),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {missing} (reason: missing output)\n"

    def test_check_hash_mismatch_returns_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cache-check returns exit 1 when hash mismatches due to changed header."""
        # Save hash first
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        capsys.readouterr()

        # Modify header content
        sample_header.write_text("int different_function(void);\n", encoding="utf-8")

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {sample_output} (reason: hash mismatch)\n"

    def test_writer_option_round_trip(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--writer-option round-trips: save then check with same options returns 0."""
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        capsys.readouterr()

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == f"up-to-date: {sample_output}\n"

    def test_writer_option_mismatch_returns_1(
        self, sample_header: Path, sample_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Differing --writer-option between save and check produces stale."""
        cache_save_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=__.*",
            ]
        )
        capsys.readouterr()

        exit_code = cache_check_main(
            [
                str(sample_output),
                "--header",
                str(sample_header),
                "--writer-name",
                "cffi",
                "--writer-option",
                "exclude=foo",
            ]
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == f"stale: {sample_output} (reason: hash mismatch)\n"


class TestCliDispatch:
    """Tests for early dispatch in _cli.main."""

    def test_cache_check_dispatches(self) -> None:
        """'headerkit cache-check' dispatches to cache_check_main."""
        with patch("headerkit._cache_cli.cache_check_main", return_value=0) as mock_check:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache-check", "dummy.py", "--header", "dummy.h", "--writer-name", "cffi"],
            ):
                from headerkit._cli import main

                main()
            mock_check.assert_called_once_with(["dummy.py", "--header", "dummy.h", "--writer-name", "cffi"])

    def test_cache_save_dispatches(self) -> None:
        """'headerkit cache-save' dispatches to cache_save_main."""
        with patch("headerkit._cache_cli.cache_save_main", return_value=0) as mock_save:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache-save", "dummy.py", "--header", "dummy.h", "--writer-name", "cffi"],
            ):
                from headerkit._cli import main

                main()
            mock_save.assert_called_once_with(["dummy.py", "--header", "dummy.h", "--writer-name", "cffi"])

    def test_cache_status_dispatches(self) -> None:
        """'headerkit cache status' dispatches to cache_status_main."""
        with patch("headerkit._cache_cli.cache_status_main", return_value=0) as mock_status:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "status", "--cache-dir", "/tmp/cache"],
            ):
                from headerkit._cli import main

                main()
            mock_status.assert_called_once_with(["status", "--cache-dir", "/tmp/cache"])

    def test_cache_clear_dispatches(self) -> None:
        """'headerkit cache clear' dispatches to cache_clear_main."""
        with patch("headerkit._cache_cli.cache_clear_main", return_value=0) as mock_clear:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "clear", "--cache-dir", "/tmp/cache"],
            ):
                from headerkit._cli import main

                main()
            mock_clear.assert_called_once_with(["clear", "--cache-dir", "/tmp/cache"])

    def test_cache_rebuild_index_dispatches(self) -> None:
        """'headerkit cache rebuild-index' dispatches to cache_rebuild_index_main."""
        with patch("headerkit._cache_cli.cache_rebuild_index_main", return_value=0) as mock_rebuild:
            with patch.object(
                sys,
                "argv",
                ["headerkit", "cache", "rebuild-index", "--cache-dir", "/tmp/cache"],
            ):
                from headerkit._cli import main

                main()
            mock_rebuild.assert_called_once_with(["rebuild-index", "--cache-dir", "/tmp/cache"])


class TestCacheStatusSubcommand:
    """Tests for cache_status_main."""

    def test_empty_cache(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty cache directory shows zero counts."""
        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / ".hkcache"
        (cache_dir / "ir").mkdir(parents=True)
        (cache_dir / "output").mkdir(parents=True)
        exit_code = cache_status_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Cache directory: {cache_dir}
            IR entries: 0
            Output entries: 0
        """)

    def test_with_ir_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Cache with IR entries shows correct count."""
        import json

        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / ".hkcache"
        ir_dir = cache_dir / "ir"
        for name in ["libclang.test_a", "libclang.test_b"]:
            entry = ir_dir / name
            entry.mkdir(parents=True)
            (entry / "metadata.json").write_text(
                json.dumps({"cache_key": f"key_{name}", "created": "2026-01-01T00:00:00Z"}),
                encoding="utf-8",
            )
        (cache_dir / "output").mkdir(parents=True)
        exit_code = cache_status_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Cache directory: {cache_dir}
            IR entries: 2
            Output entries: 0
        """)

    def test_with_output_entries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Cache with output entries shows per-writer counts."""
        import json

        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / ".hkcache"
        (cache_dir / "ir").mkdir(parents=True)

        # Create cffi writer entries
        for name in ["entry1", "entry2"]:
            entry = cache_dir / "output" / "cffi" / name
            entry.mkdir(parents=True)
            (entry / "metadata.json").write_text(
                json.dumps({"cache_key": f"key_{name}", "created": "2026-01-01T00:00:00Z"}),
                encoding="utf-8",
            )

        # Create ctypes writer entry
        entry = cache_dir / "output" / "ctypes" / "entry1"
        entry.mkdir(parents=True)
        (entry / "metadata.json").write_text(
            json.dumps({"cache_key": "key_ct1", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        exit_code = cache_status_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Cache directory: {cache_dir}
            IR entries: 0
            Output entries: 3
              cffi: 2
              ctypes: 1
        """)

    def test_nonexistent_cache_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nonexistent cache directory reports error."""
        from headerkit._cache_cli import cache_status_main

        cache_dir = tmp_path / "nonexistent"
        exit_code = cache_status_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache status: cache directory not found: {cache_dir}\n"


class TestCacheClearSubcommand:
    """Tests for cache_clear_main."""

    def test_clear_all(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Clear all removes both IR and output entries."""
        import json

        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / ".hkcache"

        # Create IR entry
        ir_entry = cache_dir / "ir" / "libclang.test"
        ir_entry.mkdir(parents=True)
        (ir_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        (ir_entry / "ir.json").write_text("{}", encoding="utf-8")
        (cache_dir / "ir" / "index.json").write_text(
            json.dumps(
                {"version": 1, "entries": {"libclang.test": {"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}}}
            ),
            encoding="utf-8",
        )

        # Create output entry
        out_entry = cache_dir / "output" / "cffi" / "entry1"
        out_entry.mkdir(parents=True)
        (out_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        (out_entry / "output.py").write_text("# output", encoding="utf-8")
        (cache_dir / "output" / "cffi" / "index.json").write_text(
            json.dumps({"version": 1, "entries": {"entry1": {"cache_key": "def", "created": "2026-01-01T00:00:00Z"}}}),
            encoding="utf-8",
        )

        exit_code = cache_clear_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == "Cleared all cache entries.\n"

        # IR dir should exist but be empty (no subdirs)
        assert not any(p for p in (cache_dir / "ir").iterdir() if p.is_dir())
        # Output dir should exist but have no writer subdirs with entries
        assert not any(p for p in (cache_dir / "output").iterdir() if p.is_dir())

    def test_clear_ir_only(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--ir flag clears only IR entries, preserving output."""
        import json

        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / ".hkcache"

        # Create IR entry
        ir_entry = cache_dir / "ir" / "libclang.test"
        ir_entry.mkdir(parents=True)
        (ir_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        # Create output entry
        out_entry = cache_dir / "output" / "cffi" / "entry1"
        out_entry.mkdir(parents=True)
        (out_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        exit_code = cache_clear_main(["--ir", "--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == "Cleared IR cache entries.\n"

        # IR should be empty
        assert not any(p for p in (cache_dir / "ir").iterdir() if p.is_dir())
        # Output should still have entry
        assert (out_entry / "metadata.json").exists()

    def test_clear_output_only(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--output flag clears only output entries, preserving IR."""
        import json

        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / ".hkcache"

        # Create IR entry
        ir_entry = cache_dir / "ir" / "libclang.test"
        ir_entry.mkdir(parents=True)
        (ir_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        # Create output entry
        out_entry = cache_dir / "output" / "cffi" / "entry1"
        out_entry.mkdir(parents=True)
        (out_entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

        exit_code = cache_clear_main(["--output", "--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == "Cleared output cache entries.\n"

        # IR should still have entry
        assert (ir_entry / "metadata.json").exists()
        # Output should be empty
        assert not any(p for p in (cache_dir / "output").iterdir() if p.is_dir())

    def test_clear_nonexistent_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nonexistent cache directory reports error."""
        from headerkit._cache_cli import cache_clear_main

        cache_dir = tmp_path / "nonexistent"
        exit_code = cache_clear_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache clear: cache directory not found: {cache_dir}\n"


class TestCacheRebuildIndexSubcommand:
    """Tests for cache_rebuild_index_main."""

    def test_rebuild_ir_index(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Rebuilds IR index.json from metadata files."""
        import json

        from headerkit._cache_cli import cache_rebuild_index_main

        cache_dir = tmp_path / ".hkcache"
        ir_dir = cache_dir / "ir"
        entry = ir_dir / "libclang.test"
        entry.mkdir(parents=True)
        (entry / "metadata.json").write_text(
            json.dumps({"cache_key": "abc123", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        exit_code = cache_rebuild_index_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == textwrap.dedent(f"""\
            Rebuilt index: {ir_dir / "index.json"} (1 entries)
        """)

        index_data = json.loads((ir_dir / "index.json").read_text(encoding="utf-8"))
        assert index_data == {
            "entries": {
                "libclang.test": {
                    "cache_key": "abc123",
                    "created": "2026-01-01T00:00:00Z",
                },
            },
            "version": 1,
        }

    def test_rebuild_output_index(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Rebuilds output writer index.json from metadata files."""
        import json

        from headerkit._cache_cli import cache_rebuild_index_main

        cache_dir = tmp_path / ".hkcache"
        (cache_dir / "ir").mkdir(parents=True)
        writer_dir = cache_dir / "output" / "cffi"
        entry = writer_dir / "entry1"
        entry.mkdir(parents=True)
        (entry / "metadata.json").write_text(
            json.dumps({"cache_key": "def456", "created": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        exit_code = cache_rebuild_index_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        # IR index should also be rebuilt (0 entries), then output index
        assert captured.out == textwrap.dedent(f"""\
            Rebuilt index: {cache_dir / "ir" / "index.json"} (0 entries)
            Rebuilt index: {writer_dir / "index.json"} (1 entries)
        """)

        index_data = json.loads((writer_dir / "index.json").read_text(encoding="utf-8"))
        assert index_data == {
            "entries": {
                "entry1": {
                    "cache_key": "def456",
                    "created": "2026-01-01T00:00:00Z",
                },
            },
            "version": 1,
        }

    def test_rebuild_nonexistent_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nonexistent cache directory reports error."""
        from headerkit._cache_cli import cache_rebuild_index_main

        cache_dir = tmp_path / "nonexistent"
        exit_code = cache_rebuild_index_main(["--cache-dir", str(cache_dir)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err == f"headerkit cache rebuild-index: cache directory not found: {cache_dir}\n"
