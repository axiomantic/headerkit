from __future__ import annotations

import pytest

from headerkit.backends import get_backend
from headerkit.hooks import HookDispatcher, HookRegistry, PipelineContext, execute_pipeline
from headerkit.ir import (
    Function,
    Pointer,
    SourceUnit,
    Struct,
)


class TestPolyglotASTExtraction:
    """Test suite for extracting interface surfaces from polyglot source units."""

    @pytest.fixture(autouse=True)
    def clean_registry(self):
        from headerkit.backends import _ensure_backends_loaded
        from headerkit.writers import _ensure_writers_loaded

        _ensure_backends_loaded()
        _ensure_writers_loaded()
        saved = HookRegistry.snapshot()
        yield
        HookRegistry.restore(saved)

    @pytest.mark.treesitter
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
        backend = get_backend("tree-sitter")
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

    def test_rust_interface_extraction(self):
        """Extract extern C functions and #[repr(C)] structs from Rust source (.rs)."""
        code = """
        #[repr(C)]
        pub struct Point3D {
            pub x: f64,
            pub y: f64,
            pub z: f64,
        }

        pub struct InternalRustStruct {
            field: String,
        }

        #[no_mangle]
        pub extern \"C\" fn calculate_norm(pt: *const Point3D) -> f64 {
            (pt.x * pt.x + pt.y * pt.y + pt.z * pt.z).sqrt()
        }

        extern \"C\" fn unexported_c_fn() {}
        pub fn normal_rust_fn() {}
        """
        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="rust", classification="interface")
        unit = dispatcher.first_result("parse_unit", code, "geometry.rs", context=ctx)

        assert isinstance(unit, SourceUnit)
        assert unit.language == "rust"

        structs = [d for d in unit.declarations if isinstance(d, Struct)]
        funcs = [d for d in unit.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert structs[0].name == "Point3D"
        assert len(structs[0].fields) == 3
        assert structs[0].fields[0].name == "x"
        assert str(structs[0].fields[0].type) == "double"

        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "calculate_norm"
        assert str(fn.return_type) == "double"
        assert len(fn.parameters) == 1
        assert fn.parameters[0].name == "pt"
        assert isinstance(fn.parameters[0].type, Pointer)

    def test_zig_interface_extraction(self):
        """Extract export fn and extern struct from Zig source (.zig)."""
        code = """
        pub const Rgba = extern struct {
            r: u8,
            g: u8,
            b: u8,
            a: u8,
        };

        const InternalType = struct {
            val: i32,
        };

        export fn invert_color(color: *Rgba) void {
            color.r = 255 - color.r;
            color.g = 255 - color.g;
            color.b = 255 - color.b;
        }

        fn regular_zig_fn() void {}
        """
        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="zig", classification="source")
        unit = dispatcher.first_result("parse_unit", code, "graphics.zig", context=ctx)

        assert isinstance(unit, SourceUnit)
        assert unit.language == "zig"

        structs = [d for d in unit.declarations if isinstance(d, Struct)]
        funcs = [d for d in unit.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert structs[0].name == "Rgba"
        assert len(structs[0].fields) == 4
        assert structs[0].fields[0].name == "r"
        assert str(structs[0].fields[0].type) == "uint8_t"

        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "invert_color"
        assert str(fn.return_type) == "void"
        assert len(fn.parameters) == 1
        assert fn.parameters[0].name == "color"
        assert isinstance(fn.parameters[0].type, Pointer)

    def test_nim_interface_extraction(self):
        """Extract exportc procs and exported types from Nim source (.nim)."""
        code = """
        type
          Matrix* = object
            rows*: int32
            cols*: int32

          InternalHelper = object
            secret: int

        proc create_matrix*(rows: int32, cols: int32): ptr Matrix {.exportc, dynlib.} =
          discard

        proc private_proc(x: int) =
          discard
        """
        dispatcher = HookDispatcher()
        ctx = PipelineContext(language="nim", classification="source")
        unit = dispatcher.first_result("parse_unit", code, "matrix.nim", context=ctx)

        assert isinstance(unit, SourceUnit)
        assert unit.language == "nim"

        structs = [d for d in unit.declarations if isinstance(d, Struct)]
        funcs = [d for d in unit.declarations if isinstance(d, Function)]

        assert len(structs) == 1
        assert structs[0].name == "Matrix"
        assert len(structs[0].fields) == 2
        assert structs[0].fields[0].name == "rows"
        assert str(structs[0].fields[0].type) == "int32_t"

        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "create_matrix"
        assert isinstance(fn.return_type, Pointer)
        assert len(fn.parameters) == 2
        assert fn.parameters[0].name == "rows"
        assert str(fn.parameters[0].type) == "int32_t"

    def test_polyglot_end_to_end_to_ctypes_writer(self):
        """End-to-end pipeline: Rust interface -> SourceUnit -> Python ctypes output."""
        code = """
        #[repr(C)]
        pub struct Handle {
            pub id: u64,
        }

        #[no_mangle]
        pub extern \"C\" fn get_handle_id(h: *const Handle) -> u64 {
            h.id
        }
        """
        ctx = PipelineContext(language="rust", writer="ctypes")
        unit, output = execute_pipeline("handle.rs", code=code, context=ctx)
        assert output is not None

        assert "class Handle(ctypes.Structure):" in output
        assert '("id", ctypes.c_uint64)' in output
        assert "_lib.get_handle_id.argtypes = [ctypes.POINTER(Handle)]" in output
        assert "_lib.get_handle_id.restype = ctypes.c_uint64" in output

    def test_polyglot_backend_registry(self):
        """Verify rust, zig, and nim backends are registered and accessible via get_backend()."""
        from headerkit.backends import get_backend, is_backend_available, list_backends

        backends = list_backends()
        assert "rust" in backends
        assert "zig" in backends
        assert "nim" in backends

        assert is_backend_available("rust") is True
        assert is_backend_available("zig") is True
        assert is_backend_available("nim") is True

        rust_backend = get_backend("rust")
        assert rust_backend.name == "rust"
        assert "rust" in rust_backend.supported_languages
        unit = rust_backend.parse('pub extern "C" fn test_fn() {}', "test.rs")
        assert isinstance(unit, SourceUnit)
        assert len(unit.declarations) == 1

        zig_backend = get_backend("zig")
        assert zig_backend.name == "zig"
        assert "zig" in zig_backend.supported_languages
        unit_z = zig_backend.parse("export fn test_fn() void {}", "test.zig")
        assert isinstance(unit_z, SourceUnit)
        assert len(unit_z.declarations) == 1

        nim_backend = get_backend("nim")
        assert nim_backend.name == "nim"
        assert "nim" in nim_backend.supported_languages
        unit_n = nim_backend.parse("proc test_fn*() {.exportc.}", "test.nim")
        assert isinstance(unit_n, SourceUnit)
        assert len(unit_n.declarations) == 1

    def test_rust_and_zig_enum_extraction(self):
        """Verify enum extraction for Rust and Zig sources."""
        from headerkit.ir import Enum

        rust_code = """
        #[repr(C)]
        pub enum Status {
            Ok = 0,
            Error = 1,
            Pending,
        }
        """
        backend_r = get_backend("rust")
        unit_r = backend_r.parse(rust_code, "status.rs")
        enums_r = [d for d in unit_r.declarations if isinstance(d, Enum)]
        assert len(enums_r) == 1
        assert enums_r[0].name == "Status"
        assert len(enums_r[0].values) == 3
        assert enums_r[0].values[0].name == "Ok" and enums_r[0].values[0].value == 0
        assert enums_r[0].values[1].name == "Error" and enums_r[0].values[1].value == 1
        assert enums_r[0].values[2].name == "Pending" and enums_r[0].values[2].value == 2

        zig_code = """
        pub const Mode = extern enum {
            Fast = 10,
            Slow = 20,
        };
        """
        backend_z = get_backend("zig")
        unit_z = backend_z.parse(zig_code, "mode.zig")
        enums_z = [d for d in unit_z.declarations if isinstance(d, Enum)]
        assert len(enums_z) == 1
        assert enums_z[0].name == "Mode"
        assert len(enums_z[0].values) == 2
        assert enums_z[0].values[0].name == "Fast" and enums_z[0].values[0].value == 10
        assert enums_z[0].values[1].name == "Slow" and enums_z[0].values[1].value == 20

    def test_zig_function_pointer_parameter(self):
        """Verify Zig export fn with function-pointer callback parameters."""
        from headerkit.ir import Function, FunctionPointer

        zig_code = """
        export fn register_callback(
            cb: ?*const fn (code: c_int) callconv(.c) void,
            user_data: ?*anyopaque,
        ) c_int {
            return 0;
        }
        """
        backend_z = get_backend("zig")
        unit = backend_z.parse(zig_code, "callback.zig")
        funcs = [d for d in unit.declarations if isinstance(d, Function)]
        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "register_callback"
        assert len(fn.parameters) == 2
        assert fn.parameters[0].name == "cb"
        assert isinstance(fn.parameters[0].type, FunctionPointer)
        assert fn.parameters[1].name == "user_data"
