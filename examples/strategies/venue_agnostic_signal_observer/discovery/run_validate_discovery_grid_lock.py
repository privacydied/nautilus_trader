# Copyright (C) 2026. All rights reserved.
r"""
CLI entrypoint to validate a grid spec against its grid lock.

Usage
-----
uv run -m examples.strategies.venue_agnostic_signal_observer.discovery.run_validate_discovery_grid_lock \\
    <grid_spec.json> <grid_lock.json>

Exits zero on match, non-zero on mismatch.
Does **not** start capture, evaluation, or any network connection.
"""

from __future__ import annotations

import json
import sys

from .grid_lock import DiscoveryGridLock
from .grid_lock import validate_grid_lock
from .search_space import load_grid_spec


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: run_validate_discovery_grid_lock.py <grid_spec.json> <grid_lock.json>",
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

    # Load grid lock
    try:
        with open(lock_path) as f:
            lock_data = json.load(f)
        lock = DiscoveryGridLock(**lock_data)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid lock: {exc}", file=sys.stderr)
        return 1

    # Validate
    try:
        validate_grid_lock(spec, lock)
    except Exception as exc:
        print(f"Validation FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        f"Grid spec '{spec.grid_id}' matches grid lock. "
        f"Grid hash: {lock.grid_hash}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
