# Runnable demo for RtMidi in Nim
import rtmidi_highlevel

proc main() =
  echo "=== Nim RtMidi Example ==="
  let midiIn = newMidiIn("TestIn")
  echo "Available MIDI Input Ports:"
  for port in midiIn.ports:
    echo "  [", port.id, "] ", port.name

  let midiOut = newMidiOut("TestOut")
  echo "Available MIDI Output Ports:"
  for port in midiOut.ports:
    echo "  [", port.id, "] ", port.name

when isMainModule:
  main()
