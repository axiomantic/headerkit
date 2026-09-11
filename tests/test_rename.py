"""Tests for the ``rename_symbol`` and ``resolve_collision`` hook points.

The identifier rules under test are Nim's, and Nim rejects the interesting cases
at the LEXER: ``a__b`` is ``invalid token: trailing underscore``, not a semantic
error. A string assertion on the emitted text therefore proves nothing about
whether the output is loadable, so the cases that matter here run the real
compiler over the generated module and let it arbitrate.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from headerkit._cache_key import compute_output_cache_key
from headerkit._config import load_config
from headerkit._generate import generate
from headerkit._rename import (
    RenameConfig,
    RenameError,
    RenameRule,
    Symbol,
    SymbolCollisionError,
    apply_case,
    enforce_injectivity,
    make_config_resolver,
    parse_rename_config,
    register_config_hooks,
    rename_cache_fingerprint,
)
from headerkit.hooks import HookRegistry, PipelineContext, Priority
from headerkit.ir import CType, Enum, EnumValue, Field, Function, Header, Parameter, Struct, Typedef
from headerkit.writers.nim import (
    _escape_ident,
    nim_ident_identity,
    validate_nim_ident,
    write_nim,
)
from tests.skip_policy import NIM_INSTALL, require_program

NIM_CONTEXT = PipelineContext(writer="nim")


@pytest.fixture()
def clean_hooks():
    """Restore the global hook registry after a test registers into it."""
    saved = HookRegistry.snapshot()
    yield
    HookRegistry.restore(saved)


def _run_nim_check(source: str, tmp_path: Path, stem: str) -> subprocess.CompletedProcess[str]:
    """Run the real ``nim check`` over *source* and return the result.

    ``nim`` not being installed is a failure, not a skip: this is the only gate
    in the suite that can tell a legal identifier from an illegal one, and a
    gate that did not run has established nothing.
    """
    nim_bin = require_program("nim", install=NIM_INSTALL)
    module = tmp_path / f"{stem}.nim"
    module.write_text(source, encoding="utf-8")
    return subprocess.run(
        [nim_bin, "check", "--hints:off", "--colors:off", str(module)],
        capture_output=True,
        text=True,
        check=False,
    )


def nim_check(source: str, tmp_path: Path, stem: str = "generated") -> None:
    """Fail with the compiler's diagnostics unless *source* is a module Nim accepts."""
    result = _run_nim_check(source, tmp_path, stem)
    if result.returncode != 0:
        pytest.fail(f"nim check rejected the generated module:\n{source}\n---\n{result.stdout}\n{result.stderr}")


def nim_check_rejects(source: str, tmp_path: Path, stem: str, expect: str) -> None:
    """Fail unless the real compiler rejects *source* for the stated reason.

    The reason is checked, not the exit status alone: a module rejected for an
    unrelated error -- a typo in a fixture, an import that does not resolve -- is
    a red run that says nothing about the defect being pinned, and reads
    identically to one that does.
    """
    result = _run_nim_check(source, tmp_path, stem)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"nim check ACCEPTED a module expected to be rejected:\n{source}\n---\n{combined}"
    assert expect in combined, f"nim check rejected the module, but not for {expect!r}:\n{source}\n---\n{combined}"


class TestNimIdentifierGrammar:
    """The floor: what the writer emits must be a legal Nim identifier."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("__sig", "u_sig"),
            ("_sig", "u_sig"),
            ("a__b", "a_b"),
            ("x__y__z", "x_y_z"),
            ("_a_", "u_a_u"),
            ("___", "u_u"),
            ("a_", "a_u"),
        ],
    )
    def test_escape_ident_leaves_no_consecutive_underscores(self, source: str, expected: str) -> None:
        assert _escape_ident(source) == expected
        validate_nim_ident(_escape_ident(source))

    def test_nim_identity_folds_underscores_and_case_after_the_first_character(self) -> None:
        assert nim_ident_identity("foo_bar") == nim_ident_identity("fooBar")
        # The first character is compared exactly, so these are two identifiers.
        assert nim_ident_identity("Foo_bar") != nim_ident_identity("fooBar")

    @pytest.mark.parametrize("bad", ["_lead", "trail_", "a__b", "", "1abc", "has space"])
    def test_validate_rejects_what_the_lexer_rejects(self, bad: str) -> None:
        with pytest.raises(RenameError):
            validate_nim_ident(bad)

    def test_validate_accepts_backtick_quoted_names(self) -> None:
        validate_nim_ident("`type`")
        validate_nim_ident("`+`")


@pytest.mark.allow("subprocess")
# A `nim check` per case runs a whole compiler front end; the suite-wide 60s
# budget is written for unit tests.
@pytest.mark.timeout(300)
class TestGeneratedNimCompiles:
    """The arbiter is the compiler, not the emitted string."""

    def test_double_underscored_c_names_produce_loadable_nim(self, tmp_path: Path) -> None:
        header = Header(
            path="underscores.h",
            declarations=[
                Function(name="__sig", return_type=CType("void")),
                Function(name="a__b", return_type=CType("void")),
                Function(name="x__y__z", return_type=CType("void")),
                Function(name="_a_", return_type=CType("void")),
                Function(name="___", return_type=CType("void")),
            ],
        )
        out = write_nim(header, header_path="underscores.h")
        nim_check(out, tmp_path, stem="underscores")

    def test_every_original_c_name_survives_in_the_importc_pragma(self) -> None:
        """Renaming changes the Nim spelling; it must not change the linked symbol."""
        header = Header(
            path="underscores.h",
            declarations=[Function(name="a__b", return_type=CType("void"))],
        )
        out = write_nim(header, header_path="underscores.h")
        assert 'proc a_b*() {.importc: "a__b", header: "underscores.h", cdecl.}' in out


class TestCollisionsRaise:
    """Renaming is not injective, and Nim's identity function is not ``==``."""

    @staticmethod
    def colliding_header() -> Header:
        return Header(
            path="collide.h",
            declarations=[
                Function(name="fooBar", return_type=CType("int")),
                Function(name="foo_bar", return_type=CType("int")),
            ],
        )

    def test_case_and_underscore_variants_are_one_identifier_to_nim(self) -> None:
        with pytest.raises(SymbolCollisionError) as excinfo:
            write_nim(self.colliding_header(), header_path="collide.h")
        message = str(excinfo.value)
        assert "'fooBar'" in message
        assert "'foo_bar'" in message

    def test_an_enum_colliding_with_a_function_is_refused_not_suffixed(self) -> None:
        """The old writer silently emitted ``status_enum``; guessing is now refused."""
        header = Header(
            path="collide.h",
            declarations=[
                Enum(name="status", values=[EnumValue("STATUS_OK", 0)]),
                Function(name="status", return_type=CType("int")),
            ],
        )
        with pytest.raises(SymbolCollisionError) as excinfo:
            write_nim(header, header_path="collide.h")
        assert "'status' (enum)" in str(excinfo.value)
        assert "'status' (function)" in str(excinfo.value)

    def test_a_tagged_typedef_of_the_same_name_is_not_a_collision(self) -> None:
        """``typedef enum E E;`` names one entity twice, not two symbols."""
        header = Header(
            path="tagged.h",
            declarations=[
                Enum(name="E", values=[EnumValue("X", 0)], is_typedef=True),
                Typedef(name="E", underlying_type=CType("enum E")),
            ],
        )
        assert "E*" in write_nim(header, header_path="tagged.h")


#: A record and a free function whose *members* collide under Nim's identity
#: rules. Module scope is clean in both: ``Holder`` and ``go`` are the only
#: module-level names, so nothing headerkit checks today has anything to say.
_FIELD_COLLISION_HEADER = Header(
    path="fields.h",
    declarations=[Struct(name="Holder", fields=[Field("fooBar", CType("int")), Field("foo_bar", CType("int"))])],
)
_PARAM_COLLISION_HEADER = Header(
    path="params.h",
    declarations=[
        Function(
            name="go",
            return_type=CType("void"),
            parameters=[Parameter("fooBar", CType("int")), Parameter("foo_bar", CType("int"))],
        )
    ],
)


@pytest.mark.allow("subprocess")
@pytest.mark.timeout(300)
class TestFieldAndParamCollisionsAreNotDetectedYet:
    """Paired pins on a known gap: closing it must turn exactly one of each pair red.

    Collision checking covers the module-level namespace only. Nim rejects a
    colliding field or parameter just as firmly -- measured on Nim 2.2.10, an
    object with both ``fooBar`` and ``foo_bar`` is ``attempt to redefine:
    'foo_bar'``, and so is a proc taking both -- so these headers produce a
    module the compiler refuses, with no diagnostic from headerkit.

    Each case is pinned TWICE, and the two pins disagree about the future on
    purpose:

    * a strict xfail asserting the END STATE, which is the **refusal** headerkit
      already performs at module scope. A single pin asserting "the module
      compiles" was wrong about the likely fix: that fix makes ``write_nim``
      raise, the test still fails, ``strict=True`` still reports xfail, and the
      pin stays green through the very change it was written to catch.
    * an ordinary test recording what is measured TODAY -- headerkit emits, and
      the real compiler refuses what it emitted. It goes red the moment
      ``write_nim`` starts refusing, whatever the xfail does.

    One of each pair must flip. There is no state of the code in which both stay
    green, which is the property a single pin could not have.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="field-level collisions are not detected yet; needs per-record identity from the IR contract work",
    )
    def test_field_collision_is_refused(self) -> None:
        """The end state: headerkit refuses, as it does for a module-scope collision."""
        with pytest.raises(SymbolCollisionError):
            write_nim(_FIELD_COLLISION_HEADER, header_path="fields.h")

    def test_field_collision_is_emitted_and_the_compiler_refuses_it(self, tmp_path: Path) -> None:
        """Today's measured behaviour, and the pin that goes red when the refusal lands."""
        source = write_nim(_FIELD_COLLISION_HEADER, header_path="fields.h")
        nim_check_rejects(source, tmp_path, stem="fields", expect="attempt to redefine")

    @pytest.mark.xfail(
        strict=True,
        reason="parameter-level collisions are not detected yet; needs per-proc identity from the IR contract work",
    )
    def test_param_collision_is_refused(self) -> None:
        """The end state: headerkit refuses, as it does for a module-scope collision."""
        with pytest.raises(SymbolCollisionError):
            write_nim(_PARAM_COLLISION_HEADER, header_path="params.h")

    def test_param_collision_is_emitted_and_the_compiler_refuses_it(self, tmp_path: Path) -> None:
        """Today's measured behaviour, and the pin that goes red when the refusal lands."""
        source = write_nim(_PARAM_COLLISION_HEADER, header_path="params.h")
        nim_check_rejects(source, tmp_path, stem="params", expect="attempt to redefine")


class TestResolveCollisionHook:
    """The resolver decides; the writer re-checks and never trusts it."""

    @staticmethod
    def colliding_header() -> Header:
        return Header(
            path="collide.h",
            declarations=[
                Function(name="fooBar", return_type=CType("int")),
                Function(name="foo_bar", return_type=CType("int")),
            ],
        )

    def test_a_resolver_that_separates_them_produces_loadable_nim(self, tmp_path: Path, clean_hooks: None) -> None:
        def resolver(collided, target, *, context, **_):  # noqa: ARG001
            return {sym: f"{target}{sym.index}" for sym in collided}

        HookRegistry.register_global("resolve_collision", resolver, priority=Priority.PROJECT, writer="nim")
        out = write_nim(self.colliding_header(), header_path="collide.h")
        assert "proc fooBar0*" in out
        assert "proc fooBar1*" in out
        nim_check(out, tmp_path, stem="resolved")

    def test_a_resolver_returning_a_still_colliding_pair_is_an_error(self, clean_hooks: None) -> None:
        """The planted failure: it proves the re-check runs at all.

        A resolver that renames both members to identifiers Nim still considers
        one identifier has resolved nothing. If the dispatcher trusted the
        mapping this would emit and the failure would surface as a compile error
        in the user's build, or not at all.
        """

        def bad_resolver(collided, target, *, context, **_):  # noqa: ARG001
            spellings = ["someName", "some_name"]
            return {sym: spellings[i] for i, sym in enumerate(collided)}

        HookRegistry.register_global("resolve_collision", bad_resolver, priority=Priority.PROJECT, writer="nim")
        with pytest.raises(SymbolCollisionError) as excinfo:
            write_nim(self.colliding_header(), header_path="collide.h")
        assert "still share the identifier" in str(excinfo.value)

    def test_a_resolver_returning_a_partial_mapping_is_an_error(self, clean_hooks: None) -> None:
        def partial_resolver(collided, target, *, context, **_):  # noqa: ARG001
            return {collided[0]: f"{target}Only"}

        HookRegistry.register_global("resolve_collision", partial_resolver, priority=Priority.PROJECT, writer="nim")
        with pytest.raises(RenameError) as excinfo:
            write_nim(self.colliding_header(), header_path="collide.h")
        assert "partial mapping" in str(excinfo.value)

    def test_a_resolver_producing_an_illegal_identifier_is_an_error(self, clean_hooks: None) -> None:
        def illegal_resolver(collided, target, *, context, **_):  # noqa: ARG001
            return {sym: f"bad__{sym.index}" for sym in collided}

        HookRegistry.register_global("resolve_collision", illegal_resolver, priority=Priority.PROJECT, writer="nim")
        with pytest.raises(RenameError) as excinfo:
            write_nim(self.colliding_header(), header_path="collide.h")
        assert "consecutive underscores" in str(excinfo.value)


class TestRenamerPriorityFloor:
    """A project renamer runs first; the language's legality rules run last."""

    def test_a_project_rename_to_an_illegal_name_is_repaired_not_emitted(self, clean_hooks: None) -> None:
        def shouty(name: str, *, context, kind: str, **_):  # noqa: ARG001
            return f"{name}__x"

        HookRegistry.register_global("rename_symbol", shouty, priority=Priority.PROJECT, writer="nim")
        header = Header(path="p.h", declarations=[Function(name="go", return_type=CType("void"))])
        out = write_nim(header, header_path="p.h")
        assert "proc go_x*" in out
        assert "go__x" not in out.split("importc")[0]

    def test_a_project_renamer_can_be_scoped_to_one_writer(self, clean_hooks: None) -> None:
        def only_for_ctypes(name: str, *, context, kind: str, **_):  # noqa: ARG001
            return f"ct_{name}"

        HookRegistry.register_global("rename_symbol", only_for_ctypes, priority=Priority.PROJECT, writer="ctypes")
        header = Header(path="p.h", declarations=[Function(name="go", return_type=CType("void"))])
        assert "proc go*" in write_nim(header, header_path="p.h")

    def test_kind_is_passed_so_rules_can_differ_per_kind(self, clean_hooks: None) -> None:
        seen: list[tuple[str, str]] = []

        def record(name: str, *, context, kind: str, **_):  # noqa: ARG001
            seen.append((name, kind))
            return name

        HookRegistry.register_global("rename_symbol", record, priority=Priority.PROJECT, writer="nim")
        header = Header(
            path="p.h",
            declarations=[
                Function(name="go", return_type=CType("int"), parameters=[Parameter("n", CType("int"))]),
                Enum(name="Color", values=[EnumValue("RED", 0)]),
            ],
        )
        write_nim(header, header_path="p.h")
        assert ("go", "function") in seen
        assert ("Color", "enum") in seen
        assert ("RED", "enumerator") in seen
        assert ("n", "param") in seen


class TestDeclarativeConfig:
    """The config half: named transforms, composed in declared order."""

    @pytest.mark.parametrize(
        ("name", "style", "expected"),
        [
            ("foo_bar_baz", "camel", "fooBarBaz"),
            ("foo_bar_baz", "pascal", "FooBarBaz"),
            ("fooBarBaz", "snake", "foo_bar_baz"),
            ("HTTPServer", "snake", "http_server"),
            ("foo_bar", "preserve", "foo_bar"),
            ("_reserved", "camel", "_reserved"),
        ],
    )
    def test_case_conversion(self, name: str, style: str, expected: str) -> None:
        assert apply_case(name, style) == expected

    def test_rules_compose_in_declared_order(self) -> None:
        config = RenameConfig(
            rules=(
                RenameRule(strip_prefix="juce_", case="camel"),
                RenameRule(add_prefix="hk", kinds=("function",)),
            )
        )
        assert config.apply("juce_audio_buffer", "function") == "hkaudioBuffer"
        assert config.apply("juce_audio_buffer", "struct") == "audioBuffer"

    def test_collapse_underscores_is_available_declaratively(self) -> None:
        config = RenameConfig(rules=(RenameRule(collapse_underscores=True),))
        assert config.apply("a__b__c", "function") == "a_b_c"

    def test_unknown_kind_is_refused_at_construction(self) -> None:
        with pytest.raises(RenameError):
            RenameRule(kinds=("widget",))

    def test_unknown_collision_policy_is_refused(self) -> None:
        with pytest.raises(RenameError):
            RenameConfig(collision_policy="whatever_seems_best")

    def test_parse_rejects_unknown_rule_keys(self) -> None:
        with pytest.raises(RenameError):
            parse_rename_config({"rules": [{"stripprefix": "x"}]})

    def test_config_file_round_trip(self, tmp_path: Path) -> None:
        (tmp_path / ".headerkit.toml").write_text(
            textwrap.dedent("""\
                [rename]
                collision_policy = "prefer_first_declared"
                [[rename.rules]]
                strip_prefix = "juce_"
                case = "camel"
                kinds = ["function"]
            """),
            encoding="utf-8",
        )
        config = load_config(tmp_path / ".headerkit.toml")
        assert config.rename.collision_policy == "prefer_first_declared"
        assert config.rename.apply("juce_get_thing", "function") == "getThing"
        assert config.rename.apply("juce_get_thing", "struct") == "juce_get_thing"

    @pytest.mark.parametrize("policy", ["prefer_shortest", "prefer_longest", "prefer_first_declared"])
    def test_preference_policies_are_deterministic(self, policy: str) -> None:
        resolver = make_config_resolver(RenameConfig(collision_policy=policy))
        assert resolver is not None
        symbols = [
            Symbol(index=0, name="fooBar", kind="function", header="a.h"),
            Symbol(index=1, name="foo_bar_x", kind="function", header="a.h"),
        ]
        first = resolver(symbols, "fooBar", context=NIM_CONTEXT)
        second = resolver(list(reversed(symbols)), "fooBar", context=NIM_CONTEXT)
        assert first == second
        assert len(set(first.values())) == 2

    def test_error_is_the_default_policy_and_registers_no_resolver(self) -> None:
        assert RenameConfig().collision_policy == "error"
        assert make_config_resolver(RenameConfig()) is None

    def test_suffix_header_stem_separates_symbols_from_different_headers(self) -> None:
        resolver = make_config_resolver(RenameConfig(collision_policy="suffix_header_stem"))
        assert resolver is not None
        mapping = resolver(
            [
                Symbol(index=0, name="fooBar", kind="function", header="/inc/alpha.h"),
                Symbol(index=1, name="foo_bar", kind="function", header="/inc/beta.h"),
            ],
            "fooBar",
            context=NIM_CONTEXT,
        )
        assert sorted(mapping.values()) == ["fooBar_alpha", "fooBar_beta"]

    def test_suffix_header_stem_cannot_separate_one_headers_symbols_and_says_so(self, clean_hooks: None) -> None:
        """Appending the same stem twice resolves nothing, and the re-check catches it.

        Nim compares identifiers with underscores removed and case folded after
        the first character, so ``fooBar_h`` and ``foo_bar_h`` remain one
        identifier. The policy is honest about the cases it cannot serve rather
        than emitting a module the compiler rejects.
        """
        register_config_hooks(RenameConfig(collision_policy="suffix_header_stem"), writer="nim")
        header = Header(
            path="collide.h",
            declarations=[
                Function(name="fooBar", return_type=CType("int")),
                Function(name="foo_bar", return_type=CType("int")),
            ],
        )
        with pytest.raises(SymbolCollisionError):
            write_nim(header, header_path="collide.h")


class TestInjectivityDispatcher:
    """Direct tests of the language-agnostic half."""

    def test_a_resolver_naming_symbols_it_was_not_asked_about_is_an_error(self, clean_hooks: None) -> None:
        a = Symbol(index=0, name="fooBar", kind="function")
        b = Symbol(index=1, name="foo_bar", kind="function")
        stranger = Symbol(index=9, name="elsewhere", kind="function")

        def meddling(collided, target, *, context, **_):  # noqa: ARG001
            return {a: "one", b: "two", stranger: "three"}

        HookRegistry.register_global("resolve_collision", meddling, priority=Priority.PROJECT, writer="nim")
        with pytest.raises(RenameError) as excinfo:
            enforce_injectivity(
                {a: "fooBar", b: "fooBar"},
                identity=nim_ident_identity,
                context=NIM_CONTEXT,
            )
        assert "not asked about" in str(excinfo.value)

    def test_distinct_identifiers_pass_through_untouched(self) -> None:
        a = Symbol(index=0, name="alpha", kind="function")
        b = Symbol(index=1, name="beta", kind="function")
        assigned = {a: "alpha", b: "beta"}
        assert enforce_injectivity(assigned, identity=nim_ident_identity, context=NIM_CONTEXT) == assigned


class TestRenameEntersTheCacheKey:
    """Rename config changes generated output, so it must change the key."""

    def test_the_fingerprint_moves_with_the_registered_hooks(self, clean_hooks: None) -> None:
        before = rename_cache_fingerprint()
        register_config_hooks(RenameConfig(rules=(RenameRule(add_prefix="hk"),)), writer="nim")
        assert rename_cache_fingerprint() != before

    def test_two_different_rename_configs_do_not_share_a_fingerprint(self, clean_hooks: None) -> None:
        saved = HookRegistry.snapshot()
        register_config_hooks(RenameConfig(rules=(RenameRule(add_prefix="hk"),)), writer="nim")
        first = rename_cache_fingerprint()
        HookRegistry.restore(saved)
        register_config_hooks(RenameConfig(rules=(RenameRule(add_prefix="zz"),)), writer="nim")
        assert rename_cache_fingerprint() != first

    def test_the_fingerprint_reaches_the_output_cache_key(self) -> None:
        common = {"ir_cache_key": "abc", "writer_name": "nim", "writer_cache_version": "1"}
        assert compute_output_cache_key(**common, rename_fingerprint="one") != compute_output_cache_key(
            **common, rename_fingerprint="two"
        )

    def test_changing_only_the_rename_config_misses_the_output_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_hooks: None
    ) -> None:
        """The end-to-end form: two generates, one changed rule, two cache entries.

        A key that ignored renaming would read the first entry back and report a
        hit, handing the caller bindings under the previous names with no
        diagnostic anywhere.
        """
        (tmp_path / ".git").mkdir()
        header_file = tmp_path / "test.h"
        header_file.write_text("int add(int a, int b);", encoding="utf-8")
        parsed = Header(str(header_file), [Function("add", CType("int"), [Parameter("a", CType("int"))])])
        backend = MagicMock()
        backend.parse.return_value = parsed
        backend.name = "libclang"
        monkeypatch.setattr("headerkit._generate.get_backend", lambda _name: backend)

        store = tmp_path / ".headerkit"
        first = generate(header_path=header_file, writer_name="nim", backend_name="libclang", store_dir=store)

        saved = HookRegistry.snapshot()
        register_config_hooks(RenameConfig(rules=(RenameRule(add_prefix="hk"),)), writer="nim")
        second = generate(header_path=header_file, writer_name="nim", backend_name="libclang", store_dir=store)
        HookRegistry.restore(saved)

        assert "proc add*" in first
        assert "proc hkadd*" in second, "a stale cache hit would have returned the first output verbatim"

        index = json.loads((store / "output" / "nim" / "index.json").read_text(encoding="utf-8"))
        assert len(index) == 2, f"expected a cache MISS to add a second entry, index holds {list(index)}"


#: The declarative collision policies that resolve rather than refuse. The
#: ``suffix_header_stem`` policy is not among them here: these symbols come from
#: one header, so appending its stem to both resolves nothing and the re-check
#: correctly refuses -- which `TestResolveCollisionHook` already pins.
_RESOLVING_POLICIES = ("prefer_shortest", "prefer_longest", "prefer_first_declared")


@pytest.mark.allow("subprocess")
@pytest.mark.timeout(300)
class TestResolvedCollisionsReachTheEmitter:
    """A resolved collision must reach the emitter as TWO identifiers.

    The resolution was computed correctly and then thrown away: the writer's name
    map was keyed by source name, and two colliding symbols share a source name
    by definition, so the second entry overwrote the first and both declarations
    were emitted under the same identifier. Every declarative policy was affected
    and none of them said anything -- the injectivity check ran over a mapping
    keyed by symbol, where the answer was still correct.

    Only the compiler can arbitrate this. An assertion on the emitted text sees
    two plausible declarations; ``nim check`` sees ``redefinition``.
    """

    @staticmethod
    def enum_and_function_header() -> Header:
        """``enum status {...}`` beside ``int status(void)``: legal C, one Nim identifier."""
        return Header(
            path="collide.h",
            declarations=[
                Enum(name="status", values=[EnumValue("STATUS_OK", 0)]),
                Function(name="status", return_type=CType("int")),
            ],
        )

    @pytest.mark.parametrize("policy", _RESOLVING_POLICIES)
    def test_a_resolved_collision_compiles(self, policy: str, clean_hooks: None, tmp_path: Path) -> None:
        register_config_hooks(RenameConfig(collision_policy=policy), writer="nim")
        source = write_nim(self.enum_and_function_header(), header_path="collide.h")
        nim_check(source, tmp_path, stem=f"resolved_{policy}")

    @pytest.mark.parametrize("policy", _RESOLVING_POLICIES)
    def test_the_two_declarations_are_spelled_differently(self, policy: str, clean_hooks: None) -> None:
        """The compile gate above is the arbiter; this names what it is arbitrating."""
        register_config_hooks(RenameConfig(collision_policy=policy), writer="nim")
        source = write_nim(self.enum_and_function_header(), header_path="collide.h")
        type_name = next(line.split("*")[0].strip() for line in source.splitlines() if "= enum" in line)
        proc_name = next(
            line.split("*")[0].removeprefix("proc ").strip() for line in source.splitlines() if "proc " in line
        )
        assert nim_ident_identity(type_name) != nim_ident_identity(proc_name), source

    def test_references_to_a_resolved_type_follow_it(self, clean_hooks: None, tmp_path: Path) -> None:
        """A use of the renamed enum must name the identifier the declaration got."""
        register_config_hooks(RenameConfig(collision_policy="prefer_shortest"), writer="nim")
        header = Header(
            path="collide.h",
            declarations=[
                Enum(name="status", values=[EnumValue("STATUS_OK", 0)]),
                Function(name="status", return_type=CType("int")),
                Function(name="report", return_type=CType("void"), parameters=[Parameter("s", CType("status"))]),
            ],
        )
        nim_check(write_nim(header, header_path="collide.h"), tmp_path, stem="resolved_reference")


@pytest.mark.allow("subprocess")
@pytest.mark.timeout(300)
class TestATypedefIsOnlyTheTagsAliasWhenItAliasesTheTag:
    """``typedef int status;`` beside ``enum status`` is two entities, not one.

    The dedup that exists for the ``typedef struct foo foo;`` idiom keyed on the
    bare fact that a typedef and a tag shared a name, so it swallowed this pair
    too: ``status*`` was emitted twice and ``nim check`` reported
    ``redefinition of 'status'``. Both the docstring of ``MODULE_SCOPE_KINDS``
    and the collision error itself told the reader the only undetected gap was at
    field and parameter level.
    """

    @staticmethod
    def enum_and_unrelated_typedef() -> Header:
        return Header(
            path="two.h",
            declarations=[
                Enum(name="status", values=[EnumValue("STATUS_OK", 0)]),
                Typedef(name="status", underlying_type=CType("int")),
            ],
        )

    def test_it_is_refused_under_the_default_policy(self) -> None:
        with pytest.raises(SymbolCollisionError) as excinfo:
            write_nim(self.enum_and_unrelated_typedef(), header_path="two.h")
        assert "'status' (enum)" in str(excinfo.value)
        assert "'status' (typedef)" in str(excinfo.value)

    def test_the_tagged_typedef_idiom_is_still_one_entity(self, tmp_path: Path) -> None:
        """The negative control: narrowing the clause must not start refusing this."""
        header = Header(
            path="tagged.h",
            declarations=[
                Enum(name="E", values=[EnumValue("X", 0)], is_typedef=True),
                Typedef(name="E", underlying_type=CType("enum E")),
            ],
        )
        source = write_nim(header, header_path="tagged.h")
        assert "E*" in source
        nim_check(source, tmp_path, stem="tagged")


@pytest.mark.allow("subprocess")
@pytest.mark.timeout(300)
class TestWriterSynthesizedNamesAreHeldToTheSameFloor:
    """``CppString`` and ``constructFoo`` are emitted names too.

    They are produced by the renderer rather than by the rename waterfall, so
    nothing validated them and nothing checked them against the identifiers the
    naming pass had already assigned.
    """

    def test_a_cpp_helper_colliding_with_a_declared_type_is_refused(self) -> None:
        """``Cpp_String`` and ``CppString`` are one identifier to Nim."""
        header = Header(
            path="clash.hpp",
            language="cpp",
            declarations=[
                Struct(name="Cpp_String", fields=[Field("n", CType("int"))], is_cppclass=True),
                Struct(name="Holder", fields=[Field("s", CType("string"))], is_cppclass=True),
            ],
        )
        with pytest.raises(RenameError) as excinfo:
            write_nim(header, header_path="clash.hpp")
        assert "Cpp_String" in str(excinfo.value)

    def test_a_constructor_is_named_from_the_renamed_type(self, tmp_path: Path) -> None:
        """Composed from the source spelling this emitted ``constructmy__type``."""
        header = Header(
            path="ctor.hpp",
            language="cpp",
            declarations=[
                Struct(
                    name="my__type",
                    is_cppclass=True,
                    constructors=[Function(name="my__type", return_type=CType("void"))],
                )
            ],
        )
        source = write_nim(header, header_path="ctor.hpp")
        assert "constructmy_type" in source, source
        nim_check(source, tmp_path, stem="ctor")


class TestTheGrammarFloorIsNotBypassableByConfiguration:
    """A ``PROJECT`` renamer is what a ``[rename]`` section registers.

    ``validate_nim_ident`` returned early for anything in backticks, so a renamer
    answering with a backticked payload had arbitrary text emitted straight into
    the generated module -- past the validator whose stated purpose is that no
    configuration can reach around it.
    """

    #: Closes the `importc` pragma, ends the line, and opens a declaration of its
    #: own. This is the payload, verbatim, that the early return admitted.
    INJECTION = '`evil".}\n\nproc pwned*() {.importc: "pwned", header: "x.h".}\nvar x* = `'

    def test_the_payload_is_refused_rather_than_emitted(self, clean_hooks: None) -> None:
        def injecting_renamer(name: str, **_: object) -> str:  # noqa: ARG001
            return self.INJECTION

        HookRegistry.register_global("rename_symbol", injecting_renamer, priority=Priority.PROJECT, writer="nim")
        header = Header(path="x.h", declarations=[Function(name="ok", return_type=CType("void"))])
        with pytest.raises(RenameError) as excinfo:
            write_nim(header, header_path="x.h")
        assert "between backticks" in str(excinfo.value)

    def test_the_payload_never_reaches_the_output(self, clean_hooks: None) -> None:
        """The refusal is what matters; this states what would otherwise be emitted."""

        def injecting_renamer(name: str, **_: object) -> str:  # noqa: ARG001
            return self.INJECTION

        HookRegistry.register_global("rename_symbol", injecting_renamer, priority=Priority.PROJECT, writer="nim")
        header = Header(path="x.h", declarations=[Function(name="ok", return_type=CType("void"))])
        try:
            source = write_nim(header, header_path="x.h")
        except RenameError:
            return
        pytest.fail(f"the injected declaration was emitted instead of refused:\n{source}")

    def test_a_legitimate_backticked_keyword_still_passes(self) -> None:
        """The negative control: the floor must not have closed on what it exists to allow."""
        for legal in ("`type`", "`+`", "`==`", "`[]`", "`shl=`"):
            validate_nim_ident(legal)

    @pytest.mark.parametrize(
        "payload",
        ['`a".}b`', "`a\nb`", "`foo bar`", "`a;b`", "``", "`a`b`"],
    )
    def test_backtick_content_outside_the_floor_is_refused(self, payload: str) -> None:
        with pytest.raises(RenameError):
            validate_nim_ident(payload)
