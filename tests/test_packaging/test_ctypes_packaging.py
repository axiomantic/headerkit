from __future__ import annotations

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.packaging.ctypes import generate_ctypes_wheel_layout
from headerkit.scaffold import ScaffoldOptions
from headerkit.writers.ctypes import CtypesWriter


def test_ctypes_wheel_layout_generation() -> None:
    fn = Function(name="play_audio", return_type=CType("void"), parameters=[Parameter("channel", CType("int"))])
    unit = Header(path="audio.h", declarations=[fn])
    opts = ScaffoldOptions(layout="wheel", package_name="audiolib")
    layout = generate_ctypes_wheel_layout(unit, opts)

    file_map = {f.path: f.content for f in layout.files}
    assert "pyproject.toml" in file_map
    assert "src/audiolib/__init__.py" in file_map
    assert "src/audiolib/_loader.py" in file_map
    assert "src/audiolib/_bindings.py" in file_map
    assert "tests/test_tripwire.py" in file_map
    assert "tests/test_audiolib.py" in file_map

    # Anti-green-mirage content verification
    assert 'name = "audiolib"' in file_map["pyproject.toml"]
    assert "from audiolib._bindings import *" in file_map["src/audiolib/__init__.py"]
    assert "find_library" in file_map["src/audiolib/_loader.py"]
    assert "play_audio" in file_map["src/audiolib/_bindings.py"]
    assert "if _lib is not None:" in file_map["src/audiolib/_bindings.py"]
    assert "play_audio = _lib.play_audio" in file_map["src/audiolib/_bindings.py"]
    assert "@pytest.mark.tripwire" in file_map["tests/test_tripwire.py"]
    assert "test_native_library_loaded" in file_map["tests/test_tripwire.py"]
    assert "test_play_audio_binding" in file_map["tests/test_audiolib.py"]


def test_ctypes_writer_wheel_dispatch() -> None:
    writer = CtypesWriter()
    assert "wheel" in writer.supported_layouts
    unit = Header(path="ops.h", declarations=[])
    opts = ScaffoldOptions(layout="wheel", package_name="my_pkg")
    layout = writer.write_layout(unit, opts)
    assert any(f.path == "src/my_pkg/_loader.py" for f in layout.files)
