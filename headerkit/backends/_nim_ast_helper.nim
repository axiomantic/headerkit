# Nim Compiler AST Extractor for HeaderKit NimBackend.
# Parses .nim source files (and recursively follows nkIncludeStmt nodes) using
# Nim's built-in compiler/[ast, parser, idents, options, lineinfos, renderer]
# when compiled as a native helper binary, or std/macros parseStmt when
# executed via `nim e` (NimScript fallback), and emits structured JSON to stdout.

import std/[json, os, strutils, sets]

when defined(nimscript):
  import std/macros

  type
    PNode = NimNode
    IdentCache = int
    ConfigRef = int

  proc newConfigRef(): ConfigRef = 0
  proc newIdentCache(): IdentCache = 0

  const
    nkEmpty = nnkEmpty
    nkIdent = nnkIdent
    nkSym = nnkSym
    nkAccQuoted = nnkAccQuoted
    nkPostfix = nnkPostfix
    nkPragmaExpr = nnkPragmaExpr
    nkPragma = nnkPragma
    nkExprColonExpr = nnkExprColonExpr
    nkExprEqExpr = nnkExprEqExpr
    nkCall = nnkCall
    nkCommand = nnkCommand
    nkCharLit = nnkCharLit
    nkUInt64Lit = nnkUInt64Lit
    nkFloatLit = nnkFloatLit
    nkFloat128Lit = nnkFloat128Lit
    nkStrLit = nnkStrLit
    nkTripleStrLit = nnkTripleStrLit
    nkPtrTy = nnkPtrTy
    nkVarTy = nnkVarTy
    nkRefTy = nnkRefTy
    nkDistinctTy = nnkDistinctTy
    nkBracketExpr = nnkBracketExpr
    nkInfix = nnkInfix
    nkProcTy = nnkProcTy
    nkFormalParams = nnkFormalParams
    nkIdentDefs = nnkIdentDefs
    nkTupleTy = nnkTupleTy
    nkTupleClassTy = nnkTupleClassTy
    nkRecList = nnkRecList
    nkCommentStmt = nnkCommentStmt
    nkRecCase = nnkRecCase
    nkIncludeStmt = nnkIncludeStmt
    nkProcDef = nnkProcDef
    nkFuncDef = nnkFuncDef
    nkMethodDef = nnkMethodDef
    nkConverterDef = nnkConverterDef
    nkIteratorDef = nnkIteratorDef
    nkTemplateDef = nnkTemplateDef
    nkStmtList = nnkStmtList
    nkTypeSection = nnkTypeSection
    nkTypeDef = nnkTypeDef
    nkObjectTy = nnkObjectTy
    nkEnumTy = nnkEnumTy
    nkEnumFieldDef = nnkEnumFieldDef
    nkTupleConstr = nnkTupleConstr
    nkConstSection = nnkConstSection
    nkConstDef = nnkConstDef
    nkVarSection = nnkVarSection
    nkLetSection = nnkLetSection
    nkWhenStmt = nnkWhenStmt

  var sourceLinesStack: seq[seq[string]] = @[]

  proc currentSourceLines(): seq[string] =
    if sourceLinesStack.len > 0: sourceLinesStack[^1] else: @[]

  proc renderTree(n: PNode): string =
    if n == nil: "" else: repr(n)

  proc nodeIdentStr(n: PNode): string =
    n.strVal

  proc nodeSymStr(n: PNode): string =
    n.strVal

  proc nodeLine(n: PNode): int =
    if n == nil: 0 else: n.lineInfoObj.line

  proc nodeCol(n: PNode): int =
    if n == nil: 0 else: n.lineInfoObj.column

  proc extractInlineOrIndentedDoc(line1Based: int, allowIndentedBelow: bool): string =
    let lines = currentSourceLines()
    if line1Based < 1 or line1Based > lines.len:
      return ""
    let declLine = lines[line1Based - 1]
    if not declLine.strip().startswith("##"):
      let docIdx = declLine.find("##")
      if docIdx >= 0:
        return declLine[docIdx + 2 .. ^1].strip()
    if not allowIndentedBelow:
      return ""
    var baseIndent = 0
    while baseIndent < declLine.len and declLine[baseIndent] == ' ':
      inc baseIndent
    var docLines: seq[string] = @[]
    var idx = line1Based - 1
    while idx < lines.len:
      let ln = lines[idx]
      let stripped = ln.strip()
      if idx == line1Based - 1 and not stripped.startswith("##"):
        inc idx
        continue
      if stripped.len == 0:
        inc idx
        continue
      var ind = 0
      while ind < ln.len and ln[ind] == ' ':
        inc ind
      if (idx == line1Based - 1 or ind > baseIndent) and stripped.startswith("##"):
        var content = stripped[2 .. ^1]
        if content.startswith(" "):
          content = content[1 .. ^1]
        docLines.add(content)
        inc idx
      else:
        break
    return docLines.join("\n").strip()

  proc nodeComment(n: PNode, allowIndentedBelow: bool = false): string =
    if n == nil:
      return ""
    if n.kind == nnkCommentStmt:
      return n.strVal.strip()
    return extractInlineOrIndentedDoc(nodeLine(n), allowIndentedBelow)

  proc expandPath(p: string): string =
    p

  proc parseNimSource(src: string, cache: IdentCache, conf: ConfigRef, filename: string): PNode =
    discard cache
    discard conf
    discard filename
    sourceLinesStack.add(src.splitLines())
    result = parseStmt(src)

  proc getCliArgs(): seq[string] =
    var foundSelf = false
    for i in 1 .. paramCount():
      let p = paramStr(i)
      if not foundSelf:
        if p.endsWith(".nim") or p.endsWith(".nims"):
          foundSelf = true
      else:
        result.add(p)

else:
  import compiler/[ast, parser, idents, options, lineinfos, renderer]

  proc nodeIdentStr(n: PNode): string =
    n.ident.s

  proc nodeSymStr(n: PNode): string =
    n.sym.name.s

  proc nodeLine(n: PNode): int =
    if n == nil: 0 else: int(n.info.line)

  proc nodeCol(n: PNode): int =
    if n == nil: 0 else: int(n.info.col)

  proc nodeComment(n: PNode, allowIndentedBelow: bool = false): string =
    discard allowIndentedBelow
    if n == nil: "" else: n.comment.strip()

  proc expandPath(p: string): string =
    expandFilename(p)

  proc parseNimSource(src: string, cache: IdentCache, conf: ConfigRef, filename: string): PNode =
    parseString(src, cache, conf, filename)

  proc getCliArgs(): seq[string] =
    commandLineParams()

type
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

proc extractIdent(n: PNode): string =
  if n == nil:
    return ""
  case n.kind
  of nkIdent:
    return nodeIdentStr(n)
  of nkSym:
    return nodeSymStr(n)
  of nkAccQuoted:
    var parts = ""
    for i in 0 ..< n.len:
      parts.add(extractIdent(n[i]))
    return parts
  of nkPostfix:
    if n.len >= 2:
      return extractIdent(n[1])
  of nkPragmaExpr:
    if n.len >= 1:
      return extractIdent(n[0])
  of nkStrLit..nkTripleStrLit:
    return n.strVal
  else:
    return renderTree(n).strip()

proc extractNameAndPragmas(n: PNode): tuple[name: string, exported: bool, pragmaNode: PNode] =
  if n == nil:
    return ("", false, nil)
  case n.kind
  of nkPragmaExpr:
    let inner = extractNameAndPragmas(n[0])
    let pNode = if n.len >= 2: n[1] else: inner.pragmaNode
    return (inner.name, inner.exported, pNode)
  of nkPostfix:
    if n.len >= 2 and extractIdent(n[0]) == "*":
      let inner = extractNameAndPragmas(n[1])
      return (inner.name, true, inner.pragmaNode)
    return (extractIdent(n), false, nil)
  else:
    return (extractIdent(n), false, nil)

proc parsePragmaNode(pNode: PNode, baseName: string): PragmaData =
  result.exportcName = baseName
  if pNode == nil or pNode.kind != nkPragma:
    return
  for i in 0 ..< pNode.len:
    let item = pNode[i]
    if item == nil:
      continue
    var key = ""
    var valNode: PNode = nil
    case item.kind
    of nkIdent, nkSym, nkAccQuoted:
      key = extractIdent(item)
    of nkExprColonExpr, nkExprEqExpr:
      if item.len >= 2:
        key = extractIdent(item[0])
        valNode = item[1]
    of nkCall, nkCommand:
      if item.len >= 1:
        key = extractIdent(item[0])
      if item.len >= 2:
        valNode = item[1]
    else:
      key = renderTree(item).strip()

    let kLow = key.toLowerAscii().replace("_", "")
    case kLow
    of "exportc":
      result.hasExportc = true
      result.attributes.add("exportc")
      if valNode != nil:
        if valNode.kind in {nkStrLit..nkTripleStrLit}:
          let s = valNode.strVal.strip()
          if s.contains("$1"):
            result.exportcName = s.replace("$1", baseName)
          elif s.len > 0:
            result.exportcName = s
    of "cdecl", "stdcall", "fastcall", "safecall", "thiscall", "syscall", "noconv", "nimcall", "closure", "inline":
      result.callingConvention = kLow
      result.attributes.add(kLow)
    of "deprecated":
      result.isDeprecated = true
      result.attributes.add("deprecated")
      if valNode != nil and valNode.kind in {nkStrLit..nkTripleStrLit}:
        result.deprecatedMsg = valNode.strVal.strip()
    of "packed":
      result.isPacked = true
      result.attributes.add("packed")
    of "union":
      result.isUnion = true
      result.attributes.add("union")
    of "bycopy", "byref", "dynlib", " raises", "gcsafe", "noinit", "inheritable", "pure", "final":
      if kLow == "bycopy":
        result.isBycopy = true
      result.attributes.add(kLow.strip())
    of "size":
      result.attributes.add("size")
      if valNode != nil:
        result.sizeExpr = renderTree(valNode).strip()
    of "bitsize":
      result.attributes.add("bitsize")
      if valNode != nil and valNode.kind in {nkCharLit..nkUInt64Lit}:
        result.bitsize = int(valNode.intVal)
        result.hasBitsize = true
    else:
      if key.len > 0:
        result.attributes.add(key)

proc serializeTypeNode(n: PNode): JsonNode =
  if n == nil or n.kind == nkEmpty:
    return %*{"kind": "ctype", "name": "void", "source_type": "void"}
  let srcType = renderTree(n).strip()
  case n.kind
  of nkIdent, nkSym, nkAccQuoted:
    let identName = extractIdent(n)
    return %*{"kind": "ctype", "name": identName, "source_type": identName}
  of nkPtrTy:
    let pointee = if n.len >= 1: serializeTypeNode(n[0]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "pointer",
      "pointee": pointee,
      "source_type": srcType
    }
  of nkVarTy:
    let pointee = if n.len >= 1: serializeTypeNode(n[0]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "pointer",
      "pointee": pointee,
      "is_var": true,
      "source_type": srcType
    }
  of nkRefTy:
    let pointee = if n.len >= 1: serializeTypeNode(n[0]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "pointer",
      "pointee": pointee,
      "is_ref": true,
      "source_type": srcType
    }
  of nkDistinctTy:
    let under = if n.len >= 1: serializeTypeNode(n[0]) else: %*{"kind": "ctype", "name": "void", "source_type": "void"}
    return %*{
      "kind": "distinct",
      "underlying": under,
      "source_type": srcType
    }
  of nkBracketExpr:
    if n.len >= 1:
      let head = extractIdent(n[0])
      let headLow = head.toLowerAscii().replace("_", "")
      if headLow == "array" and n.len >= 3:
        var arrSize = -1
        let sizeNode = n[1]
        if sizeNode.kind in {nkCharLit..nkUInt64Lit}:
          arrSize = int(sizeNode.intVal)
        elif sizeNode.kind == nkInfix and sizeNode.len >= 3 and extractIdent(sizeNode[0]) == "..":
          if sizeNode[1].kind in {nkCharLit..nkUInt64Lit} and sizeNode[2].kind in {nkCharLit..nkUInt64Lit}:
            arrSize = int(sizeNode[2].intVal - sizeNode[1].intVal + 1)
        let elem = serializeTypeNode(n[2])
        return %*{
          "kind": "array",
          "size": arrSize,
          "element_type": elem,
          "source_type": srcType
        }
      elif headLow == "uncheckedarray" and n.len >= 2:
        let elem = serializeTypeNode(n[1])
        return %*{
          "kind": "array",
          "size": nil,
          "element_type": elem,
          "source_type": srcType
        }
      elif headLow in ["ptr", "ref", "var"] and n.len >= 2:
        return %*{
          "kind": "pointer",
          "pointee": serializeTypeNode(n[1]),
          "source_type": srcType
        }
    return %*{"kind": "ctype", "name": srcType, "source_type": srcType}
  of nkProcTy:
    var retNode = %*{"kind": "ctype", "name": "void", "source_type": "void"}
    var paramsArr = newJArray()
    var cc = ""
    if n.len >= 1 and n[0] != nil and n[0].kind == nkFormalParams:
      let fp = n[0]
      if fp.len >= 1 and fp[0] != nil and fp[0].kind != nkEmpty:
        retNode = serializeTypeNode(fp[0])
      for i in 1 ..< fp.len:
        let idDefs = fp[i]
        if idDefs == nil or idDefs.kind != nkIdentDefs or idDefs.len < 3:
          continue
        let pTypeNode = idDefs[idDefs.len - 2]
        let pDefaultNode = idDefs[idDefs.len - 1]
        let pTypeJson = serializeTypeNode(pTypeNode)
        let defStr = if pDefaultNode != nil and pDefaultNode.kind != nkEmpty: renderTree(pDefaultNode).strip() else: ""
        for j in 0 ..< idDefs.len - 2:
          let pInfo = extractNameAndPragmas(idDefs[j])
          paramsArr.add(%*{
            "name": pInfo.name,
            "type": pTypeJson,
            "default": (if defStr.len > 0: %defStr else: newJNull())
          })
    if n.len >= 2 and n[1] != nil and n[1].kind == nkPragma:
      let pData = parsePragmaNode(n[1], "")
      cc = pData.callingConvention
    return %*{
      "kind": "proc",
      "return_type": retNode,
      "parameters": paramsArr,
      "calling_convention": (if cc.len > 0: %cc else: newJNull()),
      "source_type": srcType
    }
  of nkTupleTy, nkTupleClassTy:
    var fieldsArr = newJArray()
    for i in 0 ..< n.len:
      let child = n[i]
      if child != nil and child.kind == nkIdentDefs and child.len >= 3:
        let fTypeNode = child[child.len - 2]
        let fTypeJson = serializeTypeNode(fTypeNode)
        for j in 0 ..< child.len - 2:
          let fInfo = extractNameAndPragmas(child[j])
          fieldsArr.add(%*{
            "name": fInfo.name,
            "exported": fInfo.exported,
            "type": fTypeJson,
            "doc": newJNull()
          })
      elif child != nil:
        fieldsArr.add(%*{
          "name": "field" & $i,
          "exported": true,
          "type": serializeTypeNode(child),
          "doc": newJNull()
        })
    return %*{
      "kind": "tuple",
      "fields": fieldsArr,
      "source_type": srcType
    }
  else:
    return %*{"kind": "ctype", "name": srcType, "source_type": srcType}

proc collectObjectFields(recNode: PNode, outFields: var JsonNode, outDoc: var seq[string]) =
  if recNode == nil:
    return
  let recComment = nodeComment(recNode)
  if recComment.len > 0:
    outDoc.add(recComment)
  case recNode.kind
  of nkRecList:
    var pendingFieldDoc: seq[string] = @[]
    for i in 0 ..< recNode.len:
      let item = recNode[i]
      if item == nil:
        continue
      if item.kind == nkCommentStmt:
        let itemComment = nodeComment(item)
        if itemComment.len > 0:
          if outFields.len == 0:
            outDoc.add(itemComment)
          else:
            pendingFieldDoc.add(itemComment)
        continue
      if item.kind == nkIdentDefs and item.len >= 3:
        let fTypeNode = item[item.len - 2]
        let fTypeJson = serializeTypeNode(fTypeNode)
        var fDoc = nodeComment(item)
        if fDoc.len == 0 and pendingFieldDoc.len > 0:
          fDoc = pendingFieldDoc.join("\n").strip()
        pendingFieldDoc.setLen(0)
        for j in 0 ..< item.len - 2:
          let fInfo = extractNameAndPragmas(item[j])
          let fPragma = parsePragmaNode(fInfo.pragmaNode, fInfo.name)
          outFields.add(%*{
            "name": fInfo.name,
            "c_name": fPragma.exportcName,
            "exported": fInfo.exported,
            "type": fTypeJson,
            "bit_width": (if fPragma.hasBitsize: %fPragma.bitsize else: newJNull()),
            "doc": (if fDoc.len > 0: %fDoc else: newJNull()),
            "attributes": %fPragma.attributes
          })
      else:
        collectObjectFields(item, outFields, outDoc)
  of nkRecCase:
    if recNode.len >= 1 and recNode[0].kind == nkIdentDefs:
      var dummyDoc: seq[string] = @[]
      let disc = recNode[0]
      let discDoc = nodeComment(disc)
      let fTypeJson = serializeTypeNode(disc[disc.len - 2])
      for j in 0 ..< disc.len - 2:
        let fInfo = extractNameAndPragmas(disc[j])
        let fPragma = parsePragmaNode(fInfo.pragmaNode, fInfo.name)
        outFields.add(%*{
          "name": fInfo.name,
          "c_name": fPragma.exportcName,
          "exported": fInfo.exported,
          "type": fTypeJson,
          "bit_width": (if fPragma.hasBitsize: %fPragma.bitsize else: newJNull()),
          "doc": (if discDoc.len > 0: %discDoc else: newJNull()),
          "attributes": %fPragma.attributes
        })
      for i in 1 ..< recNode.len:
        let branch = recNode[i]
        if branch != nil and branch.len >= 1:
          collectObjectFields(branch[branch.len - 1], outFields, dummyDoc)
  else:
    for i in 0 ..< recNode.len:
      collectObjectFields(recNode[i], outFields, outDoc)

proc extractIncludeTarget(n: PNode): string =
  if n == nil:
    return ""
  case n.kind
  of nkStrLit..nkTripleStrLit:
    return n.strVal.strip()
  of nkIdent, nkSym, nkAccQuoted:
    return extractIdent(n)
  of nkInfix:
    if n.len >= 3:
      let op = extractIdent(n[0])
      let lhs = extractIncludeTarget(n[1])
      let rhs = extractIncludeTarget(n[2])
      return lhs & op & rhs
  else:
    return renderTree(n).strip().strip(chars = {'"', '\''})

proc parseNimFileRecursive(
  filePath: string,
  codeOverride: string,
  cache: IdentCache,
  conf: ConfigRef,
  visited: var HashSet[string],
  decls: var JsonNode,
  includedFiles: var seq[string]
)

proc processStmtList(
  stmtList: PNode,
  currentFile: string,
  cache: IdentCache,
  conf: ConfigRef,
  visited: var HashSet[string],
  decls: var JsonNode,
  includedFiles: var seq[string]
) =
  if stmtList == nil:
    return
  var pendingDoc: seq[string] = @[]

  for idx in 0 ..< stmtList.len:
    let n = stmtList[idx]
    if n == nil:
      continue

    case n.kind
    of nkCommentStmt:
      let c = nodeComment(n)
      if c.len > 0:
        pendingDoc.add(c)

    of nkIncludeStmt:
      pendingDoc.setLen(0)
      let baseDir = if currentFile.len > 0: parentDir(currentFile) else: getCurrentDir()
      for i in 0 ..< n.len:
        var rel = extractIncludeTarget(n[i])
        if rel.len == 0:
          continue
        if not rel.endsWith(".nim"):
          rel.add(".nim")
        let candidate = normalizedPath(baseDir / rel)
        if fileExists(candidate):
          let absPath = expandPath(candidate)
          if absPath notin visited:
            includedFiles.add(absPath)
            parseNimFileRecursive(absPath, "", cache, conf, visited, decls, includedFiles)

    of nkProcDef, nkFuncDef, nkMethodDef, nkConverterDef, nkIteratorDef, nkTemplateDef:
      if n.len < 7:
        pendingDoc.setLen(0)
        continue
      let nameInfo = extractNameAndPragmas(n[0])
      let pNode = if n[4] != nil and n[4].kind == nkPragma: n[4] else: nameInfo.pragmaNode
      let pData = parsePragmaNode(pNode, nameInfo.name)

      var bodyDoc: seq[string] = @[]
      let nDoc = nodeComment(n)
      if nDoc.len > 0:
        bodyDoc.add(nDoc)
      let bodyNode = n[6]
      if bodyNode != nil:
        if bodyNode.kind == nkCommentStmt:
          let bComment = nodeComment(bodyNode)
          if bComment.len > 0 and bComment notin bodyDoc:
            bodyDoc.add(bComment)
        elif bodyNode.kind == nkStmtList:
          for bIdx in 0 ..< bodyNode.len:
            let bStmt = bodyNode[bIdx]
            if bStmt != nil and bStmt.kind == nkCommentStmt:
              let bComment = nodeComment(bStmt)
              if bComment.len > 0 and bComment notin bodyDoc:
                bodyDoc.add(bComment)
            else:
              break
      if bodyDoc.len == 0:
        let indDoc = nodeComment(n, allowIndentedBelow = true)
        if indDoc.len > 0:
          bodyDoc.add(indDoc)

      let finalDocLines = if bodyDoc.len > 0: bodyDoc else: pendingDoc
      let docStr = finalDocLines.join("\n").strip()
      pendingDoc.setLen(0)

      var retJson = %*{"kind": "ctype", "name": "void", "source_type": "void"}
      var paramsArr = newJArray()
      let fp = n[3]
      if fp != nil and fp.kind == nkFormalParams:
        if fp.len >= 1 and fp[0] != nil and fp[0].kind != nkEmpty:
          retJson = serializeTypeNode(fp[0])
        for i in 1 ..< fp.len:
          let idDefs = fp[i]
          if idDefs == nil or idDefs.kind != nkIdentDefs or idDefs.len < 3:
            continue
          let pTypeNode = idDefs[idDefs.len - 2]
          let pDefaultNode = idDefs[idDefs.len - 1]
          let pTypeJson = serializeTypeNode(pTypeNode)
          let defStr = if pDefaultNode != nil and pDefaultNode.kind != nkEmpty: renderTree(pDefaultNode).strip() else: ""
          for j in 0 ..< idDefs.len - 2:
            let pInfo = extractNameAndPragmas(idDefs[j])
            paramsArr.add(%*{
              "name": pInfo.name,
              "type": pTypeJson,
              "default": (if defStr.len > 0: %defStr else: newJNull())
            })

      let routineKind = case n.kind
        of nkFuncDef: "func"
        of nkMethodDef: "method"
        of nkConverterDef: "converter"
        of nkIteratorDef: "iterator"
        of nkTemplateDef: "template"
        else: "proc"

      decls.add(%*{
        "kind": "routine",
        "routine_kind": routineKind,
        "name": nameInfo.name,
        "c_name": pData.exportcName,
        "has_exportc": pData.hasExportc,
        "exported": nameInfo.exported,
        "calling_convention": (if pData.callingConvention.len > 0: %pData.callingConvention else: newJNull()),
        "is_deprecated": pData.isDeprecated,
        "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
        "attributes": %pData.attributes,
        "return_type": retJson,
        "parameters": paramsArr,
        "doc": (if docStr.len > 0: %docStr else: newJNull()),
        "file": currentFile,
        "line": nodeLine(n),
        "column": nodeCol(n)
      })

    of nkTypeSection:
      for i in 0 ..< n.len:
        let td = n[i]
        if td == nil:
          continue
        if td.kind == nkCommentStmt:
          let tdComment = nodeComment(td)
          if tdComment.len > 0:
            pendingDoc.add(tdComment)
          continue
        if td.kind != nkTypeDef or td.len < 3:
          continue
        let nameInfo = extractNameAndPragmas(td[0])
        let pData = parsePragmaNode(nameInfo.pragmaNode, nameInfo.name)
        let body = td[2]

        var typeDoc: seq[string] = @[]
        let tdDoc = nodeComment(td, allowIndentedBelow = (body == nil or body.kind != nkEnumTy))
        if tdDoc.len > 0:
          typeDoc.add(tdDoc)
        elif td[0] != nil:
          let td0Doc = nodeComment(td[0], allowIndentedBelow = (body == nil or body.kind != nkEnumTy))
          if td0Doc.len > 0:
            typeDoc.add(td0Doc)
        if body != nil:
          let bodyDoc = nodeComment(body)
          if bodyDoc.len > 0 and bodyDoc notin typeDoc:
            typeDoc.add(bodyDoc)

        if body != nil and body.kind == nkObjectTy:
          var fieldsArr = newJArray()
          if body.len >= 3:
            collectObjectFields(body[2], fieldsArr, typeDoc)
          let finalDoc = (if typeDoc.len > 0: typeDoc else: pendingDoc).join("\n").strip()
          pendingDoc.setLen(0)
          decls.add(%*{
            "kind": "object",
            "name": nameInfo.name,
            "c_name": pData.exportcName,
            "has_exportc": pData.hasExportc,
            "exported": nameInfo.exported,
            "is_packed": pData.isPacked,
            "is_union": pData.isUnion,
            "is_bycopy": pData.isBycopy,
            "is_deprecated": pData.isDeprecated,
            "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
            "attributes": %pData.attributes,
            "fields": fieldsArr,
            "doc": (if finalDoc.len > 0: %finalDoc else: newJNull()),
            "file": currentFile,
            "line": nodeLine(td),
            "column": nodeCol(td)
          })
        elif body != nil and body.kind in {nkTupleTy, nkTupleClassTy}:
          let tupJson = serializeTypeNode(body)
          let finalDoc = (if typeDoc.len > 0: typeDoc else: pendingDoc).join("\n").strip()
          pendingDoc.setLen(0)
          decls.add(%*{
            "kind": "object",
            "name": nameInfo.name,
            "c_name": pData.exportcName,
            "has_exportc": pData.hasExportc,
            "exported": nameInfo.exported,
            "is_packed": pData.isPacked,
            "is_union": false,
            "is_bycopy": true,
            "is_tuple": true,
            "is_deprecated": pData.isDeprecated,
            "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
            "attributes": %pData.attributes,
            "fields": tupJson["fields"],
            "doc": (if finalDoc.len > 0: %finalDoc else: newJNull()),
            "file": currentFile,
            "line": nodeLine(td),
            "column": nodeCol(td)
          })
        elif body != nil and body.kind == nkEnumTy:
          var valuesArr = newJArray()
          var nextVal = 0
          for eIdx in 1 ..< body.len:
            let ef = body[eIdx]
            if ef == nil:
              continue
            var eName = ""
            var eVal = nextVal
            var eDoc = nodeComment(ef)
            case ef.kind
            of nkIdent, nkSym, nkAccQuoted, nkPragmaExpr:
              let eInfo = extractNameAndPragmas(ef)
              eName = eInfo.name
              nextVal = eVal + 1
            of nkEnumFieldDef:
              if ef.len >= 2:
                let eInfo = extractNameAndPragmas(ef[0])
                eName = eInfo.name
                let vNode = ef[1]
                if vNode.kind in {nkCharLit..nkUInt64Lit}:
                  eVal = int(vNode.intVal)
                  nextVal = eVal + 1
                elif vNode.kind == nkTupleConstr and vNode.len >= 1 and vNode[0].kind in {nkCharLit..nkUInt64Lit}:
                  eVal = int(vNode[0].intVal)
                  nextVal = eVal + 1
                else:
                  nextVal = eVal + 1
            else:
              eName = extractIdent(ef)
              nextVal = eVal + 1
            if eName.len > 0:
              valuesArr.add(%*{
                "name": eName,
                "value": eVal,
                "doc": (if eDoc.len > 0: %eDoc else: newJNull())
              })
          let finalDoc = (if typeDoc.len > 0: typeDoc else: pendingDoc).join("\n").strip()
          pendingDoc.setLen(0)
          decls.add(%*{
            "kind": "enum",
            "name": nameInfo.name,
            "c_name": pData.exportcName,
            "has_exportc": pData.hasExportc,
            "exported": nameInfo.exported,
            "size_expr": (if pData.sizeExpr.len > 0: %pData.sizeExpr else: newJNull()),
            "is_deprecated": pData.isDeprecated,
            "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
            "attributes": %pData.attributes,
            "values": valuesArr,
            "doc": (if finalDoc.len > 0: %finalDoc else: newJNull()),
            "file": currentFile,
            "line": nodeLine(td),
            "column": nodeCol(td)
          })
        else:
          let finalDoc = (if typeDoc.len > 0: typeDoc else: pendingDoc).join("\n").strip()
          pendingDoc.setLen(0)
          let underJson = serializeTypeNode(body)
          decls.add(%*{
            "kind": "typedef",
            "name": nameInfo.name,
            "c_name": pData.exportcName,
            "has_exportc": pData.hasExportc,
            "exported": nameInfo.exported,
            "is_distinct": (body != nil and body.kind == nkDistinctTy),
            "is_deprecated": pData.isDeprecated,
            "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
            "attributes": %pData.attributes,
            "underlying_type": underJson,
            "doc": (if finalDoc.len > 0: %finalDoc else: newJNull()),
            "file": currentFile,
            "line": nodeLine(td),
            "column": nodeCol(td)
          })

    of nkConstSection:
      for i in 0 ..< n.len:
        let cd = n[i]
        if cd == nil:
          continue
        if cd.kind == nkCommentStmt:
          let cdComment = nodeComment(cd)
          if cdComment.len > 0:
            pendingDoc.add(cdComment)
          continue
        if cd.kind != nkConstDef or cd.len < 3:
          continue
        let nameInfo = extractNameAndPragmas(cd[0])
        let pData = parsePragmaNode(nameInfo.pragmaNode, nameInfo.name)
        let typeNode = cd[1]
        let valNode = cd[2]
        var cDoc = nodeComment(cd)
        if cDoc.len == 0 and pendingDoc.len > 0:
          cDoc = pendingDoc.join("\n").strip()
        pendingDoc.setLen(0)

        var valJson = newJNull()
        var valKind = "expr"
        if valNode != nil:
          case valNode.kind
          of nkCharLit..nkUInt64Lit:
            valJson = %valNode.intVal
            valKind = "int"
          of nkFloatLit..nkFloat128Lit:
            valJson = %valNode.floatVal
            valKind = "float"
          of nkStrLit..nkTripleStrLit:
            valJson = %valNode.strVal
            valKind = "string"
          of nkIdent:
            let idVal = nodeIdentStr(valNode)
            if idVal == "true":
              valJson = %true
              valKind = "bool"
            elif idVal == "false":
              valJson = %false
              valKind = "bool"
            else:
              valJson = %renderTree(valNode).strip()
          else:
            valJson = %renderTree(valNode).strip()

        decls.add(%*{
          "kind": "const",
          "name": nameInfo.name,
          "c_name": pData.exportcName,
          "has_exportc": pData.hasExportc,
          "exported": nameInfo.exported,
          "is_deprecated": pData.isDeprecated,
          "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
          "attributes": %pData.attributes,
          "type": (if typeNode != nil and typeNode.kind != nkEmpty: serializeTypeNode(typeNode) else: newJNull()),
          "value": valJson,
          "value_kind": valKind,
          "doc": (if cDoc.len > 0: %cDoc else: newJNull()),
          "file": currentFile,
          "line": nodeLine(cd),
          "column": nodeCol(cd)
        })

    of nkVarSection, nkLetSection:
      let isLet = (n.kind == nkLetSection)
      for i in 0 ..< n.len:
        let vd = n[i]
        if vd == nil:
          continue
        if vd.kind == nkCommentStmt:
          let vdComment = nodeComment(vd)
          if vdComment.len > 0:
            pendingDoc.add(vdComment)
          continue
        if vd.kind != nkIdentDefs or vd.len < 3:
          continue
        let vTypeNode = vd[vd.len - 2]
        let vTypeJson = serializeTypeNode(vTypeNode)
        var vDoc = nodeComment(vd)
        if vDoc.len == 0 and pendingDoc.len > 0:
          vDoc = pendingDoc.join("\n").strip()
        pendingDoc.setLen(0)
        for j in 0 ..< vd.len - 2:
          let nameInfo = extractNameAndPragmas(vd[j])
          let pData = parsePragmaNode(nameInfo.pragmaNode, nameInfo.name)
          decls.add(%*{
            "kind": "var",
            "is_let": isLet,
            "name": nameInfo.name,
            "c_name": pData.exportcName,
            "has_exportc": pData.hasExportc,
            "exported": nameInfo.exported,
            "is_deprecated": pData.isDeprecated,
            "deprecated_msg": (if pData.deprecatedMsg.len > 0: %pData.deprecatedMsg else: newJNull()),
            "attributes": %pData.attributes,
            "type": vTypeJson,
            "doc": (if vDoc.len > 0: %vDoc else: newJNull()),
            "file": currentFile,
            "line": nodeLine(vd),
            "column": nodeCol(vd)
          })

    of nkWhenStmt:
      pendingDoc.setLen(0)
      if n.len >= 1 and n[0] != nil and n[0].len >= 2:
        processStmtList(n[0][1], currentFile, cache, conf, visited, decls, includedFiles)

    of nkStmtList:
      processStmtList(n, currentFile, cache, conf, visited, decls, includedFiles)

    else:
      pendingDoc.setLen(0)

proc parseNimFileRecursive(
  filePath: string,
  codeOverride: string,
  cache: IdentCache,
  conf: ConfigRef,
  visited: var HashSet[string],
  decls: var JsonNode,
  includedFiles: var seq[string]
) =
  let normPath = if filePath.len > 0 and fileExists(filePath): expandPath(filePath) else: filePath
  if normPath.len > 0:
    visited.incl(normPath)
  let src = if codeOverride.len > 0: codeOverride
            elif normPath.len > 0 and fileExists(normPath): readFile(normPath)
            else: ""
  if src.len == 0:
    return
  let ast = parseNimSource(src, cache, conf, (if normPath.len > 0: normPath else: "input.nim"))
  processStmtList(ast, normPath, cache, conf, visited, decls, includedFiles)
  when defined(nimscript):
    if sourceLinesStack.len > 0:
      discard sourceLinesStack.pop()

when isMainModule:
  let args = getCliArgs()
  if args.len < 1:
    echo("Usage: _nim_ast_helper <file.nim> [--stdin | --code-file <path>]")
    quit(1)
  let targetPath = args[0]
  var codeText = ""
  if args.len >= 2 and args[1] == "--stdin":
    when not defined(nimscript):
      codeText = stdin.readAll()
  elif args.len >= 3 and args[1] == "--code-file":
    codeText = readFile(args[2])

  let conf = newConfigRef()
  let cache = newIdentCache()
  var visited = initHashSet[string]()
  var decls = newJArray()
  var includedFiles: seq[string] = @[]

  parseNimFileRecursive(targetPath, codeText, cache, conf, visited, decls, includedFiles)

  let outObj = %*{
    "path": targetPath,
    "included_files": %includedFiles,
    "declarations": decls
  }
  echo($outObj)
