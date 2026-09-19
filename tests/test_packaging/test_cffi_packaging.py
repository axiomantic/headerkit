from __future__ import annotations

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.packaging.cffi import generate_cffi_wheel_layout
from headerkit.scaffold import ScaffoldOptions
from headerkit.writers.cffi import CffiWriter


def test_cffi_wheel_layout_generation() -> None:
    fn = Function(name="crypto_hash", return_type=CType("int"), parameters=[Parameter("buf", CType("void*"))])
    unit = Header(path="crypto.h", declarations=[fn])
    opts = ScaffoldOptions(layout="wheel", package_name="fastcrypto")
    layout = generate_cffi_wheel_layout(unit, opts)

    file_map = {f.path: f.content for f in layout.files}
    assert "pyproject.toml" in file_map
    assert "setup.py" in file_map
    assert "build_ffi.py" in file_map
    assert "src/fastcrypto/__init__.py" in file_map
    assert "tests/test_tripwire.py" in file_map
    assert "tests/test_fastcrypto.py" in file_map

    # Anti-green-mirage content verification
    assert 'dependencies = ["cffi>=1.15.0"]' in file_map["pyproject.toml"]
    assert "cffi_modules" in file_map["setup.py"]
    assert "ffibuilder = FFI()" in file_map["build_ffi.py"]
    assert "int crypto_hash(void* buf);" in file_map["build_ffi.py"]
    assert "from fastcrypto._cffi_backend import ffi, lib" in file_map["src/fastcrypto/__init__.py"]
    assert "@pytest.mark.tripwire" in file_map["tests/test_tripwire.py"]
    assert "test_cffi_native_backend_loaded" in file_map["tests/test_tripwire.py"]
    assert "test_crypto_hash_cffi_entrypoint" in file_map["tests/test_fastcrypto.py"]


def test_cffi_writer_wheel_dispatch() -> None:
    writer = CffiWriter()
    assert "wheel" in writer.supported_layouts
    unit = Header(path="ops.h", declarations=[])
    opts = ScaffoldOptions(layout="wheel", package_name="my_pkg")
    layout = writer.write_layout(unit, opts)
    assert any(f.path == "build_ffi.py" for f in layout.files)
