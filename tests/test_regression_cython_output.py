"""Regression tests for libclang -> Cython ``.pxd`` output.

Every case here pins a defect that headerkit's own suite could not see: the
existing writer tests build IR by hand, so a parser that produced wrong IR --
clang's internal ``(unnamed at file:line:col)`` spellings, dropped members,
stripped ``const`` -- rendered "correctly" from hand-built nodes while the real
pipeline emitted invalid Cython.

Two rules therefore apply to this module:

* Drive the real pipeline. Parse C text with the libclang backend and render
  with :func:`headerkit.writers.cython.write_pxd`. Never hand-build IR.
* Assert the full output with ``==``. A substring check cannot see a dropped
  declaration, a duplicated block, or a wrong ordering, which is what these
  defects were.

Where Cython and a C compiler are installed, the generated ``.pxd`` is also
cythonized and the resulting C compiled against the original header. The
``.pyx`` must *use* the declarations -- read fields, call through pointers --
because a bare ``cimport`` emits no C for them and so cannot detect a wrong C
spelling. :class:`TestCompileHarness` proves the harness can fail.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import sysconfig
import textwrap
from pathlib import Path
from typing import Any

import pytest

from headerkit.backends import get_backend
from headerkit.writers.cython import write_pxd

pytestmark = pytest.mark.libclang


@pytest.fixture(scope="module")
def backend() -> Any:
    return get_backend("libclang")


def render(backend: Any, code: str) -> str:
    """Parse C source and render it as a Cython ``.pxd``."""
    return write_pxd(backend.parse(code, "test.h"))


# ---------------------------------------------------------------------------
# Compilation harness
# ---------------------------------------------------------------------------

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_HAS_CYTHON = importlib.util.find_spec("Cython") is not None

requires_toolchain = pytest.mark.skipif(
    not _HAS_CYTHON or _CC is None,
    reason=f"needs Cython (present={_HAS_CYTHON}) and a C compiler (found={_CC})",
)


class ToolchainError(Exception):
    """Raised when cythonizing or compiling the generated ``.pxd`` fails."""


def cythonize_and_compile(tmp_path: Path, header: str | None, pxd: str, pyx: str) -> str:
    """Cythonize ``pyx`` against ``pxd`` and compile the generated C.

    ``header`` is written as ``test.h`` next to the sources; passing ``None``
    omits it, which is how the negative control forces the C step to fail.

    :returns: The generated C source.
    :raises ToolchainError: If either the Cython or the C compilation step fails.
    """
    if header is not None:
        (tmp_path / "test.h").write_text(header)
    (tmp_path / "m.pxd").write_text(pxd)
    (tmp_path / "use.pyx").write_text(pyx)

    cython = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "cython", "-3", "use.pyx", "-o", "use.c"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if cython.returncode != 0:
        raise ToolchainError(f"cython failed:\n{cython.stdout}\n{cython.stderr}")

    assert _CC is not None
    compile_proc = subprocess.run(  # noqa: S603
        [
            _CC,
            "-c",
            "use.c",
            "-I",
            sysconfig.get_paths()["include"],
            "-I",
            str(tmp_path),
            "-Werror=implicit-function-declaration",
            "-Werror=incompatible-pointer-types",
            "-o",
            "use.o",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if compile_proc.returncode != 0:
        raise ToolchainError(f"C compilation failed:\n{compile_proc.stdout}\n{compile_proc.stderr}")

    return (tmp_path / "use.c").read_text()


def build(backend: Any, tmp_path: Path, header: str, pyx: str) -> str:
    """Parse ``header``, render it, and compile a ``.pyx`` that consumes it."""
    return cythonize_and_compile(tmp_path, header, render(backend, header), pyx)


# ---------------------------------------------------------------------------
# R1 -- anonymous records
# ---------------------------------------------------------------------------

ANON_STRUCT_VAR = "struct { int a; int b; } my_anon_struct[10];\n"


class TestAnonymousRecordNaming:
    """clang's ``(unnamed at file:line:col)`` spelling must never reach the output.

    It was both invalid Cython and non-reproducible: it embedded a source
    position, so inserting a blank line above the declaration changed the
    generated binding.
    """

    def test_anonymous_struct_takes_its_declarator_name(self, backend: Any) -> None:
        assert render(backend, ANON_STRUCT_VAR) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _my_anon_struct_s:
                    int a
                    int b

                _my_anon_struct_s my_anon_struct[10]
        """)

    def test_anonymous_union_takes_its_declarator_name(self, backend: Any) -> None:
        assert render(backend, "union { int a; float b; } get(void);") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef union _get_u:
                    int a
                    float b

                _get_u get()
        """)

    def test_output_is_invariant_to_source_position(self, backend: Any) -> None:
        """The generated tag must not encode file:line:col.

        Shifting the declaration down the file and putting a comment above it
        changes nothing about the C interface, so it must change nothing about
        the binding.
        """
        shifted = textwrap.dedent("""\


            /* A comment that moves the declaration
               several lines down the file. */

            struct { int a; int b; } my_anon_struct[10];
        """)
        assert render(backend, shifted) == render(backend, ANON_STRUCT_VAR)

    def test_unbound_anonymous_record_uses_a_counter_slug(self, backend: Any) -> None:
        """No declarator names this record, so the fallback slug is used.

        The counter is deliberate: a source-location slug would reintroduce the
        formatting dependency that :meth:`test_output_is_invariant_to_source_position`
        forbids.
        """
        assert render(backend, "void f(struct { int a; } *);") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _anon_struct_1


                void f(_anon_struct_1*)
        """)

    @pytest.mark.parametrize(
        "source",
        [
            ANON_STRUCT_VAR,
            "union { int a; float b; } get(void);",
            "void f(struct { int a; } *);",
        ],
    )
    def test_no_clang_internal_spelling_reaches_the_output(self, backend: Any, source: str) -> None:
        """Only sources that previously leaked the spelling are listed.

        Constructs that were *dropped* rather than mis-spelled would satisfy
        this check vacuously; they are pinned by exact-output tests instead.
        """
        output = render(backend, source)
        assert "(unnamed at" not in output
        assert "(anonymous" not in output

    @requires_toolchain
    def test_anonymous_struct_binding_compiles(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct { int a; int b; } my_anon_struct[10];
        """)
        # The generated tag names a type that does not exist in C, so the .pyx
        # reaches the members through the variable rather than declaring one.
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                my_anon_struct[3].a = 1
                my_anon_struct[3].b = 2
                return my_anon_struct[3].a + my_anon_struct[3].b
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "my_anon_struct[3]" in c_source


# ---------------------------------------------------------------------------
# R2 -- function-pointer variables
# ---------------------------------------------------------------------------


class TestFunctionPointerVariables:
    """``_format_type`` yields an abstract declarator that cannot take a name.

    ``void (*)(int, char)`` followed by a variable name is not valid Cython, so
    the writer emits a named ``ctypedef`` and declares the variable through it.
    """

    def test_function_pointer_variable_gets_a_named_typedef(self, backend: Any) -> None:
        assert render(backend, "void (*my_func)(int a, char b);") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef void (*_my_func_ft)(int a, char b)

                _my_func_ft my_func
        """)

    def test_unprototyped_function_pointer_variable(self, backend: Any) -> None:
        """FUNCTIONNOPROTO has no argument list and no variadic flag."""
        assert render(backend, "const int* (*p)();") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef const int* (*_p_ft)()

                _p_ft p
        """)

    def test_parameter_names_are_recovered(self, backend: Any) -> None:
        """clang's FUNCTIONPROTO type carries no argument names.

        They come from the declaring cursor's PARM_DECL children. Losing them
        is silent -- the output stays valid Cython, it just stops documenting
        the interface.
        """
        assert render(backend, "void h(void (*cb)(int a, int b));") == textwrap.dedent("""\
            cdef extern from "test.h":

                void h(void (*cb)(int a, int b))
        """)

    @requires_toolchain
    def test_function_pointer_variable_compiles(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            void (*my_func)(int a, char b);
        """)
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                if my_func is not NULL:
                    my_func(1, <char>2)
                    return 1
                return 0
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "my_func(" in c_source


# ---------------------------------------------------------------------------
# R3 -- dropped declarations and empty bodies
# ---------------------------------------------------------------------------


class TestDroppedDeclarationsAndEmptyBodies:
    def test_named_member_of_an_anonymous_struct_type_is_not_flattened(self, backend: Any) -> None:
        """``struct { ... } css;`` has a declarator, so C11 6.7.2.1p13 does not apply.

        Flattening it dropped ``css`` entirely -- no caller could reach
        ``outer.css`` -- and hoisted ``v`` and ``g`` into ``outer``. The
        anonymous type must instead get its own bodied declaration, named for
        the enclosing record so two records may both hold a ``css``.
        """
        source = "struct outer { int a; struct { int v; int g; } css; };"
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _outer_css_s:
                    int v
                    int g

                cdef struct outer:
                    int a
                    _outer_css_s css
        """)

    def test_named_anonymous_struct_members_of_two_records_do_not_collide(self, backend: Any) -> None:
        """An unqualified ``_css_s`` would bind whichever record clang saw first."""
        source = textwrap.dedent("""\
            struct left { struct { int v; } css; };
            struct right { struct { char w; } css; };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct _left_css_s:
                    int v

                cdef struct left:
                    _left_css_s css

                cdef struct _right_css_s:
                    char w

                cdef struct right:
                    _right_css_s css
        """)

    def test_bitfields_in_a_named_anonymous_struct_member_keep_the_member(self, backend: Any) -> None:
        """The ``xnvme_opts`` shape that exposed the over-flattening downstream.

        Cython has no bitfield syntax, so the widths are dropped; the members
        must still be reachable through ``opts.css``.
        """
        source = textwrap.dedent("""\
            typedef unsigned int uint32_t;
            struct xnvme_opts {
                int nsid;
                struct { uint32_t value : 31; uint32_t given : 1; } css;
            };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef unsigned int uint32_t

                cdef struct _xnvme_opts_css_s:
                    uint32_t value
                    uint32_t given

                cdef struct xnvme_opts:
                    int nsid
                    _xnvme_opts_css_s css
        """)

    @requires_toolchain
    def test_named_anonymous_struct_member_is_reachable_from_cython(self, backend: Any, tmp_path: Path) -> None:
        """Reading through ``o.css`` is what the flattening made impossible."""
        header = "struct outer { int a; struct { int v; int g; } css; };\n"
        pyx = textwrap.dedent("""\
            from m cimport outer

            cpdef public int exercise():
                cdef outer o
                o.a = 1
                o.css.v = 2
                o.css.g = 3
                return o.a + o.css.v + o.css.g
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert ".css.v" in c_source

    def test_anonymous_member_is_flattened_into_its_parent(self, backend: Any) -> None:
        """C11 makes ``b`` and ``c`` members of ``outer_s``; they were dropped."""
        source = "struct outer_s { int a; struct { int b; int c; }; };"
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct outer_s:
                    int a
                    int b
                    int c
        """)

    def test_anonymous_enum_emits_its_values(self, backend: Any) -> None:
        """This produced an extern block with no declarations at all."""
        assert render(backend, "enum { C1, C2, C3 };") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef enum:
                    C1
                    C2
                    C3
        """)

    def test_struct_with_a_nested_enum_has_a_body(self, backend: Any) -> None:
        """A suite header with no body is a Cython syntax error.

        This emitted ``cdef struct nested_enum_struct:`` followed by nothing.
        """
        source = "struct nested_enum_struct { enum { X } e; };"
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef enum _e_e:
                    X

                cdef struct nested_enum_struct:
                    _e_e e
        """)

    def test_struct_whose_fields_are_all_filtered_emits_pass(self, backend: Any) -> None:
        """``struct timespec`` comes from a system header, so it is forward-declared only.

        A field using it by value is therefore filtered out, leaving the record
        with no members to emit; ``pass`` keeps the suite well-formed.
        """
        source = textwrap.dedent("""\
            #include <time.h>
            struct holder_s { struct timespec ts; };
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct timespec

                cdef struct holder_s:
                    pass
        """)

    @requires_toolchain
    def test_flattened_members_and_nested_enum_compile(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct outer_s { int a; struct { int b; int c; }; };
            struct nested_enum_struct { enum { X } e; };
            enum { C1, C2, C3 };
        """)
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                cdef outer_s o
                o.a = C1
                o.b = C2
                o.c = C3
                cdef nested_enum_struct n
                n.e = X
                return o.a + o.b + o.c + <int>n.e
        """)
        c_source = build(backend, tmp_path, header, pyx)
        # The flattened members must reach the C as direct member accesses.
        assert "->b" in c_source or ".b" in c_source


# ---------------------------------------------------------------------------
# R4 -- qualifiers
# ---------------------------------------------------------------------------

CONST_FIELDS = "struct s { char* const f; const char* const h; const char* const* const i; const char** const j; };"


class TestQualifiers:
    """``const`` was stripped at four distinct positions.

    Pointer-level, pointee-level, multi-level, and on record/typedef pointees
    are separate code paths in the converter; each is pinned separately.
    """

    def test_const_at_every_pointer_level(self, backend: Any) -> None:
        assert render(backend, CONST_FIELDS) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct s:
                    char* const f
                    const char* const h
                    const char* const* const i
                    const char** const j
        """)

    def test_distinct_const_placements_render_distinctly(self, backend: Any) -> None:
        """``const char* const* const`` and ``const char** const`` are different C types.

        Both previously printed as ``const char**``, collapsing the distinction.
        """
        output = render(backend, CONST_FIELDS)
        i_line = next(line.strip() for line in output.splitlines() if line.endswith(" i"))
        j_line = next(line.strip() for line in output.splitlines() if line.endswith(" j"))
        assert i_line == "const char* const* const i"
        assert j_line == "const char** const j"
        assert i_line[:-2] != j_line[:-2]

    def test_const_on_record_pointees(self, backend: Any) -> None:
        source = textwrap.dedent("""\
            struct my_struct { int x; };
            union my_union { int y; };
            void my_func_6(const struct my_struct* s, const union my_union* const u);
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct my_struct:
                    int x

                cdef union my_union:
                    int y

                void my_func_6(const my_struct* s, const my_union* const u)
        """)

    def test_const_on_typedef_pointees(self, backend: Any) -> None:
        source = textwrap.dedent("""\
            typedef struct my_struct { int x; } my_struct;
            typedef union my_union { int y; } my_union;
            void my_func_6(const my_struct* s, const my_union* const u);
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef struct my_struct:
                    int x

                ctypedef union my_union:
                    int y

                void my_func_6(const my_struct* s, const my_union* const u)
        """)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "_Atomic int at_var;",
                'cdef extern from "test.h":\n\n    int at_var\n',
            ),
            (
                "void rf(int* __restrict a);",
                'cdef extern from "test.h":\n\n    void rf(int* a)\n',
            ),
            (
                "_Noreturn void nr(void);",
                'cdef extern from "test.h":\n\n    void nr()\n',
            ),
        ],
    )
    def test_unsupported_qualifiers_stay_stripped(self, backend: Any, source: str, expected: str) -> None:
        """Cython has no ``_Atomic``/``__restrict``/``_Noreturn``.

        Recovering ``const`` must not license emitting these; they are dropped
        on purpose.
        """
        assert render(backend, source) == expected

    @requires_toolchain
    def test_const_qualified_fields_compile(self, backend: Any, tmp_path: Path) -> None:
        header = textwrap.dedent("""\
            struct s { char* const f; const char* const h; const char* const* const i; const char** const j; };
            extern struct s g_s;
        """)
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                cdef char* a = g_s.f
                cdef const char* b = g_s.h
                cdef const char* const* c = g_s.i
                cdef const char** d = g_s.j
                return (a is not NULL) + (b is not NULL) + (c is not NULL) + (d is not NULL)
        """)
        c_source = build(backend, tmp_path, header, pyx)
        assert "g_s" in c_source


# ---------------------------------------------------------------------------
# R5 -- records defined inside another record body
# ---------------------------------------------------------------------------


NESTED_UNION = textwrap.dedent("""\
    struct my_s {
      union my_nested_u {
        char c;
        int i;
      } n;
      unsigned u;
    };
""")


class TestNestedTaggedRecords:
    """A tagged record defined inside another record body must keep its body.

    The backend dropped the nested definition entirely, so the writer saw only
    an undeclared tag and emitted a bare forward declaration. The containing
    struct then used that incomplete type *by value*, which Cython rejects with
    ``Variable type 'my_nested_u' is incomplete``.
    """

    def test_nested_union_body_is_emitted_before_the_parent(self, backend: Any) -> None:
        assert render(backend, NESTED_UNION) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef union my_nested_u:
                    char c
                    int i

                cdef struct my_s:
                    my_nested_u n
                    unsigned int u
        """)

    def test_doubly_nested_records_are_hoisted_innermost_first(self, backend: Any) -> None:
        """Each level must precede the level that uses it by value."""
        source = textwrap.dedent("""\
            typedef struct my_s {
              union my_nested_u {
                char c;
                struct my_nested_s { int i; } n;
                int i;
              } n;
              unsigned u;
            } my_t;
        """)
        assert render(backend, source) == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct my_nested_s:
                    int i

                cdef union my_nested_u:
                    char c
                    my_nested_s n
                    int i

                cdef struct my_s:
                    my_nested_u n
                    unsigned int u

                ctypedef my_s my_t
        """)

    def test_pointer_only_nested_tag_stays_a_forward_declaration(self, backend: Any) -> None:
        """A tag introduced by a pointer member has no body to hoist.

        This is the negative control for the fix: only *definitions* are
        hoisted, so this must not gain a spurious empty body.
        """
        assert render(backend, "struct node { struct peer *p; };") == textwrap.dedent("""\
            cdef extern from "test.h":

                cdef struct peer

                cdef struct node:
                    peer* p
        """)

    @requires_toolchain
    def test_nested_union_compiles(self, backend: Any, tmp_path: Path) -> None:
        pyx = textwrap.dedent("""\
            from m cimport *

            cpdef public int exercise():
                cdef my_s v
                v.n.i = 7
                v.u = 3
                return v.n.i + <int>v.u
        """)
        c_source = build(backend, tmp_path, NESTED_UNION, pyx)
        assert "struct my_s" in c_source


# ---------------------------------------------------------------------------
# R6 -- pointer-to-function-pointer parameters
# ---------------------------------------------------------------------------


DOUBLE_FUNC_PTR_PARAM = "void reg(void (**pxFunc)(int, char), void **ppArg);\n"


class TestPointerToFunctionPointerParameters:
    """A named ``void (**p)(...)`` parameter must keep its name inside the declarator.

    Only single-level function-pointer parameters were routed through the
    declarator formatter. A double pointer fell through to the generic type
    formatter, which produced the abstract ``void (**)(int, char)`` and then
    appended the name *after* it -- ``void (**)(int, char) pxFunc`` -- which
    Cython rejects with ``Expected ')', found 'pxFunc'``. Reduced from
    sqlite3's ``xFindFunction``.
    """

    def test_double_pointer_parameter_name_sits_inside_the_declarator(self, backend: Any) -> None:
        assert render(backend, DOUBLE_FUNC_PTR_PARAM) == textwrap.dedent("""\
            cdef extern from "test.h":

                void reg(void (**pxFunc)(int, char), void** ppArg)
        """)

    def test_triple_pointer_parameter(self, backend: Any) -> None:
        """The star count follows the pointer depth rather than a fixed case."""
        assert render(backend, "void f(void (***p)(int));") == textwrap.dedent("""\
            cdef extern from "test.h":

                void f(void (***p)(int))
        """)

    def test_single_pointer_parameter_is_unchanged(self, backend: Any) -> None:
        assert render(backend, "void reg(void (*pxFunc)(int, char));") == textwrap.dedent("""\
            cdef extern from "test.h":

                void reg(void (*pxFunc)(int, char))
        """)

    def test_unnamed_double_pointer_parameter_stays_abstract(self, backend: Any) -> None:
        assert render(backend, "typedef int (*cb)(void (**inner)(int), int n);") == textwrap.dedent("""\
            cdef extern from "test.h":

                ctypedef int (*cb)(void (**)(int), int)
        """)

    @requires_toolchain
    def test_double_pointer_parameter_compiles(self, backend: Any, tmp_path: Path) -> None:
        pyx = textwrap.dedent("""\
            from m cimport *

            cdef void impl(int a, char b) noexcept:
                pass

            cpdef public int exercise():
                cdef void (*fp)(int, char) noexcept
                fp = impl
                cdef void **arg = NULL
                reg(&fp, arg)
                return 1
        """)
        c_source = build(backend, tmp_path, DOUBLE_FUNC_PTR_PARAM, pyx)
        assert "reg(" in c_source


# ---------------------------------------------------------------------------
# Negative controls for the harness itself
# ---------------------------------------------------------------------------


class TestCompileHarness:
    """A compile check that has never failed is a claim, not a mechanism."""

    @requires_toolchain
    def test_missing_header_fails_the_c_step(self, tmp_path: Path) -> None:
        pxd = 'cdef extern from "test.h":\n\n    int a_symbol\n'
        pyx = "from m cimport *\n\ncpdef public int exercise():\n    return a_symbol\n"
        with pytest.raises(ToolchainError) as exc:
            cythonize_and_compile(tmp_path, None, pxd, pyx)
        assert "C compilation failed" in str(exc.value)
        assert "test.h" in str(exc.value)

    @requires_toolchain
    def test_bodyless_suite_fails_the_cython_step(self, tmp_path: Path) -> None:
        """The exact shape the nested-enum struct emitted before the fix."""
        pxd = 'cdef extern from "test.h":\n\n    cdef struct broken_s:\n\n    int after\n'
        pyx = "from m cimport *\n\ncpdef public int exercise():\n    return 0\n"
        with pytest.raises(ToolchainError) as exc:
            cythonize_and_compile(tmp_path, "struct broken_s { int b; };\nint after;\n", pxd, pyx)
        assert "cython failed" in str(exc.value)
        assert "indentation" in str(exc.value).lower()

    @requires_toolchain
    def test_wrong_field_spelling_fails_the_c_step(self, tmp_path: Path) -> None:
        """Proves the C step sees through the .pxd to the real header."""
        pxd = 'cdef extern from "test.h":\n\n    cdef struct real_s:\n        int not_a_field\n'
        pyx = "from m cimport *\n\ncpdef public int exercise():\n    cdef real_s v\n    return v.not_a_field\n"
        with pytest.raises(ToolchainError) as exc:
            cythonize_and_compile(tmp_path, "struct real_s { int actual; };\n", pxd, pyx)
        assert "C compilation failed" in str(exc.value)
