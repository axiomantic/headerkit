# Polyglot Source Extraction

Headerkit supports extracting interface surfaces and C-ABI export declarations directly
from polyglot source units (Rust, Zig, Nim, and C source files) into normalized
[`SourceUnit`][headerkit.ir.SourceUnit] intermediate representation.

## Supported Languages & Declarations

| Language | Extension | Classification | Extracted Declarations |
|---|---|---|---|
| **C Source** | `.c` | `source` | Non-static function definitions (`int fn(...) { ... }`), structs, enums, typedefs |
| **Rust** | `.rs` | `interface` | `pub extern "C" fn`, `#[repr(C)] pub struct`, `#[repr(C)] pub enum` |
| **Zig** | `.zig` | `source` | `export fn`, `pub const Name = extern struct`, `pub const Name = extern enum` |
| **Nim** | `.nim` | `source` | `proc name*(...): ret {.exportc, dynlib.}`, `type Name* = object` |

## Usage Examples

### 1. Parsing Rust Interface Directly

```python
from headerkit.backends import get_backend
from headerkit.ir import Function, Struct

rust_code = """
#[repr(C)]
pub struct Point3D {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

#[no_mangle]
pub extern "C" fn calculate_norm(pt: *const Point3D) -> f64 {
    (pt.x * pt.x + pt.y * pt.y + pt.z * pt.z).sqrt()
}
"""

backend = get_backend("rust")
unit = backend.parse(rust_code, "geometry.rs")

assert len(unit.declarations) == 2
struct_decl = unit.declarations[0]
assert isinstance(struct_decl, Struct)
assert struct_decl.name == "Point3D"
```

### 2. End-to-End Pipeline with Writers

```python
from headerkit.hooks import PipelineContext, execute_pipeline

zig_code = """
pub const Rgba = extern struct {
    r: u8,
    g: u8,
    b: u8,
    a: u8,
};

export fn invert_color(color: *Rgba) void {
    color.r = 255 - color.r;
}
"""

ctx = PipelineContext(language="zig", writer="ctypes")
unit, output = execute_pipeline("graphics.zig", code=zig_code, context=ctx)
print(output)
```

### 3. Hook Pipeline Dispatch

When processing inputs via the hook engine, inputs with `.rs`, `.zig`, or `.nim` extensions
are automatically classified by [`InputSpec`][headerkit.ir.InputSpec] and dispatched to the
corresponding polyglot extractor:

```python
from headerkit.hooks import HookDispatcher, PipelineContext

dispatcher = HookDispatcher()
ctx = PipelineContext(language="nim")
unit = dispatcher.first_result("parse_unit", nim_code, "math.nim", context=ctx)
```

## API Reference

::: headerkit.backends.polyglot.RustBackend
    options:
      show_source: false

::: headerkit.backends.polyglot.ZigBackend
    options:
      show_source: false

::: headerkit.backends.polyglot.NimBackend
    options:
      show_source: false

::: headerkit.backends.polyglot.extract_rust_interface
    options:
      show_source: false

::: headerkit.backends.polyglot.extract_zig_interface
    options:
      show_source: false

::: headerkit.backends.polyglot.extract_nim_interface
    options:
      show_source: false
