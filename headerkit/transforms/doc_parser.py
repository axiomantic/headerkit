"""Structured docstring parser and extensibility hooks.

Parses raw ``doc`` strings on IR declarations into normalized :class:`~headerkit.ir.ParsedDoc`
objects using deterministic non-regex line and token scanners. Supports Doxygen,
Sphinx/rST (including Nim ``runnableExamples:``), JSDoc, Google, NumPy,
Rustdoc/Markdown, and plain text docstring conventions, plus user-extensible
``preprocess_doc`` (waterfall) and ``parse_doc`` (first_result) hooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Literal

from headerkit.hooks import HookDispatcher, HookRegistry, PipelineContext, Priority, hook
from headerkit.ir import (
    Constant,
    Declaration,
    Enum,
    EnumValue,
    Field,
    Function,
    Header,
    ParamDoc,
    ParsedDoc,
    RaisesDoc,
    SourceUnit,
    Struct,
    Typedef,
    Variable,
)
from headerkit.transforms.filter import PipelineFilter, register_transform

logger = logging.getLogger(__name__)

DocFormat = Literal["auto", "doxygen", "rst", "sphinx", "jsdoc", "google", "numpy", "rustdoc", "markdown", "plain"]
ErrorMode = Literal["lenient", "warn", "strict"]


class DocParseError(ValueError):
    """Raised in ``strict`` mode when docstring parsing or validation fails."""


def _strip_comment_delimiters(raw: str) -> str:
    """Strip outer C/C++/Rust/Nim comment markers cleanly without regex while preserving indentation."""
    lines = raw.splitlines()
    cleaned: list[str] = []
    for line in lines:
        s = line.strip()
        if s in ("/**", "/*!", "/*", "*/", "*", "///", "//!", "//", "##"):
            cleaned.append("")
            continue
        cur = line
        stripped_lead = cur.lstrip()
        if stripped_lead.startswith(("/**", "/*!")):
            cur = stripped_lead[3:]
            if cur.startswith(" "):
                cur = cur[1:]
        elif stripped_lead.startswith("/*"):
            cur = stripped_lead[2:]
            if cur.startswith(" "):
                cur = cur[1:]
        elif stripped_lead.startswith(("///", "//!")):
            cur = stripped_lead[3:]
            if cur.startswith(" "):
                cur = cur[1:]
        elif stripped_lead.startswith(("//", "##")):
            cur = stripped_lead[2:]
            if cur.startswith(" "):
                cur = cur[1:]
        elif stripped_lead.startswith("*"):
            cur = stripped_lead[1:]
            if cur.startswith(" "):
                cur = cur[1:]
        if cur.rstrip().endswith("*/"):
            cur = cur.rstrip()[:-2]
        cleaned.append(cur.rstrip())
    return "\n".join(cleaned).strip("\n")


def _split_summary_and_body(paragraphs: list[str]) -> tuple[str | None, str | None]:
    """Split a list of non-empty text paragraphs into summary and body."""
    non_empty = [p.strip() for p in paragraphs if p.strip()]
    if not non_empty:
        return None, None
    summary = non_empty[0]
    body = "\n\n".join(non_empty[1:]) if len(non_empty) > 1 else None
    return summary, body


def _first_token_and_rest(text: str) -> tuple[str, str]:
    """Split text into first whitespace-delimited token and remaining stripped string."""
    s = text.strip()
    if not s:
        return "", ""
    idx = 0
    n = len(s)
    while idx < n and not s[idx].isspace():
        idx += 1
    first = s[:idx]
    rest = s[idx:].strip()
    if rest.startswith("- ") or rest.startswith(": "):
        rest = rest[2:].strip()
    elif rest == "-" or rest == ":":
        rest = ""
    return first, rest


def _is_ident_string(s: str) -> bool:
    """Return True if ``s`` is a valid identifier name (optional leading ``*``/``...``)."""
    t = s.lstrip("*.")
    if not t:
        return False
    return (t[0].isalpha() or t[0] == "_") and all(ch.isalnum() or ch == "_" for ch in t)


def _detect_format(text: str, language: str | None = None) -> str:
    """Deterministically detect docstring format from structural markers or source language."""
    stripped_doc = _strip_comment_delimiters(text)
    lines = [ln.strip() for ln in stripped_doc.splitlines()]
    # Check JSDoc markers first: @param {Type} or @returns {Type} or @throws {Type}
    for ln in lines:
        if ln.startswith(("@param {", "@returns {", "@return {", "@throws {", "@typedef {")):
            return "jsdoc"
    # Check Doxygen markers
    for ln in lines:
        if ln.startswith(
            (
                "@brief ",
                "\\brief ",
                "@param[",
                "\\param[",
                "@retval ",
                "\\retval ",
                "@code",
                "\\code",
            )
        ):
            return "doxygen"
        if ln.startswith(("@param ", "\\param ", "@return ", "\\return ", "@returns ", "\\returns ")):
            return "doxygen"
        if ln.startswith(("@deprecated", "\\deprecated", "@note ", "\\note ", "@warning ", "\\warning ")):
            return "doxygen"
        if ln.startswith(
            ("@see ", "\\see ", "@since ", "\\since ", "@exception ", "\\exception ", "@throws ", "\\throws ")
        ):
            return "doxygen"
    # Check Sphinx / rST / Nim doc markers
    for ln in lines:
        if ln.startswith((":param ", ":type ", ":return:", ":returns:", ":rtype:", ":raises ", ":raise ")):
            return "rst"
        if ln.startswith(
            (".. deprecated::", ".. note::", ".. warning::", ".. versionadded::", ".. seealso::", ".. code-block::")
        ):
            return "rst"
        if ln.startswith("runnableExamples:"):
            return "rst"
    # Check NumPy underlined section headers
    for i in range(len(lines) - 1):
        hdr = lines[i]
        underline = lines[i + 1]
        if (
            hdr in ("Parameters", "Returns", "Raises", "Notes", "Warnings", "Examples", "See Also", "Deprecated")
            and len(underline) >= 3
            and all(ch == "-" or ch == "=" for ch in underline)
        ):
            return "numpy"
    # Check Google-style headers
    for ln in lines:
        if ln in (
            "Args:",
            "Arguments:",
            "Parameters:",
            "Returns:",
            "Yields:",
            "Raises:",
            "Examples:",
            "Example:",
            "Note:",
            "Notes:",
            "Warning:",
            "Warnings:",
            "Deprecated:",
            "See Also:",
        ):
            return "google"
    # Check Rustdoc / Markdown headers
    for ln in lines:
        if ln.startswith(
            (
                "# Arguments",
                "# Parameters",
                "# Returns",
                "# Errors",
                "# Panics",
                "# Safety",
                "# Examples",
                "# Example",
                "# Deprecated",
            )
        ):
            return "rustdoc"
        if ln.startswith("```"):
            return "rustdoc"
    if language in ("rust",):
        return "rustdoc"
    if language in ("nim", "nimony", "nif"):
        return "rst"
    return "plain"


def _parse_doxygen(text: str) -> ParsedDoc:
    """Parse Doxygen docstring tags without regular expressions."""
    cleaned = _strip_comment_delimiters(text)
    lines = cleaned.splitlines()

    prose_paragraphs: list[str] = []
    cur_prose: list[str] = []
    brief_override: str | None = None
    params: list[ParamDoc] = []
    returns_parts: list[str] = []
    raises: list[RaisesDoc] = []
    deprecated: str | None = None
    examples: list[str] = []
    see_also: list[str] = []
    since: str | None = None
    notes: list[str] = []
    warnings: list[str] = []
    parse_errors: list[str] = []

    i = 0
    n = len(lines)
    active_tag: str | None = None
    active_buf: list[str] = []
    active_meta: dict[str, Any] = {}

    def flush_prose() -> None:
        if cur_prose:
            prose_paragraphs.append(" ".join(cur_prose).strip())
            cur_prose.clear()

    def flush_tag() -> None:
        nonlocal active_tag, active_buf, active_meta, brief_override, deprecated, since
        if active_tag is None:
            return
        content = " ".join(s.strip() for s in active_buf if s.strip()).strip()
        if active_tag == "brief":
            if content:
                brief_override = content
            else:
                parse_errors.append("Empty @brief tag")
        elif active_tag == "param":
            pname = active_meta.get("name", "")
            direction = active_meta.get("direction")
            if not pname:
                parse_errors.append("Malformed @param tag: missing parameter name")
            else:
                params.append(ParamDoc(name=pname, description=content, direction=direction))
        elif active_tag in ("return", "returns"):
            if content:
                returns_parts.append(content)
            else:
                parse_errors.append(f"Empty @{active_tag} tag")
        elif active_tag == "retval":
            rval = active_meta.get("name", "")
            if rval:
                returns_parts.append(f"{rval}: {content}" if content else rval)
            else:
                parse_errors.append("Malformed @retval tag: missing value")
        elif active_tag == "deprecated":
            deprecated = content or "Deprecated."
        elif active_tag == "note":
            if content:
                notes.append(content)
        elif active_tag == "warning":
            if content:
                warnings.append(content)
        elif active_tag == "see":
            if content:
                see_also.append(content)
        elif active_tag == "since":
            if content:
                since = content
        elif active_tag in ("exception", "throws", "throw"):
            exc_name = active_meta.get("name", "")
            if not exc_name:
                parse_errors.append(f"Malformed @{active_tag} tag: missing exception name")
            else:
                raises.append(RaisesDoc(exception=exc_name, description=content))
        active_tag = None
        active_buf = []
        active_meta = {}

    while i < n:
        raw_line = lines[i]
        stripped = raw_line.strip()

        if not stripped:
            flush_tag()
            flush_prose()
            i += 1
            continue

        if stripped.startswith(("@code", "\\code")):
            flush_tag()
            flush_prose()
            code_lines: list[str] = []
            i += 1
            closed = False
            while i < n:
                cl = lines[i]
                if cl.strip().startswith(("@endcode", "\\endcode")):
                    closed = True
                    i += 1
                    break
                code_lines.append(cl)
                i += 1
            if not closed:
                parse_errors.append("Unclosed @code block")
            examples.append("\n".join(code_lines).strip("\n"))
            continue

        if stripped[0] in ("@", "\\") and len(stripped) > 1 and stripped[1].isalpha():
            flush_tag()
            flush_prose()
            rest_line = stripped[1:]
            # Check for param[...]
            if rest_line.startswith("param["):
                close_br = rest_line.find("]")
                if close_br == -1:
                    parse_errors.append(f"Malformed @param direction syntax: {stripped}")
                    i += 1
                    continue
                raw_dir = rest_line[6:close_br].strip().lower().replace(" ", "")
                dir_val: Literal["in", "out", "inout"] | None = None
                if raw_dir == "in":
                    dir_val = "in"
                elif raw_dir == "out":
                    dir_val = "out"
                elif raw_dir in ("in,out", "inout", "out,in"):
                    dir_val = "inout"
                else:
                    parse_errors.append(f"Unknown @param direction: {raw_dir}")
                after_br = rest_line[close_br + 1 :].strip()
                pname, pdesc = _first_token_and_rest(after_br)
                active_tag = "param"
                active_meta = {"name": pname, "direction": dir_val}
                active_buf = [pdesc] if pdesc else []
                i += 1
                continue

            tag_name, after_tag = _first_token_and_rest(rest_line)
            tag_lower = tag_name.lower()
            if tag_lower in ("param", "retval", "exception", "throws", "throw"):
                target_name, desc = _first_token_and_rest(after_tag)
                active_tag = tag_lower
                active_meta = {"name": target_name}
                active_buf = [desc] if desc else []
            elif tag_lower in (
                "brief",
                "return",
                "returns",
                "deprecated",
                "note",
                "warning",
                "see",
                "since",
            ):
                active_tag = tag_lower
                active_buf = [after_tag] if after_tag else []
            elif tag_lower in ("details", "short"):
                if tag_lower == "short":
                    active_tag = "brief"
                    active_buf = [after_tag] if after_tag else []
                else:
                    if after_tag:
                        cur_prose.append(after_tag)
            else:
                parse_errors.append(f"Unrecognized Doxygen tag: @{tag_name}")
            i += 1
            continue

        if active_tag is not None:
            active_buf.append(stripped)
        else:
            cur_prose.append(stripped)
        i += 1

    flush_tag()
    flush_prose()

    summary: str | None
    body: str | None
    if brief_override is not None:
        summary = brief_override
        body = "\n\n".join(prose_paragraphs) if prose_paragraphs else None
    else:
        summary, body = _split_summary_and_body(prose_paragraphs)

    returns_str = "\n".join(returns_parts) if returns_parts else None
    return ParsedDoc(
        summary=summary,
        body=body,
        params=tuple(params),
        returns=returns_str,
        raises=tuple(raises),
        deprecated=deprecated,
        examples=tuple(examples),
        see_also=tuple(see_also),
        since=since,
        notes=tuple(notes),
        warnings=tuple(warnings),
        source_format="doxygen",
        parse_errors=tuple(parse_errors),
    )


def _parse_rst(text: str) -> ParsedDoc:
    """Parse Sphinx / reStructuredText / Nim docstrings without regular expressions."""
    cleaned = _strip_comment_delimiters(text)
    lines = cleaned.splitlines()

    prose_paragraphs: list[str] = []
    cur_prose: list[str] = []
    param_descs: dict[str, str] = {}
    param_types: dict[str, str] = {}
    param_order: list[str] = []
    returns_desc: str | None = None
    return_type_hint: str | None = None
    raises: list[RaisesDoc] = []
    deprecated: str | None = None
    examples: list[str] = []
    see_also: list[str] = []
    since: str | None = None
    notes: list[str] = []
    warnings: list[str] = []
    parse_errors: list[str] = []

    def flush_prose() -> None:
        if cur_prose:
            prose_paragraphs.append(" ".join(cur_prose).strip())
            cur_prose.clear()

    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]
        stripped = raw_line.strip()
        if not stripped:
            flush_prose()
            i += 1
            continue

        if stripped.startswith("runnableExamples:"):
            flush_prose()
            i += 1
            block_lines: list[str] = []
            base_indent: int | None = None
            while i < n:
                ln = lines[i]
                if not ln.strip():
                    block_lines.append("")
                    i += 1
                    continue
                ind = len(ln) - len(ln.lstrip(" "))
                if base_indent is None:
                    if ind == 0:
                        break
                    base_indent = ind
                if ind < base_indent:
                    break
                block_lines.append(ln[base_indent:])
                i += 1
            ex_str = "\n".join(block_lines).strip()
            if ex_str:
                examples.append(ex_str)
            else:
                parse_errors.append("Empty runnableExamples block")
            continue

        if stripped.startswith(".. "):
            flush_prose()
            after_dots = stripped[3:].strip()
            sep = after_dots.find("::")
            if sep == -1:
                parse_errors.append(f"Malformed rST directive: {stripped}")
                i += 1
                continue
            directive = after_dots[:sep].strip().lower()
            inline_arg = after_dots[sep + 2 :].strip()
            i += 1
            block_lines = []
            base_indent = None
            while i < n:
                ln = lines[i]
                if not ln.strip():
                    if block_lines:
                        block_lines.append("")
                    i += 1
                    continue
                ind = len(ln) - len(ln.lstrip(" "))
                if ind == 0:
                    break
                if base_indent is None:
                    base_indent = ind
                block_lines.append(ln[min(ind, base_indent) :])
                i += 1
            body_text = "\n".join(block_lines).strip()
            combined = f"{inline_arg}\n{body_text}".strip() if inline_arg and body_text else (inline_arg or body_text)
            if directive == "deprecated":
                deprecated = combined or "Deprecated."
            elif directive == "note":
                if combined:
                    notes.append(combined)
            elif directive == "warning":
                if combined:
                    warnings.append(combined)
            elif directive == "versionadded":
                if combined:
                    since = combined
            elif directive == "seealso":
                if combined:
                    see_also.append(combined)
            elif directive in ("code-block", "code"):
                if body_text:
                    examples.append(body_text)
                else:
                    parse_errors.append(f"Empty .. {directive}:: block")
            continue

        if stripped.startswith(":"):
            flush_prose()
            second_colon = stripped.find(":", 1)
            if second_colon == -1:
                parse_errors.append(f"Malformed rST field list line: {stripped}")
                i += 1
                continue
            field_spec = stripped[1:second_colon].strip()
            field_val_lines = [stripped[second_colon + 1 :].strip()]
            i += 1
            while i < n:
                ln = lines[i]
                if not ln.strip():
                    break
                ind = len(ln) - len(ln.lstrip(" "))
                if ind == 0 or ln.strip().startswith((":", ".. ")):
                    break
                field_val_lines.append(ln.strip())
                i += 1
            field_val = " ".join(s for s in field_val_lines if s).strip()
            parts = field_spec.split()
            if not parts:
                parse_errors.append(f"Empty rST field tag: {stripped}")
                continue
            tag = parts[0].lower()
            if tag in ("param", "parameter", "arg", "argument"):
                if len(parts) == 2:
                    pname = parts[1]
                elif len(parts) >= 3:
                    pname = parts[-1]
                    param_types[pname] = " ".join(parts[1:-1])
                else:
                    parse_errors.append(f"Missing parameter name in :{tag}:")
                    continue
                if pname not in param_order:
                    param_order.append(pname)
                param_descs[pname] = field_val
            elif tag == "type":
                if len(parts) >= 2:
                    pname = parts[-1]
                    if pname not in param_order:
                        param_order.append(pname)
                    param_types[pname] = field_val
                else:
                    parse_errors.append("Missing parameter name in :type:")
            elif tag in ("return", "returns"):
                returns_desc = field_val
            elif tag == "rtype":
                return_type_hint = field_val
            elif tag in ("raises", "raise", "except", "exception"):
                exc_name = parts[1] if len(parts) >= 2 else "Exception"
                raises.append(RaisesDoc(exception=exc_name, description=field_val))
            else:
                parse_errors.append(f"Unrecognized rST field :{tag}:")
            continue

        cur_prose.append(stripped)
        i += 1

    flush_prose()
    summary, body = _split_summary_and_body(prose_paragraphs)
    params = [
        ParamDoc(
            name=pname,
            description=param_descs.get(pname, ""),
            type_hint=param_types.get(pname),
        )
        for pname in param_order
    ]
    return ParsedDoc(
        summary=summary,
        body=body,
        params=tuple(params),
        returns=returns_desc,
        return_type_hint=return_type_hint,
        raises=tuple(raises),
        deprecated=deprecated,
        examples=tuple(examples),
        see_also=tuple(see_also),
        since=since,
        notes=tuple(notes),
        warnings=tuple(warnings),
        source_format="rst",
        parse_errors=tuple(parse_errors),
    )


def _extract_brace_type(s: str) -> tuple[str | None, str, str | None]:
    """Extract leading ``{Type}`` from ``s``, returning ``(type_hint, remainder, error)``."""
    stripped = s.strip()
    if not stripped.startswith("{"):
        return None, stripped, None
    depth = 0
    for idx, ch in enumerate(stripped):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                type_str = stripped[1:idx].strip()
                rest = stripped[idx + 1 :].strip()
                return type_str, rest, None
    return None, stripped, f"Unclosed brace type in JSDoc tag: {stripped}"


def _parse_jsdoc(text: str) -> ParsedDoc:
    """Parse JSDoc docstrings without regular expressions."""
    cleaned = _strip_comment_delimiters(text)
    lines = cleaned.splitlines()

    prose_paragraphs: list[str] = []
    cur_prose: list[str] = []
    params: list[ParamDoc] = []
    returns_desc: str | None = None
    return_type_hint: str | None = None
    raises: list[RaisesDoc] = []
    deprecated: str | None = None
    examples: list[str] = []
    see_also: list[str] = []
    since: str | None = None
    notes: list[str] = []
    warnings: list[str] = []
    parse_errors: list[str] = []

    def flush_prose() -> None:
        if cur_prose:
            prose_paragraphs.append(" ".join(cur_prose).strip())
            cur_prose.clear()

    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            flush_prose()
            i += 1
            continue

        if stripped.startswith("@"):
            flush_prose()
            tag_name, after_tag = _first_token_and_rest(stripped[1:])
            tag_lower = tag_name.lower()
            i += 1
            if tag_lower == "example":
                ex_lines: list[str] = [after_tag] if after_tag else []
                while i < n and not lines[i].strip().startswith("@"):
                    ex_lines.append(lines[i])
                    i += 1
                ex_str = "\n".join(ex_lines).strip()
                if ex_str:
                    examples.append(ex_str)
                continue

            cont_lines = [after_tag] if after_tag else []
            while i < n and lines[i].strip() and not lines[i].strip().startswith("@"):
                cont_lines.append(lines[i].strip())
                i += 1
            full_arg = " ".join(cont_lines).strip()

            if tag_lower in ("param", "arg", "argument"):
                thint, rest, err = _extract_brace_type(full_arg)
                if err:
                    parse_errors.append(err)
                    continue
                optional = False
                if thint and thint.endswith("="):
                    optional = True
                    thint = thint[:-1].strip()
                if rest.startswith("["):
                    close_sq = rest.find("]")
                    if close_sq == -1:
                        parse_errors.append(f"Unclosed optional parameter bracket in JSDoc: {rest}")
                        continue
                    optional = True
                    raw_pname = rest[1:close_sq].strip()
                    if "=" in raw_pname:
                        raw_pname = raw_pname.split("=", 1)[0].strip()
                    pname = raw_pname
                    pdesc = rest[close_sq + 1 :].strip()
                    if pdesc.startswith("- "):
                        pdesc = pdesc[2:].strip()
                    elif pdesc.startswith("-"):
                        pdesc = pdesc[1:].strip()
                else:
                    pname, pdesc = _first_token_and_rest(rest)
                if not pname:
                    parse_errors.append("Missing parameter name in JSDoc @param")
                else:
                    params.append(
                        ParamDoc(
                            name=pname,
                            description=pdesc,
                            type_hint=thint,
                            optional=optional,
                        )
                    )
            elif tag_lower in ("return", "returns"):
                thint, rest, err = _extract_brace_type(full_arg)
                if err:
                    parse_errors.append(err)
                    continue
                if thint:
                    return_type_hint = thint
                if rest.startswith("- "):
                    rest = rest[2:].strip()
                returns_desc = rest or None
            elif tag_lower in ("throws", "exception"):
                thint, rest, err = _extract_brace_type(full_arg)
                if err:
                    parse_errors.append(err)
                    continue
                if thint:
                    exc_name = thint
                    exc_desc = rest
                else:
                    exc_name, exc_desc = _first_token_and_rest(rest)
                if not exc_name:
                    parse_errors.append("Missing exception type in JSDoc @throws")
                else:
                    raises.append(RaisesDoc(exception=exc_name, description=exc_desc))
            elif tag_lower == "deprecated":
                deprecated = full_arg or "Deprecated."
            elif tag_lower == "see":
                if full_arg:
                    see_also.append(full_arg)
            elif tag_lower == "since":
                if full_arg:
                    since = full_arg
            elif tag_lower == "note":
                if full_arg:
                    notes.append(full_arg)
            elif tag_lower in ("warning", "warn"):
                if full_arg:
                    warnings.append(full_arg)
            else:
                parse_errors.append(f"Unrecognized JSDoc tag: @{tag_name}")
            continue

        cur_prose.append(stripped)
        i += 1

    flush_prose()
    summary, body = _split_summary_and_body(prose_paragraphs)
    return ParsedDoc(
        summary=summary,
        body=body,
        params=tuple(params),
        returns=returns_desc,
        return_type_hint=return_type_hint,
        raises=tuple(raises),
        deprecated=deprecated,
        examples=tuple(examples),
        see_also=tuple(see_also),
        since=since,
        notes=tuple(notes),
        warnings=tuple(warnings),
        source_format="jsdoc",
        parse_errors=tuple(parse_errors),
    )


_GOOGLE_SECTIONS: dict[str, str] = {
    "args:": "params",
    "arguments:": "params",
    "parameters:": "params",
    "returns:": "returns",
    "return:": "returns",
    "raises:": "raises",
    "examples:": "examples",
    "example:": "examples",
    "note:": "notes",
    "notes:": "notes",
    "warning:": "warnings",
    "warnings:": "warnings",
    "deprecated:": "deprecated",
    "see also:": "see_also",
}


def _parse_indented_entries(block_lines: list[str]) -> list[tuple[str, str]]:
    """Parse indented ``header: description`` entries inside Google-style sections."""
    entries: list[tuple[str, list[str]]] = []
    base_indent: int | None = None
    for raw in block_lines:
        s = raw.strip()
        if not s:
            continue
        ind = len(raw) - len(raw.lstrip(" "))
        if base_indent is None or ind <= base_indent:
            colon = s.find(":")
            if colon != -1:
                base_indent = ind
                head = s[:colon].strip()
                tail = s[colon + 1 :].strip()
                entries.append((head, [tail] if tail else []))
                continue
        if entries:
            entries[-1][1].append(s)
        else:
            entries.append(("", [s]))
    return [(head, " ".join(parts).strip()) for head, parts in entries]


def _parse_google(text: str) -> ParsedDoc:
    """Parse Google-style docstrings without regular expressions."""
    cleaned = _strip_comment_delimiters(text)
    lines = cleaned.splitlines()

    prose_paragraphs: list[str] = []
    cur_prose: list[str] = []
    params: list[ParamDoc] = []
    returns_desc: str | None = None
    return_type_hint: str | None = None
    raises: list[RaisesDoc] = []
    deprecated: str | None = None
    examples: list[str] = []
    see_also: list[str] = []
    notes: list[str] = []
    warnings: list[str] = []
    parse_errors: list[str] = []

    def flush_prose() -> None:
        if cur_prose:
            prose_paragraphs.append(" ".join(cur_prose).strip())
            cur_prose.clear()

    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            flush_prose()
            i += 1
            continue

        sec_key = stripped.lower()
        if sec_key in _GOOGLE_SECTIONS:
            flush_prose()
            sec_kind = _GOOGLE_SECTIONS[sec_key]
            sec_indent = len(lines[i]) - len(lines[i].lstrip(" "))
            i += 1
            block_lines: list[str] = []
            while i < n:
                ln = lines[i]
                st = ln.strip()
                if not st:
                    block_lines.append("")
                    i += 1
                    continue
                ind = len(ln) - len(ln.lstrip(" "))
                if ind <= sec_indent and st.lower() in _GOOGLE_SECTIONS:
                    break
                if ind <= sec_indent and block_lines:
                    break
                block_lines.append(ln)
                i += 1

            if sec_kind == "params":
                for head, desc in _parse_indented_entries(block_lines):
                    if not head:
                        parse_errors.append(f"Malformed Google Args entry: {desc}")
                        continue
                    thint: str | None = None
                    optional = False
                    pname = head
                    if "(" in head and head.endswith(")"):
                        open_p = head.find("(")
                        pname = head[:open_p].strip()
                        raw_type = head[open_p + 1 : -1].strip()
                        if raw_type.endswith(", optional"):
                            optional = True
                            raw_type = raw_type[: -len(", optional")].strip()
                        elif raw_type == "optional":
                            optional = True
                            raw_type = ""
                        thint = raw_type or None
                    params.append(
                        ParamDoc(
                            name=pname,
                            description=desc,
                            type_hint=thint,
                            optional=optional,
                        )
                    )
            elif sec_kind == "returns":
                entries = _parse_indented_entries(block_lines)
                if len(entries) == 1 and entries[0][0] and entries[0][1]:
                    return_type_hint = entries[0][0]
                    returns_desc = entries[0][1]
                else:
                    returns_desc = " ".join(ln.strip() for ln in block_lines if ln.strip()).strip() or None
            elif sec_kind == "raises":
                for head, desc in _parse_indented_entries(block_lines):
                    if head:
                        raises.append(RaisesDoc(exception=head, description=desc))
                    else:
                        parse_errors.append(f"Malformed Google Raises entry: {desc}")
            elif sec_kind == "examples":
                non_empty = [ln for ln in block_lines if ln.strip()]
                if non_empty:
                    min_ind = min(len(ln) - len(ln.lstrip(" ")) for ln in non_empty)
                    dedented = "\n".join(ln[min_ind:] if len(ln) >= min_ind else ln for ln in block_lines).strip()
                    if dedented:
                        examples.append(dedented)
            elif sec_kind == "notes":
                note_str = " ".join(ln.strip() for ln in block_lines if ln.strip()).strip()
                if note_str:
                    notes.append(note_str)
            elif sec_kind == "warnings":
                warn_str = " ".join(ln.strip() for ln in block_lines if ln.strip()).strip()
                if warn_str:
                    warnings.append(warn_str)
            elif sec_kind == "deprecated":
                dep_str = " ".join(ln.strip() for ln in block_lines if ln.strip()).strip()
                deprecated = dep_str or "Deprecated."
            elif sec_kind == "see_also":
                sa_str = " ".join(ln.strip() for ln in block_lines if ln.strip()).strip()
                if sa_str:
                    see_also.append(sa_str)
            continue

        cur_prose.append(stripped)
        i += 1

    flush_prose()
    summary, body = _split_summary_and_body(prose_paragraphs)
    return ParsedDoc(
        summary=summary,
        body=body,
        params=tuple(params),
        returns=returns_desc,
        return_type_hint=return_type_hint,
        raises=tuple(raises),
        deprecated=deprecated,
        examples=tuple(examples),
        see_also=tuple(see_also),
        notes=tuple(notes),
        warnings=tuple(warnings),
        source_format="google",
        parse_errors=tuple(parse_errors),
    )


_NUMPY_SECTIONS: set[str] = {
    "parameters",
    "returns",
    "raises",
    "notes",
    "warnings",
    "examples",
    "see also",
    "deprecated",
}


def _parse_numpy_items(sec_lines: list[str]) -> list[tuple[str, str | None, str]]:
    """Parse NumPy section items ``name : type`` followed by indented description lines."""
    items: list[tuple[str, str | None, list[str]]] = []
    i = 0
    n = len(sec_lines)
    while i < n:
        ln = sec_lines[i]
        st = ln.strip()
        if not st:
            i += 1
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        # Top-level item header within the section
        if ind == 0 or not items:
            if ":" in st:
                left, right = st.split(":", 1)
                head = left.strip()
                thint = right.strip() or None
            else:
                head = st
                thint = None
            i += 1
            desc_lines: list[str] = []
            while i < n:
                nxt = sec_lines[i]
                nxt_st = nxt.strip()
                if not nxt_st:
                    i += 1
                    continue
                nxt_ind = len(nxt) - len(nxt.lstrip(" "))
                if nxt_ind <= ind:
                    break
                desc_lines.append(nxt_st)
                i += 1
            items.append((head, thint, desc_lines))
        else:
            items[-1][2].append(st)
            i += 1
    return [(h, t, " ".join(d).strip()) for h, t, d in items]


def _parse_numpy(text: str) -> ParsedDoc:
    """Parse NumPy-style docstrings without regular expressions."""
    cleaned = _strip_comment_delimiters(text)
    lines = cleaned.splitlines()

    prose_paragraphs: list[str] = []
    cur_prose: list[str] = []
    params: list[ParamDoc] = []
    returns_desc: str | None = None
    return_type_hint: str | None = None
    raises: list[RaisesDoc] = []
    deprecated: str | None = None
    examples: list[str] = []
    see_also: list[str] = []
    notes: list[str] = []
    warnings: list[str] = []
    parse_errors: list[str] = []

    def flush_prose() -> None:
        if cur_prose:
            prose_paragraphs.append(" ".join(cur_prose).strip())
            cur_prose.clear()

    def is_section_header(idx: int) -> str | None:
        if idx + 1 >= len(lines):
            return None
        title = lines[idx].strip().lower()
        underline = lines[idx + 1].strip()
        if title in _NUMPY_SECTIONS and len(underline) >= 3 and all(ch in "-=" for ch in underline):
            return title
        return None

    i = 0
    n = len(lines)
    while i < n:
        sec_title = is_section_header(i)
        if sec_title is not None:
            flush_prose()
            i += 2
            sec_lines: list[str] = []
            while i < n and is_section_header(i) is None:
                sec_lines.append(lines[i])
                i += 1
            # Dedent section lines
            non_empty = [ln for ln in sec_lines if ln.strip()]
            if non_empty:
                min_ind = min(len(ln) - len(ln.lstrip(" ")) for ln in non_empty)
                dedented = [ln[min_ind:] if len(ln) >= min_ind else ln for ln in sec_lines]
            else:
                dedented = []

            if sec_title == "parameters":
                for head, thint, desc in _parse_numpy_items(dedented):
                    optional = False
                    if thint:
                        if thint.endswith(", optional"):
                            optional = True
                            thint = thint[: -len(", optional")].strip() or None
                        elif thint == "optional":
                            optional = True
                            thint = None
                    params.append(ParamDoc(name=head, description=desc, type_hint=thint, optional=optional))
            elif sec_title == "returns":
                items = _parse_numpy_items(dedented)
                if items:
                    head, thint, desc = items[0]
                    if thint is not None:
                        return_type_hint = thint
                        returns_desc = desc or head
                    else:
                        return_type_hint = head if desc else None
                        returns_desc = desc or head
            elif sec_title == "raises":
                for head, _thint, desc in _parse_numpy_items(dedented):
                    raises.append(RaisesDoc(exception=head, description=desc))
            elif sec_title == "notes":
                note_text = " ".join(ln.strip() for ln in dedented if ln.strip()).strip()
                if note_text:
                    notes.append(note_text)
            elif sec_title == "warnings":
                warn_text = " ".join(ln.strip() for ln in dedented if ln.strip()).strip()
                if warn_text:
                    warnings.append(warn_text)
            elif sec_title == "examples":
                ex_text = "\n".join(dedented).strip()
                if ex_text:
                    examples.append(ex_text)
            elif sec_title == "see also":
                sa_text = " ".join(ln.strip() for ln in dedented if ln.strip()).strip()
                if sa_text:
                    see_also.append(sa_text)
            elif sec_title == "deprecated":
                dep_text = " ".join(ln.strip() for ln in dedented if ln.strip()).strip()
                deprecated = dep_text or "Deprecated."
            continue

        stripped = lines[i].strip()
        if not stripped:
            flush_prose()
        else:
            cur_prose.append(stripped)
        i += 1

    flush_prose()
    summary, body = _split_summary_and_body(prose_paragraphs)
    return ParsedDoc(
        summary=summary,
        body=body,
        params=tuple(params),
        returns=returns_desc,
        return_type_hint=return_type_hint,
        raises=tuple(raises),
        deprecated=deprecated,
        examples=tuple(examples),
        see_also=tuple(see_also),
        notes=tuple(notes),
        warnings=tuple(warnings),
        source_format="numpy",
        parse_errors=tuple(parse_errors),
    )


def _parse_rustdoc(text: str) -> ParsedDoc:
    """Parse Rustdoc / Markdown docstrings without regular expressions."""
    cleaned = _strip_comment_delimiters(text)
    lines = cleaned.splitlines()

    prose_paragraphs: list[str] = []
    cur_prose: list[str] = []
    params: list[ParamDoc] = []
    returns_desc: str | None = None
    raises: list[RaisesDoc] = []
    deprecated: str | None = None
    examples: list[str] = []
    notes: list[str] = []
    warnings: list[str] = []
    parse_errors: list[str] = []

    def flush_prose() -> None:
        if cur_prose:
            prose_paragraphs.append(" ".join(cur_prose).strip())
            cur_prose.clear()

    section: str | None = None
    sec_buf: list[str] = []

    def flush_section() -> None:
        nonlocal section, sec_buf, returns_desc, deprecated
        if section is None:
            return
        if section in ("arguments", "parameters"):
            for ln in sec_buf:
                st = ln.strip()
                if st.startswith(("* ", "- ")):
                    item = st[2:].strip()
                    sep = item.find(" - ")
                    if sep == -1:
                        sep = item.find(": ")
                        sep_len = 2
                    else:
                        sep_len = 3
                    if sep != -1:
                        raw_name = item[:sep].strip().strip("`")
                        pdesc = item[sep + sep_len :].strip()
                        params.append(ParamDoc(name=raw_name, description=pdesc))
                    else:
                        parse_errors.append(f"Malformed Rustdoc argument bullet: {st}")
        elif section == "returns":
            rtext = " ".join(s.strip() for s in sec_buf if s.strip()).strip()
            if rtext:
                returns_desc = rtext
        elif section in ("errors", "panics"):
            etext = " ".join(s.strip() for s in sec_buf if s.strip()).strip()
            if etext:
                exc_kind = "Error" if section == "errors" else "Panic"
                raises.append(RaisesDoc(exception=exc_kind, description=etext))
        elif section == "safety":
            stext = " ".join(s.strip() for s in sec_buf if s.strip()).strip()
            if stext:
                warnings.append(f"Safety: {stext}")
        elif section == "notes":
            ntext = " ".join(s.strip() for s in sec_buf if s.strip()).strip()
            if ntext:
                notes.append(ntext)
        elif section == "deprecated":
            dtext = " ".join(s.strip() for s in sec_buf if s.strip()).strip()
            deprecated = dtext or "Deprecated."
        section = None
        sec_buf = []

    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            flush_prose()
            i += 1
            code_lines: list[str] = []
            closed = False
            while i < n:
                if lines[i].strip().startswith("```"):
                    closed = True
                    i += 1
                    break
                code_lines.append(lines[i])
                i += 1
            if not closed:
                parse_errors.append("Unclosed fenced code block in Markdown/Rustdoc")
            examples.append("\n".join(code_lines).strip("\n"))
            continue

        if stripped.startswith("#"):
            flush_prose()
            flush_section()
            heading = stripped.lstrip("#").strip().lower()
            if heading in (
                "arguments",
                "parameters",
                "returns",
                "errors",
                "panics",
                "safety",
                "examples",
                "example",
                "notes",
                "deprecated",
            ):
                section = "examples" if heading == "example" else heading
                i += 1
                continue

        if section is not None:
            if section != "examples":
                sec_buf.append(raw_line)
            i += 1
            continue

        if not stripped:
            flush_prose()
        else:
            cur_prose.append(stripped)
        i += 1

    flush_prose()
    flush_section()
    summary, body = _split_summary_and_body(prose_paragraphs)
    return ParsedDoc(
        summary=summary,
        body=body,
        params=tuple(params),
        returns=returns_desc,
        raises=tuple(raises),
        deprecated=deprecated,
        examples=tuple(examples),
        notes=tuple(notes),
        warnings=tuple(warnings),
        source_format="rustdoc",
        parse_errors=tuple(parse_errors),
    )


def _parse_plain(text: str) -> ParsedDoc:
    """Parse plain-text docstrings into summary and body paragraphs."""
    cleaned = _strip_comment_delimiters(text)
    paragraphs: list[str] = []
    cur: list[str] = []
    for ln in cleaned.splitlines():
        st = ln.strip()
        if not st:
            if cur:
                paragraphs.append("\n".join(cur).strip())
                cur.clear()
        else:
            cur.append(st)
    if cur:
        paragraphs.append("\n".join(cur).strip())
    summary, body = _split_summary_and_body(paragraphs)
    return ParsedDoc(summary=summary, body=body, source_format="plain")


# =============================================================================
# Built-in @hook("parse_doc") Implementations
# =============================================================================


@hook("parse_doc", priority=Priority.STANDARD, doc_format="doxygen")
def _hook_parse_doxygen(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "doxygen",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_doxygen(raw_doc)


@hook("parse_doc", priority=Priority.STANDARD, doc_format="rst")
def _hook_parse_rst(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "rst",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_rst(raw_doc)


@hook("parse_doc", priority=Priority.STANDARD, doc_format="sphinx")
def _hook_parse_sphinx(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "sphinx",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return replace(_parse_rst(raw_doc), source_format="sphinx")


@hook("parse_doc", priority=Priority.STANDARD, doc_format="jsdoc")
def _hook_parse_jsdoc(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "jsdoc",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_jsdoc(raw_doc)


@hook("parse_doc", priority=Priority.STANDARD, doc_format="google")
def _hook_parse_google(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "google",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_google(raw_doc)


@hook("parse_doc", priority=Priority.STANDARD, doc_format="numpy")
def _hook_parse_numpy(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "numpy",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_numpy(raw_doc)


@hook("parse_doc", priority=Priority.STANDARD, doc_format="rustdoc")
def _hook_parse_rustdoc(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "rustdoc",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_rustdoc(raw_doc)


@hook("parse_doc", priority=Priority.STANDARD, doc_format="markdown")
def _hook_parse_markdown(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "markdown",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return replace(_parse_rustdoc(raw_doc), source_format="markdown")


@hook("parse_doc", priority=Priority.STANDARD, doc_format="plain")
def _hook_parse_plain(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "plain",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = (context, doc_format, on_error)
    return _parse_plain(raw_doc)


@hook("parse_doc", priority=Priority.FALLBACK)
def _hook_parse_auto(
    raw_doc: str,
    _decl: Any,
    *,
    context: PipelineContext,
    doc_format: str = "auto",
    on_error: str = "lenient",
) -> ParsedDoc:
    _ = on_error
    detected = _detect_format(raw_doc, language=context.language) if doc_format == "auto" else doc_format
    if detected == "doxygen":
        return _parse_doxygen(raw_doc)
    if detected in ("rst", "sphinx"):
        res = _parse_rst(raw_doc)
        return replace(res, source_format="sphinx") if detected == "sphinx" else res
    if detected == "jsdoc":
        return _parse_jsdoc(raw_doc)
    if detected == "google":
        return _parse_google(raw_doc)
    if detected == "numpy":
        return _parse_numpy(raw_doc)
    if detected in ("rustdoc", "markdown"):
        res = _parse_rustdoc(raw_doc)
        return replace(res, source_format="markdown") if detected == "markdown" else res
    return _parse_plain(raw_doc)


def _is_synthetic_param_name(name: str | None) -> bool:
    """Return True if a parameter name is absent or synthetic placeholder (e.g. ``param_0``, ``arg0``)."""
    if not name:
        return True
    for prefix in ("param_", "arg_", "param", "arg", "a", "p"):
        if name.startswith(prefix) and name[len(prefix) :].isdigit():
            return True
    return False


# =============================================================================
# DocParserTransform
# =============================================================================


@dataclass
class DocParserTransform(PipelineFilter):
    """Layer-2 IR transform that parses raw ``doc`` strings into structured :class:`~headerkit.ir.ParsedDoc`.

    Executes ``preprocess_doc`` (waterfall) and ``parse_doc`` (first_result) hooks,
    propagates parameter documentation onto :class:`~headerkit.ir.Parameter` nodes,
    backfills synthetic parameter names when documented positionally, and enforces
    configurable validation in ``lenient``, ``warn``, or ``strict`` mode.
    """

    doc_format: str = "auto"
    on_error: ErrorMode = "lenient"
    require_docstrings: bool = False
    require_param_docs: bool = False
    registry: HookRegistry | None = None

    def cache_fingerprint(self) -> dict[str, object]:
        return {
            "name": "doc-parser",
            "doc_format": self.doc_format,
            "on_error": self.on_error,
            "require_docstrings": self.require_docstrings,
            "require_param_docs": self.require_param_docs,
        }

    def _handle_diagnostics(self, decl_name: str, errors: list[str]) -> None:
        if not errors:
            return
        if self.on_error == "strict":
            raise DocParseError(f"Docstring errors on {decl_name!r}: {'; '.join(errors)}")
        if self.on_error == "warn":
            for err in errors:
                logger.warning("Docstring warning on %s: %s", decl_name, err)

    def _parse_single_doc(
        self,
        raw_doc: str | None,
        decl: Any,
        decl_name: str,
        dispatcher: HookDispatcher,
        base_lang: str,
    ) -> ParsedDoc | None:
        if not raw_doc or not raw_doc.strip():
            if self.require_docstrings:
                self._handle_diagnostics(decl_name, [f"Missing required docstring on {decl_name!r}"])
            return getattr(decl, "parsed_doc", None)

        effective_format = _detect_format(raw_doc, language=base_lang) if self.doc_format == "auto" else self.doc_format
        ctx = PipelineContext(
            language=base_lang,
            options={"doc_format": effective_format, "on_error": self.on_error},
        )
        preprocessed = dispatcher.waterfall("preprocess_doc", raw_doc, decl, context=ctx)
        if effective_format != self.doc_format and self.doc_format == "auto":
            # Re-detect after preprocessing in case preprocess_doc transformed banners into tags
            effective_format = _detect_format(preprocessed, language=base_lang)
            ctx = PipelineContext(
                language=base_lang,
                options={"doc_format": effective_format, "on_error": self.on_error},
            )

        parsed: ParsedDoc | None = dispatcher.first_result(
            "parse_doc",
            preprocessed,
            decl,
            context=ctx,
            doc_format=effective_format,
            on_error=self.on_error,
        )
        if parsed is None:
            err_msg = f"No parse_doc hook produced a ParsedDoc for {decl_name!r} (format={effective_format!r})"
            self._handle_diagnostics(decl_name, [err_msg])
            return ParsedDoc(summary=preprocessed.strip(), source_format=effective_format, parse_errors=(err_msg,))

        return parsed

    def _transform_function(
        self,
        fn: Function,
        dispatcher: HookDispatcher,
        base_lang: str,
    ) -> Function:
        parsed = self._parse_single_doc(fn.doc, fn, fn.name, dispatcher, base_lang)
        diagnostics: list[str] = list(parsed.parse_errors) if parsed else []

        new_params = list(fn.parameters)
        if parsed and parsed.params:
            # First check if all parameters have synthetic/missing names and counts match
            if len(new_params) == len(parsed.params) and all(_is_synthetic_param_name(p.name) for p in new_params):
                for idx, (param, pdoc) in enumerate(zip(new_params, parsed.params, strict=True)):
                    param_pdoc = ParsedDoc(
                        summary=pdoc.description,
                        return_type_hint=pdoc.type_hint,
                        source_format=parsed.source_format,
                    )
                    new_params[idx] = replace(
                        param,
                        name=pdoc.name if _is_synthetic_param_name(param.name) else param.name,
                        doc=param.doc or pdoc.description,
                        parsed_doc=param.parsed_doc or param_pdoc,
                    )
            else:
                param_by_name = {p.name: idx for idx, p in enumerate(new_params) if p.name}
                for pdoc in parsed.params:
                    if pdoc.name in param_by_name:
                        idx = param_by_name[pdoc.name]
                        existing = new_params[idx]
                        param_pdoc = ParsedDoc(
                            summary=pdoc.description,
                            return_type_hint=pdoc.type_hint,
                            source_format=parsed.source_format,
                        )
                        new_params[idx] = replace(
                            existing,
                            doc=existing.doc or pdoc.description,
                            parsed_doc=existing.parsed_doc or param_pdoc,
                        )
                    else:
                        diagnostics.append(
                            f"Documented parameter {pdoc.name!r} does not match any parameter of {fn.name!r}"
                        )

        if self.require_param_docs and new_params:
            documented_names = {p.name for p in parsed.params} if parsed else set()
            for p in new_params:
                if p.name and p.name not in documented_names and not p.doc:
                    diagnostics.append(f"Parameter {p.name!r} of {fn.name!r} is missing documentation")

        if parsed and tuple(diagnostics) != parsed.parse_errors:
            parsed = replace(parsed, parse_errors=tuple(diagnostics))

        self._handle_diagnostics(fn.name, diagnostics)

        is_dep = fn.is_deprecated or (parsed is not None and parsed.deprecated is not None)
        return replace(
            fn,
            parameters=new_params,
            parsed_doc=parsed,
            is_deprecated=is_dep,
        )

    def _transform_struct(
        self,
        st: Struct,
        dispatcher: HookDispatcher,
        base_lang: str,
    ) -> Struct:
        st_name = st.name or "<anonymous struct>"
        parsed = self._parse_single_doc(st.doc, st, st_name, dispatcher, base_lang)
        if parsed:
            self._handle_diagnostics(st_name, list(parsed.parse_errors))

        # Map @param or field docs from struct docstring onto fields if matching
        field_doc_map: dict[str, ParamDoc] = {}
        if parsed and parsed.params:
            field_doc_map = {p.name: p for p in parsed.params}

        new_fields: list[Field] = []
        for fld in st.fields:
            f_parsed = self._parse_single_doc(
                fld.doc,
                fld,
                f"{st_name}.{fld.name}",
                dispatcher,
                base_lang,
            )
            f_doc = fld.doc
            if f_parsed is None and fld.name in field_doc_map:
                pdoc = field_doc_map[fld.name]
                f_doc = f_doc or pdoc.description
                f_parsed = ParsedDoc(
                    summary=pdoc.description,
                    return_type_hint=pdoc.type_hint,
                    source_format=parsed.source_format if parsed else "plain",
                )
            elif f_parsed:
                self._handle_diagnostics(f"{st_name}.{fld.name}", list(f_parsed.parse_errors))
            new_fields.append(replace(fld, doc=f_doc, parsed_doc=f_parsed))

        new_methods = [self._transform_function(m, dispatcher, base_lang) for m in st.methods]
        new_ctors = [self._transform_function(c, dispatcher, base_lang) for c in st.constructors]
        new_dtor = self._transform_function(st.destructor, dispatcher, base_lang) if st.destructor else None
        new_nested = [self._transform_struct(nr, dispatcher, base_lang) for nr in st.nested_records]
        is_dep = st.is_deprecated or (parsed is not None and parsed.deprecated is not None)
        return replace(
            st,
            fields=new_fields,
            methods=new_methods,
            constructors=new_ctors,
            destructor=new_dtor,
            nested_records=new_nested,
            parsed_doc=parsed,
            is_deprecated=is_dep,
        )

    def _transform_enum(
        self,
        en: Enum,
        dispatcher: HookDispatcher,
        base_lang: str,
    ) -> Enum:
        en_name = en.name or "<anonymous enum>"
        parsed = self._parse_single_doc(en.doc, en, en_name, dispatcher, base_lang)
        if parsed:
            self._handle_diagnostics(en_name, list(parsed.parse_errors))
        new_values: list[EnumValue] = []
        for ev in en.values:
            ev_parsed = self._parse_single_doc(ev.doc, ev, f"{en_name}.{ev.name}", dispatcher, base_lang)
            if ev_parsed:
                self._handle_diagnostics(f"{en_name}.{ev.name}", list(ev_parsed.parse_errors))
            new_values.append(replace(ev, parsed_doc=ev_parsed))
        return replace(en, values=new_values, parsed_doc=parsed)

    def transform(self, unit: SourceUnit | Header, context: PipelineContext | None = None) -> SourceUnit:
        _ = context
        dispatcher = HookDispatcher(registry=self.registry)
        base_lang = unit.language or "c"
        new_decls: list[Declaration] = []
        for decl in unit.declarations:
            if isinstance(decl, Function):
                new_decls.append(self._transform_function(decl, dispatcher, base_lang))
            elif isinstance(decl, Struct):
                new_decls.append(self._transform_struct(decl, dispatcher, base_lang))
            elif isinstance(decl, Enum):
                new_decls.append(self._transform_enum(decl, dispatcher, base_lang))
            elif isinstance(decl, (Typedef, Variable, Constant)):
                parsed = self._parse_single_doc(decl.doc, decl, decl.name, dispatcher, base_lang)
                if parsed:
                    self._handle_diagnostics(decl.name, list(parsed.parse_errors))
                if isinstance(decl, (Typedef, Variable)):
                    is_dep = decl.is_deprecated or (parsed is not None and parsed.deprecated is not None)
                    new_decls.append(replace(decl, parsed_doc=parsed, is_deprecated=is_dep))
                else:
                    new_decls.append(replace(decl, parsed_doc=parsed))
            else:
                new_decls.append(decl)
        return replace(unit, declarations=new_decls)


register_transform("doc-parser", DocParserTransform)
