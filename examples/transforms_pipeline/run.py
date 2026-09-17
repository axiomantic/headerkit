#!/usr/bin/env python3
"""Runnable example demonstrating HeaderKit IR pipeline filters and transformations."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure headerkit root is in sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from headerkit.backends import get_backend
from headerkit.ir import CType, Pointer, Struct
from headerkit.transforms import MakeOpaque, StripPrefix


def main() -> None:
    header_path = Path(__file__).parent / "input.h"
    backend = get_backend("libclang")
    unit = backend.parse(header_path.read_text(), filename=str(header_path))

    print(f"Original unit declarations: {len(unit.declarations)}")
    for d in unit.declarations:
        print(f"  - {type(d).__name__}: {getattr(d, 'name', '')}")

    # Build transformation pipeline:
    # 1. Strip 'ma_' prefix across all declarations and type references
    # 2. Make 'device' opaque (stripping its internal fields)
    pipeline = StripPrefix("ma_") >> MakeOpaque("device")
    transformed = pipeline.transform(unit)

    print("\nTransformed unit declarations:")
    for d in transformed.declarations:
        print(f"  - {type(d).__name__}: {getattr(d, 'name', '')}")

    # Verification: 'ma_device' is now 'device' with 0 fields (opaque)
    device_struct = next(d for d in transformed.declarations if isinstance(d, Struct) and d.name == "device")
    assert len(device_struct.fields) == 0, "device struct should be opaque"

    # Verification: 'context' struct's field type was rewritten from 'ma_device*' to 'device*'
    context_struct = next(d for d in transformed.declarations if isinstance(d, Struct) and d.name == "context")
    field_type = context_struct.fields[0].type
    assert isinstance(field_type, Pointer), "field should be pointer"
    assert isinstance(field_type.pointee, CType) and field_type.pointee.name == "device", (
        f"Expected pointee 'device', got '{field_type.pointee}'"
    )

    print("\nAll pipeline transformations verified successfully!")


if __name__ == "__main__":
    main()
