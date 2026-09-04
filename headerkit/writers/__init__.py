"""Writers that convert headerkit IR to various output formats.

This package contains writer implementations that convert headerkit IR
(Intermediate Representation) into various output formats such as CFFI
cdef strings, JSON, ctypes modules, Cython .pxd files, and more.

Available Writers
-----------------
cffi
    CFFI cdef declarations for ``ffibuilder.cdef()``.
ctypes
    Python ctypes binding modules.
cython
    Cython .pxd declaration files with C++ support.
diff
    API compatibility reports in JSON or Markdown format.
json
    JSON serialization of IR for inspection and tooling.
lua
    LuaJIT FFI binding files.
prompt
    Token-optimized IR output for LLM context.

Example
-------
::

    from headerkit.writers import get_writer, list_writers

    # Get the default writer (cffi)
    writer = get_writer()

    # Get a specific writer
    writer = get_writer("json", indent=4)

    # List available writers
    for name in list_writers():
        print(name)
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol, runtime_checkable

from headerkit.hooks import HookDispatcher, HookRegistry, PipelineContext, Priority
from headerkit.ir import Header, SourceUnit
from headerkit.scaffold import OutputFile, ProjectLayout, ScaffoldOptions
from headerkit.writers.base import BaseWriter, WriterOption

__all__ = [
    "BaseWriter",
    "WriterBackend",
    "WriterOption",
    "get_default_writer",
    "get_writer",
    "get_writer_info",
    "is_writer_available",
    "list_writer_layouts",
    "list_writer_options",
    "list_writers",
    "register_writer",
]

# =============================================================================
# Writer Protocol
# =============================================================================


@runtime_checkable
class WriterBackend(Protocol):
    """Protocol defining the interface for output writers.

    Writers convert headerkit IR (Header objects) into various output
    formats: CFFI cdef strings, JSON, PXD files, ctypes code, etc.

    Writer-specific options (e.g. exclude_patterns for CFFI, indent
    for JSON) are constructor parameters or dataclass fields on the
    concrete class -- NOT part of the write() signature. This keeps
    the protocol simple and mypy-strict compatible.

    Example
    -------
    ::

        from headerkit.writers import get_writer

        writer = get_writer("cffi")
        output = writer.write(header)
    """

    def write(self, header: Header) -> str:
        """Convert parsed header IR to the target output format.

        Writers should produce best-effort output, silently skipping
        declarations they cannot represent. Writers must not raise
        exceptions for valid Header input.

        :param header: Parsed header IR from a parser backend.
        :returns: String representation in the target format.
        """
        ...

    def write_layout(
        self,
        unit: SourceUnit | Header,
        options: ScaffoldOptions | None = None,
    ) -> ProjectLayout:
        """Convert parsed unit IR into a complete ProjectLayout.

        :param unit: Parsed unit IR from a parser backend.
        :param options: Scaffolding configuration options.
        :returns: ProjectLayout containing all generated files.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable name of this writer (e.g., ``"cffi"``)."""
        ...

    @property
    def format_description(self) -> str:
        """Short description of the output format."""
        ...


# =============================================================================
# Writer Registry
# =============================================================================

# Registry of available writers
# Writers are registered lazily to avoid import errors
_WRITER_REGISTRY: dict[str, type[WriterBackend]] = {}
_WRITER_DESCRIPTIONS: dict[str, str] = {}
_DEFAULT_WRITER: str | None = None
_WRITERS_LOADED: bool = False


def register_writer(
    name: str,
    writer_class: type[WriterBackend],
    is_default: bool = False,
    description: str | None = None,
    priority: int = Priority.STANDARD,
) -> None:
    """Register an output writer.

    Called by writer modules during import to self-register.
    The first registered writer becomes the default unless
    ``is_default`` is explicitly set on a later registration.

    :param name: Writer name used in :func:`get_writer` lookups.
    :param writer_class: The writer class implementing :class:`WriterBackend`.
    :param is_default: If True, this writer becomes the default.
    :param description: Optional short description for :func:`get_writer_info`.
        If not provided, falls back to the class docstring's first line.
    :param priority: Execution priority for hook dispatch.
    """
    global _DEFAULT_WRITER  # pylint: disable=global-statement
    if name in _WRITER_REGISTRY:
        raise ValueError(f"Writer already registered: {name!r}")
    _WRITER_REGISTRY[name] = writer_class
    if description is not None:
        _WRITER_DESCRIPTIONS[name] = description
    elif writer_class.__doc__:
        _WRITER_DESCRIPTIONS[name] = writer_class.__doc__.strip().split("\n")[0]
    if is_default or _DEFAULT_WRITER is None:
        _DEFAULT_WRITER = name

    def _get_hook(context: PipelineContext | None = None, **kwargs: Any) -> WriterBackend:
        opts = (context.options if context and context.options else {}) | kwargs
        return writer_class(**opts)

    def _write_hook(unit: SourceUnit, context: PipelineContext | None = None, **kwargs: Any) -> str | None:
        opts = (context.options if context and context.options else {}) | kwargs
        return writer_class(**opts).write(unit)

    def _scaffold_hook(
        unit: SourceUnit | Header,
        options: ScaffoldOptions,
        context: PipelineContext | None = None,
        **kwargs: Any,
    ) -> ProjectLayout | None:
        sig = inspect.signature(writer_class.__init__)
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        combined = (context.options if context and context.options else {}) | kwargs
        if has_var_keyword:
            inst = writer_class(**combined)
        else:
            valid_keys = set(sig.parameters.keys()) - {"self"}
            init_kwargs = {k: v for k, v in combined.items() if k in valid_keys}
            inst = writer_class(**init_kwargs)

        supported = getattr(inst, "supported_layouts", ("file",))
        if options.layout not in supported:
            return None
        if hasattr(inst, "write_layout"):
            return inst.write_layout(unit, options)
        ext = getattr(inst, "default_extension", ".txt")
        return ProjectLayout(files=[OutputFile(path=f"{options.package_name}{ext}", content=inst.write(unit))])

    HookRegistry.register_global("get_writer", _get_hook, priority=priority, writer=name)
    HookRegistry.register_global("write_output", _write_hook, priority=priority, writer=name)
    HookRegistry.register_global("scaffold_project", _scaffold_hook, priority=priority, writer=name, target=name)


def list_writers() -> list[str]:
    """List names of all registered writers.

    :returns: List of writer names that can be passed to :func:`get_writer`.

    Example
    -------
    ::

        from headerkit.writers import list_writers

        for name in list_writers():
            print(f"Available: {name}")
    """
    _ensure_writers_loaded()
    names = set(_WRITER_REGISTRY.keys())
    for impl in HookRegistry.snapshot():
        if impl.point in ("get_writer", "write_output") and "writer" in impl.matchers:
            w_name = impl.matchers["writer"]
            if "*" not in w_name and "?" not in w_name:
                names.add(w_name)
    return sorted(names)


def is_writer_available(name: str) -> bool:
    """Check if a writer is available for use.

    :param name: Writer name to check.
    :returns: True if the writer is registered and can be instantiated.
    """
    _ensure_writers_loaded()
    return name in _WRITER_REGISTRY


def list_writer_layouts(name: str) -> tuple[str, ...]:
    """Return the tuple of layouts supported by the named writer.

    :param name: Writer name to inspect.
    :returns: Tuple of supported layout names (e.g. ``("file", "package")``).
    :raises ValueError: If the requested writer is not found.
    """
    _ensure_writers_loaded()
    if name not in _WRITER_REGISTRY:
        raise ValueError(f"Unknown writer: {name!r}. Available: {', '.join(list_writers())}")
    writer_class = _WRITER_REGISTRY[name]
    layouts = getattr(writer_class, "supported_layouts", ("file",))
    return tuple(layouts)


def list_writer_options(name: str) -> tuple[WriterOption, ...]:
    """Return the tuple of options supported by the named writer.

    :param name: Writer name to inspect.
    :returns: Tuple of WriterOption specifications.
    :raises ValueError: If the requested writer is not found.
    """
    _ensure_writers_loaded()
    if name not in _WRITER_REGISTRY:
        raise ValueError(f"Unknown writer: {name!r}. Available: {', '.join(list_writers())}")
    writer_class = _WRITER_REGISTRY[name]
    opts = getattr(writer_class, "supported_options", ())
    return tuple(opts)


def get_writer_info() -> list[dict[str, str | bool]]:
    """Get information about all known writers.

    Returns metadata from the registry without instantiating any writer.
    Uses descriptions stored by :func:`register_writer`, falling back to the
    class docstring's first line if no description was provided.

    .. note::
        Keys differ from :func:`~headerkit.backends.get_backend_info`:
        uses ``"is_default"`` (not ``"default"``), and omits the
        ``"available"`` key (writers have no external dependencies
        that could make them unavailable).

    :returns: List of dicts with keys: name, description, is_default.
    """
    _ensure_writers_loaded()

    result: list[dict[str, str | bool]] = []
    for name, writer_class in _WRITER_REGISTRY.items():
        desc = _WRITER_DESCRIPTIONS.get(name, "")
        if not desc and writer_class.__doc__:
            desc = writer_class.__doc__.strip().split("\n")[0]
        result.append(
            {
                "name": name,
                "description": desc,
                "is_default": name == _DEFAULT_WRITER,
            }
        )
    return result


def get_writer(name: str | None = None, **kwargs: object) -> WriterBackend:
    """Get a writer instance.

    Keyword arguments are forwarded to the writer constructor,
    allowing per-invocation configuration::

        writer = get_writer("cffi", exclude_patterns=["__.*"])

    :param name: Writer name, or None for the default writer.
    :param kwargs: Forwarded to writer class constructor.
    :returns: New instance of the requested writer.
    :raises ValueError: If the requested writer is not available.
    """
    _ensure_writers_loaded()
    if name is None:
        if _DEFAULT_WRITER is None:
            raise ValueError("No writers available")
        name = _DEFAULT_WRITER

    ctx = PipelineContext(writer=name, options=dict(kwargs))
    writer = HookDispatcher().first_result("get_writer", context=ctx, **kwargs)
    if isinstance(writer, WriterBackend):
        return writer

    if name in _WRITER_REGISTRY:
        return _WRITER_REGISTRY[name](**kwargs)

    available = ", ".join(list_writers()) or "(none)"
    raise ValueError(f"Unknown writer: {name!r}. Available: {available}")


def get_default_writer() -> str:
    """Get the name of the default writer.

    :returns: Writer name (e.g., ``"cffi"``).
    :raises ValueError: If no writers are available.
    """
    _ensure_writers_loaded()
    if _DEFAULT_WRITER is None:
        raise ValueError("No writers available")
    return _DEFAULT_WRITER


def _ensure_writers_loaded() -> None:
    """Lazily load writer modules to populate the registry.

    NOTE: Managed circular import pattern.
    This module and headerkit.writers.cffi / headerkit.writers.json have a
    circular dependency:

    - This module defines the registry functions
    - Writer modules import register_writer from here at load time
    - _ensure_writers_loaded() in this module imports writer modules lazily

    This is intentional and mirrors the pattern in headerkit/backends/__init__.py.
    Do not restructure without understanding the full cycle.
    """
    global _WRITERS_LOADED  # pylint: disable=global-statement

    if _WRITERS_LOADED:
        return

    _WRITERS_LOADED = True

    # Import triggers module-level registration
    import headerkit.writers.cffi  # noqa: F401
    import headerkit.writers.cshim  # noqa: F401
    import headerkit.writers.ctypes  # noqa: F401
    import headerkit.writers.cython  # noqa: F401
    import headerkit.writers.diff  # noqa: F401
    import headerkit.writers.json  # noqa: F401
    import headerkit.writers.lua  # noqa: F401
    import headerkit.writers.mojo  # noqa: F401
    import headerkit.writers.nim  # noqa: F401
    import headerkit.writers.prompt  # noqa: F401


def _load_writer_plugins() -> None:
    """Load writer plugins registered via entry points.

    Called explicitly by the CLI before invoking writers.
    NOT called from _ensure_writers_loaded() to preserve test hermeticity.

    Plugin authoring contract: the entry point value must be a module path.
    ep.load() imports the module, which calls register_writer() at module bottom.
    Note: register_writer() raises ValueError on duplicate names (unlike register_backend
    which silently replaces). Plugin authors must use unique names.
    """
    import importlib.metadata

    _ensure_writers_loaded()
    for ep in importlib.metadata.entry_points(group="headerkit.writers"):
        try:
            ep.load()
        except (ImportError, ValueError) as exc:
            import logging

            logging.getLogger(__name__).warning("Failed to load writer plugin %r: %s", ep.name, exc)
