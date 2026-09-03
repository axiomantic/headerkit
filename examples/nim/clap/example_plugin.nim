# Runnable CLAP audio plugin implementation in Nim
import clap, clap_highlevel

clapPlugin("com.headerkit.nim_synth", "NimSynth", "Axiomantic"):
  # Audio DSP processing loop
  discard

proc main() =
  echo "=== Nim CLAP Audio Plugin Descriptor ==="
  let desc = newClapDescriptor(
    id = "com.headerkit.nim_synth",
    name = "NimSynth",
    vendor = "Axiomantic",
    version = "1.0.0"
  )
  echo "Plugin ID:      ", desc.id
  echo "Plugin Name:    ", desc.name
  echo "Plugin Vendor:  ", desc.vendor
  echo "CLAP Version:   ", desc.clap_version.major, ".", desc.clap_version.minor, ".", desc.clap_version.revision

when isMainModule:
  main()
