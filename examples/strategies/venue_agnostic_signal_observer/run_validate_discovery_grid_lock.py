"""CLI entrypoint to validate a discovery grid spec against its lock.

Usage:
    python run_validate_discovery_grid_lock.py <grid_spec.json> <grid_lock.json>

Validates grid_id, schema_version, grid_hash, primary_cell_count,
and cost_sensitivity_cell_count.
"""

from __future__ import annotations

import json
import sys

from examples.strategies.venue_agnostic_signal_observer.discovery.search_space import (
    load_grid_spec,
    validate_grid_spec,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import (
    load_grid_lock,
    validate_grid_spec_against_lock,
)


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: run_validate_discovery_grid_lock.py <grid_spec.json> <grid_lock.json>",
            file=sys.stderr,
        )
        return 1

    grid_path = sys.argv[1]
    lock_path = sys.argv[2]

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

    try:
        lock = load_grid_lock(lock_path)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid lock: {exc}", file=sys.stderr)
        return 1

    try:
        validate_grid_spec_against_lock(spec, lock)
    except Exception as exc:
        print(f"Validation FAILED: {exc}", file=sys.stderr)
        return 1

    print("Validation PASSED: grid spec matches grid lock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
