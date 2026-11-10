# Nimony NIF S-expression AST Extractor for HeaderKit NimonyBackend.
# Parses Nimony NIF (.nif, .s.nif, .p.nif) S-expression AST files using a
# deterministic token/S-expression parser and emits structured JSON matching
# the HeaderKit Nim/Nimony AST declaration schema to stdout.

import std/[json, os, strutils]

type
  NifKind = enum
    nkAtom
    nkStr
    nkList

  NifNode = ref object
    kind: NifKind
    text: string
    suffix: string
    children: seq[NifNode]
    line: int
    col: int

  PragmaData = object
    exportcName: string
    hasExportc: bool
    callingConvention: string
    isDeprecated: bool
    deprecatedMsg: string
    isPacked: bool
    isUnion: bool
    isBycopy: bool
    sizeExpr: string
    bitsize: int
    hasBitsize: bool
    attributes: seq[string]

proc decodeNifEscapes(s: string): string =
  var i = 0
  while i < s.len:
    if s[i] == '\\' and i + 2 < s.len and s[i + 1] in HexDigits and s[i + 2] in HexDigits:
      let hexStr = s[i + 1 .. i + 2]
      try:
        let code = parseHexInt(hexStr)
        result.add(char(code))
        i += 3
        continue
      except ValueError:
        discard
    elif s[i] == '\\' and i + 1 < s.len:
      let nxt = s[i + 1]
      case nxt
      of 'n': result.add('\n')
      of 'r': result.add('\r')
      of 't': result.add('\t')
      of '"': result.add('"')
      of '\\': result.add('\\')
      else: result.add(nxt)
      i += 2
      continue
    result.add(s[i])
    i += 1

proc stripModuleSuffix(ident: string): tuple[base: string, exported: bool] =
  var s = ident.strip()
  var exported = false
  if s.endsWith(".x"):
    exported = true
    s = s[0 ..< s.len - 2]
  # Nimony local/global disambiguator suffixes like `foo.0.mymod` or `Point.0.sys`
  let dotIdx = s.find('.')
  if dotIdx > 0:
    s = s[0 ..< dotIdx]
  return (s, exported)

proc parseNifSexpr(src: string): NifNode =
  result = NifNode(kind: nkList, text: "root", children: @[], line: 1, col: 1)
  var stack: seq[NifNode] = @[result]
  var i = 0
  var line = 1
  var col = 1

  while i < src.len:
    let ch = src[i]
    if ch in {' ', '\t', '\r'}:
      inc i
      inc col
    elif ch == '\n':
      inc i
      inc line
      col = 1
    elif ch == '#' or (ch == ';' and (i + 1 >= src.len or src[i + 1] == ';')):
      # Comment or directive line (.nif24)
      while i < src.len and src[i] != '\n':
        inc i
    elif ch == '.' and col == 1 and i + 6 <= src.len and src[i .. i + 5] == ".nif24":
      while i < src.len and src[i] != '\n':
        inc i
    elif ch == '(':
      let node = NifNode(kind: nkList, text: "", children: @[], line: line, col: col)
      stack[^1].children.add(node)
      stack.add(node)
      inc i
      inc col
    elif ch == ')':
      if stack.len > 1:
        discard stack.pop()
      inc i
      inc col
    elif ch == '"':
      let startLine = line
      let startCol = col
      inc i
      inc col
      var raw = ""
      while i < src.len and src[i] != '"':
        if src[i] == '\\' and i + 1 < src.len:
          raw.add(src[i])
          raw.add(src[i + 1])
          inc i, 2
          inc col, 2
        else:
          if src[i] == '\n':
            inc line
            col = 1
          else:
            inc col
          raw.add(src[i])
          inc i
      if i < src.len and src[i] == '"':
        inc i
        inc col
      let strNode = NifNode(
        kind: nkStr,
        text: decodeNifEscapes(raw),
        children: @[],
        line: startLine,
        col: startCol
      )
      stack[^1].children.add(strNode)
    else:
      # Atom or line info prefix (Wait: in NIF, line infos like `12,4` or `~3` precede nodes or appear as atoms)
      let startLine = line
      let startCol = col
      var tok = ""
      while i < src.len and src[i] notin {' ', '\t', '\r', '\n', '(', ')', '"'}:
        tok.add(src[i])
        inc i
        inc col
      # Skip standalone NIF line-info tokens like `1,2` or `~4,1`
      var isLineInfo = true
      for c in tok:
        if c notin {'0'..'9', ',', '~', '-'}:
          isLineInfo = false
          break
      if isLineInfo and (tok.contains(',') or tok.startsWith("~")):
        continue
      let atomNode = NifNode(
        kind: nkAtom,
        text: tok,
        children: @[],
        line: startLine,
        col: startCol
      )
      stack[^1].children.add(atomNode)

proc headTag(n: NifNode): string =
  if n != nil and n.kind == nkList and n.children.len > 0 and n.children[0].kind == nkAtom:
    return n.children[0].text
  return ""

proc renderNifNode(n: NifNode): string =
  if n == nil:
    return ""
  case n.kind
  of nkAtom:
    if n.text == ".":
      return ""
    let (base, _) = stripModuleSuffix(n.text)
    return base
  of nkStr:
    return n.text
  of nkList:
    let tag = headTag(n)
    case tag
    of "i":
      if n.children.len >= 2:
        let bits = n.children[1].text
        if bits == "-1": return "int"
        elif bits == "-8": return "int8"
        elif bits == "-16": return "int16"
        elif bits == "-32": return "int32"
        elif bits == "-64": return "int64"
        return "int" & bits
      return "int"
    of "u":
      if n.children.len >= 2:
        let bits = n.children[1].text.strip(chars = {'+'})
        if bits == "1" or bits == "-1": return "uint"
        elif bits == "8": return "uint8"
        elif bits == "16": return "uint16"
        elif bits == "32": return "uint32"
        elif bits == "64": return "uint64"
        return "uint" & bits
      return "uint"
    of "f":
      if n.children.len >= 2:
        let bits = n.children[1].text.strip(chars = {'+'})
        if bits == "32": return "float32"
        elif bits == "64": return "float64"
        return "float" & bits
      return "float64"
    of "bool":
      return "bool"
    of "char":
      return "char"
    of "cstring":
      return "cstring"
    of "void":
      return "void"
    of "ptr", "aptr":
      if n.children.len >= 2:
        return "ptr " & renderNifNode(n.children[1])
      return "pointer"
    of "ref":
      if n.children.len >= 2:
        return "ref " & renderNifNode(n.children[1])
      return "pointer"
    of "mut", "var", "out":
      if n.children.len >= 2:
        return "var " & renderNifNode(n.children[1])
      return "pointer"
    of "array":
      if n.children.len >= 3:
        return "array[" & renderNifNode(n.children[2]) & ", " & renderNifNode(n.children[1]) & "]"
      elif n.children.len == 2:
        return "UncheckedArray[" & renderNifNode(n.children[1]) & "]"
      return "array"
    of "range":
      if n.children.len >= 3:
        return renderNifNode(n.children[1]) & ".." & renderNifNode(n.children[2])
      return "range"
    else:
      var parts: seq[string] = @[]
      for c in n.children:
        let r = renderNifNode(c)
        if r.len > 0:
          parts.add(r)
      return parts.join(" ")

proc parsePragmas(n: NifNode, baseName: string): PragmaData =
  result.exportcName = baseName
  if n == nil or n.kind != nkList:
    return
  let tag = headTag(n)
  let items = if tag in ["pragmas", "pragma"]: n.children[1 .. ^1] else: @[n]
  for item in items:
    if item == nil:
      continue
    if item.kind == nkAtom:
      let kLow = item.text.toLowerAscii().replace("_", "")
      if kLow == "exportc":
        result.hasExportc = true
        result.attributes.add("exportc")
      elif kLow in ["cdecl", "stdcall", "fastcall", "safecall", "thiscall", "syscall", "noconv", "nimcall", "closure", "inline"]:
        result.callingConvention = kLow
        result.attributes.add(kLow)
      elif kLow == "deprecated":
        result.isDeprecated = true
        result.attributes.add("deprecated")
      elif kLow == "packed":
        result.isPacked = true
        result.attributes.add("packed")
      elif kLow == "union":
        result.isUnion = true
        result.attributes.add("union")
      elif kLow == "bycopy":
        result.isBycopy = true
        result.attributes.add("bycopy")
      elif kLow != ".":
        result.attributes.add(item.text)
    elif item.kind == nkList and item.children.len > 0:
      let pTag = headTag(item).toLowerAscii().replace("_", "")
      case pTag
      of "exportc":
        result.hasExportc = true
        result.attributes.add("exportc")
        if item.children.len >= 2:
          let s = renderNifNode(item.children[1]).strip()
          if s.contains("$1"):
            result.exportcName = s.replace("$1", baseName)
          elif s.len > 0:
            result.exportcName = s
      of "cdecl", "stdcall", "fastcall", "safecall", "thiscall", "syscall", "noconv", "nimcall", "closure", "inline":
        result.callingConvention = pTag
        result.attributes.add(pTag)
      of "deprecated":
        result.isDeprecated = true
        result.attributes.add("deprecated")
        if item.children.len >= 2:
          result.deprecatedMsg = renderNifNode(item.children[1]).strip()
      of "packed":
        result.isPacked = true
        result.attributes.add("packed")
      of "union":
        result.isUnion = true
        result.attributes.add("union")
      of "bycopy":
        result.isBycopy = true
        result.attributes.add("bycopy")
      of "size":
        result.attributes.add("size")
        if item.children.len >= 2:
          result.sizeExpr = renderNifNode(item.children[1]).strip()
      of "bitsize":
        result.attributes.add("bitsize")
        if item.children.len >= 2:
          try:
            result.bitsize = parseInt(renderNifNode(item.children[1]).strip())
            result.hasBitsize = true
          except ValueError:
            discard
      else:
        if pTag.len > 0 and pTag != ".":
          result.attributes.add(pTag)

proc serializeNifType(n: NifNode): JsonNode =
  if n == nil or (n.kind == nkAtom and (n.text == "." or n.text == "void")):
    return %*{"kind": "ctype", "name": "void", "source_type": "void"}
  let srcType = renderNifNode(n)
  if n.kind == nkAtom:
    let (base, _) = stripModuleSuffix(n.text)
    return %*{"kind": "ctype", "name": base, "source_type": base}
  if n.kind == nkStr:
    return %*{"kind": "ctype", "name": n.text, "source_type": n.text}

  let tag = headTag(n)
  case tag
  of "i", "u", "f", "bool", "char", "cstring", "void":
    return %*{"kind": "ctype", "name": srcType, "source_type": srcType}
  of "ptr", "aptr":
    let pointee = if n.children.len >= 2: serializeNifType(n.children[1]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "pointer",
      "pointee": pointee,
      "source_type": srcType
    }
  of "ref":
    let pointee = if n.children.len >= 2: serializeNifType(n.children[1]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "pointer",
      "pointee": pointee,
      "is_ref": true,
      "source_type": srcType
    }
  of "mut", "var", "out":
    let pointee = if n.children.len >= 2: serializeNifType(n.children[1]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "pointer",
      "pointee": pointee,
      "is_var": true,
      "source_type": srcType
    }
  of "distinct":
    let under = if n.children.len >= 2: serializeNifType(n.children[1]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "distinct",
      "underlying": under,
      "source_type": srcType
    }
  of "array":
    if n.children.len >= 3:
      let elem = serializeNifType(n.children[1])
      var arrSize = -1
      let szNode = n.children[2]
      if szNode.kind == nkAtom:
        try:
          arrSize = parseInt(szNode.text.strip(chars = {'+'}))
        except ValueError:
          discard
      elif szNode.kind == nkList and headTag(szNode) == "range" and szNode.children.len >= 3:
        try:
          let lo = parseInt(renderNifNode(szNode.children[1]).strip(chars = {'+'}))
          let hi = parseInt(renderNifNode(szNode.children[2]).strip(chars = {'+'}))
          arrSize = hi - lo + 1
        except ValueError:
          discard
      return %*{
        "kind": "array",
        "size": arrSize,
        "element_type": elem,
        "source_type": srcType
      }
    elif n.children.len == 2:
      return %*{
        "kind": "array",
        "size": newJNull(),
        "element_type": serializeNifType(n.children[1]),
        "source_type": srcType
      }
  of "proctype", "proc", "func":
    var retJson = %*{"kind": "ctype", "name": "void", "source_type": "void"}
    var paramsArr = newJArray()
    var cc = ""
    for idx in 1 ..< n.children.len:
      let ch = n.children[idx]
      let cTag = headTag(ch)
      if cTag == "params":
        for pNode in ch.children[1 .. ^1]:
          if headTag(pNode) in ["param", "fld"] and pNode.children.len >= 4:
            let (pName, _) = stripModuleSuffix(renderNifNode(pNode.children[1]))
            let pTypeJson = serializeNifType(pNode.children[3])
            paramsArr.add(%*{
              "name": pName,
              "type": pTypeJson,
              "default": newJNull()
            })
      elif cTag in ["pragmas", "pragma"]:
        let pData = parsePragmas(ch, "")
        cc = pData.callingConvention
      elif idx == 3 and cTag notin ["params", "pragmas", "pragma", "effects", "stmts"]:
        if not (ch.kind == nkAtom and ch.text == "."):
          retJson = serializeNifType(ch)
    return %*{
      "kind": "proc",
      "return_type": retJson,
      "parameters": paramsArr,
      "calling_convention": (if cc.len > 0: %cc else: newJNull()),
      "source_type": srcType
    }
  else:
    return %*{"kind": "ctype", "name": srcType, "source_type": srcType}

proc extractDocFromNodes(nodes: seq[NifNode]): string =
  var lines: seq[string] = @[]
  for ch in nodes:
    if ch == nil:
      continue
    if ch.kind == nkStr and ch.text.strip().len > 0:
      lines.add(ch.text.strip())
    elif ch.kind == nkList and headTag(ch) in ["comment", "doc"] and ch.children.len >= 2:
      let s = renderNifNode(ch.children[1]).strip()
      if s.len > 0:
        lines.add(s)
    elif ch.kind == nkList and headTag(ch) == "stmts":
      for st in ch.children[1 .. ^1]:
        if st.kind == nkStr and st.text.strip().len > 0:
          lines.add(st.text.strip())
        elif st.kind == nkList and headTag(st) in ["comment", "doc"] and st.children.len >= 2:
          let s = renderNifNode(st.children[1]).strip()
          if s.len > 0:
            lines.add(s)
        else:
          break
  return lines.join("\n").strip()

proc processNifNodes(nodes: seq[NifNode], currentFile: string, decls: var JsonNode) =
  var pendingDoc: seq[string] = @[]

  for n in nodes:
    if n == nil:
      continue
    if n.kind == nkStr:
      if n.text.strip().len > 0:
        pendingDoc.add(n.text.strip())
      continue
    if n.kind != nkList or n.children.len == 0:
      continue

    let tag = headTag(n)
    case tag
    of "stmts", "root":
      processNifNodes(n.children[1 .. ^1], currentFile, decls)

    of "comment", "doc":
      if n.children.len >= 2:
        let c = renderNifNode(n.children[1]).strip()
        if c.len > 0:
          pendingDoc.add(c)

    of "proc", "func", "method", "converter", "iterator", "template":
      # Nimony NIF proc layout:
      # (proc name.x export_marker pattern typevars (params ...) ret_type (pragmas ...) effects body)
      if n.children.len < 2:
        pendingDoc.setLen(0)
        continue
      let (rawName, suffixExported) = stripModuleSuffix(n.children[1].text)
      var isExported = suffixExported
      if n.children.len >= 3 and n.children[2].kind == nkAtom and n.children[2].text in ["x", ".x", "export"]:
        isExported = true

      var paramsNode: NifNode = nil
      var retNode: NifNode = nil
      var pragmaNode: NifNode = nil
      var bodyNodes: seq[NifNode] = @[]

      for idx in 2 ..< n.children.len:
        let ch = n.children[idx]
        let cTag = headTag(ch)
        if cTag == "params":
          paramsNode = ch
          if idx + 1 < n.children.len:
            let nxt = n.children[idx + 1]
            if headTag(nxt) notin ["pragmas", "pragma", "effects", "stmts"]:
              retNode = nxt
        elif cTag in ["pragmas", "pragma"]:
          pragmaNode = ch
        elif cTag == "stmts" or ch.kind == nkStr or cTag in ["comment", "doc"]:
          bodyNodes.add(ch)

      let pData = parsePragmas(pragmaNode, rawName)
      var docStr = extractDocFromNodes(bodyNodes)
      if docStr.len == 0 and pendingDoc.len > 0:
        docStr = pendingDoc.join("\n").strip()
      pendingDoc.setLen(0)

      let retJson = serializeNifType(retNode)
      var paramsArr = newJArray()
      if paramsNode != nil:
        for p in paramsNode.children[1 .. ^1]:
          if headTag(p) in ["param", "fld"] and p.children.len >= 2:
            let (pName, _) = stripModuleSuffix(p.children[1].text)
            var pTypeNode: NifNode = nil
            var pDefNode: NifNode = nil
            # (param name export pragma type default) or (param name type)
            if p.children.len >= 5:
              pTypeNode = p.children[4]
              if p.children.len >= 6:
                pDefNode = p.children[5]
            elif p.children.len == 4:
              pTypeNode = p.children[3]
            elif p.children.len == 3:
              pTypeNode = p.children[2]
            let defStr = if pDefNode != nil and not (pDefNode.kind == nkAtom and pDefNode.text == "."):
                           renderNifNode(pDefNode).strip()
                         else: ""
            paramsArr.add(%*{
              "name": pName,
              "type": serializeNifType(pTypeNode),
              "default": (if defStr.len > 0: %defStr else: newJNull())
            })

      decls.add(%*{
        "kind": "routine",
        "routine_kind": tag,
        "name": rawName,
        "c_name": pData.exportcName,
        "has_exportc": pData.hasExportc,
        "exported": isExported,
        "calling_convention": (if pData.callingConvention.len > 0: %pData.callingConvention else: newJNull()),
        "is_deprecated": pData.isDeprecated,
        "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
        "attributes": %pData.attributes,
        "return_type": retJson,
        "parameters": paramsArr,
        "doc": (if docStr.len > 0: %docStr else: newJNull()),
        "file": currentFile,
        "line": n.line,
        "column": n.col
      })

    of "type", "enum":
      # Nimony NIF type layout:
      # (type Name.x export typevars (pragmas ...) body)
      if n.children.len < 3:
        pendingDoc.setLen(0)
        continue
      let (rawName, suffixExported) = stripModuleSuffix(n.children[1].text)
      var isExported = suffixExported
      var pragmaNode: NifNode = nil
      var bodyNode: NifNode = n.children[^1]
      var docNodes: seq[NifNode] = @[]

      for idx in 2 ..< n.children.len:
        let ch = n.children[idx]
        if ch.kind == nkAtom and ch.text in ["x", ".x", "export"]:
          isExported = true
        elif headTag(ch) in ["pragmas", "pragma"]:
          pragmaNode = ch
        elif ch.kind == nkStr or headTag(ch) in ["comment", "doc"]:
          docNodes.add(ch)
        elif idx == n.children.len - 1 or headTag(ch) in ["object", "enum", "tuple", "distinct", "ptr", "aptr", "array", "proctype", "i", "u", "f", "bool", "cstring"]:
          bodyNode = ch

      let pData = parsePragmas(pragmaNode, rawName)
      var docStr = extractDocFromNodes(docNodes)
      if docStr.len == 0 and pendingDoc.len > 0:
        docStr = pendingDoc.join("\n").strip()
      pendingDoc.setLen(0)

      let bTag = if tag == "enum" and headTag(bodyNode) != "enum": "enum" else: headTag(bodyNode)
      if bTag in ["object", "tuple"]:
        var fieldsArr = newJArray()
        for fNode in bodyNode.children[1 .. ^1]:
          if fNode.kind == nkStr and docStr.len == 0:
            docStr = fNode.text.strip()
            continue
          if headTag(fNode) == "fld" and fNode.children.len >= 2:
            let (fName, suffixExp) = stripModuleSuffix(fNode.children[1].text)
            var fExp = suffixExp
            var fPragmaNode: NifNode = nil
            var fTypeNode: NifNode = nil
            var fDoc = ""
            for fIdx in 2 ..< fNode.children.len:
              let fc = fNode.children[fIdx]
              if fc.kind == nkAtom and fc.text in ["x", ".x", "export"]:
                fExp = true
              elif headTag(fc) in ["pragmas", "pragma"]:
                fPragmaNode = fc
              elif fc.kind == nkStr or headTag(fc) in ["comment", "doc"]:
                fDoc = renderNifNode(fc).strip()
              elif fTypeNode == nil and not (fc.kind == nkAtom and fc.text in [".", "x", ".x"]):
                fTypeNode = fc
            let fpData = parsePragmas(fPragmaNode, fName)
            fieldsArr.add(%*{
              "name": fName,
              "c_name": fpData.exportcName,
              "exported": fExp,
              "type": serializeNifType(fTypeNode),
              "bit_width": (if fpData.hasBitsize: %fpData.bitsize else: newJNull()),
              "doc": (if fDoc.len > 0: %fDoc else: newJNull()),
              "attributes": %fpData.attributes
            })
        decls.add(%*{
          "kind": "object",
          "name": rawName,
          "c_name": pData.exportcName,
          "has_exportc": pData.hasExportc,
          "exported": isExported,
          "is_packed": pData.isPacked,
          "is_union": pData.isUnion,
          "is_bycopy": pData.isBycopy or (bTag == "tuple"),
          "is_tuple": (bTag == "tuple"),
          "is_deprecated": pData.isDeprecated,
          "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
          "attributes": %pData.attributes,
          "fields": fieldsArr,
          "doc": (if docStr.len > 0: %docStr else: newJNull()),
          "file": currentFile,
          "line": n.line,
          "column": n.col
        })
      elif bTag == "enum":
        var valuesArr = newJArray()
        var nextVal = 0
        for eNode in bodyNode.children[1 .. ^1]:
          if headTag(eNode) == "efld" and eNode.children.len >= 2:
            let (eName, _) = stripModuleSuffix(eNode.children[1].text)
            var eVal = nextVal
            var eDoc = ""
            for eIdx in 2 ..< eNode.children.len:
              let ec = eNode.children[eIdx]
              if ec.kind == nkAtom and ec.text notin [".", "x", ".x"]:
                try:
                  eVal = parseInt(ec.text.strip(chars = {'+'}))
                except ValueError:
                  discard
              elif ec.kind == nkList and headTag(ec) == "tup" and ec.children.len >= 2:
                try:
                  eVal = parseInt(renderNifNode(ec.children[1]).strip(chars = {'+'}))
                except ValueError:
                  discard
              elif ec.kind == nkStr:
                eDoc = ec.text.strip()
            nextVal = eVal + 1
            valuesArr.add(%*{
              "name": eName,
              "value": eVal,
              "doc": (if eDoc.len > 0: %eDoc else: newJNull())
            })
        decls.add(%*{
          "kind": "enum",
          "name": rawName,
          "c_name": pData.exportcName,
          "has_exportc": pData.hasExportc,
          "exported": isExported,
          "size_expr": (if pData.sizeExpr.len > 0: %pData.sizeExpr else: newJNull()),
          "is_deprecated": pData.isDeprecated,
          "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
          "attributes": %pData.attributes,
          "values": valuesArr,
          "doc": (if docStr.len > 0: %docStr else: newJNull()),
          "file": currentFile,
          "line": n.line,
          "column": n.col
        })
      else:
        decls.add(%*{
          "kind": "typedef",
          "name": rawName,
          "c_name": pData.exportcName,
          "has_exportc": pData.hasExportc,
          "exported": isExported,
          "is_distinct": (bTag == "distinct"),
          "is_deprecated": pData.isDeprecated,
          "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
          "attributes": %pData.attributes,
          "underlying_type": serializeNifType(bodyNode),
          "doc": (if docStr.len > 0: %docStr else: newJNull()),
          "file": currentFile,
          "line": n.line,
          "column": n.col
        })

    of "const", "var", "let", "gvar", "tvar":
      if n.children.len < 2:
        pendingDoc.setLen(0)
        continue
      let (rawName, suffixExported) = stripModuleSuffix(n.children[1].text)
      var isExported = suffixExported
      var pragmaNode: NifNode = nil
      var typeNode: NifNode = nil
      var valNode: NifNode = nil

      for idx in 2 ..< n.children.len:
        let ch = n.children[idx]
        if ch.kind == nkAtom and ch.text in ["x", ".x", "export"]:
          isExported = true
        elif headTag(ch) in ["pragmas", "pragma"]:
          pragmaNode = ch
        elif typeNode == nil and not (ch.kind == nkAtom and ch.text == "."):
          typeNode = ch
        elif valNode == nil and not (ch.kind == nkAtom and ch.text == "."):
          valNode = ch

      let pData = parsePragmas(pragmaNode, rawName)
      let docStr = pendingDoc.join("\n").strip()
      pendingDoc.setLen(0)

      if tag == "const":
        var valJson = newJNull()
        var valKind = "expr"
        if valNode != nil:
          if valNode.kind == nkStr:
            valJson = %valNode.text
            valKind = "string"
          elif valNode.kind == nkAtom:
            let t = valNode.text.strip(chars = {'+'})
            try:
              valJson = %parseInt(t)
              valKind = "int"
            except ValueError:
              try:
                valJson = %parseFloat(t)
                valKind = "float"
              except ValueError:
                if t == "true":
                  valJson = %true
                  valKind = "bool"
                elif t == "false":
                  valJson = %false
                  valKind = "bool"
                else:
                  valJson = %t
          else:
            valJson = %renderNifNode(valNode)

        decls.add(%*{
          "kind": "const",
          "name": rawName,
          "c_name": pData.exportcName,
          "has_exportc": pData.hasExportc,
          "exported": isExported,
          "is_deprecated": pData.isDeprecated,
          "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
          "attributes": %pData.attributes,
          "type": (if typeNode != nil: serializeNifType(typeNode) else: newJNull()),
          "value": valJson,
          "value_kind": valKind,
          "doc": (if docStr.len > 0: %docStr else: newJNull()),
          "file": currentFile,
          "line": n.line,
          "column": n.col
        })
      else:
        decls.add(%*{
          "kind": "var",
          "is_let": (tag == "let"),
          "name": rawName,
          "c_name": pData.exportcName,
          "has_exportc": pData.hasExportc,
          "exported": isExported,
          "is_deprecated": pData.isDeprecated,
          "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
          "attributes": %pData.attributes,
          "type": serializeNifType(typeNode),
          "doc": (if docStr.len > 0: %docStr else: newJNull()),
          "file": currentFile,
          "line": n.line,
          "column": n.col
        })

    else:
      pendingDoc.setLen(0)

when isMainModule:
  let args = commandLineParams()
  if args.len < 1:
    stderr.writeLine("Usage: _nimony_nif_helper <file.nif> [--stdin]")
    quit(1)
  let targetPath = args[0]
  let useStdin = args.len >= 2 and args[1] == "--stdin"
  let src = if useStdin: stdin.readAll()
            elif fileExists(targetPath): readFile(targetPath)
            else: ""

  var decls = newJArray()
  if src.len > 0:
    let root = parseNifSexpr(src)
    processNifNodes(root.children, targetPath, decls)

  let outObj = %*{
    "path": targetPath,
    "included_files": newJArray(),
    "declarations": decls
  }
  echo($outObj)
