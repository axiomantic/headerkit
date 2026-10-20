from __future__ import annotations

from headerkit.ir import CType, Function, Header
from headerkit.matrix import ConfigCondition
from headerkit.reconcile import reconcile_matrix
from headerkit.writers.cython import CythonWriter


def test_cython_reconciled_platform_divergence() -> None:
    f_mac = Function(name="mac_play", return_type=CType("void"))
    f_linux = Function(name="linux_play", return_type=CType("void"))
    f_common = Function(name="audio_close", return_type=CType("void"))

    u_mac = Header(path="audio.h", declarations=[f_mac, f_common])
    u_linux = Header(path="audio.h", declarations=[f_linux, f_common])

    reconciled = reconcile_matrix(
        [
            (ConfigCondition({"target": "darwin"}), u_mac),
            (ConfigCondition({"target": "linux"}), u_linux),
        ]
    )

    writer = CythonWriter()
    output = writer.write(reconciled)

    assert 'IF UNAME_SYSNAME == "Darwin":' in output
    assert "void mac_play()" in output
    assert 'ELIF UNAME_SYSNAME == "Linux":' in output
    assert "void linux_play()" in output
    assert "void audio_close()" in output
