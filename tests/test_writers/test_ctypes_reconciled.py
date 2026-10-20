from __future__ import annotations

from headerkit.ir import CType, Field, Function, Header, Parameter, Struct
from headerkit.matrix import ConfigCondition
from headerkit.reconcile import reconcile_matrix
from headerkit.writers.ctypes import CtypesWriter


def test_ctypes_reconciled_platform_divergence() -> None:
    s_mac = Struct(name="Engine", fields=[Field(name="queue", type=CType("void*"))])
    s_linux = Struct(name="Engine", fields=[Field(name="pcm_fd", type=CType("int"))])
    u_mac = Header(path="audio.h", declarations=[s_mac])
    u_linux = Header(path="audio.h", declarations=[s_linux])

    reconciled = reconcile_matrix(
        [
            (ConfigCondition({"target": "darwin"}), u_mac),
            (ConfigCondition({"target": "linux"}), u_linux),
        ]
    )

    writer = CtypesWriter()
    output = writer.write(reconciled)

    assert "if sys.platform == 'darwin':" in output
    assert "class Engine(ctypes.Structure):" in output
    assert '("queue", ctypes.c_void_p)' in output
    assert "elif sys.platform == 'linux':" in output
    assert '("pcm_fd", ctypes.c_int)' in output
    assert 'raise OSError(f"Unsupported platform: {sys.platform}")' in output


def test_ctypes_reconciled_with_common_and_functions() -> None:
    f_common = Function(name="close_audio", return_type=CType("void"), parameters=[])
    f_mac = Function(name="play_mac", return_type=CType("void"), parameters=[Parameter("ch", CType("int"))])
    f_linux = Function(name="play_linux", return_type=CType("void"), parameters=[Parameter("ch", CType("int"))])

    u_mac = Header(path="audio.h", declarations=[f_common, f_mac])
    u_linux = Header(path="audio.h", declarations=[f_common, f_linux])

    reconciled = reconcile_matrix(
        [
            (ConfigCondition({"target": "darwin"}), u_mac),
            (ConfigCondition({"target": "linux"}), u_linux),
        ]
    )

    writer = CtypesWriter()
    output = writer.write(reconciled)

    assert "close_audio" in output
    assert "if sys.platform == 'darwin':" in output
    assert "play_mac" in output
    assert "elif sys.platform == 'linux':" in output
    assert "play_linux" in output
