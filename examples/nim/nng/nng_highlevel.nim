# High-level idiomatic Nim wrapper for NNG (nanomsg-next-gen)
import nng

type
  NngSocket* = object
    raw*: nng_socket

  NngError* = object of CatchableError
    code*: cint

# Helper to check NNG error codes
proc checkNng(code: cint, op: string = "NNG operation") =
  if code != 0:
    let msg = $nng_strerror(code)
    raise (ref NngError)(msg: op & " failed: " & msg, code: code)

# RAII Destructor
proc `=destroy`*(sock: var NngSocket) =
  if sock.raw.id != 0:
    discard nng_close(sock.raw)
    sock.raw.id = 0

# Socket Constructors for Scalability Protocols
proc openReq0*(): NngSocket =
  var s: nng_socket
  checkNng(nng_req0_open(addr s), "openReq0")
  result = NngSocket(raw: s)

proc openRep0*(): NngSocket =
  var s: nng_socket
  checkNng(nng_rep0_open(addr s), "openRep0")
  result = NngSocket(raw: s)

proc openPub0*(): NngSocket =
  var s: nng_socket
  checkNng(nng_pub0_open(addr s), "openPub0")
  result = NngSocket(raw: s)

proc openSub0*(): NngSocket =
  var s: nng_socket
  checkNng(nng_sub0_open(addr s), "openSub0")
  result = NngSocket(raw: s)

# Dial / Listen
proc dial*(sock: NngSocket, url: string, flags: cint = 0) =
  checkNng(nng_dial(sock.raw, cstring(url), nil, flags), "dial " & url)

proc listen*(sock: NngSocket, url: string, flags: cint = 0) =
  checkNng(nng_listen(sock.raw, cstring(url), nil, flags), "listen " & url)

# Send / Recv
proc send*(sock: NngSocket, data: string, flags: cint = 0) =
  if data.len == 0:
    return
  checkNng(nng_send(sock.raw, cast[pointer](cstring(data)), csize_t(data.len), flags), "send")

proc recv*(sock: NngSocket, flags: cint = 0): string =
  var msg: pointer = nil
  var size: csize_t = 0
  let allocFlag = flags or NNG_FLAG_ALLOC
  checkNng(nng_recv(sock.raw, addr msg, addr size, allocFlag), "recv")
  if msg != nil and size > 0:
    result = newString(size)
    copyMem(addr result[0], msg, size)
    nng_free(msg, size)

# DSL / Templates
template withReqSocket*(url: string, body: untyped) =
  block:
    var sock {.cursor.} = openReq0()
    sock.dial(url)
    body

template withRepSocket*(url: string, body: untyped) =
  block:
    var sock {.cursor.} = openRep0()
    sock.listen(url)
    body
