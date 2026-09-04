from __future__ import annotations

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.hooks import HookDispatcher, PipelineContext
from headerkit.ir import Enum, Function, Header, Pointer, Struct


class TestTreeSitterBackend:
    def test_availability_and_capabilities(self):
        backend = TreeSitterBackend()
        assert backend.name == "tree-sitter"
        assert backend.is_available() is True
        assert "c" in backend.supported_languages
        assert backend.supports_cpp is False

    def test_parse_simple_struct(self):
        code = """
        struct Point {
            int x;
            int y;
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "test.h")

        assert len(header.declarations) == 1
        st = header.declarations[0]
        assert isinstance(st, Struct)
        assert st.name == "Point"
        assert len(st.fields) == 2
        assert st.fields[0].name == "x"
        assert str(st.fields[0].type) == "int"
        assert st.fields[1].name == "y"
        assert str(st.fields[1].type) == "int"

    def test_parse_function_declaration(self):
        code = "int distance(const Point *a, const Point *b);"
        backend = TreeSitterBackend()
        header = backend.parse(code, "math.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "distance"
        assert str(fn.return_type) == "int"
        assert len(fn.parameters) == 2
        assert fn.parameters[0].name == "a"
        assert isinstance(fn.parameters[0].type, Pointer)
        assert fn.parameters[1].name == "b"
        assert isinstance(fn.parameters[1].type, Pointer)

    def test_parse_typedef_struct(self):
        code = """
        typedef struct Vector3 {
            float x, y, z;
        } Vector3;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "vec.h")

        assert len(header.declarations) >= 1
        st = [d for d in header.declarations if isinstance(d, Struct)][0]
        assert st.name == "Vector3"
        assert st.is_typedef is True
        assert len(st.fields) == 3

    def test_parse_enum(self):
        code = """
        enum Status {
            STATUS_OK = 0,
            STATUS_ERR = 1
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "status.h")

        assert len(header.declarations) == 1
        en = header.declarations[0]
        assert isinstance(en, Enum)
        assert en.name == "Status"
        assert len(en.values) == 2
        assert en.values[0].name == "STATUS_OK"
        assert en.values[0].value == 0
        assert en.values[1].name == "STATUS_ERR"
        assert en.values[1].value == 1

    def test_hook_fallback_dispatch(self):
        dispatcher = HookDispatcher()
        ctx = PipelineContext(backend="tree-sitter", language="c")

        result = dispatcher.first_result("parse_unit", "int add(int a, int b);", "math.h", context=ctx)
        assert isinstance(result, Header)
        assert len(result.declarations) == 1
        fn = result.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "add"
