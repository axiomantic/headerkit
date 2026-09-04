import pytest

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.hooks import HookDispatcher, PipelineContext
from headerkit.ir import BaseSpecifier, CType, Enum, Function, Header, Pointer, Reference, Struct, Typedef
from headerkit.writers.cython import CythonWriter

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
        try:
            import tree_sitter_cpp  # noqa: F401

            assert backend.supports_cpp is True
            assert "c++" in backend.supported_languages
        except ImportError:
            assert backend.supports_cpp is False

    def test_is_cpp_mode_explicit_override(self):
        backend = TreeSitterBackend()
        # Explicit -x c should force C mode even for .hpp filename
        assert backend._is_cpp_mode("struct Point { int x; };", "test.hpp", ["-x", "c"]) is False
        # Explicit -x c++ should force C++ mode even for .h filename
        assert backend._is_cpp_mode("struct Point { int x; };", "test.h", ["-x", "c++"]) is True
        # Explicit -std=c11 should force C mode even for .hpp filename
        assert backend._is_cpp_mode("struct Point { int x; };", "test.hpp", ["-std=c11"]) is False

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

    def test_parse_preprocessor_does_not_traverse_else_branch(self):
        code = """
        #if defined(USE_FLOAT)
        float process(float x);
        #else
        double process_alternative(double x);
        #endif
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "compute.h")

        assert len(header.declarations) == 1
        fn = header.declarations[0]
        assert isinstance(fn, Function)
        assert fn.name == "process"

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

    def test_parse_cpp_class_and_methods(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        class Widget {
        private:
            int m_id;
        public:
            Widget();
            explicit Widget(int id);
            virtual ~Widget();

            int get_id() const;
            void set_id(int id);
            static Widget create_default();
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "widget.hpp")

        assert len(header.declarations) == 1
        st = header.declarations[0]
        assert isinstance(st, Struct)
        assert st.name == "Widget"
        assert st.is_cppclass is True

        # Fields
        assert len(st.fields) == 1
        assert st.fields[0].name == "m_id"
        assert st.fields[0].access == "private"

        # Constructors
        assert len(st.constructors) == 2
        assert st.constructors[0].name == "Widget"
        assert st.constructors[1].name == "Widget"
        assert len(st.constructors[1].parameters) == 1

        # Destructor
        assert st.destructor is not None
        assert st.destructor.name == "~Widget"

        # Methods
        assert len(st.methods) == 3
        m_map = {m.name: m for m in st.methods}
        assert m_map["get_id"].is_const is True
        assert m_map["create_default"].is_static is True

    def test_parse_cpp_inheritance_and_virtual(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        class Shape {
        public:
            virtual void draw() = 0;
        };

        class Circle : public Shape {
        public:
            Circle();
            void draw() override;
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "shapes.hpp")

        assert len(header.declarations) == 2
        shape, circle = header.declarations[0], header.declarations[1]
        assert isinstance(shape, Struct) and isinstance(circle, Struct)

        assert shape.name == "Shape"
        assert len(shape.methods) == 1
        assert shape.methods[0].name == "draw"
        assert shape.methods[0].is_virtual is True
        assert shape.methods[0].is_pure_virtual is True

        assert circle.name == "Circle"
        assert len(circle.bases) == 1
        assert isinstance(circle.bases[0], BaseSpecifier)
        assert circle.bases[0].name == "Shape"
        assert circle.bases[0].access == "public"
        assert circle.bases[0].is_virtual is False

    def test_parse_cpp_namespaces_and_templates(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        namespace math {
        namespace linalg {

        template <typename T>
        class Vector {
        public:
            using ValueType = T;
            T x;
            T y;
            Vector(T x, T y);
            T get_x() const;
            Vector& operator+=(const Vector& other);
        };

        template <typename T>
        T dot_product(const Vector<T>& a, const Vector<T>& b);

        } // linalg
        } // math
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "linalg.hpp")

        assert len(header.declarations) == 2
        vec, dot = header.declarations[0], header.declarations[1]

        assert isinstance(vec, Struct)
        assert vec.name == "Vector"
        assert vec.namespace == "math::linalg"
        assert vec.template_params == ["T"]
        assert vec.is_cppclass is True
        assert vec.inner_typedefs.get("ValueType") == "T"

        # operator
        op = [m for m in vec.methods if m.name == "operator+="]
        assert len(op) == 1
        assert isinstance(op[0].return_type, Reference)

        assert isinstance(dot, Function)
        assert dot.name == "dot_product"
        assert dot.namespace == "math::linalg"
        assert dot.template_params == ["T"]

    def test_parse_cpp_roundtrip_cython_writer(self):
        pytest.importorskip("tree_sitter_cpp")
        code = """
        class Calculator {
        public:
            int value;
            void reset();
            int add(int x);
            int multiply(int a, int b);
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "calc.hpp")

        writer = CythonWriter()
        out = writer.write(header)
        assert 'cdef extern from "calc.hpp":' in out
        assert "cdef cppclass Calculator:" in out
        assert "int value" in out
        assert "void reset()" in out
        assert "int add(int x)" in out
        assert "int multiply(int a, int b)" in out

    def test_c_source_function_definitions(self):
        """Extract non-static function definitions from C source code (.c)."""
        code = """
        struct Config {
            int timeout;
            double threshold;
        };

        static int internal_helper(int x) {
            return x * 2;
        }

        int process_data(const struct Config *cfg, double *values, int count) {
            if (!cfg) return -1;
            return 0;
        }
        """
        backend = TreeSitterBackend()
        unit = backend.parse(code, "processor.c")

        structs = [d for d in unit.declarations if isinstance(d, Struct)]
        funcs = [d for d in unit.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert structs[0].name == "Config"
        assert len(structs[0].fields) == 2
        assert structs[0].fields[0].name == "timeout"
        assert str(structs[0].fields[0].type) == "int"

        func_names = [f.name for f in funcs]
        assert "process_data" in func_names
        assert "internal_helper" not in func_names

        fn = next(f for f in funcs if f.name == "process_data")
        assert str(fn.return_type) == "int"
        assert len(fn.parameters) == 3
        assert fn.parameters[0].name == "cfg"
        assert isinstance(fn.parameters[0].type, Pointer)
        assert fn.parameters[1].name == "values"
        assert isinstance(fn.parameters[1].type, Pointer)
        assert fn.parameters[2].name == "count"
        assert str(fn.parameters[2].type) == "int"
