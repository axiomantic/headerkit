"""Parser backends for clangir.

This package contains parser backend implementations that convert C/C++
source code into the clangir IR (Intermediate Representation).

Available Backends
------------------
libclang
    LLVM clang-based parser with full C++ support. Requires system
    libclang library. Uses vendored clang Python bindings.

Example
-------
::

    from clangir.backends import get_backend, list_backends

    # Get the default backend
    backend = get_backend()

    # Get a specific backend
    backend = get_backend("libclang")

    # List available backends
    for name in list_backends():
        print(name)
"""

from clangir.ir import (
    ParserBackend,
)

# Registry of available backends
# Backends are registered lazily to avoid import errors if dependencies are missing
_BACKEND_REGISTRY: dict[str, type[ParserBackend]] = {}
_DEFAULT_BACKEND: str | None = None
_BACKENDS_LOADED: bool = False  # Track if we've tried to load all backends


def register_backend(name: str, backend_class: type[ParserBackend], is_default: bool = False) -> None:
    """Register a parser backend.

    Called by backend modules during import to add themselves to the registry.
    The first registered backend becomes the default unless ``is_default`` is
    explicitly set on a later registration.

    :param name: Unique name for the backend (e.g., ``"libclang"``).
    :param backend_class: Class implementing the :class:`~clangir.ir.ParserBackend` protocol.
    :param is_default: If True, this becomes the default backend for :func:`get_backend`.
    """
    global _DEFAULT_BACKEND  # pylint: disable=global-statement
    _BACKEND_REGISTRY[name] = backend_class
    if is_default or _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = name


def list_backends() -> list[str]:
    """List names of all registered backends.

    :returns: List of backend names that can be passed to :func:`get_backend`.

    Example
    -------
    ::

        from clangir.backends import list_backends

        for name in list_backends():
            print(f"Available: {name}")
    """
    _ensure_backends_loaded()
    return list(_BACKEND_REGISTRY.keys())


def is_backend_available(name: str) -> bool:
    """Check if a backend is available for use.

    :param name: Backend name to check.
    :returns: True if the backend is registered and can be instantiated.
    """
    _ensure_backends_loaded()
    return name in _BACKEND_REGISTRY


def get_backend_info() -> list[dict[str, str | bool]]:
    """Get information about all known backends.

    :returns: List of dicts with name, available, default, and description.
    """
    _ensure_backends_loaded()

    descriptions = {
        "libclang": "Full C/C++ support via LLVM",
    }

    result: list[dict[str, str | bool]] = []
    for name in ["libclang"]:  # clangir only ships libclang
        result.append(
            {
                "name": name,
                "available": name in _BACKEND_REGISTRY,
                "default": name == _DEFAULT_BACKEND,
                "description": descriptions.get(name, ""),
            }
        )
    return result


def get_backend(name: str | None = None) -> ParserBackend:
    """Get a parser backend instance.

    Returns a new instance of the requested backend. If no name is provided,
    returns the default backend (libclang).

    :param name: Backend name (e.g., ``"libclang"``),
        or None for the default backend.
    :returns: New instance of the requested backend.
    :raises ValueError: If the requested backend is not available.

    Example
    -------
    ::

        from clangir.backends import get_backend

        # Get default backend
        backend = get_backend()

        # Get libclang backend
        clang = get_backend("libclang")

        # Parse a header
        header = backend.parse(code, "myheader.h")
    """
    _ensure_backends_loaded()

    if name is None:
        if _DEFAULT_BACKEND is None:
            raise ValueError("No backends available")
        name = _DEFAULT_BACKEND

    if name not in _BACKEND_REGISTRY:
        available = ", ".join(_BACKEND_REGISTRY.keys()) or "(none)"
        raise ValueError(f"Unknown backend: {name!r}. Available: {available}")

    return _BACKEND_REGISTRY[name]()


def get_default_backend() -> str:
    """Get the name of the default backend.

    Returns the name of the currently configured default backend.

    :returns: Backend name (e.g., "libclang").
    :raises ValueError: If no backends are available.

    Example
    -------
    ::

        from clangir.backends import get_default_backend

        default = get_default_backend()
        print(f"Default backend: {default}")
    """
    _ensure_backends_loaded()

    if _DEFAULT_BACKEND is None:
        raise ValueError("No backends available")
    return _DEFAULT_BACKEND


def _detect_system_clang_version() -> str | None:
    """Detect the system libclang/LLVM version.

    :returns: Version string like "18" or None if not detected.
    """
    from clangir._clang._version import detect_llvm_version

    return detect_llvm_version()


def _ensure_backends_loaded() -> None:
    """Lazily load backend modules to populate the registry.

    NOTE: Managed circular import pattern.
    This module and clangir.backends.libclang have a circular dependency:
    - This module defines the registry functions
    - libclang.py imports register_backend from here at load time
    - _ensure_backends_loaded() in this module imports libclang lazily
    This is intentional. Do not restructure without understanding the full cycle.

    With vendored cindex bindings, the import always succeeds. The failure
    mode is that libclang's shared library is not found during configuration,
    in which case the backend simply does not register itself.
    """
    global _BACKENDS_LOADED  # pylint: disable=global-statement

    if _BACKENDS_LOADED:
        return

    _BACKENDS_LOADED = True

    # Import triggers module-level registration if libclang is available
    try:
        import clangir.backends.libclang  # noqa: F401 (side effect import)
    except ImportError:
        pass

    if not _BACKEND_REGISTRY:
        from clangir._clang._version import detect_llvm_version

        version = detect_llvm_version()
        if version:
            hint = (
                f"libclang {version} detected but shared library not found.\n"
                f"Install: brew install llvm (macOS) or "
                f"apt install libclang-{version}-dev (Ubuntu)"
            )
        else:
            hint = (
                "No LLVM/clang installation found.\n"
                "Install: brew install llvm (macOS) or "
                "apt install libclang-dev (Ubuntu)"
            )
        import warnings

        warnings.warn(f"No parser backends available. {hint}", stacklevel=2)
