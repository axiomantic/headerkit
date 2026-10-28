"""Nim jsondoc enrichment transform for HeaderKit IR."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from headerkit.hooks import PipelineContext
from headerkit.ir import (
    Constant,
    Enum,
    Function,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
)
from headerkit.transforms.filter import PipelineFilter

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


class NimJsonDocEnricher(PipelineFilter):
    """Enrich a ``SourceUnit`` with docstrings and parameter names from ``nim jsondoc`` or ``.phyne.ir.json``."""

    def __init__(
        self,
        jsondoc_path: Path | str | None = None,
        *,
        nim_source_path: Path | str | None = None,
        entries: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Initialize the Nim jsondoc enricher.

        Note: Passing ``nim_source_path`` invokes ``nim jsondoc`` as a subprocess;
        because Nim compilation may evaluate compile-time macros/static blocks,
        ``nim_source_path`` should only be used on trusted source files.
        """
        self.jsondoc_path = Path(jsondoc_path) if jsondoc_path is not None else None
        self.nim_source_path = Path(nim_source_path) if nim_source_path is not None else None
        self._entries: list[Mapping[str, Any]] = list(entries) if entries is not None else []
        self._loaded = entries is not None
        self._docs_by_kind_and_name: dict[tuple[str, str], str] = {}
        self._docs_by_name: dict[str, str] = {}
        self._params_by_func: dict[str, list[str]] = {}
        if self._loaded:
            self._index_entries(self._entries)

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
            with tempfile.TemporaryDirectory() as tmpdir:
                out_json = Path(tmpdir) / f"{self.nim_source_path.stem}.jsondoc.json"
                try:
                    proc = subprocess.run(
                        ["nim", "jsondoc", "--raw", f"-o:{out_json}", str(self.nim_source_path)],
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
            category = _categorize_entry_kind(entry.get("type") or entry.get("kind"))
            desc = entry.get("description") or entry.get("doc")
            if isinstance(desc, str) and desc.strip():
                cleaned_desc = desc.strip()
                if category is not None:
                    self._docs_by_kind_and_name[(category, name)] = cleaned_desc
                else:
                    self._docs_by_name[name] = cleaned_desc
            if category in (None, "function"):
                params = entry.get("parameters") or entry.get("params")
                if params is None:
                    sig = entry.get("signature")
                    if isinstance(sig, Mapping):
                        params = sig.get("arguments") or sig.get("parameters") or sig.get("params")
                if isinstance(params, list):
                    param_names: list[str] = []
                    for p in params:
                        if isinstance(p, str):
                            param_names.append(p)
                        elif isinstance(p, Mapping) and isinstance(p.get("name"), str):
                            param_names.append(str(p["name"]))
                    if param_names:
                        self._params_by_func[name] = param_names

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
        doc = self._lookup_doc("function", fn.name, fn.doc)
        param_names = self._params_by_func.get(fn.name)
        new_params = fn.parameters
        if param_names and len(param_names) == len(fn.parameters):
            new_params = [replace(p, name=p_name) for p, p_name in zip(fn.parameters, param_names, strict=True)]
        if doc != fn.doc or new_params != fn.parameters:
            return replace(fn, doc=doc, parameters=new_params)
        return fn

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        st = self.generic_visit(node, context=context)
        doc = self._lookup_doc("type", st.name, st.doc)
        if doc != st.doc:
            return replace(st, doc=doc)
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
        if doc != td.doc:
            return replace(td, doc=doc)
        return td

    def visit_Variable(self, node: Variable, context: PipelineContext | None = None) -> Variable:
        var = self.generic_visit(node, context=context)
        doc = self._lookup_doc("variable", var.name, var.doc)
        if doc != var.doc:
            return replace(var, doc=doc)
        return var

    def visit_Constant(self, node: Constant, context: PipelineContext | None = None) -> Constant:
        const = self.generic_visit(node, context=context)
        doc = self._lookup_doc("constant", const.name, const.doc)
        if doc != const.doc:
            return replace(const, doc=doc)
        return const
