"""CLI runner for Phase 2B-1 offline discovery-plan generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_discovery_plan import STATUS_INPUT_HASH_MISMATCH
from .offline_discovery_plan import STATUS_INVALID_DISCOVERY_CONFIG
from .offline_discovery_plan import STATUS_UNUSABLE_STRESS_INDEX
from .offline_discovery_plan import STATUS_WINDOW_INDEX_HASH_MISMATCH
from .offline_discovery_plan import build_offline_discovery_plan
from .offline_discovery_plan import load_discovery_config
from .offline_discovery_plan import write_offline_discovery_plan_outputs
from .offline_stress_windows import _load_prepare_manifest


_FAILURE_STATUSES = {
    STATUS_INPUT_HASH_MISMATCH,
    STATUS_WINDOW_INDEX_HASH_MISMATCH,
    STATUS_UNUSABLE_STRESS_INDEX,
    STATUS_INVALID_DISCOVERY_CONFIG,
}


def run(args: argparse.Namespace) -> int:
    prepare_manifest = _load_prepare_manifest(Path(args.prepare_manifest))
    stress_window_manifest = json.loads(Path(args.stress_window_manifest).read_text(encoding="utf-8"))
    stress_windows_payload = json.loads(Path(args.stress_windows).read_text(encoding="utf-8"))
    discovery_config_payload = json.loads(Path(args.discovery_config).read_text(encoding="utf-8"))
    discovery_config = load_discovery_config(discovery_config_payload)

    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_window_manifest,
        stress_windows_payload=stress_windows_payload,
        discovery_config=discovery_config,
    )

    run_dir = Path(args.out) / plan.run_id
    outputs = write_offline_discovery_plan_outputs(
        plan,
        run_dir,
        prepare_manifest_path=str(Path(args.prepare_manifest)),
        stress_window_manifest_path=str(Path(args.stress_window_manifest)),
        overwrite=args.overwrite,
    )

    print(
        json.dumps(
            {
                "status": plan.status,
                "run_id": plan.run_id,
                "plan_path": str(outputs["plan_path"]),
                "manifest_path": str(outputs["manifest_path"]),
                "plan_hash": plan.plan_hash,
                "discovery_config_hash": plan.discovery_config_hash,
                "window_index_hash": plan.window_index_hash,
                "data_corpus_hash": plan.data_corpus_hash,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 2 if plan.status in _FAILURE_STATUSES else 0



def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2B-1 offline discovery-plan runner.")
    parser.add_argument("--prepare-manifest", required=True, help="Path to Phase 1 offline_prepare_manifest.json")
    parser.add_argument("--stress-window-manifest", required=True, help="Path to Phase 2A stress_window_manifest.json")
    parser.add_argument("--stress-windows", required=True, help="Path to Phase 2A stress_windows.json")
    parser.add_argument("--discovery-config", required=True, help="Path to offline discovery config JSON")
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_discovery_plan",
        help="Base output directory for run-specific discovery-plan artifacts",
    )
    parser.add_argument("--overwrite", action="store_true", default=False, help="Allow overwriting an existing run output directory")
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
