"""CLI runner for Phase 2B-2A offline train-only evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_stress_windows import _load_prepare_manifest
from .offline_train_evaluation import build_offline_train_evaluation_report
from .offline_train_evaluation import write_offline_train_evaluation_outputs


def run(args: argparse.Namespace) -> int:
    prepare_manifest = _load_prepare_manifest(Path(args.prepare_manifest))
    stress_window_manifest = json.loads(Path(args.stress_window_manifest).read_text(encoding="utf-8"))
    stress_windows_payload = json.loads(Path(args.stress_windows).read_text(encoding="utf-8"))
    discovery_plan_manifest = json.loads(Path(args.discovery_plan_manifest).read_text(encoding="utf-8"))
    discovery_plan_payload = json.loads(Path(args.discovery_plan).read_text(encoding="utf-8"))

    from .offline_discovery_plan import CostConfig
    from .offline_discovery_plan import OfflineDiscoveryPlan
    from .offline_discovery_plan import OfflineDiscoveryPlanCell

    plan_cells = [
        OfflineDiscoveryPlanCell(
            **{
                **item,
                "cost_config": CostConfig(**item["cost_config"]),
            }
        )
        for item in discovery_plan_payload.get("plan_cells", [])
    ]
    discovery_plan = OfflineDiscoveryPlan(
        status=discovery_plan_payload["status"],
        plan_cells=plan_cells,
        excluded_windows=discovery_plan_payload.get("excluded_windows", []),
        family_summary=discovery_plan_payload.get("family_summary", {}),
        train_window_ids=discovery_plan_payload.get("train_window_ids", []),
        holdout_window_ids=discovery_plan_payload.get("holdout_window_ids", []),
        edge_family_cell_count=discovery_plan_payload.get("edge_family_cell_count", 0),
        conditioning_cell_count=discovery_plan_payload.get("conditioning_cell_count", 0),
        discovery_config_hash=discovery_plan_payload.get("discovery_config_hash", ""),
        plan_hash=discovery_plan_payload.get("plan_hash", ""),
        data_corpus_hash=discovery_plan_payload.get("data_corpus_hash", discovery_plan_manifest.get("data_corpus_hash", "")),
        window_index_hash=discovery_plan_payload.get("window_index_hash", discovery_plan_manifest.get("window_index_hash", "")),
        split_timestamp_boundary_ns=discovery_plan_payload.get("split_timestamp_boundary_ns"),
        train_survivor_cell_ids=discovery_plan_payload.get("train_survivor_cell_ids", []),
        holdout_evaluation_cell_ids=discovery_plan_payload.get("holdout_evaluation_cell_ids", []),
        survivor_freeze_status=discovery_plan_payload.get("survivor_freeze_status", "NOT_RUN_PHASE_2B1"),
        run_id=discovery_plan_manifest.get("run_id", "offline_discovery_plan_unknown"),
        generated_at_utc=discovery_plan_manifest.get("generated_at_utc", "unknown"),
        git_sha=discovery_plan_manifest.get("git_sha", "unknown"),
        stress_rule_config_hash=discovery_plan_manifest.get("stress_rule_config_hash"),
        precommitment_hash=discovery_plan_manifest.get("precommitment_hash"),
    )

    report = build_offline_train_evaluation_report(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_window_manifest,
        stress_windows_payload=stress_windows_payload,
        discovery_plan_manifest=discovery_plan_manifest,
        discovery_plan_payload=discovery_plan_payload,
        discovery_plan=discovery_plan,
        source_config_path=Path(args.source_config),
    )

    run_dir = Path(args.out) / report.run_id
    outputs = write_offline_train_evaluation_outputs(
        report,
        run_dir,
        prepare_manifest_path=args.prepare_manifest,
        stress_window_manifest_path=args.stress_window_manifest,
        discovery_plan_manifest_path=args.discovery_plan_manifest,
        overwrite=args.overwrite,
    )

    print(
        json.dumps(
            {
                "status": report.status,
                "run_id": report.run_id,
                "manifest_path": str(outputs["manifest_path"]),
                "report_path": str(outputs["report_path"]),
                "evaluation_hash": report.evaluation_hash,
                "plan_hash": report.plan_hash,
                "window_index_hash": report.window_index_hash,
                "data_corpus_hash": report.data_corpus_hash,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2B-2A offline train-only evaluation runner.")
    parser.add_argument("--prepare-manifest", required=True, help="Path to Phase 1 offline_prepare_manifest.json")
    parser.add_argument("--stress-window-manifest", required=True, help="Path to Phase 2A stress_window_manifest.json")
    parser.add_argument("--stress-windows", required=True, help="Path to Phase 2A stress_windows.json")
    parser.add_argument("--discovery-plan-manifest", required=True, help="Path to Phase 2B-1 offline_discovery_plan_manifest.json")
    parser.add_argument("--discovery-plan", required=True, help="Path to Phase 2B-1 offline_discovery_plan.json")
    parser.add_argument("--source-config", required=True, help="Path to offline_sources.json used for Phase 1 prepare")
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_train_evaluation",
        help="Base output directory for run-specific train-evaluation artifacts",
    )
    parser.add_argument("--overwrite", action="store_true", default=False, help="Allow overwriting an existing run output directory")
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
