"""Tree-sitter based parser backend for C headers."""

from __future__ import annotations

import logging
from typing import Any

from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    Header,
    Parameter,
    ParserBackend,
    Pointer,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
)

logger = logging.getLogger("headerkit.backends.treesitter")

_HAS_TREESITTER: bool = False
try:
    import tree_sitter_c as tsc
    from tree_sitter import Language, Node, Parser

    _HAS_TREESITTER = True
except ImportError:
    pass


def _node_text(node: Any) -> str:
    if node is None:
        return ""
    raw = getattr(node, "text", None)
    if raw is None:
        return ""
    if isinstance(raw, bytes | bytearray):
        return raw.decode("utf-8")
    return str(raw)


class TreeSitterBackend:
    """Parser backend using tree-sitter-c."""

    supported_languages: frozenset[str] = frozenset({"c"})
    supported_classifications: frozenset[str] = frozenset({"header", "source"})

    @property
    def name(self) -> str:
        return "tree-sitter"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return False

    def is_available(self) -> bool:
        return _HAS_TREESITTER

    def parse(
        self,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
        *,
        use_default_includes: bool = True,
        recursive_includes: bool = True,
        max_depth: int = 10,
        project_prefixes: tuple[str, ...] | None = None,
    ) -> Header:
        if not _HAS_TREESITTER:
            msg = "tree-sitter or tree-sitter-c is not installed. Install with: pip install 'headerkit[treesitter]'"
            raise RuntimeError(msg)

        language = Language(tsc.language())
        parser = Parser(language)
        tree = parser.parse(code.encode("utf-8"))

        declarations: list[Declaration] = []
        for child in tree.root_node.children:
            decls = self._convert_top_level(child, filename)
            declarations.extend(decls)

        return Header(path=filename, declarations=declarations)

    def _convert_top_level(self, node: Node, filename: str) -> list[Declaration]:
        if node.type in ("preproc_ifdef", "preproc_if", "preproc_elif", "preproc_else"):
            results: list[Declaration] = []
            for child in node.children:
                results.extend(self._convert_top_level(child, filename))
            return results

        if node.type in ("linkage_specification", "declaration_list"):
            results = []
            for child in node.children:
                results.extend(self._convert_top_level(child, filename))
            return results

        if node.type == "declaration":
            return self._convert_declaration(node, filename)
        if node.type == "type_definition":
            return self._convert_type_definition(node, filename)
        if node.type == "struct_specifier":
            st = self._convert_struct(node, filename)
            return [st] if st else []
        if node.type == "enum_specifier":
            en = self._convert_enum(node, filename)
            return [en] if en else []
        return []

    def _convert_type_definition(self, node: Node, filename: str) -> list[Declaration]:
        struct_node = node.child_by_field_name("type")
        declarator_node = node.child_by_field_name("declarator")

        if struct_node and struct_node.type == "struct_specifier":
            st = self._convert_struct(struct_node, filename)
            if st:
                if declarator_node:
                    st.name = _node_text(declarator_node)
                st.is_typedef = True
                return [st]

        if struct_node and struct_node.type == "enum_specifier":
            en = self._convert_enum(struct_node, filename)
            if en:
                if declarator_node:
                    en.name = _node_text(declarator_node)
                en.is_typedef = True
                return [en]

        if struct_node and declarator_node:
            base_type = self._parse_type_expr(struct_node)
            alias_name = _node_text(declarator_node)
            loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
            return [Typedef(name=alias_name, underlying_type=base_type, location=loc)]

        return []

    def _convert_declaration(self, node: Node, filename: str) -> list[Declaration]:
        type_node = node.child_by_field_name("type")
        declarator_node = node.child_by_field_name("declarator")

        if type_node and type_node.type == "struct_specifier" and not declarator_node:
            st = self._convert_struct(type_node, filename)
            return [st] if st else []

        if type_node and type_node.type == "enum_specifier" and not declarator_node:
            en = self._convert_enum(type_node, filename)
            return [en] if en else []

        # Function returning a pointer has pointer_declarator wrapping function_declarator
        if declarator_node:
            is_pointer_ret = False
            curr: Node | None = declarator_node
            while curr and curr.type == "pointer_declarator":
                is_pointer_ret = True
                curr = curr.child_by_field_name("declarator")

            if curr and curr.type == "function_declarator":
                return self._convert_function_declarator(type_node, curr, filename, is_pointer_return=is_pointer_ret)

        return []

    def _convert_function_declarator(
        self,
        type_node: Node | None,
        declarator_node: Node,
        filename: str,
        *,
        is_pointer_return: bool = False,
    ) -> list[Declaration]:
        ret_type: TypeExpr = self._parse_type_expr(type_node) if type_node else CType("int")
        if is_pointer_return:
            ret_type = Pointer(ret_type)
        ident_node = declarator_node.child_by_field_name("declarator")
        func_name = _node_text(ident_node)

        params_node = declarator_node.child_by_field_name("parameters")
        parameters: list[Parameter] = []
        is_variadic = False

        if params_node:
            for child in params_node.children:
                if child.type == "parameter_declaration":
                    p_type_node = child.child_by_field_name("type")
                    p_decl_node = child.child_by_field_name("declarator")

                    p_type = self._parse_type_expr(p_type_node) if p_type_node else CType("void")
                    p_name = None

                    if p_decl_node:
                        if p_decl_node.type == "pointer_declarator":
                            p_type = Pointer(p_type)
                            sub = p_decl_node.child_by_field_name("declarator")
                            if sub:
                                p_name = _node_text(sub)
                        else:
                            p_name = _node_text(p_decl_node)

                    # void parameter e.g. fn(void) should not produce a parameter
                    if str(p_type) == "void" and p_name is None:
                        continue

                    parameters.append(Parameter(name=p_name, type=p_type))
                elif child.type == "...":
                    is_variadic = True

        loc = SourceLocation(
            file=filename,
            line=declarator_node.start_point[0] + 1,
            column=declarator_node.start_point[1] + 1,
        )
        return [
            Function(
                name=func_name,
                return_type=ret_type,
                parameters=parameters,
                is_variadic=is_variadic,
                location=loc,
            )
        ]

    def _convert_struct(self, node: Node, filename: str) -> Struct | None:
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node) if name_node else None
        body_node = node.child_by_field_name("body")

        fields: list[Field] = []
        if body_node:
            for child in body_node.children:
                if child.type == "field_declaration":
                    f_type_node = child.child_by_field_name("type")
                    base_type = self._parse_type_expr(f_type_node) if f_type_node else CType("int")

                    for sibling in child.children:
                        if sibling.type == "field_identifier":
                            fields.append(Field(name=_node_text(sibling), type=base_type))
                        elif sibling.type == "pointer_declarator":
                            sub = sibling.child_by_field_name("declarator")
                            if sub:
                                fields.append(Field(name=_node_text(sub), type=Pointer(base_type)))

        loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
        return Struct(name=name, fields=fields, is_union=False, location=loc)

    def _convert_enum(self, node: Node, filename: str) -> Enum | None:
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node) if name_node else None
        body_node = node.child_by_field_name("body")

        values: list[EnumValue] = []
        if body_node:
            current_int = 0
            for child in body_node.children:
                if child.type == "enumerator":
                    e_name_node = child.child_by_field_name("name")
                    e_val_node = child.child_by_field_name("value")

                    if e_name_node:
                        e_name = _node_text(e_name_node)
                        val: int | str
                        if e_val_node:
                            val_str = _node_text(e_val_node)
                            try:
                                parsed_int = int(val_str, 0)
                                val = parsed_int
                                current_int = parsed_int + 1
                            except ValueError:
                                val = val_str
                        else:
                            val = current_int
                            current_int += 1
                        values.append(EnumValue(name=e_name, value=val))

        loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
        return Enum(name=name, values=values, location=loc)

    def _parse_type_expr(self, node: Node) -> TypeExpr:
        text = _node_text(node).strip()
        tokens = text.split()
        quals: list[str] = []
        name_parts: list[str] = []

        for token in tokens:
            if token in {"const", "volatile", "unsigned", "signed"}:
                quals.append(token)
            elif token not in ("struct", "enum"):
                name_parts.append(token)

        type_name = " ".join(name_parts) if name_parts else text
        return CType(name=type_name, qualifiers=quals)


_BACKEND_INSTANCE = TreeSitterBackend()


@hook("parse_unit", backend="tree-sitter", priority=Priority.STANDARD)
@hook("parse_unit", backend="*", priority=Priority.FALLBACK)
def _treesitter_parse_hook(
    code: str,
    filename: str = "input.h",
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    if not _BACKEND_INSTANCE.is_available():
        return None
    if context and context.language not in (None, "c"):
        return None
    return _BACKEND_INSTANCE.parse(code, filename, **kwargs)


@hook("get_backend", backend="tree-sitter", priority=Priority.STANDARD)
def _treesitter_get_backend_hook(context: PipelineContext | None = None) -> ParserBackend:
    _ = context
    return _BACKEND_INSTANCE


from headerkit.backends import register_backend  # noqa: E402

register_backend("tree-sitter", TreeSitterBackend, is_default=False)
