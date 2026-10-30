import? 'justfile.local'

default:
    @just --list

# 1. Dependency Setup
setup:
    uv sync --extra dev

# 2. Test Suite Execution
test:
    @just test-py

test-py:
    uv run --extra dev pytest tests/test_public_api.py

test-all:
    uv run --extra dev pytest -rs

# 3. Linter Execution
lint:
    @just lint-py

lint-py:
    uv run --extra dev ruff check .
    uv run --extra dev ruff format --check .
    uv run --extra dev mypy --strict headerkit

# 4. Formatter Execution
fmt:
    uv run --extra dev ruff format .

# 5. Build Artifacts
build:
    uv build --wheel

# 8. Clean Build Artifacts
clean:
    rm -rf build dist .pytest_cache .ruff_cache .mypy_cache *.egg-info .venv

# 9. Secret Scanning
check-secrets:
    @if command -v gitleaks >/dev/null 2>&1; then \
        gitleaks detect -v; \
    elif command -v pre-commit >/dev/null 2>&1; then \
        pre-commit run gitleaks --all-files; \
    else \
        echo "Error: neither gitleaks nor pre-commit found. Install gitleaks or pre-commit." >&2; exit 1; \
    fi

# 10. Upstream Template Synchronization
update-template:
    uvx copier update --trust
