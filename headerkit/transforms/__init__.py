"""AST transformation architecture for HeaderKit IR."""

from headerkit.transforms.base import IRTransformer
from headerkit.transforms.builtin import (
    FilterByHeader,
    FilterBySymbol,
    FilterDeprecated,
    FilterUnexportedSymbols,
    FilterUnexportedSymbolsTransform,
    MakeOpaque,
    RenameTypes,
    StripPaddingFields,
    StripPrefix,
    rewrite_type_references,
)
from headerkit.transforms.doc_parser import DocParseError, DocParserTransform
from headerkit.transforms.filter import (
    ChainedFilter,
    PipelineFilter,
    TransformOption,
    coerce_transform_options,
    get_transform,
    list_transform_options,
    list_transforms,
    register_transform,
)

__all__ = [
    "ChainedFilter",
    "DocParseError",
    "DocParserTransform",
    "FilterByHeader",
    "FilterBySymbol",
    "FilterDeprecated",
    "FilterUnexportedSymbols",
    "FilterUnexportedSymbolsTransform",
    "IRTransformer",
    "MakeOpaque",
    "PipelineFilter",
    "RenameTypes",
    "StripPaddingFields",
    "StripPrefix",
    "TransformOption",
    "coerce_transform_options",
    "get_transform",
    "list_transform_options",
    "list_transforms",
    "register_transform",
    "rewrite_type_references",
]
