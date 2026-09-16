#!/usr/bin/env bash
set -euo pipefail

echo "=== Installing Linux Toolchains ==="

if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y libclang-dev gcc-14 g++-14
    sudo update-alternatives --install /usr/bin/cc cc /usr/bin/gcc-14 100
    sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100
    sudo update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++-14 100
    sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-14 100
    cc --version
else
    echo "Notice: apt-get not found; skipping Debian/Ubuntu package installation."
fi
