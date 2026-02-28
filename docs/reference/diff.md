# Diff Writer

The diff writer compares two Header objects and produces structured API
compatibility reports. Each change is classified as breaking or non-breaking.
Output is available in JSON or Markdown format.

## Writer Class

::: headerkit.writers.diff.DiffWriter
    options:
      show_source: false

## Core Function

::: headerkit.writers.diff.diff_headers
    options:
      show_source: false

## Output Formatters

::: headerkit.writers.diff.diff_to_json
    options:
      show_source: false

::: headerkit.writers.diff.diff_to_markdown
    options:
      show_source: false

## Data Types

::: headerkit.writers.diff.DiffReport
    options:
      show_source: false

::: headerkit.writers.diff.DiffEntry
    options:
      show_source: false

## Example

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend()

old_header = backend.parse("""
int add(int a, int b);
int multiply(int a, int b);
""", "math_v1.h")

new_header = backend.parse("""
int add(int a, int b);
double multiply(double a, double b);
int subtract(int a, int b);
""", "math_v2.h")

# JSON report
writer = get_writer("diff", baseline=old_header, format="json")
print(writer.write(new_header))

# Markdown report
writer = get_writer("diff", baseline=old_header, format="markdown")
print(writer.write(new_header))
```

JSON output:

```json
{
  "schema_version": "1.0",
  "baseline": "math_v1.h",
  "target": "math_v2.h",
  "summary": {
    "total": 2,
    "breaking": 1,
    "non_breaking": 1
  },
  "entries": [
    {
      "kind": "function_signature_changed",
      "severity": "breaking",
      "name": "multiply",
      "detail": "return type changed from 'int' to 'double'"
    },
    {
      "kind": "function_added",
      "severity": "non_breaking",
      "name": "subtract",
      "detail": "function 'subtract' added"
    }
  ]
}
```
