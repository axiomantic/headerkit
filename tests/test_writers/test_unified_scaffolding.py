from __future__ import annotations

import pytest

from headerkit.ir import CType, Function, Header, Parameter
from headerkit.writers import (
    coerce_writer_options,
    get_writer,
    list_writer_layouts,
    list_writer_options,
    list_writers,
)
from headerkit.writers.base import ProjectLayout, ScaffoldOptions


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
    """Test suite asserting all writers implement single-file write_layout and option coercion."""

    def test_all_writers_support_write_layout_single_file(self, sample_header: Header) -> None:
        """Every registered writer must support write_layout(layout='file') and match write()."""
        for writer_name in list_writers():
            writer = get_writer(writer_name)
            assert hasattr(writer, "write_layout"), f"{writer_name} must implement write_layout"
            assert list_writer_layouts(writer_name) == ("file",)

            opts = ScaffoldOptions(package_name="test_pkg", target_language=writer_name, layout="file")
            layout = writer.write_layout(sample_header, opts)
            assert isinstance(layout, ProjectLayout)
            assert len(layout.files) >= 1

            # write() must produce the same content as the primary output file
            rendered = writer.write(sample_header)
            assert rendered == layout.files[0].content

    def test_json_writer_options(self, sample_header: Header) -> None:
        """Json writer must respect indent option in ScaffoldOptions."""
        writer = get_writer("json")
        opts_4 = ScaffoldOptions(package_name="out", layout="file", options={"indent": 4})
        layout_4 = writer.write_layout(sample_header, opts_4)
        assert "    " in layout_4.files[0].content

    def test_diff_writer_options(self, sample_header: Header) -> None:
        """Diff writer must respect format='markdown' in ScaffoldOptions."""
        writer = get_writer("diff")
        opts_md = ScaffoldOptions(package_name="report", layout="file", options={"format": "markdown"})
        layout_md = writer.write_layout(sample_header, opts_md)
        assert layout_md.files[0].path == "report.md"
        assert "# API Diff:" in layout_md.files[0].content

    def test_prompt_writer_options(self, sample_header: Header) -> None:
        """Prompt writer must respect verbosity option in ScaffoldOptions."""
        writer = get_writer("prompt")
        opts_v = ScaffoldOptions(package_name="llm_ctx", layout="file", options={"verbosity": "verbose"})
        layout_v = writer.write_layout(sample_header, opts_v)
        assert layout_v.files[0].path == "llm_ctx.txt"
        assert "{" in layout_v.files[0].content  # verbose emits JSON

    def test_writer_supported_options_completeness(self) -> None:
        """Declared supported_options should cover key constructor options."""
        mojo_opts = {opt.name: opt for opt in list_writer_options("mojo")}
        assert "library_name" in mojo_opts
        assert "emit_classes" in mojo_opts
        assert mojo_opts["emit_classes"].type is bool

        ctypes_opts = {opt.name: opt for opt in list_writer_options("ctypes")}
        assert "lib_name" in ctypes_opts

        cffi_opts = {opt.name: opt for opt in list_writer_options("cffi")}
        assert "exclude_patterns" in cffi_opts
        assert cffi_opts["exclude_patterns"].type is list

        cython_opts = {opt.name: opt for opt in list_writer_options("cython")}
        assert "stub_cimport_prefix" in cython_opts
        assert "header_path" in cython_opts

        cshim_opts = {opt.name: opt for opt in list_writer_options("cshim")}
        assert "header_path" in cshim_opts

        nim_opts = {opt.name: opt for opt in list_writer_options("nim")}
        assert "header_path" in nim_opts
        assert "strip_include_prefixes" in nim_opts
        assert "discardable_procs" in nim_opts

        ts_wasm_opts = {opt.name: opt for opt in list_writer_options("ts_wasm")}
        assert "interface_name" in ts_wasm_opts
        assert "include_memory" in ts_wasm_opts

    def test_writer_option_coercion_boolean_and_list(self) -> None:
        """Coercion converts string booleans to bool and list options properly."""
        mojo_coerced = coerce_writer_options("mojo", {"emit_classes": "false"})
        assert mojo_coerced["emit_classes"] is False

        cffi_coerced = coerce_writer_options("cffi", {"exclude_patterns": ["^__"]})
        assert cffi_coerced["exclude_patterns"] == ["^__"]
