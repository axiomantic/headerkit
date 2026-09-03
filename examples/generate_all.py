#!/usr/bin/env python3
"""Generate Nim bindings for RtAudio, RtMidi, NNG, and CLAP examples."""

import os

from headerkit.backends.libclang import LibclangBackend
from headerkit.writers.nim import NimWriter

backend = LibclangBackend()
writer = NimWriter()

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

targets = [
    {
        "name": "nng",
        "output": "examples/nim/nng/nng.nim",
        "header_path": os.path.join(repo_root, "examples/headers/nng_all.h"),
        "file": os.path.join(repo_root, "examples/headers/nng_all.h"),
        "include_dirs": ["/opt/homebrew/include"],
        "project_prefixes": ("/opt/homebrew/include/nng",),
        "is_cpp": False,
    },
    {
        "name": "rtmidi_c",
        "output": "examples/nim/rtmidi/rtmidi_c.nim",
        "header_path": "rtmidi/rtmidi_c.h",
        "file": "/opt/homebrew/include/rtmidi/rtmidi_c.h",
        "include_dirs": ["/opt/homebrew/include"],
        "project_prefixes": ("/opt/homebrew/include/rtmidi",),
        "is_cpp": False,
    },
    {
        "name": "rtaudio_c",
        "output": "examples/nim/rtaudio/rtaudio_c.nim",
        "header_path": "rtaudio/rtaudio_c.h",
        "file": "/opt/homebrew/include/rtaudio/rtaudio_c.h",
        "include_dirs": ["/opt/homebrew/include"],
        "project_prefixes": ("/opt/homebrew/include/rtaudio",),
        "is_cpp": False,
    },
    {
        "name": "clap",
        "output": "examples/nim/clap/clap.nim",
        "header_path": "clap/clap.h",
        "file": os.path.join(repo_root, "examples/headers/clap/clap.h"),
        "include_dirs": [
            os.path.join(repo_root, "examples/headers"),
            os.path.join(repo_root, "examples/headers/clap"),
        ],
        "project_prefixes": (os.path.join(repo_root, "examples/headers/clap"),),
        "is_cpp": False,
    },
]


def main() -> None:
    for t in targets:
        if "code" in t:
            code = t["code"]
        else:
            with open(t["file"]) as f:
                code = f.read()

        extra_args = ["-x", "c++", "-std=c++17"] if t["is_cpp"] else []
        header = backend.parse(
            code,
            t["header_path"],
            include_dirs=t["include_dirs"],
            project_prefixes=t["project_prefixes"],
            recursive_includes=True,
            extra_args=extra_args,
        )
        nim_code = writer.write(header)
        out_path = os.path.join(repo_root, t["output"])
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(nim_code)
        print(f"Generated {t['output']} ({len(header.declarations)} declarations)")


if __name__ == "__main__":
    main()
