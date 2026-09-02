"""Tests for CShimWriter (C-ABI shim generation)."""

from __future__ import annotations

import textwrap

from headerkit.ir import (
    Array,
    CType,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    Struct,
)
from headerkit.writers import get_writer
from headerkit.writers.cshim import CShimWriter


def test_cshim_writer_registration() -> None:
    """Test that cshim writer is registered and retrievable."""
    writer = get_writer("cshim")
    assert isinstance(writer, CShimWriter)
    assert writer.name == "cshim"
    assert writer.format_description == "C-ABI shim wrapper generator (extern C)"


def test_cshim_free_functions() -> None:
    """Test generating C-ABI wrapper for free functions."""
    fn = Function(
        name="calculate",
        return_type=CType("int"),
        parameters=[Parameter("a", CType("int")), Parameter("b", CType("int"))],
        namespace="math::core",
    )
    header = Header(path="test.h", declarations=[fn])
    writer = CShimWriter()
    output = writer.write(header)

    expected = textwrap.dedent("""\
        // Auto-generated C-ABI shim by HeaderKit
        #pragma once

        #ifdef __cplusplus
        extern "C" {
        #endif

        int math_core_calculate(int a, int b);

        #ifdef __cplusplus
        }
        #endif

        #ifdef __cplusplus
        #include <new>
        #include "test.h"

        int math_core_calculate(int a, int b) {
            return math::core::calculate(a, b);
        }

        #endif
    """)
    assert output == expected


def test_cshim_class_methods_and_lifecycle() -> None:
    """Test generating opaque handles, constructors, destructors, and methods."""
    ctor = Function(
        name="Engine",
        return_type=CType("void"),
        parameters=[Parameter("speed", CType("int"))],
    )
    method = Function(
        name="start",
        return_type=CType("void"),
        parameters=[],
    )
    cls = Struct(
        name="Engine",
        namespace="vehicle",
        is_cppclass=True,
        constructors=[ctor],
        methods=[method],
    )
    header = Header(path="test.h", declarations=[cls])
    writer = CShimWriter()
    output = writer.write(header)

    expected = textwrap.dedent("""\
        // Auto-generated C-ABI shim by HeaderKit
        #pragma once

        #ifdef __cplusplus
        extern "C" {
        #endif

        /* Opaque Handle Types */
        typedef struct vehicle_Engine_s vehicle_Engine_t;

        vehicle_Engine_t* vehicle_Engine_create(int speed);
        void vehicle_Engine_destroy(vehicle_Engine_t* self);
        void vehicle_Engine_start(vehicle_Engine_t* self);

        #ifdef __cplusplus
        }
        #endif

        #ifdef __cplusplus
        #include <new>
        #include "test.h"

        vehicle_Engine_t* vehicle_Engine_create(int speed) {
            return reinterpret_cast<vehicle_Engine_t*>(new (std::nothrow) vehicle::Engine(speed));
        }

        void vehicle_Engine_destroy(vehicle_Engine_t* self) {
            if (self) {
                delete reinterpret_cast<vehicle::Engine*>(self);
            }
        }

        void vehicle_Engine_start(vehicle_Engine_t* self) {
            reinterpret_cast<vehicle::Engine*>(self)->start();
        }

        #endif
    """)
    assert output == expected


def test_cshim_static_methods_unnamed_params_and_operators() -> None:
    """Test generating wrappers for static methods, private filtering, unnamed params, and operators."""
    ctor0 = Function(name="Device", return_type=CType("void"), parameters=[])
    ctor1 = Function(
        name="Device",
        return_type=CType("void"),
        parameters=[Parameter(name=None, type=CType("int")), Parameter(name=None, type=CType("double"))],
    )
    m_static = Function(name="getDefaultId", return_type=CType("int"), parameters=[], is_static=True)
    m_private = Function(name="secretInternal", return_type=CType("void"), parameters=[], access="private")
    m_op = Function(
        name="operator+=",
        return_type=CType("void"),
        parameters=[Parameter(name="delta", type=CType("int"))],
    )
    cls = Struct(
        name="Device",
        is_cppclass=True,
        constructors=[ctor0, ctor1],
        methods=[m_static, m_private, m_op],
    )
    header = Header(path="test.h", declarations=[cls])
    writer = CShimWriter()
    output = writer.write(header)

    expected = textwrap.dedent("""\
        // Auto-generated C-ABI shim by HeaderKit
        #pragma once

        #ifdef __cplusplus
        extern "C" {
        #endif

        /* Opaque Handle Types */
        typedef struct Device_s Device_t;

        Device_t* Device_create(void);
        Device_t* Device_create_1(int arg0, double arg1);
        void Device_destroy(Device_t* self);
        int Device_getDefaultId(void);
        void Device_add_assign(Device_t* self, int delta);

        #ifdef __cplusplus
        }
        #endif

        #ifdef __cplusplus
        #include <new>
        #include "test.h"

        Device_t* Device_create(void) {
            return reinterpret_cast<Device_t*>(new (std::nothrow) Device());
        }

        Device_t* Device_create_1(int arg0, double arg1) {
            return reinterpret_cast<Device_t*>(new (std::nothrow) Device(arg0, arg1));
        }

        void Device_destroy(Device_t* self) {
            if (self) {
                delete reinterpret_cast<Device*>(self);
            }
        }

        int Device_getDefaultId(void) {
            return Device::getDefaultId();
        }

        void Device_add_assign(Device_t* self, int delta) {
            reinterpret_cast<Device*>(self)->operator+=(delta);
        }

        #endif
    """)
    assert output == expected


def test_cshim_complex_types_and_all_operators() -> None:
    """Test pointer, reference, array, function pointer types, and operator* / operator& sanitization."""
    callback_type = FunctionPointer(
        return_type=CType("void"),
        parameters=[Parameter(name="code", type=CType("int"))],
    )
    fn_complex = Function(
        name="process_buffer",
        return_type=Pointer(CType("char")),
        parameters=[
            Parameter(name="buf", type=Array(element_type=CType("int"), size=16)),
            Parameter(name="ref_val", type=Reference(CType("double"))),
            Parameter(name="cb", type=callback_type),
        ],
        namespace="io::stream",
    )
    m_mul = Function(name="operator*", return_type=CType("int"), parameters=[Parameter("rhs", CType("int"))])
    m_band = Function(name="operator&", return_type=CType("int"), parameters=[Parameter("mask", CType("int"))])
    cls = Struct(
        name="Number",
        is_cppclass=True,
        methods=[m_mul, m_band],
    )
    header = Header(path="test.h", declarations=[fn_complex, cls])
    writer = CShimWriter()
    output = writer.write(header)

    expected = textwrap.dedent("""\
        // Auto-generated C-ABI shim by HeaderKit
        #pragma once

        #ifdef __cplusplus
        extern "C" {
        #endif

        /* Opaque Handle Types */
        typedef struct Number_s Number_t;

        char* io_stream_process_buffer(int buf[16], double* ref_val, void (*cb)(int code));
        void Number_destroy(Number_t* self);
        int Number_mul(Number_t* self, int rhs);
        int Number_band(Number_t* self, int mask);

        #ifdef __cplusplus
        }
        #endif

        #ifdef __cplusplus
        #include <new>
        #include "test.h"

        char* io_stream_process_buffer(int buf[16], double* ref_val, void (*cb)(int code)) {
            return io::stream::process_buffer(buf, ref_val, cb);
        }

        void Number_destroy(Number_t* self) {
            if (self) {
                delete reinterpret_cast<Number*>(self);
            }
        }

        int Number_mul(Number_t* self, int rhs) {
            return reinterpret_cast<Number*>(self)->operator*(rhs);
        }

        int Number_band(Number_t* self, int mask) {
            return reinterpret_cast<Number*>(self)->operator&(mask);
        }

        #endif
    """)
    assert output == expected


def test_cshim_spaceship_operator_and_namespaced_types() -> None:
    """Test C++20 spaceship operator <=> sanitization and namespaced parameter/return types."""
    m_cmp = Function(
        name="operator<=>",
        return_type=CType("int"),
        parameters=[Parameter("other", CType("Point"))],
    )
    m_le = Function(
        name="operator<=",
        return_type=CType("bool"),
        parameters=[Parameter("other", CType("Point"))],
    )
    cls = Struct(
        name="Point",
        namespace="geometry::d2",
        is_cppclass=True,
        methods=[m_cmp, m_le],
    )
    header = Header(path="geometry.h", declarations=[cls])
    writer = CShimWriter()
    output = writer.write(header)

    expected = textwrap.dedent("""\
        // Auto-generated C-ABI shim by HeaderKit
        #pragma once

        #ifdef __cplusplus
        extern "C" {
        #endif

        /* Opaque Handle Types */
        typedef struct geometry_d2_Point_s geometry_d2_Point_t;

        void geometry_d2_Point_destroy(geometry_d2_Point_t* self);
        int geometry_d2_Point_spaceship(geometry_d2_Point_t* self, Point other);
        bool geometry_d2_Point_le(geometry_d2_Point_t* self, Point other);

        #ifdef __cplusplus
        }
        #endif

        #ifdef __cplusplus
        #include <new>
        #include "geometry.h"

        void geometry_d2_Point_destroy(geometry_d2_Point_t* self) {
            if (self) {
                delete reinterpret_cast<geometry::d2::Point*>(self);
            }
        }

        int geometry_d2_Point_spaceship(geometry_d2_Point_t* self, Point other) {
            return reinterpret_cast<geometry::d2::Point*>(self)->operator<=>(other);
        }

        bool geometry_d2_Point_le(geometry_d2_Point_t* self, Point other) {
            return reinterpret_cast<geometry::d2::Point*>(self)->operator<=(other);
        }

        #endif
    """)
    assert output == expected
