"""Tests for DocParserTransform, doc_parser hooks, error policies, and writer rendering."""

from __future__ import annotations

import textwrap

import pytest

from headerkit import (
    Constant,
    CType,
    DocParseError,
    DocParserTransform,
    Enum,
    EnumValue,
    Field,
    Function,
    HookRegistry,
    ParamDoc,
    Parameter,
    ParsedDoc,
    PipelineContext,
    Priority,
    RaisesDoc,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
    hook,
)
from headerkit.transforms import get_transform, list_transforms
from headerkit.writers.ctypes import CtypesWriter
from headerkit.writers.cython import CythonWriter
from headerkit.writers.nim import NimWriter
from headerkit.writers.prompt import PromptWriter
from headerkit.writers.pyi import PyiWriter


class TestDocParserFormats:
    """Verify built-in @hook('parse_doc') implementations across all supported docstring formats."""

    def test_doxygen_format_and_auto_detection(self) -> None:
        """Verify Doxygen @brief, @param[in,out], @return, @throws, @deprecated, @note, @see, and @code."""
        raw = textwrap.dedent("""\
            /**
             * @brief Solve a linear system Ax = b.
             *
             * Uses LU decomposition with partial pivoting.
             *
             * @param[in] matrix Coefficient matrix A.
             * @param[in,out] rhs Right-hand side vector b, overwritten with solution x.
             * @param n Dimension of the system.
             * @return int 0 on success, negative on singular matrix.
             * @throws SingularMatrixError When pivot is zero.
             * @deprecated Use solve_linear_v2 instead.
             * @note Thread-safe when workspace is non-shared.
             * @see solve_linear_v2
             * @code
             * int rc = solve_linear(A, b, 3);
             * @endcode
             */
        """)
        unit = SourceUnit(
            path="linalg.h",
            declarations=[
                Function(
                    name="solve_linear",
                    return_type=CType("int"),
                    parameters=[
                        Parameter("param_0", CType("double")),
                        Parameter("arg_1", CType("double")),
                        Parameter("a2", CType("int")),
                    ],
                    doc=raw,
                )
            ],
        )

        transformed = unit >> DocParserTransform(doc_format="auto")
        fn = transformed.declarations[0]
        assert isinstance(fn, Function)
        assert fn.is_deprecated is True
        assert fn.parsed_doc == ParsedDoc(
            summary="Solve a linear system Ax = b.",
            body="Uses LU decomposition with partial pivoting.",
            params=(
                ParamDoc(
                    name="matrix",
                    description="Coefficient matrix A.",
                    direction="in",
                ),
                ParamDoc(
                    name="rhs",
                    description="Right-hand side vector b, overwritten with solution x.",
                    direction="inout",
                ),
                ParamDoc(
                    name="n",
                    description="Dimension of the system.",
                ),
            ),
            returns="int 0 on success, negative on singular matrix.",
            raises=(RaisesDoc(exception="SingularMatrixError", description="When pivot is zero."),),
            examples=("int rc = solve_linear(A, b, 3);",),
            notes=("Thread-safe when workspace is non-shared.",),
            see_also=("solve_linear_v2",),
            deprecated="Use solve_linear_v2 instead.",
            source_format="doxygen",
        )
        # Synthetic parameter names (param_0, arg_1, a2) are backfilled from ParsedDoc.params!
        assert [p.name for p in fn.parameters] == ["matrix", "rhs", "n"]
        assert [p.doc for p in fn.parameters] == [
            "Coefficient matrix A.",
            "Right-hand side vector b, overwritten with solution x.",
            "Dimension of the system.",
        ]

    def test_rst_sphinx_format(self) -> None:
        """Verify RST / Sphinx :param, :type, :returns:, :rtype:, :raises:, .. note::, and .. deprecated::."""
        raw = textwrap.dedent("""\
            Compute cryptographic digest.

            Streams input chunks through SHA-256.

            :param data: Input byte buffer.
            :type data: const uint8_t*
            :param int length: Number of bytes in buffer.
            :returns: Digest status code.
            :rtype: int
            :raises ValueError: If length is negative.
            .. note:: Zero-length input is valid.
            .. deprecated:: 1.4 Use digest_stream instead.
        """)
        unit = SourceUnit(
            path="hash.h",
            declarations=[
                Function(
                    name="digest_buf",
                    return_type=CType("int"),
                    parameters=[
                        Parameter("data", CType("uint8_t")),
                        Parameter("length", CType("int")),
                    ],
                    doc=raw,
                )
            ],
        )
        transformed = unit >> DocParserTransform(doc_format="rst")
        fn = transformed.declarations[0]
        assert isinstance(fn, Function)
        assert fn.parsed_doc == ParsedDoc(
            summary="Compute cryptographic digest.",
            body="Streams input chunks through SHA-256.",
            params=(
                ParamDoc(name="data", description="Input byte buffer.", type_hint="const uint8_t*"),
                ParamDoc(name="length", description="Number of bytes in buffer.", type_hint="int"),
            ),
            returns="Digest status code.",
            return_type_hint="int",
            raises=(RaisesDoc(exception="ValueError", description="If length is negative."),),
            notes=("Zero-length input is valid.",),
            deprecated="1.4 Use digest_stream instead.",
            source_format="rst",
        )

    def test_jsdoc_format(self) -> None:
        """Verify JSDoc @param {type} [name=default] - description, @returns {type}, and @throws {type}."""
        raw = textwrap.dedent("""\
            /**
             * Connect to remote endpoint.
             *
             * @param {string} host - Target hostname.
             * @param {number} [port=8080] - TCP port number.
             * @returns {boolean} True when connected.
             * @throws {NetworkError} When connection times out.
             */
        """)
        unit = SourceUnit(
            path="net.h",
            declarations=[
                Function(
                    name="connect_host",
                    return_type=CType("bool"),
                    parameters=[
                        Parameter("host", CType("char")),
                        Parameter("port", CType("int")),
                    ],
                    doc=raw,
                )
            ],
        )
        transformed = unit >> DocParserTransform(doc_format="jsdoc")
        fn = transformed.declarations[0]
        assert isinstance(fn, Function)
        assert fn.parsed_doc == ParsedDoc(
            summary="Connect to remote endpoint.",
            params=(
                ParamDoc(name="host", description="Target hostname.", type_hint="string"),
                ParamDoc(
                    name="port",
                    description="TCP port number.",
                    type_hint="number",
                    optional=True,
                ),
            ),
            returns="True when connected.",
            return_type_hint="boolean",
            raises=(RaisesDoc(exception="NetworkError", description="When connection times out."),),
            source_format="jsdoc",
        )

    def test_google_format(self) -> None:
        """Verify Google-style Args:, Returns:, Raises:, Notes:, Examples:, and See Also: sections."""
        raw = textwrap.dedent("""\
            Resize an image buffer.

            Applies bilinear interpolation.

            Args:
                width (int): Target width in pixels.
                height (int, optional): Target height in pixels.

            Returns:
                int: 0 on success.

            Raises:
                MemoryError: If allocation fails.

            Notes:
                Preserves alpha channel.
        """)
        unit = SourceUnit(
            path="img.h",
            declarations=[
                Function(
                    name="resize_img",
                    return_type=CType("int"),
                    parameters=[
                        Parameter("width", CType("int")),
                        Parameter("height", CType("int")),
                    ],
                    doc=raw,
                )
            ],
        )
        transformed = unit >> DocParserTransform(doc_format="google")
        fn = transformed.declarations[0]
        assert isinstance(fn, Function)
        assert fn.parsed_doc == ParsedDoc(
            summary="Resize an image buffer.",
            body="Applies bilinear interpolation.",
            params=(
                ParamDoc(name="width", description="Target width in pixels.", type_hint="int"),
                ParamDoc(name="height", description="Target height in pixels.", type_hint="int", optional=True),
            ),
            returns="0 on success.",
            return_type_hint="int",
            raises=(RaisesDoc(exception="MemoryError", description="If allocation fails."),),
            notes=("Preserves alpha channel.",),
            source_format="google",
        )

    def test_numpy_format(self) -> None:
        """Verify NumPy-style Parameters / Returns / Raises underlined sections."""
        raw = textwrap.dedent("""\
            Compute weighted average.

            Parameters
            ----------
            values : float*
                Array of sample values.
            count : int, optional
                Number of elements.

            Returns
            -------
            double
                Weighted mean value.

            Raises
            ------
            ZeroDivisionError
                When total weight is zero.
        """)
        unit = SourceUnit(
            path="stats.h",
            declarations=[
                Function(
                    name="weighted_avg",
                    return_type=CType("double"),
                    parameters=[
                        Parameter("values", CType("float")),
                        Parameter("count", CType("int")),
                    ],
                    doc=raw,
                )
            ],
        )
        transformed = unit >> DocParserTransform(doc_format="numpy")
        fn = transformed.declarations[0]
        assert isinstance(fn, Function)
        assert fn.parsed_doc == ParsedDoc(
            summary="Compute weighted average.",
            params=(
                ParamDoc(name="values", description="Array of sample values.", type_hint="float*"),
                ParamDoc(name="count", description="Number of elements.", type_hint="int", optional=True),
            ),
            returns="Weighted mean value.",
            return_type_hint="double",
            raises=(RaisesDoc(exception="ZeroDivisionError", description="When total weight is zero."),),
            source_format="numpy",
        )

    def test_rustdoc_markdown_format(self) -> None:
        """Verify Rustdoc / Markdown # Arguments, # Returns, # Errors, # Examples sections."""
        raw = textwrap.dedent("""\
            /// Parse packet header from wire bytes.
            ///
            /// # Arguments
            /// * `buf` - Raw wire buffer.
            /// * `cap` - Buffer capacity in bytes.
            ///
            /// # Returns
            /// Number of bytes consumed.
            ///
            /// # Errors
            /// TruncatedPacket: Buffer is shorter than header size.
            ///
            /// # Examples
            /// ```c
            /// size_t n = parse_pkt(buf, 64);
            /// ```
        """)
        unit = SourceUnit(
            path="pkt.h",
            declarations=[
                Function(
                    name="parse_pkt",
                    return_type=CType("size_t"),
                    parameters=[
                        Parameter("buf", CType("uint8_t")),
                        Parameter("cap", CType("size_t")),
                    ],
                    doc=raw,
                )
            ],
        )
        transformed = unit >> DocParserTransform(doc_format="rustdoc")
        fn = transformed.declarations[0]
        assert isinstance(fn, Function)
        assert fn.parsed_doc == ParsedDoc(
            summary="Parse packet header from wire bytes.",
            params=(
                ParamDoc(name="buf", description="Raw wire buffer."),
                ParamDoc(name="cap", description="Buffer capacity in bytes."),
            ),
            returns="Number of bytes consumed.",
            raises=(RaisesDoc(exception="Error", description="TruncatedPacket: Buffer is shorter than header size."),),
            examples=("size_t n = parse_pkt(buf, 64);",),
            source_format="rustdoc",
        )


class TestDocParserErrorPoliciesAndHooks:
    """Verify DocParserTransform on_error modes ('lenient', 'warn', 'strict'), requirements, and custom @hook."""

    def test_strict_and_warn_error_modes_on_require_docstrings_and_param_docs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify require_docstrings and require_param_docs under strict, warn, and lenient modes."""
        assert "doc_parser" in list_transforms()
        t_from_reg = get_transform("doc-parser", doc_format="doxygen", on_error="strict", require_docstrings=True)
        assert isinstance(t_from_reg, DocParserTransform)

        undocumented_unit = SourceUnit(
            path="missing.h",
            declarations=[
                Function(
                    name="undoc_fn",
                    return_type=CType("void"),
                    parameters=[],
                )
            ],
        )

        # 1. strict mode raises DocParseError when require_docstrings=True
        with pytest.raises(DocParseError, match="Missing required docstring on 'undoc_fn'"):
            _ = undocumented_unit >> t_from_reg

        # 2. warn mode logs warning and returns unit
        warn_transform = DocParserTransform(on_error="warn", require_docstrings=True)
        _ = undocumented_unit >> warn_transform
        assert any("Missing required docstring on 'undoc_fn'" in r.message for r in caplog.records)

        # 3. require_param_docs raises in strict mode when parameter is missing from ParsedDoc.params
        partial_param_unit = SourceUnit(
            path="partial.h",
            declarations=[
                Function(
                    name="partial_fn",
                    return_type=CType("int"),
                    parameters=[
                        Parameter("documented_arg", CType("int")),
                        Parameter("missing_arg", CType("int")),
                    ],
                    doc="Short summary.\n\n:param documented_arg: Documented parameter.",
                )
            ],
        )
        strict_params = DocParserTransform(on_error="strict", require_param_docs=True)
        with pytest.raises(DocParseError, match="Parameter 'missing_arg' of 'partial_fn' is missing documentation"):
            _ = partial_param_unit >> strict_params

    def test_custom_preprocess_doc_and_parse_doc_hooks(self) -> None:
        """Verify user-registered @hook('preprocess_doc') and @hook('parse_doc') override built-in parsing."""
        snapshot = HookRegistry.snapshot()
        try:

            @hook("preprocess_doc", priority=Priority.PROJECT)
            def _strip_custom_banner(raw_doc: str, _decl: object, *, context: PipelineContext) -> str:
                _ = context
                return raw_doc.replace("[INTERNAL_BANNER]", "").strip()

            @hook("parse_doc", priority=Priority.PROJECT, doc_format="custom_dsl")
            def _parse_custom_dsl(
                raw_doc: str,
                _decl: object,
                *,
                context: PipelineContext,
                doc_format: str = "custom_dsl",
                on_error: str = "lenient",
            ) -> ParsedDoc | None:
                _ = (context, doc_format, on_error)
                return ParsedDoc(
                    summary=f"Custom DSL: {raw_doc}",
                    notes=("Parsed by custom_dsl hook",),
                    source_format="custom_dsl",
                )

            unit = SourceUnit(
                path="custom.h",
                declarations=[
                    Function(
                        name="run_dsl",
                        return_type=CType("void"),
                        parameters=[],
                        doc="[INTERNAL_BANNER] Hello world",
                    )
                ],
            )
            res = unit >> DocParserTransform(doc_format="custom_dsl")
            fn = res.declarations[0]
            assert isinstance(fn, Function)
            assert fn.parsed_doc == ParsedDoc(
                summary="Custom DSL: Hello world",
                notes=("Parsed by custom_dsl hook",),
                source_format="custom_dsl",
            )
        finally:
            HookRegistry.restore(snapshot)


class TestWritersRenderParsedDoc:
    """Verify PyiWriter, CtypesWriter, CythonWriter, NimWriter, and PromptWriter render ParsedDoc."""

    def test_all_writers_render_parsed_doc(self) -> None:
        """Verify structured ParsedDoc is emitted across pyi, ctypes, cython, nim, and prompt writers."""
        unit = SourceUnit(
            path="api.h",
            declarations=[
                Struct(
                    name="Config",
                    fields=[Field("flags", CType("int"), doc="Bitmask flags.")],
                    doc="Engine configuration.\n\n@note Must be zero-initialized.",
                ),
                Enum(
                    name="Status",
                    values=[EnumValue("OK", 0, doc="Success status.")],
                    doc="Status codes.",
                ),
                Typedef(
                    name="Handle",
                    underlying_type=CType("int"),
                    doc="Opaque resource handle.",
                ),
                Variable(
                    name="g_active",
                    type=CType("int"),
                    doc="Global active counter.",
                ),
                Constant(
                    name="API_VER",
                    value=2,
                    doc="Current protocol version.",
                ),
                Function(
                    name="process",
                    return_type=CType("int"),
                    parameters=[Parameter("cfg", CType("int"))],
                    doc="@brief Process configuration.\n@param cfg Input config value.\n@return Result code.",
                ),
            ],
        )
        enriched = unit >> DocParserTransform()

        pyi_out = PyiWriter().write(enriched)
        assert ":param cfg: Input config value." in pyi_out
        assert ":returns: Result code." in pyi_out
        assert "Note: Must be zero-initialized." in pyi_out

        ctypes_out = CtypesWriter().write(enriched)
        assert "Process configuration." in ctypes_out
        assert "@param cfg: Input config value." in ctypes_out

        cython_out = CythonWriter().write(enriched)
        assert "# Process configuration." in cython_out
        assert "# @param cfg: Input config value." in cython_out

        nim_out = NimWriter().write(enriched)
        assert "## Process configuration." in nim_out
        assert "## @param cfg: Input config value." in nim_out

        prompt_out = PromptWriter(verbosity="standard").write(enriched)
        assert "# Process configuration." in prompt_out
        assert "doc: Engine configuration." in prompt_out
