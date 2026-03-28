"""Tests for generate() and generate_all() API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from headerkit._generate import GenerateResult, generate, generate_all
from headerkit.ir import CType, Function, Header, Parameter


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Set up a project directory with .git and .hkcache."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture()
def header_file(project_dir: Path) -> Path:
    """Create a simple header file."""
    h = project_dir / "test.h"
    h.write_text("int add(int a, int b);")
    return h


class TestGenerateIRCacheMiss:
    """Test generate() when IR cache is empty (requires mock backend)."""

    def test_populates_ir_cache(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mock get_backend to avoid needing libclang
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        assert result  # non-empty output
        # IR cache should now exist
        ir_dir = project_dir / ".hkcache" / "ir"
        assert ir_dir.exists()
        # At least one entry should exist
        entries = [d for d in ir_dir.iterdir() if d.is_dir()]
        assert len(entries) >= 1


class TestGenerateIRCacheHit:
    """Test generate() when IR cache already has the entry."""

    def test_uses_cached_ir(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(
            str(header_file),
            [Function("add", CType("int"), [Parameter("a", CType("int"))])],
        )
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        # First call: populate cache
        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        # Reset mock to track second call
        mock_backend.parse.reset_mock()

        # Second call: should use cache
        result2 = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        assert result2  # non-empty
        mock_backend.parse.assert_not_called()  # should not parse again


class TestGenerateNoCache:
    """Test cache bypass flags."""

    def test_no_cache_skips_all(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(str(header_file), [])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
            no_cache=True,
        )

        # Cache dir should not have any IR entries
        ir_dir = project_dir / ".hkcache" / "ir"
        if ir_dir.exists():
            entries = [d for d in ir_dir.iterdir() if d.is_dir()]
            assert len(entries) == 0


class TestGenerateAll:
    """Test generate_all() with multiple writers."""

    def test_multiple_writers(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(str(header_file), [Function("f", CType("void"))])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        results = generate_all(
            header_path=header_file,
            writers=["json"],
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        assert len(results) >= 1
        assert all(isinstance(r, GenerateResult) for r in results)
        assert all(r.output for r in results)

    def test_parses_only_once(self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_header = Header(str(header_file), [Function("f", CType("void"))])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        generate_all(
            header_path=header_file,
            writers=["json"],
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
        )

        assert mock_backend.parse.call_count == 1


class TestGenerateOutputPath:
    """Test generate() with output_path parameter."""

    def test_writes_file_and_returns_output(
        self, project_dir: Path, header_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_header = Header(str(header_file), [Function("f", CType("void"))])
        mock_backend = MagicMock()
        mock_backend.parse.return_value = mock_header
        mock_backend.name = "libclang"

        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: mock_backend)

        out_file = project_dir / "output" / "bindings.json"
        result = generate(
            header_path=header_file,
            writer_name="json",
            backend_name="libclang",
            cache_dir=project_dir / ".hkcache",
            output_path=out_file,
        )

        # Returns the output string
        assert result
        assert isinstance(result, str)
        # AND writes the file
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == result


class TestGenerateFileNotFound:
    """Test error handling for missing header files."""

    def test_missing_header(self, project_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            generate(
                header_path=project_dir / "nonexistent.h",
                writer_name="json",
                cache_dir=project_dir / ".hkcache",
            )
