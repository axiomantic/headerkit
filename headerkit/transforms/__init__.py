"""AST transformation architecture for HeaderKit IR."""

from headerkit.transforms.base import IRTransformer
from headerkit.transforms.builtin import (
    FilterByHeader,
    MakeOpaque,
    RenameTypes,
    StripPaddingFields,
    StripPrefix,
    rewrite_type_references,
)
from headerkit.transforms.filter import ChainedFilter, PipelineFilter
from headerkit.transforms.nim_cleanup import NimCleanupTransform
from headerkit.transforms.nim_jsondoc import NimJsonDocEnricher

__all__ = [
    "ChainedFilter",
    "FilterByHeader",
    "IRTransformer",
    "MakeOpaque",
    "NimCleanupTransform",
    "NimJsonDocEnricher",
    "PipelineFilter",
    "RenameTypes",
    "StripPaddingFields",
    "StripPrefix",
    "rewrite_type_references",
]
