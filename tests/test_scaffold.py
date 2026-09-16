"""Tests for project scaffolding, BYOScaffolder, and unified layout engine."""

from __future__ import annotations

import io
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from headerkit.hooks import HookRegistry, Priority, hook
from headerkit.ir import Constant, CType, Function, Header, Parameter
from headerkit.scaffold import (
    _TEST_EXTRACTORS,
    BYOScaffolder,
    CTestExtractor,
    NimTestExtractor,
    OutputFile,
    ProjectLayout,
    PythonTestExtractor,
    ScaffoldOptions,
    TestBlockExtractor,
    extract_header_version,
    get_test_extractor,
    merge_incremental_tests,
    prompt_scaffold_options,
    register_test_extractor,
    scaffold,
)


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = HookRegistry.snapshot()
    extractor_snapshot = dict(_TEST_EXTRACTORS)
    yield
    HookRegistry.restore(snapshot)
    _TEST_EXTRACTORS.clear()
    _TEST_EXTRACTORS.update(extractor_snapshot)


@pytest.fixture
def sample_unit() -> Header:
    fn = Function(
        name="compute_hash",
        return_type=CType("uint32_t"),
        parameters=[Parameter("seed", CType("uint32_t"))],
    )
    return Header(path="hasher.h", declarations=[fn])


class TestProjectLayout:
    def test_output_file_immutability(self) -> None:
        f = OutputFile(path="src/lib.py", content="x = 1")
        assert f.path == "src/lib.py"
        assert f.content == "x = 1"
        assert not f.is_executable

    def test_write_to_disk(self, tmp_path: Path) -> None:
        layout = ProjectLayout(
            files=[
                OutputFile(path="README.md", content="# My Lib"),
                OutputFile(path="src/lib.py", content="def foo(): pass"),
                OutputFile(path="bin/run.sh", content="#!/bin/sh\necho ok", is_executable=True),
            ]
        )
        written = layout.write_to_disk(tmp_path)
        assert len(written) == 3
        assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# My Lib"
        assert (tmp_path / "src/lib.py").read_text(encoding="utf-8") == "def foo(): pass"
        assert (tmp_path / "bin/run.sh").exists()
        if sys.platform != "win32":
            assert (tmp_path / "bin/run.sh").stat().st_mode & 0o111

    def test_get_file(self) -> None:
        layout = ProjectLayout(
            files=[
                OutputFile(path="config.toml", content="key = 'val'"),
            ]
        )
        assert layout.get_file("config.toml") is not None
        assert layout.get_file("missing.toml") is None

    def test_write_to_disk_rejects_path_traversal(self, tmp_path: Path) -> None:
        layout = ProjectLayout(
            files=[
                OutputFile(path="../../escaped.txt", content="malicious"),
            ]
        )
        with pytest.raises(ValueError, match="Path traversal detected"):
            layout.write_to_disk(tmp_path)


class TestStdlibScaffolderNim:
    def test_nim_single_file_layout(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(package_name="hasher", target_language="nim", layout="file")
        layout = scaffold(sample_unit, opts)
        assert len(layout.files) == 1
        assert layout.files[0].path == "hasher.nim"
        assert "proc compute_hash*" in layout.files[0].content

    def test_nim_package_both_tests(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(
            package_name="hasher",
            target_language="nim",
            layout="package",
            test_type="both",
        )
        layout = scaffold(sample_unit, opts)

        paths = {f.path for f in layout.files}
        assert "hasher.nimble" in paths
        assert "src/hasher.nim" in paths
        assert "src/hasher/bindings.nim" in paths
        assert "nim.cfg" in paths
        assert "tests/test_tripwire.nim" in paths
        assert "tests/test_hasher.nim" in paths
        assert "AGENTS.md" in paths

        agents_file = layout.get_file("AGENTS.md")
        assert agents_file is not None
        assert "# Agents" in agents_file.content
        assert "hasher" in agents_file.content

        nimble_content = layout.get_file("hasher.nimble").content
        assert 'packageName   = "hasher"' in nimble_content

        tripwire_content = layout.get_file("tests/test_tripwire.nim").content
        assert "compute_hash" in tripwire_content
        assert "tripwire" in tripwire_content.lower()

    def test_nim_package_test_type_filtering(self, sample_unit: Header) -> None:
        opts_none = ScaffoldOptions(package_name="hasher", target_language="nim", layout="package", test_type="none")
        layout_none = scaffold(sample_unit, opts_none)
        paths_none = {f.path for f in layout_none.files}
        assert "tests/test_tripwire.nim" not in paths_none
        assert "tests/test_hasher.nim" not in paths_none

        opts_tripwire = ScaffoldOptions(
            package_name="hasher", target_language="nim", layout="package", test_type="tripwire"
        )
        layout_tw = scaffold(sample_unit, opts_tripwire)
        paths_tw = {f.path for f in layout_tw.files}
        assert "tests/test_tripwire.nim" in paths_tw
        assert "tests/test_hasher.nim" not in paths_tw


class TestStdlibScaffolderMojo:
    def test_mojo_package_layout(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(
            package_name="fastmath",
            target_language="mojo",
            layout="package",
            test_type="both",
        )
        layout = scaffold(sample_unit, opts)
        paths = {f.path for f in layout.files}

        assert "mojoproject.toml" in paths
        assert "src/fastmath/__init__.mojo" in paths
        assert "src/fastmath/bindings.mojo" in paths
        assert "tests/test_tripwire.mojo" in paths
        assert "tests/test_fastmath.mojo" in paths

        bindings = layout.get_file("src/fastmath/bindings.mojo").content
        assert "compute_hash" in bindings

    def test_mojo_test_type_filtering(self, sample_unit: Header) -> None:
        opts_none = ScaffoldOptions(package_name="fastmath", target_language="mojo", layout="package", test_type="none")
        layout_none = scaffold(sample_unit, opts_none)
        paths_none = {f.path for f in layout_none.files}
        assert "tests/test_tripwire.mojo" not in paths_none
        assert "tests/test_fastmath.mojo" not in paths_none

        opts_tw = ScaffoldOptions(
            package_name="fastmath", target_language="mojo", layout="package", test_type="tripwire"
        )
        layout_tw = scaffold(sample_unit, opts_tw)
        paths_tw = {f.path for f in layout_tw.files}
        assert "tests/test_tripwire.mojo" in paths_tw
        assert "tests/test_fastmath.mojo" not in paths_tw


class TestStdlibScaffolderPython:
    def test_ctypes_package_layout(self, sample_unit: Header) -> None:
        opts = ScaffoldOptions(
            package_name="hashkit",
            target_language="ctypes",
            layout="package",
            test_type="both",
        )
        layout = scaffold(sample_unit, opts)
        paths = {f.path for f in layout.files}

        assert "pyproject.toml" in paths
        assert "src/hashkit/__init__.py" in paths
        assert "src/hashkit/_bindings.py" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_bindings.py" in paths

        pyproject = layout.get_file("pyproject.toml").content
        assert 'name = "hashkit"' in pyproject

        tripwire = layout.get_file("tests/test_tripwire.py").content
        assert "pytest.mark.tripwire" in tripwire or "tripwire" in tripwire.lower()

    def test_python_cffi_target_and_filtering(self, sample_unit: Header) -> None:
        opts_cffi = ScaffoldOptions(
            package_name="hashkit",
            target_language="cffi",
            layout="package",
            test_type="tripwire",
        )
        layout_cffi = scaffold(sample_unit, opts_cffi)
        paths = {f.path for f in layout_cffi.files}
        assert "src/hashkit/_bindings.py" in paths
        assert "tests/test_tripwire.py" in paths
        assert "tests/test_bindings.py" not in paths

        opts_unit = ScaffoldOptions(
            package_name="hashkit",
            target_language="ctypes",
            layout="package",
            test_type="unit",
        )
        layout_unit = scaffold(sample_unit, opts_unit)
        paths_u = {f.path for f in layout_unit.files}
        assert "tests/test_tripwire.py" not in paths_u
        assert "tests/test_bindings.py" in paths_u


class TestBYOScaffolderHook:
    def test_custom_scaffolder_plugin(self, sample_unit: Header) -> None:
        class CustomCopierScaffolder(BYOScaffolder):
            def scaffold(self, unit: Header, options: ScaffoldOptions) -> ProjectLayout:
                return ProjectLayout(
                    files=[
                        OutputFile(path="copier.generated", content=f"copier template for {options.package_name}"),
                    ]
                )

        custom = CustomCopierScaffolder()

        @hook("scaffold_project", priority=Priority.OVERRIDE)
        def custom_hook(
            unit: Header,
            options: ScaffoldOptions,
            _context: Any = None,
            **_kwargs: Any,
        ) -> ProjectLayout:
            return custom.scaffold(unit, options)

        opts = ScaffoldOptions(package_name="mycustom", target_language="nim", layout="package")
        layout = scaffold(sample_unit, opts)

        assert len(layout.files) == 1
        assert layout.files[0].path == "copier.generated"
        assert layout.files[0].content == "copier template for mycustom"


class TestTTYPromptWizard:
    def test_non_interactive_preserves_defaults(self) -> None:
        opts = ScaffoldOptions(package_name="default_pkg", target_language="nim")
        resolved = prompt_scaffold_options(opts, is_tty=False)
        assert resolved.package_name == "default_pkg"
        assert resolved.target_language == "nim"

    def test_interactive_wizard_reads_inputs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inputs = io.StringIO("custom_pkg\nmojo\npackage\nboth\n")
        monkeypatch.setattr("sys.stdin", inputs)

        resolved = prompt_scaffold_options(is_tty=True)
        assert resolved.package_name == "custom_pkg"
        assert resolved.target_language == "mojo"
        assert resolved.layout == "package"
        assert resolved.test_type == "both"


class TestCLIScaffolding:
    def test_cli_scaffold_package(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from headerkit._cli import main

        header = tmp_path / "math_lib.h"
        header.write_text("int add(int a, int b);\n", encoding="utf-8")
        out_dir = tmp_path / "nim_math"

        test_args = [
            "headerkit",
            str(header),
            "-w",
            "nim",
            "--layout",
            "package",
            "--package-name",
            "nim_math",
            "-o",
            f"nim:{out_dir}",
            "--no-input",
        ]
        monkeypatch.setattr("sys.argv", test_args)

        ret = main()
        assert ret == 0
        assert (out_dir / "nim_math.nimble").exists()
        assert (out_dir / "src/nim_math.nim").exists()
        assert (out_dir / "src/nim_math/bindings.nim").exists()
        assert (out_dir / "tests/test_tripwire.nim").exists()
        assert (out_dir / "tests/test_nim_math.nim").exists()


class TestExtractHeaderVersion:
    def test_extract_composite_macros(self) -> None:
        unit = Header(
            path="juce.h",
            declarations=[
                Constant("JUCE_MAJOR_VERSION", 8, is_macro=True),
                Constant("JUCE_MINOR_VERSION", 0, is_macro=True),
                Constant("JUCE_BUILD_NUMBER", 2, is_macro=True),
            ],
        )
        assert extract_header_version(unit) == "8.0.2"

    def test_extract_composite_patch_fallback(self) -> None:
        unit = Header(
            path="lib.h",
            declarations=[
                Constant("FOO_VERSION_MAJOR", 3, is_macro=True),
                Constant("FOO_VERSION_MINOR", 1, is_macro=True),
            ],
        )
        assert extract_header_version(unit) == "3.1.0"

    def test_extract_version_string_macro(self) -> None:
        unit = Header(
            path="sqlite3.h",
            declarations=[
                Constant("SQLITE_VERSION", '"3.45.1"', is_macro=True),
            ],
        )
        assert extract_header_version(unit) == "3.45.1"

    def test_extract_returns_none_when_no_version(self) -> None:
        unit = Header(
            path="plain.h",
            declarations=[
                Constant("MAX_BUFFER_SIZE", 1024, is_macro=True),
            ],
        )
        assert extract_header_version(unit) is None


class TestIncrementalTestMerger:
    def test_nim_merger_appends_new_tests_and_preserves_existing(self) -> None:
        existing = textwrap.dedent("""\
            import std/unittest
            import juce/core

            suite "String":
              test "String.isEmpty":
                var s = initString()
                check s.isEmpty()
                # Human added custom assertion
                check s.length() == 0
        """)

        incoming = textwrap.dedent("""\
            import std/unittest
            import juce/core

            suite "String":
              test "String.isEmpty":
                var s = initString()
                check s.isEmpty()

              test "String.toRawUTF8":
                var s = initString("hello")
                check s.toRawUTF8() != nil
        """)

        merged = merge_incremental_tests(existing, incoming, language="nim")

        # Must preserve human-edited assertion in String.isEmpty
        assert "check s.length() == 0" in merged
        # Must contain newly added test
        assert 'test "String.toRawUTF8":' in merged
        assert "check s.toRawUTF8() != nil" in merged
        # String.isEmpty must only appear once
        assert merged.count('test "String.isEmpty":') == 1

    def test_nim_merger_with_guarded_tests(self) -> None:
        existing = textwrap.dedent("""\
            suite "File":
              test "File.exists":
                var f = initFile("/tmp")
                check f.exists()
        """)

        incoming = textwrap.dedent("""\
            suite "File":
              test "File.exists":
                var f = initFile("/tmp")
                check f.exists()

              when declared(hasWriteAccess):
                test "File.hasWriteAccess":
                  var f = initFile("/tmp")
                  check f.hasWriteAccess()
        """)

        merged = merge_incremental_tests(existing, incoming, language="nim")
        assert "when declared(hasWriteAccess):" in merged
        assert 'test "File.hasWriteAccess":' in merged
        assert merged.count('test "File.exists":') == 1

    def test_nim_merger_no_new_tests_returns_identical(self) -> None:
        existing = textwrap.dedent("""\
            suite "Plain":
              test "single":
                check 1 == 1
        """)
        merged = merge_incremental_tests(existing, existing, language="nim")
        assert merged == existing

    def test_python_merger_appends_new_functions(self) -> None:
        existing = textwrap.dedent("""\
            def test_one():
                assert 1 == 1
        """)
        incoming = textwrap.dedent("""\
            def test_one():
                assert 1 == 1

            def test_two():
                assert 2 == 2
        """)
        merged = merge_incremental_tests(existing, incoming, language="python")
        assert "def test_one():" in merged
        assert "def test_two():" in merged
        assert merged.count("def test_one():") == 1


class TestScaffoldingHookLifecycle:
    def test_scaffold_tests_hook_enriches_layout(self, sample_unit: Header) -> None:
        @hook("scaffold_tests", priority=Priority.PROJECT)
        def add_extra_test(
            layout: ProjectLayout,
            _unit: Header,
            _options: ScaffoldOptions,
            **_kwargs: Any,
        ) -> ProjectLayout:
            layout.files.append(
                OutputFile(
                    path="tests/test_custom_extra.nim",
                    content="# Custom extra test suite\n",
                    merge_strategy="append_new_tests",
                )
            )
            return layout

        opts = ScaffoldOptions(package_name="hasher", target_language="nim", layout="package")
        layout = scaffold(sample_unit, opts)
        extra_file = layout.get_file("tests/test_custom_extra.nim")
        assert extra_file is not None
        assert extra_file.content == "# Custom extra test suite\n"
        assert extra_file.merge_strategy == "append_new_tests"

    def test_transform_layout_hook_injects_nim_cfg(self, sample_unit: Header) -> None:
        @hook("transform_layout", priority=Priority.PROJECT)
        def inject_nim_cfg(
            layout: ProjectLayout,
            _unit: Header,
            _options: ScaffoldOptions,
            **_kwargs: Any,
        ) -> ProjectLayout:
            existing = layout.get_file("nim.cfg")
            if existing:
                layout.files.remove(existing)
                layout.files.append(
                    OutputFile(
                        path="nim.cfg",
                        content=existing.content + '--backend:cpp\n--passC:"-std=c++17"\n',
                    )
                )
            else:
                layout.files.append(
                    OutputFile(
                        path="nim.cfg",
                        content='--backend:cpp\n--passC:"-std=c++17"\n',
                    )
                )
            return layout

        opts = ScaffoldOptions(package_name="hasher", target_language="nim", layout="package")
        layout = scaffold(sample_unit, opts)
        cfg = layout.get_file("nim.cfg")
        assert cfg is not None
        assert '--passC:"-std=c++17"' in cfg.content

    def test_version_detection_populates_extra_context(self) -> None:
        unit = Header(
            path="juce.h",
            declarations=[
                Constant("JUCE_MAJOR_VERSION", 8, is_macro=True),
                Constant("JUCE_MINOR_VERSION", 0, is_macro=True),
                Constant("JUCE_BUILD_NUMBER", 1, is_macro=True),
            ],
        )
        opts = ScaffoldOptions(package_name="juce", target_language="nim", layout="file")
        scaffold(unit, opts)
        assert opts.extra_context.get("library_version") == "8.0.1"


class TestProjectLayoutIncrementalWrite:
    def test_write_to_disk_with_append_new_tests(self, tmp_path: Path) -> None:
        test_file = tmp_path / "tests" / "test_string.nim"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        # Pre-existing test file with human edit
        test_file.write_text(
            textwrap.dedent("""\
                suite "String":
                  test "String.isEmpty":
                    check 1 == 1
                    # Custom user code that must not be wiped
                    let customUserVal = 42
                    check customUserVal == 42
            """),
            encoding="utf-8",
        )

        incoming_layout = ProjectLayout(
            files=[
                OutputFile(
                    path="tests/test_string.nim",
                    content=textwrap.dedent("""\
                        suite "String":
                          test "String.isEmpty":
                            check 1 == 1

                          test "String.contains":
                            check "hello".contains("ll")
                    """),
                    merge_strategy="append_new_tests",
                )
            ]
        )

        incoming_layout.write_to_disk(tmp_path)

        result_content = test_file.read_text(encoding="utf-8")
        assert "# Custom user code that must not be wiped" in result_content
        assert 'test "String.contains":' in result_content
        assert result_content.count('test "String.isEmpty":') == 1

    def test_nim_merger_multi_test_when_block(self) -> None:
        incoming = textwrap.dedent("""\
            suite "Memory":
              when declared(constructBlock):
                test "Block constructor empty":
                  check 1 == 1

                test "Block constructor sized":
                  check 2 == 2
        """)
        existing = textwrap.dedent("""\
            suite "Memory":
              test "placeholder":
                check 0 == 0
        """)
        merged = merge_incremental_tests(existing, incoming, language="nim")
        # Both tests must retain the guard when imported into a file that didn't have them
        assert 'test "Block constructor empty":' in merged
        assert 'test "Block constructor sized":' in merged
        assert merged.count("when declared(constructBlock):") >= 1

    def test_nim_merger_canonicalize_order_independent(self) -> None:
        source_v8 = textwrap.dedent("""\
            import std/unittest
            import juce_core

            suite "JUCE Core":
              when declared(constructString):
                test "String default constructor is empty":
                  check 1 == 1

              when declared(juce8Feature):
                test "JUCE 8 feature":
                  check 8 == 8
        """)

        source_v9 = textwrap.dedent("""\
            import std/unittest
            import juce_core

            suite "JUCE Core":
              when declared(constructString):
                test "String default constructor is empty":
                  check 1 == 1

              when declared(juce9Feature):
                test "JUCE 9 feature":
                  check 9 == 9
        """)

        # Run 1: Start with v8, merge v9
        merged_8_then_9 = merge_incremental_tests(source_v8, source_v9, language="nim", canonicalize=True)

        # Run 2: Start with v9, merge v8
        merged_9_then_8 = merge_incremental_tests(source_v9, source_v8, language="nim", canonicalize=True)

        # Order of operations must not matter: output is canonical and identical
        assert merged_8_then_9 == merged_9_then_8
        assert 'test "JUCE 8 feature":' in merged_8_then_9
        assert 'test "JUCE 9 feature":' in merged_8_then_9
        assert 'test "String default constructor is empty":' in merged_8_then_9

    def test_write_to_disk_merge_strategy_takes_precedence_over_preserve_existing(self, tmp_path: Path) -> None:
        test_file = tmp_path / "tests" / "test_merge.nim"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            textwrap.dedent("""\
                suite "Alpha":
                  test "alpha":
                    check 1 == 1
            """),
            encoding="utf-8",
        )

        incoming = ProjectLayout(
            files=[
                OutputFile(
                    path="tests/test_merge.nim",
                    content=textwrap.dedent("""\
                        suite "Alpha":
                          test "alpha":
                            check 1 == 1

                          test "beta":
                            check 2 == 2
                    """),
                    preserve_existing=True,  # Even with preserve_existing=True, merge_strategy must run
                    merge_strategy="append_new_tests",
                )
            ]
        )
        incoming.write_to_disk(tmp_path)
        content = test_file.read_text(encoding="utf-8")
        assert 'test "beta":' in content
        assert 'test "alpha":' in content


class TestTestExtractors:
    def test_default_extractors_registered(self) -> None:
        c_ext = get_test_extractor("c")
        assert isinstance(c_ext, CTestExtractor)
        assert isinstance(c_ext, TestBlockExtractor)
        assert isinstance(get_test_extractor("nim"), NimTestExtractor)
        assert isinstance(get_test_extractor("python"), PythonTestExtractor)
        assert isinstance(get_test_extractor("py"), PythonTestExtractor)
        assert isinstance(get_test_extractor("cpp"), CTestExtractor)
        assert isinstance(get_test_extractor("cxx"), CTestExtractor)
        assert get_test_extractor("unknown_lang") is None

    def test_custom_extractor_registration(self) -> None:
        class DummyExtractor:
            def extract_tests(self, content: str) -> tuple[str, list[tuple[str, str]]]:
                lines = content.splitlines(keepends=True)
                preamble: list[str] = []
                tests: list[tuple[str, str]] = []
                for line in lines:
                    if line.startswith("// test:"):
                        title = line[len("// test:") :].strip()
                        tests.append((title, line))
                    else:
                        preamble.append(line)
                return "".join(preamble), tests

        register_test_extractor("dummy", DummyExtractor())
        extractor = get_test_extractor("dummy")
        assert extractor is not None
        preamble, tests = extractor.extract_tests("// preamble\n// test: alpha\n")
        assert preamble == "// preamble\n"
        assert tests == [("alpha", "// test: alpha\n")]

        existing = textwrap.dedent("""\
            // dummy preamble
            // test: test1
        """)
        incoming = textwrap.dedent("""\
            // dummy preamble
            // test: test1
            // test: test2
        """)
        merged = merge_incremental_tests(existing, incoming, language="dummy")
        assert "// test: test1" in merged
        assert "// test: test2" in merged


class TestCTestExtractor:
    def test_extract_catch2_tests(self) -> None:
        extractor = CTestExtractor()
        source = textwrap.dedent("""\
            #include <catch2/catch_test_macros.hpp>
            #include "mylib.h"

            static int helper(int x) {
                return x * 2;
            }

            TEST_CASE("Addition works", "[math]") {
                REQUIRE(1 + 1 == 2);
                if (true) {
                    REQUIRE(helper(2) == 4);
                }
            }

            TEST_CASE("Subtraction works", "[math]") {
                REQUIRE(3 - 1 == 2);
            }
        """)
        preamble, tests = extractor.extract_tests(source)
        assert "#include <catch2/catch_test_macros.hpp>" in preamble
        assert "static int helper" in preamble
        assert len(tests) == 2
        assert tests[0][0] == "Addition works"
        assert "REQUIRE(helper(2) == 4);" in tests[0][1]
        assert tests[1][0] == "Subtraction works"
        assert "REQUIRE(3 - 1 == 2);" in tests[1][1]

    def test_extract_gtest_and_criterion(self) -> None:
        extractor = CTestExtractor()
        source = textwrap.dedent("""\
            #include <gtest/gtest.h>

            TEST(MathSuite, Multiply) {
                EXPECT_EQ(2 * 3, 6);
            }

            TEST_F(FixtureSuite, Step) {
                EXPECT_TRUE(true);
            }

            Test(criterion_suite, sample) {
                cr_assert(1);
            }
        """)
        preamble, tests = extractor.extract_tests(source)
        assert "#include <gtest/gtest.h>" in preamble
        assert len(tests) == 3
        assert tests[0][0] == "MathSuite.Multiply"
        assert "EXPECT_EQ(2 * 3, 6);" in tests[0][1]
        assert tests[1][0] == "FixtureSuite.Step"
        assert tests[2][0] == "criterion_suite.sample"

    def test_extract_plain_c_functions(self) -> None:
        extractor = CTestExtractor()
        source = textwrap.dedent("""\
            #include <assert.h>

            void test_basic_arithmetic(void) {
                assert(1 + 1 == 2);
            }

            static void test_internal_state(void) {
                assert(42 == 42);
            }
        """)
        preamble, tests = extractor.extract_tests(source)
        assert "#include <assert.h>" in preamble
        assert len(tests) == 2
        assert tests[0][0] == "test_basic_arithmetic"
        assert "assert(1 + 1 == 2);" in tests[0][1]
        assert tests[1][0] == "test_internal_state"
        assert "assert(42 == 42);" in tests[1][1]

    def test_c_merge_incremental_tests(self) -> None:
        existing = textwrap.dedent("""\
            #include <catch2/catch_test_macros.hpp>

            TEST_CASE("Alpha", "[tag]") {
                REQUIRE(1 == 1);
            }
        """)
        incoming = textwrap.dedent("""\
            #include <catch2/catch_test_macros.hpp>

            TEST_CASE("Alpha", "[tag]") {
                REQUIRE(1 == 1);
            }

            TEST_CASE("Beta", "[tag]") {
                REQUIRE(2 == 2);
            }
        """)
        merged = merge_incremental_tests(existing, incoming, language="c")
        assert 'TEST_CASE("Alpha", "[tag]")' in merged
        assert 'TEST_CASE("Beta", "[tag]")' in merged
        assert merged.count('TEST_CASE("Alpha"') == 1

    def test_c_merger_canonicalize_order_independent(self) -> None:
        source_a = textwrap.dedent("""\
            #include <catch2/catch_test_macros.hpp>

            TEST_CASE("Common", "[tag]") {
                REQUIRE(0 == 0);
            }

            TEST_CASE("Feature A", "[tag]") {
                REQUIRE(1 == 1);
            }
        """)
        source_b = textwrap.dedent("""\
            #include <catch2/catch_test_macros.hpp>

            TEST_CASE("Common", "[tag]") {
                REQUIRE(0 == 0);
            }

            TEST_CASE("Feature B", "[tag]") {
                REQUIRE(2 == 2);
            }
        """)

        merged_ab = merge_incremental_tests(source_a, source_b, language="cpp", canonicalize=True)
        merged_ba = merge_incremental_tests(source_b, source_a, language="cpp", canonicalize=True)

        assert merged_ab == merged_ba
        assert 'TEST_CASE("Feature A", "[tag]")' in merged_ab
        assert 'TEST_CASE("Feature B", "[tag]")' in merged_ab
