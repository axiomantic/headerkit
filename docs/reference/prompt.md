# Prompt Writer

The prompt writer converts headerkit IR into token-optimized text
representations designed for embedding C/C++ API information into LLM
prompts. Three verbosity tiers control the output density.

## Writer Class

::: headerkit.writers.prompt.PromptWriter
    options:
      show_source: false

## Verbosity Tiers

### Compact

The most token-efficient format. One-liner C-like declarations prefixed with
keywords (`FUNC`, `STRUCT`, `ENUM`, `TYPEDEF`, `CONST`, `VAR`, `CALLBACK`).
Bitfields use the `name:type:Nb` notation.

### Standard

YAML-like structured text that groups declarations by category (constants,
enums, structs, callbacks, functions, typedefs, variables). Includes struct
field listings and bitfield annotations.

### Verbose

Full JSON output using the JSON writer with additional `used_in`
cross-reference metadata showing which declarations reference each type.

## Example

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend()
header = backend.parse("""
#define MAX_SIZE 256

typedef struct {
    int x;
    int y;
} Point;

int distance(const Point* a, const Point* b);
""", "geometry.h")

# Compact (most token-efficient)
writer = get_writer("prompt", verbosity="compact")
print(writer.write(header))

# Standard (structured)
writer = get_writer("prompt", verbosity="standard")
print(writer.write(header))

# Verbose (full JSON with cross-references)
writer = get_writer("prompt", verbosity="verbose")
print(writer.write(header))
```

Compact output:

```
// geometry.h (headerkit compact)
CONST MAX_SIZE=256
STRUCT Point {x:int, y:int}
FUNC distance(a:const Point*, b:const Point*) -> int
```

Standard output:

```yaml
# geometry.h (headerkit standard)

constants:
  MAX_SIZE: 256

structs:
  Point:
    fields:
      x: int
      y: int

functions:
  distance: (a: const Point*, b: const Point*) -> int
```
