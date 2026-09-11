# Contributing to headerkit

Thanks for your interest in contributing to headerkit! This guide covers everything
you need to get started.

## Development setup

```bash
git clone https://github.com/axiomantic/headerkit.git
cd headerkit
pip install -e '.[dev]'
pytest
```

## Quality gates

All code must pass before submitting a PR:

```bash
ruff check .
ruff format --check .
mypy --strict headerkit/
pytest
```

Pre-commit hooks enforce lint and format checks automatically on each commit.

## PR process

1. Fork the repo and create a branch from `main`.
2. Make your changes.
3. Add or update tests as needed.
4. Run all quality gates listed above.
5. Update `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.
6. Open a pull request against `main`.
7. Dispatch CI for the pull request's head commit, and wait for it to pass. Nothing does this for you -- see below.

## CI is manual

The test matrix spans four operating systems and five Python versions. Running it on
every push costs real money and holds the macOS runner that every other pull request
is queued behind, so **no workflow in this repository runs on `push` or
`pull_request`.** You start CI yourself:

```bash
# The boundary Python versions (3.10 and 3.14) on Linux, macOS and Windows.
gh workflow run CI --ref <your-branch>

# Every Python version. Dispatch this before a release, or when a change is
# version-sensitive.
gh workflow run CI --ref <your-branch> -f full-matrix=true

# Watch it.
gh run watch "$(gh run list --workflow=CI --branch <your-branch> --limit 1 \
  --json databaseId --jq '.[0].databaseId')"
```

Two other workflows are dispatched by hand when a change calls for them. There is no
longer a `paths` filter deciding for you:

```bash
# When you touch headerkit/install_libclang.py or tests/test_install_libclang.py.
gh workflow run "Test install_libclang" --ref <your-branch>

# When you touch docs/ or mkdocs.yml and want the dev site updated.
gh workflow run Documentation --ref main
```

### The merge gate

Manual CI has one hazard, and one workflow exists for it. If nothing ran, a pull
request's checks list is *empty* -- and an empty checks list looks exactly like a
clean one. Merging on that appearance would mean merging code no machine has
compiled.

So the `Merge gate` workflow is the single exception that triggers automatically. It
runs in seconds, does no build work, and **starts red**:

- No `CI` run for your head commit: **failure**, with the dispatch command in the log.
- A `CI` run that failed, was cancelled, or is still going: **failure**.
- A `CI` run that succeeded for that exact commit: **success**.

It keys on the head SHA, never the branch. A green run on the commit before the one
under review is evidence about code that no longer exists, so pushing a new commit
turns the gate red again and you dispatch again. This is also why a green gate cannot
be obtained without a real run: the gate's only input is GitHub's own record of runs
against that SHA.

Once a dispatched run finishes, the gate re-evaluates on its own. If it has not, press
**Re-run** on the `Merge gate` check.

Make `CI ran and passed for this commit` a required status check on `main` in the
branch protection settings. With it required, merging without a dispatched, passing
run is not possible.

## Important notes

### Vendored clang bindings

`headerkit/_clang/` contains vendored upstream clang Python bindings. **Do not modify,
refactor, or lint these files.** They are maintained upstream and excluded from ruff
and mypy.

### Zero runtime dependencies

headerkit has no runtime dependencies and must stay that way. If a feature needs an
external package, make it an optional dependency with graceful degradation when absent.

### Registry pattern

Backends and writers use a self-registration pattern. When adding a new backend or
writer, follow the existing pattern in `headerkit/backends/` and `headerkit/writers/`.

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). Please
read it before participating.
