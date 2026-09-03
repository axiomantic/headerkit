# High-level idiomatic Nim wrapper for RtMidi
import rtmidi_c

type
  MidiIn* = distinct RtMidiInPtr
  MidiOut* = distinct RtMidiOutPtr

  MidiCallback* = proc(msg: seq[uint8], deltaTime: float64)

# Helper for port name buffer
proc getPortName(handle: RtMidiPtr, portNumber: int): string =
  var length: cint = 0
  discard rtmidi_get_port_name(handle, cuint(portNumber), nil, addr length)
  if length > 0:
    var buf = newString(length)
    discard rtmidi_get_port_name(handle, cuint(portNumber), cast[ptr cchar](addr buf[0]), addr length)
    if buf.len > 0 and buf[^1] == '\0':
      buf.setLen(buf.len - 1)
    return buf
  return ""

# RAII Destructors
proc `=destroy`*(midi: var MidiIn) =
  if RtMidiInPtr(midi) != nil:
    rtmidi_close_port(RtMidiPtr(RtMidiInPtr(midi)))
    rtmidi_in_free(RtMidiInPtr(midi))
    RtMidiInPtr(midi) = nil

proc `=destroy`*(midi: var MidiOut) =
  if RtMidiOutPtr(midi) != nil:
    rtmidi_close_port(RtMidiPtr(RtMidiOutPtr(midi)))
    rtmidi_out_free(RtMidiOutPtr(midi))
    RtMidiOutPtr(midi) = nil

# Constructors
proc newMidiIn*(name: string = "NimRtMidiIn", api: RtMidiApi = RTMIDI_API_UNSPECIFIED): MidiIn =
  let handle = rtmidi_in_create(api, cstring(name), 1024)
  if handle == nil or not handle.ok:
    raise newException(IOError, "Failed to create RtMidiIn: " & (if handle != nil: $handle.msg else: "null"))
  result = MidiIn(handle)

proc newMidiOut*(name: string = "NimRtMidiOut", api: RtMidiApi = RTMIDI_API_UNSPECIFIED): MidiOut =
  let handle = rtmidi_out_create(api, cstring(name))
  if handle == nil or not handle.ok:
    raise newException(IOError, "Failed to create RtMidiOut: " & (if handle != nil: $handle.msg else: "null"))
  result = MidiOut(handle)

# Iterators
iterator ports*(midi: MidiIn): tuple[id: int, name: string] =
  let count = rtmidi_get_port_count(RtMidiPtr(RtMidiInPtr(midi)))
  for i in 0 ..< count:
    yield (id: int(i), name: getPortName(RtMidiPtr(RtMidiInPtr(midi)), int(i)))

iterator ports*(midi: MidiOut): tuple[id: int, name: string] =
  let count = rtmidi_get_port_count(RtMidiPtr(RtMidiOutPtr(midi)))
  for i in 0 ..< count:
    yield (id: int(i), name: getPortName(RtMidiPtr(RtMidiOutPtr(midi)), int(i)))

# Port operations
proc openPort*(midi: MidiIn, portNumber: int, portName: string = "Midi In") =
  rtmidi_open_port(RtMidiPtr(RtMidiInPtr(midi)), cuint(portNumber), cstring(portName))

proc openPort*(midi: MidiOut, portNumber: int, portName: string = "Midi Out") =
  rtmidi_open_port(RtMidiPtr(RtMidiOutPtr(midi)), cuint(portNumber), cstring(portName))

proc send*(midi: MidiOut, msg: openArray[uint8]) =
  if msg.len > 0:
    discard rtmidi_out_send_message(RtMidiOutPtr(midi), cast[ptr cuchar](unsafeAddr msg[0]), cint(msg.len))

# DSL / Template
template withMidiIn*(name: string, portNum: int, body: untyped) =
  block:
    var midiIn {.cursor.} = newMidiIn(name)
    midiIn.openPort(portNum)
    body

template withMidiOut*(name: string, portNum: int, body: untyped) =
  block:
    var midiOut {.cursor.} = newMidiOut(name)
    midiOut.openPort(portNum)
    body
