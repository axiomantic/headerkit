"""Tests for IRTransformer visitor base class."""

from __future__ import annotations

from dataclasses import replace

import pytest

from headerkit.ir import CType, Function, SourceUnit
from headerkit.transforms.base import IRTransformer


class ReplaceFunctionTransformer(IRTransformer):
    """Renames functions named 'old_func' to 'new_func'."""

    def visit_Function(self, node: Function, context: object | None = None) -> Function:
        if node.name == "old_func":
            return replace(node, name="new_func")
        return node


class DeleteFunctionTransformer(IRTransformer):
    """Deletes functions named 'delete_me'."""

    def visit_Function(self, node: Function, context: object | None = None) -> Function | None:
        if node.name == "delete_me":
            return None
        return node


class SpliceFunctionTransformer(IRTransformer):
    """Splices one function into two."""

    def visit_Function(self, node: Function, context: object | None = None) -> list[Function] | Function:
        if node.name == "splice_me":
            return [
                replace(node, name="spliced_1"),
                replace(node, name="spliced_2"),
            ]
        return node


def test_ir_transformer_node_replacement() -> None:
    fn1 = Function(name="old_func", return_type=CType("void"))
    fn2 = Function(name="keep_func", return_type=CType("int"))
    unit = SourceUnit(path="test.h", declarations=[fn1, fn2])

    transformer = ReplaceFunctionTransformer()
    transformed = transformer.visit(unit)

    assert isinstance(transformed, SourceUnit)
    assert len(transformed.declarations) == 2
    assert transformed.declarations[0].name == "new_func"
    assert transformed.declarations[1].name == "keep_func"
    # Verify immutability of original unit
    assert unit.declarations[0].name == "old_func"


def test_ir_transformer_node_deletion() -> None:
    fn1 = Function(name="delete_me", return_type=CType("void"))
    fn2 = Function(name="stay", return_type=CType("int"))
    unit = SourceUnit(path="test.h", declarations=[fn1, fn2])

    transformer = DeleteFunctionTransformer()
    transformed = transformer.visit(unit)

    assert isinstance(transformed, SourceUnit)
    assert len(transformed.declarations) == 1
    assert transformed.declarations[0].name == "stay"


def test_ir_transformer_node_splicing() -> None:
    fn = Function(name="splice_me", return_type=CType("void"))
    unit = SourceUnit(path="test.h", declarations=[fn])

    transformer = SpliceFunctionTransformer()
    transformed = transformer.visit(unit)

    assert isinstance(transformed, SourceUnit)
    assert len(transformed.declarations) == 2
    assert transformed.declarations[0].name == "spliced_1"
    assert transformed.declarations[1].name == "spliced_2"


def test_ir_transformer_scalar_field_splice_error() -> None:
    class BadScalarTransformer(IRTransformer):
        def visit_CType(self, node: CType, context: object | None = None) -> list[CType]:
            return [CType("int"), CType("float")]

    fn = Function(name="foo", return_type=CType("int"))
    transformer = BadScalarTransformer()
    with pytest.raises(TypeError, match="Cannot replace scalar field 'return_type'"):
        transformer.visit(fn)
