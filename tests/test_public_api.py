"""Tests for the public API re-exports from clangir package."""


def test_all_matches_module_exports():
    """__all__ should list every public name exported from clangir."""
    import clangir

    for name in clangir.__all__:
        assert hasattr(clangir, name), f"clangir.__all__ lists {name!r} but it is not an attribute"

    # Reverse check: every public non-module attribute should be listed in __all__
    import types

    public_attrs = {
        name
        for name in dir(clangir)
        if not name.startswith("_") and not isinstance(getattr(clangir, name), types.ModuleType)
    }
    all_set = set(clangir.__all__)
    missing = public_attrs - all_set
    assert missing == set(), f"Public attributes missing from __all__: {missing}"


def test_type_aliases_are_unions():
    """TypeExpr and Declaration should be Union type aliases."""
    import typing

    from clangir import Declaration, TypeExpr

    # These are Union types, so get_args should return their members
    type_expr_args = typing.get_args(TypeExpr)
    assert len(type_expr_args) > 0, "TypeExpr should be a Union with multiple members"

    decl_args = typing.get_args(Declaration)
    assert len(decl_args) > 0, "Declaration should be a Union with multiple members"


def test_ir_types_match_direct_import():
    """Types imported from clangir should be the same objects as from clangir.ir."""
    import clangir
    from clangir import ir

    assert clangir.CType is ir.CType
    assert clangir.Pointer is ir.Pointer
    assert clangir.Array is ir.Array
    assert clangir.Parameter is ir.Parameter
    assert clangir.FunctionPointer is ir.FunctionPointer
    assert clangir.Field is ir.Field
    assert clangir.EnumValue is ir.EnumValue
    assert clangir.Enum is ir.Enum
    assert clangir.Struct is ir.Struct
    assert clangir.Function is ir.Function
    assert clangir.Typedef is ir.Typedef
    assert clangir.Variable is ir.Variable
    assert clangir.Constant is ir.Constant
    assert clangir.Header is ir.Header
    assert clangir.SourceLocation is ir.SourceLocation
    assert clangir.ParserBackend is ir.ParserBackend


def test_backend_functions_match_direct_import():
    """Backend functions imported from clangir should match clangir.backends."""
    import clangir
    from clangir import backends

    assert clangir.get_backend is backends.get_backend
    assert clangir.list_backends is backends.list_backends
    assert clangir.is_backend_available is backends.is_backend_available


def test_writer_symbols_in_all():
    """All writer symbols should be listed in clangir.__all__."""
    import clangir

    writer_names = [
        "WriterBackend",
        "get_default_writer",
        "get_writer",
        "get_writer_info",
        "is_writer_available",
        "list_writers",
        "register_writer",
    ]
    all_set = set(clangir.__all__)
    for name in writer_names:
        assert name in all_set, f"{name!r} missing from clangir.__all__"


def test_writer_functions_match_direct_import():
    """Writer functions imported from clangir should match clangir.writers."""
    import clangir
    from clangir import writers

    assert clangir.WriterBackend is writers.WriterBackend
    assert clangir.get_default_writer is writers.get_default_writer
    assert clangir.get_writer is writers.get_writer
    assert clangir.get_writer_info is writers.get_writer_info
    assert clangir.is_writer_available is writers.is_writer_available
    assert clangir.list_writers is writers.list_writers
    assert clangir.register_writer is writers.register_writer
