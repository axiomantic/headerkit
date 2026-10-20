from __future__ import annotations

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.packaging.cython import generate_cython_wheel_layout
from headerkit.scaffold import ScaffoldOptions
from headerkit.writers.cython import CythonWriter


def test_cython_wheel_layout_generation() -> None:
    fn = Function(name="compute", return_type=CType("int"), parameters=[Parameter("val", CType("int"))])
    unit = Header(path="math_ops.h", declarations=[fn])
    opts = ScaffoldOptions(layout="wheel", package_name="fastmath")
    layout = generate_cython_wheel_layout(unit, opts)

    file_map = {f.path: f.content for f in layout.files}
    assert "pyproject.toml" in file_map
    assert "setup.py" in file_map
    assert "src/fastmath/__init__.py" in file_map
    assert "src/fastmath/_fastmath.pxd" in file_map
    assert "src/fastmath/fastmath.pyx" in file_map
    assert "tests/test_tripwire.py" in file_map
    assert "tests/test_fastmath.py" in file_map

    # Anti-green-mirage content verification
    assert 'requires = ["setuptools>=61.0", "Cython>=3.0.0", "wheel"]' in file_map["pyproject.toml"]
    assert "cythonize" in file_map["setup.py"]
    assert "from fastmath.fastmath import *" in file_map["src/fastmath/__init__.py"]
    assert "int compute(int val)" in file_map["src/fastmath/_fastmath.pxd"]
    assert "def compute(val):" in file_map["src/fastmath/fastmath.pyx"]
    assert "@pytest.mark.tripwire" in file_map["tests/test_tripwire.py"]
    assert "test_cython_binary_extension_loaded" in file_map["tests/test_tripwire.py"]
    assert "test_compute_wrapper_signature" in file_map["tests/test_fastmath.py"]


def test_cython_writer_wheel_dispatch() -> None:
    writer = CythonWriter()
    assert "wheel" in writer.supported_layouts
    unit = Header(path="ops.h", declarations=[])
    opts = ScaffoldOptions(layout="wheel", package_name="my_pkg")
    layout = writer.write_layout(unit, opts)
    assert any(f.path == "setup.py" for f in layout.files)
