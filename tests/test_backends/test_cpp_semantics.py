"""Tests for C++ class semantics in IR and libclang backend."""

from __future__ import annotations

import json
import textwrap

import pytest

from headerkit.backends import get_backend
from headerkit.backends.libclang import is_system_libclang_available
from headerkit.ir import BaseSpecifier, CType, Struct
from headerkit.writers.json import JsonWriter

libclang = pytest.mark.libclang


@pytest.fixture(autouse=True)
def _skip_if_no_libclang(request: pytest.FixtureRequest) -> None:
    marker = request.node.get_closest_marker("libclang")
    if marker is not None and not is_system_libclang_available():
        pytest.skip("System libclang not available")


@pytest.mark.allow("subprocess")
@libclang
class TestCppClassSemantics:
    """Test parsing C++ class semantics into IR and JSON."""

    def test_probe_header_cpp_class_semantics(self) -> None:
        """Parse probe.hpp with juce::Base and juce::Model and verify full C++ IR semantics."""
        header_code = textwrap.dedent("""\
            namespace juce {
            class Base {
            public:
                virtual ~Base() = default;
            };

            class Model : public Base {
            public:
                Model(int seed);
                virtual int getNumRows() = 0;
                virtual void paintRow(int row) = 0;
                int publicMethod(const char* s) const;
                static int staticMethod();
                operator int() const;
                bool operator==(const Model& o) const;
            private:
                int privateMethod();
                int privateField;
            };
            }
        """)

        backend = get_backend("libclang")
        h = backend.parse(header_code, "probe.hpp", extra_args=["-x", "c++", "-std=c++17"])

        # Find Base and Model declarations
        structs = [d for d in h.declarations if isinstance(d, Struct)]
        base = next(s for s in structs if s.name == "Base")
        model = next(s for s in structs if s.name == "Model")

        # Verify Base
        assert base.is_cppclass is True
        assert base.namespace == "juce"
        assert base.destructor is not None
        assert base.destructor.name == "~Base"
        assert base.destructor.is_virtual is True
        assert base.destructor.is_defaulted is True
        assert base.destructor.access == "public"

        # Verify Model class level
        assert model.is_cppclass is True
        assert model.namespace == "juce"
        assert model.is_abstract is True
        assert len(model.bases) == 1
        assert model.bases[0] == BaseSpecifier(name="Base", access="public", is_virtual=False)

        # Verify Model constructors
        assert len(model.constructors) == 1
        ctor = model.constructors[0]
        assert ctor.name == "Model"
        assert ctor.access == "public"
        assert len(ctor.parameters) == 1
        assert ctor.parameters[0].name == "seed"
        assert ctor.parameters[0].type == CType("int")

        # Verify Model conversions
        assert len(model.conversions) == 1
        conv = model.conversions[0]
        assert conv.name == "operator int"
        assert conv.return_type == CType("int")
        assert conv.is_const is True
        assert conv.access == "public"

        # Verify Model methods
        methods_by_name = {m.name: m for m in model.methods}

        get_num_rows = methods_by_name["getNumRows"]
        assert get_num_rows.is_virtual is True
        assert get_num_rows.is_pure_virtual is True
        assert get_num_rows.access == "public"
        assert get_num_rows.return_type == CType("int")

        paint_row = methods_by_name["paintRow"]
        assert paint_row.is_virtual is True
        assert paint_row.is_pure_virtual is True
        assert paint_row.access == "public"
        assert paint_row.return_type == CType("void")

        public_method = methods_by_name["publicMethod"]
        assert public_method.is_const is True
        assert public_method.is_virtual is False
        assert public_method.access == "public"
        assert public_method.return_type == CType("int")

        static_method = methods_by_name["staticMethod"]
        assert static_method.is_static is True
        assert static_method.access == "public"
        assert static_method.return_type == CType("int")

        op_eq = methods_by_name["operator=="]
        assert op_eq.is_const is True
        assert op_eq.access == "public"
        assert op_eq.return_type == CType("bool")

        private_method = methods_by_name["privateMethod"]
        assert private_method.access == "private"
        assert private_method.return_type == CType("int")

        # Verify Model fields
        fields_by_name = {f.name: f for f in model.fields}
        private_field = fields_by_name["privateField"]
        assert private_field.access == "private"
        assert private_field.type == CType("int")
        assert private_field.is_static is False

        # Verify JSON writer output
        json_writer = JsonWriter()
        json_output = json_writer.write(h)

        data = json.loads(json_output)
        model_json = next(d for d in data["declarations"] if d.get("name") == "Model")
        assert model_json["is_abstract"] is True
        assert model_json["bases"] == [{"name": "Base", "access": "public"}]
        assert len(model_json["constructors"]) == 1
        assert model_json["constructors"][0]["name"] == "Model"
        assert len(model_json["conversions"]) == 1
        assert model_json["conversions"][0]["name"] == "operator int"

    def test_static_fields_and_virtual_bases(self) -> None:
        """Test virtual base inheritance and static field extraction."""
        code = textwrap.dedent("""\
            class VBase {
            public:
                int x;
            };

            class Derived : public virtual VBase {
            public:
                static int instanceCount;
            protected:
                int protectedValue;
            };
        """)

        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])
        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        derived = structs["Derived"]

        assert len(derived.bases) == 1
        assert derived.bases[0] == BaseSpecifier(name="VBase", access="public", is_virtual=True)

        fields_by_name = {f.name: f for f in derived.fields}
        assert fields_by_name["instanceCount"].is_static is True
        assert fields_by_name["instanceCount"].access == "public"
        assert fields_by_name["protectedValue"].is_static is False
        assert fields_by_name["protectedValue"].access == "protected"

    def test_notes_fallback_when_unrepresentable_member(self) -> None:
        """Negative test ensuring note is recorded if a member is skipped."""
        code = textwrap.dedent("""\
            class Complex {
            public:
                int validField;
            };
        """)
        backend = get_backend("libclang")
        h = backend.parse(code, "test.hpp", extra_args=["-x", "c++", "-std=c++17"])
        structs = {s.name: s for s in h.declarations if isinstance(s, Struct)}
        assert "Complex" in structs
        assert len(structs["Complex"].fields) == 1
