"""Regression pins for backend/writer defects fixed on the libclang-cython-output branch.

Every test here drives the real pipeline -- real C or C++ source through a real
parser backend into a real writer.  Hand-constructed IR is deliberately avoided:
each defect pinned below produced correct-looking IR assertions while the
generated output stayed wrong, so IR-level tests are what let these bugs through.

Scope of this module:

R5  Tag-less ``typedef enum`` must reach Cython as ``ctypedef enum`` and reach the
    cffi writer without an invented tag.
R6  A C tag colliding with a Cython keyword must carry its ``"cname"`` on
    *forward* declarations, not only on definitions.
R7  ``LibclangBackend.parse(whitelist=...)`` retains declarations from included
    files, by resolved-path match rather than substring match.
R9  The tree-sitter backend must not drop ``const``/``volatile`` from any
    declaration position, and must not fold a *trailing* C++ qualifier into a
    return type.

Cross-backend parity is the last section and is the highest-value part: both
backends feed the same Cython writer, so a divergence between them is a defect in
one of them.  Known divergences are recorded as strict xfails naming the defect so
they stay visible instead of being silently tolerated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import textwrap
from pathlib import Path

import pytest

from headerkit.backends import get_backend, is_backend_available
from headerkit.writers.cffi import header_to_cffi
from headerkit.writers.cython import write_pxd

libclang = pytest.mark.libclang
treesitter = pytest.mark.treesitter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pxd(backend_name: str, code: str, filename: str = "test.h", extra_args: list[str] | None = None) -> str:
    """Parse ``code`` with a named backend and render it through the Cython writer."""
    backend = get_backend(backend_name)
    return write_pxd(backend.parse(code, filename, extra_args=extra_args or []))


def _require_c_toolchain() -> str:
    """Skip unless both Cython and a C compiler are present; return the compiler path.

    The skip is narrow and explicit on purpose. A compile check that quietly
    no-ops when the toolchain is absent proves nothing while looking green.
    """
    pytest.importorskip("Cython", reason="Cython is required to verify generated C")
    for candidate in ("cc", "gcc", "clang"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no C compiler (cc/gcc/clang) on PATH")


def _cythonize(workdir: Path, header: str, pxd: str, pyx: str, stem: str = "mod") -> str:
    """Write a header/.pxd/.pyx triple, run Cython, and return the generated C source.

    :raises AssertionError: if Cython itself rejects the ``.pxd``/``.pyx`` pair.
    """
    (workdir / "test.h").write_text(header)
    (workdir / "defs.pxd").write_text(pxd)
    (workdir / f"{stem}.pyx").write_text(pyx)

    c_path = workdir / f"{stem}.c"
    result = subprocess.run(
        [sys.executable, "-m", "cython", "-3", f"{stem}.pyx", "-o", str(c_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"cython failed:\n{result.stdout}\n{result.stderr}"
    return c_path.read_text()


def _compile_c(workdir: Path, compiler: str, stem: str = "mod") -> subprocess.CompletedProcess[str]:
    """Compile a generated C file to an object file, without linking."""
    return subprocess.run(
        [
            compiler,
            "-c",
            f"{stem}.c",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{workdir}",
            "-o",
            f"{stem}.o",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# R5 -- tag-less typedef'd enums
# ---------------------------------------------------------------------------


@libclang
class TestR5TaglessTypedefEnum:
    """``typedef enum { ... } Name;`` declares no ``enum Name`` tag.

    The Cython text of this fix is pinned in ``test_integration/test_roundtrip_cython.py``.
    What is pinned here is the consequence that motivated it -- the generated C --
    plus the C++ gate and the cffi writer's half of the same fix.
    """

    def test_cplusplus_enum_output_is_unchanged(self) -> None:
        """C++ has no separate tag namespace, so the C-only fix must not touch C++.

        In C++ ``enum Plain`` and ``Plain`` name the same type, so ``cdef enum``
        is always correct and the tag-less form needs no special case. If the
        discriminator were applied unconditionally, the first case below would
        become ``ctypedef enum``.
        """
        cpp_args = ["-x", "c++"]

        assert _pxd("libclang", "typedef enum { C1, C2 } MyEnumType;", "t.hpp", cpp_args) == textwrap.dedent("""\
            cdef extern from "t.hpp":



                cdef enum MyEnumType:
                    C1
                    C2
        """)

        assert _pxd("libclang", "typedef enum LogLevel { L1, L2 } LogLevel;", "t.hpp", cpp_args) == textwrap.dedent(
            """\
            cdef extern from "t.hpp":



                cdef enum LogLevel:
                    L1
                    L2
        """
        )

        assert _pxd("libclang", "enum Plain { A };", "t.hpp", cpp_args) == textwrap.dedent("""\
            cdef extern from "t.hpp":

                cdef enum Plain:
                    A
        """)

    def test_cffi_tagless_enum_invents_no_tag(self) -> None:
        """The cffi writer must not emit ``typedef enum Switch`` for a tag-less enum."""
        header = get_backend("libclang").parse("typedef enum { OFF, ON } Switch;", "test.h")
        assert header_to_cffi(header) == textwrap.dedent("""\
            typedef enum {
                OFF = 0,
                ON = 1,
            } Switch;""")

    def test_cffi_tagged_enum_keeps_its_real_tag(self) -> None:
        """A TAGGED typedef enum really does declare ``enum Switch``; the tag must survive.

        Sibling of the tag-less case above. ``_find_typedef_enum_pairs`` previously
        selected on ``not decl.is_typedef``, which was True for every enum, so this
        case was collapsed into the tag-less form and lost its ``enum Switch`` tag.
        Pinning both sides is what makes the discriminator load-bearing.
        """
        header = get_backend("libclang").parse("typedef enum Switch { OFF, ON } Switch;", "test.h")
        assert header_to_cffi(header) == textwrap.dedent("""\
            enum Switch {
                OFF = 0,
                ON = 1,
            };
            typedef enum Switch Switch;""")

    @pytest.mark.timeout(300)
    def test_generated_c_completes_the_enum_type(self, tmp_path: Path) -> None:
        """The C-level consequence: ``cdef enum`` yields an incomplete type in the C.

        The negative control is the same pipeline with the single keyword reverted
        to its pre-fix spelling. It must fail to compile -- that is what proves the
        positive case is checking something.
        """
        compiler = _require_c_toolchain()
        source = "typedef enum { C1, C2, C3 } MyEnumType;\n"
        pxd = _pxd("libclang", source)
        assert "ctypedef enum MyEnumType:" in pxd

        pyx = textwrap.dedent("""\
            from defs cimport MyEnumType, C1

            cpdef public int use_it():
                cdef MyEnumType v = C1
                return <int>v
        """)

        good = tmp_path / "good"
        good.mkdir()
        _cythonize(good, source, pxd, pyx)
        result = _compile_c(good, compiler)
        assert result.returncode == 0, f"post-fix output failed to compile:\n{result.stderr}"

        # Negative control: revert the one keyword the fix changed.
        bad = tmp_path / "bad"
        bad.mkdir()
        _cythonize(bad, source, pxd.replace("ctypedef enum", "cdef enum"), pyx)
        reverted = _compile_c(bad, compiler)
        assert reverted.returncode != 0, "pre-fix output compiled cleanly; the compile check proves nothing"
        assert "incomplete type" in reverted.stderr


# ---------------------------------------------------------------------------
# R6 -- keyword-escape cnames on forward declarations
# ---------------------------------------------------------------------------


@libclang
class TestR6KeywordEscapeOnForwardDeclarations:
    """A C tag that collides with a Cython keyword needs its cname everywhere.

    ``_escape_name`` renames ``class`` to ``class_``; the ``"class"`` cname is what
    tells Cython the real C spelling. Omitting it on a forward declaration makes
    Cython emit ``struct class_``, a type no header declares.
    """

    def test_undeclared_record_forward_declarations_carry_cnames(self) -> None:
        """Site 2: a record used through a field but never defined.

        Pre-fix this emitted a bare ``cdef struct class``, which is a Cython
        *syntax* error -- ``class`` is a reserved word.
        """
        source = "struct node { struct class *c; union global *g; };"
        assert _pxd("libclang", source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct class_ "class"
                cdef union global_ "global"

                cdef struct node:
                    class_* c
                    global_* g
        """)

    def test_cycle_forward_declarations_carry_cnames(self) -> None:
        """Site 1: the dependency-cycle forward-declaration path.

        A struct-pointer cycle is not enough to reach this path -- only non-pointer
        use creates a struct->struct edge. The ``class_t`` typedef edge is
        unconditional, so it is what forces the cycle here.
        """
        source = textwrap.dedent("""\
            typedef struct class class_t;
            struct class { class_t *self; int x; };
            struct node { struct class *c; };
        """)
        assert _pxd("libclang", source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct node
                cdef struct class_ "class"

                ctypedef class_ class_t


                cdef struct class_ "class"


                cdef struct node:
                    class_* c

                cdef struct class_ "class":
                    class_t* self
                    int x
        """)

    def test_keyword_typedef_enum_emits_no_self_referential_alias(self) -> None:
        """The circular-typedef guard must compare bare names, not cname-annotated ones.

        ``_escape_name(..., include_c_name=True)`` yields ``with_ "with"``, which never
        equals what ``_format_type`` produces, so the guard missed the self-reference
        and emitted a bogus ``ctypedef with_ with_ "with"`` line.
        """
        result = _pxd("libclang", "typedef enum { ZERO, ONE } with;")
        assert result == textwrap.dedent("""\
            cdef extern from "test.h":



                ctypedef enum with_ "with":
                    ZERO
                    ONE
        """)

    @pytest.mark.timeout(300)
    def test_generated_c_names_the_real_tag(self, tmp_path: Path) -> None:
        """The C-level consequence: the generated C must say ``struct class``.

        The pre-fix pxd still compiles -- ``struct class_`` is merely an incomplete
        type, and C permits pointers to those -- so a compile check alone would pass
        and prove nothing. The discriminating artifact is the generated C text, which
        must name the tag the header actually declares.
        """
        _require_c_toolchain()
        # The records are deliberately never defined: only a record that is used
        # but not defined reaches the forward-declaration path this fix repaired.
        # Adding definitions makes the definitions carry the cnames instead, and
        # the test stops discriminating.
        source = "struct node { struct class *c; union global *g; };\n"
        pxd = _pxd("libclang", source)
        assert 'cdef struct class_ "class"' in pxd
        pyx = textwrap.dedent("""\
            from defs cimport node, class_, global_

            cpdef public int use_it(long addr):
                cdef node *n = <node*>addr
                cdef class_ *c = n.c
                cdef global_ *g = n.g
                return <int>(c != NULL) + <int>(g != NULL)
        """)

        good = tmp_path / "good"
        good.mkdir()
        generated = _cythonize(good, source, pxd, pyx)
        assert "struct class_" not in generated
        assert "union global_" not in generated
        assert "struct class " in generated
        assert "union global " in generated

        # Negative control: strip the cnames the fix added. The generated C then
        # names types that do not exist in the header.
        bad = tmp_path / "bad"
        bad.mkdir()
        reverted = _cythonize(bad, source, pxd.replace(' "class"', "").replace(' "global"', ""), pyx)
        assert "struct class_" in reverted
        assert "struct class " not in reverted


# ---------------------------------------------------------------------------
# R7 -- whitelisted symbols from included headers
# ---------------------------------------------------------------------------


@libclang
class TestR7IncludeWhitelist:
    """``parse(whitelist=...)`` keeps declarations that arrive through ``#include``."""

    @staticmethod
    def _fixture(tmp_path: Path) -> tuple[Path, str]:
        """Create a main header whose entire content is an include, and return it."""
        (tmp_path / "other.h").write_text("struct Widget { int w; };\nint widget_size(struct Widget *x);\n")
        main = tmp_path / "main.h"
        main.write_text('#include "other.h"\n')
        return main, main.read_text()

    @staticmethod
    def _expected(main: Path) -> str:
        return textwrap.dedent(f"""\
            cdef extern from "{main}":

                cdef struct Widget:
                    int w

                int widget_size(Widget* x)
        """)

    def test_without_whitelist_included_declarations_are_dropped(self, tmp_path: Path) -> None:
        """The negative direction, and the one that proves the fix stayed additive.

        Without this case a fix that simply disabled the include filter altogether
        would pass every other test in this class.
        """
        main, source = self._fixture(tmp_path)
        result = write_pxd(get_backend("libclang").parse(source, str(main)))
        assert result == f'cdef extern from "{main}":\n    pass\n'

    @pytest.mark.parametrize("style", ["basename", "absolute", "dotdot", "symlink"])
    def test_whitelist_entry_resolution(self, tmp_path: Path, style: str) -> None:
        """A whitelist entry is resolved to an absolute, symlink-free path before matching.

        A bare basename resolves against the parsed file's own directory, which is
        the common case; ``..`` segments and symlinks must normalize away rather
        than defeat the match.
        """
        main, source = self._fixture(tmp_path)
        entry = {
            "basename": "other.h",
            "absolute": str(tmp_path / "other.h"),
            "dotdot": str(tmp_path / "sub" / ".." / "other.h"),
        }.get(style)
        if entry is None:
            link = tmp_path / "linked"
            os.symlink(tmp_path, link)
            entry = str(link / "other.h")

        result = write_pxd(get_backend("libclang").parse(source, str(main), whitelist=[entry]))
        assert result == self._expected(main)

    def test_whitelist_match_is_not_a_substring_test(self, tmp_path: Path) -> None:
        """``oo.h`` must not match ``foo.h``.

        The pre-fix filter compared unnormalized substrings, so any suffix of a real
        path silently whitelisted it. Matching is a whole-path comparison.
        """
        main, source = self._fixture(tmp_path)
        result = write_pxd(get_backend("libclang").parse(source, str(main), whitelist=["er.h"]))
        assert result == f'cdef extern from "{main}":\n    pass\n'

    def test_whitelisted_record_referenced_before_definition_is_forward_declared(self, tmp_path: Path) -> None:
        """A record used above its own definition still needs a forward declaration."""
        (tmp_path / "other.h").write_text("int widget_size(struct Widget *x);\nstruct Widget { int w; };\n")
        main = tmp_path / "main.h"
        main.write_text('#include "other.h"\n')

        result = write_pxd(get_backend("libclang").parse(main.read_text(), str(main), whitelist=["other.h"]))
        assert result == textwrap.dedent(f"""\
            cdef extern from "{main}":

                cdef struct Widget

                int widget_size(Widget* x)

                cdef struct Widget:
                    int w
        """)


# ---------------------------------------------------------------------------
# R9 -- tree-sitter const/volatile qualifiers
# ---------------------------------------------------------------------------


@treesitter
class TestR9TreeSitterQualifiers:
    """Leading ``const``/``volatile`` were dropped across many declaration positions.

    The C grammar attaches a declaration's leading qualifiers as siblings of the
    ``type`` field, so reading that field alone silently loses them.
    """

    @pytest.mark.parametrize(
        ("position", "source", "expected_body"),
        [
            ("struct_field", "struct S { const char *name; };", "    cdef struct S:\n        const char* name"),
            (
                "union_field",
                "union U { const char *s; int i; };",
                "    cdef union U:\n        const char* s\n        int i",
            ),
            ("parameter", "void f(const char *s);", "    void f(const char* s)"),
            ("return_type", "const char *g(void);", "    const char* g()"),
            ("typedef", "typedef const char *cstr;", "    ctypedef const char* cstr"),
            ("global", "extern const char *gp;", "    const char* gp"),
            ("array_element", "extern const char *tbl[4];", "    const char* tbl[4]"),
            ("pointer_level_const", "void h(char * const p);", "    void h(char* const p)"),
        ],
    )
    def test_const_survives_in_every_position(self, position: str, source: str, expected_body: str) -> None:
        assert _pxd("tree-sitter", source) == f'cdef extern from "test.h":\n\n{expected_body}\n'

    def test_trailing_cpp_qualifier_is_not_folded_into_the_return_type(self) -> None:
        """A trailing ``const`` qualifies the method, not its return type.

        This is the way the qualifier fix is most likely to break silently: folding
        every ``type_qualifier`` sibling would turn ``int f() const`` into
        ``const int f()``, which is a different signature. The qualifier fold is
        therefore restricted to nodes positioned *before* the type node.
        """
        source = "class C { public: int f() const; const char* g() const; };"
        assert _pxd("tree-sitter", source, "t.hpp", ["-x", "c++"]) == textwrap.dedent("""\
            cdef extern from "t.hpp":

                cdef cppclass C:
                    int f() const
                    const char* g() const
        """)

    def test_only_const_and_volatile_are_folded(self) -> None:
        """``_Atomic`` is a ``type_qualifier`` node too, and must not reach the output.

        The C grammar makes ``_Atomic`` a ``type_qualifier`` sibling exactly like
        ``const``, so an unfiltered fold produces ``CType("_Atomic int")`` -- a type
        name Cython cannot use.

        Two independent layers prevent that: the backend's
        ``_FOLDABLE_TYPE_QUALIFIERS`` allowlist, and the writer's
        ``UNSUPPORTED_TYPE_QUALIFIERS`` strip list. Either alone is sufficient, which
        is why this assertion only goes red when *both* are defeated. That is
        deliberate defense in depth, and the reason this test asserts the observable
        output rather than the internals of either layer.
        """
        assert _pxd("tree-sitter", "extern _Atomic int at;") == textwrap.dedent("""\
            cdef extern from "test.h":

                int at
        """)


# ---------------------------------------------------------------------------
# Cross-backend parity
# ---------------------------------------------------------------------------


def _xfail(reason: str) -> pytest.MarkDecorator:
    """Strict xfail: a divergence that gets fixed must surface as XPASS, not stay hidden."""
    return pytest.mark.xfail(strict=True, reason=reason)


PARITY_CASES = [
    pytest.param("void f(const char *s);", id="const_ptr_param"),
    pytest.param("const char *g(void);", id="const_return"),
    pytest.param("struct S { const char *name; int n; };", id="const_struct_field"),
    pytest.param("union U { const char *s; int i; };", id="const_union_field"),
    pytest.param("typedef const char *cstr;", id="const_typedef"),
    pytest.param("extern const char *gp;", id="const_global"),
    pytest.param("extern const char *tbl[4];", id="const_array"),
    pytest.param("extern const int m[2][3];", id="const_array_2d"),
    pytest.param("void f(const int x);", id="const_value_param"),
    pytest.param("void h(char * const p);", id="pointer_level_const"),
    pytest.param("void h(const char * const p);", id="const_ptr_to_const"),
    pytest.param("void f(const void *p);", id="const_void_ptr"),
    pytest.param("extern const double *dp;", id="const_double_ptr"),
    pytest.param("extern volatile int vg;", id="volatile_global"),
    pytest.param("void r(char * __restrict p);", id="restrict_param"),
    pytest.param("void r(char * __restrict__ p);", id="restrict2_param"),
    pytest.param("struct S { char * __restrict__ p; };", id="restrict2_field"),
    pytest.param("extern _Atomic int at;", id="atomic_global"),
    pytest.param("void pp(char **x);", id="pointer_to_pointer"),
    pytest.param("extern int grid[3][4];", id="array_2d"),
    pytest.param("extern char *argv[8];", id="array_of_pointer"),
    pytest.param("enum Color { RED, GREEN };", id="enum_plain"),
    pytest.param("enum E { A = 5, B = 10 };", id="enum_explicit_values"),
    pytest.param("struct S { int (*cb)(int a); };", id="funcptr_field"),
    pytest.param("typedef void (*handler)(void);", id="funcptr_typedef_no_params"),
    pytest.param("struct P { int x; int y; };", id="struct_simple"),
    pytest.param("typedef struct P { int x; } P;", id="typedef_struct"),
    pytest.param("struct A { int x; }; struct B { struct A a; };", id="nested_struct_value"),
    pytest.param("struct A; struct B { struct A *a; };", id="struct_pointer_field"),
    pytest.param("int add(int a, int b);", id="function_two_params"),
    pytest.param("void nop(void);", id="function_void"),
    pytest.param("int p(const char *fmt, ...);", id="function_variadic"),
    pytest.param("extern unsigned long ul;", id="unsigned_long"),
    pytest.param("extern signed char sc;", id="signed_char"),
    pytest.param("extern long long ll;", id="long_long"),
    pytest.param("extern int a, b, c;", id="multiple_declarators"),
    # -- known divergences, recorded rather than hidden --------------------
    pytest.param(
        "extern volatile const int vc;",
        id="qualifier_ordering",
        marks=_xfail(
            "tree-sitter preserves source qualifier order ('volatile const int') while "
            "libclang normalizes to 'const volatile int'. Semantically identical; cosmetic only."
        ),
    ),
    pytest.param(
        "typedef enum Switch { OFF, ON } Switch;",
        id="tagged_typedef_enum",
        marks=_xfail(
            "DEFECT (tree-sitter): a TAGGED 'typedef enum Switch {...} Switch;' is marked "
            "is_typedef=True, so the writer emits 'ctypedef enum Switch' where libclang "
            "correctly emits 'cdef enum Switch'. This is the R5 defect class, still unfixed "
            "in the tree-sitter backend."
        ),
    ),
    pytest.param(
        "typedef enum { OFF, ON } Switch;",
        id="tagless_typedef_enum_layout",
        marks=_xfail(
            "Blank-line layout only: libclang routes typedef'd enums through the "
            "cycle-detection phases and emits two extra blank lines. Both spell "
            "'ctypedef enum Switch'."
        ),
    ),
    pytest.param("typedef int (*cb)(int a, char b);", id="funcptr_typedef_param_names"),
    pytest.param(
        "void reg(int (*cb)(int));",
        id="funcptr_parameter",
        marks=_xfail(
            "DEFECT (tree-sitter): a function-pointer PARAMETER collapses to its return type "
            "-- 'void reg(int)' -- losing the whole signature that libclang renders as "
            "'void reg(int (*cb)(int))'."
        ),
    ),
    pytest.param(
        "void f(const struct P *p);",
        id="undefined_struct_forward_decl",
        marks=_xfail(
            "DEFECT (tree-sitter): a record named but never defined gets no forward "
            "declaration, so the emitted 'const P* p' references an undeclared type. "
            "libclang emits 'cdef struct P'."
        ),
    ),
    pytest.param(
        "extern short int si;",
        id="short_int_spelling",
        marks=_xfail(
            "libclang canonicalizes 'short int' to 'short'; tree-sitter preserves the source "
            "spelling. Both are valid Cython; cosmetic only."
        ),
    ),
    pytest.param(
        "struct F { unsigned a : 3; unsigned b : 5; };",
        id="bitfield",
        marks=_xfail(
            "DEFECT (libclang): bit widths are lost, so the bitfield comment the tree-sitter "
            "path emits is absent and the fields render as plain 'unsigned int'."
        ),
    ),
]


@pytest.mark.skipif(
    not (is_backend_available("libclang") and is_backend_available("tree-sitter")),
    reason="cross-backend parity needs both the libclang and tree-sitter backends",
)
@pytest.mark.parametrize("source", PARITY_CASES)
def test_backends_agree_on_cython_output(source: str) -> None:
    """Both backends feed the same Cython writer, so their output must match.

    Divergence between the two is precisely how each backend's qualifier and enum
    defects were originally identified, which makes this the most sensitive check
    in the module: it needs no expected value to be maintained by hand, and any new
    asymmetry introduced on either side surfaces here.
    """
    assert _pxd("libclang", source) == _pxd("tree-sitter", source)
