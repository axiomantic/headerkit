"""Tests for the CFFI cdef writer."""

import textwrap

import pytest

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
    Variable,
)
from headerkit.writers.cffi import _format_field, _format_params, decl_to_cffi, header_to_cffi, type_to_cffi


class TestTypeToCffi:
    def test_simple_type(self):
        assert type_to_cffi(CType("int")) == "int"

    def test_qualified_type(self):
        assert type_to_cffi(CType("int", ["const"])) == "const int"

    def test_pointer(self):
        assert type_to_cffi(Pointer(CType("int"))) == "int *"

    def test_double_pointer(self):
        assert type_to_cffi(Pointer(Pointer(CType("char")))) == "char **"

    def test_array(self):
        assert type_to_cffi(Array(CType("int"), 10)) == "int[10]"

    def test_flexible_array(self):
        assert type_to_cffi(Array(CType("char"), None)) == "char[]"

    def test_function_pointer(self):
        fp = FunctionPointer(CType("void"), [])
        assert type_to_cffi(fp) == "void(*)(void)"

    def test_function_pointer_with_params(self):
        fp = FunctionPointer(
            CType("int"),
            [Parameter("x", CType("int")), Parameter(None, CType("float"))],
        )
        assert type_to_cffi(fp) == "int(*)(int x, float)"

    def test_pointer_to_function_pointer(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        result = type_to_cffi(Pointer(fp))
        assert result == "void(**)(int x)"

    def test_pointer_to_function_pointer_no_params(self):
        fp = FunctionPointer(CType("void"), [])
        result = type_to_cffi(Pointer(fp))
        assert result == "void(**)(void)"

    def test_bare_function_type_zero_stars(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))], is_pointer=False)
        assert type_to_cffi(fp) == "void(int x)"
        assert type_to_cffi(Pointer(fp)) == "void(*)(int x)"

    def test_outer_pointer_qualifiers_filtered(self):
        t_inner_const = Pointer(Pointer(CType("char"), qualifiers=["const", "restrict", "__restrict", "_Atomic"]))
        assert type_to_cffi(t_inner_const) == "char * const *"
        t_outer_const = Pointer(Pointer(CType("char")), qualifiers=["const", "restrict", "__restrict", "_Atomic"])
        assert type_to_cffi(t_outer_const) == "char ** const"


class TestDeclToCffi:
    def test_struct_with_fields(self):
        s = Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))])
        result = decl_to_cffi(s)
        assert result == "struct Point {\n    int x;\n    int y;\n};"

    def test_opaque_struct(self):
        s = Struct("Opaque", [])
        result = decl_to_cffi(s)
        assert result == "struct Opaque { ...; };"

    def test_typedef_struct(self):
        s = Struct("Point", [Field("x", CType("int"))], is_typedef=True)
        result = decl_to_cffi(s)
        assert result == "typedef struct {\n    int x;\n} Point;"

    def test_typedef_struct_self_referential_preserves_tag(self):
        s = Struct("Node", [Field("next", Pointer(CType("struct Node")))], is_typedef=True)
        result = decl_to_cffi(s)
        assert result == "typedef struct Node {\n    struct Node * next;\n} Node;"

    def test_union(self):
        u = Struct("Data", [Field("i", CType("int"))], is_union=True)
        result = decl_to_cffi(u)
        assert result == "union Data {\n    int i;\n};"

    def test_enum(self):
        e = Enum("Color", [EnumValue("RED", 0), EnumValue("GREEN", 1)])
        result = decl_to_cffi(e)
        assert result == "enum Color {\n    RED = 0,\n    GREEN = 1,\n};"

    def test_function(self):
        f = Function("add", CType("int"), [Parameter("a", CType("int")), Parameter("b", CType("int"))])
        result = decl_to_cffi(f)
        assert result == "int add(int a, int b);"

    def test_variadic_function(self):
        f = Function(
            "printf",
            CType("int"),
            [Parameter("fmt", Pointer(CType("char", ["const"])))],
            is_variadic=True,
        )
        result = decl_to_cffi(f)
        assert result == "int printf(const char * fmt, ...);"

    def test_typedef(self):
        t = Typedef("myint", CType("unsigned int"))
        result = decl_to_cffi(t)
        assert result == "typedef unsigned int myint;"

    def test_constant_integer(self):
        c = Constant("SIZE", 100, is_macro=True)
        result = decl_to_cffi(c)
        assert result == "#define SIZE 100"

    def test_constant_non_integer_skipped(self):
        c = Constant("STR", '"hello"', is_macro=True)
        result = decl_to_cffi(c)
        assert result is None

    def test_constant_float_returns_none(self):
        """CFFI only supports integer constants; float values should return None."""
        c = Constant("PI", 3.14, is_macro=True)
        result = decl_to_cffi(c)
        assert result is None

    def test_unknown_declaration_type_returns_none(self):
        """decl_to_cffi should return None for unknown declaration types."""

        class FakeDecl:
            name = "fake"

        result = decl_to_cffi(FakeDecl())
        assert result is None

    def test_variable(self):
        v = Variable("count", CType("int"))
        result = decl_to_cffi(v)
        assert result == "int count;"

    def test_anonymous_struct_skipped(self):
        s = Struct(None, [Field("x", CType("int"))])
        result = decl_to_cffi(s)
        assert result is None

    def test_exclude_patterns(self):
        import re

        f = Function("_internal_func", CType("void"), [])
        result = decl_to_cffi(f, exclude_patterns=[re.compile(r"_internal_")])
        assert result is None


class TestFormatField:
    def test_array_field(self):
        f = Field("buf", Array(CType("char"), 256))
        result = _format_field(f)
        assert result == "    char buf[256];"

    def test_function_pointer_field(self):
        fp = FunctionPointer(CType("void"), [Parameter("x", CType("int"))])
        f = Field("callback", fp)
        result = _format_field(f)
        assert result == "    void (*callback)(int x);"

    def test_regular_field(self):
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"


class TestFormatParams:
    def test_array_parameter(self):
        params = [Parameter("buf", Array(CType("char"), 256))]
        result = _format_params(params, False)
        assert result == "char buf[256]"

    def test_function_pointer_parameter(self):
        fp = FunctionPointer(CType("int"), [Parameter("x", CType("float"))])
        params = [Parameter("cb", fp)]
        result = _format_params(params, False)
        assert result == "int (*cb)(float x)"

    def test_empty_params_void(self):
        result = _format_params([], False)
        assert result == "void"

    def test_variadic_only(self):
        result = _format_params([], True)
        assert result == "..."


class TestHeaderToCffi:
    def test_simple_header(self):
        header = Header(
            "test.h",
            [
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
                Function("get_point", Pointer(CType("Point")), []),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            struct Point {
                int x;
                int y;
            };
            struct Point * get_point(void);""")

    def test_exclude_patterns(self):
        header = Header(
            "test.h",
            [
                Function("public_func", CType("void"), []),
                Function("_private_func", CType("void"), []),
            ],
        )
        result = header_to_cffi(header, exclude_patterns=[r"_private_"])
        assert result == "void public_func(void);"

    def test_opaque_struct_with_typedef_dedup(self):
        """Opaque struct + matching typedef should emit only the typedef."""
        header = Header(
            "test.h",
            [
                Struct("nng_socket", []),  # opaque
                Typedef("nng_socket", CType("struct nng_socket")),
            ],
        )
        result = header_to_cffi(header)
        assert result == "typedef struct nng_socket nng_socket;"

    def test_enum_typedef_pair_combined(self):
        """A TAG-LESS enum + matching typedef combine into `typedef enum { ... } name;`.

        `is_typedef=True` marks the `typedef enum { ... } nng_pipe_ev;` source form,
        which declares no `enum nng_pipe_ev` tag. Emitting one would name a type the
        real header does not define.
        """
        header = Header(
            "test.h",
            [
                Enum(
                    "nng_pipe_ev",
                    [EnumValue("NNG_PIPE_EV_ADD", 0), EnumValue("NNG_PIPE_EV_REM", 1)],
                    is_typedef=True,
                ),
                Typedef("nng_pipe_ev", CType("enum nng_pipe_ev")),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            typedef enum {
                NNG_PIPE_EV_ADD = 0,
                NNG_PIPE_EV_REM = 1,
            } nng_pipe_ev;""")

    def test_enum_typedef_pair_with_tag_kept_separate(self):
        """A TAGGED enum + matching typedef stay separate, preserving the tag.

        Sibling of :meth:`test_enum_typedef_pair_combined`: pins the `is_typedef`
        discriminator from both sides, so collapsing every enum+typedef pair into the
        tag-less form would fail here.
        """
        header = Header(
            "test.h",
            [
                Enum("nng_pipe_ev", [EnumValue("NNG_PIPE_EV_ADD", 0), EnumValue("NNG_PIPE_EV_REM", 1)]),
                Typedef("nng_pipe_ev", CType("enum nng_pipe_ev")),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            enum nng_pipe_ev {
                NNG_PIPE_EV_ADD = 0,
                NNG_PIPE_EV_REM = 1,
            };
            typedef enum nng_pipe_ev nng_pipe_ev;""")

    def test_typedef_only_enum_parameter_type_not_prefixed(self):
        """Anonymous enum typedef used as param type should not be prefixed with 'enum'."""
        header = Header(
            "test.h",
            [
                Enum("nng_pipe_ev", [EnumValue("NNG_PIPE_EV_ADD", 0)], is_typedef=True),
                Function("nng_pipe_notify", CType("int"), [Parameter("ev", CType("nng_pipe_ev"))]),
            ],
        )
        result = header_to_cffi(header)
        assert "int nng_pipe_notify(nng_pipe_ev ev);" in result
        assert "enum nng_pipe_ev" not in result

    def test_tag_kind_qualification(self):
        """Bare struct name in typedef should get 'struct' prefix."""
        header = Header(
            "test.h",
            [
                Struct("inner_s", [Field("x", CType("int"))]),
                Typedef("outer_t", CType("inner_s")),
            ],
        )
        result = header_to_cffi(header)
        assert result == textwrap.dedent("""\
            struct inner_s {
                int x;
            };
            typedef struct inner_s outer_t;""")


class TestBitfieldFormatting:
    def test_bitfield_regular_type(self):
        f = Field("flags", CType("unsigned int"), bit_width=4)
        result = _format_field(f)
        assert result == "    unsigned int flags : 4;"

    def test_bitfield_single_bit(self):
        f = Field("valid", CType("unsigned int"), bit_width=1)
        result = _format_field(f)
        assert result == "    unsigned int valid : 1;"

    def test_no_bitwidth_omits_suffix(self):
        f = Field("count", CType("int"))
        result = _format_field(f)
        assert result == "    int count;"
        assert ":" not in result


class TestAnonymousStructField:
    def test_anonymous_struct_inline(self):
        anon = Struct(None, [Field("x", CType("int")), Field("y", CType("int"))])
        f = Field("pos", CType("int"), anonymous_struct=anon)
        result = _format_field(f)
        assert result == "    struct {\n        int x;\n        int y;\n    };"

    def test_anonymous_union_inline(self):
        anon = Struct(None, [Field("i", CType("int")), Field("f", CType("float"))], is_union=True)
        f = Field("data", CType("int"), anonymous_struct=anon)
        result = _format_field(f)
        assert result == "    union {\n        int i;\n        float f;\n    };"


class TestPackedStruct:
    def test_packed_struct_names_the_argument_the_reader_must_pass(self):
        """The comment has to be actionable, because nothing else reports the
        problem. cffi rejects ``__attribute__((packed))`` in either position,
        so the record is emitted unpacked and cdef lays it out that way in
        silence: measured with cffi, a record that is 6 bytes in C comes back
        as 12 without ``packed=True``."""
        s = Struct("Packed", [Field("x", CType("int"))], is_packed=True)
        result = decl_to_cffi(s)
        assert "HEADERKIT: packed record" in result
        assert "packed=True" in result
        assert result.endswith("struct Packed {\n    int x;\n};")

    def test_non_packed_struct_no_comment(self):
        s = Struct("Normal", [Field("x", CType("int"))])
        result = decl_to_cffi(s)
        assert result == "struct Normal {\n    int x;\n};"
        assert "HEADERKIT: packed record" not in result


class TestCallingConventionCffi:
    def test_function_calling_convention_omitted(self):
        f = Function("WinMain", CType("int"), [], calling_convention="stdcall")
        result = decl_to_cffi(f)
        assert result is not None
        assert "stdcall" not in result
        assert result == "int WinMain(void);"

    def test_function_pointer_calling_convention_omitted(self):
        fp = FunctionPointer(CType("void"), [], calling_convention="stdcall")
        result = type_to_cffi(fp)
        assert "stdcall" not in result
        assert result == "void(*)(void)"


class TestCffiWriter:
    """Tests for the CffiWriter class (protocol-compliant wrapper)."""

    def test_writer_produces_output_with_expected_content(self):
        """CffiWriter.write() should produce output with expected declarations."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function(
                    "get_point",
                    CType("int"),
                    [
                        Parameter("x", CType("float")),
                    ],
                ),
                Struct("Point", [Field("x", CType("int")), Field("y", CType("int"))]),
            ],
        )
        writer = CffiWriter()
        result = writer.write(header)
        assert result == textwrap.dedent("""\
            int get_point(float x);
            struct Point {
                int x;
                int y;
            };""")

    def test_writer_with_exclude_patterns(self):
        """CffiWriter should forward exclude_patterns to header_to_cffi."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            "test.h",
            [
                Function("public_func", CType("void"), []),
                Function("_private_func", CType("void"), []),
            ],
        )
        writer = CffiWriter(exclude_patterns=[r"_private_"])
        result = writer.write(header)
        assert result == "void public_func(void);"

    def test_writer_protocol_compliance(self):
        """CffiWriter should satisfy the WriterBackend protocol."""
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        # Verify required attributes exist and have correct types
        assert isinstance(writer.name, str)
        assert writer.name == "cffi"
        assert isinstance(writer.format_description, str)
        assert writer.format_description == "CFFI cdef declarations for ffibuilder.cdef()"
        # Verify write() produces string output
        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        result = writer.write(header)
        assert result == "void foo(void);"

    def test_writer_name(self):
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert writer.name == "cffi"

    def test_writer_format_description(self):
        from headerkit.writers.cffi import CffiWriter

        writer = CffiWriter()
        assert writer.format_description == "CFFI cdef declarations for ffibuilder.cdef()"


class TestDefinePatterns:
    """Tests for the define_patterns option on CffiWriter."""

    def test_define_patterns_basic(self):
        """Matched defines are appended as #define NAME ... lines."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("nng_alloc", CType("void"), []),
            ],
        )
        writer = CffiWriter(define_patterns=[r"NNG_FLAG_\w+"])
        writer._matched_defines = ["NNG_FLAG_ALLOC", "NNG_FLAG_NONBLOCK"]
        result = writer.write(header)
        assert "#define NNG_FLAG_ALLOC ..." in result
        assert "#define NNG_FLAG_NONBLOCK ..." in result
        # The function declaration should still be present
        assert "void nng_alloc(void);" in result

    def test_define_patterns_empty_matches(self):
        """When define_patterns is set but no defines match, no #define lines appear."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(define_patterns=[r"NONEXISTENT_\w+"])
        writer._matched_defines = []
        result = writer.write(header)
        assert "#define" not in result
        assert "void foo(void);" in result

    def test_define_patterns_none(self):
        """Without define_patterns, output is normal (no #define lines)."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("bar", CType("int"), [Parameter("x", CType("int"))]),
            ],
        )
        writer = CffiWriter()
        result = writer.write(header)
        assert "#define" not in result
        assert "int bar(int x);" in result


class TestDefinePatternsFromIRConstants:
    """Tests for CffiWriter matching define_patterns against Constant declarations."""

    def test_matches_non_integer_constants_and_deduplicates_integers(self):
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="nng.h",
            declarations=[
                Constant("NNG_FLAG_ALLOC", 1, is_macro=True),
                Constant("NNG_FLAG_NONBLOCK", None, is_macro=True),
                Constant("NNG_VERSION", '"1.0"', is_macro=True),
                Constant("OTHER_MACRO", None, is_macro=True),
                Function("nng_alloc", CType("void"), []),
            ],
        )
        writer = CffiWriter(define_patterns=[r"^NNG_FLAG_\w+$"])
        result = writer.write(header)
        assert result == ("#define NNG_FLAG_ALLOC 1\nvoid nng_alloc(void);\n#define NNG_FLAG_NONBLOCK ...")

    def test_define_patterns_respects_exclude_patterns(self):
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="nng.h",
            declarations=[
                Constant("NNG_FLAG_PUBLIC", None, is_macro=True),
                Constant("NNG_FLAG_PRIVATE", None, is_macro=True),
            ],
        )
        writer = CffiWriter(
            define_patterns=[r"^NNG_FLAG_\w+$"],
            exclude_patterns=[r"PRIVATE"],
        )
        result = writer.write(header)
        assert result == "#define NNG_FLAG_PUBLIC ..."


class TestTaglessStructTypedefAndModuleVarAndExternPython:
    """Tests for tag-less typedef struct, 0/1/2-star function pointers, module_var, and extern_python."""

    def test_tagless_typedef_struct_and_parameter(self):
        header = Header(
            path="nng.h",
            declarations=[
                Struct("nng_init_params", [Field("num_task_threads", CType("int16_t"))], is_typedef=True),
                Typedef("nng_init_params", CType("struct nng_init_params")),
                Function(
                    "nng_init",
                    CType("int"),
                    [Parameter("params", Pointer(CType("struct nng_init_params", ["const"])))],
                ),
            ],
        )
        result = header_to_cffi(header)
        assert result == (
            "typedef struct {\n"
            "    int16_t num_task_threads;\n"
            "} nng_init_params;\n"
            "int nng_init(const nng_init_params * params);"
        )

    def test_function_pointer_typedef_zero_one_two_stars(self):
        header = Header(
            path="cb.h",
            declarations=[
                Typedef(
                    "bare_fn_t",
                    FunctionPointer(CType("void"), [Parameter("x", CType("int"))], is_pointer=False),
                ),
                Typedef(
                    "ptr_fn_t",
                    FunctionPointer(CType("void"), [Parameter("x", CType("int"))], is_pointer=True),
                ),
                Typedef(
                    "dbl_ptr_fn_t",
                    Pointer(FunctionPointer(CType("void"), [Parameter("x", CType("int"))], is_pointer=True)),
                ),
                Struct(
                    "ops_t",
                    [
                        Field(
                            "malloc_fn",
                            FunctionPointer(
                                Pointer(CType("void")),
                                [Parameter("sz", CType("size_t"))],
                                is_pointer=True,
                            ),
                        ),
                        Field(
                            "out_fn",
                            Pointer(
                                FunctionPointer(
                                    Pointer(CType("void")),
                                    [Parameter("sz", CType("size_t"))],
                                    is_pointer=True,
                                )
                            ),
                        ),
                    ],
                    is_typedef=True,
                ),
            ],
        )
        result = header_to_cffi(header)
        assert result == (
            "typedef void bare_fn_t(int x);\n"
            "typedef void (*ptr_fn_t)(int x);\n"
            "typedef void (**dbl_ptr_fn_t)(int x);\n"
            "typedef struct {\n"
            "    void * (*malloc_fn)(size_t sz);\n"
            "    void * (**out_fn)(size_t sz);\n"
            "} ops_t;"
        )

    def test_module_var_wraps_python_module(self):
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="lockfree.h",
            declarations=[
                Function("ring_push", CType("int"), [Parameter("val", CType("int"))]),
            ],
        )
        writer = CffiWriter(module_var="CDEF")
        result = writer.write(header)
        assert result == (
            '"""Auto-generated CFFI cdef declarations by headerkit from lockfree.h."""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "CDEF: str = 'int ring_push(int val);'\n"
            "\n"
            "__all__ = ['CDEF']\n"
        )
        assert writer.hash_comment_format() == "# {line}"

    def test_extern_python_callbacks(self):
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="nng.h",
            declarations=[
                Function("nng_aio_alloc", CType("int"), []),
                Function("_async_complete", CType("void"), [Parameter("arg", Pointer(CType("void")))]),
                Typedef(
                    "_nng_pipe_cb",
                    FunctionPointer(CType("void"), [Parameter("arg", Pointer(CType("void")))]),
                ),
            ],
        )
        writer = CffiWriter(
            extern_python=[
                "_async_complete",
                "_nng_pipe_cb",
            ]
        )
        result = writer.write(header)
        assert result == (
            "int nng_aio_alloc(void);\n"
            "typedef void (*_nng_pipe_cb)(void * arg);\n"
            'extern "Python" void _async_complete(void * arg);\n'
            'extern "Python" void _nng_pipe_cb(void * arg);'
        )

    def test_extern_python_rejects_raw_prototypes_and_unknown_symbols(self):
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="nng.h",
            declarations=[
                Function("nng_aio_alloc", CType("int"), []),
                Typedef("my_int_t", CType("int")),
            ],
        )
        for bad_entry in (
            "void _async_complete(void *)",
            'extern "Python" void _async_complete(void *);',
            "",
            "not a symbol",
        ):
            writer = CffiWriter(extern_python=[bad_entry])
            with pytest.raises(ValueError, match="must be a symbol name"):
                writer.write(header)

        for missing_or_non_fn in ("nonexistent_cb", "my_int_t"):
            writer = CffiWriter(extern_python=[missing_or_non_fn])
            with pytest.raises(ValueError, match="not found as a Function or function-pointer Typedef"):
                writer.write(header)


class TestExtraCdef:
    """Tests for the extra_cdef option on CffiWriter."""

    def test_extra_cdef_basic(self):
        """extra_cdef lines are appended verbatim to the output."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(extra_cdef=['extern "Python" void cb(void *);'])
        result = writer.write(header)
        assert 'extern "Python" void cb(void *);' in result
        assert "void foo(void);" in result

    def test_extra_cdef_multiple_lines(self):
        """Multiple extra_cdef lines are all appended."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(
            extra_cdef=[
                'extern "Python" void on_data(void *);',
                'extern "Python" void on_error(int);',
            ]
        )
        result = writer.write(header)
        assert 'extern "Python" void on_data(void *);' in result
        assert 'extern "Python" void on_error(int);' in result

    def test_extra_cdef_none(self):
        """Without extra_cdef, output is unchanged."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter()
        result = writer.write(header)
        assert result == "void foo(void);"
        assert "extern" not in result

    def test_define_patterns_and_extra_cdef_together(self):
        """When both are set: main cdef, then defines, then extra_cdef."""
        from headerkit.writers.cffi import CffiWriter

        header = Header(
            path="test.h",
            declarations=[
                Function("foo", CType("void"), []),
            ],
        )
        writer = CffiWriter(
            define_patterns=[r"FLAG_\w+"],
            extra_cdef=['extern "Python" void cb(void *);'],
        )
        writer._matched_defines = ["FLAG_ONE", "FLAG_TWO"]
        result = writer.write(header)

        # Verify all parts present
        assert "void foo(void);" in result
        assert "#define FLAG_ONE ..." in result
        assert "#define FLAG_TWO ..." in result
        assert 'extern "Python" void cb(void *);' in result

        # Verify ordering: main output, then defines, then extra_cdef
        func_pos = result.index("void foo(void);")
        define_pos = result.index("#define FLAG_ONE ...")
        extra_pos = result.index('extern "Python" void cb(void *);')
        assert func_pos < define_pos < extra_pos
