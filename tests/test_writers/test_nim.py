"""Tests for the Nim writer."""

from __future__ import annotations

from headerkit.ir import (
    BaseSpecifier,
    Constant,
    CType,
    Enum,
    EnumValue,
    Field,
    Function,
    Header,
    Parameter,
    Pointer,
    Reference,
    Struct,
)
from headerkit.writers.nim import NimWriter, write_nim


class TestNimWriter:
    def test_primitive_function_and_escape(self) -> None:
        """Test basic C function with types and keyword escaping."""
        h = Header(
            path="test.h",
            declarations=[
                Function(
                    name="type",
                    return_type=CType("int"),
                    parameters=[Parameter(name="var", type=CType("int"))],
                )
            ],
        )
        writer = NimWriter(header_path="test.h")
        out = writer.write(h)

        assert 'proc `type`*(`var`: cint): cint {.importc: "type", header: "test.h", cdecl.}' in out

    def test_struct_with_fields(self) -> None:
        """Test C struct with primitive and pointer fields."""
        h = Header(
            path="test.h",
            declarations=[
                Struct(
                    name="Point",
                    fields=[
                        Field("x", CType("int")),
                        Field("y", CType("int")),
                        Field("label", Pointer(CType("char", ["const"]))),
                    ],
                )
            ],
        )
        out = write_nim(h, header_path="test.h")
        assert 'Point* = object {.importc: "Point", header: "test.h", bycopy.}' in out
        assert "x*: cint" in out
        assert "label*: cstring" in out

    def test_cpp_class_with_templates_and_methods(self) -> None:
        """Test C++ class with template parameters, methods, inheritance, and constructors."""
        h = Header(
            path="test.hpp",
            declarations=[
                Struct(
                    name="Container",
                    template_params=["T"],
                    is_cppclass=True,
                    bases=[BaseSpecifier(name="Base")],
                    fields=[Field("value", CType("T"))],
                    constructors=[
                        Function(
                            name="Container",
                            return_type=CType("void"),
                            parameters=[Parameter("initVal", CType("T"), default_value="0")],
                        )
                    ],
                    methods=[
                        Function(
                            name="getVal",
                            return_type=CType("T"),
                            is_const=True,
                        ),
                        Function(
                            name="setVal",
                            return_type=CType("void"),
                            parameters=[Parameter("v", Reference(CType("T")))],
                        ),
                    ],
                )
            ],
        )
        out = write_nim(h, header_path="test.hpp")
        assert 'Container[T]* = object of Base {.importcpp: "Container", header: "test.hpp", bycopy.}' in out
        assert "value*: T" in out
        assert (
            'proc constructContainer*(initVal: T = 0): Container {.importcpp: "Container(@)", header: "test.hpp", constructor.}'
            in out
        )
        assert 'proc getVal*(this: Container): T {.importcpp: "#.getVal(@)", header: "test.hpp".}' in out
        assert 'proc setVal*(this: var Container, v: var T) {.importcpp: "#.setVal(@)", header: "test.hpp".}' in out

    def test_enum_and_constants(self) -> None:
        """Test enum and constant generation."""
        h = Header(
            path="test.h",
            declarations=[
                Enum(
                    name="Status",
                    values=[EnumValue("OK", 0), EnumValue("ERR", 1)],
                ),
                Constant(name="MAX_COUNT", value=100),
            ],
        )
        out = write_nim(h, header_path="test.h")
        assert 'Status* {.size: sizeof(cint), importc: "Status", header: "test.h".} = enum' in out
        assert "OK = 0" in out
        assert "MAX_COUNT* = 100" in out

    def test_cpp_advanced_features(self) -> None:
        """Test C++ smart pointers, move semantics, operators, and container iterators."""
        h = Header(
            path="test.hpp",
            declarations=[
                Struct(
                    name="MyVector",
                    fields=[
                        Field("ptr", CType("std::shared_ptr<int>")),
                    ],
                    methods=[
                        Function(
                            name="operator[]",
                            return_type=Reference(CType("int")),
                            parameters=[Parameter("index", CType("size_t"))],
                        ),
                        Function(
                            name="push",
                            return_type=CType("void"),
                            parameters=[Parameter("val", Reference(CType("int"), is_rvalue=True))],
                        ),
                        Function(
                            name="begin",
                            return_type=Pointer(CType("int")),
                        ),
                        Function(
                            name="end",
                            return_type=Pointer(CType("int")),
                        ),
                    ],
                )
            ],
        )
        out = write_nim(h, header_path="test.hpp")
        assert "`ptr`*: SharedPtr[cint]" in out
        assert (
            'proc `[]`*(this: var MyVector, index: csize_t): var cint {.importcpp: "#[@]", header: "test.hpp".}' in out
        )
        assert 'proc push*(this: var MyVector, val: sink cint) {.importcpp: "#.push(@)", header: "test.hpp".}' in out
        assert "iterator items*(this: MyVector): auto = {.inline.}" in out
