"""Example showing how to use Copier with HeaderKit via BYOScaffolder.

This script demonstrates how an external template engine (like Copier) can plug
into HeaderKit's unified hook engine to generate rich project structures with
custom corporate templates, git-backed migrations, and conditional files.

Usage:
------
    python copier_scaffolder.py
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from headerkit.hooks import Priority, hook
from headerkit.ir import CType, Function, Header, Parameter
from headerkit.scaffold import (
    BYOScaffolder,
    OutputFile,
    ProjectLayout,
    ScaffoldOptions,
    scaffold,
)
from headerkit.writers import get_writer

try:
    import copier  # type: ignore[import-untyped]

    HAS_COPIER = True
except ImportError:
    HAS_COPIER = False


class CopierBYOScaffolder(BYOScaffolder):
    """A custom BYOScaffolder plugin powered by Copier.

    When Copier is installed, this scaffolder renders a Copier template
    directory with full answers, migrations, and conditional logic.
    When Copier is absent, it gracefully provides a structured mock layout.
    """

    def __init__(self, template_dir: Path | str | None = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else None

    def scaffold(self, unit: Header, options: ScaffoldOptions) -> ProjectLayout:
        writer = get_writer(options.target_language)
        bindings_content = writer.write(unit)

        data = {
            "package_name": options.package_name,
            "target_language": options.target_language,
            "test_type": options.test_type,
            "bindings_code": bindings_content,
        }

        if HAS_COPIER and self.template_dir and self.template_dir.exists():
            with tempfile.TemporaryDirectory() as tmp_out:
                copier.run_copy(
                    str(self.template_dir),
                    tmp_out,
                    data=data,
                    defaults=True,
                    overwrite=True,
                )
                files = []
                for p in Path(tmp_out).rglob("*"):
                    if p.is_file():
                        rel = str(p.relative_to(tmp_out))
                        files.append(OutputFile(path=rel, content=p.read_text(encoding="utf-8")))
                return ProjectLayout(files=files)

        # Fallback / simulated Copier template output
        return ProjectLayout(
            files=[
                OutputFile(
                    path="copier-template.yml",
                    content=textwrap.dedent(f"""\
                        # Copier template configuration
                        package_name: {options.package_name}
                        target_language: {options.target_language}
                        generator: HeaderKit CopierBYOScaffolder
                    """),
                ),
                OutputFile(
                    path=f"src/{options.package_name}_bindings.txt",
                    content=bindings_content,
                ),
            ]
        )


def main() -> None:
    # 1. Define a sample C interface
    func = Function(
        name="tensor_matmul",
        return_type=CType("void"),
        parameters=[
            Parameter("a", CType("float", qualifiers=["const"])),
            Parameter("b", CType("float", qualifiers=["const"])),
            Parameter("out", CType("float")),
        ],
    )
    unit = Header(path="tensor.h", declarations=[func])

    # 2. Register our custom Copier scaffolder with Priority.OVERRIDE
    copier_scaffolder = CopierBYOScaffolder()

    @hook("scaffold_project", priority=Priority.OVERRIDE)
    def copier_hook(unit: Header, options: ScaffoldOptions, **_kwargs: object) -> ProjectLayout:
        return copier_scaffolder.scaffold(unit, options)

    # 3. Trigger project scaffolding through HeaderKit's unified API
    opts = ScaffoldOptions(
        package_name="mytensor",
        target_language="nim",
        layout="package",
        test_type="both",
    )
    layout = scaffold(unit, opts)

    print(f"Generated {len(layout.files)} files via CopierBYOScaffolder:")
    for f in layout.files:
        print(f"  - {f.path}")


if __name__ == "__main__":
    main()
