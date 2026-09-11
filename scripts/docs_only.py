#!/usr/bin/env python3
"""Decide whether a set of changed paths is documentation-only.

The path set itself lives in ``.github/docs-only-paths.txt`` and is read, not
restated. Three places once carried their own spelling of "docs-only" -- the
Versioning rule in AGENTS.md, the ``paths-ignore`` list in ``ci.yml``, and the
release-prep check -- and they disagreed about which files counted.

Reads changed paths on stdin, one per line. Exits 0 if every path matches a
pattern, 1 if any path does not, and 1 on an empty input: a diff with no files
is not a docs-only change, it is a question the caller asked wrong.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERN_FILE = Path(__file__).resolve().parent.parent / ".github" / "docs-only-paths.txt"


def _compile(pattern: str) -> re.Pattern[str]:
    """Translate one GitHub Actions path-filter glob into a full-match regex.

    ``fnmatch`` is not usable here: its ``*`` crosses ``/``, so ``*.md`` would
    match ``docs/deep/nested.md`` and the root-level restriction the rule
    depends on would silently vanish.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z")


def load_patterns(path: Path = PATTERN_FILE) -> list[re.Pattern[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_compile(stripped) for line in lines if (stripped := line.strip()) and not stripped.startswith("#")]


def is_docs_only(paths: list[str], patterns: list[re.Pattern[str]]) -> bool:
    if not paths:
        return False
    return all(any(p.match(path) for p in patterns) for path in paths)


def main() -> int:
    patterns = load_patterns()
    if not patterns:
        print(f"error: no patterns in {PATTERN_FILE}", file=sys.stderr)
        return 2
    paths = [line.strip() for line in sys.stdin if line.strip()]
    verdict = is_docs_only(paths, patterns)
    print("docs-only" if verdict else "not docs-only")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
