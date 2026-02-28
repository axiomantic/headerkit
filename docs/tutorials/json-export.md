# Tutorial: JSON Export and IR Inspection

The built-in JSON writer serializes headerkit's IR into a structured JSON document. This is useful for inspecting parsed headers, building custom tooling, diffing API surfaces in CI/CD, and feeding header metadata into other programs.

## Basic Usage

### Using get_writer()

```python
from headerkit import get_backend, get_writer

backend = get_backend()
header = backend.parse(open("mylib.h").read(), "mylib.h")

writer = get_writer("json", indent=2)
print(writer.write(header))
```

### Using header_to_json() Directly

The [`header_to_json()`][headerkit.writers.json.header_to_json] function provides the same functionality:

```python
from headerkit import get_backend
from headerkit.writers.json import header_to_json

backend = get_backend()
header = backend.parse(open("mylib.h").read(), "mylib.h")

json_str = header_to_json(header, indent=2)
print(json_str)
```

For programmatic use, [`header_to_json_dict()`][headerkit.writers.json.header_to_json_dict] returns a Python dict instead of a string:

```python
from headerkit.writers.json import header_to_json_dict

data = header_to_json_dict(header)
for decl in data["declarations"]:
    print(f"{decl['kind']}: {decl.get('name', '(anonymous)')}")
```

## JSON Schema Overview

The JSON output follows a consistent structure. Every node includes a `"kind"` field that identifies its type.

### Top-Level Structure

```json
{
  "path": "mylib.h",
  "declarations": [ ... ],
  "included_headers": ["stdio.h", "stdlib.h"]
}
```

The `included_headers` field is only present when the header includes other files and the backend tracks them.

### Type Expression Kinds

Type expressions use the `"kind"` discriminator:

| Kind | Fields | C Equivalent |
|------|--------|-------------|
| `"ctype"` | `name`, `qualifiers` (optional) | `int`, `const char` |
| `"pointer"` | `pointee` (nested type) , `qualifiers` (optional) | `int*`, `void**` |
| `"array"` | `element_type`, `size` (optional) | `int[10]`, `char[]` |
| `"function_pointer"` | `return_type`, `parameters`, `is_variadic` | `void (*)(int)` |

Example -- `const char*`:

```json
{
  "kind": "pointer",
  "pointee": {
    "kind": "ctype",
    "name": "char",
    "qualifiers": ["const"]
  }
}
```

### Declaration Kinds

| Kind | Key Fields |
|------|-----------|
| `"struct"` | `name`, `fields`, `is_typedef` |
| `"union"` | `name`, `fields`, `is_typedef` |
| `"enum"` | `name`, `values` (list of `{name, value}`), `is_typedef` |
| `"function"` | `name`, `return_type`, `parameters`, `is_variadic` |
| `"typedef"` | `name`, `underlying_type` |
| `"variable"` | `name`, `type` |
| `"constant"` | `name`, `value`, `is_macro` (optional) |

Structs and unions share the same schema but use distinct `"kind"` values.

## Worked Example

Given this C header:

```c
// calculator.h
typedef enum {
    OP_ADD = 0,
    OP_SUB = 1,
    OP_MUL = 2,
    OP_DIV = 3,
} Operation;

typedef struct {
    double result;
    int error;
} CalcResult;

CalcResult calculate(double a, double b, Operation op);
```

The JSON output is:

```json
{
  "path": "calculator.h",
  "declarations": [
    {
      "kind": "enum",
      "name": "Operation",
      "values": [
        {"name": "OP_ADD", "value": 0},
        {"name": "OP_SUB", "value": 1},
        {"name": "OP_MUL", "value": 2},
        {"name": "OP_DIV", "value": 3}
      ],
      "is_typedef": true
    },
    {
      "kind": "struct",
      "name": "CalcResult",
      "fields": [
        {"name": "result", "type": {"kind": "ctype", "name": "double"}},
        {"name": "error", "type": {"kind": "ctype", "name": "int"}}
      ],
      "is_typedef": true
    },
    {
      "kind": "function",
      "name": "calculate",
      "return_type": {"kind": "ctype", "name": "CalcResult"},
      "parameters": [
        {"name": "a", "type": {"kind": "ctype", "name": "double"}},
        {"name": "b", "type": {"kind": "ctype", "name": "double"}},
        {"name": "op", "type": {"kind": "ctype", "name": "Operation"}}
      ],
      "is_variadic": false
    }
  ]
}
```

## Processing JSON with Python

### List All Functions

```python
import json
from headerkit import get_backend
from headerkit.writers.json import header_to_json_dict

backend = get_backend()
header = backend.parse(open("mylib.h").read(), "mylib.h")
data = header_to_json_dict(header)

functions = [d for d in data["declarations"] if d["kind"] == "function"]
for fn in functions:
    params = ", ".join(
        f"{p['type']['name']} {p.get('name', '')}"
        for p in fn["parameters"]
        if p["type"]["kind"] == "ctype"
    )
    print(f"{fn['name']}({params})")
```

### Find Structs with Pointer Fields

```python
for decl in data["declarations"]:
    if decl["kind"] == "struct" and decl.get("fields"):
        pointer_fields = [
            f for f in decl["fields"]
            if f["type"]["kind"] == "pointer"
        ]
        if pointer_fields:
            names = ", ".join(f["name"] for f in pointer_fields)
            print(f"{decl['name']} has pointer fields: {names}")
```

## Processing JSON with jq

The JSON output is well-suited for processing with [jq](https://jqlang.github.io/jq/):

### List All Declaration Names

```bash
python -c "
from headerkit import get_backend
from headerkit.writers.json import header_to_json
backend = get_backend()
header = backend.parse(open('mylib.h').read(), 'mylib.h')
print(header_to_json(header))
" | jq '.declarations[].name'
```

### Extract Function Signatures

```bash
cat output.json | jq '.declarations[] | select(.kind == "function") | {name, params: [.parameters[].name]}'
```

### Count Declarations by Kind

```bash
cat output.json | jq '[.declarations[].kind] | group_by(.) | map({kind: .[0], count: length})'
```

## Use Case: CI/CD API Diffing

Use JSON export to detect API changes between releases:

```python
"""Compare two header versions and report API changes."""

import json
from headerkit import get_backend
from headerkit.writers.json import header_to_json_dict


def get_api_surface(header_path: str) -> dict[str, str]:
    """Extract function names and their kinds from a header."""
    backend = get_backend()
    with open(header_path) as f:
        header = backend.parse(f.read(), header_path)
    data = header_to_json_dict(header)

    return {
        decl["name"]: decl["kind"]
        for decl in data["declarations"]
        if decl.get("name")
    }


def diff_apis(old_path: str, new_path: str) -> None:
    old_api = get_api_surface(old_path)
    new_api = get_api_surface(new_path)

    added = set(new_api) - set(old_api)
    removed = set(old_api) - set(new_api)

    if added:
        print("Added:")
        for name in sorted(added):
            print(f"  + {new_api[name]} {name}")

    if removed:
        print("Removed:")
        for name in sorted(removed):
            print(f"  - {old_api[name]} {name}")

    if not added and not removed:
        print("No API changes detected.")
```

## Compact Output

For machine-to-machine communication, use `indent=None` for compact JSON:

```python
writer = get_writer("json", indent=None)
compact = writer.write(header)
# Single line, no whitespace
```

Or with the function:

```python
from headerkit.writers.json import header_to_json

compact = header_to_json(header, indent=None)
```

## What's Next

- [Using CFFI Writer](../guides/cffi.md) -- generate CFFI bindings from the same IR
- [Writing Custom Writers](../guides/custom-writers.md) -- build your own output format
- [API Reference: JSON Writer](../reference/json.md) -- full API details
