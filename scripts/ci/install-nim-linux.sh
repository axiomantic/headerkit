#!/usr/bin/env bash
set -euo pipefail

# Installs the pinned Nim release directly from nim-lang.org's official
# prebuilt Linux x86_64 tarball. The GitHub workflow (.github/workflows/test.yml)
# provisions Nim via asdf-vm, which needs GitHub API access and an asdf plugin
# fetch; the Forgejo runner's job network is deny-by-default outbound, so this
# script takes the single-destination path instead: one HTTPS download, no
# package-manager plugin index, no GitHub token.
#
# NIM_VERSION must match .github/workflows/test.yml's env.NIM_VERSION so the
# two CI paths test the same compiler. Kept as a separate literal here (no
# shared source file exists for a one-line version string); a version bump
# updates both.
NIM_VERSION="2.2.12"
NIM_INSTALL_DIR="/opt/nim"

echo "=== Installing Nim ${NIM_VERSION} ==="

url="https://nim-lang.org/download/nim-${NIM_VERSION}-linux_x64.tar.xz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/nim.tar.xz" "$url"

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
fi
$SUDO mkdir -p "$NIM_INSTALL_DIR"
$SUDO tar -xJf "$tmp/nim.tar.xz" -C "$NIM_INSTALL_DIR" --strip-components=1

echo "$NIM_INSTALL_DIR/bin" >> "${GITHUB_PATH:-/dev/null}"
"$NIM_INSTALL_DIR/bin/nim" --version
