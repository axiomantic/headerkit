"""IR to Cython ``.pxd`` writer.

This module converts the headerkit IR (Intermediate Representation) to
Cython ``.pxd`` declaration files.

Features
--------
* Keyword escaping -- Python/Cython keywords get ``_`` suffix with C name alias
* stdint type imports -- Automatically adds ``cimport`` for ``libc.stdint`` types
* Full Cython syntax -- Supports all declaration types (structs, enums, functions, etc.)
* C++ support -- Namespaces, templates, cppclass, operator aliasing

Example
-------
::

    from headerkit.writers import get_writer

    writer = get_writer("cython")
    pxd_content = writer.write(header)

    with open("myheader.pxd", "w") as f:
        f.write(pxd_content)
"""

from __future__ import annotations

import re
import textwrap
from collections import defaultdict
from typing import ClassVar

from headerkit.ir import (
    Array,
    Constant,
    CType,
    Declaration,
    Enum,
    Function,
    FunctionPointer,
    Header,
    Parameter,
    Pointer,
    Reference,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers._cython_keywords import keywords
from headerkit.writers._cython_types import (
    LIBCPP_TYPES,
    get_cython_module_for_type,
    get_libcpp_module_for_type,
    get_stub_module_for_type,
)
from headerkit.writers.base import BaseWriter, WriterOption

# Type qualifiers that Cython doesn't support -- strip from output
UNSUPPORTED_TYPE_QUALIFIERS: set[str] = {
    "_Atomic",
    "__restrict",
    "_Noreturn",
    "__restrict__",
}

# C type names that need to be converted to Cython equivalents
C_TO_CYTHON_TYPE_MAP: dict[str, str] = {
    "_Bool": "bint",  # C99 boolean type -> Cython boolean integer
}


class PxdWriter:
    """Writes IR to Cython ``.pxd`` format.

    Converts a :class:`~headerkit.ir.Header` containing parsed C/C++
    declarations into valid Cython ``.pxd`` syntax. Handles keyword
    escaping, stdint imports, topological sorting, circular dependency
    detection, C++ namespaces, templates, operator aliasing, and
    automatic cimport generation.

    :param header: The parsed header to convert.
    """

    default_output_pattern: str = "{dir}/{stem}.pxd"
    INDENT: str = "    "

    def __init__(self, header: Header, *, stub_cimport_prefix: str | None = "headerkit.stubs") -> None:
        self.header: Header = header
        self.stub_cimport_prefix: str | None = stub_cimport_prefix
        # Track declared struct/union/enum names for type reference cleanup
        self.known_structs: set[str] = set()
        self.known_unions: set[str] = set()
        self.known_enums: set[str] = set()
        self.known_typedefs: set[str] = set()
        self._collect_known_types()

        # Track used-but-undeclared struct/union types (need forward declarations)
        self.undeclared_structs: set[str] = set()
        self.undeclared_unions: set[str] = set()

        # Track incomplete structs (forward declarations with no fields)
        # Fields using these as value types must be skipped
        self.incomplete_structs: set[str] = set()
        self._collect_incomplete_types()

        # Cimport tracking using registries
        self.cython_cimports: dict[str, set[str]] = {}  # module -> types
        self.libcpp_cimports: dict[str, set[str]] = {}  # module -> types
        self.stub_cimports: dict[str, set[str]] = {}  # stub_module -> types

        # Current struct's inner typedefs for method return type resolution
        self._current_inner_typedefs: dict[str, str] = {}

        # Inner typedefs that cannot be represented in Cython (nested template types)
        self._unsupported_inner_typedefs: set[str] = set()

        # Collect types from all declarations
        self._collect_cimport_types()

    # -----------------------------------------------------------------
    # Topological sorting
    # -----------------------------------------------------------------

    def _sort_declarations(self, decls: list[Declaration]) -> tuple[list[Declaration], set[int]]:
        """Sort declarations topologically to resolve forward references.

        Returns a tuple of (sorted declarations, set of indices that are
        in cycles).
        """
        # Build dependency graph
        dependencies: dict[int, set[int]] = defaultdict(set)
        decl_names: dict[str, list[int]] = defaultdict(list)

        for i, decl in enumerate(decls):
            if isinstance(decl, Struct | Typedef | Enum) and decl.name:
                decl_names[decl.name].append(i)

        # Build typedef->underlying_struct map
        typedef_to_struct: dict[str, str] = {}
        for decl in decls:
            if isinstance(decl, Typedef) and decl.name:
                underlying_names = self._extract_type_names(decl.underlying_type)
                for uname in underlying_names:
                    if uname in decl_names:
                        for idx in decl_names[uname]:
                            if isinstance(decls[idx], Struct):
                                typedef_to_struct[decl.name] = uname
                                break

        # Build dependency edges
        for i, decl in enumerate(decls):
            if isinstance(decl, Typedef):
                deps = self._extract_type_names(decl.underlying_type)
                for dep_name in deps:
                    if dep_name in decl_names:
                        for dep_idx in decl_names[dep_name]:
                            dep_decl = decls[dep_idx]
                            if isinstance(dep_decl, Struct | Enum | Typedef):
                                dependencies[i].add(dep_idx)

            elif isinstance(decl, Struct):
                for fld in decl.fields:
                    is_pointer = isinstance(fld.type, Pointer)
                    deps = self._extract_type_names(fld.type)
                    for dep_name in deps:
                        if dep_name in decl_names:
                            for dep_idx in decl_names[dep_name]:
                                dep_decl = decls[dep_idx]
                                if isinstance(dep_decl, Typedef):
                                    dependencies[i].add(dep_idx)
                                    if not is_pointer and dep_name in typedef_to_struct:
                                        struct_name = typedef_to_struct[dep_name]
                                        if struct_name in decl_names:
                                            for struct_idx in decl_names[struct_name]:
                                                if isinstance(decls[struct_idx], Struct):
                                                    dependencies[i].add(struct_idx)
                                elif isinstance(dep_decl, Struct | Enum):
                                    if not is_pointer:
                                        if self._is_value_type_usage(fld.type, dep_name):
                                            dependencies[i].add(dep_idx)

            elif isinstance(decl, Function):
                all_types: set[str] = set()
                all_types.update(self._extract_type_names(decl.return_type))
                for param in decl.parameters:
                    all_types.update(self._extract_type_names(param.type))
                for dep_name in all_types:
                    if dep_name in decl_names:
                        for dep_idx in decl_names[dep_name]:
                            dep_decl = decls[dep_idx]
                            if isinstance(dep_decl, Typedef):
                                dependencies[i].add(dep_idx)

        # Topological sort (Kahn's algorithm)
        in_degree: dict[int, int] = {i: len(dependencies[i]) for i in range(len(decls))}
        queue = [i for i in range(len(decls)) if in_degree[i] == 0]
        sorted_indices: list[int] = []

        while queue:
            queue.sort()
            idx = queue.pop(0)
            sorted_indices.append(idx)
            for dependent in range(len(decls)):
                if idx in dependencies[dependent]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        cycle_indices: set[int] = set()
        if len(sorted_indices) != len(decls):
            sorted_set = set(sorted_indices)
            unsorted_indices = [i for i in range(len(decls)) if i not in sorted_set]
            cycle_indices = set(unsorted_indices)
            sorted_indices.extend(unsorted_indices)

        return ([decls[i] for i in sorted_indices], cycle_indices)

    def _extract_type_names(self, typ: TypeExpr) -> set[str]:
        """Extract all type names referenced by a type expression."""
        names: set[str] = set()

        if isinstance(typ, CType):
            name = typ.name
            if name.startswith("struct "):
                names.add(name[7:])
            elif name.startswith("union "):
                names.add(name[6:])
            elif name.startswith("enum "):
                names.add(name[5:])
            else:
                names.add(name)

        elif isinstance(typ, Pointer):
            names.update(self._extract_type_names(typ.pointee))

        elif isinstance(typ, Reference):
            names.update(self._extract_type_names(typ.target))

        elif isinstance(typ, Array):
            names.update(self._extract_type_names(typ.element_type))

        elif isinstance(typ, FunctionPointer):
            names.update(self._extract_type_names(typ.return_type))
            for param in typ.parameters:
                names.update(self._extract_type_names(param.type))

        return names

    def _is_value_type_usage(self, typ: TypeExpr, type_name: str) -> bool:
        """Check if a type is used as a value type (not through a pointer)."""
        if isinstance(typ, CType):
            name = typ.name
            if name.startswith("struct "):
                return name[7:] == type_name
            elif name.startswith("union "):
                return name[6:] == type_name
            elif name.startswith("enum "):
                return name[5:] == type_name
            else:
                return name == type_name
        return False

    # -----------------------------------------------------------------
    # Main write entry point
    # -----------------------------------------------------------------

    def write(self) -> str:
        """Convert IR Header to Cython ``.pxd`` string."""
        lines: list[str] = []

        # 1. Cython stdlib cimports (sorted for determinism)
        for module in sorted(self.cython_cimports.keys()):
            types = sorted(self.cython_cimports[module])
            lines.append(f"from {module} cimport {', '.join(types)}")

        # 2. C++ STL cimports
        for module in sorted(self.libcpp_cimports.keys()):
            types = sorted(self.libcpp_cimports[module])
            lines.append(f"from {module} cimport {', '.join(types)}")

        # 3. Stub cimports (only when stub_cimport_prefix is configured)
        if self.stub_cimport_prefix is not None:
            for stub_module in sorted(self.stub_cimports.keys()):
                types = sorted(self.stub_cimports[stub_module])
                lines.append(f"from {self.stub_cimport_prefix}.{stub_module} cimport {', '.join(types)}")

        # Blank line before extern blocks if we had cimports
        if lines:
            lines.append("")

        # Group declarations by namespace
        by_namespace: dict[str | None, list[Declaration]] = defaultdict(list)
        for decl in self.header.declarations:
            ns: str | None = getattr(decl, "namespace", None)
            by_namespace[ns].append(decl)

        # If no declarations at all, still output empty extern block
        if not by_namespace:
            by_namespace[None] = []

        # Sort and detect cycles per namespace
        sorted_by_namespace: dict[str | None, list[Declaration]] = {}
        cycle_indices_by_namespace: dict[str | None, set[int]] = {}

        for ns in by_namespace:
            sorted_decls, cycle_indices = self._sort_declarations(by_namespace[ns])
            sorted_by_namespace[ns] = sorted_decls
            cycle_indices_by_namespace[ns] = cycle_indices

        # Output non-namespaced declarations first, then namespaced (sorted)
        namespace_order = sorted(by_namespace.keys(), key=lambda x: (x is not None, x or ""))

        for namespace in namespace_order:
            decls = sorted_by_namespace[namespace]
            cycle_indices = cycle_indices_by_namespace[namespace]

            # Extern block header
            if namespace:
                lines.append(f'cdef extern from "{self.header.path}" namespace "{namespace}":')
            else:
                lines.append(f'cdef extern from "{self.header.path}":')

            # Forward declarations for undeclared types (global namespace only)
            if namespace is None:
                forward_decls: list[str] = []
                for struct_name in sorted(self.undeclared_structs):
                    forward_decls.append(f"{self.INDENT}cdef struct {struct_name}")
                for union_name in sorted(self.undeclared_unions):
                    forward_decls.append(f"{self.INDENT}cdef union {union_name}")
                if forward_decls:
                    lines.append("")
                    lines.extend(forward_decls)

            # 5-phase output for circular dependencies
            if cycle_indices:
                self._write_cycle_phases(decls, lines)
            else:
                # No cycles -- normal output
                if not decls and not (namespace is None and (self.undeclared_structs or self.undeclared_unions)):
                    lines.append(f"{self.INDENT}pass")
                    lines.append("")
                else:
                    lines.append("")
                    for decl in decls:
                        decl_lines = self._write_declaration(decl)
                        for line in decl_lines:
                            lines.append(f"{self.INDENT}{line}" if line else "")
                        lines.append("")

        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Cycle-breaking multi-phase output
    # -----------------------------------------------------------------

    def _write_cycle_phases(
        self,
        decls: list[Declaration],
        lines: list[str],
    ) -> None:
        """Emit declarations in 5 phases to break circular dependencies."""
        typedef_struct_names: set[str] = {
            decl.name for decl in decls if isinstance(decl, Struct) and decl.is_typedef and decl.name
        }

        # Phase 1: Forward declarations for ALL structs with bodies
        forward_struct_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Struct) and (decl.fields or decl.methods):
                if decl.name and get_stub_module_for_type(decl.name):
                    continue
                if decl.is_typedef:
                    continue
                kind = "union" if decl.is_union else "struct"
                name = self._escape_name(decl.name, include_c_name=False)
                forward_struct_decls.append(f"{self.INDENT}cdef {kind} {name}")
        if forward_struct_decls:
            lines.append("")
            lines.extend(forward_struct_decls)

        # Phase 2: ALL typedefs
        typedef_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Typedef):
                decl_lines = self._write_declaration(decl)
                for line in decl_lines:
                    typedef_decls.append(f"{self.INDENT}{line}" if line else "")
                typedef_decls.append("")
        if typedef_decls:
            lines.append("")
            lines.extend(typedef_decls)

        # Phase 3: Enums and forward-declaration-only structs (NOT functions)
        other_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Typedef):
                continue
            if isinstance(decl, Struct) and (decl.fields or decl.methods):
                continue
            if isinstance(decl, Struct) and decl.name in typedef_struct_names:
                continue
            if isinstance(decl, Function):
                continue
            decl_lines = self._write_declaration(decl)
            for line in decl_lines:
                other_decls.append(f"{self.INDENT}{line}" if line else "")
            other_decls.append("")
        if other_decls:
            lines.append("")
            lines.extend(other_decls)

        # Phase 4: ALL struct bodies (topologically sorted among themselves)
        struct_decls_list = [
            d
            for d in decls
            if isinstance(d, Struct) and (d.fields or d.methods) and not (d.name and get_stub_module_for_type(d.name))
        ]

        struct_name_to_idx: dict[str, int] = {}
        for idx, sd in enumerate(struct_decls_list):
            if sd.name:
                struct_name_to_idx[sd.name] = idx

        typedef_to_struct_name: dict[str, str] = {}
        for d in decls:
            if isinstance(d, Typedef) and d.name:
                underlying_names = self._extract_type_names(d.underlying_type)
                for un in underlying_names:
                    if un in struct_name_to_idx:
                        typedef_to_struct_name[d.name] = un
                        break

        struct_deps: dict[int, set[int]] = {i: set() for i in range(len(struct_decls_list))}
        for idx, sd in enumerate(struct_decls_list):
            for fld in sd.fields:
                if isinstance(fld.type, Pointer):
                    continue
                field_types = self._extract_type_names(fld.type)
                for ft in field_types:
                    if ft in struct_name_to_idx and ft != sd.name:
                        struct_deps[idx].add(struct_name_to_idx[ft])
                    if ft in typedef_to_struct_name:
                        target = typedef_to_struct_name[ft]
                        if target in struct_name_to_idx and target != sd.name:
                            struct_deps[idx].add(struct_name_to_idx[target])

        # Topological sort of struct bodies
        in_degree: dict[int, int] = {i: len(struct_deps[i]) for i in range(len(struct_decls_list))}
        queue = [i for i in range(len(struct_decls_list)) if in_degree[i] == 0]
        sorted_struct_indices: list[int] = []

        while queue:
            idx = queue.pop(0)
            sorted_struct_indices.append(idx)
            for dependent in range(len(struct_decls_list)):
                if idx in struct_deps[dependent]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if len(sorted_struct_indices) != len(struct_decls_list):
            remaining = [i for i in range(len(struct_decls_list)) if i not in sorted_struct_indices]
            sorted_struct_indices.extend(remaining)

        struct_bodies: list[str] = []
        for idx in sorted_struct_indices:
            decl = struct_decls_list[idx]
            decl_lines = self._write_declaration(decl)
            for line in decl_lines:
                struct_bodies.append(f"{self.INDENT}{line}" if line else "")
            struct_bodies.append("")
        if struct_bodies:
            lines.append("")
            lines.extend(struct_bodies)

        # Phase 5: Functions
        func_decls: list[str] = []
        for decl in decls:
            if isinstance(decl, Function):
                decl_lines = self._write_declaration(decl)
                for line in decl_lines:
                    func_decls.append(f"{self.INDENT}{line}" if line else "")
                func_decls.append("")
        if func_decls:
            lines.append("")
            lines.extend(func_decls)

    # -----------------------------------------------------------------
    # Known-type collection
    # -----------------------------------------------------------------

    def _collect_known_types(self) -> None:
        """Collect all declared struct/union/enum names for type resolution."""
        for decl in self.header.declarations:
            if isinstance(decl, Struct):
                if decl.name:
                    if getattr(decl, "namespace", None) == "std" and decl.name in LIBCPP_TYPES:
                        continue
                    if decl.is_union:
                        self.known_unions.add(decl.name)
                    else:
                        self.known_structs.add(decl.name)
            elif isinstance(decl, Enum):
                if decl.name:
                    self.known_enums.add(decl.name)
            elif isinstance(decl, Typedef):
                if decl.name:
                    self.known_typedefs.add(decl.name)
                    self.known_structs.add(decl.name)

    def _collect_incomplete_types(self) -> None:
        """Collect structs that are forward declarations (no fields)."""
        for decl in self.header.declarations:
            if isinstance(decl, Struct):
                if decl.name and not decl.fields and not decl.methods:
                    self.incomplete_structs.add(decl.name)

    # -----------------------------------------------------------------
    # Cimport collection
    # -----------------------------------------------------------------

    def _collect_cimport_types(self) -> None:
        """Collect all types that need cimport statements."""
        for decl in self.header.declarations:
            self._collect_types_from_declaration(decl)

    def _collect_types_from_declaration(self, decl: Declaration) -> None:
        """Recursively collect types from a declaration."""
        if isinstance(decl, Function):
            self._check_type(decl.return_type)
            for param in decl.parameters:
                self._check_type(param.type)
        elif isinstance(decl, Struct):
            for fld in decl.fields:
                self._check_type(fld.type)
            for method in decl.methods:
                self._check_type(method.return_type)
                for param in method.parameters:
                    self._check_type(param.type)
        elif isinstance(decl, Typedef):
            self._check_type(decl.underlying_type)
        elif isinstance(decl, Variable):
            self._check_type(decl.type)

    def _check_type(self, typ: TypeExpr) -> None:
        """Check if a type needs a cimport and record it."""
        if isinstance(typ, CType):
            self._check_type_name(typ.name)
        elif isinstance(typ, Pointer):
            self._check_type(typ.pointee)
        elif isinstance(typ, Array):
            self._check_type(typ.element_type)
        elif isinstance(typ, FunctionPointer):
            self._check_type(typ.return_type)
            for param in typ.parameters:
                self._check_type(param.type)

    def _check_type_name(self, name: str) -> None:
        """Check a type name against registries."""
        clean_name = name.removeprefix("struct ").removeprefix("class ").removeprefix("union ")

        # If the type is locally declared in this header, do not cimport from external modules or stubs
        if (
            clean_name in self.known_structs
            or clean_name in self.known_unions
            or clean_name in self.known_enums
            or clean_name in self.known_typedefs
        ):
            return

        # Check stubs BEFORE deciding to forward-declare
        stub_module = get_stub_module_for_type(name) or get_stub_module_for_type(clean_name)

        if not stub_module:
            if name.startswith("struct "):
                struct_name = name[7:].removeprefix("struct ")
                if (
                    "(unnamed" not in struct_name
                    and "(anonymous" not in struct_name
                    and not struct_name.startswith("(")
                    and struct_name not in self.known_structs
                ):
                    self.undeclared_structs.add(struct_name)
            elif name.startswith("union "):
                union_name = name[6:].removeprefix("union ")
                if (
                    "(unnamed" not in union_name
                    and "(anonymous" not in union_name
                    and not union_name.startswith("(")
                    and union_name not in self.known_unions
                ):
                    self.undeclared_unions.add(union_name)

        # Strip std:: prefix for C++ types
        cpp_name = clean_name.removeprefix("std::")

        # For template types, extract the base name
        base_name = cpp_name.split("<")[0] if "<" in cpp_name else cpp_name

        # Check Cython stdlib
        module = get_cython_module_for_type(name)
        if module:
            self.cython_cimports.setdefault(module, set()).add(name)
            return

        # Check C++ STL
        module = get_libcpp_module_for_type(base_name)
        if module:
            self.libcpp_cimports.setdefault(module, set()).add(base_name)

        # Collect stub cimports if a prefix is configured
        if self.stub_cimport_prefix is not None and stub_module:
            self.stub_cimports.setdefault(stub_module, set()).add(clean_name)

        # Also check template arguments recursively
        if "<" in cpp_name:
            self._check_template_args(cpp_name)
            return

    def _check_template_args(self, type_str: str) -> None:
        """Recursively check template arguments for types that need cimports."""
        start = type_str.find("<")
        if start == -1:
            return

        depth = 0
        end = -1
        for i in range(start, len(type_str)):
            if type_str[i] == "<":
                depth += 1
            elif type_str[i] == ">":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            return

        args_str = type_str[start + 1 : end]

        args: list[str] = []
        current_arg = ""
        depth = 0
        for char in args_str:
            if char == "<":
                depth += 1
                current_arg += char
            elif char == ">":
                depth -= 1
                current_arg += char
            elif char == "," and depth == 0:
                args.append(current_arg.strip())
                current_arg = ""
            else:
                current_arg += char

        if current_arg.strip():
            args.append(current_arg.strip())

        for arg in args:
            self._check_type_name(arg)

    # -----------------------------------------------------------------
    # Declaration dispatch
    # -----------------------------------------------------------------

    def _write_declaration(self, decl: Declaration) -> list[str]:
        """Write a single declaration."""
        if isinstance(decl, Struct):
            return self._write_struct(decl)
        if isinstance(decl, Enum):
            return self._write_enum(decl)
        if isinstance(decl, Function):
            return self._write_function(decl)
        if isinstance(decl, Typedef):
            return self._write_typedef(decl)
        if isinstance(decl, Variable):
            return self._write_variable(decl)
        if isinstance(decl, Constant):
            return self._write_constant(decl)
        return []

    # -----------------------------------------------------------------
    # Struct / union / cppclass
    # -----------------------------------------------------------------

    def _write_struct(self, struct: Struct) -> list[str]:
        """Convert a Struct to Cython struct/union/cppclass definition."""
        if not struct.name or (
            not struct.fields and not struct.methods and ("(anonymous" in struct.name or "(unnamed" in struct.name)
        ):
            return []

        lines: list[str] = []

        # Store inner typedefs context for _format_ctype
        self._current_inner_typedefs = struct.inner_typedefs if struct.is_cppclass else {}

        # Track unsupported inner template typedefs
        self._unsupported_inner_typedefs = set()
        if struct.is_cppclass and struct.inner_typedefs:
            for inner_name, inner_type in struct.inner_typedefs.items():
                if "<" in inner_type and ">" in inner_type:
                    base_type = inner_type.split("<")[0].strip()
                    if base_type and base_type in self.known_structs:
                        self._unsupported_inner_typedefs.add(inner_name)

        # Emit notes as comments
        if struct.notes:
            for note in struct.notes:
                lines.append(f"# {note}")

        # Emit packed comment
        if struct.is_packed:
            lines.append("# NOTE: packed struct (Cython does not support __attribute__((packed)))")

        if struct.is_cppclass:
            kind = "cppclass"
        elif struct.is_union:
            kind = "union"
        else:
            kind = "struct"
        name = self._escape_name(struct.name, include_c_name=True)

        # Template parameters
        if struct.template_params:
            params = ", ".join(struct.template_params)
            name = f"{name}[{params}]"

        # Base classes for C++ classes
        if struct.is_cppclass and struct.bases:
            base_names = [self._escape_name(b.name) for b in struct.bases if b.name]
            if base_names:
                name = f"{name}({', '.join(base_names)})"

        # C++ name if different
        if struct.cpp_name and struct.cpp_name != struct.name:
            name = f'{name} "{struct.cpp_name}"'

        keyword = "ctypedef" if struct.is_typedef else "cdef"

        # Forward declaration (no fields, no methods)
        if not struct.fields and not struct.methods and not struct.constructors:
            lines.append(f"{keyword} {kind} {name}")
            return lines

        lines.append(f"{keyword} {kind} {name}:")

        for fld in struct.fields:
            # Skip anonymous struct/union fields
            if fld.is_anonymous_transparent or not fld.name:
                continue
            if isinstance(fld.type, CType) and ("(unnamed" in fld.type.name or "(anonymous" in fld.type.name):
                continue

            # Skip fields using incomplete types as values
            if self._is_incomplete_value_type(fld.type):
                continue

            field_name = self._escape_name(fld.name, include_c_name=True)

            # Bitfield comment (Cython doesn't support bitfields)
            bit_comment = ""
            if fld.bit_width is not None:
                bit_comment = f"  # bitfield: {fld.bit_width} bits"

            if isinstance(fld.type, FunctionPointer):
                if self._is_nested_func_ptr(fld.type):
                    lines.append(f"{self.INDENT}void* {field_name}{bit_comment}")
                else:
                    lines.append(f"{self.INDENT}{self._format_func_ptr(fld.type, field_name)}{bit_comment}")
            elif isinstance(fld.type, Pointer) and isinstance(fld.type.pointee, FunctionPointer):
                if self._is_nested_func_ptr(fld.type.pointee):
                    lines.append(f"{self.INDENT}void* {field_name}{bit_comment}")
                else:
                    lines.append(f"{self.INDENT}{self._format_func_ptr(fld.type.pointee, field_name)}{bit_comment}")
            elif isinstance(fld.type, Array):
                field_type = self._format_type(fld.type)
                dims = self._format_array_dims(fld.type)
                lines.append(f"{self.INDENT}{field_type} {field_name}{dims}{bit_comment}")
            else:
                field_type = self._format_type(fld.type)
                lines.append(f"{self.INDENT}{field_type} {field_name}{bit_comment}")

        # Constructors (cppclass)
        for ctor in struct.constructors:
            params_str = self._format_params(ctor.parameters, ctor.is_variadic)
            lines.append(f"{self.INDENT}{struct.name}({params_str})")

        # Methods (cppclass)
        operator_aliases: dict[str, str] = {
            "operator->": "deref",
            "operator()": "call",
        }
        unsupported_operators: set[str] = {"operator,"}

        for method in struct.methods:
            if method.name in unsupported_operators:
                continue

            return_type_name = method.return_type.name if isinstance(method.return_type, CType) else None
            if return_type_name and return_type_name in self._unsupported_inner_typedefs:
                underlying = self._current_inner_typedefs.get(return_type_name, return_type_name)
                lines.append(
                    f"{self.INDENT}# UNSUPPORTED: {method.name}() returns "
                    f"C++ inner type '{return_type_name}' ({underlying})"
                )
                lines.append(
                    f"{self.INDENT}# Cython cannot represent nested template types. Use the C++ API directly if needed."
                )
                continue

            if method.name in operator_aliases:
                alias = operator_aliases[method.name]
                return_type = self._format_type(method.return_type)
                params_str = self._format_params(method.parameters, method.is_variadic)
                method_line = f'{return_type} {alias} "{method.name}"({params_str})'
                lines.append(f"{self.INDENT}{method_line}")
            else:
                method_lines = self._write_function(method)
                for line in method_lines:
                    lines.append(f"{self.INDENT}{line}")

        # Clear inner typedef context
        self._current_inner_typedefs = {}
        self._unsupported_inner_typedefs = set()

        return lines

    # -----------------------------------------------------------------
    # Enum
    # -----------------------------------------------------------------

    def _write_enum(self, enum: Enum) -> list[str]:
        """Write an enum declaration."""
        if enum.name and "(unnamed at" in enum.name:
            return []

        name = self._escape_name(enum.name, include_c_name=True)

        keyword = "ctypedef" if enum.is_typedef else "cdef"
        if enum.name:
            lines = [f"{keyword} enum {name}:"]
        else:
            lines = [f"{keyword} enum:"]

        if enum.values:
            for val in enum.values:
                val_name = self._escape_name(val.name, include_c_name=True)
                lines.append(f"{self.INDENT}{val_name}")
        else:
            lines.append(f"{self.INDENT}pass")

        return lines

    # -----------------------------------------------------------------
    # Function
    # -----------------------------------------------------------------

    def _write_function(self, func: Function) -> list[str]:
        """Write a function declaration."""
        lines: list[str] = []
        if func.is_static:
            lines.append("@staticmethod")

        return_type = self._format_type(func.return_type)
        name = self._escape_name(func.name, include_c_name=True)
        if func.template_params:
            t_params = ", ".join(func.template_params)
            name = f"{name}[{t_params}]"
        params = self._format_params(func.parameters, func.is_variadic)
        const_suffix = " const" if func.is_const else ""

        # Calling convention comment (Cython doesn't support calling conventions)
        cc_comment = ""
        if func.calling_convention:
            cc_comment = f"  # calling convention: __{func.calling_convention}__"

        lines.append(f"{return_type} {name}({params}){const_suffix}{cc_comment}")
        return lines

    # -----------------------------------------------------------------
    # Typedef
    # -----------------------------------------------------------------

    def _write_typedef(self, typedef: Typedef) -> list[str]:
        """Write a typedef declaration."""
        name = self._escape_name(typedef.name, include_c_name=True)

        # Function pointer typedefs
        if isinstance(typedef.underlying_type, Pointer):
            if isinstance(typedef.underlying_type.pointee, FunctionPointer):
                return self._write_func_ptr_typedef(name, typedef.underlying_type.pointee)
        if isinstance(typedef.underlying_type, FunctionPointer):
            return self._write_func_ptr_typedef(name, typedef.underlying_type)

        underlying = self._format_type(typedef.underlying_type)

        # Skip circular typedefs
        if (
            underlying == name
            or underlying == f"struct {name}"
            or underlying == f"union {name}"
            or underlying == f"enum {name}"
        ):
            return []

        return [f"ctypedef {underlying} {name}"]

    def _write_func_ptr_typedef(self, name: str, fp: FunctionPointer) -> list[str]:
        """Write a function pointer typedef."""
        is_func_ptr_return = isinstance(fp.return_type, FunctionPointer) or (
            isinstance(fp.return_type, Pointer) and isinstance(fp.return_type.pointee, FunctionPointer)
        )
        if is_func_ptr_return:
            return_type = "void*"
        else:
            return_type = self._format_type(fp.return_type)

        params = self._format_params(fp.parameters, fp.is_variadic)
        return [f"ctypedef {return_type} (*{name})({params})"]

    # -----------------------------------------------------------------
    # Variable
    # -----------------------------------------------------------------

    def _write_variable(self, var: Variable) -> list[str]:
        """Write a variable declaration."""
        var_type = self._format_type(var.type)
        name = self._escape_name(var.name, include_c_name=True)

        if isinstance(var.type, Array):
            dims = self._format_array_dims(var.type)
            name = f"{name}{dims}"

        return [f"{var_type} {name}"]

    # -----------------------------------------------------------------
    # Constant
    # -----------------------------------------------------------------

    def _write_constant(self, const: Constant) -> list[str]:
        """Write a constant declaration."""
        name = self._escape_name(const.name, include_c_name=True)

        if const.type:
            type_str = self._format_ctype(const.type)
            if const.type.name == "char" and "const" in const.type.qualifiers:
                return [f"const char* {name}"]
            return [f"{type_str} {name}"]

        # Default to int for macros without detected type
        return [f"int {name}"]

    # -----------------------------------------------------------------
    # Type formatting
    # -----------------------------------------------------------------

    def _format_type(self, type_expr: TypeExpr) -> str:
        """Format a type expression as Cython string."""
        if isinstance(type_expr, CType):
            return self._format_ctype(type_expr)
        if isinstance(type_expr, Pointer):
            return self._format_pointer(type_expr)
        if isinstance(type_expr, Reference):
            return self._format_reference(type_expr)
        if isinstance(type_expr, Array):
            return self._format_array(type_expr)
        if isinstance(type_expr, FunctionPointer):
            return self._format_func_ptr(type_expr)
        return "void"

    def _format_reference(self, ref: Reference) -> str:
        """Format a Reference type."""
        target = self._format_type(ref.target)
        quals = f"{' '.join(ref.qualifiers)} " if ref.qualifiers else ""
        ref_symbol = "&&" if ref.is_rvalue else "&"
        return f"{quals}{target}{ref_symbol}"

    def _format_ctype(self, ctype: CType) -> str:
        """Format a CType.

        Strips struct/union/enum prefixes for declared types, strips
        unsupported qualifiers, resolves inner typedefs, and maps C types
        to Cython equivalents.
        """
        name = ctype.name

        # Map C types to Cython equivalents
        if name in C_TO_CYTHON_TYPE_MAP:
            name = C_TO_CYTHON_TYPE_MAP[name]

        # Strip C++ namespace prefixes
        while "::" in name:
            name = re.sub(r"\b\w+::", "", name)

        # Resolve inner typedefs
        if self._current_inner_typedefs and name in self._current_inner_typedefs:
            name = self._current_inner_typedefs[name]

        # Strip unsupported type qualifiers
        for qual in UNSUPPORTED_TYPE_QUALIFIERS:
            name = name.replace(f"{qual} ", "")
            if name.endswith(f" {qual}"):
                name = name[: -(len(qual) + 1)]
            prefix = f"{qual}("
            if name.startswith(prefix) and name.endswith(")"):
                name = name[len(prefix) : -1]

        # Strip struct/union/enum prefix if the type is declared or known
        if name.startswith("struct "):
            struct_name = name[7:]
            stub_available = get_stub_module_for_type(struct_name) is not None
            if struct_name in self.known_structs or struct_name in self.undeclared_structs or stub_available:
                name = struct_name
        elif name.startswith("union "):
            union_name = name[6:]
            stub_available = get_stub_module_for_type(union_name) is not None
            if union_name in self.known_unions or union_name in self.undeclared_unions or stub_available:
                name = union_name
        elif name.startswith("enum "):
            enum_name = name[5:]
            if enum_name in self.known_enums or " " not in enum_name:
                name = enum_name

        # Convert C++ template syntax <> to Cython syntax []
        if "<" in name and ">" in name:
            name = self._convert_template_syntax(name)

        # Escape keywords in type names
        parts = name.split()
        escaped_parts = [self._escape_name(p) for p in parts]
        name = " ".join(escaped_parts)

        if ctype.qualifiers:
            filtered_quals = [q for q in ctype.qualifiers if q not in UNSUPPORTED_TYPE_QUALIFIERS]
            new_quals = []
            for q in filtered_quals:
                if q not in parts:
                    new_quals.append(q)
            if new_quals:
                quals = " ".join(new_quals)
                return f"{quals} {name}"
        return name

    def _format_pointer(self, ptr: Pointer) -> str:
        """Format a Pointer type."""
        if isinstance(ptr.pointee, FunctionPointer):
            return self._format_func_ptr_as_ptr(ptr.pointee, ptr.qualifiers)

        if isinstance(ptr.pointee, Pointer) and isinstance(ptr.pointee.pointee, FunctionPointer):
            fp = ptr.pointee.pointee
            return_type = self._format_type(fp.return_type)
            params = self._format_params(fp.parameters, fp.is_variadic)
            if not params:
                params = "void"
            result = f"{return_type} (**)({params})"
            if ptr.qualifiers:
                quals = " ".join(ptr.qualifiers)
                result = f"{result} {quals}"
            return result

        pointee = self._format_type(ptr.pointee)
        result = f"{pointee}*"
        if ptr.qualifiers:
            quals = " ".join(ptr.qualifiers)
            result = f"{result} {quals}"
        return result

    def _format_array(self, arr: Array) -> str:
        """Format an Array type (element type only; dimensions added by caller)."""
        return self._format_type(arr.element_type)

    def _is_incomplete_value_type(self, typ: TypeExpr) -> bool:
        """Check if a type is an incomplete struct used as a value."""
        if isinstance(typ, CType):
            name = typ.name
            if name.startswith("struct "):
                struct_name = name[7:]
            else:
                struct_name = name
            if struct_name in self.incomplete_structs:
                return True
            if struct_name in self.undeclared_structs:
                return True
        return False

    def _is_nested_func_ptr(self, fp: FunctionPointer) -> bool:
        """Check if a function pointer returns another function pointer."""
        if isinstance(fp.return_type, FunctionPointer):
            return True
        return isinstance(fp.return_type, Pointer) and isinstance(fp.return_type.pointee, FunctionPointer)

    def _format_func_ptr(self, fp: FunctionPointer, name: str | None = None) -> str:
        """Format a FunctionPointer type."""
        return_type = self._format_type(fp.return_type)
        params = self._format_params(fp.parameters, fp.is_variadic)
        if name:
            return f"{return_type} (*{name})({params})"
        return f"{return_type} (*)({params})"

    def _format_func_ptr_as_ptr(self, fp: FunctionPointer, ptr_quals: list[str]) -> str:
        """Format a pointer to function pointer."""
        return_type = self._format_type(fp.return_type)
        params = self._format_params(fp.parameters, fp.is_variadic)
        result = f"{return_type} (*)({params})"
        if ptr_quals:
            quals = " ".join(ptr_quals)
            result = f"{result} {quals}"
        return result

    def _format_params(self, params: list[Parameter], is_variadic: bool) -> str:
        """Format function parameters."""
        parts: list[str] = []
        for param in params:
            if param.name:
                name = self._escape_name(param.name)
                if isinstance(param.type, FunctionPointer):
                    parts.append(self._format_func_ptr(param.type, name))
                elif isinstance(param.type, Pointer) and isinstance(param.type.pointee, FunctionPointer):
                    parts.append(self._format_func_ptr(param.type.pointee, name))
                elif isinstance(param.type, Array):
                    param_type = self._format_type(param.type)
                    dims = self._format_array_dims(param.type)
                    parts.append(f"{param_type} {name}{dims}")
                else:
                    param_type = self._format_type(param.type)
                    parts.append(f"{param_type} {name}")
            else:
                param_type = self._format_type(param.type)
                parts.append(param_type)

        if is_variadic:
            parts.append("...")

        return ", ".join(parts)

    def _format_array_dims(self, arr: Array) -> str:
        """Format array dimensions for variable/field names."""
        dims: list[str] = []
        current: TypeExpr = arr
        while isinstance(current, Array):
            if current.size is not None:
                dims.append(str(current.size))
            else:
                dims.append("")
            current = current.element_type
        return "".join(f"[{d}]" for d in dims)

    def _convert_template_syntax(self, name: str) -> str:
        """Convert C++ template syntax ``<>`` to Cython syntax ``[]``."""
        result: list[str] = []
        i = 0
        depth = 0

        while i < len(name):
            char = name[i]

            if char in ("(", ")"):
                result.append(char)
                i += 1
            elif char == "<":
                is_template = False
                if i > 0:
                    prev = name[i - 1]
                    if prev.isalnum() or prev == "_" or prev == "]":
                        is_template = True
                else:
                    is_template = True

                if is_template:
                    result.append("[")
                    depth += 1
                else:
                    result.append(char)
                i += 1
            elif char == ">":
                if depth > 0:
                    result.append("]")
                    depth -= 1
                else:
                    result.append(char)
                i += 1
            else:
                result.append(char)
                i += 1

        return "".join(result)

    def _escape_name(self, name: str | None, include_c_name: bool = False) -> str:
        """Escape Python/Cython keywords by adding underscore suffix."""
        if name is None:
            return ""

        if name in keywords:
            if include_c_name:
                return f'{name}_ "{name}"'
            return f"{name}_"

        return name


# =====================================================================
# Public convenience function
# =====================================================================


def write_pxd(header: Header, *, stub_cimport_prefix: str | None = "headerkit.stubs") -> str:
    """Convenience function to convert a Header IR to Cython .pxd format.

    :param header: The Header IR to convert
    :param stub_cimport_prefix: If set, emit cimport lines for stub types
        (e.g., "headerkit.stubs" -> "from headerkit.stubs.stdarg cimport va_list").
        Defaults to "headerkit.stubs". Pass None to suppress stub cimports.
    :return: The generated .pxd file contents as a string
    """
    writer = PxdWriter(header, stub_cimport_prefix=stub_cimport_prefix)
    return writer.write()


# =====================================================================
# WriterBackend wrapper
# =====================================================================


class CythonWriter(BaseWriter):
    """Writer that converts Header IR into Cython .pxd declarations.

    Example::

        from headerkit.writers import get_writer

        writer = get_writer("cython")
        pxd_string = writer.write(header)
    """

    name: str = "cython"
    format_description: str = "Cython .pxd declarations for C/C++ interop"
    default_output_pattern: str = "{dir}/{stem}.pxd"
    default_extension: str = ".pxd"
    supported_layouts: ClassVar[tuple[str, ...]] = ("file", "package", "project")
    supported_options: ClassVar[tuple[WriterOption, ...]] = (
        WriterOption(
            name="test_type",
            description="Type of test stubs to generate",
            default="both",
            choices=("both", "tripwire", "unit", "none"),
        ),
    )

    def __init__(self, *, stub_cimport_prefix: str | None = "headerkit.stubs") -> None:
        self.stub_cimport_prefix: str | None = stub_cimport_prefix

    def _render(self, unit: SourceUnit | Header) -> str:
        header = unit if isinstance(unit, Header) else Header(declarations=unit.declarations, path=unit.path)
        writer = PxdWriter(header, stub_cimport_prefix=self.stub_cimport_prefix)
        return writer.write()

    def write(self, header: Header) -> str:
        """Convert header IR to Cython .pxd string."""
        return self._render(header)

    def _write_package_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
    ) -> ProjectLayout:
        pkg = options.package_name
        test_type = options.get_option("test_type", "both")
        pxd_code = self._render(unit)

        pyproject = textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=61.0", "Cython>=3.0"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "{pkg}"
            version = "0.1.0"
            description = "Cython bindings for {pkg}"
            requires-python = ">=3.9"

            [tool.setuptools.packages.find]
            where = ["src"]
        """)

        init_py = textwrap.dedent(f"""\
            \"\"\"{pkg} package initialization.\"\"\"
            from {pkg} import {pkg}

            __all__ = ["{pkg}"]
        """)

        pyx_code = textwrap.dedent(f"""\
            # cython: language_level=3
            cimport {pkg}.{pkg} as c_{pkg}

            # High-level Python wrappers can be exposed here
        """)

        files = [
            OutputFile(path="pyproject.toml", content=pyproject),
            OutputFile(path=f"src/{pkg}/__init__.py", content=init_py),
            OutputFile(path=f"src/{pkg}/{pkg}.pxd", content=pxd_code),
            OutputFile(path=f"src/{pkg}/{pkg}.pyx", content=pyx_code),
        ]

        if test_type in ("both", "tripwire"):
            tripwire = textwrap.dedent(f"""\
                import pytest

                @pytest.mark.tripwire
                def test_cython_tripwire():
                    \"\"\"Tripwire: verify cython wrapper import.\"\"\"
                    try:
                        import {pkg}
                    except ImportError:
                        pytest.fail("Failed to import cython wrapper module '{pkg}'")
            """)
            files.append(OutputFile(path="tests/test_tripwire.py", content=tripwire))

        if test_type in ("both", "unit"):
            unit_test = textwrap.dedent(f"""\
                import {pkg}

                def test_{pkg}_loaded():
                    assert {pkg} is not None
            """)
            files.append(OutputFile(path="tests/test_wrapper.py", content=unit_test))

        return ProjectLayout(files=files)

    def hash_comment_format(self) -> str:
        """Return format string for wrapping TOML cache metadata in Cython comments."""
        return "# {line}"


# Uses bottom-of-module self-registration (same pattern as cffi.py).
from headerkit.writers import register_writer  # noqa: E402

register_writer(
    "cython",
    CythonWriter,
    description="Cython .pxd declarations for C/C++ interop",
)
