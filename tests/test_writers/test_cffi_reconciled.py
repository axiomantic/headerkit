from __future__ import annotations

from headerkit.ir import CType, Function, Header
from headerkit.matrix import ConfigCondition
from headerkit.reconcile import reconcile_matrix
from headerkit.writers.cffi import CffiWriter


def test_cffi_reconciled_chunked_cdef() -> None:
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

    writer = CffiWriter()
    output = writer.write(reconciled)

    # Must be valid Python script with chunked ffi.cdef() calls
    assert "ffi = FFI()" in output
    assert "if sys.platform == 'darwin':" in output
    assert "ffi.cdef" in output
    assert "void mac_play(void);" in output
    assert "elif sys.platform == 'linux':" in output
    assert "void linux_play(void);" in output
    assert "void audio_close(void);" in output
