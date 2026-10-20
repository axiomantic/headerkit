"""Tests for polyglot override block preservation, validation, and manifest emission."""

from __future__ import annotations

import pytest

from headerkit.matrix.manifest import (
    COMMENT_PREFIXES,
    MalformedOverrideError,
    MatrixManifest,
    emit_manifest_comment,
    extract_manifest,
    extract_overrides,
    inject_overrides,
)
from headerkit.matrix.matrix import ConfigMatrix


def test_override_extraction_and_reinjection() -> None:
    code = """\
# Generated header
# @headerkit:override custom_hook
proc myCustomHook*() =
  echo "preserved"
# @headerkit:end-override custom_hook
# End of file
"""
    overrides = extract_overrides(code, language="nim")
    assert "custom_hook" in overrides
    assert 'echo "preserved"' in overrides["custom_hook"]

    new_gen = """\
# Freshly generated header
# @headerkit:override custom_hook
# (Placeholder)
# @headerkit:end-override custom_hook
# Done
"""
    restored = inject_overrides(new_gen, overrides, language="nim")
    assert 'echo "preserved"' in restored
    assert "(Placeholder)" not in restored


def test_override_malformed_unclosed_raises() -> None:
    bad_code = """\
# @headerkit:override broken
proc dangling() = nil
"""
    with pytest.raises(MalformedOverrideError, match="Unclosed override block"):
        extract_overrides(bad_code, language="nim")


def test_override_c_comment_delimiters() -> None:
    c_code = """\
// @headerkit:override c_ext
int custom_c_function(void) { return 42; }
// @headerkit:end-override c_ext
"""
    overrides = extract_overrides(c_code, language="c")
    assert "c_ext" in overrides
    assert "custom_c_function" in overrides["c_ext"]


def test_manifest_roundtrip() -> None:
    matrix = ConfigMatrix()
    matrix.add_dimension("target", ["darwin", "linux"], dimension_type="target")
    manifest = MatrixManifest(
        schema_version=1,
        source_hash="sha256:abc123def456",
        header_path="include/test.h",
        matrix=matrix,
    )

    comment = emit_manifest_comment(manifest, language="nim")
    assert "@headerkit-matrix-manifest" in comment

    parsed = extract_manifest(comment, language="nim")
    assert parsed.schema_version == 1
    assert parsed.source_hash == "sha256:abc123def456"
    assert parsed.header_path == "include/test.h"
    assert COMMENT_PREFIXES["nim"] == "#"
    assert COMMENT_PREFIXES["c"] == "//"
