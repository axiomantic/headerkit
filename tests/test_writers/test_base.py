"""Tests for the shared writer helpers in :mod:`headerkit.writers.base`."""

import ast
import textwrap

import pytest

from headerkit.ir import (
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    Header,
    Parameter,
    Struct,
)
from headerkit.writers.base import (
    DEDENT_BLOCK,
    BaseWriter,
    canonicalize_type_brackets,
    module_level_bindings,
    render_block_template,
    split_template_args,
)


class TestModuleLevelBindings:
    """The collision set for a generated module's export block is derived from this.

    A binding form this walk does not recognize is a name the export block will
    happily overwrite, so each form is asserted separately rather than through
    one composite source: a composite passes as soon as *any* clause contributes
    the expected name.
    """

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("x = 1", {"x"}),
            ("x = y = 1", {"x", "y"}),
            ("x: int = 1", {"x"}),
            ("x: int", {"x"}),
            ("def f(): pass", {"f"}),
            ("async def f(): pass", {"f"}),
            ("class C: pass", {"C"}),
            ("import json", {"json"}),
            ("import os.path", {"os"}),
            ("import os.path as p", {"p"}),
            ("from a import b", {"b"}),
            ("from a import b as c", {"c"}),
            ("from a import b, d", {"b", "d"}),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_each_binding_form_is_recognized(self, source: str, expected: set[str]) -> None:
        assert module_level_bindings(source) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "def f():\n    inner = 1",
            "class C:\n    attr = 1",
            "def f():\n    import json",
        ],
        ids=["function_local", "class_attribute", "function_local_import"],
    )
    def test_a_nested_binding_is_not_module_level(self, source: str) -> None:
        """A name bound inside a function or class cannot collide with an export."""
        assert module_level_bindings(source) <= {"f", "C"}

    def test_an_expression_binds_nothing(self) -> None:
        assert module_level_bindings("print(1)\n_lib.thing()") == frozenset()

    def test_unparseable_source_raises(self) -> None:
        """Silently reporting no bindings would under-reserve the collision set."""
        with pytest.raises(SyntaxError):
            module_level_bindings("def (:")


class TestRenderBlockTemplate:
    """``textwrap.dedent`` measures after interpolation; this substitutes after."""

    #: A block whose lines carry their own indentation, as a generated body does.
    BLOCK = "    a = 1\n    b = 2"

    def test_a_multi_line_block_leaves_the_template_dedented(self) -> None:
        template = """\
            def f():
            {block}
        """.replace("{block}", DEDENT_BLOCK)
        rendered = render_block_template(template, self.BLOCK)

        assert rendered.startswith("def f():"), "the template kept its source indentation"
        ast.parse(rendered)

    def test_the_naive_form_is_what_this_avoids(self) -> None:
        """Pin the defect itself, so the helper's reason for existing is executable.

        The emitted text is asserted to be *un-parseable* rather than to carry
        some particular leftover indent: how much survives depends on the block's
        own indentation, but that the file does not parse is the defect.
        """
        naive = textwrap.dedent(f"""\
            def f():
            {self.BLOCK}
        """)

        with pytest.raises(SyntaxError):
            ast.parse(naive)

    def test_an_empty_block_leaves_a_blank_line(self) -> None:
        template = "x = 1\n" + DEDENT_BLOCK + "\n"
        assert render_block_template(template, "") == "x = 1\n\n"

    def test_blocks_substitute_left_to_right(self) -> None:
        template = f"{DEDENT_BLOCK}\n{DEDENT_BLOCK}\n"
        assert render_block_template(template, "first", "second") == "first\nsecond\n"


class TestSplitTemplateArgs:
    def test_split_simple_comma(self):
        assert split_template_args("int, float, char*") == ["int", "float", "char*"]

    def test_split_nested_angle_brackets(self):
        assert split_template_args("std::map<std::string, int>, float") == [
            "std::map<std::string, int>",
            "float",
        ]

    def test_split_nested_square_brackets(self):
        assert split_template_args("Table[string, seq[int]], int") == [
            "Table[string, seq[int]]",
            "int",
        ]


class TestCanonicalizeTypeBrackets:
    def test_canonicalize_nested_brackets(self):
        def norm(t: str) -> str:
            return {"float32": "cfloat", "float64": "cdouble"}.get(t, t)

        assert canonicalize_type_brackets("AudioBuffer[float32]", norm) == "AudioBuffer[cfloat]"
        assert canonicalize_type_brackets("Map[string, Vector[float64]]", norm) == "Map[string, Vector[cdouble]]"


class TestBaseWriterSharedFeatures:
    def test_get_overload_signature(self):
        class DummyWriter(BaseWriter):
            pass

        writer = DummyWriter()
        fn1 = Function(
            name="process",
            return_type=CType("void"),
            parameters=[Parameter("buf", CType("AudioBuffer[float32]"))],
        )
        fn2 = Function(
            name="process",
            return_type=CType("void"),
            parameters=[Parameter("buf", CType("AudioBuffer[float64]"))],
        )

        sig1 = writer.get_overload_signature(fn1)
        sig2 = writer.get_overload_signature(fn2)

        assert sig1 != sig2
        assert sig1 == ("process", ("AudioBuffer[float32]",))
        assert sig2 == ("process", ("AudioBuffer[float64]",))

    def test_min_access_floor_filters_in_prepare(self):
        class PublicOnlyWriter(BaseWriter):
            min_access_floor = "public"

            def _render(self, unit):
                return ""

        st = Struct(
            name="Foo",
            fields=[
                Field("pub", CType("int"), access="public"),
                Field("priv", CType("int"), access="private"),
            ],
        )
        h = Header(path="test.h", declarations=[st])
        writer = PublicOnlyWriter()
        prepared = writer._prepare(h)
        res_st = [d for d in prepared.declarations if isinstance(d, Struct)][0]
        assert len(res_st.fields) == 1
        assert res_st.fields[0].name == "pub"

    def test_disambiguate_anonymous_enums(self):
        class DummyWriter(BaseWriter):
            pass

        writer = DummyWriter()
        e_scoped = Enum(
            name="",
            namespace="juce::Commands",
            values=[EnumValue(name="cut", value=1)],
        )
        e_root = Enum(
            name="",
            values=[EnumValue(name="reset", value=2)],
        )
        h = Header(path="test.h", declarations=[e_scoped, e_root])
        disambiguated = writer.disambiguate_anonymous_enums(h, func_names={"cut", "reset"})
        enums = [d for d in disambiguated.declarations if isinstance(d, Enum)]
        assert enums[0].values[0].name == "Commands_cut"
        assert enums[1].values[0].name == "reset_val"
