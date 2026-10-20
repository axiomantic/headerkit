from __future__ import annotations

import pytest

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.scaffold import ScaffoldOptions
from headerkit.writers import get_writer


@pytest.mark.parametrize("writer_name", ["cython", "cffi", "ctypes", "nim"])
def test_e2e_wheel_layout_scaffolding(writer_name: str) -> None:
    fn = Function(name="compute_hash", return_type=CType("int"), parameters=[Parameter("val", CType("int"))])
    unit = Header(path="hasher.h", declarations=[fn])
    writer = get_writer(writer_name)

    assert "wheel" in writer.supported_layouts
    opts = ScaffoldOptions(layout="wheel", package_name="my_hasher")
    layout = writer.write_layout(unit, opts)

    file_map = {f.path: f.content for f in layout.files}
    assert "pyproject.toml" in file_map
    assert "tests/test_tripwire.py" in file_map

    # Anti-green-mirage verification: tripwire must not be empty or tautological
    tw_content = file_map["tests/test_tripwire.py"]
    assert "def test_" in tw_content
    assert "@pytest.mark.tripwire" in tw_content
    assert "compute_hash" in tw_content

    # Unit test verification: must test module structure and callable
    assert "tests/test_my_hasher.py" in file_map
    ut_content = file_map["tests/test_my_hasher.py"]
    assert "def test_module_structure" in ut_content
    assert "compute_hash" in ut_content
