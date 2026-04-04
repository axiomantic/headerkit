# CI Store Population

headerkit's PEP 517 build backend populates `.headerkit/` during wheel
builds. To keep the store up to date across platforms, use a CI workflow
that builds wheels in a matrix, collects artifacts, and opens a PR if
anything changed. No custom action needed -- just standard GitHub Actions
building blocks.

## How it works

The `.headerkit/` directory contains IR (parsed headers) and output
(generated bindings) so that downstream builds work without libclang
installed. This directory should be committed to your repository; it is
not ephemeral cache.

The CI pattern has two jobs:

1. **build** -- runs in a matrix across platforms, builds wheels, and
   uploads the `.headerkit/` directory as an artifact.
2. **update-store** -- downloads all artifacts into a single `.headerkit/`
   directory and opens a PR if anything changed.

## Example workflow

```yaml
name: Update headerkit store
on:
  push:
    branches: [main]
    paths:
      - "include/**/*.h"
      - "pyproject.toml"
  schedule:
    - cron: "0 6 * * 1"  # weekly on Monday

jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Build wheel
        run: pip install build && python -m build --wheel

      - uses: actions/upload-artifact@v4
        with:
          name: headerkit-store-${{ matrix.os }}
          path: .headerkit/

  update-store:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4

      - uses: actions/download-artifact@v4
        with:
          pattern: headerkit-store-*
          path: .headerkit/
          merge-multiple: true

      - uses: peter-evans/create-pull-request@v8
        with:
          commit-message: "chore: update headerkit store"
          title: "chore: update headerkit store"
          branch: headerkit/update-store
          body: |
            Automated update of `.headerkit/` store from CI matrix build.
          labels: automated
```

## Using cibuildwheel

If your project uses cibuildwheel, replace the build step:

```yaml
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Build wheels
        run: pip install cibuildwheel && cibuildwheel --output-dir dist

      - uses: actions/upload-artifact@v4
        with:
          name: headerkit-store-${{ matrix.os }}
          path: .headerkit/
```

The build backend populates `.headerkit/` as a side effect of each wheel
build, so cibuildwheel produces store entries for every platform in the
matrix automatically.

## Configuration options

### Customizing platforms

Add or remove entries from `matrix.os` to match your target platforms:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, ubuntu-24.04-arm, macos-latest, windows-latest]
```

### Configuring the PR

The `peter-evans/create-pull-request` action accepts many options for
customizing the resulting pull request:

```yaml
- uses: peter-evans/create-pull-request@v8
  with:
    commit-message: "chore: update headerkit store"
    title: "chore: update headerkit store"
    branch: headerkit/update-store
    labels: automated, dependencies
    reviewers: your-username
    draft: false
```

See the [create-pull-request documentation](https://github.com/peter-evans/create-pull-request)
for the full list of inputs.

### Running on schedule vs on push

The example workflow triggers both on push (when headers change) and on a
weekly schedule. Adjust to fit your project:

- **On push with path filters** -- reacts to header changes immediately.
- **On schedule** -- catches changes from dependency updates or toolchain
  upgrades that affect generated output.
- **Manual dispatch** -- add `workflow_dispatch:` to the `on:` block
  to allow manual runs from the Actions tab.

## See also

- [Cache Strategy Guide](cache.md) for cache layout, bypass flags, and
  multi-platform population details.
- [Build Backend Guide](build-backend.md) for using headerkit as a
  PEP 517 build backend with committed store.
