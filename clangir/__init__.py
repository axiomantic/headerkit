"""clangir - C/C++ header parsing toolkit."""

from clangir.backends import get_backend, is_backend_available, list_backends
from clangir.ir import (
    Array,
    Constant,
    # Type expressions
    CType,
    Declaration,
    Enum,
    EnumValue,
    # Declarations
    Field,
    Function,
    FunctionPointer,
    # Container
    Header,
    Parameter,
    # Protocol
    ParserBackend,
    Pointer,
    SourceLocation,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)

__all__ = [
    # Types
    "CType",
    "Pointer",
    "Array",
    "Parameter",
    "FunctionPointer",
    "TypeExpr",
    # Declarations
    "Field",
    "EnumValue",
    "Enum",
    "Struct",
    "Function",
    "Typedef",
    "Variable",
    "Constant",
    "Declaration",
    # Container
    "Header",
    "SourceLocation",
    # Protocol
    "ParserBackend",
    # Backend API
    "get_backend",
    "list_backends",
    "is_backend_available",
]
