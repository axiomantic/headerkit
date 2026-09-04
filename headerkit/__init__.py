"""headerkit - C/C++ header parsing toolkit."""

from headerkit._generate import BatchResult, GenerateResult, batch_generate, generate, generate_all
from headerkit._ir_json import json_to_header
from headerkit._populate import PopulateResult, PopulateTarget, populate
from headerkit._resolve import check_output_collisions, resolve_headers, resolve_output_path
from headerkit._store_merge import MergeResult, store_merge
from headerkit._target import detect_process_triple, resolve_target
from headerkit.backends import (
    LibclangUnavailableError,
    get_backend,
    is_backend_available,
    list_backends,
)
from headerkit.hooks import (
    HookCaller,
    HookDispatcher,
    HookImpl,
    HookRegistry,
    PipelineContext,
    Priority,
    execute_pipeline,
    hook,
)
from headerkit.install_libclang import auto_install
from headerkit.ir import (
    Array,
    BaseSpecifier,
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
    InputSpec,
    Parameter,
    # Protocol
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
from headerkit.scaffold import (
    BYOScaffolder,
    OutputFile,
    ProjectLayout,
    ScaffoldOptions,
    StdlibScaffolder,
    extract_function_names,
    prompt_scaffold_options,
    scaffold,
)
from headerkit.writers import (
    BaseWriter,
    WriterBackend,
    WriterOption,
    get_default_writer,
    get_writer,
    get_writer_info,
    is_writer_available,
    list_writer_layouts,
    list_writer_options,
    list_writers,
    register_writer,
)

__all__ = [
    # Types
    "CType",
    "Pointer",
    "Reference",
    "Array",
    "Parameter",
    "FunctionPointer",
    "TypeExpr",
    # Declarations
    "BaseSpecifier",
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
    "SourceUnit",
    "InputSpec",
    "SourceLocation",
    # Parser Protocol
    "ParserBackend",
    # Backend API
    "get_backend",
    "list_backends",
    "is_backend_available",
    "LibclangUnavailableError",
    # Writer Protocol
    "WriterBackend",
    "BaseWriter",
    "WriterOption",
    # Writer API
    "get_default_writer",
    "get_writer",
    "get_writer_info",
    "is_writer_available",
    "list_writer_layouts",
    "list_writer_options",
    "list_writers",
    "register_writer",
    # Generate API
    "generate",
    "generate_all",
    "GenerateResult",
    # Batch API
    "batch_generate",
    "BatchResult",
    # Resolve API
    "resolve_headers",
    "resolve_output_path",
    "check_output_collisions",
    # IR JSON API
    "json_to_header",
    # Populate API
    "populate",
    "PopulateResult",
    "PopulateTarget",
    # Install API
    "auto_install",
    # Store merge API
    "store_merge",
    "MergeResult",
    # Target detection API
    "detect_process_triple",
    "resolve_target",
    # Hooks API
    "Priority",
    "PipelineContext",
    "HookImpl",
    "HookRegistry",
    "hook",
    "HookDispatcher",
    "HookCaller",
    "execute_pipeline",
    # Scaffolding API
    "OutputFile",
    "ProjectLayout",
    "ScaffoldOptions",
    "BYOScaffolder",
    "StdlibScaffolder",
    "extract_function_names",
    "prompt_scaffold_options",
    "scaffold",
]
