"""Demonstration of Headerkit Tree-sitter backend and unified hooks.

Parses a C header without requiring libclang, converts it to IR,
and emits Python ctypes bindings.
"""

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.writers.ctypes import CtypesWriter

CODE = """
typedef struct Color {
    unsigned char r;
    unsigned char g;
    unsigned char b;
} Color;

int blend(const Color *c1, const Color *c2, Color *out);
"""


def main() -> None:
    backend = TreeSitterBackend()
    header = backend.parse(CODE, "color.h")

    writer = CtypesWriter()
    output = writer.write(header)
    print(output)


if __name__ == "__main__":
    main()
