"""Tests for scripts/vendor_clang.py."""

import re
import sys
import textwrap
from pathlib import Path

import pytest

# Import vendor_clang from scripts/ directory
_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import vendor_clang  # noqa: E402


@pytest.fixture()
def clang_dir(tmp_path: Path) -> Path:
    """Create a fake _clang directory with some vendored versions."""
    clang = tmp_path / "headerkit" / "_clang"
    clang.mkdir(parents=True)
    return clang


def _make_version_dir(clang_dir: Path, major: str) -> Path:
    """Create a minimal vendored version directory."""
    vdir = clang_dir / f"v{major}"
    vdir.mkdir()
    (vdir / "__init__.py").write_text("")
    (vdir / "cindex.py").write_text("# stub")
    return vdir


def _make_init_py(clang_dir: Path, versions: tuple[str, ...], latest: str) -> Path:
    """Write a minimal __init__.py with VENDORED_VERSIONS and LATEST_VENDORED."""
    init_path = clang_dir / "__init__.py"
    versions_str = ", ".join(f'"{v}"' for v in versions)
    init_path.write_text(
        textwrap.dedent(f"""\
            VENDORED_VERSIONS = ({versions_str})
            LATEST_VENDORED = "{latest}"
        """)
    )
    return init_path


class TestFindNearestVersion:
    def test_prefers_lower(self, clang_dir: Path) -> None:
        """Given v18, v19, v20 exist, target 21 returns '20'."""
        for v in ("18", "19", "20"):
            _make_version_dir(clang_dir, v)

        result = vendor_clang.find_nearest_version(clang_dir, 21)
        assert result == "20"

    def test_falls_back_to_higher(self, clang_dir: Path) -> None:
        """Given only v20 exists, target 18 returns '20'."""
        _make_version_dir(clang_dir, "20")

        result = vendor_clang.find_nearest_version(clang_dir, 18)
        assert result == "20"

    def test_no_versions_raises(self, clang_dir: Path) -> None:
        """Empty dir raises RuntimeError."""
        with pytest.raises(RuntimeError, match="No existing vendored versions"):
            vendor_clang.find_nearest_version(clang_dir, 22)


class TestVendor:
    @pytest.fixture()
    def repo_root(self, tmp_path: Path, clang_dir: Path) -> Path:
        """Return the tmp_path as repo root, with clang_dir already set up."""
        return tmp_path

    @pytest.fixture()
    def setup_existing_versions(self, clang_dir: Path) -> None:
        """Set up v20 and v21 as existing versions with __init__.py."""
        _make_version_dir(clang_dir, "20")
        _make_version_dir(clang_dir, "21")
        _make_init_py(clang_dir, ("20", "21"), "21")

    @pytest.fixture()
    def mock_download(self, monkeypatch: pytest.MonkeyPatch) -> bytes:
        """Mock download_cindex to return fake content."""
        fake_content = b"# fake cindex.py content\n"
        monkeypatch.setattr(vendor_clang, "download_cindex", lambda _tag: fake_content)
        return fake_content

    def test_creates_directory_structure(
        self,
        repo_root: Path,
        clang_dir: Path,
        setup_existing_versions: None,
        mock_download: bytes,
    ) -> None:
        """Mock download, verify all files created."""
        vendor_clang.vendor("22", "22.1.0", repo_root)

        version_dir = clang_dir / "v22"
        assert version_dir.is_dir()
        assert (version_dir / "cindex.py").is_file()
        assert (version_dir / "__init__.py").is_file()
        assert (version_dir / "PROVENANCE").is_file()

    def test_creates_provenance(
        self,
        repo_root: Path,
        clang_dir: Path,
        setup_existing_versions: None,
        mock_download: bytes,
    ) -> None:
        """Verify PROVENANCE format matches expected pattern."""
        vendor_clang.vendor("22", "22.1.0", repo_root)

        provenance = (clang_dir / "v22" / "PROVENANCE").read_text()
        lines = provenance.strip().split("\n")
        assert len(lines) == 5
        assert lines[0] == (
            "source: https://github.com/llvm/llvm-project/blob/llvmorg-22.1.0/clang/bindings/python/clang/cindex.py"
        )
        assert lines[1].startswith("sha256: ")
        assert len(lines[1].split(": ", 1)[1]) == 64  # hex sha256
        assert lines[2] == "llvm_version: 22.1.0"
        assert re.match(r"vendored_date: \d{4}-\d{2}-\d{2}", lines[3])
        assert lines[4] == "license: Apache-2.0 WITH LLVM-exception"

    def test_copies_pyi_stubs(
        self,
        repo_root: Path,
        clang_dir: Path,
        setup_existing_versions: None,
        mock_download: bytes,
    ) -> None:
        """Verify .pyi files copied from nearest version."""
        # Add a .pyi stub to v21 (nearest below 22)
        (clang_dir / "v21" / "cindex.pyi").write_text("# type stub\n")

        vendor_clang.vendor("22", "22.1.0", repo_root)

        copied_stub = clang_dir / "v22" / "cindex.pyi"
        assert copied_stub.is_file()
        assert copied_stub.read_text() == "# type stub\n"

    def test_updates_vendored_versions(
        self,
        repo_root: Path,
        clang_dir: Path,
        setup_existing_versions: None,
        mock_download: bytes,
    ) -> None:
        """Verify VENDORED_VERSIONS tuple is updated in __init__.py."""
        vendor_clang.vendor("22", "22.1.0", repo_root)

        content = (clang_dir / "__init__.py").read_text()
        match = re.search(r"VENDORED_VERSIONS\s*=\s*\(([^)]+)\)", content)
        assert match is not None
        versions = [v.strip().strip('"') for v in match.group(1).split(",")]
        assert "22" in versions
        assert versions == sorted(versions, key=int)

    def test_updates_latest_vendored(
        self,
        repo_root: Path,
        clang_dir: Path,
        setup_existing_versions: None,
        mock_download: bytes,
    ) -> None:
        """Verify LATEST_VENDORED updated for newest version."""
        vendor_clang.vendor("22", "22.1.0", repo_root)

        content = (clang_dir / "__init__.py").read_text()
        match = re.search(r'LATEST_VENDORED\s*=\s*"([^"]+)"', content)
        assert match is not None
        assert match.group(1) == "22"

    def test_refuses_existing_directory(
        self,
        repo_root: Path,
        clang_dir: Path,
        setup_existing_versions: None,
        mock_download: bytes,
    ) -> None:
        """Verify FileExistsError if version directory already exists."""
        _make_version_dir(clang_dir, "22")

        with pytest.raises(FileExistsError, match="already exists"):
            vendor_clang.vendor("22", "22.1.0", repo_root)
