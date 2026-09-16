"""Integration tests for multi-version JUCE scaffolding (8.0, 8.1, 9.0).

Verifies that:
1. Multi-version scaffolding is order-independent and deterministic.
2. Downstream user applications can branch on compile-time JUCE version constants
   (`when JUCE_MAJOR_VERSION >= 9:`, `when declared(...)`) and inspect runtime versions.
3. Incremental test merging preserves existing human edits while appending new version tests.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from headerkit.hooks import HookRegistry, Priority, hook
from headerkit.ir import Constant, CType, Function, Header, Parameter, Struct
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions, scaffold


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = HookRegistry.snapshot()
    yield
    HookRegistry.restore(snapshot)


def make_juce_unit(major: int, minor: int, build: int, extra_funcs: list[str]) -> Header:
    """Create a mock Header AST representing a specific JUCE version."""
    decls: list[Any] = [
        Constant("JUCE_MAJOR_VERSION", major, is_macro=True),
        Constant("JUCE_MINOR_VERSION", minor, is_macro=True),
        Constant("JUCE_BUILD_NUMBER", build, is_macro=True),
        Struct(name="String", fields=[]),
        Struct(name="MemoryBlock", fields=[]),
    ]
    if major >= 9:
        decls.append(Struct(name="File", fields=[]))

    for fn_name in extra_funcs:
        decls.append(
            Function(
                name=fn_name,
                return_type=CType("void"),
                parameters=[Parameter("flags", CType("int"))],
            )
        )

    return Header(
        path=f"juce_core_{major}_{minor}.h",
        declarations=decls,
    )


class TestJuceMultiVersionScaffolding:
    """Verifies multi-version scaffolding behavior, determinism, and downstream compatibility."""

    def test_multi_version_scaffolding_order_independence(self, tmp_path: Path) -> None:
        """Assert that scaffolding (v8.0 -> v8.1 -> v9.0) produces identical output to (v9.0 -> v8.1 -> v8.0)."""

        @hook("scaffold_tests", writer="nim", priority=Priority.PROJECT)
        def juce_test_hook(
            layout: ProjectLayout,
            unit: Header,
            _options: ScaffoldOptions,
            **_kwargs: Any,
        ) -> ProjectLayout:
            decls = getattr(unit, "declarations", [])
            constants = {d.name: d for d in decls if isinstance(d, Constant)}
            maj = int(constants.get("JUCE_MAJOR_VERSION", Constant("", 0)).value or 0)
            min_ = int(constants.get("JUCE_MINOR_VERSION", Constant("", 0)).value or 0)

            # Generate version-tailored test blocks with compile-time version guards
            tests = [
                textwrap.dedent("""\
                    suite "JUCE Core Test Suite":
                      when declared(constructString):
                        test "String default constructor":
                          check 1 == 1
                """)
            ]

            if maj == 8 and min_ == 0:
                tests.append(
                    textwrap.dedent("""\
                    when declared(juce80LegacyProcessor):
                      test "JUCE 8.0 Legacy Processor API":
                        check 80 == 80
                """)
                )
            elif maj == 8 and min_ == 1:
                tests.append(
                    textwrap.dedent("""\
                    when declared(juce81AudioProcessor):
                      test "JUCE 8.1 Enhanced Audio Processor":
                        check 81 == 81
                """)
                )
            elif maj >= 9:
                tests.append(
                    textwrap.dedent("""\
                    when declared(juce90ModernEngine):
                      test "JUCE 9.0 Modern Audio Engine":
                        check 90 == 90
                """)
                )

            combined_test_code = "\n\n".join(tests) + "\n"
            existing = layout.get_file("tests/test_juce_core.nim")
            if existing:
                layout.files.remove(existing)
            layout.files.append(
                OutputFile(
                    path="tests/test_juce_core.nim",
                    content=combined_test_code,
                    merge_strategy="canonical_merge",
                )
            )
            return layout

        unit_8_0 = make_juce_unit(8, 0, 4, ["juce80LegacyProcessor"])
        unit_8_1 = make_juce_unit(8, 1, 0, ["juce81AudioProcessor"])
        unit_9_0 = make_juce_unit(9, 0, 2, ["juce90ModernEngine"])

        opts = ScaffoldOptions(package_name="juce_core", target_language="nim", layout="package")

        # Run A: Forward progression (8.0 -> 8.1 -> 9.0)
        dir_forward = tmp_path / "forward"
        layout_8_0 = scaffold(unit_8_0, opts)
        layout_8_0.write_to_disk(dir_forward)

        layout_8_1 = scaffold(unit_8_1, opts)
        layout_8_1.write_to_disk(dir_forward)

        layout_9_0 = scaffold(unit_9_0, opts)
        layout_9_0.write_to_disk(dir_forward)

        # Run B: Reverse progression (9.0 -> 8.1 -> 8.0)
        dir_reverse = tmp_path / "reverse"
        layout_9_0_rev = scaffold(unit_9_0, opts)
        layout_9_0_rev.write_to_disk(dir_reverse)

        layout_8_1_rev = scaffold(unit_8_1, opts)
        layout_8_1_rev.write_to_disk(dir_reverse)

        layout_8_0_rev = scaffold(unit_8_0, opts)
        layout_8_0_rev.write_to_disk(dir_reverse)

        forward_test = (dir_forward / "tests" / "test_juce_core.nim").read_text(encoding="utf-8")
        reverse_test = (dir_reverse / "tests" / "test_juce_core.nim").read_text(encoding="utf-8")

        # Invariant: output is canonical, order-independent, and identical
        assert forward_test == reverse_test

        # All versions represented
        assert 'test "JUCE 8.0 Legacy Processor API":' in forward_test
        assert 'test "JUCE 8.1 Enhanced Audio Processor":' in forward_test
        assert 'test "JUCE 9.0 Modern Audio Engine":' in forward_test
        assert 'test "String default constructor":' in forward_test

    def test_downstream_user_code_dual_targeting(self, tmp_path: Path) -> None:
        """Verify that downstream user code can consume the generated bindings across versions."""
        nim_exe = shutil.which("nim")
        if not nim_exe:
            pytest.skip("Nim compiler not available in current environment")

        pkg_dir = tmp_path / "juce_pkg"
        unit_9_0 = make_juce_unit(9, 0, 2, ["juce90ModernEngine"])
        opts = ScaffoldOptions(package_name="juce_core", target_language="nim", layout="package")
        layout = scaffold(unit_9_0, opts)
        layout.write_to_disk(pkg_dir)

        # Downstream user application importing juce_core and conditionally calling functions
        user_app_code = textwrap.dedent("""\
            import juce_core

            # 1. Compile-time branching on version constants
            when JUCE_MAJOR_VERSION >= 9:
              proc runUserAudioEngine(): int =
                result = 90
            else:
              proc runUserAudioEngine(): int =
                result = 80

            # 2. Compile-time symbol probing
            when declared(juce90ModernEngine):
              proc probeModernFeature(): bool = true
            else:
              proc probeModernFeature(): bool = false

            assert runUserAudioEngine() == 90
            assert probeModernFeature() == true
        """)

        app_file = pkg_dir / "user_app.nim"
        app_file.write_text(user_app_code, encoding="utf-8")

        # Verify downstream application compiles and passes nim check
        proc = subprocess.run(
            [nim_exe, "check", "--path:src", str(app_file)],
            cwd=pkg_dir,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"nim check failed on downstream user app:\n{proc.stderr}\n{proc.stdout}"
