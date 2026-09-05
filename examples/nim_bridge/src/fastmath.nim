type
  FastMatrix* = object
    rows*, cols*: int
    data*: seq[float64]

proc NimMain*() {.cdecl, importc.}

proc createMatrix*(rows, cols: int): ptr FastMatrix {.exportc, dynlib.} =
  setupForeignThreadGc()
  let m = create(FastMatrix)
  m.rows = rows
  m.cols = cols
  m.data = newSeq[float64](rows * cols)
  return m

proc destroyMatrix*(m: ptr FastMatrix) {.exportc, dynlib.} =
  if m != nil:
    setupForeignThreadGc()
    # Explicitly clear seq before freeing container
    m.data = @[]
    dealloc(m)

proc addNumbers*(a, b: int): int {.exportc, dynlib.} =
  return a + b

proc computeSum*(m: ptr FastMatrix): float64 {.exportc, dynlib.} =
  if m == nil: return 0.0
  setupForeignThreadGc()
  var s = 0.0
  for v in m.data:
    s += v
  return s
