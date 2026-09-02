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

    assert "int math_core_calculate(int a, int b);" in output
    assert "return math::core::calculate(a, b);" in output


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

    assert "typedef struct vehicle_Engine_s vehicle_Engine_t;" in output
    assert "vehicle_Engine_t* vehicle_Engine_create(int speed);" in output
    assert "void vehicle_Engine_destroy(vehicle_Engine_t* self);" in output
    assert "void vehicle_Engine_start(vehicle_Engine_t* self);" in output
    assert "reinterpret_cast<vehicle::Engine*>(self)->start();" in output
