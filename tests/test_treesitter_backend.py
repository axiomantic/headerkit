import pytest

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.hooks import HookDispatcher, PipelineContext
from headerkit.ir import CType, Enum, Function, Header, Pointer, Struct, Typedef

treesitter = pytest.mark.treesitter


@treesitter
class TestTreeSitterBackend:
    @classmethod
    def setup_class(cls):
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c")

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

    def test_parse_preprocessor_ifdef_and_linkage(self):
        code = """
        #ifndef FOO_H
        #define FOO_H

        #ifdef __cplusplus
        extern "C" {
        #endif

        int compute(int x);

        #ifdef __cplusplus
        }
        #endif

        #endif
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "foo.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "compute"

    def test_parse_pointer_return_and_void_param(self):
        code = """
        char* get_version(void);
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "version.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "get_version"
        assert isinstance(fn.return_type, Pointer)
        assert isinstance(fn.return_type.pointee, CType)
        assert str(fn.return_type) == "char*"
        assert len(fn.parameters) == 0

    def test_parse_multilevel_pointers_in_functions(self):
        code = "char ***get_entries(int **matrix, void **out_handle);"
        backend = TreeSitterBackend()
        header = backend.parse(code, "entries.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "get_entries"

        # Check 3-level pointer return type: char***
        p3 = fn.return_type
        assert isinstance(p3, Pointer)
        assert isinstance(p3.pointee, Pointer)
        assert isinstance(p3.pointee.pointee, Pointer)
        assert isinstance(p3.pointee.pointee.pointee, CType)
        assert p3.pointee.pointee.pointee.name == "char"

        # Check 2-level pointer parameter 1: int **matrix
        assert len(fn.parameters) == 2
        p_matrix = fn.parameters[0]
        assert p_matrix.name == "matrix"
        assert isinstance(p_matrix.type, Pointer)
        assert isinstance(p_matrix.type.pointee, Pointer)
        assert isinstance(p_matrix.type.pointee.pointee, CType)
        assert p_matrix.type.pointee.pointee.name == "int"

        # Check 2-level pointer parameter 2: void **out_handle
        p_handle = fn.parameters[1]
        assert p_handle.name == "out_handle"
        assert isinstance(p_handle.type, Pointer)
        assert isinstance(p_handle.type.pointee, Pointer)
        assert isinstance(p_handle.type.pointee.pointee, CType)
        assert p_handle.type.pointee.pointee.name == "void"

    def test_parse_multilevel_pointers_in_struct_and_typedef(self):
        code = """
        struct MatrixBundle {
            void **buffers;
            char ***labels;
        };

        typedef int **IntGrid;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "bundle.h")

        decls = header.declarations
        assert len(decls) == 2

        st = decls[0]
        assert isinstance(st, Struct)
        assert st.name == "MatrixBundle"
        assert len(st.fields) == 2

        f_buf = st.fields[0]
        assert f_buf.name == "buffers"
        assert isinstance(f_buf.type, Pointer)
        assert isinstance(f_buf.type.pointee, Pointer)
        assert isinstance(f_buf.type.pointee.pointee, CType)
        assert f_buf.type.pointee.pointee.name == "void"

        f_labels = st.fields[1]
        assert f_labels.name == "labels"
        assert isinstance(f_labels.type, Pointer)
        assert isinstance(f_labels.type.pointee, Pointer)
        assert isinstance(f_labels.type.pointee.pointee, Pointer)
        assert isinstance(f_labels.type.pointee.pointee.pointee, CType)
        assert f_labels.type.pointee.pointee.pointee.name == "char"

        td = decls[1]
        assert isinstance(td, Typedef)
        assert td.name == "IntGrid"
        assert isinstance(td.underlying_type, Pointer)
        assert isinstance(td.underlying_type.pointee, Pointer)
        assert isinstance(td.underlying_type.pointee.pointee, CType)
        assert td.underlying_type.pointee.pointee.name == "int"
