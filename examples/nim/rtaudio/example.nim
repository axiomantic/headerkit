# Runnable demo for RtAudio in Nim
import rtaudio_highlevel

proc main() =
  echo "=== Nim RtAudio Example ==="
  var audio = newAudioContext()
  echo "Default Output Device ID: ", audio.defaultOutputDevice
  echo "Default Input Device ID:  ", audio.defaultInputDevice
  echo "Available Audio Devices:"
  for dev in audio.devices:
    echo "  [", dev.id, "] ", $cast[cstring](unsafeAddr dev.info.name[0]), " (In: ", dev.info.input_channels, ", Out: ", dev.info.output_channels, ")"

when isMainModule:
  main()
