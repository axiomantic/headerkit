# Runnable demo for NNG (nanomsg-next-gen) in Nim
import nng_highlevel

proc main() =
  echo "=== Nim NNG (nanomsg-next-gen) Example ==="
  let url = "inproc://demo_exchange"

  # Server (Rep)
  var rep = openRep0()
  rep.listen(url)

  # Client (Req)
  var req = openReq0()
  req.dial(url)

  echo "Sending message from client..."
  req.send("Hello from HeaderKit Nim bindings!")

  let received = rep.recv()
  echo "Server received: '", received, "'"

  rep.send("ACK from server")
  let reply = req.recv()
  echo "Client received reply: '", reply, "'"
  echo "=== NNG Exchange Completed Successfully ==="

when isMainModule:
  main()
