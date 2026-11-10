"""Native Nim parser backend for HeaderKit IR.

Parses ``.nim`` source files directly via Nim's built-in compiler AST library
(``compiler/[ast, parser, idents, options, lineinfos, renderer]``) and also
handles Nim ``--header`` C files by delegating to available C parser backends,
normalizing ``nimbase.h`` / ``ccgtypes.nim`` constructs, enriching with
``nim jsondoc`` and ``_nim_ast_helper.nim``, and running ``DocParserTransform``.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.resources
import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from headerkit.backends import get_backend, is_backend_available, register_backend
from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    Array,
    Constant,
    CType,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    FunctionPointer,
    MacroFunction,
    Parameter,
    ParserBackend,
    Pointer,
    Reference,
    SourceLocation,
    SourceMeta,
    SourceUnit,
    Struct,
    Typedef,
    TypeExpr,
    Variable,
)
from headerkit.transforms.doc_parser import DocParserTransform

logger = logging.getLogger(__name__)

_NIM_STORAGE_MACROS: frozenset[str] = frozenset(
    {
        "N_LIB_PRIVATE",
        "N_LIB_EXPORT",
        "N_LIB_IMPORT",
        "N_LIB_EXPORT_VAR",
        "NIM_EXTERNC",
        "N_NIM_DEPRECATED",
    }
)

_NIM_CALLCONV_MACROS: frozenset[str] = frozenset(
    {
        "N_CDECL",
        "N_NOCONV",
        "N_STDCALL",
        "N_FASTCALL",
        "N_INLINE",
        "N_NOINLINE",
        "N_NIMCALL",
        "N_SYSCALL",
        "N_THISCALL",
        "N_SAFECALL",
        "N_CLOSURE",
    }
)

_NIM_CALLCONV_PTR_MACROS: frozenset[str] = frozenset(
    {
        "N_CDECL_PTR",
        "N_NOCONV_PTR",
        "N_STDCALL_PTR",
        "N_FASTCALL_PTR",
        "N_INLINE_PTR",
        "N_NOINLINE_PTR",
        "N_NIMCALL_PTR",
        "N_SYSCALL_PTR",
        "N_THISCALL_PTR",
        "N_SAFECALL_PTR",
        "N_CLOSURE_PTR",
    }
)

_NIM_INTERNAL_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "NimMain",
        "NimMainInner",
        "NimMainModule",
        "PreMain",
        "PreMainInner",
        "cmdCount",
        "cmdLine",
        "gEnv",
        "signalHandler",
        "nimFrame",
        "popFrame",
        "TFrame",
        "TFrame_",
        "nimfr_",
        "nimln_",
        "nimlf_",
        "NIM_INTBITS",
        "NIM_EmulateOverflowChecks",
        "NimSeqV2",
        "NimStrPayload",
        "NimStringV2",
        "RefHeader",
        "CellSeq",
        "GcStat",
        "TNimTypeV2",
        "RootObj",
    }
)

_NIM_INTERNAL_PREFIXES: tuple[str, ...] = (
    "nimGC_",
    "nimRegister",
    "nimErrorFlag",
    "nimZeroMem",
    "nimCopyMem",
    "nimSetMem",
    "nimAdd",
    "nimSub",
    "nimMul",
    "eqdestroy_",
    "eqcopy_",
    "eqsink_",
    "eqwasMoved_",
    "eqdup_",
    "atmdotdotats",
    "callDepthLimitReached__",
    "framePtr__",
    "tySequence__",
)

_NIM_SCALAR_TYPE_MAP: dict[str, str] = {
    "NI": "intptr_t",
    "NU": "uintptr_t",
    "NI8": "int8_t",
    "NI16": "int16_t",
    "NI32": "int32_t",
    "NI64": "int64_t",
    "NU8": "uint8_t",
    "NU16": "uint16_t",
    "NU32": "uint32_t",
    "NU64": "uint64_t",
    "NF": "double",
    "NF64": "double",
    "NF32": "float",
    "NIM_BOOL": "bool",
    "_Bool": "bool",
    "NIM_CHAR": "char",
}

_NIM_SOURCE_SCALAR_MAP: dict[str, str] = {
    "int": "intptr_t",
    "uint": "uintptr_t",
    "int8": "int8_t",
    "int16": "int16_t",
    "int32": "int32_t",
    "int64": "int64_t",
    "uint8": "uint8_t",
    "byte": "uint8_t",
    "uint16": "uint16_t",
    "uint32": "uint32_t",
    "uint64": "uint64_t",
    "float": "double",
    "float64": "double",
    "float32": "float",
    "cint": "int",
    "cuint": "unsigned int",
    "cshort": "short",
    "cushort": "unsigned short",
    "clong": "long",
    "culong": "unsigned long",
    "clonglong": "long long",
    "culonglong": "unsigned long long",
    "cschar": "signed char",
    "cuchar": "unsigned char",
    "cchar": "char",
    "char": "char",
    "csize_t": "size_t",
    "cssize_t": "ssize_t",
    "cfloat": "float",
    "cdouble": "double",
    "clongdouble": "long double",
    "bool": "bool",
    "NIM_BOOL": "bool",
    "void": "void",
    **_NIM_SCALAR_TYPE_MAP,
}

_NIM_BUILTIN_TYPEDEFS: frozenset[str] = frozenset({"NCSTRING", *_NIM_SCALAR_TYPE_MAP})
_C_PTR_QUALS: frozenset[str] = frozenset({"const", "volatile", "restrict", "__restrict"})


@functools.lru_cache(maxsize=1)
def _find_nim_libpath() -> str | None:
    """Locate the Nim standard library directory containing ``nimbase.h``."""
    nim_bin = shutil.which("nim")
    if not nim_bin:
        return None
    try:
        res = subprocess.run(
            ["nim", "dump", "--dump.format:json", "."],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            start = res.stdout.find("{")
            end = res.stdout.rfind("}")
            if start != -1 and end > start:
                data = json.loads(res.stdout[start : end + 1])
                libpath = data.get("libpath")
                if isinstance(libpath, str) and os.path.isfile(os.path.join(libpath, "nimbase.h")):
                    return libpath
    except (subprocess.SubprocessError, OSError, ValueError):
        pass

    candidate = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(nim_bin)), "..", "lib"))
    if os.path.isfile(os.path.join(candidate, "nimbase.h")):
        return candidate
    return None


def _has_nimbase_in_dirs(
    include_dirs: list[str] | None,
    extra_args: list[str] | None,
    filename: str,
) -> bool:
    """Return True if ``nimbase.h`` already exists in user-supplied include directories."""
    search_dirs: list[str] = []
    file_dir = os.path.dirname(os.path.abspath(filename))
    if file_dir:
        search_dirs.append(file_dir)
    if include_dirs:
        search_dirs.extend(include_dirs)
    if extra_args:
        i = 0
        while i < len(extra_args):
            arg = extra_args[i]
            if arg in ("-I", "-isystem") and i + 1 < len(extra_args):
                search_dirs.append(extra_args[i + 1])
                i += 2
                continue
            for prefix in ("-I", "-isystem"):
                if arg.startswith(prefix) and len(arg) > len(prefix):
                    search_dirs.append(arg[len(prefix) :])
                    break
            i += 1
    return any(os.path.isfile(os.path.join(d, "nimbase.h")) for d in search_dirs)


def _is_ascii_ident(text: str) -> bool:
    return bool(text) and (text[0].isalpha() or text[0] == "_") and all(ch.isalnum() or ch == "_" for ch in text)


def _is_typroc_name(name: str) -> bool:
    if not name.startswith("tyProc__"):
        return False
    tail = name[len("tyProc__") :]
    return bool(tail) and all(ch.isalnum() or ch == "_" for ch in tail)


def _unmangle_nim_colon_name(name: str) -> str:
    """Unmangle Nim compiler ``_colon_<name>`` C identifier suffixes."""
    search_start = 0
    while True:
        idx = name.find("_colon_", search_start)
        if idx == -1:
            return name
        tail = name[idx + len("_colon_") :]
        if idx > 0 and _is_ascii_ident(tail):
            return name[:idx]
        search_start = idx + 1


def _unmangle_nim_type_name(name: str) -> str:
    """Unmangle Nim ``ccgtypes.nim`` ``tyObject_<Name>__<hash>`` and ``tyEnum_<Name>__<hash>`` names."""
    prefix = ""
    rest = name
    for kw in ("struct ", "union ", "enum "):
        if rest.startswith(kw):
            idx = len(kw)
            while idx < len(rest) and rest[idx].isspace():
                idx += 1
            prefix = rest[:idx]
            rest = rest[idx:]
            break

    for tag_prefix, allowed_kws in (
        ("tyObject_", ("", "struct", "union")),
        ("tyEnum_", ("", "enum")),
    ):
        if rest.startswith(tag_prefix) and prefix.strip() in allowed_kws:
            after_tag = rest[len(tag_prefix) :]
            sep = after_tag.rfind("__")
            if sep > 0:
                type_ident = after_tag[:sep]
                hash_tail = after_tag[sep + 2 :]
                if _is_ascii_ident(type_ident) and hash_tail and all(ch.isalnum() or ch == "_" for ch in hash_tail):
                    return f"{prefix}{type_ident}"
    return name


def _clean_nim_identifier(name: str | None) -> str | None:
    """Strip at most one trailing Nim C parameter suffix (``_p0``) and template gensyms (```gensym0``)."""
    if not name:
        return name
    cleaned = _unmangle_nim_colon_name(name)
    p_idx = cleaned.rfind("_p")
    if p_idx > 0 and cleaned[p_idx + 2 :].isdigit():
        cleaned = cleaned[:p_idx]
    while True:
        prev = cleaned
        for sep in ("`gensym", "_gensym"):
            g_idx = cleaned.rfind(sep)
            if g_idx > 0 and cleaned[g_idx + len(sep) :].isdigit():
                cleaned = cleaned[:g_idx]
        cleaned = _unmangle_nim_colon_name(cleaned)
        if cleaned == prev:
            break
    return cleaned


def _clean_nim_field_name(name: str) -> str:
    """Clean a Nim struct field name without stripping numeric suffixes like ``_0`` or ``_1``."""
    cleaned = _unmangle_nim_colon_name(name)
    while True:
        prev = cleaned
        for sep in ("`gensym", "_gensym"):
            g_idx = cleaned.rfind(sep)
            if g_idx > 0 and cleaned[g_idx + len(sep) :].isdigit():
                cleaned = cleaned[:g_idx]
        cleaned = _unmangle_nim_colon_name(cleaned)
        if cleaned == prev:
            break
    return cleaned


def _is_direct_nim_bool(type_expr: TypeExpr) -> bool:
    """Return True if ``type_expr`` is directly ``CType('NIM_BOOL')``."""
    return isinstance(type_expr, CType) and type_expr.name == "NIM_BOOL"


def _parse_c_type_str(type_str: str) -> TypeExpr:
    """Parse a C type string (such as ``const char **`` or ``NI32 *``) into a ``TypeExpr``."""
    s = type_str.strip()
    star_idx = s.rfind("*")
    if star_idx != -1:
        after_star = s[star_idx + 1 :].strip()
        after_tokens = [q for q in after_star.split() if q]
        if all(q in _C_PTR_QUALS for q in after_tokens):
            prefix = s[:star_idx].strip()
            return Pointer(pointee=_parse_c_type_str(prefix), qualifiers=after_tokens)

    quals: list[str] = []
    tokens = s.split()
    base_tokens: list[str] = []
    for tok in tokens:
        if tok in ("const", "volatile", "restrict", "__restrict"):
            quals.append(tok)
        else:
            base_tokens.append(tok)
    base_name = " ".join(base_tokens) if base_tokens else s
    if base_name == "NCSTRING":
        return Pointer(pointee=CType(name="char", qualifiers=quals, is_elaborated=False))
    mapped = _NIM_SCALAR_TYPE_MAP.get(base_name)
    if mapped is not None:
        return CType(name=mapped, qualifiers=quals)
    unmangled = _unmangle_nim_type_name(base_name)
    return CType(name=unmangled, qualifiers=quals)


def _rewrite_nim_type(
    type_expr: TypeExpr,
    typroc_map: dict[str, FunctionPointer] | None = None,
) -> TypeExpr:
    """Recursively normalize Nim ``nimbase.h`` and ``ccgtypes.nim`` C types into standard C99 ``TypeExpr`` nodes."""
    if isinstance(type_expr, CType):
        if "*" in type_expr.name:
            qual_prefix = f"{' '.join(type_expr.qualifiers)} " if type_expr.qualifiers else ""
            return _parse_c_type_str(f"{qual_prefix}{type_expr.name}")
        if typroc_map and type_expr.name in typroc_map:
            return typroc_map[type_expr.name]
        if type_expr.name == "NCSTRING":
            return Pointer(pointee=CType(name="char", qualifiers=list(type_expr.qualifiers), is_elaborated=False))
        mapped = _NIM_SCALAR_TYPE_MAP.get(type_expr.name)
        if mapped is not None:
            return replace(type_expr, name=mapped)
        unmangled = _unmangle_nim_type_name(type_expr.name)
        if unmangled != type_expr.name:
            return replace(type_expr, name=unmangled)
        return type_expr
    if isinstance(type_expr, Pointer):
        return replace(type_expr, pointee=_rewrite_nim_type(type_expr.pointee, typroc_map))
    if isinstance(type_expr, Reference):
        return replace(type_expr, target=_rewrite_nim_type(type_expr.target, typroc_map))
    if isinstance(type_expr, Array):
        return replace(type_expr, element_type=_rewrite_nim_type(type_expr.element_type, typroc_map))
    if isinstance(type_expr, FunctionPointer):
        new_params: list[Parameter] = []
        for p in type_expr.parameters:
            was_nim_bool = _is_direct_nim_bool(p.type)
            p_clean = replace(
                p,
                name=_clean_nim_identifier(p.name),
                type=_rewrite_nim_type(p.type, typroc_map),
            )
            if was_nim_bool and hasattr(p_clean, "type_overrides"):
                overrides = dict(getattr(p_clean, "type_overrides", {}) or {})
                overrides.setdefault("pyi", "bool")
                p_clean = replace(p_clean, type_overrides=overrides)
            new_params.append(p_clean)
        return replace(
            type_expr,
            return_type=_rewrite_nim_type(type_expr.return_type, typroc_map),
            parameters=new_params,
        )
    return type_expr


def _load_backend_resource_bytes(resource_name: str) -> bytes | None:
    """Load a packaged backend helper file via ``importlib.resources.files`` with ``Path(__file__)`` fallback."""
    try:
        ref = importlib.resources.files("headerkit.backends").joinpath(resource_name)
        data = ref.read_bytes()
        if data:
            return data
    except (OSError, TypeError, ModuleNotFoundError, FileNotFoundError):
        pass
    fallback = Path(__file__).with_name(resource_name)
    if fallback.is_file():
        try:
            return fallback.read_bytes()
        except OSError:
            return None
    return None


def _find_nim_compiler_path_args(nim_cmd: str = "nim") -> list[str]:
    """Return ``--path:<dir>`` compiler flags for directories containing ``compiler/ast.nim``."""
    resolved_nim = shutil.which(nim_cmd)
    if not resolved_nim:
        return []
    candidates: list[Path] = []
    try:
        real_bin = Path(resolved_nim).resolve()
        candidates.extend([real_bin.parent.parent, real_bin.parent])
    except OSError:
        pass
    libpath = _find_nim_libpath()
    if libpath:
        try:
            real_lib = Path(libpath).resolve()
            candidates.extend([real_lib.parent, real_lib])
        except OSError:
            pass
    seen: set[str] = set()
    path_args: list[str] = []
    for cand in candidates:
        cand_str = str(cand)
        if cand_str not in seen and (cand / "compiler" / "ast.nim").is_file():
            seen.add(cand_str)
            path_args.append(f"--path:{cand_str}")
    return path_args


@functools.lru_cache(maxsize=4)
def _get_nim_ast_helper_binary(nim_executable: str | None = None) -> Path | None:
    """Compile and cache ``_nim_ast_helper.nim`` under ``~/.cache/headerkit/bin/``."""
    nim_cmd = nim_executable or "nim"
    if not shutil.which(nim_cmd):
        return None
    src_bytes = _load_backend_resource_bytes("_nim_ast_helper.nim")
    if not src_bytes:
        return None
    try:
        ver_proc = subprocess.run(
            [nim_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ver_str = ver_proc.stdout.splitlines()[0] if ver_proc.returncode == 0 and ver_proc.stdout else "unknown"
        digest = hashlib.sha256(src_bytes + ver_str.encode("utf-8")).hexdigest()[:16]
        cache_dir = Path(os.environ.get("HEADERKIT_CACHE_DIR", Path.home() / ".cache" / "headerkit")) / "bin"
        cache_dir.mkdir(parents=True, exist_ok=True)
        exe_suffix = ".exe" if os.name == "nt" else ""
        bin_path = cache_dir / f"nim_ast_helper_{digest}{exe_suffix}"
        if bin_path.is_file() and os.access(bin_path, os.X_OK):
            return bin_path

        compiler_path_args = _find_nim_compiler_path_args(nim_cmd)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_src = Path(tmpdir) / "nim_ast_helper.nim"
            tmp_src.write_bytes(src_bytes)
            tmp_bin = Path(tmpdir) / f"nim_ast_helper{exe_suffix}"
            compile_res = subprocess.run(
                [
                    nim_cmd,
                    "c",
                    "-d:release",
                    "--hints:off",
                    "--warnings:off",
                    *compiler_path_args,
                    f"-o:{tmp_bin}",
                    str(tmp_src),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if compile_res.returncode == 0 and tmp_bin.is_file():
                shutil.move(str(tmp_bin), str(bin_path))
                bin_path.chmod(0o755)
                return bin_path
            logger.warning("Failed to compile _nim_ast_helper.nim: %s", compile_res.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Error preparing _nim_ast_helper binary: %s", exc)
    return None


def _run_nim_ast_helper(
    nim_path: str | Path,
    code: str | None = None,
    *,
    nim_executable: str | None = None,
) -> dict[str, Any] | None:
    """Execute the compiled ``_nim_ast_helper`` binary on ``nim_path`` (or ``code`` via stdin).

    Falls back to ``nim e`` (NimScript ``std/macros`` ``parseStmt`` mode) when
    ``compiler/ast.nim`` is unavailable on a binary-only Nim installation.
    """
    helper_bin = _get_nim_ast_helper_binary(nim_executable)
    if helper_bin is not None:
        try:
            cmd = [str(helper_bin), str(nim_path)]
            if code is not None:
                cmd.append("--stdin")
            res = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, dict):
                    return data
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("Failed running _nim_ast_helper on %s: %s", nim_path, exc)

    nim_cmd = nim_executable or "nim"
    if not shutil.which(nim_cmd):
        return None
    src_bytes = _load_backend_resource_bytes("_nim_ast_helper.nim")
    if not src_bytes:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_nims = Path(tmpdir) / "nim_ast_helper.nims"
            tmp_nims.write_bytes(src_bytes)
            cmd = [nim_cmd, "e", "--hints:off", "--warnings:off", str(tmp_nims), str(nim_path)]
            if code is not None:
                tmp_code = Path(tmpdir) / "input_code.nim"
                tmp_code.write_text(code, encoding="utf-8")
                cmd.extend(["--code-file", str(tmp_code)])
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, dict):
                    return data
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("Failed running NimScript _nim_ast_helper fallback on %s: %s", nim_path, exc)
    return None


def extract_nim_ast_data(
    filename: str | Path,
    *,
    code: str | None = None,
    nim_executable: str | None = None,
) -> dict[str, Any] | None:
    """Extract structured Nim compiler AST metadata from a ``.nim`` source file or code string.

    Compiles and invokes HeaderKit's ``_nim_ast_helper.nim`` compiler AST extractor
    (using ``compiler/[ast, parser, idents, options, lineinfos, renderer]``) and returns
    the parsed JSON dictionary with schema:

    - ``"path"``: ``str`` — target ``.nim`` file path.
    - ``"included_files"``: ``list[str]`` — recursively resolved ``include`` file paths.
    - ``"declarations"``: ``list[dict[str, Any]]`` — extracted declaration items with
      ``"kind"`` in ``{"routine", "object", "enum", "typedef", "const", "var"}`` (representing
      procs/funcs/routines, object/tuple types, enums, typedefs/distinct types, and constants/vars),
      including ``name``, ``c_name``, ``has_exportc``, ``exported``, ``is_deprecated``,
      ``attributes``, ``parameters``, ``return_type``, ``doc``, ``file``, ``line``, and ``column``.

    Returns ``None`` if the ``nim`` compiler is unavailable or AST extraction fails.
    """
    return _run_nim_ast_helper(Path(filename), code=code, nim_executable=nim_executable)


def _lower_nim_type_json(
    type_json: Mapping[str, Any] | None,
    type_name_map: Mapping[str, str] | None = None,
) -> tuple[TypeExpr, str, bool]:
    """Convert a JSON type object emitted by ``_nim_ast_helper.nim`` into ``(TypeExpr, source_type, is_bool)``."""
    if not type_json or not isinstance(type_json, Mapping):
        return CType("void"), "void", False
    kind = str(type_json.get("kind", "ctype"))
    src_type = str(type_json.get("source_type") or type_json.get("name") or "void")

    if kind == "pointer":
        pointee_expr, _, _ = _lower_nim_type_json(type_json.get("pointee"), type_name_map)
        return Pointer(pointee=pointee_expr), src_type, False

    if kind == "distinct":
        under_expr, _, is_b = _lower_nim_type_json(type_json.get("underlying"), type_name_map)
        return under_expr, src_type, is_b

    if kind == "array":
        elem_expr, _, _ = _lower_nim_type_json(type_json.get("element_type"), type_name_map)
        raw_size = type_json.get("size")
        if raw_size is None:
            return Pointer(pointee=elem_expr), src_type, False
        size_val = int(raw_size) if isinstance(raw_size, int) and raw_size >= 0 else None
        return Array(element_type=elem_expr, size=size_val), src_type, False

    if kind == "proc":
        ret_expr, _, _ = _lower_nim_type_json(type_json.get("return_type"), type_name_map)
        params: list[Parameter] = []
        raw_params = type_json.get("parameters")
        if isinstance(raw_params, list):
            for p_item in raw_params:
                if not isinstance(p_item, Mapping):
                    continue
                p_name = p_item.get("name")
                p_expr, p_src, p_bool = _lower_nim_type_json(p_item.get("type"), type_name_map)
                overrides = {"pyi": "bool"} if p_bool else {}
                params.append(
                    Parameter(
                        name=str(p_name) if p_name else None,
                        type=p_expr,
                        type_overrides=overrides,
                        source_meta=SourceMeta(
                            source_language="nim",
                            source_name=str(p_name) if p_name else None,
                            source_type=p_src,
                        ),
                    )
                )
        cc_raw = type_json.get("calling_convention")
        cc = str(cc_raw) if isinstance(cc_raw, str) and cc_raw else None
        return FunctionPointer(return_type=ret_expr, parameters=params, calling_convention=cc), src_type, False

    raw_name = str(type_json.get("name") or src_type).strip()
    if raw_name in ("cstring", "NCSTRING"):
        return Pointer(pointee=CType("char", is_elaborated=False)), src_type, False
    if raw_name == "pointer":
        return Pointer(pointee=CType("void", is_elaborated=False)), src_type, False
    if raw_name == "string":
        return Pointer(pointee=CType("char", is_elaborated=False)), src_type, False
    is_bool = raw_name in ("bool", "NIM_BOOL")
    mapped = _NIM_SOURCE_SCALAR_MAP.get(raw_name)
    if mapped is not None:
        return CType(mapped), src_type, is_bool
    unmangled = _unmangle_nim_type_name(raw_name)
    if type_name_map and unmangled in type_name_map:
        unmangled = type_name_map[unmangled]
    return CType(unmangled), src_type, False


def _normalize_meta_attributes(raw_attrs: Any) -> tuple[tuple[str, str], ...]:
    """Normalize a list/tuple of pragma strings or (key, val) pairs into ``tuple[tuple[str, str], ...]``."""
    if not isinstance(raw_attrs, (list, tuple)):
        return ()
    out: list[tuple[str, str]] = []
    for item in raw_attrs:
        if isinstance(item, tuple) and len(item) == 2:
            out.append((str(item[0]), str(item[1])))
        elif isinstance(item, str) and item:
            if ":" in item:
                k, _, v = item.partition(":")
                out.append((k.strip(), v.strip()))
            else:
                out.append((item, ""))
    return tuple(out)


def _ast_json_to_declarations(
    ast_data: Mapping[str, Any],
    *,
    include_unexported: bool = False,
) -> list[Declaration]:
    """Convert the JSON output of ``_nim_ast_helper`` into HeaderKit IR declarations."""
    declarations: list[Declaration] = []
    raw_decls = ast_data.get("declarations")
    if not isinstance(raw_decls, list):
        return declarations

    type_name_map: dict[str, str] = {}
    for item in raw_decls:
        if isinstance(item, Mapping) and str(item.get("kind", "")) in ("object", "enum", "typedef"):
            nim_n = str(item.get("name") or "")
            c_n = str(item.get("c_name") or nim_n)
            if nim_n and c_n and nim_n != c_n:
                type_name_map[nim_n] = c_n

    for item in raw_decls:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind", ""))
        nim_name = str(item.get("name") or "")
        c_name = str(item.get("c_name") or nim_name)
        has_exportc = bool(item.get("has_exportc", False))
        exported = bool(item.get("exported", False))
        if not include_unexported and not (exported or has_exportc):
            continue

        file_str = str(item.get("file") or ast_data.get("path") or "input.nim")
        line_num = int(item.get("line") or 1)
        col_num = int(item.get("column") or 1)
        loc = SourceLocation(file=file_str, line=line_num, column=col_num)

        attrs = _normalize_meta_attributes(item.get("attributes"))
        raw_doc = item.get("doc")
        doc_str = str(raw_doc).strip() if isinstance(raw_doc, str) and raw_doc.strip() else None
        is_dep = bool(item.get("is_deprecated", False))

        if kind == "routine":
            ret_expr, ret_src, ret_bool = _lower_nim_type_json(item.get("return_type"), type_name_map)
            params: list[Parameter] = []
            raw_params = item.get("parameters")
            if isinstance(raw_params, list):
                for p_item in raw_params:
                    if not isinstance(p_item, Mapping):
                        continue
                    p_name = str(p_item.get("name") or "")
                    p_expr, p_src, p_bool = _lower_nim_type_json(p_item.get("type"), type_name_map)
                    p_def = p_item.get("default")
                    p_overrides = {"pyi": "bool"} if p_bool else {}
                    params.append(
                        Parameter(
                            name=p_name or None,
                            type=p_expr,
                            default_value=str(p_def) if p_def is not None else None,
                            type_overrides=p_overrides,
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name=p_name or None,
                                source_type=p_src,
                                module=file_str,
                            ),
                        )
                    )
            cc_raw = item.get("calling_convention")
            cc = str(cc_raw) if isinstance(cc_raw, str) and cc_raw else None
            fn_overrides = {"pyi": "bool"} if ret_bool else {}
            declarations.append(
                Function(
                    name=c_name,
                    return_type=ret_expr,
                    parameters=params,
                    calling_convention=cc,
                    location=loc,
                    doc=doc_str,
                    is_deprecated=is_dep,
                    type_overrides=fn_overrides,
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name=nim_name,
                        source_type=ret_src,
                        attributes=attrs,
                        is_exported=exported or has_exportc,
                        module=file_str,
                    ),
                )
            )

        elif kind == "object":
            fields: list[Field] = []
            raw_fields = item.get("fields")
            if isinstance(raw_fields, list):
                for f_item in raw_fields:
                    if not isinstance(f_item, Mapping):
                        continue
                    f_nim_name = str(f_item.get("name") or "")
                    f_c_name = str(f_item.get("c_name") or f_nim_name)
                    f_expr, f_src, f_bool = _lower_nim_type_json(f_item.get("type"), type_name_map)
                    f_bw = f_item.get("bit_width")
                    f_doc_raw = f_item.get("doc")
                    f_doc = str(f_doc_raw).strip() if isinstance(f_doc_raw, str) and f_doc_raw.strip() else None
                    f_attrs = _normalize_meta_attributes(f_item.get("attributes"))
                    f_overrides = {"pyi": "bool"} if f_bool else {}
                    fields.append(
                        Field(
                            name=f_c_name,
                            type=f_expr,
                            bit_width=int(f_bw) if isinstance(f_bw, int) else None,
                            type_overrides=f_overrides,
                            doc=f_doc,
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name=f_nim_name,
                                source_type=f_src,
                                attributes=f_attrs,
                                is_exported=bool(f_item.get("exported", False)),
                                module=file_str,
                            ),
                        )
                    )
            declarations.append(
                Struct(
                    name=c_name,
                    fields=fields,
                    is_union=bool(item.get("is_union", False)),
                    is_packed=bool(item.get("is_packed", False)),
                    is_typedef=True,
                    location=loc,
                    doc=doc_str,
                    is_deprecated=is_dep,
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name=nim_name,
                        source_type="tuple" if item.get("is_tuple") else "object",
                        attributes=attrs,
                        is_exported=exported or has_exportc,
                        module=file_str,
                    ),
                )
            )

        elif kind == "enum":
            values: list[EnumValue] = []
            raw_vals = item.get("values")
            if isinstance(raw_vals, list):
                for v_item in raw_vals:
                    if not isinstance(v_item, Mapping):
                        continue
                    v_name = str(v_item.get("name") or "")
                    v_val = v_item.get("value")
                    v_doc_raw = v_item.get("doc")
                    v_doc = str(v_doc_raw).strip() if isinstance(v_doc_raw, str) and v_doc_raw.strip() else None
                    values.append(
                        EnumValue(
                            name=v_name,
                            value=int(v_val) if isinstance(v_val, int) else v_val,
                            doc=v_doc,
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name=v_name,
                                module=file_str,
                            ),
                        )
                    )
            declarations.append(
                Enum(
                    name=c_name,
                    values=values,
                    is_typedef=True,
                    underlying_type="int32_t",
                    location=loc,
                    doc=doc_str,
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name=nim_name,
                        source_type="enum",
                        attributes=attrs,
                        is_exported=exported or has_exportc,
                        module=file_str,
                    ),
                )
            )

        elif kind == "typedef":
            under_expr, under_src, under_bool = _lower_nim_type_json(item.get("underlying_type"))
            td_overrides = {"pyi": "bool"} if under_bool else {}
            declarations.append(
                Typedef(
                    name=c_name,
                    underlying_type=under_expr,
                    location=loc,
                    doc=doc_str,
                    is_deprecated=is_dep,
                    type_overrides=td_overrides,
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name=nim_name,
                        source_type=under_src,
                        attributes=attrs,
                        is_exported=exported or has_exportc,
                        module=file_str,
                    ),
                )
            )

        elif kind == "const":
            c_type_raw = item.get("type")
            c_type_expr: CType | None = None
            c_src_type: str | None = None
            if isinstance(c_type_raw, Mapping):
                lowered_t, c_src_type, _ = _lower_nim_type_json(c_type_raw)
                if isinstance(lowered_t, CType):
                    c_type_expr = lowered_t
            raw_val = item.get("value")
            val_kind = str(item.get("value_kind") or "")
            val_out: int | float | str | None
            if val_kind == "bool":
                val_out = int(bool(raw_val))
            elif isinstance(raw_val, (int, float, str)):
                val_out = raw_val
            else:
                val_out = None
            declarations.append(
                Constant(
                    name=c_name,
                    value=val_out,
                    type=c_type_expr,
                    is_macro=False,
                    location=loc,
                    doc=doc_str,
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name=nim_name,
                        source_type=c_src_type,
                        attributes=attrs,
                        is_exported=exported or has_exportc,
                        module=file_str,
                    ),
                )
            )

        elif kind == "var":
            v_expr, v_src, v_bool = _lower_nim_type_json(item.get("type"))
            v_overrides = {"pyi": "bool"} if v_bool else {}
            declarations.append(
                Variable(
                    name=c_name,
                    type=v_expr,
                    location=loc,
                    doc=doc_str,
                    is_deprecated=is_dep,
                    type_overrides=v_overrides,
                    source_meta=SourceMeta(
                        source_language="nim",
                        source_name=nim_name,
                        source_type=v_src,
                        attributes=attrs,
                        is_exported=exported or has_exportc,
                        module=file_str,
                    ),
                )
            )

    return declarations


@dataclass(frozen=True, slots=True)
class _NimSymbolMeta:
    nim_name: str
    c_name: str
    doc: str | None
    param_names: tuple[str, ...]
    param_nim_types: tuple[str, ...]
    return_nim_type: str | None
    is_deprecated: bool
    attributes: tuple[str, ...]
    source_file: str | None


def _detect_nim_project_root(nim_source_path: Path) -> Path:
    """Walk upward from ``nim_source_path`` to locate a directory containing a ``*.nimble`` file."""
    resolved = nim_source_path.resolve()
    curr = resolved.parent if resolved.is_file() else resolved
    for parent in (curr, *curr.parents):
        try:
            if any(p.suffix == ".nimble" for p in parent.iterdir() if p.is_file()):
                return parent
        except OSError:
            continue
    return curr


def clean_nim_c_unit(
    unit: SourceUnit,
    *,
    filtered_functions: Sequence[str] | None = None,
    keep_nim_main: bool = False,
    nim_source_path: str | Path | None = None,
    nim_source_code: str | None = None,
    jsondoc_path: str | Path | None = None,
    jsondoc_entries: Sequence[Mapping[str, Any]] | None = None,
    doc_format: str = "auto",
) -> SourceUnit:
    """Clean and enrich a ``SourceUnit`` parsed from a Nim ``--header`` C file.

    1. Strips Nim runtime internals (``NimMain``, ``signalHandler``, ``nimFrame``, ``eqdestroy_``, etc.).
    2. Normalizes ``nimbase.h`` scalar types (``NI``, ``NU``, ``NF``, ``NCSTRING``, ``NIM_BOOL``),
       unmangles ``tyObject_<Name>__<hash>`` and ``tyEnum_<Name>__<hash>``, strips ``_p0`` /
       ```gensym0`` / ``_colon_`` suffixes, and inlines ``tyProc__<hash>`` callbacks.
    3. Correlates declarations with ``_nim_ast_helper.nim`` and/or ``jsondoc`` metadata to
       restore exact parameter names, docstrings, deprecation flags, and ``SourceMeta``.
    4. Runs ``DocParserTransform`` to populate ``parsed_doc`` when docstrings exist.
    """
    base_filtered = set(_NIM_INTERNAL_EXACT_NAMES)
    if keep_nim_main:
        base_filtered.discard("NimMain")
    if filtered_functions is not None:
        base_filtered.update(filtered_functions)

    def _is_internal(name: str | None) -> bool:
        if not name:
            return False
        unmangled = _unmangle_nim_type_name(name)
        for cand in (name, unmangled):
            if cand in base_filtered or cand.startswith(_NIM_INTERNAL_PREFIXES):
                return True
        return False

    typroc_map: dict[str, FunctionPointer] = {}
    for decl in unit.declarations:
        if isinstance(decl, Typedef) and decl.name and _is_typroc_name(decl.name):
            underlying = decl.underlying_type
            fp: FunctionPointer | None = None
            if isinstance(underlying, FunctionPointer):
                fp = underlying
            elif isinstance(underlying, Pointer) and isinstance(underlying.pointee, FunctionPointer):
                fp = underlying.pointee
            if fp is not None:
                cleaned_fp = _rewrite_nim_type(fp, typroc_map)
                if isinstance(cleaned_fp, FunctionPointer):
                    typroc_map[decl.name] = cleaned_fp

    cleaned_decls: list[Declaration] = []
    seen_typedefs: set[tuple[str, str, bool | None]] = set()

    for decl in unit.declarations:
        if isinstance(decl, Function):
            if _is_internal(decl.name):
                continue
            was_nim_bool = _is_direct_nim_bool(decl.return_type)
            new_params: list[Parameter] = []
            for p in decl.parameters:
                p_bool = _is_direct_nim_bool(p.type)
                p_name = _clean_nim_identifier(p.name)
                p_type = _rewrite_nim_type(p.type, typroc_map)
                p_overrides = dict(p.type_overrides)
                if p_bool:
                    p_overrides.setdefault("pyi", "bool")
                new_params.append(
                    replace(
                        p,
                        name=p_name,
                        type=p_type,
                        type_overrides=p_overrides,
                        source_meta=p.source_meta
                        or SourceMeta(
                            source_language="nim",
                            source_name=p_name,
                            source_type=str(p.type),
                        ),
                    )
                )
            new_ret = _rewrite_nim_type(decl.return_type, typroc_map)
            fn_overrides = dict(decl.type_overrides)
            if was_nim_bool:
                fn_overrides.setdefault("pyi", "bool")
            cleaned_decls.append(
                replace(
                    decl,
                    return_type=new_ret,
                    parameters=new_params,
                    type_overrides=fn_overrides,
                    source_meta=decl.source_meta
                    or SourceMeta(
                        source_language="nim",
                        source_name=decl.name,
                        source_type=str(decl.return_type),
                    ),
                )
            )

        elif isinstance(decl, Struct):
            if _is_internal(decl.name):
                continue
            new_fields: list[Field] = []
            for f in decl.fields:
                f_bool = _is_direct_nim_bool(f.type)
                f_name = _clean_nim_field_name(f.name) if f.name else f.name
                f_type = _rewrite_nim_type(f.type, typroc_map)
                f_overrides = dict(f.type_overrides)
                if f_bool:
                    f_overrides.setdefault("pyi", "bool")
                new_fields.append(
                    replace(
                        f,
                        name=f_name,
                        type=f_type,
                        type_overrides=f_overrides,
                        source_meta=f.source_meta
                        or SourceMeta(
                            source_language="nim",
                            source_name=f_name,
                            source_type=str(f.type),
                        ),
                    )
                )
            new_s_name = _unmangle_nim_type_name(decl.name) if decl.name else decl.name
            cleaned_decls.append(
                replace(
                    decl,
                    name=new_s_name,
                    fields=new_fields,
                    source_meta=decl.source_meta
                    or SourceMeta(
                        source_language="nim",
                        source_name=new_s_name,
                        source_type=decl.name,
                    ),
                )
            )

        elif isinstance(decl, Enum):
            if _is_internal(decl.name):
                continue
            new_e_name = _unmangle_nim_type_name(decl.name) if decl.name else decl.name
            new_under = (
                _NIM_SCALAR_TYPE_MAP.get(decl.underlying_type, decl.underlying_type) if decl.underlying_type else None
            )
            cleaned_decls.append(
                replace(
                    decl,
                    name=new_e_name,
                    underlying_type=new_under,
                    source_meta=decl.source_meta
                    or SourceMeta(
                        source_language="nim",
                        source_name=new_e_name,
                        source_type=decl.name,
                    ),
                )
            )

        elif isinstance(decl, Typedef):
            if _is_internal(decl.name) or decl.name in _NIM_BUILTIN_TYPEDEFS or decl.name in typroc_map:
                continue
            was_nim_bool = _is_direct_nim_bool(decl.underlying_type)
            new_td_name = _unmangle_nim_type_name(decl.name)
            new_td_under = _rewrite_nim_type(decl.underlying_type, typroc_map)
            is_elab = new_td_under.is_elaborated if isinstance(new_td_under, CType) else None
            if isinstance(new_td_under, CType) and new_td_under.name == new_td_name and not is_elab:
                continue
            key = (new_td_name, str(new_td_under), is_elab)
            if key in seen_typedefs:
                continue
            seen_typedefs.add(key)
            td_overrides = dict(decl.type_overrides)
            if was_nim_bool:
                td_overrides.setdefault("pyi", "bool")
            cleaned_decls.append(
                replace(
                    decl,
                    name=new_td_name,
                    underlying_type=new_td_under,
                    type_overrides=td_overrides,
                    source_meta=decl.source_meta
                    or SourceMeta(
                        source_language="nim",
                        source_name=new_td_name,
                        source_type=str(decl.underlying_type),
                    ),
                )
            )

        elif isinstance(decl, Variable):
            if _is_internal(decl.name):
                continue
            was_nim_bool = _is_direct_nim_bool(decl.type)
            new_v_type = _rewrite_nim_type(decl.type, typroc_map)
            v_overrides = dict(decl.type_overrides)
            if was_nim_bool:
                v_overrides.setdefault("pyi", "bool")
            cleaned_decls.append(
                replace(
                    decl,
                    type=new_v_type,
                    type_overrides=v_overrides,
                    source_meta=decl.source_meta
                    or SourceMeta(
                        source_language="nim",
                        source_name=decl.name,
                        source_type=str(decl.type),
                    ),
                )
            )

        elif isinstance(decl, Constant):
            if _is_internal(decl.name):
                continue
            cleaned_decls.append(
                replace(
                    decl,
                    source_meta=decl.source_meta
                    or SourceMeta(
                        source_language="nim",
                        source_name=decl.name,
                    ),
                )
            )

        elif isinstance(decl, MacroFunction):
            if _is_internal(decl.name):
                continue
            cleaned_decls.append(decl)

        else:
            cleaned_decls.append(decl)

    # Enrich from Nim AST helper or jsondoc if provided
    func_by_name_and_arity: dict[tuple[str, int], _NimSymbolMeta] = {}
    func_by_name: dict[str, _NimSymbolMeta] = {}
    docs_by_kind_and_name: dict[tuple[str, str], str] = {}
    docs_by_name: dict[str, str] = {}
    deprecated_symbols: set[str] = set()

    def _register_meta(meta: _NimSymbolMeta) -> None:
        arity = len(meta.param_names)
        for k_name in dict.fromkeys((meta.c_name, meta.nim_name)):
            if not k_name:
                continue
            existing = func_by_name_and_arity.get((k_name, arity))
            if existing is None:
                func_by_name_and_arity[(k_name, arity)] = meta
            else:
                func_by_name_and_arity[(k_name, arity)] = _NimSymbolMeta(
                    nim_name=meta.nim_name or existing.nim_name,
                    c_name=meta.c_name or existing.c_name,
                    doc=meta.doc or existing.doc,
                    param_names=meta.param_names or existing.param_names,
                    param_nim_types=meta.param_nim_types or existing.param_nim_types,
                    return_nim_type=meta.return_nim_type or existing.return_nim_type,
                    is_deprecated=meta.is_deprecated or existing.is_deprecated,
                    attributes=meta.attributes or existing.attributes,
                    source_file=meta.source_file or existing.source_file,
                )
            func_by_name[k_name] = func_by_name_and_arity[(k_name, arity)]
            if meta.doc:
                docs_by_kind_and_name[("function", k_name)] = meta.doc
            if meta.is_deprecated:
                deprecated_symbols.add(k_name)

    all_jsondoc_entries: list[Mapping[str, Any]] = list(jsondoc_entries) if jsondoc_entries else []
    if jsondoc_path is not None:
        jp = Path(jsondoc_path)
        if jp.is_file():
            try:
                raw_j = json.loads(jp.read_text(encoding="utf-8"))
                if isinstance(raw_j, list):
                    all_jsondoc_entries.extend(x for x in raw_j if isinstance(x, Mapping))
                elif isinstance(raw_j, Mapping):
                    sub = raw_j.get("entries") or raw_j.get("exports") or raw_j.get("declarations")
                    if isinstance(sub, list):
                        all_jsondoc_entries.extend(x for x in sub if isinstance(x, Mapping))
            except (OSError, ValueError) as exc:
                logger.warning("Failed to load jsondoc file %s: %s", jp, exc)

    for entry in all_jsondoc_entries:
        e_name = entry.get("name")
        if not isinstance(e_name, str) or not e_name:
            continue
        c_raw = entry.get("exportc") or entry.get("c_name")
        c_name = str(c_raw).strip() if isinstance(c_raw, str) and c_raw.strip() else e_name
        is_dep = bool(entry.get("deprecated") or entry.get("is_deprecated"))
        if is_dep:
            deprecated_symbols.add(e_name)
            deprecated_symbols.add(c_name)
        raw_kind = str(entry.get("type") or entry.get("kind") or "").strip()
        cat: str | None = None
        if raw_kind in (
            "skProc",
            "skFunc",
            "skMethod",
            "skIterator",
            "skConverter",
            "skTemplate",
            "proc",
            "func",
            "function",
        ):
            cat = "function"
        elif raw_kind in ("skType", "type"):
            cat = "type"
        elif raw_kind in ("skVar", "skLet", "var", "let", "variable"):
            cat = "variable"
        elif raw_kind in ("skConst", "const", "constant"):
            cat = "constant"

        desc = entry.get("description") or entry.get("doc")
        clean_desc = desc.strip() if isinstance(desc, str) and desc.strip() else None
        if clean_desc:
            for t_name in dict.fromkeys((c_name, e_name)):
                if cat is not None:
                    docs_by_kind_and_name[(cat, t_name)] = clean_desc
                else:
                    docs_by_name[t_name] = clean_desc

        if cat in (None, "function"):
            params_raw = entry.get("parameters") or entry.get("params")
            ret_nim: str | None = None
            sig = entry.get("signature")
            if isinstance(sig, Mapping):
                if params_raw is None:
                    params_raw = sig.get("arguments") or sig.get("parameters") or sig.get("params")
                r_raw = sig.get("return") or sig.get("return_type")
                if isinstance(r_raw, str) and r_raw.strip():
                    ret_nim = r_raw.strip()
            p_names: list[str] = []
            p_types: list[str] = []
            if isinstance(params_raw, list):
                for p in params_raw:
                    if isinstance(p, str):
                        p_names.append(p)
                        p_types.append("")
                    elif isinstance(p, Mapping) and isinstance(p.get("name"), str):
                        p_names.append(str(p["name"]))
                        pt = p.get("type")
                        p_types.append(str(pt).strip() if isinstance(pt, str) else "")
            if cat == "function" or p_names or c_name != e_name:
                _register_meta(
                    _NimSymbolMeta(
                        nim_name=e_name,
                        c_name=c_name,
                        doc=clean_desc,
                        param_names=tuple(p_names),
                        param_nim_types=tuple(p_types),
                        return_nim_type=ret_nim,
                        is_deprecated=is_dep,
                        attributes=(),
                        source_file=None,
                    )
                )

    if nim_source_path is not None or nim_source_code is not None:
        ast_data = _run_nim_ast_helper(nim_source_path or "input.nim", code=nim_source_code)
        if ast_data is not None:
            for item in ast_data.get("declarations", []):
                if not isinstance(item, Mapping):
                    continue
                kind = str(item.get("kind", ""))
                n_name = str(item.get("name") or "")
                c_name = str(item.get("c_name") or n_name)
                doc_val = item.get("doc")
                clean_doc = str(doc_val).strip() if isinstance(doc_val, str) and str(doc_val).strip() else None
                is_dep = bool(item.get("is_deprecated", False))
                if is_dep:
                    deprecated_symbols.add(n_name)
                    deprecated_symbols.add(c_name)
                if kind == "routine":
                    p_names_list: list[str] = []
                    p_types_list: list[str] = []
                    for p_obj in item.get("parameters", []):
                        if isinstance(p_obj, Mapping):
                            p_names_list.append(str(p_obj.get("name") or ""))
                            pt_obj = p_obj.get("type")
                            p_types_list.append(
                                str(pt_obj.get("source_type") or pt_obj.get("name") or "")
                                if isinstance(pt_obj, Mapping)
                                else ""
                            )
                    ret_obj = item.get("return_type")
                    ret_nim_str = (
                        str(ret_obj.get("source_type") or ret_obj.get("name") or "")
                        if isinstance(ret_obj, Mapping)
                        else None
                    )
                    raw_attrs = item.get("attributes")
                    attrs = tuple(str(a) for a in raw_attrs) if isinstance(raw_attrs, list) else ()
                    _register_meta(
                        _NimSymbolMeta(
                            nim_name=n_name,
                            c_name=c_name,
                            doc=clean_doc,
                            param_names=tuple(p_names_list),
                            param_nim_types=tuple(p_types_list),
                            return_nim_type=ret_nim_str,
                            is_deprecated=is_dep,
                            attributes=attrs,
                            source_file=str(item.get("file") or ""),
                        )
                    )
                elif kind in ("object", "enum", "typedef"):
                    if clean_doc:
                        docs_by_kind_and_name[("type", c_name)] = clean_doc
                        docs_by_kind_and_name[("type", n_name)] = clean_doc
                elif kind == "const":
                    if clean_doc:
                        docs_by_kind_and_name[("constant", c_name)] = clean_doc
                        docs_by_kind_and_name[("constant", n_name)] = clean_doc
                elif kind == "var":
                    if clean_doc:
                        docs_by_kind_and_name[("variable", c_name)] = clean_doc
                        docs_by_kind_and_name[("variable", n_name)] = clean_doc

    def _lookup_doc(category: str, name: str | None, current: str | None) -> str | None:
        if not name:
            return current
        if (category, name) in docs_by_kind_and_name:
            return docs_by_kind_and_name[(category, name)]
        if name in docs_by_name:
            return docs_by_name[name]
        return current

    enriched_decls: list[Declaration] = []
    for decl in cleaned_decls:
        if isinstance(decl, Function):
            arity = len(decl.parameters)
            meta = func_by_name_and_arity.get((decl.name, arity))
            if meta is None:
                fb = func_by_name.get(decl.name)
                if fb is not None and (not fb.param_names or len(fb.param_names) == arity):
                    meta = fb
            doc = (meta.doc if meta and meta.doc else None) or _lookup_doc("function", decl.name, decl.doc)
            new_params = list(decl.parameters)
            if meta and meta.param_names and len(meta.param_names) == len(decl.parameters):
                updated_params: list[Parameter] = []
                for idx, (p, p_name) in enumerate(zip(decl.parameters, meta.param_names, strict=True)):
                    nim_t = meta.param_nim_types[idx] if idx < len(meta.param_nim_types) else ""
                    p_ov = dict(p.type_overrides)
                    if nim_t == "bool":
                        p_ov.setdefault("pyi", "bool")
                    updated_params.append(
                        replace(
                            p,
                            name=p_name,
                            type_overrides=p_ov,
                            source_meta=SourceMeta(
                                source_language="nim",
                                source_name=p_name,
                                source_type=nim_t or (p.source_meta.source_type if p.source_meta else None),
                                module=meta.source_file or (p.source_meta.module if p.source_meta else None),
                            ),
                        )
                    )
                new_params = updated_params
            is_dep = decl.is_deprecated or bool(meta and meta.is_deprecated) or (decl.name in deprecated_symbols)
            fn_ov = dict(decl.type_overrides)
            if meta and meta.return_nim_type == "bool":
                fn_ov.setdefault("pyi", "bool")
            s_meta = decl.source_meta
            if meta is not None:
                s_meta = SourceMeta(
                    source_language="nim",
                    source_name=meta.nim_name,
                    source_type=meta.return_nim_type or (s_meta.source_type if s_meta else None),
                    attributes=_normalize_meta_attributes(meta.attributes) or (s_meta.attributes if s_meta else ()),
                    is_exported=True,
                    module=meta.source_file or (s_meta.module if s_meta else None),
                )
            enriched_decls.append(
                replace(
                    decl,
                    doc=doc,
                    parameters=new_params,
                    is_deprecated=is_dep,
                    type_overrides=fn_ov,
                    source_meta=s_meta,
                )
            )
        elif isinstance(decl, Struct):
            doc = _lookup_doc("type", decl.name, decl.doc)
            is_dep = decl.is_deprecated or bool(decl.name and decl.name in deprecated_symbols)
            enriched_decls.append(replace(decl, doc=doc, is_deprecated=is_dep))
        elif isinstance(decl, Enum):
            doc = _lookup_doc("type", decl.name, decl.doc)
            enriched_decls.append(replace(decl, doc=doc))
        elif isinstance(decl, Typedef):
            doc = _lookup_doc("type", decl.name, decl.doc)
            is_dep = decl.is_deprecated or (decl.name in deprecated_symbols)
            enriched_decls.append(replace(decl, doc=doc, is_deprecated=is_dep))
        elif isinstance(decl, Variable):
            doc = _lookup_doc("variable", decl.name, decl.doc)
            is_dep = decl.is_deprecated or (decl.name in deprecated_symbols)
            enriched_decls.append(replace(decl, doc=doc, is_deprecated=is_dep))
        elif isinstance(decl, Constant):
            doc = _lookup_doc("constant", decl.name, decl.doc)
            enriched_decls.append(replace(decl, doc=doc))
        else:
            enriched_decls.append(decl)

    result_unit = replace(unit, declarations=enriched_decls)
    if any(getattr(d, "doc", None) for d in result_unit.declarations):
        doc_parser = DocParserTransform(doc_format=doc_format)
        result_unit = doc_parser.transform(result_unit)
    return result_unit


class NimBackend:
    """First-class HeaderKit parser backend for Nim ``.nim`` sources and ``nim c --header`` headers."""

    description: ClassVar[str] = "Nim compiler AST and --header C parser with SourceMeta & ParsedDoc"
    supported_languages: frozenset[str] = frozenset({"nim", "c"})
    supported_classifications: frozenset[str] = frozenset({"source", "header"})
    supports_auto_install: ClassVar[bool] = False

    def __init__(
        self,
        *,
        c_backend: str = "auto",
        doc_format: str = "auto",
        keep_nim_main: bool = False,
        filtered_functions: Sequence[str] | None = None,
        include_unexported: bool = False,
    ) -> None:
        self.c_backend = c_backend
        self.doc_format = doc_format
        self.keep_nim_main = keep_nim_main
        self.filtered_functions = list(filtered_functions) if filtered_functions else []
        self.include_unexported = include_unexported

    @classmethod
    def c_backend_options(cls) -> dict[str, Any]:
        """Return C parser backend options required for Nim ``--header`` C files."""
        return {
            "storage_macros": sorted(_NIM_STORAGE_MACROS),
            "callconv_macros": sorted(_NIM_CALLCONV_MACROS),
            "callconv_ptr_macros": sorted(_NIM_CALLCONV_PTR_MACROS),
            "system_headers": ["nimbase.h"],
            "skip_function_bodies": True,
        }

    @classmethod
    def prepare_c_parse_args(
        cls,
        code: str,
        filename: str,
        include_dirs: list[str] | None = None,
        extra_args: list[str] | None = None,
    ) -> tuple[list[str], list[str], dict[str, Any]]:
        """Resolve ``nimbase.h`` include path, ``NIM_INTBITS``, and CST macro sets before C parsing."""
        inc = list(include_dirs or [])
        args = list(extra_args or [])
        if "NIM_INTBITS" not in code and not any("NIM_INTBITS" in a for a in args):
            args.append(f"-DNIM_INTBITS={struct.calcsize('P') * 8}")
        if not _has_nimbase_in_dirs(inc, args, filename):
            nim_libpath = _find_nim_libpath()
            if nim_libpath is not None:
                args.append(f"-isystem{nim_libpath}")
        return inc, args, cls.c_backend_options()

    def is_available(self) -> bool:
        """Return True if the ``nim`` compiler is available on PATH or a C backend is available."""
        return (
            shutil.which("nim") is not None or is_backend_available("tree-sitter") or is_backend_available("libclang")
        )

    @property
    def name(self) -> str:
        return "nim"

    @property
    def supports_macros(self) -> bool:
        return True

    @property
    def supports_cpp(self) -> bool:
        return False

    @property
    def follows_includes(self) -> bool:
        return True

    @property
    def supports_compiler_args(self) -> bool:
        return True

    def get_name(self) -> str:
        return "nim"

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
        allowlist: list[str] | None = None,
        denylist: list[str] | None = None,
        nim_source_path: str | Path | None = None,
        jsondoc_path: str | Path | None = None,
        jsondoc_entries: Sequence[Mapping[str, Any]] | None = None,
        keep_nim_main: bool | None = None,
        filtered_functions: Sequence[str] | None = None,
        include_unexported: bool | None = None,
        doc_format: str | None = None,
        **kwargs: Any,
    ) -> SourceUnit:
        """Parse either a ``.nim`` source file or a Nim ``--header`` ``.h`` file into HeaderKit IR."""
        _ = (allowlist, denylist, kwargs)
        eff_keep_main = self.keep_nim_main if keep_nim_main is None else keep_nim_main
        eff_filtered = self.filtered_functions if filtered_functions is None else list(filtered_functions)
        eff_unexported = self.include_unexported if include_unexported is None else include_unexported
        eff_doc_format = self.doc_format if doc_format is None else doc_format

        ext = os.path.splitext(filename)[1].lower()
        is_nim_source = ext in (".nim", ".nims")

        if is_nim_source:
            ast_data = _run_nim_ast_helper(filename, code=code)
            if ast_data is None:
                raise RuntimeError(f"NimBackend requires the 'nim' compiler on PATH to parse {filename!r}")
            decls = _ast_json_to_declarations(ast_data, include_unexported=eff_unexported)
            inc_files = {str(x) for x in ast_data.get("included_files", []) if isinstance(x, str)}
            unit = SourceUnit(
                path=filename,
                declarations=decls,
                included_headers=inc_files,
            )
            if jsondoc_path is not None or jsondoc_entries is not None:
                unit = clean_nim_c_unit(
                    unit,
                    filtered_functions=eff_filtered,
                    keep_nim_main=eff_keep_main,
                    jsondoc_path=jsondoc_path,
                    jsondoc_entries=jsondoc_entries,
                    doc_format=eff_doc_format,
                )
            elif any(getattr(d, "doc", None) for d in unit.declarations):
                unit = DocParserTransform(doc_format=eff_doc_format).transform(unit)
            return unit

        # Otherwise, parse as a Nim `--header` C header
        inc, args, b_opts = self.prepare_c_parse_args(code, filename, include_dirs, extra_args)
        c_backend_name = self.c_backend
        if c_backend_name == "auto":
            c_backend_name = "libclang" if is_backend_available("libclang") else "tree-sitter"
        c_parser: ParserBackend = get_backend(c_backend_name)
        raw_unit = c_parser.parse(
            code,
            filename,
            include_dirs=inc,
            extra_args=args,
            use_default_includes=use_default_includes,
            recursive_includes=recursive_includes,
            max_depth=max_depth,
            project_prefixes=project_prefixes,
            **b_opts,
        )
        return clean_nim_c_unit(
            raw_unit,
            filtered_functions=eff_filtered,
            keep_nim_main=eff_keep_main,
            nim_source_path=nim_source_path,
            jsondoc_path=jsondoc_path,
            jsondoc_entries=jsondoc_entries,
            doc_format=eff_doc_format,
        )


@hook("get_backend", language="nim", priority=Priority.STANDARD)
def _nim_get_backend_by_lang(context: PipelineContext | None = None) -> ParserBackend:
    _ = context
    return NimBackend()


@hook("parse_unit", language="nim", priority=Priority.STANDARD)
def _nim_parse_unit_by_lang(
    code: str,
    filename: str = "input.nim",
    include_dirs: list[str] | None = None,
    extra_args: list[str] | None = None,
    *,
    use_default_includes: bool = True,
    recursive_includes: bool = True,
    max_depth: int = 10,
    project_prefixes: tuple[str, ...] | None = None,
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    _ = context
    inst = NimBackend()
    if not inst.is_available():
        return None
    return inst.parse(
        code,
        filename,
        include_dirs=include_dirs,
        extra_args=extra_args,
        use_default_includes=use_default_includes,
        recursive_includes=recursive_includes,
        max_depth=max_depth,
        project_prefixes=project_prefixes,
        **kwargs,
    )


register_backend("nim", NimBackend)

__all__ = [
    "NimBackend",
    "clean_nim_c_unit",
    "extract_nim_ast_data",
]
