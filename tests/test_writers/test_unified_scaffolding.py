from __future__ import annotations

import pytest

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.scaffold import ProjectLayout, ScaffoldOptions
from headerkit.writers import get_writer, list_writers


@pytest.fixture
def sample_header() -> Header:
    return Header(
        path="sample.h",
        declarations=[
            Function(
                name="multiply",
                return_type=CType("int"),
                parameters=[
                    Parameter(name="a", type=CType("int")),
                    Parameter(name="b", type=CType("int")),
                ],
            )
        ],
    )


class TestUnifiedWriterScaffolding:
    """Test suite asserting all writers implement write_layout and conform to scaffolding."""

    def test_all_writers_support_write_layout_single_file(self, sample_header: Header) -> None:
        """Every registered writer must support write_layout(layout='file') and match write()."""
        for writer_name in list_writers():
            writer = get_writer(writer_name)
            assert hasattr(writer, "write_layout"), f"{writer_name} must implement write_layout"

            opts = ScaffoldOptions(package_name="test_pkg", target_language=writer_name, layout="file")
            layout = writer.write_layout(sample_header, opts)
            assert isinstance(layout, ProjectLayout)
            assert len(layout.files) >= 1

            # Legacy write() must produce the same content as the primary output file
            rendered = writer.write(sample_header)
            assert rendered == layout.files[0].content

    def test_cython_package_scaffolding(self, sample_header: Header) -> None:
        """Cython writer must produce .pxd, .pyx, pyproject.toml, and tripwire tests in package mode."""
        writer = get_writer("cython")
        opts = ScaffoldOptions(package_name="mathkit", target_language="cython", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "src/mathkit/__init__.py" in paths
        assert "src/mathkit/mathkit.pxd" in paths
        assert "src/mathkit/mathkit.pyx" in paths
        assert "tests/test_tripwire.py" in paths

        pxd_file = layout.get_file("src/mathkit/mathkit.pxd")
        assert pxd_file is not None
        assert "int multiply(int a, int b)" in pxd_file.content

    def test_cffi_package_scaffolding(self, sample_header: Header) -> None:
        """CFFI writer must produce build_ffi.py, _bindings.py, pyproject.toml, and tripwires."""
        writer = get_writer("cffi")
        opts = ScaffoldOptions(package_name="cffikit", target_language="cffi", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "pyproject.toml" in paths
        assert "build_ffi.py" in paths
        assert "src/cffikit/__init__.py" in paths
        assert "src/cffikit/_bindings.py" in paths
        assert "tests/test_tripwire.py" in paths

    def test_cshim_package_scaffolding(self, sample_header: Header) -> None:
        """CShim writer must produce CMakeLists.txt, include header, src bridge, and test harness."""
        writer = get_writer("cshim")
        opts = ScaffoldOptions(package_name="bridge", target_language="cshim", layout="package")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "CMakeLists.txt" in paths
        assert "include/bridge_cshim.h" in paths
        assert "src/bridge_cshim.cpp" in paths

    def test_luajit_package_scaffolding(self, sample_header: Header) -> None:
        """LuaJIT writer must produce rockspec, src module, and tripwire tests."""
        writer = get_writer("lua")
        opts = ScaffoldOptions(package_name="luabridge", target_language="lua", layout="package")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "luabridge-scm-1.rockspec" in paths
        assert "src/luabridge.lua" in paths
        assert "tests/test_tripwire.lua" in paths

    def test_nim_package_scaffolding(self, sample_header: Header) -> None:
        """Nim writer must produce .nimble, src module, bindings, nim.cfg, and tripwires."""
        writer = get_writer("nim")
        opts = ScaffoldOptions(package_name="nimkit", target_language="nim", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "nimkit.nimble" in paths
        assert "src/nimkit.nim" in paths
        assert "src/nimkit/bindings.nim" in paths
        assert "nim.cfg" in paths
        assert "tests/test_tripwire.nim" in paths

    def test_mojo_package_scaffolding(self, sample_header: Header) -> None:
        """Mojo writer must produce mojoproject.toml, src module, bindings, and tripwires."""
        writer = get_writer("mojo")
        opts = ScaffoldOptions(package_name="mojokit", target_language="mojo", layout="package", test_type="both")
        layout = writer.write_layout(sample_header, opts)

        paths = {f.path for f in layout.files}
        assert "mojoproject.toml" in paths
        assert "src/mojokit/__init__.mojo" in paths
        assert "src/mojokit/bindings.mojo" in paths
        assert "tests/test_tripwire.mojo" in paths
