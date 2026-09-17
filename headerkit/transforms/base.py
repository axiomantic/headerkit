"""Base visitor for immutable IR transformations."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from typing import Any, TypeVar

from headerkit.hooks import HookDispatcher, HookRegistry, PipelineContext

NodeT = TypeVar("NodeT")


class IRTransformer:
    """Recursively walks an IR AST, transforming nodes via visitor methods or hooks.

    Transformation Semantics:
    - Return a Node: Replaces the current node with the returned node.
    - Return None: Deletes the current node from list/tuple containers.
    - Return list[Node]: Splices multiple nodes in place of the current node in collections.
    """

    def visit(self, node: Any, context: PipelineContext | None = None) -> Any:
        """Visit a node, checking for hook overrides before dispatching to visit_<type>."""
        if node is None:
            return None

        node_type = node.__class__.__name__
        hook_point = f"transform:{node_type}"
        has_point_fn = getattr(HookRegistry, "has_point", None)
        if context is not None and (has_point_fn(hook_point) if has_point_fn is not None else False):
            dispatcher = HookDispatcher()
            transformed = dispatcher.waterfall(hook_point, node, context=context)
            if transformed is None:
                return None
            node = transformed

        method_name = f"visit_{node_type}"
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node, context=context)

    def generic_visit(self, node: NodeT, context: PipelineContext | None = None) -> NodeT:
        """Default visitor traversing all dataclass fields."""
        if not dataclasses.is_dataclass(node) or isinstance(node, type):
            return node

        changes: dict[str, Any] = {}
        for field_def in dataclasses.fields(node):
            val = getattr(node, field_def.name)
            if isinstance(val, list):
                new_list: list[Any] = []
                for item in val:
                    res = self.visit(item, context=context)
                    if res is None:
                        continue
                    elif isinstance(res, list):
                        new_list.extend(res)
                    else:
                        new_list.append(res)
                changes[field_def.name] = new_list
            elif isinstance(val, tuple):
                new_tuple_list: list[Any] = []
                for item in val:
                    res = self.visit(item, context=context)
                    if res is None:
                        continue
                    elif isinstance(res, list):
                        new_tuple_list.extend(res)
                    else:
                        new_tuple_list.append(res)
                changes[field_def.name] = tuple(new_tuple_list)
            elif dataclasses.is_dataclass(val):
                res = self.visit(val, context=context)
                if isinstance(res, list):
                    raise TypeError(
                        f"Cannot replace scalar field '{field_def.name}' of "
                        f"{node.__class__.__name__} with a list of nodes."
                    )
                changes[field_def.name] = res

        return replace(node, **changes)
