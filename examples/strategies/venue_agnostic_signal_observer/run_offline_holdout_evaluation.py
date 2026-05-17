"""CLI runner for Phase 2B-2B2 offline holdout evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_holdout_evaluation import build_offline_holdout_evaluation_report, write_offline_holdout_evaluation_outputs
from .offline_historical_models import OfflinePrepareManifest


def run(args: argparse.Namespace) -> int:
    prepare_manifest = OfflinePrepareManifest(**json.loads(Path(args.prepare_manifest).read_text(encoding="utf-8")))
    stress_window_manifest = json.loads(Path(args.stress_window_manifest).read_text(encoding="utf-8"))
    stress_windows_payload = json.loads(Path(args.stress_windows).read_text(encoding="utf-8"))
    discovery_plan_manifest = json.loads(Path(args.discovery_plan_manifest).read_text(encoding="utf-8"))
    discovery_plan_payload = json.loads(Path(args.discovery_plan).read_text(encoding="utf-8"))
    train_evaluation_manifest = json.loads(Path(args.train_evaluation_manifest).read_text(encoding="utf-8"))
    train_evaluation_payload = json.loads(Path(args.train_evaluation).read_text(encoding="utf-8"))
    train_survivor_freeze_manifest = json.loads(Path(args.train_survivor_freeze_manifest).read_text(encoding="utf-8"))
    train_survivor_freeze_payload = json.loads(Path(args.train_survivor_freeze).read_text(encoding="utf-8"))

    report = build_offline_holdout_evaluation_report(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_window_manifest,
        stress_windows_payload=stress_windows_payload,
        discovery_plan_manifest=discovery_plan_manifest,
        discovery_plan_payload=discovery_plan_payload,
        train_evaluation_manifest=train_evaluation_manifest,
        train_evaluation_payload=train_evaluation_payload,
        train_survivor_freeze_manifest=train_survivor_freeze_manifest,
        train_survivor_freeze_payload=train_survivor_freeze_payload,
        source_config_path=Path(args.source_config),
    )
    run_dir = Path(args.out) / report.run_id
    outputs = write_offline_holdout_evaluation_outputs(
        report,
        run_dir,
        prepare_manifest_path=args.prepare_manifest,
        stress_window_manifest_path=args.stress_window_manifest,
        discovery_plan_manifest_path=args.discovery_plan_manifest,
        train_evaluation_manifest_path=args.train_evaluation_manifest,
        train_survivor_freeze_manifest_path=args.train_survivor_freeze_manifest,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            {
                "status": report.status,
                "run_id": report.run_id,
                "manifest_path": str(outputs["manifest_path"]),
                "result_path": str(outputs["result_path"]),
                "data_corpus_hash": report.data_corpus_hash,
                "window_index_hash": report.window_index_hash,
                "plan_hash": report.plan_hash,
                "evaluation_hash": report.evaluation_hash,
                "survivor_freeze_hash": report.survivor_freeze_hash,
                "holdout_evaluation_hash": report.holdout_evaluation_hash,
                "holdout_evaluated_cell_ids": report.holdout_evaluated_cell_ids,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2B-2B2 offline holdout evaluation runner.")
    parser.add_argument("--prepare-manifest", required=True, help="Path to Phase 1 offline_prepare_manifest.json")
    parser.add_argument("--stress-window-manifest", required=True, help="Path to Phase 2A stress_window_manifest.json")
    parser.add_argument("--stress-windows", required=True, help="Path to Phase 2A stress_windows.json")
    parser.add_argument("--discovery-plan-manifest", required=True, help="Path to Phase 2B-1 offline_discovery_plan_manifest.json")
    parser.add_argument("--discovery-plan", required=True, help="Path to Phase 2B-1 offline_discovery_plan.json")
    parser.add_argument("--train-evaluation-manifest", required=True, help="Path to Phase 2B-2A offline_train_evaluation_manifest.json")
    parser.add_argument("--train-evaluation", required=True, help="Path to Phase 2B-2A offline_train_evaluation.json")
    parser.add_argument("--train-survivor-freeze-manifest", required=True, help="Path to Phase 2B-2B1 offline_train_survivor_freeze_manifest.json")
    parser.add_argument("--train-survivor-freeze", required=True, help="Path to Phase 2B-2B1 offline_train_survivor_freeze.json")
    parser.add_argument("--source-config", required=True, help="Path to offline source config used to reload prepared streams")
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_holdout_evaluation",
        help="Base output directory for run-specific holdout-evaluation artifacts",
    )
    parser.add_argument("--overwrite", action="store_true", default=False, help="Allow overwriting an existing run output directory")
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
