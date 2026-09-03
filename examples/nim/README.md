# Nim Binding Examples

These examples demonstrate how HeaderKit generates both raw FFI definitions and high-level idiomatic Nim wrappers with RAII destructors (`=destroy`), iterators, templates, and DSL patterns.

## Libraries Covered

1. **RtMidi** (`examples/nim/rtmidi/`)
   - `rtmidi_c.nim`: Low-level C-ABI binding.
   - `rtmidi_highlevel.nim`: RAII wrapper (`MidiIn`, `MidiOut`), port iterators, and `withMidiIn`/`withMidiOut` DSL templates.
   - `example.nim`: Runnable demo listing MIDI ports.

2. **RtAudio** (`examples/nim/rtaudio/`)
   - `rtaudio_c.nim`: Low-level C-ABI binding.
   - `rtaudio_highlevel.nim`: RAII wrapper (`AudioContext`), device iterators, and stream control.
   - `example.nim`: Runnable demo querying audio hardware devices.

3. **NNG (nanomsg-next-gen)** (`examples/nim/nng/`)
   - `nng.nim`: Low-level bindings for core NNG and Scalability Protocols (req/rep, pub/sub, bus, pipeline, pair, survey).
   - `nng_highlevel.nim`: RAII socket destructors, protocol constructors (`openReq0`, `openRep0`), string send/recv.
   - `example.nim`: Runnable demo establishing an in-process client/server exchange.

4. **CLAP (CLever Audio Plugin)** (`examples/nim/clap/`)
   - `clap.nim`: Complete CLAP 1.2+ specification bindings.
   - `clap_highlevel.nim`: `clapPlugin` DSL macro and `newClapDescriptor` builder.
   - `example_plugin.nim`: Runnable demo constructing and initializing a CLAP plugin descriptor.

## Prerequisites

- Nim 2.0+ (`nim`)
- C/C++ libraries (for RtMidi, RtAudio, NNG):
  ```bash
  # macOS
  brew install rtaudio rtmidi nng
  ```

## Regenerating Bindings

To regenerate all raw Nim bindings using HeaderKit:
```bash
uv run python examples/generate_all.py
```

## Running the Demos

```bash
# NNG in-process messaging
nim c -r --cincludes:/opt/homebrew/include --clibdir:/opt/homebrew/lib --passL:"-lnng" examples/nim/nng/example.nim

# RtMidi MIDI port probe
nim c -r --cincludes:/opt/homebrew/include --clibdir:/opt/homebrew/lib --passL:"-lrtmidi" examples/nim/rtmidi/example.nim

# RtAudio Audio device probe
nim c -r --cincludes:/opt/homebrew/include --clibdir:/opt/homebrew/lib --passL:"-lrtaudio" examples/nim/rtaudio/example.nim

# CLAP plugin demo (header-only)
nim c -r --cincludes:$PWD/examples/headers examples/nim/clap/example_plugin.nim
```
