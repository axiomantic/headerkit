#!/usr/bin/env bash
set -euo pipefail

# Ensure virtualenv binaries are on PATH if running locally in a virtualenv
if [ -d ".venv/bin" ]; then
    export PATH="$(pwd)/.venv/bin:$PATH"
fi

echo "=== Running CI Tests ==="

# Verify toolchain prerequisites if script is available
if [ -f "scripts/check_test_toolchain.py" ]; then
    echo "--- Checking test toolchain ---"
    python3 scripts/check_test_toolchain.py
fi

# Run pytest with options passed as arguments or defaults
PYTEST_ARGS=("-rs")
if [ "$#" -gt 0 ]; then
    PYTEST_ARGS=("$@")
fi

echo "--- Executing pytest ${PYTEST_ARGS[*]} ---"
pytest "${PYTEST_ARGS[@]}"

echo "=== All Tests Passed ==="
