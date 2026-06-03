"""CLI entrypoint to create a discovery candidate lock.

Usage:
    python run_lock_discovery_candidate.py <grid_spec.json> <grid_lock.json> <selected_cells.json> <lock_output.json> [capture_manifest.json ...]

Loads a grid spec, grid lock, selected cells, and capture manifest paths.
Validates all inputs and writes a candidate lock JSON.
"""

from __future__ import annotations

import json
import sys

from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    create_candidate_lock,
    load_candidate_lock,
    save_candidate_lock,
    canonical_candidate_payload,
    candidate_sha256,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint import (
    build_capture_manifest_ref,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import (
    load_grid_lock,
    validate_grid_spec_against_lock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.search_space import (
    load_grid_spec,
    validate_grid_spec,
)


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "Usage: run_lock_discovery_candidate.py "
            "<grid_spec.json> <grid_lock.json> <selected_cells.json> "
            "<lock_output.json> [capture_manifest.json ...]",
            file=sys.stderr,
        )
        return 1

    grid_spec_path = sys.argv[1]
    grid_lock_path = sys.argv[2]
    cells_path = sys.argv[3]
    lock_output_path = sys.argv[4]
    capture_paths = sys.argv[5:]

    # Load grid spec
    try:
        spec = load_grid_spec(grid_spec_path)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid spec: {exc}", file=sys.stderr)
        return 1

    try:
        validate_grid_spec(spec)
    except Exception as exc:
        print(f"Grid spec validation error: {exc}", file=sys.stderr)
        return 1

    # Load grid lock
    try:
        grid_lock = load_grid_lock(grid_lock_path)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid lock: {exc}", file=sys.stderr)
        return 1

    # Validate grid spec against grid lock
    try:
        validate_grid_spec_against_lock(spec, grid_lock)
    except Exception as exc:
        print(f"Grid spec vs lock validation error: {exc}", file=sys.stderr)
        return 1

    # Load selected cells JSON
    try:
        with open(cells_path, "r") as f:
            cells_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading selected cells: {exc}", file=sys.stderr)
        return 1

    if not isinstance(cells_data, list):
        print("Error: selected_cells JSON must be an array", file=sys.stderr)
        return 1

    # Validate capture manifest paths
    if not capture_paths:
        print("Error: at least one capture manifest path is required", file=sys.stderr)
        return 1

    # Build capture manifest refs
    capture_refs = []
    for cap_path in capture_paths:
        try:
            ref = build_capture_manifest_ref(cap_path)
            capture_refs.append(ref)
        except Exception as exc:
            print(f"Error processing capture manifest {cap_path}: {exc}", file=sys.stderr)
            return 1

    # Create candidate lock
    try:
        candidate_lock = create_candidate_lock(
            grid_spec=spec,
            grid_lock=grid_lock,
            candidate_id="cli-candidate",
            selected_cells=tuple(cells_data),
            cluster_summary={},
            selection_reason="CLI-generated candidate lock",
            discovery_capture_refs=tuple(capture_refs),
        )
    except Exception as exc:
        print(f"Error creating candidate lock: {exc}", file=sys.stderr)
        return 1

    # Check if lock output already exists
    from pathlib import Path
    lock_path_obj = Path(lock_output_path)
    if lock_path_obj.exists():
        try:
            existing_lock = load_candidate_lock(lock_path_obj)
        except Exception as exc:
            print(f"Error loading existing candidate lock: {exc}", file=sys.stderr)
            return 1

        # Compare by canonical JSON
        from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
            canonical_candidate_payload,
        )
        new_canon = canonical_candidate_payload(candidate_lock)
        existing_canon = canonical_candidate_payload(existing_lock)

        if new_canon == existing_canon:
            print(f"Already locked (semantically identical): {lock_output_path}")
            print(f"  candidate_hash: {candidate_lock.candidate_hash}")
            return 0
        else:
            print(
                f"ERROR: Candidate lock file already exists with different content: "
                f"{lock_output_path}",
                file=sys.stderr,
            )
            print("Create a new candidate or change candidate_id.", file=sys.stderr)
            return 1

    # Write the lock file
    save_candidate_lock(candidate_lock, lock_output_path)
    print(f"Candidate lock written: {lock_output_path}")
    print(f"  candidate_hash: {candidate_lock.candidate_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
