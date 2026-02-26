"""Tests for the public API re-exports from cir package."""


def test_all_ir_types_importable_from_cir():
    """All public IR types should be importable directly from cir."""
    from cir import (
        CType,
        Pointer,
        Array,
        Parameter,
        FunctionPointer,
        TypeExpr,
        Field,
        EnumValue,
        Enum,
        Struct,
        Function,
        Typedef,
        Variable,
        Constant,
        Declaration,
        Header,
        SourceLocation,
        ParserBackend,
        get_backend,
        list_backends,
        is_backend_available,
    )

    all_exports = [
        CType, Pointer, Array, Parameter, FunctionPointer, TypeExpr,
        Field, EnumValue, Enum, Struct, Function, Typedef,
        Variable, Constant, Declaration, Header, SourceLocation,
        ParserBackend, get_backend, list_backends, is_backend_available,
    ]
    assert all(t is not None for t in all_exports)


def test_all_matches_module_exports():
    """__all__ should list every public name exported from cir."""
    import cir

    for name in cir.__all__:
        assert hasattr(cir, name), f"cir.__all__ lists {name!r} but it is not an attribute"


def test_type_aliases_are_unions():
    """TypeExpr and Declaration should be Union type aliases."""
    import typing

    from cir import Declaration, TypeExpr

    # These are Union types, so get_args should return their members
    type_expr_args = typing.get_args(TypeExpr)
    assert len(type_expr_args) > 0, "TypeExpr should be a Union with multiple members"

    decl_args = typing.get_args(Declaration)
    assert len(decl_args) > 0, "Declaration should be a Union with multiple members"


def test_ir_types_match_direct_import():
    """Types imported from cir should be the same objects as from cir.ir."""
    import cir
    from cir import ir

    assert cir.CType is ir.CType
    assert cir.Pointer is ir.Pointer
    assert cir.Array is ir.Array
    assert cir.Parameter is ir.Parameter
    assert cir.FunctionPointer is ir.FunctionPointer
    assert cir.Field is ir.Field
    assert cir.EnumValue is ir.EnumValue
    assert cir.Enum is ir.Enum
    assert cir.Struct is ir.Struct
    assert cir.Function is ir.Function
    assert cir.Typedef is ir.Typedef
    assert cir.Variable is ir.Variable
    assert cir.Constant is ir.Constant
    assert cir.Header is ir.Header
    assert cir.SourceLocation is ir.SourceLocation
    assert cir.ParserBackend is ir.ParserBackend


def test_backend_functions_match_direct_import():
    """Backend functions imported from cir should match cir.backends."""
    import cir
    from cir import backends

    assert cir.get_backend is backends.get_backend
    assert cir.list_backends is backends.list_backends
    assert cir.is_backend_available is backends.is_backend_available
