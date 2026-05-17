# Copyright (C) 2026. All rights reserved.
"""CLI entrypoint to create a discovery grid lock.

Usage
-----
python run_lock_discovery_grid.py <grid_spec.json> <lock_output.json>

Loads a discovery grid JSON, validates it, computes grid hash and cell counts,
and writes a grid lock JSON.

If the lock path already exists:
  - Semantically identical content  → exit 0, print "already locked"
  - Semantically different content  → exit non-zero (no --force)

Does **not** start capture, evaluation, or connect to any network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.discovery.search_space import (
    enumerate_cost_sensitivity_cell_count,
    enumerate_primary_cell_count,
    grid_sha256,
    load_grid_spec,
    validate_grid_spec,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import (
    create_grid_lock,
    load_grid_lock,
    locks_are_semantically_identical,
    save_grid_lock,
)


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: run_lock_discovery_grid.py <grid_spec.json> <lock_output.json>",
            file=sys.stderr,
        )
        return 1

    grid_path = sys.argv[1]
    lock_path = sys.argv[2]

    # Load grid spec
    try:
        spec = load_grid_spec(grid_path)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid spec: {exc}", file=sys.stderr)
        return 1

    # Validate grid spec
    try:
        validate_grid_spec(spec)
    except Exception as exc:
        print(f"Grid spec validation error: {exc}", file=sys.stderr)
        return 1

    # Compute grid hash and cell counts
    ghash = grid_sha256(spec)
    primary = enumerate_primary_cell_count(spec)
    cost_sens = enumerate_cost_sensitivity_cell_count(spec)

    # Create the lock object (re-validates internally)
    lock = create_grid_lock(spec)

    # Check if lock file already exists
    lock_path_obj = Path(lock_path)
    if lock_path_obj.exists():
        try:
            existing_lock = load_grid_lock(lock_path_obj)
        except Exception as exc:
            print(
                f"Error loading existing lock file: {exc}",
                file=sys.stderr,
            )
            return 1

        if locks_are_semantically_identical(lock, existing_lock):
            print(f"Already locked (semantically identical): {lock_path}")
            print(f"  grid_hash: {ghash}")
            print(f"  primary_cell_count: {primary}")
            print(f"  cost_sensitivity_cell_count: {cost_sens}")
            return 0
        else:
            print(
                f"ERROR: Lock file already exists with different content: {lock_path}",
                file=sys.stderr,
            )
            print(
                "Create a new grid_id. No --force option exists.",
                file=sys.stderr,
            )
            return 1

    # Write the lock file
    save_grid_lock(lock, lock_path)
    print(f"Grid lock written: {lock_path}")
    print(f"  grid_hash: {ghash}")
    print(f"  primary_cell_count: {primary}")
    print(f"  cost_sensitivity_cell_count: {cost_sens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
