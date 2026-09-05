"""Tree-sitter based parser backend for C and C++ headers."""

from __future__ import annotations

import logging
from typing import Any

from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    Array,
    BaseSpecifier,
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    ParserBackend,
    Pointer,
    Reference,
    SourceLocation,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)

logger = logging.getLogger("headerkit.backends.treesitter")

_HAS_TREESITTER: bool = False
_HAS_TREESITTER_C: bool = False
_HAS_TREESITTER_CPP: bool = False

try:
    from tree_sitter import Language, Node, Parser

    _HAS_TREESITTER = True
except ImportError:
    Node = Any  # type: ignore[misc,assignment]

try:
    import tree_sitter_c as tsc

    _HAS_TREESITTER_C = True
except ImportError:
    pass

try:
    import tree_sitter_cpp as tscpp

    _HAS_TREESITTER_CPP = True
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
    """Parser backend using tree-sitter-c and tree-sitter-cpp."""

    supported_classifications: frozenset[str] = frozenset({"header", "source"})

    @property
    def supported_languages(self) -> frozenset[str]:
        langs = set()
        if _HAS_TREESITTER_C:
            langs.add("c")
        if _HAS_TREESITTER_CPP:
            langs.update({"c", "c++", "cpp"})
        return frozenset(langs)

    @property
    def name(self) -> str:
        return "tree-sitter"

    @property
    def supports_macros(self) -> bool:
        return False

    @property
    def supports_cpp(self) -> bool:
        return _HAS_TREESITTER_CPP

    def is_available(self) -> bool:
        return _HAS_TREESITTER and (_HAS_TREESITTER_C or _HAS_TREESITTER_CPP)

    def _is_cpp_mode(self, code: str, filename: str, extra_args: list[str] | None = None) -> bool:
        if extra_args:
            for i, arg in enumerate(extra_args):
                if arg == "-x" and i + 1 < len(extra_args):
                    if extra_args[i + 1] in ("c++", "cpp"):
                        return True
                    if extra_args[i + 1] == "c":
                        return False
                if arg.startswith("-std=c++") or arg.startswith("-std=gnu++"):
                    return True
                if (arg.startswith("-std=c") or arg.startswith("-std=gnu")) and not (
                    arg.startswith("-std=c++") or arg.startswith("-std=gnu++")
                ):
                    return False
        ext = filename.lower()
        if ext.endswith((".hpp", ".hh", ".hxx", ".h++", ".cpp", ".cc", ".cxx", ".c++", ".cpptest")):
            return True
        if _HAS_TREESITTER_CPP:
            if "class " in code or "namespace " in code or "template<" in code or "template <" in code:
                return True
        return False

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
        if not self.is_available():
            msg = "tree-sitter is not installed. Install with: pip install 'headerkit[treesitter]'"
            raise RuntimeError(msg)

        is_cpp = self._is_cpp_mode(code, filename, extra_args)

        if is_cpp:
            if not _HAS_TREESITTER_CPP:
                msg = "tree-sitter-cpp is not installed. Install with: pip install 'headerkit[treesitter]'"
                raise RuntimeError(msg)
            language = Language(tscpp.language())
        elif _HAS_TREESITTER_C:
            language = Language(tsc.language())
        elif _HAS_TREESITTER_CPP:
            language = Language(tscpp.language())
        else:
            msg = "tree-sitter-c or tree-sitter-cpp is not installed. Install with: pip install 'headerkit[treesitter]'"
            raise RuntimeError(msg)

        parser = Parser(language)
        tree = parser.parse(code.encode("utf-8"))

        declarations: list[Declaration] = []
        for child in tree.root_node.children:
            decls = self._convert_top_level(child, filename, is_cpp=is_cpp)
            declarations.extend(decls)

        return Header(path=filename, declarations=declarations)

    def _convert_top_level(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        if node.type in ("preproc_ifdef", "preproc_if"):
            results: list[Declaration] = []
            for child in node.children:
                # Do not walk mutually exclusive #elif/#else branches when traversing the primary #if branch
                if child.type in ("preproc_elif", "preproc_else"):
                    continue
                results.extend(
                    self._convert_top_level(
                        child,
                        filename,
                        namespace=namespace,
                        template_params=template_params,
                        is_cpp=is_cpp,
                    )
                )
            return results

        if node.type in ("preproc_elif", "preproc_else"):
            return []

        if node.type in ("linkage_specification", "declaration_list"):
            results = []
            for child in node.children:
                results.extend(
                    self._convert_top_level(
                        child,
                        filename,
                        namespace=namespace,
                        template_params=template_params,
                        is_cpp=is_cpp,
                    )
                )
            return results

        if node.type == "namespace_definition":
            return self._convert_namespace(node, filename, parent_namespace=namespace, is_cpp=is_cpp)

        if node.type == "template_declaration":
            return self._convert_template(node, filename, namespace=namespace, is_cpp=is_cpp)

        if node.type in ("class_specifier", "struct_specifier", "union_specifier"):
            st = self._convert_class_or_struct(
                node,
                filename,
                namespace=namespace,
                template_params=template_params,
                is_cpp=is_cpp,
            )
            return [st] if st else []

        if node.type == "alias_declaration":
            td = self._convert_alias_declaration(node, filename, namespace=namespace)
            return [td] if td else []

        if node.type == "declaration":
            return self._convert_declaration(
                node,
                filename,
                namespace=namespace,
                template_params=template_params,
                is_cpp=is_cpp,
            )

        if node.type == "function_definition":
            return self._convert_function_definition(
                node,
                filename,
                namespace=namespace,
                template_params=template_params,
                is_cpp=is_cpp,
            )

        if node.type == "type_definition":
            return self._convert_type_definition(node, filename, namespace=namespace)

        if node.type == "enum_specifier":
            en = self._convert_enum(node, filename, namespace=namespace)
            return [en] if en else []

        return []

    def _convert_namespace(
        self,
        node: Node,
        filename: str,
        *,
        parent_namespace: str | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        name_node = node.child_by_field_name("name")
        ns_name = _node_text(name_node).strip() if name_node else None
        current_ns = f"{parent_namespace}::{ns_name}" if parent_namespace and ns_name else (ns_name or parent_namespace)

        body_node = node.child_by_field_name("body")
        if not body_node:
            for child in node.children:
                if child.type == "declaration_list":
                    body_node = child
                    break

        results: list[Declaration] = []
        if body_node:
            for child in body_node.children:
                results.extend(
                    self._convert_top_level(
                        child,
                        filename,
                        namespace=current_ns,
                        is_cpp=is_cpp,
                    )
                )
        return results

    def _convert_template(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        t_params: list[str] = []
        param_list = node.child_by_field_name("parameters")
        if param_list:
            for child in param_list.children:
                if child.type in (
                    "type_parameter_declaration",
                    "optional_type_parameter_declaration",
                    "variadic_type_parameter_declaration",
                ):
                    name_child = child.child_by_field_name("name") or child.child_by_field_name("declarator")
                    if not name_child:
                        for sub in child.children:
                            if sub.type in ("type_identifier", "identifier"):
                                name_child = sub
                                break
                    if name_child:
                        t_params.append(_node_text(name_child).strip())
                elif child.type == "parameter_declaration":
                    decl = child.child_by_field_name("declarator")
                    if decl:
                        t_params.append(_node_text(decl).strip())

        results: list[Declaration] = []
        for child in node.children:
            if child.type in ("template", "template_parameter_list", "<", ">", ";"):
                continue
            results.extend(
                self._convert_top_level(
                    child,
                    filename,
                    namespace=namespace,
                    template_params=t_params,
                    is_cpp=is_cpp,
                )
            )
        return results

    def _convert_alias_declaration(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
    ) -> Typedef | None:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        if name_node and type_node:
            name = _node_text(name_node).strip()
            underlying = self._parse_type_expr(type_node)
            loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
            return Typedef(name=name, underlying_type=underlying, namespace=namespace, location=loc)
        return None

    def _convert_type_definition(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
    ) -> list[Declaration]:
        struct_node = node.child_by_field_name("type")
        declarators = node.children_by_field_name("declarator")
        if not declarators:
            single = node.child_by_field_name("declarator")
            if single:
                declarators = [single]

        if struct_node and struct_node.type in ("struct_specifier", "class_specifier", "union_specifier"):
            body_node = struct_node.child_by_field_name("body")
            if body_node:
                st = self._convert_class_or_struct(struct_node, filename, namespace=namespace)
                if st:
                    if len(declarators) == 1:
                        alias_name, _, _ = self._unwrap_declarator(declarators[0], CType(st.name or ""))
                        if alias_name:
                            st.name = alias_name
                        st.is_typedef = True
                        return [st]
                    results: list[Declaration] = [st]
                    for d in declarators:
                        alias_name, underlying_type, ident_node = self._unwrap_declarator(d, CType(st.name or ""))
                        loc_node = ident_node or d
                        loc = SourceLocation(
                            file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                        )
                        if alias_name and alias_name != st.name:
                            results.append(
                                Typedef(
                                    name=alias_name, underlying_type=underlying_type, namespace=namespace, location=loc
                                )
                            )
                    return results

        if struct_node and struct_node.type == "enum_specifier":
            body_node = struct_node.child_by_field_name("body")
            if body_node:
                en = self._convert_enum(struct_node, filename, namespace=namespace)
                if en:
                    if len(declarators) == 1:
                        alias_name, _, _ = self._unwrap_declarator(declarators[0], CType(en.name or ""))
                        if alias_name:
                            en.name = alias_name
                        en.is_typedef = True
                        return [en]
                    res_en: list[Declaration] = [en]
                    for d in declarators:
                        alias_name, underlying_type, ident_node = self._unwrap_declarator(d, CType(en.name or ""))
                        loc_node = ident_node or d
                        loc = SourceLocation(
                            file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                        )
                        if alias_name and alias_name != en.name:
                            res_en.append(
                                Typedef(
                                    name=alias_name, underlying_type=underlying_type, namespace=namespace, location=loc
                                )
                            )
                    return res_en

        if struct_node and declarators:
            base_type = self._parse_type_expr(struct_node)
            td_results: list[Declaration] = []
            for d in declarators:
                alias_name, underlying_type, ident_node = self._unwrap_declarator(d, base_type)
                loc_node = ident_node or d
                loc = SourceLocation(
                    file=filename, line=loc_node.start_point[0] + 1, column=loc_node.start_point[1] + 1
                )
                td_results.append(
                    Typedef(name=alias_name or "", underlying_type=underlying_type, namespace=namespace, location=loc)
                )
            return td_results

        return []

    def _convert_function_definition(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        for child in node.children:
            if child.type == "storage_class_specifier" and _node_text(child).strip() == "static":
                return []

        type_node = node.child_by_field_name("type")
        declarator_node = node.child_by_field_name("declarator")

        if declarator_node:
            pointer_depth = 0
            curr: Node | None = declarator_node
            while curr and curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                pointer_depth += 1
                curr = curr.child_by_field_name("declarator")

            if curr and curr.type == "function_declarator":
                return self._convert_function_declarator(
                    type_node,
                    curr,
                    filename,
                    pointer_depth=pointer_depth,
                    namespace=namespace,
                    template_params=template_params,
                )

        return []

    def _is_function_declaration(self, declarator: Node) -> bool:
        curr: Node | None = declarator
        while curr and curr.type in (
            "pointer_declarator",
            "abstract_pointer_declarator",
            "reference_declarator",
        ):
            child = curr.child_by_field_name("declarator")
            if not child:
                for c in curr.children:
                    if c.type not in ("*", "&", "&&", "type_qualifier", "const", "volatile"):
                        child = c
                        break
            curr = child

        if curr and curr.type == "function_declarator":
            inner = curr.child_by_field_name("declarator")
            if inner and inner.type in (
                "identifier",
                "field_identifier",
                "operator_name",
                "qualified_identifier",
                "destructor_name",
            ):
                return True
            if inner and inner.type == "parenthesized_declarator":
                subs = [c for c in inner.children if c.type not in ("(", ")")]
                if subs and subs[0].type in (
                    "identifier",
                    "field_identifier",
                    "operator_name",
                    "qualified_identifier",
                    "destructor_name",
                ):
                    return True
        return False

    def _unwrap_declarator(
        self,
        node: Node,
        curr_type: TypeExpr,
    ) -> tuple[str | None, TypeExpr, Node | None]:
        """Unwrap a declarator node, wrapping curr_type and finding the identifier node."""
        if node.type == "init_declarator":
            decl = node.child_by_field_name("declarator")
            if not decl:
                for c in node.children:
                    if c.type not in ("=", "initializer_list"):
                        decl = c
                        break
            if decl:
                return self._unwrap_declarator(decl, curr_type)
            return None, curr_type, node

        if node.type == "parenthesized_declarator":
            for c in node.children:
                if c.type not in ("(", ")"):
                    return self._unwrap_declarator(c, curr_type)
            return None, curr_type, node

        if node.type in ("pointer_declarator", "abstract_pointer_declarator"):
            quals = [
                _node_text(c).strip()
                for c in node.children
                if (c.type == "type_qualifier" or c.type in ("const", "volatile"))
            ]
            ptr_type: TypeExpr = Pointer(curr_type, qualifiers=quals)
            inner_decl = node.child_by_field_name("declarator")
            if not inner_decl:
                for c in node.children:
                    if c.type not in ("*", "type_qualifier", "const", "volatile"):
                        inner_decl = c
                        break
            if inner_decl:
                return self._unwrap_declarator(inner_decl, ptr_type)
            return None, ptr_type, node

        if node.type == "reference_declarator":
            is_rval = any(c.type == "&&" for c in node.children)
            ref_type: TypeExpr = Reference(curr_type, is_rvalue=is_rval)
            inner_decl = node.child_by_field_name("declarator")
            if not inner_decl:
                for c in node.children:
                    if c.type not in ("&", "&&"):
                        inner_decl = c
                        break
            if inner_decl:
                return self._unwrap_declarator(inner_decl, ref_type)
            return None, ref_type, node

        if node.type == "array_declarator":
            inner_decl = node.child_by_field_name("declarator")
            size_node = node.child_by_field_name("size")
            size: int | str | None = None
            if size_node:
                s_text = _node_text(size_node).strip()
                try:
                    size = int(s_text, 0)
                except ValueError:
                    size = s_text
            arr_type: TypeExpr = Array(element_type=curr_type, size=size)
            if inner_decl:
                return self._unwrap_declarator(inner_decl, arr_type)
            return None, arr_type, node

        if node.type == "function_declarator":
            params_node = node.child_by_field_name("parameters")
            params: list[Parameter] = []
            is_variadic = False
            if params_node:
                for child in params_node.children:
                    if child.type == "parameter_declaration":
                        param = self._convert_parameter(child)
                        if param:
                            params.append(param)
                    elif child.type in ("...", "variadic_parameter"):
                        is_variadic = True
            fn_type: TypeExpr = FunctionPointer(return_type=curr_type, parameters=params, is_variadic=is_variadic)
            inner_decl = node.child_by_field_name("declarator")
            if inner_decl:
                return self._unwrap_declarator(inner_decl, fn_type)
            return None, fn_type, node

        if node.type in ("identifier", "field_identifier", "type_identifier", "qualified_identifier"):
            return _node_text(node).strip(), curr_type, node

        return _node_text(node).strip(), curr_type, node

    def _convert_declaration(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> list[Declaration]:
        type_node = node.child_by_field_name("type")
        declarators = node.children_by_field_name("declarator")
        if not declarators:
            single = node.child_by_field_name("declarator")
            if single:
                declarators = [single]

        results: list[Declaration] = []

        if type_node and type_node.type in ("struct_specifier", "class_specifier", "union_specifier"):
            body_node = type_node.child_by_field_name("body")
            if not declarators or body_node:
                st = self._convert_class_or_struct(
                    type_node,
                    filename,
                    namespace=namespace,
                    template_params=template_params,
                    is_cpp=is_cpp,
                )
                if st:
                    results.append(st)
            if not declarators:
                return results

        elif type_node and type_node.type == "enum_specifier":
            body_node = type_node.child_by_field_name("body")
            if not declarators or body_node:
                en = self._convert_enum(type_node, filename, namespace=namespace)
                if en:
                    results.append(en)
            if not declarators:
                return results

        if not declarators:
            return results

        base_type = self._parse_type_expr(type_node) if type_node else CType("int")
        is_deprecated = any(
            "deprecated" in _node_text(c)
            for c in node.children
            if c.type in ("attribute_specifier", "attribute_declaration", "ms_declspec_modifier")
        )

        for decl in declarators:
            if self._is_function_declaration(decl):
                pointer_depth = 0
                curr: Node | None = decl
                while curr and curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                    pointer_depth += 1
                    curr = curr.child_by_field_name("declarator")

                if curr and curr.type == "function_declarator":
                    funcs = self._convert_function_declarator(
                        type_node,
                        curr,
                        filename,
                        pointer_depth=pointer_depth,
                        namespace=namespace,
                        template_params=template_params,
                    )
                    results.extend(funcs)
            else:
                name, var_type, ident_node = self._unwrap_declarator(decl, base_type)
                if name:
                    loc_node = ident_node or decl
                    loc = SourceLocation(
                        file=filename,
                        line=loc_node.start_point[0] + 1,
                        column=loc_node.start_point[1] + 1,
                    )
                    results.append(
                        Variable(
                            name=name,
                            type=var_type,
                            namespace=namespace,
                            is_deprecated=is_deprecated,
                            location=loc,
                        )
                    )

        return results

    def _convert_function_declarator(
        self,
        type_node: Node | None,
        declarator_node: Node,
        filename: str,
        *,
        pointer_depth: int = 0,
        namespace: str | None = None,
        template_params: list[str] | None = None,
    ) -> list[Declaration]:
        ret_type: TypeExpr = self._parse_type_expr(type_node) if type_node else CType("int")
        for _ in range(pointer_depth):
            ret_type = Pointer(ret_type)
        ident_node = declarator_node.child_by_field_name("declarator")
        if not ident_node:
            for c in declarator_node.children:
                if c.type in ("identifier", "field_identifier", "operator_name"):
                    ident_node = c
                    break

        func_name = _node_text(ident_node).strip() if ident_node else ""

        params_node = declarator_node.child_by_field_name("parameters")
        parameters: list[Parameter] = []
        is_variadic = False

        if params_node:
            for child in params_node.children:
                if child.type == "parameter_declaration":
                    param = self._convert_parameter(child)
                    if param:
                        parameters.append(param)
                elif child.type in ("...", "variadic_parameter"):
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
                namespace=namespace,
                template_params=template_params or [],
                location=loc,
            )
        ]

    def _convert_class_or_struct(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
        template_params: list[str] | None = None,
        is_cpp: bool = False,
    ) -> Struct | None:
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node).strip() if name_node else None
        body_node = node.child_by_field_name("body")

        is_class_keyword = node.type == "class_specifier" or any(c.type == "class" for c in node.children)
        is_union = node.type == "union_specifier" or any(c.type == "union" for c in node.children)

        bases: list[BaseSpecifier] = []
        base_clause = None
        for child in node.children:
            if child.type == "base_class_clause":
                base_clause = child
                break

        if base_clause:
            curr_access = "public"
            curr_virt = False
            for child in base_clause.children:
                if child.type == "access_specifier":
                    curr_access = _node_text(child).strip()
                elif child.type == "virtual":
                    curr_virt = True
                elif child.type in ("type_identifier", "qualified_identifier", "template_type"):
                    base_name = _node_text(child).strip()
                    bases.append(BaseSpecifier(name=base_name, access=curr_access, is_virtual=curr_virt))
                    curr_access = "public"
                    curr_virt = False

        fields: list[Field] = []
        methods: list[Function] = []
        constructors: list[Function] = []
        destructor: Function | None = None
        inner_typedefs: dict[str, str] = {}

        current_access = "private" if is_class_keyword else "public"

        if body_node:
            for child in body_node.children:
                if child.type == "access_specifier":
                    spec = _node_text(child.children[0]).strip() if child.children else _node_text(child).strip()
                    if spec.endswith(":"):
                        spec = spec[:-1].strip()
                    if spec in ("public", "protected", "private"):
                        current_access = spec
                    continue

                if child.type == "alias_declaration":
                    u_name_node = child.child_by_field_name("name")
                    u_type_node = child.child_by_field_name("type")
                    if u_name_node and u_type_node:
                        inner_typedefs[_node_text(u_name_node).strip()] = _node_text(u_type_node).strip()
                    continue

                if child.type == "type_definition":
                    tds = self._convert_type_definition(child, filename, namespace=None)
                    for td in tds:
                        if isinstance(td, Typedef):
                            inner_typedefs[td.name] = str(td.underlying_type)
                    continue

                if child.type in ("field_declaration", "declaration"):
                    func_decl, ret_type_node, is_virt, is_stat, is_expl = self._find_function_declarator(child)
                    if func_decl:
                        fn = self._convert_method_declarator(
                            func_decl,
                            ret_type_node,
                            filename,
                            class_name=name,
                            access=current_access,
                            is_virtual=is_virt,
                            is_static=is_stat,
                            is_explicit=is_expl,
                            child_node=child,
                        )
                        if fn:
                            if fn.name.startswith("~") or (name and fn.name == f"~{name}"):
                                destructor = fn
                            elif name and fn.name == name and ret_type_node is None:
                                constructors.append(fn)
                            else:
                                methods.append(fn)
                    else:
                        f_type_node = child.child_by_field_name("type")
                        base_type = self._parse_type_expr(f_type_node) if f_type_node else CType("int")
                        is_static_field = any(
                            c.type == "storage_class_specifier" and _node_text(c).strip() == "static"
                            for c in child.children
                        )
                        bit_width: int | None = None
                        for c in child.children:
                            if c.type == "bitfield_clause":
                                num_child = c.child_by_field_name("length") or c.child_by_field_name("width")
                                if not num_child:
                                    for sub in c.children:
                                        if sub.type == "number_literal":
                                            num_child = sub
                                            break
                                if num_child:
                                    try:
                                        bit_width = int(_node_text(num_child).strip(), 0)
                                    except ValueError:
                                        bit_width = None

                        field_decls = child.children_by_field_name("declarator")
                        if not field_decls:
                            for sibling in child.children:
                                if sibling.type in (
                                    "field_identifier",
                                    "identifier",
                                    "pointer_declarator",
                                    "array_declarator",
                                    "function_declarator",
                                ):
                                    field_decls.append(sibling)

                        for f_decl in field_decls:
                            f_name, f_type, _ = self._unwrap_declarator(f_decl, base_type)
                            if f_name or bit_width is not None:
                                fields.append(
                                    Field(
                                        name=f_name or "",
                                        type=f_type,
                                        bit_width=bit_width,
                                        access=current_access,
                                        is_static=is_static_field,
                                    )
                                )

        is_cppclass = is_class_keyword or bool(methods) or bool(bases) or bool(constructors) or (destructor is not None)
        loc = SourceLocation(file=filename, line=node.start_point[0] + 1, column=node.start_point[1] + 1)
        return Struct(
            name=name,
            fields=fields,
            methods=methods,
            constructors=constructors,
            destructor=destructor,
            bases=bases,
            is_union=is_union,
            is_cppclass=is_cppclass,
            namespace=namespace,
            template_params=template_params or [],
            inner_typedefs=inner_typedefs,
            location=loc,
        )

    def _find_function_declarator(self, node: Node) -> tuple[Node | None, Node | None, bool, bool, bool]:
        is_virtual = any(c.type == "virtual" for c in node.children)
        is_static = any(
            c.type == "storage_class_specifier" and _node_text(c).strip() == "static" for c in node.children
        )
        is_explicit = any(c.type in ("explicit", "explicit_function_specifier") for c in node.children)
        ret_type_node = node.child_by_field_name("type")

        decl = node.child_by_field_name("declarator")
        if not decl:
            for child in node.children:
                if child.type in (
                    "function_declarator",
                    "pointer_declarator",
                    "reference_declarator",
                ):
                    decl = child
                    break

        if not decl:
            return None, None, False, False, False

        curr: Node | None = decl
        while curr and curr.type in ("pointer_declarator", "abstract_pointer_declarator", "reference_declarator"):
            next_child = curr.child_by_field_name("declarator")
            if not next_child:
                for c in curr.children:
                    if c.type in ("function_declarator", "reference_declarator", "pointer_declarator"):
                        next_child = c
                        break
            curr = next_child

        if curr and curr.type == "function_declarator":
            inner = curr.child_by_field_name("declarator")
            if inner and inner.type == "parenthesized_declarator":
                if any(c.type in ("pointer_declarator", "*") for c in inner.children):
                    return None, None, False, False, False
            return curr, ret_type_node, is_virtual, is_static, is_explicit

        return None, None, False, False, False

    def _extract_return_type(self, type_node: Node | None, declarator_root: Node | None) -> TypeExpr:
        base_type: TypeExpr = self._parse_type_expr(type_node) if type_node else CType("void")
        if not declarator_root:
            return base_type

        curr: Node | None = declarator_root
        wrappers: list[str] = []
        while curr and curr.type != "function_declarator":
            if curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                wrappers.append("*")
                curr = curr.child_by_field_name("declarator")
            elif curr.type == "reference_declarator":
                is_rval = any(c.type == "&&" for c in curr.children)
                wrappers.append("&&" if is_rval else "&")
                curr = curr.child_by_field_name("declarator")
                if not curr:
                    for c in declarator_root.children:
                        if c.type == "function_declarator":
                            curr = c
                            break
            else:
                break

        ret = base_type
        for w in wrappers:
            if w == "*":
                ret = Pointer(ret)
            elif w == "&&":
                ret = Reference(ret, is_rvalue=True)
            elif w == "&":
                ret = Reference(ret, is_rvalue=False)
        return ret

    def _convert_method_declarator(
        self,
        func_decl: Node,
        ret_type_node: Node | None,
        filename: str,
        *,
        class_name: str | None = None,
        access: str | None = None,
        is_virtual: bool = False,
        is_static: bool = False,
        is_explicit: bool = False,
        child_node: Node | None = None,
    ) -> Function | None:
        ident_node = func_decl.child_by_field_name("declarator")
        if not ident_node:
            for c in func_decl.children:
                if c.type in ("identifier", "field_identifier", "destructor_name", "operator_name"):
                    ident_node = c
                    break

        func_name = _node_text(ident_node).strip() if ident_node else ""

        is_pure_virtual = False
        if child_node:
            has_eq = any(c.type == "=" for c in child_node.children)
            has_zero = any(c.type == "number_literal" and _node_text(c).strip() == "0" for c in child_node.children)
            if has_eq and has_zero:
                is_pure_virtual = True
                is_virtual = True

        is_const = False
        for c in func_decl.children:
            if (c.type == "type_qualifier" and _node_text(c).strip() == "const") or c.type == "const":
                is_const = True

        params_node = func_decl.child_by_field_name("parameters")
        parameters: list[Parameter] = []
        is_variadic = False
        if params_node:
            for child in params_node.children:
                if child.type == "parameter_declaration":
                    param = self._convert_parameter(child)
                    if param:
                        parameters.append(param)
                elif child.type in ("...", "variadic_parameter"):
                    is_variadic = True

        decl_root = child_node.child_by_field_name("declarator") if child_node else None
        if not decl_root and child_node:
            for c in child_node.children:
                if c.type in ("reference_declarator", "pointer_declarator", "function_declarator"):
                    decl_root = c
                    break
        ret_type = self._extract_return_type(ret_type_node, decl_root)

        loc = SourceLocation(
            file=filename,
            line=func_decl.start_point[0] + 1,
            column=func_decl.start_point[1] + 1,
        )

        return Function(
            name=func_name,
            return_type=ret_type,
            parameters=parameters,
            is_variadic=is_variadic,
            is_virtual=is_virtual,
            is_pure_virtual=is_pure_virtual,
            is_static=is_static,
            is_const=is_const,
            is_explicit=is_explicit,
            access=access,
            location=loc,
        )

    def _convert_parameter(self, node: Node) -> Parameter | None:
        p_type_node = node.child_by_field_name("type")
        p_decl_node = node.child_by_field_name("declarator")

        p_type: TypeExpr = self._parse_type_expr(p_type_node) if p_type_node else CType("void")
        p_name: str | None = None

        if p_decl_node:
            curr: Node | None = p_decl_node
            while curr and curr.type in (
                "pointer_declarator",
                "abstract_pointer_declarator",
                "reference_declarator",
            ):
                if curr.type in ("pointer_declarator", "abstract_pointer_declarator"):
                    p_type = Pointer(p_type)
                elif curr.type == "reference_declarator":
                    is_rval = any(c.type == "&&" for c in curr.children)
                    p_type = Reference(p_type, is_rvalue=is_rval)
                curr = curr.child_by_field_name("declarator")

            if curr and curr.type in ("identifier", "type_identifier", "field_identifier"):
                p_name = _node_text(curr).strip()

        if str(p_type) == "void" and p_name is None:
            return None

        default_val_node = node.child_by_field_name("default_value")
        default_value = _node_text(default_val_node).strip() if default_val_node else None

        return Parameter(name=p_name, type=p_type, default_value=default_value)

    def _parse_array_declarator(self, node: Node, base_type: TypeExpr) -> tuple[str | None, TypeExpr]:
        name_node = node.child_by_field_name("declarator")
        size_node = node.child_by_field_name("size")
        size: int | str | None = None
        if size_node:
            s_text = _node_text(size_node).strip()
            try:
                size = int(s_text, 0)
            except ValueError:
                size = s_text
        arr_type = Array(element_type=base_type, size=size)
        name = _node_text(name_node).strip() if name_node else None
        return name, arr_type

    def _convert_enum(
        self,
        node: Node,
        filename: str,
        *,
        namespace: str | None = None,
    ) -> Enum | None:
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node).strip() if name_node else None
        body_node = node.child_by_field_name("body")

        values: list[EnumValue] = []
        if body_node:
            current_int = 0
            for child in body_node.children:
                if child.type == "enumerator":
                    e_name_node = child.child_by_field_name("name")
                    e_val_node = child.child_by_field_name("value")

                    if e_name_node:
                        e_name = _node_text(e_name_node).strip()
                        val: int | str
                        if e_val_node:
                            val_str = _node_text(e_val_node).strip()
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

    def _parse_type_expr(self, node: Node | None) -> TypeExpr:
        if node is None:
            return CType("int")
        text = _node_text(node).strip()
        return self._parse_type_str(text)

    def _parse_type_str(self, text: str) -> TypeExpr:
        text = text.strip()
        if text.endswith("&&"):
            return Reference(target=self._parse_type_str(text[:-2]), is_rvalue=True)
        if text.endswith("&"):
            return Reference(target=self._parse_type_str(text[:-1]), is_rvalue=False)
        if text.endswith("*"):
            return Pointer(pointee=self._parse_type_str(text[:-1]))

        tokens = text.split()
        quals: list[str] = []
        name_parts: list[str] = []

        for token in tokens:
            if token in {"const", "volatile", "unsigned", "signed"}:
                quals.append(token)
            elif token not in ("struct", "enum", "class", "union"):
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
    if context and context.language not in (None, "c", "c++", "cpp"):
        return None
    return _BACKEND_INSTANCE.parse(code, filename, **kwargs)


@hook("get_backend", backend="tree-sitter", priority=Priority.STANDARD)
def _treesitter_get_backend_hook(context: PipelineContext | None = None) -> ParserBackend:
    _ = context
    return _BACKEND_INSTANCE


from headerkit.backends import register_backend  # noqa: E402

register_backend("tree-sitter", TreeSitterBackend, is_default=False)
