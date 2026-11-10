"""Nimony NIF S-expression AST parser backend for HeaderKit IR.

Parses Nimony NIF (``.nif``, ``.s.nif``, ``.p.nif``) S-expression AST files and
Nimony ``.nim`` sources into normalized ``SourceUnit`` IR with ``SourceMeta``
(``source_language="nimony"``) and ``ParsedDoc``.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.resources
import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from headerkit.backends import register_backend
from headerkit.backends.nim import _ast_json_to_declarations, _load_backend_resource_bytes
from headerkit.hooks import PipelineContext, Priority, hook
from headerkit.ir import (
    Constant,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    Parameter,
    ParserBackend,
    SourceMeta,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
)
from headerkit.transforms.doc_parser import DocParserTransform

logger = logging.getLogger(__name__)


def _get_nimony_nif_helper_binary() -> Path | None:
    """Compile and cache ``_nimony_nif_helper.nim`` into ``~/.cache/headerkit/bin/``."""
    nim_bin = shutil.which("nim")
    if not nim_bin:
        return None
    try:
        ref = importlib.resources.files("headerkit.backends").joinpath("_nimony_nif_helper.nim")
        src_bytes: bytes | None = ref.read_bytes()
    except (OSError, TypeError, ModuleNotFoundError, FileNotFoundError):
        src_bytes = _load_backend_resource_bytes("_nimony_nif_helper.nim")
    if not src_bytes:
        return None

    try:
        ver_res = subprocess.run(
            [nim_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ver_line = ver_res.stdout.splitlines()[0] if ver_res.stdout else ""
    except (OSError, subprocess.SubprocessError):
        return None

    digest = hashlib.sha256(src_bytes + ver_line.encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(os.environ.get("HEADERKIT_CACHE_DIR", Path.home() / ".cache" / "headerkit")) / "bin"
    exe_suffix = ".exe" if os.name == "nt" else ""
    bin_path = cache_dir / f"nimony_nif_helper_{digest}{exe_suffix}"
    if bin_path.is_file() and os.access(bin_path, os.X_OK):
        return bin_path

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="hk_nimony_nif_") as tmpdir:
            tmp_src = Path(tmpdir) / "nimony_nif_helper.nim"
            tmp_src.write_bytes(src_bytes)
            tmp_bin = Path(tmpdir) / f"nimony_nif_helper_{os.getpid()}{exe_suffix}"
            compile_res = subprocess.run(
                [
                    nim_bin,
                    "c",
                    "--hints:off",
                    "--warnings:off",
                    "-d:release",
                    f"--nimcache:{Path(tmpdir) / 'nimcache'}",
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
                return bin_path
            logger.debug("Failed to compile _nimony_nif_helper.nim: %s", compile_res.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Error building _nimony_nif_helper.nim: %s", exc)
    return None


@dataclass(slots=True)
class _NifNode:
    kind: str  # "atom", "str", "list"
    text: str = ""
    children: list[_NifNode] | None = None
    line: int = 1
    col: int = 1


def _decode_nif_escapes(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    hex_chars = "0123456789abcdefABCDEF"
    while i < n:
        if s[i] == "\\" and i + 2 < n and s[i + 1] in hex_chars and s[i + 2] in hex_chars:
            out.append(chr(int(s[i + 1 : i + 3], 16)))
            i += 3
            continue
        if s[i] == "\\" and i + 1 < n:
            nxt = s[i + 1]
            escape_map = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
            out.append(escape_map.get(nxt, nxt))
            i += 2
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _strip_module_suffix(ident: str) -> tuple[str, bool]:
    s = ident.strip()
    exported = False
    if s.endswith(".x"):
        exported = True
        s = s[:-2]
    dot_idx = s.find(".")
    if dot_idx > 0:
        s = s[:dot_idx]
    return s, exported


def _parse_nif_sexpr_py(src: str) -> _NifNode:
    root = _NifNode(kind="list", text="root", children=[], line=1, col=1)
    stack: list[_NifNode] = [root]
    i = 0
    n = len(src)
    line = 1
    col = 1

    while i < n:
        ch = src[i]
        if ch in (" ", "\t", "\r"):
            i += 1
            col += 1
        elif ch == "\n":
            i += 1
            line += 1
            col = 1
        elif (
            ch == "#"
            or (ch == ";" and (i + 1 >= n or src[i + 1] == ";"))
            or (ch == "." and col == 1 and src[i : i + 6] == ".nif24")
        ):
            while i < n and src[i] != "\n":
                i += 1
        elif ch == "(":
            node = _NifNode(kind="list", text="", children=[], line=line, col=col)
            assert stack[-1].children is not None
            stack[-1].children.append(node)
            stack.append(node)
            i += 1
            col += 1
        elif ch == ")":
            if len(stack) > 1:
                stack.pop()
            i += 1
            col += 1
        elif ch == '"':
            start_line = line
            start_col = col
            i += 1
            col += 1
            raw_chars: list[str] = []
            while i < n and src[i] != '"':
                if src[i] == "\\" and i + 1 < n:
                    raw_chars.append(src[i])
                    raw_chars.append(src[i + 1])
                    i += 2
                    col += 2
                else:
                    if src[i] == "\n":
                        line += 1
                        col = 1
                    else:
                        col += 1
                    raw_chars.append(src[i])
                    i += 1
            if i < n and src[i] == '"':
                i += 1
                col += 1
            assert stack[-1].children is not None
            stack[-1].children.append(
                _NifNode(
                    kind="str",
                    text=_decode_nif_escapes("".join(raw_chars)),
                    line=start_line,
                    col=start_col,
                )
            )
        else:
            start_line = line
            start_col = col
            tok_chars: list[str] = []
            while i < n and src[i] not in (" ", "\t", "\r", "\n", "(", ")", '"'):
                tok_chars.append(src[i])
                i += 1
                col += 1
            tok = "".join(tok_chars)
            if all(c in "0123456789,~-" for c in tok) and ("," in tok or tok.startswith("~")):
                continue
            assert stack[-1].children is not None
            stack[-1].children.append(_NifNode(kind="atom", text=tok, line=start_line, col=start_col))
    return root


def _head_tag(node: _NifNode | None) -> str:
    if node is not None and node.kind == "list" and node.children and node.children[0].kind == "atom":
        return node.children[0].text
    return ""


def _render_nif_node(node: _NifNode | None) -> str:
    if node is None:
        return ""
    if node.kind == "atom":
        if node.text == ".":
            return ""
        base, _ = _strip_module_suffix(node.text)
        return base
    if node.kind == "str":
        return node.text
    children = node.children or []
    tag = _head_tag(node)
    if tag == "i":
        if len(children) >= 2:
            bits = children[1].text
            mapping = {"-1": "int", "-8": "int8", "-16": "int16", "-32": "int32", "-64": "int64"}
            return mapping.get(bits, f"int{bits}")
        return "int"
    if tag == "u":
        if len(children) >= 2:
            bits = children[1].text.lstrip("+")
            if bits in ("1", "-1"):
                return "uint"
            return f"uint{bits}"
        return "uint"
    if tag == "f":
        if len(children) >= 2:
            bits = children[1].text.lstrip("+")
            return f"float{bits}"
        return "float64"
    if tag in ("bool", "char", "cstring", "void"):
        return tag
    if tag in ("ptr", "aptr"):
        return f"ptr {_render_nif_node(children[1])}" if len(children) >= 2 else "pointer"
    if tag == "ref":
        return f"ref {_render_nif_node(children[1])}" if len(children) >= 2 else "pointer"
    if tag in ("mut", "var", "out"):
        return f"var {_render_nif_node(children[1])}" if len(children) >= 2 else "pointer"
    if tag == "array":
        if len(children) >= 3:
            return f"array[{_render_nif_node(children[2])}, {_render_nif_node(children[1])}]"
        if len(children) == 2:
            return f"UncheckedArray[{_render_nif_node(children[1])}]"
        return "array"
    if tag == "range" and len(children) >= 3:
        return f"{_render_nif_node(children[1])}..{_render_nif_node(children[2])}"
    parts = [_render_nif_node(c) for c in children]
    return " ".join(p for p in parts if p)


def _parse_pragmas_py(node: _NifNode | None, base_name: str) -> dict[str, Any]:
    res: dict[str, Any] = {
        "exportc_name": base_name,
        "has_exportc": False,
        "calling_convention": None,
        "is_deprecated": False,
        "deprecated_msg": None,
        "is_packed": False,
        "is_union": False,
        "is_bycopy": False,
        "size_expr": None,
        "bit_width": None,
        "attributes": [],
    }
    if node is None or node.kind != "list" or not node.children:
        return res
    tag = _head_tag(node)
    items = node.children[1:] if tag in ("pragmas", "pragma") else [node]
    callconvs = {
        "cdecl",
        "stdcall",
        "fastcall",
        "safecall",
        "thiscall",
        "syscall",
        "noconv",
        "nimcall",
        "closure",
        "inline",
    }
    for item in items:
        if item.kind == "atom":
            k_low = item.text.lower().replace("_", "")
            if k_low == "exportc":
                res["has_exportc"] = True
                res["attributes"].append("exportc")
            elif k_low in callconvs:
                res["calling_convention"] = k_low
                res["attributes"].append(k_low)
            elif k_low == "deprecated":
                res["is_deprecated"] = True
                res["attributes"].append("deprecated")
            elif k_low == "packed":
                res["is_packed"] = True
                res["attributes"].append("packed")
            elif k_low == "union":
                res["is_union"] = True
                res["attributes"].append("union")
            elif k_low == "bycopy":
                res["is_bycopy"] = True
                res["attributes"].append("bycopy")
            elif k_low != ".":
                res["attributes"].append(item.text)
        elif item.kind == "list" and item.children:
            p_tag = _head_tag(item).lower().replace("_", "")
            if p_tag == "exportc":
                res["has_exportc"] = True
                res["attributes"].append("exportc")
                if len(item.children) >= 2:
                    s = _render_nif_node(item.children[1]).strip()
                    if "$1" in s:
                        res["exportc_name"] = s.replace("$1", base_name)
                    elif s:
                        res["exportc_name"] = s
            elif p_tag in callconvs:
                res["calling_convention"] = p_tag
                res["attributes"].append(p_tag)
            elif p_tag == "deprecated":
                res["is_deprecated"] = True
                res["attributes"].append("deprecated")
                if len(item.children) >= 2:
                    res["deprecated_msg"] = _render_nif_node(item.children[1]).strip()
            elif p_tag == "packed":
                res["is_packed"] = True
                res["attributes"].append("packed")
            elif p_tag == "union":
                res["is_union"] = True
                res["attributes"].append("union")
            elif p_tag == "bycopy":
                res["is_bycopy"] = True
                res["attributes"].append("bycopy")
            elif p_tag == "size":
                res["attributes"].append("size")
                if len(item.children) >= 2:
                    res["size_expr"] = _render_nif_node(item.children[1]).strip()
            elif p_tag == "bitsize":
                res["attributes"].append("bitsize")
                if len(item.children) >= 2:
                    with contextlib.suppress(ValueError):
                        res["bit_width"] = int(_render_nif_node(item.children[1]).strip())
            elif p_tag and p_tag != ".":
                res["attributes"].append(p_tag)
    return res


def _serialize_nif_type_py(node: _NifNode | None) -> dict[str, Any]:
    if node is None or (node.kind == "atom" and node.text in (".", "void")):
        return {"kind": "ctype", "name": "void", "source_type": "void"}
    src_type = _render_nif_node(node)
    if node.kind == "atom":
        base, _ = _strip_module_suffix(node.text)
        return {"kind": "ctype", "name": base, "source_type": base}
    if node.kind == "str":
        return {"kind": "ctype", "name": node.text, "source_type": node.text}
    children = node.children or []
    tag = _head_tag(node)
    if tag in ("i", "u", "f", "bool", "char", "cstring", "void"):
        return {"kind": "ctype", "name": src_type, "source_type": src_type}
    if tag in ("ptr", "aptr"):
        pointee = (
            _serialize_nif_type_py(children[1])
            if len(children) >= 2
            else {"kind": "ctype", "name": "void", "source_type": "void"}
        )
        return {"kind": "pointer", "pointee": pointee, "source_type": src_type}
    if tag == "ref":
        pointee = (
            _serialize_nif_type_py(children[1])
            if len(children) >= 2
            else {"kind": "ctype", "name": "void", "source_type": "void"}
        )
        return {"kind": "pointer", "pointee": pointee, "is_ref": True, "source_type": src_type}
    if tag in ("mut", "var", "out"):
        pointee = (
            _serialize_nif_type_py(children[1])
            if len(children) >= 2
            else {"kind": "ctype", "name": "void", "source_type": "void"}
        )
        return {"kind": "pointer", "pointee": pointee, "is_var": True, "source_type": src_type}
    if tag == "distinct":
        under = (
            _serialize_nif_type_py(children[1])
            if len(children) >= 2
            else {"kind": "ctype", "name": "void", "source_type": "void"}
        )
        return {"kind": "distinct", "underlying": under, "source_type": src_type}
    if tag == "array":
        if len(children) >= 3:
            elem = _serialize_nif_type_py(children[1])
            arr_size = -1
            sz_node = children[2]
            if sz_node.kind == "atom":
                with contextlib.suppress(ValueError):
                    arr_size = int(sz_node.text.lstrip("+"))
            elif (
                sz_node.kind == "list"
                and _head_tag(sz_node) == "range"
                and sz_node.children
                and len(sz_node.children) >= 3
            ):
                with contextlib.suppress(ValueError):
                    lo = int(_render_nif_node(sz_node.children[1]).lstrip("+"))
                    hi = int(_render_nif_node(sz_node.children[2]).lstrip("+"))
                    arr_size = hi - lo + 1
            return {"kind": "array", "size": arr_size, "element_type": elem, "source_type": src_type}
        if len(children) == 2:
            return {
                "kind": "array",
                "size": None,
                "element_type": _serialize_nif_type_py(children[1]),
                "source_type": src_type,
            }
    if tag in ("proctype", "proc", "func"):
        ret_json: dict[str, Any] = {"kind": "ctype", "name": "void", "source_type": "void"}
        params_arr: list[dict[str, Any]] = []
        cc: str | None = None
        for idx, ch in enumerate(children[1:], start=1):
            c_tag = _head_tag(ch)
            if c_tag == "params" and ch.children:
                for p_node in ch.children[1:]:
                    if _head_tag(p_node) in ("param", "fld") and p_node.children and len(p_node.children) >= 4:
                        p_name, _ = _strip_module_suffix(_render_nif_node(p_node.children[1]))
                        params_arr.append(
                            {
                                "name": p_name,
                                "type": _serialize_nif_type_py(p_node.children[3]),
                                "default": None,
                            }
                        )
            elif c_tag in ("pragmas", "pragma"):
                p_data = _parse_pragmas_py(ch, "")
                cc = p_data["calling_convention"]
            elif idx == 3 and c_tag not in ("params", "pragmas", "pragma", "effects", "stmts"):
                if not (ch.kind == "atom" and ch.text == "."):
                    ret_json = _serialize_nif_type_py(ch)
        return {
            "kind": "proc",
            "return_type": ret_json,
            "parameters": params_arr,
            "calling_convention": cc,
            "source_type": src_type,
        }
    return {"kind": "ctype", "name": src_type, "source_type": src_type}


def _extract_doc_from_nodes_py(nodes: Sequence[_NifNode]) -> str:
    lines: list[str] = []
    for ch in nodes:
        if ch.kind == "str" and ch.text.strip():
            lines.append(ch.text.strip())
        elif ch.kind == "list" and _head_tag(ch) in ("comment", "doc") and ch.children and len(ch.children) >= 2:
            s = _render_nif_node(ch.children[1]).strip()
            if s:
                lines.append(s)
        elif ch.kind == "list" and _head_tag(ch) == "stmts" and ch.children:
            for st in ch.children[1:]:
                if st.kind == "str" and st.text.strip():
                    lines.append(st.text.strip())
                elif (
                    st.kind == "list" and _head_tag(st) in ("comment", "doc") and st.children and len(st.children) >= 2
                ):
                    s = _render_nif_node(st.children[1]).strip()
                    if s:
                        lines.append(s)
                else:
                    break
    return "\n".join(lines).strip()


def _process_nif_nodes_py(nodes: Sequence[_NifNode], current_file: str, decls: list[dict[str, Any]]) -> None:
    pending_doc: list[str] = []
    for n in nodes:
        if n.kind == "str":
            if n.text.strip():
                pending_doc.append(n.text.strip())
            continue
        if n.kind != "list" or not n.children:
            continue
        tag = _head_tag(n)
        children = n.children
        if tag in ("stmts", "root"):
            _process_nif_nodes_py(children[1:], current_file, decls)
        elif tag in ("comment", "doc"):
            if len(children) >= 2:
                c = _render_nif_node(children[1]).strip()
                if c:
                    pending_doc.append(c)
        elif tag in ("proc", "func", "method", "converter", "iterator", "template"):
            if len(children) < 2:
                pending_doc.clear()
                continue
            raw_name, suffix_exported = _strip_module_suffix(children[1].text)
            is_exported = suffix_exported
            if len(children) >= 3 and children[2].kind == "atom" and children[2].text in ("x", ".x", "export"):
                is_exported = True
            params_node: _NifNode | None = None
            ret_node: _NifNode | None = None
            pragma_node: _NifNode | None = None
            body_nodes: list[_NifNode] = []
            for idx in range(2, len(children)):
                ch = children[idx]
                c_tag = _head_tag(ch)
                if c_tag == "params":
                    params_node = ch
                    if idx + 1 < len(children):
                        nxt = children[idx + 1]
                        if _head_tag(nxt) not in ("pragmas", "pragma", "effects", "stmts"):
                            ret_node = nxt
                elif c_tag in ("pragmas", "pragma"):
                    pragma_node = ch
                elif c_tag == "stmts" or ch.kind == "str" or c_tag in ("comment", "doc"):
                    body_nodes.append(ch)
            p_data = _parse_pragmas_py(pragma_node, raw_name)
            doc_str = _extract_doc_from_nodes_py(body_nodes) or "\n".join(pending_doc).strip()
            pending_doc.clear()
            ret_json = _serialize_nif_type_py(ret_node)
            params_arr: list[dict[str, Any]] = []
            if params_node is not None and params_node.children:
                for p in params_node.children[1:]:
                    if _head_tag(p) in ("param", "fld") and p.children and len(p.children) >= 2:
                        p_name, _ = _strip_module_suffix(p.children[1].text)
                        p_type_node: _NifNode | None = None
                        p_def_node: _NifNode | None = None
                        if len(p.children) >= 5:
                            p_type_node = p.children[4]
                            if len(p.children) >= 6:
                                p_def_node = p.children[5]
                        elif len(p.children) == 4:
                            p_type_node = p.children[3]
                        elif len(p.children) == 3:
                            p_type_node = p.children[2]
                        def_str = (
                            _render_nif_node(p_def_node).strip()
                            if p_def_node is not None and not (p_def_node.kind == "atom" and p_def_node.text == ".")
                            else ""
                        )
                        params_arr.append(
                            {
                                "name": p_name,
                                "type": _serialize_nif_type_py(p_type_node),
                                "default": def_str or None,
                            }
                        )
            decls.append(
                {
                    "kind": "routine",
                    "routine_kind": tag,
                    "name": raw_name,
                    "c_name": p_data["exportc_name"],
                    "has_exportc": p_data["has_exportc"],
                    "exported": is_exported,
                    "calling_convention": p_data["calling_convention"],
                    "is_deprecated": p_data["is_deprecated"],
                    "deprecated_msg": p_data["deprecated_msg"],
                    "attributes": p_data["attributes"],
                    "return_type": ret_json,
                    "parameters": params_arr,
                    "doc": doc_str or None,
                    "file": current_file,
                    "line": n.line,
                    "column": n.col,
                }
            )
        elif tag in ("type", "enum"):
            if len(children) < 3:
                pending_doc.clear()
                continue
            raw_name, suffix_exported = _strip_module_suffix(children[1].text)
            is_exported = suffix_exported
            pragma_node = None
            body_node = children[-1]
            doc_nodes: list[_NifNode] = []
            for idx in range(2, len(children)):
                ch = children[idx]
                if ch.kind == "atom" and ch.text in ("x", ".x", "export"):
                    is_exported = True
                elif _head_tag(ch) in ("pragmas", "pragma"):
                    pragma_node = ch
                elif ch.kind == "str" or _head_tag(ch) in ("comment", "doc"):
                    doc_nodes.append(ch)
                elif idx == len(children) - 1 or _head_tag(ch) in (
                    "object",
                    "enum",
                    "tuple",
                    "distinct",
                    "ptr",
                    "aptr",
                    "array",
                    "proctype",
                    "i",
                    "u",
                    "f",
                    "bool",
                    "cstring",
                ):
                    body_node = ch
            p_data = _parse_pragmas_py(pragma_node, raw_name)
            doc_str = _extract_doc_from_nodes_py(doc_nodes) or "\n".join(pending_doc).strip()
            pending_doc.clear()
            b_tag = "enum" if tag == "enum" and _head_tag(body_node) != "enum" else _head_tag(body_node)
            if b_tag in ("object", "tuple"):
                fields_arr: list[dict[str, Any]] = []
                for f_node in (body_node.children or [])[1:]:
                    if f_node.kind == "str" and not doc_str:
                        doc_str = f_node.text.strip()
                        continue
                    if _head_tag(f_node) == "fld" and f_node.children and len(f_node.children) >= 2:
                        f_name, f_exp = _strip_module_suffix(f_node.children[1].text)
                        f_pragma_node: _NifNode | None = None
                        f_type_node: _NifNode | None = None
                        f_doc = ""
                        for fc in f_node.children[2:]:
                            if fc.kind == "atom" and fc.text in ("x", ".x", "export"):
                                f_exp = True
                            elif _head_tag(fc) in ("pragmas", "pragma"):
                                f_pragma_node = fc
                            elif fc.kind == "str" or _head_tag(fc) in ("comment", "doc"):
                                f_doc = _render_nif_node(fc).strip()
                            elif f_type_node is None and not (fc.kind == "atom" and fc.text in (".", "x", ".x")):
                                f_type_node = fc
                        fp_data = _parse_pragmas_py(f_pragma_node, f_name)
                        fields_arr.append(
                            {
                                "name": f_name,
                                "c_name": fp_data["exportc_name"],
                                "exported": f_exp,
                                "type": _serialize_nif_type_py(f_type_node),
                                "bit_width": fp_data["bit_width"],
                                "doc": f_doc or None,
                                "attributes": fp_data["attributes"],
                            }
                        )
                decls.append(
                    {
                        "kind": "object",
                        "name": raw_name,
                        "c_name": p_data["exportc_name"],
                        "has_exportc": p_data["has_exportc"],
                        "exported": is_exported,
                        "is_packed": p_data["is_packed"],
                        "is_union": p_data["is_union"],
                        "is_bycopy": p_data["is_bycopy"] or (b_tag == "tuple"),
                        "is_tuple": b_tag == "tuple",
                        "is_deprecated": p_data["is_deprecated"],
                        "deprecated_msg": p_data["deprecated_msg"],
                        "attributes": p_data["attributes"],
                        "fields": fields_arr,
                        "doc": doc_str or None,
                        "file": current_file,
                        "line": n.line,
                        "column": n.col,
                    }
                )
            elif b_tag == "enum":
                values_arr: list[dict[str, Any]] = []
                next_val = 0
                for e_node in (body_node.children or [])[1:]:
                    if _head_tag(e_node) == "efld" and e_node.children and len(e_node.children) >= 2:
                        e_name, _ = _strip_module_suffix(e_node.children[1].text)
                        e_val = next_val
                        e_doc = ""
                        for ec in e_node.children[2:]:
                            if ec.kind == "atom" and ec.text not in (".", "x", ".x"):
                                with contextlib.suppress(ValueError):
                                    e_val = int(ec.text.lstrip("+"))
                            elif ec.kind == "list" and _head_tag(ec) == "tup" and ec.children and len(ec.children) >= 2:
                                with contextlib.suppress(ValueError):
                                    e_val = int(_render_nif_node(ec.children[1]).lstrip("+"))
                            elif ec.kind == "str":
                                e_doc = ec.text.strip()
                        next_val = e_val + 1
                        values_arr.append(
                            {
                                "name": e_name,
                                "value": e_val,
                                "doc": e_doc or None,
                            }
                        )
                decls.append(
                    {
                        "kind": "enum",
                        "name": raw_name,
                        "c_name": p_data["exportc_name"],
                        "has_exportc": p_data["has_exportc"],
                        "exported": is_exported,
                        "size_expr": p_data["size_expr"],
                        "is_deprecated": p_data["is_deprecated"],
                        "deprecated_msg": p_data["deprecated_msg"],
                        "attributes": p_data["attributes"],
                        "values": values_arr,
                        "doc": doc_str or None,
                        "file": current_file,
                        "line": n.line,
                        "column": n.col,
                    }
                )
            else:
                decls.append(
                    {
                        "kind": "typedef",
                        "name": raw_name,
                        "c_name": p_data["exportc_name"],
                        "has_exportc": p_data["has_exportc"],
                        "exported": is_exported,
                        "is_distinct": b_tag == "distinct",
                        "is_deprecated": p_data["is_deprecated"],
                        "deprecated_msg": p_data["deprecated_msg"],
                        "attributes": p_data["attributes"],
                        "underlying_type": _serialize_nif_type_py(body_node),
                        "doc": doc_str or None,
                        "file": current_file,
                        "line": n.line,
                        "column": n.col,
                    }
                )
        elif tag in ("const", "var", "let", "gvar", "tvar"):
            if len(children) < 2:
                pending_doc.clear()
                continue
            raw_name, suffix_exported = _strip_module_suffix(children[1].text)
            is_exported = suffix_exported
            pragma_node = None
            type_node: _NifNode | None = None
            val_node: _NifNode | None = None
            for ch in children[2:]:
                if ch.kind == "atom" and ch.text in ("x", ".x", "export"):
                    is_exported = True
                elif _head_tag(ch) in ("pragmas", "pragma"):
                    pragma_node = ch
                elif type_node is None and not (ch.kind == "atom" and ch.text == "."):
                    type_node = ch
                elif val_node is None and not (ch.kind == "atom" and ch.text == "."):
                    val_node = ch
            p_data = _parse_pragmas_py(pragma_node, raw_name)
            doc_str = "\n".join(pending_doc).strip()
            pending_doc.clear()
            if tag == "const":
                val_json: Any = None
                val_kind = "expr"
                if val_node is not None:
                    if val_node.kind == "str":
                        val_json = val_node.text
                        val_kind = "string"
                    elif val_node.kind == "atom":
                        t = val_node.text.lstrip("+")
                        try:
                            val_json = int(t)
                            val_kind = "int"
                        except ValueError:
                            try:
                                val_json = float(t)
                                val_kind = "float"
                            except ValueError:
                                if t == "true":
                                    val_json = True
                                    val_kind = "bool"
                                elif t == "false":
                                    val_json = False
                                    val_kind = "bool"
                                else:
                                    val_json = t
                    else:
                        val_json = _render_nif_node(val_node)
                decls.append(
                    {
                        "kind": "const",
                        "name": raw_name,
                        "c_name": p_data["exportc_name"],
                        "has_exportc": p_data["has_exportc"],
                        "exported": is_exported,
                        "is_deprecated": p_data["is_deprecated"],
                        "deprecated_msg": p_data["deprecated_msg"],
                        "attributes": p_data["attributes"],
                        "type": _serialize_nif_type_py(type_node) if type_node is not None else None,
                        "value": val_json,
                        "value_kind": val_kind,
                        "doc": doc_str or None,
                        "file": current_file,
                        "line": n.line,
                        "column": n.col,
                    }
                )
            else:
                decls.append(
                    {
                        "kind": "var",
                        "is_let": tag == "let",
                        "name": raw_name,
                        "c_name": p_data["exportc_name"],
                        "has_exportc": p_data["has_exportc"],
                        "exported": is_exported,
                        "is_deprecated": p_data["is_deprecated"],
                        "deprecated_msg": p_data["deprecated_msg"],
                        "attributes": p_data["attributes"],
                        "type": _serialize_nif_type_py(type_node),
                        "doc": doc_str or None,
                        "file": current_file,
                        "line": n.line,
                        "column": n.col,
                    }
                )
        else:
            pending_doc.clear()


def _run_nimony_nif_helper(source_path: str | Path, code: str | None = None) -> dict[str, Any]:
    """Parse NIF code via compiled ``_nimony_nif_helper.nim`` (or Python NIF parser fallback)."""
    bin_path = _get_nimony_nif_helper_binary()
    if bin_path is not None:
        cmd = [str(bin_path), str(source_path)]
        if code is not None:
            cmd.append("--stdin")
        try:
            res = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                parsed = json.loads(res.stdout)
                if isinstance(parsed, dict):
                    return parsed
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.debug("_nimony_nif_helper binary execution failed: %s", exc)

    src_text = code if code is not None else Path(source_path).read_text(encoding="utf-8")
    root = _parse_nif_sexpr_py(src_text)
    decls: list[dict[str, Any]] = []
    _process_nif_nodes_py(root.children or [], str(source_path), decls)
    return {
        "path": str(source_path),
        "included_files": [],
        "declarations": decls,
    }


def _rewrite_source_language_to_nimony(decl: Declaration) -> Declaration:
    """Stamp ``source_language='nimony'`` onto ``SourceMeta`` across all IR nodes."""
    s_meta = getattr(decl, "source_meta", None)
    new_meta = (
        replace(s_meta, source_language="nimony")
        if isinstance(s_meta, SourceMeta)
        else SourceMeta(source_language="nimony", source_name=getattr(decl, "name", None))
    )
    if isinstance(decl, Function):
        new_params: list[Parameter] = []
        for p in decl.parameters:
            pm = p.source_meta
            new_pm = (
                replace(pm, source_language="nimony")
                if isinstance(pm, SourceMeta)
                else SourceMeta(source_language="nimony", source_name=p.name)
            )
            new_params.append(replace(p, source_meta=new_pm))
        return replace(decl, parameters=new_params, source_meta=new_meta)
    if isinstance(decl, Struct):
        new_fields: list[Field] = []
        for f in decl.fields:
            fm = f.source_meta
            new_fm = (
                replace(fm, source_language="nimony")
                if isinstance(fm, SourceMeta)
                else SourceMeta(source_language="nimony", source_name=f.name)
            )
            new_fields.append(replace(f, source_meta=new_fm))
        return replace(decl, fields=new_fields, source_meta=new_meta)
    if isinstance(decl, Enum):
        new_vals: list[EnumValue] = []
        for ev in decl.values:
            evm = ev.source_meta
            new_evm = (
                replace(evm, source_language="nimony")
                if isinstance(evm, SourceMeta)
                else SourceMeta(source_language="nimony", source_name=ev.name)
            )
            new_vals.append(replace(ev, source_meta=new_evm))
        return replace(decl, values=new_vals, source_meta=new_meta)
    if isinstance(decl, (Typedef, Variable, Constant)):
        return replace(decl, source_meta=new_meta)
    return decl


class NimonyBackend:
    """HeaderKit parser backend for Nimony NIF (``.nif``) S-expression AST files and Nimony sources."""

    description: ClassVar[str] = "Nimony NIF S-expression AST parser with SourceMeta & ParsedDoc"
    supported_languages: frozenset[str] = frozenset({"nimony", "nif"})
    supported_classifications: frozenset[str] = frozenset({"interface", "source", "header"})
    supports_auto_install: ClassVar[bool] = False

    def __init__(
        self,
        *,
        doc_format: str = "auto",
        include_unexported: bool = False,
    ) -> None:
        self.doc_format = doc_format
        self.include_unexported = include_unexported

    def is_available(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "nimony"

    @property
    def supports_macros(self) -> bool:
        return True

    @property
    def supports_cpp(self) -> bool:
        return False

    @property
    def follows_includes(self) -> bool:
        return False

    @property
    def supports_compiler_args(self) -> bool:
        return False

    def get_name(self) -> str:
        return "nimony"

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
        include_unexported: bool | None = None,
        doc_format: str | None = None,
        **kwargs: Any,
    ) -> SourceUnit:
        _ = (
            include_dirs,
            extra_args,
            use_default_includes,
            recursive_includes,
            max_depth,
            project_prefixes,
            allowlist,
            denylist,
            kwargs,
        )
        eff_unexported = self.include_unexported if include_unexported is None else include_unexported
        eff_doc_format = self.doc_format if doc_format is None else doc_format

        nif_code = code
        if filename.endswith(".nim") and not code.lstrip().startswith(("(", ".nif")):
            nimony_bin = shutil.which("nimony") or shutil.which("nifler")
            if nimony_bin:
                with tempfile.TemporaryDirectory(prefix="hk_nimony_src_") as tmpdir:
                    src_file = Path(tmpdir) / Path(filename).name
                    src_file.write_text(code, encoding="utf-8")
                    nif_file = src_file.with_suffix(".nif")
                    subprocess.run(
                        [nimony_bin, "p", str(src_file), str(nif_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    if nif_file.is_file():
                        nif_code = nif_file.read_text(encoding="utf-8")

        ast_data = _run_nimony_nif_helper(filename, code=nif_code)
        raw_decls = _ast_json_to_declarations(ast_data, include_unexported=eff_unexported)
        decls = [_rewrite_source_language_to_nimony(d) for d in raw_decls]
        unit = SourceUnit(
            path=filename,
            declarations=decls,
            included_headers=set(),
        )
        if any(getattr(d, "doc", None) for d in unit.declarations):
            unit = DocParserTransform(doc_format=eff_doc_format).transform(unit)
        return unit


@hook("get_backend", language="nif", priority=Priority.STANDARD)
@hook("get_backend", language="nimony", priority=Priority.STANDARD)
def _nimony_get_backend_by_lang(context: PipelineContext | None = None) -> ParserBackend:
    _ = context
    return NimonyBackend()


@hook("parse_unit", language="nif", priority=Priority.STANDARD)
@hook("parse_unit", language="nimony", priority=Priority.STANDARD)
def _nimony_parse_unit_by_lang(
    code: str,
    filename: str = "input.nif",
    include_dirs: list[str] | None = None,
    extra_args: list[str] | None = None,
    *,
    context: PipelineContext | None = None,
    **kwargs: Any,
) -> SourceUnit | None:
    _ = context
    inst = NimonyBackend()
    return inst.parse(
        code,
        filename,
        include_dirs=include_dirs,
        extra_args=extra_args,
        **kwargs,
    )


register_backend("nimony", NimonyBackend)
