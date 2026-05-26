# Copyright (C) 2026. All rights reserved.
"""CLI entrypoint to create a discovery candidate lock.

Usage
-----
uv run -m examples.strategies.venue_agnostic_signal_observer.discovery.run_lock_discovery_candidate \\
    <grid_spec.json> <grid_lock.json> <selected_cells.json> <output_candidate_lock.json> \\
    --capture <manifest_1.json> [--capture <manifest_2.json> ...]

Exits zero on success, non-zero on failure.
Does **not** start validation metrics or promote to tradeable status.
Does **not** connect to any network endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .candidate_lock import (
    CANDIDATE_SCHEMA_VERSION,
    candidate_locks_are_semantically_identical,
    create_candidate_lock,
    load_candidate_lock,
    save_candidate_lock,
)
from .capture_fingerprint import build_capture_manifest_ref
from .grid_lock import DiscoveryGridLock, load_grid_lock, validate_grid_spec_against_lock
from .search_space import load_grid_spec, validate_grid_spec


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a discovery candidate lock",
    )
    parser.add_argument("grid_spec", help="Path to grid spec JSON")
    parser.add_argument("grid_lock", help="Path to grid lock JSON")
    parser.add_argument("selected_cells", help="Path to selected cells JSON")
    parser.add_argument("output", help="Path for output candidate lock JSON")
    parser.add_argument(
        "--candidate-id",
        default="candidate_v1",
        help="Human-readable candidate label (default: candidate_v1)",
    )
    parser.add_argument(
        "--capture",
        action="append",
        default=[],
        dest="capture_paths",
        help="Path to a capture manifest JSON (may be repeated)",
    )
    parser.add_argument(
        "--cluster-summary",
        default=None,
        help="Path to cluster summary JSON (optional)",
    )
    parser.add_argument(
        "--selection-reason",
        default="Discovered by Edge Miner (Phase 1 freeze)",
        help="Reason for selecting this cluster (default: auto)",
    )

    args = parser.parse_args()

    # Require at least the grid spec path
    if not args.grid_spec:
        print("Error: grid_spec path is required", file=sys.stderr)
        return 1

    # Load grid spec
    try:
        spec = load_grid_spec(args.grid_spec)
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
        grid_lock = load_grid_lock(args.grid_lock)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading grid lock: {exc}", file=sys.stderr)
        return 1

    try:
        validate_grid_spec_against_lock(spec, grid_lock)
    except Exception as exc:
        print(f"Grid lock validation error: {exc}", file=sys.stderr)
        return 1

    # Load selected cells
    try:
        with open(args.selected_cells) as f:
            selected_cells = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
        print(f"Error loading selected cells: {exc}", file=sys.stderr)
        return 1

    if not isinstance(selected_cells, list):
        print("Error: selected_cells JSON must be an array", file=sys.stderr)
        return 1

    # Load cluster summary if provided
    cluster_summary = {}
    if args.cluster_summary:
        try:
            with open(args.cluster_summary) as f:
                cluster_summary = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
            print(f"Error loading cluster summary: {exc}", file=sys.stderr)
            return 1

    # Capture manifest paths -> CaptureManifestRef objects
    if not args.capture_paths:
        print("Error: At least one --capture manifest path is required",
              file=sys.stderr)
        return 1

    try:
        discovery_capture_refs = tuple(
            build_capture_manifest_ref(p) for p in args.capture_paths
        )
    except Exception as exc:
        print(f"Error building capture manifest refs: {exc}", file=sys.stderr)
        return 1

    # Create candidate lock
    try:
        lock = create_candidate_lock(
            grid_spec=spec,
            grid_lock=grid_lock,
            candidate_id=args.candidate_id,
            selected_cells=tuple(selected_cells),
            cluster_summary=cluster_summary,
            selection_reason=args.selection_reason,
            discovery_capture_refs=discovery_capture_refs,
        )
    except Exception as exc:
        print(f"Error creating candidate lock: {exc}", file=sys.stderr)
        return 1

    # Write candidate lock
    output_path = Path(args.output)
    if output_path.exists():
        try:
            existing = load_candidate_lock(output_path)
            if candidate_locks_are_semantically_identical(lock, existing):
                print(f"Already locked (semantically identical): {args.output}")
                print(f"  candidate_hash: {lock.candidate_hash}")
                return 0
        except Exception:
            pass
        print(
            f"ERROR: Candidate lock file already exists with different "
            f"content: {args.output}",
            file=sys.stderr,
        )
        print("Create a new candidate_id.  No --force option exists.",
              file=sys.stderr)
        return 1

    save_candidate_lock(lock, args.output)
    print(f"Candidate lock written: {args.output}")
    print(f"  candidate_hash: {lock.candidate_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
