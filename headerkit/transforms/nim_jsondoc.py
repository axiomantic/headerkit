"""Nim jsondoc enrichment transform for HeaderKit IR."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from headerkit.hooks import PipelineContext
from headerkit.ir import (
    Enum,
    Function,
    SourceUnit,
    Struct,
    Typedef,
)
from headerkit.transforms.filter import PipelineFilter


class NimJsonDocEnricher(PipelineFilter):
    """Enrich a ``SourceUnit`` with docstrings and parameter names from ``nim jsondoc`` or ``.phyne.ir.json``."""

    def __init__(
        self,
        jsondoc_path: Path | str | None = None,
        *,
        nim_source_path: Path | str | None = None,
        entries: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self.jsondoc_path = Path(jsondoc_path) if jsondoc_path is not None else None
        self.nim_source_path = Path(nim_source_path) if nim_source_path is not None else None
        self._entries: list[Mapping[str, Any]] = list(entries) if entries is not None else []
        self._loaded = entries is not None
        self._docs_by_name: dict[str, str] = {}
        self._params_by_func: dict[str, list[str]] = {}
        if self._loaded:
            self._index_entries(self._entries)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.jsondoc_path is not None and self.jsondoc_path.is_file():
            raw = json.loads(self.jsondoc_path.read_text(encoding="utf-8"))
            self._entries = self._extract_entries(raw)
        elif self.nim_source_path is not None and self.nim_source_path.is_file():
            out_json = self.nim_source_path.with_suffix(".jsondoc.json")
            try:
                proc = subprocess.run(
                    ["nim", "jsondoc", f"-o:{out_json}", str(self.nim_source_path)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if proc.returncode == 0 and out_json.is_file():
                    raw = json.loads(out_json.read_text(encoding="utf-8"))
                    self._entries = self._extract_entries(raw)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            finally:
                if out_json.is_file():
                    out_json.unlink(missing_ok=True)
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
            desc = entry.get("description") or entry.get("doc")
            if isinstance(desc, str) and desc.strip():
                self._docs_by_name[name] = desc.strip()
            params = entry.get("parameters") or entry.get("params")
            if isinstance(params, list):
                param_names: list[str] = []
                for p in params:
                    if isinstance(p, str):
                        param_names.append(p)
                    elif isinstance(p, Mapping) and isinstance(p.get("name"), str):
                        param_names.append(str(p["name"]))
                if param_names:
                    self._params_by_func[name] = param_names

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
        doc = self._docs_by_name.get(fn.name, fn.doc)
        param_names = self._params_by_func.get(fn.name)
        new_params = fn.parameters
        if param_names and len(param_names) == len(fn.parameters):
            new_params = [replace(p, name=p_name) for p, p_name in zip(fn.parameters, param_names, strict=True)]
        if doc != fn.doc or new_params != fn.parameters:
            return replace(fn, doc=doc, parameters=new_params)
        return fn

    def visit_Struct(self, node: Struct, context: PipelineContext | None = None) -> Struct:
        st = self.generic_visit(node, context=context)
        if st.name and st.name in self._docs_by_name:
            return replace(st, doc=self._docs_by_name[st.name])
        return st

    def visit_Enum(self, node: Enum, context: PipelineContext | None = None) -> Enum:
        en = self.generic_visit(node, context=context)
        if en.name and en.name in self._docs_by_name:
            return replace(en, doc=self._docs_by_name[en.name])
        return en

    def visit_Typedef(self, node: Typedef, context: PipelineContext | None = None) -> Typedef:
        td = self.generic_visit(node, context=context)
        if td.name in self._docs_by_name:
            return replace(td, doc=self._docs_by_name[td.name])
        return td
