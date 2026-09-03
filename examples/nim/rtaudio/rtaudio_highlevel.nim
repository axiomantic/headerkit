# High-level idiomatic Nim wrapper for RtAudio
import rtaudio_c

type
  AudioContext* = distinct rtaudio_t

  AudioCallback* = proc(
    outputBuffer: pointer,
    inputBuffer: pointer,
    frameCount: uint32,
    streamTime: float64,
    status: uint32,
  ): int

# RAII Destructor
proc `=destroy`*(ctx: var AudioContext) =
  if rtaudio_t(ctx) != nil:
    rtaudio_close_stream(rtaudio_t(ctx))
    rtaudio_destroy(rtaudio_t(ctx))
    rtaudio_t(ctx) = nil

# Constructor
proc newAudioContext*(api: rtaudio_api = RTAUDIO_API_UNSPECIFIED): AudioContext =
  let handle = rtaudio_create(cast[rtaudio_api_t](api))
  if handle == nil:
    raise newException(IOError, "Failed to initialize RtAudio")
  result = AudioContext(handle)

# Iterators
iterator devices*(ctx: AudioContext): tuple[id: int, info: rtaudio_device_info_t] =
  let count = rtaudio_device_count(rtaudio_t(ctx))
  for i in 0 ..< count:
    let devId = rtaudio_get_device_id(rtaudio_t(ctx), cint(i))
    let info = rtaudio_get_device_info(rtaudio_t(ctx), devId)
    yield (id: int(devId), info: info)

proc defaultOutputDevice*(ctx: AudioContext): int =
  int(rtaudio_get_default_output_device(rtaudio_t(ctx)))

proc defaultInputDevice*(ctx: AudioContext): int =
  int(rtaudio_get_default_input_device(rtaudio_t(ctx)))

# Stream Control
proc start*(ctx: AudioContext) =
  let err = rtaudio_start_stream(rtaudio_t(ctx))
  if err != 0:
    raise newException(IOError, "RtAudio start stream error: " & $rtaudio_error(rtaudio_t(ctx)))

proc stop*(ctx: AudioContext) =
  let err = rtaudio_stop_stream(rtaudio_t(ctx))
  if err != 0:
    raise newException(IOError, "RtAudio stop stream error: " & $rtaudio_error(rtaudio_t(ctx)))

# DSL / Templates
template withAudioContext*(audioVar: untyped, body: untyped) =
  block:
    var audioVar = newAudioContext()
    body

template withAudioContext*(audioVar: untyped, api: rtaudio_api, body: untyped) =
  block:
    var audioVar = newAudioContext(api)
    body
