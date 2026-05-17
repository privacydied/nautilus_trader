# Copyright (C) 2026. All rights reserved.
"""CLI entrypoint to create a discovery grid lock.

Usage
-----
uv run -m examples.strategies.venue_agnostic_signal_observer.discovery.run_lock_discovery_grid \\
    <grid_spec.json> <output_lock.json>

Exits zero on success, non-zero on failure.
Does **not** start capture, evaluation, or any network connection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .grid_lock import create_grid_lock, save_grid_lock
from .search_space import (
    DiscoveryGridSpec,
    enumerate_cost_sensitivity_cell_count,
    enumerate_primary_cell_count,
    grid_sha256,
    load_grid_spec,
    validate_grid_spec,
)


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: run_lock_discovery_grid.py <grid_spec.json> <output_lock.json>",
              file=sys.stderr)
        return 1

    grid_path = sys.argv[1]
    lock_path = sys.argv[2]

    # Load and validate
    try:
        spec = load_grid_spec(grid_path)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid spec: {exc}", file=sys.stderr)
        return 1

    try:
        validate_grid_spec(spec)
    except Exception as exc:
        print(f"Grid spec validation error: {exc}", file=sys.stderr)
        return 1

    # Compute
    ghash = grid_sha256(spec)
    primary = enumerate_primary_cell_count(spec)
    cost_sens = enumerate_cost_sensitivity_cell_count(spec)

    # Create lock
    lock = create_grid_lock(spec)

    # Write
    lock_path_obj = Path(lock_path)
    if lock_path_obj.exists():
        from .grid_lock import lock_file_is_semantically_identical
        if lock_file_is_semantically_identical(lock, lock_path_obj):
            print(f"Already locked (semantically identical): {lock_path}")
            print(f"  grid_hash: {ghash}")
            print(f"  primary_cell_count: {primary}")
            print(f"  cost_sensitivity_cell_count: {cost_sens}")
            return 0
        print(f"ERROR: Lock file already exists with different content: {lock_path}",
              file=sys.stderr)
        print("Create a new grid_id.  No --force option exists.", file=sys.stderr)
        return 1

    save_grid_lock(lock, lock_path)
    print(f"Grid lock written: {lock_path}")
    print(f"  grid_hash: {ghash}")
    print(f"  primary_cell_count: {primary}")
    print(f"  cost_sensitivity_cell_count: {cost_sens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
