#!/usr/bin/env python3
"""Runnable example demonstrating HeaderKit configuration matrix, cross-probing, and reconciliation."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure headerkit root is in sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from headerkit.matrix import ConfigMatrix, probe_configuration
from headerkit.reconcile import MatrixGuardedBlock, reconcile_matrix
from headerkit.writers import get_writer


def main() -> None:
    header_path = str(Path(__file__).parent / "audio_stub.h")

    # Define a configuration matrix spanning three target platforms
    matrix = ConfigMatrix()
    matrix.add_dimension(
        "target",
        ["x86_64-apple-darwin", "x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"],
        dimension_type="target",
    )

    configs = matrix.resolve()
    print(f"Resolving matrix: {len(configs)} configurations")

    matrix_units = []
    for cond in configs:
        target = cond.get("target")
        print(f"  Probing target: {target}...")
        unit = probe_configuration(header_path, cond)
        matrix_units.append((cond, unit))

    # Reconcile ASTs across the matrix
    reconciled = reconcile_matrix(matrix_units, matrix=matrix, target_lang="nim")
    print(f"\nReconciled items: {len(reconciled.items)}")

    # Guard block verification
    guarded_blocks = [it for it in reconciled.items if isinstance(it, MatrixGuardedBlock)]
    assert len(guarded_blocks) >= 1, "Expected at least one matrix-guarded block for platform divergence"

    # Generate Nim bindings
    writer = get_writer("nim")
    nim_code = writer.write(reconciled)

    print("\n--- Generated Nim Code with Platform Guards ---")
    print(nim_code)

    assert "audio_init" in nim_code
    assert "audio_shutdown" in nim_code
    assert "when defined(macosx):" in nim_code
    assert "audio_play_coreaudio" in nim_code
    assert "when defined(linux):" in nim_code or "elif defined(linux):" in nim_code
    assert "audio_play_alsa" in nim_code
    assert "when defined(windows):" in nim_code or "elif defined(windows):" in nim_code
    assert "audio_play_wasapi" in nim_code

    print("All matrix reconciliation assertions passed successfully!")


if __name__ == "__main__":
    main()
