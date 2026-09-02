"""Tests for CShimWriter (C-ABI shim generation)."""

from __future__ import annotations

import textwrap

from headerkit.ir import (
    CType,
    Function,
    Header,
    Parameter,
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
