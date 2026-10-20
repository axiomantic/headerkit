#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing Linux Toolchains ==="

# A container job (Forgejo Actions) runs as root, where sudo is typically
# absent. A hosted GitHub runner runs as an unprivileged user with
# passwordless sudo. Use sudo only when it is both present and needed.
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
fi

if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update
    $SUDO apt-get install -y libclang-dev gcc-14 g++-14
    $SUDO update-alternatives --install /usr/bin/cc cc /usr/bin/gcc-14 100
    $SUDO update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100
    $SUDO update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++-14 100
    $SUDO update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-14 100
    cc --version
else
    echo "Notice: apt-get not found; skipping Debian/Ubuntu package installation."
fi
