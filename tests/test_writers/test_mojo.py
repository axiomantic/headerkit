"""Tests for MojoWriter (Mojo FFI and C++ CShim bridge)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from headerkit.hooks import HookRegistry
from headerkit.ir import (
    Array,
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
)
from headerkit.writers import get_writer, list_writers
from headerkit.writers.mojo import MojoWriter, write_mojo


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = HookRegistry.snapshot()
    yield
    HookRegistry.restore(snapshot)


def test_mojo_writer_registration() -> None:
    """Test that mojo writer is registered and retrievable."""
    assert "mojo" in list_writers()
    writer = get_writer("mojo")
    assert isinstance(writer, MojoWriter)
    assert writer.name == "mojo"
    assert "Mojo" in writer.format_description


def test_mojo_writer_primitive_types_and_functions() -> None:
    """Test generating Mojo FFI bindings for C primitive types and free functions."""
    fn_add = Function(
        name="add_numbers",
        return_type=CType("int"),
        parameters=[Parameter("a", CType("int")), Parameter("b", CType("int"))],
    )
    fn_compute = Function(
        name="compute_stats",
        return_type=CType("double"),
        parameters=[
            Parameter("count", CType("size_t")),
            Parameter("threshold", CType("float")),
            Parameter("flag", CType("bool")),
        ],
    )
    fn_reset = Function(
        name="reset_state",
        return_type=CType("void"),
        parameters=[],
    )
    header = Header(path="math.h", declarations=[fn_add, fn_compute, fn_reset])

    output = write_mojo(header, library_name="MathLib")

    assert "from sys.ffi import DLHandle" in output
    assert "from memory import UnsafePointer" in output
    assert "struct MathLib:" in output
    assert "var handle: DLHandle" in output
    assert "fn __init__(out self, path: String) raises:" in output
    assert "self.handle = DLHandle(path)" in output
    assert "fn close(mut self):" in output
    assert "self.handle.close()" in output

    # Check function signatures and DLHandle.get_function
    assert "fn add_numbers(self, a: Int32, b: Int32) -> Int32:" in output
    assert 'var f = self.handle.get_function[fn(Int32, Int32) -> Int32]("add_numbers")' in output
    assert "return f(a, b)" in output

    assert "fn compute_stats(self, count: Int, threshold: Float32, flag: Bool) -> Float64:" in output
    assert 'var f = self.handle.get_function[fn(Int, Float32, Bool) -> Float64]("compute_stats")' in output
    assert "return f(count, threshold, flag)" in output

    assert "fn reset_state(self):" in output
    assert 'var f = self.handle.get_function[fn() -> None]("reset_state")' in output
    assert "f()" in output


def test_mojo_writer_pointers_and_arrays() -> None:
    """Test pointer mappings, multi-level pointers, and arrays in Mojo."""
    fn = Function(
        name="process_buffer",
        return_type=Pointer(CType("void")),
        parameters=[
            Parameter("data", Pointer(CType("uint8_t"))),
            Parameter("matrix", Pointer(Pointer(CType("float")))),
            Parameter("name", Pointer(CType("char"))),
            Parameter("fixed", Array(CType("int"), 4)),
        ],
    )
    header = Header(path="test.h", declarations=[fn])
    output = write_mojo(header)

    assert "from collections import InlineArray" in output
    assert (
        "fn process_buffer(self, data: UnsafePointer[UInt8], "
        "matrix: UnsafePointer[UnsafePointer[Float32]], "
        "name: UnsafePointer[Int8], "
        "fixed: InlineArray[Int32, 4]) -> UnsafePointer[NoneType]:" in output
    )
    assert (
        "var f = self.handle.get_function[fn(UnsafePointer[UInt8], "
        "UnsafePointer[UnsafePointer[Float32]], "
        "UnsafePointer[Int8], "
        'InlineArray[Int32, 4]) -> UnsafePointer[NoneType]]("process_buffer")' in output
    )


def test_mojo_writer_structs_and_enums() -> None:
    """Test generating Mojo structs and enums."""
    point_struct = Struct(
        name="Point",
        fields=[
            Field(name="x", type=CType("float")),
            Field(name="y", type=CType("float")),
        ],
    )
    status_enum = Enum(
        name="Status",
        values=[
            EnumValue(name="STATUS_OK", value=0),
            EnumValue(name="STATUS_ERROR", value=1),
        ],
    )
    opaque_struct = Struct(
        name="OpaqueContext",
        fields=[],
    )
    header = Header(path="test.h", declarations=[point_struct, status_enum, opaque_struct])
    output = write_mojo(header)

    expected_struct = textwrap.dedent("""\
        @value
        @register_passable("trivial")
        struct Point:
            var x: Float32
            var y: Float32
    """)
    assert expected_struct in output

    expected_enum = textwrap.dedent("""\
        @value
        @register_passable("trivial")
        struct Status:
            var value: Int32

            alias STATUS_OK = Status(0)
            alias STATUS_ERROR = Status(1)
    """)
    assert expected_enum in output

    expected_opaque = textwrap.dedent("""\
        @value
        @register_passable("trivial")
        struct OpaqueContext:
            var _opaque: UnsafePointer[NoneType]
    """)
    assert expected_opaque in output


def test_mojo_writer_typedefs_and_constants() -> None:
    """Test generating Mojo aliases for typedefs and constants."""
    td = Typedef(name="handle_t", underlying_type=Pointer(CType("void")))
    c1 = Constant(name="BUFFER_SIZE", type=CType("int"), value="4096")
    header = Header(path="test.h", declarations=[td, c1])
    output = write_mojo(header)

    assert "alias handle_t = UnsafePointer[NoneType]" in output
    assert "alias BUFFER_SIZE: Int32 = 4096" in output


def test_mojo_writer_cpp_cshim_bridge() -> None:
    """Test generating Mojo bindings for C++ classes using the C-ABI shim convention."""
    ctor = Function(
        name="Engine",
        return_type=CType("void"),
        parameters=[Parameter("power", CType("int"))],
    )
    method_start = Function(
        name="start",
        return_type=CType("int"),
        parameters=[Parameter("mode", CType("int"))],
    )
    method_stop = Function(
        name="stop",
        return_type=CType("void"),
        parameters=[],
    )
    cls = Struct(
        name="Engine",
        namespace="vehicle",
        is_cppclass=True,
        constructors=[ctor],
        methods=[method_start, method_stop],
    )
    header = Header(path="test.h", declarations=[cls])
    output = write_mojo(header, library_name="EngineLib")

    # 1. Opaque handle alias
    assert "alias vehicle_Engine_t = UnsafePointer[NoneType]" in output

    # 2. C-ABI shim bindings on Library struct
    assert "struct EngineLib:" in output
    assert "fn vehicle_Engine_create(self, power: Int32) -> UnsafePointer[NoneType]:" in output
    assert 'var f = self.handle.get_function[fn(Int32) -> UnsafePointer[NoneType]]("vehicle_Engine_create")' in output
    assert "fn vehicle_Engine_destroy(self, self_ptr: UnsafePointer[NoneType]):" in output
    assert 'var f = self.handle.get_function[fn(UnsafePointer[NoneType]) -> None]("vehicle_Engine_destroy")' in output
    assert "fn vehicle_Engine_start(self, self_ptr: UnsafePointer[NoneType], mode: Int32) -> Int32:" in output
    assert (
        'var f = self.handle.get_function[fn(UnsafePointer[NoneType], Int32) -> Int32]("vehicle_Engine_start")'
        in output
    )
    assert "fn vehicle_Engine_stop(self, self_ptr: UnsafePointer[NoneType]):" in output
    assert 'var f = self.handle.get_function[fn(UnsafePointer[NoneType]) -> None]("vehicle_Engine_stop")' in output

    # 3. High-level Mojo struct wrapper for vehicle::Engine
    assert "struct vehicle_Engine:" in output
    assert "var handle: UnsafePointer[NoneType]" in output
    assert "fn __init__(out self, handle: UnsafePointer[NoneType]):" in output
    assert "self.handle = handle" in output
    assert "@staticmethod" in output
    assert "fn create(lib: EngineLib, power: Int32) -> Self:" in output
    assert "return Self(lib.vehicle_Engine_create(power))" in output
    assert "fn destroy(mut self, lib: EngineLib):" in output
    assert "lib.vehicle_Engine_destroy(self.handle)" in output
    assert "fn start(self, lib: EngineLib, mode: Int32) -> Int32:" in output
    assert "return lib.vehicle_Engine_start(self.handle, mode)" in output
    assert "fn stop(self, lib: EngineLib):" in output
    assert "lib.vehicle_Engine_stop(self.handle)" in output


def test_mojo_writer_keyword_escaping() -> None:
    """Test that Mojo reserved keywords used as identifiers are properly escaped."""
    fn = Function(
        name="fn",
        return_type=CType("int"),
        parameters=[Parameter("var", CType("int")), Parameter("inout", CType("float"))],
    )
    st = Struct(
        name="struct",
        fields=[Field(name="let", type=CType("int"))],
    )
    header = Header(path="test.h", declarations=[fn, st])
    output = write_mojo(header)

    # Identifier 'fn' as function name and 'var', 'inout' as parameter names
    assert "fn fn_(self, var_: Int32, inout_: Float32) -> Int32:" in output
    assert 'var f = self.handle.get_function[fn(Int32, Float32) -> Int32]("fn")' in output
    assert "struct struct_:" in output
    assert "var let_: Int32" in output


def test_mojo_writer_hook_pipeline() -> None:
    """Test Mojo writer dispatch through the unified hook engine."""
    from headerkit.hooks import HookDispatcher, PipelineContext

    fn = Function(name="ping", return_type=CType("void"), parameters=[])
    unit = Header(path="service.h", declarations=[fn])

    ctx = PipelineContext(writer="mojo", options={"library_name": "ServiceLib"})
    output = HookDispatcher().first_result("write_output", unit=unit, context=ctx)
    assert output is not None
    assert "struct ServiceLib:" in output
    assert "fn ping(self):" in output


@pytest.mark.treesitter
def test_mojo_writer_tree_sitter_end_to_end(tmp_path: Path) -> None:
    """Test parsing a C header with TreeSitter and emitting Mojo bindings."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_c")
    from headerkit.backends.treesitter import TreeSitterBackend

    header_file = tmp_path / "simple_math.h"
    header_file.write_text(
        textwrap.dedent("""\
            #ifndef SIMPLE_MATH_H
            #define SIMPLE_MATH_H

            typedef struct {
                float x;
                float y;
            } Vec2;

            int multiply(int a, int b);
            float vec2_dot(Vec2 a, Vec2 b);

            #endif
        """)
    )

    backend = TreeSitterBackend()
    unit = backend.parse(header_file.read_text(), str(header_file))
    output = write_mojo(unit, library_name="SimpleMath")

    assert "struct SimpleMath:" in output
    assert "fn multiply(self, a: Int32, b: Int32) -> Int32:" in output
    assert "struct Vec2:" in output
    assert "var x: Float32" in output
    assert "var y: Float32" in output


def test_mojo_writer_variadic_function() -> None:
    """Verify variadic C function emits warning comment about fixed parameters."""
    fn = Function(
        name="custom_printf",
        return_type=CType("int"),
        parameters=[Parameter("fmt", Pointer(CType("char", qualifiers=["const"])))],
        is_variadic=True,
    )
    unit = Header(path="printf.h", declarations=[fn])
    output = write_mojo(unit, library_name="PrintLib")

    assert "Warning: 'custom_printf' is a C variadic function" in output
    assert "fn custom_printf(self, fmt: UnsafePointer[Int8]) -> Int32:" in output


def test_mojo_writer_type_qualifiers_and_fn_pointers() -> None:
    """Verify CType qualifiers and typed FunctionPointer emission in Mojo."""
    cb = FunctionPointer(
        return_type=CType("void"),
        parameters=[Parameter("code", CType("int", qualifiers=["unsigned"]))],
    )
    fn = Function(
        name="register_handler",
        return_type=CType("int", qualifiers=["unsigned"]),
        parameters=[Parameter("callback", cb)],
    )
    unit = Header(path="handler.h", declarations=[fn])
    output = write_mojo(unit, library_name="HandlerLib")

    assert "fn register_handler(self, callback: fn(UInt32) -> None) -> UInt32:" in output
    assert 'var f = self.handle.get_function[fn(fn(UInt32) -> None) -> UInt32]("register_handler")' in output
