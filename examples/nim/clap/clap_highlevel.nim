# High-level idiomatic Nim wrapper for CLAP (CLever Audio Plugin)
import clap

type
  ClapHost* = object
    raw*: ptr clap_host_t

  ClapPlugin* = object
    raw*: clap_plugin_t
    initProc*: proc(): bool
    destroyProc*: proc()
    activateProc*: proc(sampleRate: float64, minFrames, maxFrames: uint32): bool
    deactivateProc*: proc()
    startProc*: proc(): bool
    stopProc*: proc()
    processProc*: proc(process: ptr clap_process_t): clap_process_status

# Helper to create a CLAP plugin descriptor
proc newClapDescriptor*(
  id: string,
  name: string,
  vendor: string,
  url: string = "",
  version: string = "1.0.0",
  description: string = "",
  features: openArray[string] = ["instrument", "synthesizer"]
): clap_plugin_descriptor_t =
  result.clap_version = CLAP_VERSION
  result.id = cstring(id)
  result.name = cstring(name)
  result.vendor = cstring(vendor)
  result.url = cstring(url)
  result.version = cstring(version)
  result.description = cstring(description)

# Template / DSL for creating a CLAP audio plugin
template clapPlugin*(
  pluginId: string,
  pluginName: string,
  pluginVendor: string,
  body: untyped
): untyped =
  var desc {.global.} = newClapDescriptor(pluginId, pluginName, pluginVendor)

  proc clapInit(plugin: ptr clap_plugin_t): bool {.cdecl.} =
    true

  proc clapDestroy(plugin: ptr clap_plugin_t) {.cdecl.} =
    discard

  proc clapActivate(plugin: ptr clap_plugin_t, sampleRate: cdouble, minFrames, maxFrames: uint32): bool {.cdecl.} =
    true

  proc clapDeactivate(plugin: ptr clap_plugin_t) {.cdecl.} =
    discard

  proc clapStartProcessing(plugin: ptr clap_plugin_t): bool {.cdecl.} =
    true

  proc clapStopProcessing(plugin: ptr clap_plugin_t) {.cdecl.} =
    discard

  proc pluginProcessFn(plugin: ptr clap_plugin_t, process: ptr clap_process_t): clap_process_status {.cdecl.} =
    body
    return CLAP_PROCESS_CONTINUE

  proc clapGetExtension(plugin: ptr clap_plugin_t, id: cstring): pointer {.cdecl.} =
    nil

  proc clapOnMainThread(plugin: ptr clap_plugin_t) {.cdecl.} =
    discard

  proc createPlugin*(host: ptr clap_host_t): ptr clap_plugin_t =
    var p = cast[ptr clap_plugin_t](alloc0(sizeof(clap_plugin_t)))
    p.desc = addr desc
    p.plugin_data = nil
    p.init = clapInit
    p.destroy = clapDestroy
    p.activate = clapActivate
    p.deactivate = clapDeactivate
    p.start_processing = clapStartProcessing
    p.stop_processing = clapStopProcessing
    p.process = pluginProcessFn
    p.get_extension = clapGetExtension
    p.on_main_thread = clapOnMainThread
    return p
