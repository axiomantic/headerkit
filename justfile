import? 'justfile.local'

default:
    @just --list

# 1. Dependency Setup
setup:
    python3 -m pip install -e ".[dev]"

# 2. Test Suite Execution
test:
    @just test-py

test-py:
    uv run --with pytest pytest tests_py

# 3. Linter Execution
lint:
    @just lint-py

lint-py:
    uv run --with ruff ruff check .

# 4. Formatter Execution
fmt:
    uv run --with ruff ruff format .

# 5. Build Artifacts
build:
    python3 -m build --wheel

# 8. Clean Build Artifacts
clean:
    rm -rf nimcache htmldocs site docs/dist docs/.astro tools/docgen dist .venv
    find . -name '*.exe' -delete

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
