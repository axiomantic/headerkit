import pytest

from headerkit.backends.treesitter import TreeSitterBackend
from headerkit.hooks import HookDispatcher, PipelineContext
from headerkit.ir import (
    Array,
    BaseSpecifier,
    CType,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Pointer,
    Reference,
    Struct,
    Typedef,
    Variable,
)
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

    def test_c_variadic_function_declaration(self):
        """Verify C variadic function declarations have is_variadic=True."""
        code = "int printf(const char *format, ...);"
        backend = TreeSitterBackend()
        header = backend.parse(code, "stdio.h")

        funcs = [d for d in header.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "printf"
        assert fn.is_variadic is True
        assert len(fn.parameters) == 1
        assert fn.parameters[0].name == "format"
        assert isinstance(fn.parameters[0].type, Pointer)

    def test_global_variables_simple_and_pointer(self):
        """Verify global variables including pointers and sized types are parsed."""
        code = """
        int* ptr;
        int arr[10];
        unsigned long count;
        extern int global_flag;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "vars.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}
        assert "ptr" in vars_by_name
        assert isinstance(vars_by_name["ptr"].type, Pointer)
        assert str(vars_by_name["ptr"].type.pointee) == "int"

        assert "arr" in vars_by_name
        assert isinstance(vars_by_name["arr"].type, Array)
        assert vars_by_name["arr"].type.size == 10
        assert str(vars_by_name["arr"].type.element_type) == "int"

        assert "count" in vars_by_name
        assert str(vars_by_name["count"].type) == "unsigned long"

        assert "global_flag" in vars_by_name
        assert str(vars_by_name["global_flag"].type) == "int"

    def test_multiple_declarators_in_single_declaration(self):
        """Verify multiple variables declared in a single statement are all captured."""
        code = "int a, *b, c[5];"
        backend = TreeSitterBackend()
        header = backend.parse(code, "multi.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}
        assert len(vars_by_name) == 3

        assert "a" in vars_by_name
        assert str(vars_by_name["a"].type) == "int"

        assert "b" in vars_by_name
        assert isinstance(vars_by_name["b"].type, Pointer)
        assert str(vars_by_name["b"].type.pointee) == "int"

        assert "c" in vars_by_name
        assert isinstance(vars_by_name["c"].type, Array)
        assert vars_by_name["c"].type.size == 5

    def test_array_of_pointers_and_pointer_to_array(self):
        """Verify complex declarators: array of pointers vs pointer to array."""
        code = """
        char *argv[5];
        char (*row_ptr)[5];
        int matrix[10][20];
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "arrays.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}

        # argv is Array of 5 Pointers to char
        argv = vars_by_name["argv"]
        assert isinstance(argv.type, Array)
        assert argv.type.size == 5
        assert isinstance(argv.type.element_type, Pointer)
        assert str(argv.type.element_type.pointee) == "char"

        # row_ptr is Pointer to Array of 5 chars
        row_ptr = vars_by_name["row_ptr"]
        assert isinstance(row_ptr.type, Pointer)
        assert isinstance(row_ptr.type.pointee, Array)
        assert row_ptr.type.pointee.size == 5
        assert str(row_ptr.type.pointee.element_type) == "char"

        # matrix is Array of 10 Arrays of 20 ints
        matrix = vars_by_name["matrix"]
        assert isinstance(matrix.type, Array)
        assert matrix.type.size == 10
        assert isinstance(matrix.type.element_type, Array)
        assert matrix.type.element_type.size == 20
        assert str(matrix.type.element_type.element_type) == "int"

    def test_function_pointer_variable_and_typedef(self):
        """Verify function pointer variable and typedef parsing."""
        code = """
        int (*handler)(int code, double val);
        typedef void (*callback_t)(const char *msg);
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "callbacks.h")

        vars_by_name = {d.name: d for d in header.declarations if isinstance(d, Variable)}
        assert "handler" in vars_by_name
        handler = vars_by_name["handler"]
        assert isinstance(handler.type, Pointer)
        assert isinstance(handler.type.pointee, FunctionPointer)
        fp = handler.type.pointee
        assert str(fp.return_type) == "int"
        assert len(fp.parameters) == 2
        assert fp.parameters[0].name == "code"
        assert str(fp.parameters[0].type) == "int"
        assert fp.parameters[1].name == "val"
        assert str(fp.parameters[1].type) == "double"

        typedefs = {d.name: d for d in header.declarations if isinstance(d, Typedef)}
        assert "callback_t" in typedefs
        cb = typedefs["callback_t"]
        assert isinstance(cb.underlying_type, Pointer)
        assert isinstance(cb.underlying_type.pointee, FunctionPointer)
        cb_fp = cb.underlying_type.pointee
        assert str(cb_fp.return_type) == "void"
        assert len(cb_fp.parameters) == 1
        assert cb_fp.parameters[0].name == "msg"

    def test_struct_with_callback_and_bitfield(self):
        """Verify struct fields handle function pointers and bitfields."""
        code = """
        struct Device {
            int id;
            int (*read)(void);
            unsigned int flags : 4;
        };
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "dev.h")

        structs = [d for d in header.declarations if isinstance(d, Struct)]
        assert len(structs) == 1
        st = structs[0]
        assert len(st.methods) == 0  # read must NOT be classified as a method
        fields_by_name = {f.name: f for f in st.fields}

        assert "id" in fields_by_name
        assert str(fields_by_name["id"].type) == "int"

        assert "read" in fields_by_name
        read_field = fields_by_name["read"]
        assert isinstance(read_field.type, Pointer)
        assert isinstance(read_field.type.pointee, FunctionPointer)

        assert "flags" in fields_by_name
        assert fields_by_name["flags"].bit_width == 4

    def test_opaque_struct_typedef_and_deduplication(self):
        """Opaque struct inside typedefs emits Struct and Typedef, avoiding duplicate structs."""
        code = """
        typedef struct db_connection db;
        typedef struct db_connection *db_ptr;
        typedef struct db_statement db_stmt;
        """
        backend = TreeSitterBackend()
        header = backend.parse(code, "db.h")

        struct_names = [d.name for d in header.declarations if isinstance(d, Struct)]
        typedef_names = [d.name for d in header.declarations if isinstance(d, Typedef)]

        assert struct_names == ["db_connection", "db_statement"]
        assert typedef_names == ["db", "db_ptr", "db_stmt"]

        # Struct followed by typedef must not re-emit duplicate struct
        code2 = """
        struct MyStruct { int a; };
        typedef struct MyStruct MyStructAlias;
        """
        header2 = backend.parse(code2, "mystruct.h")
        structs2 = [d for d in header2.declarations if isinstance(d, Struct)]
        typedefs2 = [d for d in header2.declarations if isinstance(d, Typedef)]
        assert len(structs2) == 1
        assert structs2[0].name == "MyStruct"
        assert len(structs2[0].fields) == 1
        assert len(typedefs2) == 1
        assert typedefs2[0].name == "MyStructAlias"
