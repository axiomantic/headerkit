#!/usr/bin/env bash
set -euo pipefail

# Ensure virtualenv binaries are on PATH if running locally in a virtualenv
if [ -d ".venv/bin" ]; then
    export PATH="$(pwd)/.venv/bin:$PATH"
fi

echo "=== Running CI Lint Checks ==="

# Install package in editable mode with lint extras if not already installed
if ! python3 -c "import ruff, mypy" >/dev/null 2>&1; then
    python3 -m pip install -e '.[lint]'
fi

# Run ruff check and format check
echo "--- Ruff check ---"
ruff check .

echo "--- Ruff format check ---"
ruff format --check .

# Run mypy strict type check
echo "--- Mypy strict check ---"
mypy --strict headerkit

# Stubtest vendored clang bindings
for version in 18 19 20 21; do
    echo "--- Stubtest Clang v${version} ---"
    python3 -m mypy.stubtest "headerkit._clang.v${version}.cindex"
done

echo "--- Stubtest Clang v18 enumerations ---"
python3 -m mypy.stubtest "headerkit._clang.v18.enumerations"

echo "=== All Lint Checks Passed ==="
