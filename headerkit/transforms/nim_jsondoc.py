"""Nim jsondoc enrichment transform for HeaderKit IR."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from headerkit.hooks import PipelineContext
from headerkit.ir import (
    Constant,
    Enum,
    Function,
    Parameter,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
)
from headerkit.transforms.filter import PipelineFilter, _normalize_fingerprint_value

logger = logging.getLogger(__name__)


_FUNC_ENTRY_KINDS: frozenset[str] = frozenset(
    {
        "skProc",
        "skFunc",
        "skMethod",
        "skIterator",
        "skConverter",
        "skMacro",
        "skTemplate",
        "proc",
        "func",
        "method",
        "iterator",
        "converter",
        "macro",
        "template",
        "function",
    }
)
_TYPE_ENTRY_KINDS: frozenset[str] = frozenset({"skType", "type"})
_VAR_ENTRY_KINDS: frozenset[str] = frozenset({"skVar", "skLet", "var", "let", "variable"})
_CONST_ENTRY_KINDS: frozenset[str] = frozenset({"skConst", "const", "constant"})

_EXPORTC_PRAGMA_RE = re.compile(r"""exportc\s*:\s*"([^"]+)"|exportc\s*=\s*"([^"]+)""")
_DEPRECATED_PRAGMA_RE = re.compile(r"""\bdeprecated\b""")
_INCLUDE_LINE_RE = re.compile(r"^\s*include\s+(.+?)(?:\s*#.*)?$")
_PROC_HEADER_RE = re.compile(
    r"^\s*(?:proc|func|method|iterator|converter|template)\s+"
    r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s*(\*)?"
    r"(?:\s*\[[^\]]*\])?\s*"
    r"\((.*)"
)


def _categorize_entry_kind(raw_kind: Any) -> str | None:
    if not isinstance(raw_kind, str) or not raw_kind.strip():
        return None
    kind = raw_kind.strip()
    if kind in _FUNC_ENTRY_KINDS:
        return "function"
    if kind in _TYPE_ENTRY_KINDS:
        return "type"
    if kind in _VAR_ENTRY_KINDS:
        return "variable"
    if kind in _CONST_ENTRY_KINDS:
        return "constant"
    return None


@dataclass
class _FuncDocMeta:
    """Enrichment metadata for a single Nim proc/func export."""

    nim_name: str
    c_name: str
    doc: str | None = None
    param_names: list[str] = field(default_factory=list)
    param_nim_types: list[str] = field(default_factory=list)
    return_nim_type: str | None = None
    is_deprecated: bool = False


def _detect_nim_project_root(nim_path: Path) -> Path:
    """Walk upward from ``nim_path`` to locate the Nim project root."""
    resolved = nim_path.resolve()
    start_dir = resolved.parent if resolved.is_file() or not resolved.is_dir() else resolved
    for parent in (start_dir, *start_dir.parents):
        try:
            if any(parent.glob("*.nimble")):
                return parent
            if (
                (parent / "nim.cfg").is_file()
                or (parent / "config.nims").is_file()
                or (parent / "pyproject.toml").is_file()
            ):
                return parent
        except OSError:
            continue
    return start_dir


def _parse_include_targets(raw_targets: str) -> list[str]:
    """Parse target paths from a Nim ``include a, "b/c"`` statement."""
    targets: list[str] = []
    for part in raw_targets.split(","):
        cleaned = part.strip().strip('"').strip("'")
        if cleaned:
            targets.append(cleaned)
    return targets


def _resolve_included_nim_file(target: str, current_dir: Path, project_root: Path) -> Path | None:
    """Resolve an ``include`` target relative to ``current_dir`` or ``project_root``."""
    candidates: list[Path] = []
    for base in (current_dir, project_root, project_root / "src"):
        cand = base / target
        candidates.append(cand)
        if cand.suffix != ".nim":
            candidates.append(cand.with_suffix(".nim"))
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    return None


def _collect_constituent_nim_files(entrypoint: Path, project_root: Path) -> list[Path]:
    """Follow ``include`` directives recursively from ``entrypoint`` without running subprocesses."""
    if not entrypoint.is_file():
        return []
    ordered: list[Path] = []
    visited: set[Path] = set()

    def _walk(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited or not resolved.is_file():
            return
        visited.add(resolved)
        ordered.append(resolved)
        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            m = _INCLUDE_LINE_RE.match(line)
            if not m:
                continue
            for target in _parse_include_targets(m.group(1)):
                inc_file = _resolve_included_nim_file(target, resolved.parent, project_root)
                if inc_file is not None:
                    _walk(inc_file)

    _walk(entrypoint)
    return ordered


def _is_included_fragment(nim_file: Path, project_root: Path) -> bool:
    """Return True if ``nim_file`` is included via ``include`` by another ``.nim`` file in the project."""
    target_resolved = nim_file.resolve()
    search_roots = [project_root]
    if (project_root / "src").is_dir():
        search_roots.append(project_root / "src")
    seen_files: set[Path] = set()
    for root in search_roots:
        try:
            candidates = list(root.glob("*.nim")) + list(root.glob("*/*.nim"))
        except OSError:
            continue
        for other in candidates:
            other_res = other.resolve()
            if other_res == target_resolved or other_res in seen_files:
                continue
            seen_files.add(other_res)
            try:
                text = other_res.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                m = _INCLUDE_LINE_RE.match(line)
                if not m:
                    continue
                for inc_target in _parse_include_targets(m.group(1)):
                    resolved_inc = _resolve_included_nim_file(inc_target, other_res.parent, project_root)
                    if resolved_inc == target_resolved:
                        return True
    return False


def _split_top_level(text: str, sep_chars: tuple[str, ...] = (",", ";")) -> list[str]:
    """Split ``text`` by ``sep_chars`` only at nesting depth 0."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_quotes = False
    quote_char = ""
    for ch in text:
        if in_quotes:
            current.append(ch)
            if ch == quote_char:
                in_quotes = False
            continue
        if ch in ('"', "'"):
            in_quotes = True
            quote_char = ch
            current.append(ch)
        elif ch in ("(", "[", "{"):
            depth += 1
            current.append(ch)
        elif ch in (")", "]", "}"):
            depth = max(0, depth - 1)
            current.append(ch)
        elif depth == 0 and ch in sep_chars:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_nim_params(params_str: str) -> tuple[list[str], list[str]]:
    """Parse a Nim parameter list into ``(param_names, param_nim_types)``."""
    names: list[str] = []
    types: list[str] = []
    if not params_str.strip():
        return names, types
    for group in _split_top_level(params_str, (",", ";")):
        if ":" not in group:
            continue
        lhs, rhs = group.split(":", 1)
        # Strip default value '= ...' from rhs at depth 0
        type_part = _split_top_level(rhs, ("=",))[0].strip() if "=" in rhs else rhs.strip()
        for raw_ident in lhs.split(","):
            ident = raw_ident.strip()
            ident = re.sub(r"\{\..*?\.\}", "", ident).strip()
            ident = ident.rstrip("*").strip().strip("`")
            if ident:
                names.append(ident)
                types.append(type_part)
    return names, types


def _extract_pragma_info(nim_name: str, pragma_text: str) -> tuple[str, bool]:
    """Extract ``(exported_c_name, is_deprecated)`` from a Nim ``{. ... .}`` pragma block."""
    c_name = nim_name
    m_exp = _EXPORTC_PRAGMA_RE.search(pragma_text)
    if m_exp:
        raw_c = m_exp.group(1) or m_exp.group(2) or ""
        if raw_c:
            c_name = raw_c.replace("$1", nim_name)
    is_dep = _DEPRECATED_PRAGMA_RE.search(pragma_text) is not None
    return c_name, is_dep


def _parse_nim_source_file(path: Path) -> tuple[list[_FuncDocMeta], dict[tuple[str, str], str], set[str]]:
    """Extract proc metadata, type/var/const docstrings, and deprecated symbols from a ``.nim`` file."""
    funcs: list[_FuncDocMeta] = []
    docs_by_kind_and_name: dict[tuple[str, str], str] = {}
    deprecated_symbols: set[str] = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return funcs, docs_by_kind_and_name, deprecated_symbols

    n = len(lines)
    i = 0
    pending_doc_lines: list[str] = []
    in_type_section = False

    while i < n:
        raw_line = lines[i]
        stripped = raw_line.strip()

        if not stripped:
            pending_doc_lines = []
            i += 1
            continue

        if stripped.startswith("##"):
            doc_text = stripped[2:].strip()
            if doc_text:
                pending_doc_lines.append(doc_text)
            i += 1
            continue

        if stripped.startswith("#"):
            i += 1
            continue

        # Check for proc/func/method/iterator/converter/template declaration
        m_proc = _PROC_HEADER_RE.match(raw_line)
        if m_proc:
            in_type_section = False
            nim_name = m_proc.group(1)
            header_lines = [raw_line]
            # Accumulate multi-line signature until ')' is closed and '=' or end of prototype is reached
            paren_depth = raw_line.count("(") - raw_line.count(")")
            j = i + 1
            while j < n and (
                paren_depth > 0
                or (header_lines[-1].rstrip().endswith((",", ";", "{.", "*")) and "=" not in header_lines[-1])
            ):
                next_l = lines[j]
                if next_l.strip().startswith("#"):
                    j += 1
                    continue
                header_lines.append(next_l)
                paren_depth += next_l.count("(") - next_l.count(")")
                j += 1

            full_sig = " ".join(line.strip() for line in header_lines)
            # Extract parameter substring between first '(' and matching ')'
            open_idx = full_sig.find("(")
            close_idx = -1
            depth = 0
            for idx in range(open_idx, len(full_sig)):
                if full_sig[idx] == "(":
                    depth += 1
                elif full_sig[idx] == ")":
                    depth -= 1
                    if depth == 0:
                        close_idx = idx
                        break

            params_str = full_sig[open_idx + 1 : close_idx] if open_idx >= 0 and close_idx > open_idx else ""
            after_params = full_sig[close_idx + 1 :] if close_idx >= 0 else ""

            pragma_text = ""
            for pm in re.finditer(r"\{\.(.*?)\.\}", full_sig):
                pragma_text += " " + pm.group(1)

            c_name, is_dep = _extract_pragma_info(nim_name, pragma_text)
            if is_dep:
                deprecated_symbols.add(c_name)
                deprecated_symbols.add(nim_name)

            # Extract return type if present before '{.' or '='
            after_no_pragma = re.sub(r"\{\..*?\.\}", "", after_params)
            if "=" in after_no_pragma:
                after_no_pragma = after_no_pragma.split("=", 1)[0]
            ret_nim_type: str | None = None
            if ":" in after_no_pragma:
                ret_nim_type = after_no_pragma.split(":", 1)[1].strip() or None

            param_names, param_nim_types = _parse_nim_params(params_str)

            # Collect body '##' doc comments immediately following the signature
            body_doc_lines: list[str] = []
            k = j
            while k < n:
                b_strip = lines[k].strip()
                if not b_strip:
                    k += 1
                    continue
                if b_strip.startswith("##"):
                    b_doc = b_strip[2:].strip()
                    if b_doc:
                        body_doc_lines.append(b_doc)
                    k += 1
                    continue
                break

            combined_doc_lines = body_doc_lines if body_doc_lines else pending_doc_lines
            doc_str = "\n".join(combined_doc_lines).strip() if combined_doc_lines else None
            pending_doc_lines = []

            funcs.append(
                _FuncDocMeta(
                    nim_name=nim_name,
                    c_name=c_name,
                    doc=doc_str,
                    param_names=param_names,
                    param_nim_types=param_nim_types,
                    return_nim_type=ret_nim_type,
                    is_deprecated=is_dep,
                )
            )
            i = j
            continue

        # Track 'type' section and type declarations
        if re.match(r"^type\b", stripped):
            in_type_section = True
            rest = stripped[4:].strip()
            if not rest:
                i += 1
                continue
            stripped = rest

        if in_type_section and (raw_line.startswith((" ", "\t")) or re.match(r"^[A-Za-z_`]", stripped)):
            if re.match(
                r"^(?:proc|func|method|iterator|converter|template|var|let|const|include|import|from|when)\b", stripped
            ):
                in_type_section = False
            else:
                m_type = re.match(
                    r"^`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\*?(?:\s*\[[^\]]*\])?\s*(?:\{\.(.*?)\.\})?\s*=",
                    stripped,
                )
                if m_type:
                    t_nim_name = m_type.group(1)
                    t_pragma = m_type.group(2) or ""
                    t_c_name, t_dep = _extract_pragma_info(t_nim_name, t_pragma)
                    if t_dep:
                        deprecated_symbols.add(t_c_name)
                        deprecated_symbols.add(t_nim_name)
                    type_doc_lines: list[str] = []
                    k = i + 1
                    while k < n:
                        t_strip = lines[k].strip()
                        if t_strip.startswith("##"):
                            td = t_strip[2:].strip()
                            if td:
                                type_doc_lines.append(td)
                            k += 1
                            continue
                        break
                    use_docs = type_doc_lines if type_doc_lines else pending_doc_lines
                    if use_docs:
                        doc_val = "\n".join(use_docs).strip()
                        docs_by_kind_and_name[("type", t_c_name)] = doc_val
                        docs_by_kind_and_name[("type", t_nim_name)] = doc_val
                    pending_doc_lines = []
                    i += 1
                    continue

        pending_doc_lines = []
        i += 1

    return funcs, docs_by_kind_and_name, deprecated_symbols


class NimJsonDocEnricher(PipelineFilter):
    """Enrich a ``SourceUnit`` with docstrings, parameter names, deprecation flags, and exportc mappings."""

    def __init__(
        self,
        jsondoc_path: Path | str | None = None,
        *,
        nim_source_path: Path | str | None = None,
        nim_source: Path | str | None = None,
        project_root: Path | str | None = None,
        entries: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Initialize the Nim jsondoc enricher."""
        if nim_source is not None and nim_source_path is None:
            nim_source_path = nim_source
        self.jsondoc_path = Path(jsondoc_path) if jsondoc_path is not None else None
        self.nim_source_path = Path(nim_source_path) if nim_source_path is not None else None
        self.project_root: Path | None = (
            Path(project_root).resolve()
            if project_root is not None
            else (_detect_nim_project_root(self.nim_source_path) if self.nim_source_path is not None else None)
        )
        self._explicit_entries: list[Mapping[str, Any]] = list(entries) if entries is not None else []
        self._entries: list[Mapping[str, Any]] = list(entries) if entries is not None else []
        self._loaded = entries is not None
        self._docs_by_kind_and_name: dict[tuple[str, str], str] = {}
        self._docs_by_name: dict[str, str] = {}
        self._func_by_name_and_arity: dict[tuple[str, int], _FuncDocMeta] = {}
        self._func_by_name: dict[str, _FuncDocMeta] = {}
        self._params_by_func: dict[str, list[str]] = {}
        self._deprecated_symbols: set[str] = set()
        if self._loaded:
            self._index_entries(self._entries)

    def _register_func_meta(self, meta: _FuncDocMeta) -> None:
        arity = len(meta.param_names)
        for key_name in dict.fromkeys((meta.c_name, meta.nim_name)):
            if not key_name:
                continue
            existing_arity = self._func_by_name_and_arity.get((key_name, arity))
            if existing_arity is None:
                self._func_by_name_and_arity[(key_name, arity)] = meta
            else:
                merged = replace(
                    existing_arity,
                    doc=meta.doc or existing_arity.doc,
                    param_names=meta.param_names or existing_arity.param_names,
                    param_nim_types=meta.param_nim_types or existing_arity.param_nim_types,
                    return_nim_type=meta.return_nim_type or existing_arity.return_nim_type,
                    is_deprecated=meta.is_deprecated or existing_arity.is_deprecated,
                )
                self._func_by_name_and_arity[(key_name, arity)] = merged

            existing_by_name = self._func_by_name.get(key_name)
            if existing_by_name is None:
                self._func_by_name[key_name] = meta
            else:
                self._func_by_name[key_name] = replace(
                    existing_by_name,
                    doc=meta.doc or existing_by_name.doc,
                    param_names=meta.param_names or existing_by_name.param_names,
                    param_nim_types=meta.param_nim_types or existing_by_name.param_nim_types,
                    return_nim_type=meta.return_nim_type or existing_by_name.return_nim_type,
                    is_deprecated=meta.is_deprecated or existing_by_name.is_deprecated,
                )

            if meta.doc:
                self._docs_by_kind_and_name[("function", key_name)] = meta.doc
            if meta.param_names:
                self._params_by_func[key_name] = meta.param_names
            if meta.is_deprecated:
                self._deprecated_symbols.add(key_name)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.jsondoc_path is not None and self.jsondoc_path.is_file():
            try:
                raw = json.loads(self.jsondoc_path.read_text(encoding="utf-8"))
                self._entries = self._extract_entries(raw)
            except (OSError, ValueError) as exc:
                logger.warning("Failed to load jsondoc file %s: %s", self.jsondoc_path, exc)
        elif self.nim_source_path is not None and self.nim_source_path.is_file():
            proj_root = self.project_root or _detect_nim_project_root(self.nim_source_path)
            constituent_files = _collect_constituent_nim_files(self.nim_source_path, proj_root)

            # Only invoke `nim jsondoc` on root compilable entrypoints (never on raw include fragments)
            if not _is_included_fragment(self.nim_source_path, proj_root):
                with tempfile.TemporaryDirectory() as tmpdir:
                    out_json = Path(tmpdir) / f"{self.nim_source_path.stem}.jsondoc.json"
                    try:
                        proc = subprocess.run(
                            ["nim", "jsondoc", "--raw", f"-o:{out_json}", str(self.nim_source_path.resolve())],
                            cwd=str(proj_root),
                            capture_output=True,
                            text=True,
                            timeout=15,
                            check=False,
                        )
                        if proc.returncode == 0 and out_json.is_file():
                            raw = json.loads(out_json.read_text(encoding="utf-8"))
                            self._entries = self._extract_entries(raw)
                    except (OSError, subprocess.SubprocessError, ValueError) as exc:
                        logger.warning("Failed to run nim jsondoc on %s: %s", self.nim_source_path, exc)

            self._index_entries(self._entries)

            # Follow `include` directives at the source level to enrich from entrypoint and all fragment files
            for nim_file in constituent_files:
                funcs, type_docs, dep_syms = _parse_nim_source_file(nim_file)
                for k, v in type_docs.items():
                    self._docs_by_kind_and_name.setdefault(k, v)
                self._deprecated_symbols.update(dep_syms)
                for f_meta in funcs:
                    self._register_func_meta(f_meta)
            return

        self._index_entries(self._entries)

    @staticmethod
    def _extract_entries(raw: Any) -> list[Mapping[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, Mapping):
            entries = raw.get("entries") or raw.get("exports") or raw.get("declarations")
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, Mapping)]
        return []

    def _index_entries(self, entries: Sequence[Mapping[str, Any]]) -> None:
        for entry in entries:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            c_name_raw = entry.get("exportc") or entry.get("c_name")
            pragma_str = str(entry.get("pragma") or entry.get("code") or "")
            inferred_c_name, inferred_dep = _extract_pragma_info(name, pragma_str)
            c_name = str(c_name_raw).strip() if isinstance(c_name_raw, str) and c_name_raw.strip() else inferred_c_name
            is_dep = bool(entry.get("deprecated") or entry.get("is_deprecated") or inferred_dep)
            if is_dep:
                self._deprecated_symbols.add(name)
                self._deprecated_symbols.add(c_name)

            category = _categorize_entry_kind(entry.get("type") or entry.get("kind"))
            desc = entry.get("description") or entry.get("doc")
            cleaned_desc = desc.strip() if isinstance(desc, str) and desc.strip() else None
            if cleaned_desc:
                for target_name in dict.fromkeys((c_name, name)):
                    if category is not None:
                        self._docs_by_kind_and_name[(category, target_name)] = cleaned_desc
                    else:
                        self._docs_by_name[target_name] = cleaned_desc

            if category in (None, "function"):
                params = entry.get("parameters") or entry.get("params")
                ret_nim_type: str | None = None
                sig = entry.get("signature")
                if isinstance(sig, Mapping):
                    if params is None:
                        params = sig.get("arguments") or sig.get("parameters") or sig.get("params")
                    ret_raw = sig.get("return") or sig.get("return_type")
                    if isinstance(ret_raw, str) and ret_raw.strip():
                        ret_nim_type = ret_raw.strip()
                param_names: list[str] = []
                param_nim_types: list[str] = []
                if isinstance(params, list):
                    for p in params:
                        if isinstance(p, str):
                            param_names.append(p)
                            param_nim_types.append("")
                        elif isinstance(p, Mapping) and isinstance(p.get("name"), str):
                            param_names.append(str(p["name"]))
                            p_type = p.get("type")
                            param_nim_types.append(str(p_type).strip() if isinstance(p_type, str) else "")

                if category == "function" or param_names or c_name != name:
                    self._register_func_meta(
                        _FuncDocMeta(
                            nim_name=name,
                            c_name=c_name,
                            doc=cleaned_desc,
                            param_names=param_names,
                            param_nim_types=param_nim_types,
                            return_nim_type=ret_nim_type,
                            is_deprecated=is_dep,
                        )
                    )

    def _lookup_doc(self, category: str, name: str | None, default: str | None) -> str | None:
        if not name:
            return default
        if (category, name) in self._docs_by_kind_and_name:
            return self._docs_by_kind_and_name[(category, name)]
        if name in self._docs_by_name:
            return self._docs_by_name[name]
        return default

    def apply(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        """Apply jsondoc enrichment to a SourceUnit."""
        return self.transform(unit, context=context)

    def __call__(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        """Allow direct invocation as a callable."""
        return self.transform(unit, context=context)

    def transform(self, unit: SourceUnit, context: PipelineContext | None = None) -> SourceUnit:
        self._ensure_loaded()
        return super().transform(unit, context=context)

    def visit_Function(self, node: Function, context: PipelineContext | None = None) -> Function:
        fn = self.generic_visit(node, context=context)
        arity = len(fn.parameters)
        meta = self._func_by_name_and_arity.get((fn.name, arity))
        if meta is None:
            fallback_meta = self._func_by_name.get(fn.name)
            if fallback_meta is not None and (not fallback_meta.param_names or len(fallback_meta.param_names) == arity):
                meta = fallback_meta

        doc = (meta.doc if meta and meta.doc else None) or self._lookup_doc("function", fn.name, fn.doc)
        param_names = meta.param_names if meta and meta.param_names else self._params_by_func.get(fn.name)
        param_types = meta.param_nim_types if meta else []

        new_params: list[Parameter] = list(fn.parameters)
        if param_names and len(param_names) == len(fn.parameters):
            updated_params: list[Parameter] = []
            for idx, (p, p_name) in enumerate(zip(fn.parameters, param_names, strict=True)):
                p_new = replace(p, name=p_name)
                nim_t = param_types[idx] if idx < len(param_types) else ""
                if nim_t == "bool" and hasattr(p_new, "type_overrides"):
                    overrides = dict(getattr(p_new, "type_overrides", {}) or {})
                    overrides.setdefault("pyi", "bool")
                    p_new = replace(p_new, type_overrides=overrides)
                updated_params.append(p_new)
            new_params = updated_params

        is_dep = fn.is_deprecated or bool(meta and meta.is_deprecated) or (fn.name in self._deprecated_symbols)
        if doc != fn.doc or new_params != fn.parameters or is_dep != fn.is_deprecated:
            fn = replace(fn, doc=doc, parameters=new_params, is_deprecated=is_dep)

        if meta and meta.return_nim_type == "bool" and hasattr(fn, "type_overrides"):
            overrides = dict(getattr(fn, "type_overrides", {}) or {})
            overrides.setdefault("pyi", "bool")
            fn = replace(fn, type_overrides=overrides)
        return fn

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        st = self.generic_visit(node, context=context)
        doc = self._lookup_doc("type", st.name, st.doc)
        is_dep = st.is_deprecated or bool(st.name and st.name in self._deprecated_symbols)
        if doc != st.doc or is_dep != st.is_deprecated:
            return replace(st, doc=doc, is_deprecated=is_dep)
        return st

    def visit_Enum(self, node: Enum, context: PipelineContext | None = None) -> Enum:
        en = self.generic_visit(node, context=context)
        doc = self._lookup_doc("type", en.name, en.doc)
        if doc != en.doc:
            return replace(en, doc=doc)
        return en

    def visit_Typedef(self, node: Typedef, context: PipelineContext | None = None) -> Typedef:
        td = self.generic_visit(node, context=context)
        doc = self._lookup_doc("type", td.name, td.doc)
        is_dep = td.is_deprecated or (td.name in self._deprecated_symbols)
        if doc != td.doc or is_dep != td.is_deprecated:
            return replace(td, doc=doc, is_deprecated=is_dep)
        return td

    def visit_Variable(self, node: Variable, context: PipelineContext | None = None) -> Variable:
        var = self.generic_visit(node, context=context)
        doc = self._lookup_doc("variable", var.name, var.doc)
        is_dep = var.is_deprecated or (var.name in self._deprecated_symbols)
        if doc != var.doc or is_dep != var.is_deprecated:
            return replace(var, doc=doc, is_deprecated=is_dep)
        return var

    def visit_Constant(self, node: Constant, context: PipelineContext | None = None) -> Constant:
        const = self.generic_visit(node, context=context)
        doc = self._lookup_doc("constant", const.name, const.doc)
        if doc != const.doc:
            return replace(const, doc=doc)
        return const

    def cache_fingerprint(self) -> dict[str, Any]:
        """Return a deterministic fingerprint including SHA-256 digests of all constituent ``.nim`` files."""
        nim_digests: dict[str, str] = {}
        if self.nim_source_path is not None and self.nim_source_path.is_file():
            proj_root = self.project_root or _detect_nim_project_root(self.nim_source_path)
            for nim_file in _collect_constituent_nim_files(self.nim_source_path, proj_root):
                try:
                    nim_digests[str(nim_file)] = hashlib.sha256(nim_file.read_bytes()).hexdigest()
                except OSError:
                    continue

        jsondoc_sha256: str | None = None
        if self.jsondoc_path is not None and self.jsondoc_path.is_file():
            try:
                jsondoc_sha256 = hashlib.sha256(self.jsondoc_path.read_bytes()).hexdigest()
            except OSError:
                jsondoc_sha256 = None

        return {
            "filter": self.__class__.__name__,
            "config": {
                "jsondoc_path": str(self.jsondoc_path) if self.jsondoc_path is not None else None,
                "jsondoc_sha256": jsondoc_sha256,
                "nim_source_path": str(self.nim_source_path) if self.nim_source_path is not None else None,
                "project_root": str(self.project_root) if self.project_root is not None else None,
                "nim_file_sha256s": dict(sorted(nim_digests.items())),
                "entries": _normalize_fingerprint_value(self._explicit_entries),
            },
        }
