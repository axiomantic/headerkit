# Installing libclang

The `headerkit-install-libclang` CLI tool automates installing the **libclang** system dependency for your platform. It detects your OS and package manager, runs the appropriate install command, and verifies that headerkit can load the library afterward.

## Usage

Run as a console script (installed with headerkit):

```bash
headerkit-install-libclang
```

Or invoke as a Python module:

```bash
python -m headerkit.install_libclang
```

## CLI Options

| Option | Description |
|--------|-------------|
| `--version VERSION` | LLVM version to install (default: `21.1.8`). Only affects Windows ARM64 direct downloads; package managers use their own default version. |
| `--skip-verify` | Skip the post-install verification step that checks whether libclang is loadable by headerkit. |

## Platform Behavior

The tool automatically detects your platform and selects the appropriate installation method:

| Platform | Method | Command |
|----------|--------|---------|
| Linux (RHEL/Fedora/AlmaLinux) | dnf | `dnf install -y clang-devel` |
| Linux (Debian/Ubuntu) | apt-get | `apt-get install -y libclang-dev` |
| Linux (Alpine) | apk | `apk add clang-dev` |
| macOS | Homebrew | `brew install llvm` |
| Windows x64 | Chocolatey | `choco install llvm -y` |
| Windows ARM64 | Direct download | Downloads `LLVM-<version>-woa64.exe` from GitHub releases and runs a silent install |

On Linux, the tool tries package managers in order (dnf, apt-get, apk) and uses the first one available.

## When to Use

This tool is useful when you need to install libclang non-interactively:

- **CI/CD pipelines** where you need libclang available before running tests or builds
- **Docker images** where you want a single command to set up the dependency
- **Fresh development environments** to avoid looking up platform-specific instructions
- **Cross-platform scripts** that need to work on Linux, macOS, and Windows

For manual installation instructions with more control over versions and paths, see the [Installation](installation.md) guide.

## Example: GitHub Actions

Add a step to your workflow to install libclang before running tests:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install headerkit
        run: pip install headerkit

      - name: Install libclang
        run: headerkit-install-libclang

      - name: Run tests
        run: pytest
```

For Windows ARM64 runners, specify the LLVM version explicitly:

```yaml
      - name: Install libclang (Windows ARM64)
        run: headerkit-install-libclang --version 21.1.8
```

## Verification

By default, the tool verifies that libclang is loadable after installation. If verification fails, you may need to:

- Set your library path (e.g., `LD_LIBRARY_PATH` on Linux)
- Restart your shell to pick up new PATH entries
- Check that the installed LLVM version is compatible (headerkit supports LLVM 18-21)

Skip verification with `--skip-verify` if you know the library will not be on the default search path until a later step configures it.
