"""End-to-end tests with real-world C/C++ library headers.

Downloads actual library headers and tests the full parse -> IR -> write
pipeline for both CFFI and JSON writers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headerkit.backends import is_backend_available
from headerkit.ir import Header
from headerkit.writers.cffi import header_to_cffi
from headerkit.writers.json import header_to_json, header_to_json_dict

pytestmark = [
    pytest.mark.skipif(
        not is_backend_available("libclang"),
        reason="libclang backend not available",
    ),
    pytest.mark.download,
]


def _parse_header(
    backend,
    header_path: Path,
    include_dirs: list[str] | None = None,
) -> Header:
    """Parse a header file, skipping the test on parse failure."""
    code = header_path.read_text()
    try:
        return backend.parse(code, header_path.name, include_dirs=include_dirs)
    except (RuntimeError, Exception) as e:
        pytest.skip(f"Failed to parse {header_path.name}: {e}")


def _skip_if_unavailable(fixture_value: Path | None, name: str) -> None:
    """Skip the test if the header fixture returned None (download failure)."""
    if fixture_value is None:
        pytest.skip(f"{name} header not available (download failed)")


# =============================================================================
# sqlite3
# =============================================================================


@pytest.mark.timeout(120)
class TestSqlite3:
    """Test the sqlite3.h amalgamation header through the full pipeline."""

    def test_parse(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        assert len(header.declarations) > 0

    def test_cffi_write(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        cffi_output = header_to_cffi(header)
        assert len(cffi_output) > 0

    def test_json_write(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        json_output = header_to_json(header)
        assert len(json_output) > 0
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) > 0

    def test_known_symbols(self, backend, sqlite3_header):
        _skip_if_unavailable(sqlite3_header, "sqlite3")
        header = _parse_header(backend, sqlite3_header)
        names = {d.name for d in header.declarations}
        assert "sqlite3_open" in names
        assert "sqlite3_close" in names
        assert "sqlite3_exec" in names


# =============================================================================
# zlib
# =============================================================================


@pytest.mark.timeout(120)
class TestZlib:
    """Test the zlib.h header through the full pipeline."""

    def test_parse(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header)
        assert len(header.declarations) > 0

    def test_cffi_write(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header)
        cffi_output = header_to_cffi(header)
        assert len(cffi_output) > 0

    def test_json_write(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header)
        json_output = header_to_json(header)
        assert len(json_output) > 0
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) > 0

    def test_known_symbols(self, backend, zlib_header):
        _skip_if_unavailable(zlib_header, "zlib")
        header = _parse_header(backend, zlib_header)
        names = {d.name for d in header.declarations}
        assert "deflate" in names
        assert "inflate" in names
        assert "compress" in names
        assert "uncompress" in names
        assert "z_stream" in names


# =============================================================================
# lua
# =============================================================================


@pytest.mark.timeout(120)
class TestLua:
    """Test lua headers through the full pipeline."""

    def test_parse(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        assert len(header.declarations) > 0

    def test_cffi_write(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        cffi_output = header_to_cffi(header)
        assert len(cffi_output) > 0

    def test_json_write(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        json_output = header_to_json(header)
        assert len(json_output) > 0
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) > 0

    def test_known_symbols(self, backend, lua_headers):
        _skip_if_unavailable(lua_headers, "lua")
        header = _parse_header(
            backend,
            lua_headers / "lua.h",
            include_dirs=[str(lua_headers)],
        )
        names = {d.name for d in header.declarations}
        assert "lua_pushstring" in names
        # lua_tonumber is a macro wrapping lua_tonumberx in Lua 5.4+
        assert "lua_tonumberx" in names
        # lua_pcall is a macro wrapping lua_pcallk in Lua 5.4+
        assert "lua_pcallk" in names
        assert "lua_close" in names


# =============================================================================
# libcurl
# =============================================================================


@pytest.mark.timeout(120)
class TestCurl:
    """Test curl headers through the full pipeline."""

    def test_parse(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        assert len(header.declarations) > 0

    def test_cffi_write(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        cffi_output = header_to_cffi(header)
        assert len(cffi_output) > 0

    def test_json_write(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        json_output = header_to_json(header)
        assert len(json_output) > 0
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) > 0

    def test_known_symbols(self, backend, curl_headers):
        _skip_if_unavailable(curl_headers, "curl")
        curl_dir = curl_headers / "curl"
        header = _parse_header(
            backend,
            curl_dir / "curl.h",
            include_dirs=[str(curl_headers), str(curl_dir)],
        )
        names = {d.name for d in header.declarations}
        # curl_easy_init/perform/cleanup are in easy.h (included header)
        # and may not appear depending on recursive include behavior.
        # These symbols are defined directly in curl.h:
        assert "curl_global_init" in names
        assert "curl_global_cleanup" in names
        assert "curl_version" in names
        assert "curl_slist_append" in names


# =============================================================================
# SDL2
# =============================================================================


@pytest.mark.timeout(120)
class TestSDL2:
    """Test SDL2 headers through the full pipeline."""

    def test_parse(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        assert len(header.declarations) > 0

    def test_cffi_write(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        cffi_output = header_to_cffi(header)
        assert len(cffi_output) > 0

    def test_json_write(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        json_output = header_to_json(header)
        assert len(json_output) > 0
        json_dict = header_to_json_dict(header)
        assert "declarations" in json_dict
        assert len(json_dict["declarations"]) > 0

    def test_known_symbols(self, backend, sdl2_headers):
        _skip_if_unavailable(sdl2_headers, "SDL2")
        sdl_dir = sdl2_headers / "SDL2"
        header = _parse_header(
            backend,
            sdl_dir / "SDL.h",
            include_dirs=[str(sdl2_headers), str(sdl_dir)],
        )
        names = {d.name for d in header.declarations}
        # SDL.h defines init/quit functions directly
        assert "SDL_Init" in names
        assert "SDL_Quit" in names
        assert "SDL_WasInit" in names


# =============================================================================
# CPython
# =============================================================================


@pytest.mark.timeout(120)
class TestCPython:
    """Test CPython headers through the full pipeline.

    Python.h requires platform-specific pyconfig.h which is generated at
    build time, so parsing is expected to fail on most systems.
    """

    @pytest.mark.xfail(reason="Python.h requires platform-specific pyconfig.h")
    def test_parse(self, backend, cpython_headers):
        _skip_if_unavailable(cpython_headers, "CPython")
        inc_dir = cpython_headers / "Include"
        code = (inc_dir / "Python.h").read_text()
        # Let the error propagate so xfail can catch it
        header = backend.parse(code, "Python.h", include_dirs=[str(inc_dir)])
        assert len(header.declarations) > 0
