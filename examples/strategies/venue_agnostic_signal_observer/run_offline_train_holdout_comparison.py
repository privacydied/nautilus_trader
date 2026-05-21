"""CLI runner for Phase 2B-2C1 offline train-holdout comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_train_holdout_comparison import OfflineTrainHoldoutComparisonConfig
from .offline_train_holdout_comparison import _validate_config_keys
from .offline_train_holdout_comparison import build_offline_train_holdout_comparison_report
from .offline_train_holdout_comparison import write_offline_train_holdout_comparison_outputs


def run(args: argparse.Namespace) -> int:
    train_evaluation_manifest = json.loads(Path(args.train_evaluation_manifest).read_text(encoding="utf-8"))
    train_evaluation_payload = json.loads(Path(args.train_evaluation).read_text(encoding="utf-8"))
    train_survivor_freeze_manifest = json.loads(Path(args.train_survivor_freeze_manifest).read_text(encoding="utf-8"))
    train_survivor_freeze_payload = json.loads(Path(args.train_survivor_freeze).read_text(encoding="utf-8"))
    holdout_evaluation_manifest = json.loads(Path(args.holdout_evaluation_manifest).read_text(encoding="utf-8"))
    holdout_evaluation_payload = json.loads(Path(args.holdout_evaluation).read_text(encoding="utf-8"))

    comparison_config: OfflineTrainHoldoutComparisonConfig | None = None
    if args.comparison_config:
        config_dict = json.loads(Path(args.comparison_config).read_text(encoding="utf-8"))
        _validate_config_keys(config_dict)
        comparison_config = OfflineTrainHoldoutComparisonConfig(**config_dict)

    report = build_offline_train_holdout_comparison_report(
        train_evaluation_manifest=train_evaluation_manifest,
        train_evaluation_payload=train_evaluation_payload,
        train_survivor_freeze_manifest=train_survivor_freeze_manifest,
        train_survivor_freeze_payload=train_survivor_freeze_payload,
        holdout_evaluation_manifest=holdout_evaluation_manifest,
        holdout_evaluation_payload=holdout_evaluation_payload,
        comparison_config=comparison_config,
    )

    run_dir = Path(args.out) / report.run_id
    outputs = write_offline_train_holdout_comparison_outputs(
        report,
        run_dir,
        train_evaluation_manifest_path=args.train_evaluation_manifest,
        train_survivor_freeze_manifest_path=args.train_survivor_freeze_manifest,
        holdout_evaluation_manifest_path=args.holdout_evaluation_manifest,
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
                "comparison_config_hash": report.comparison_config_hash,
                "comparison_hash": report.comparison_hash,
                "train_survivor_cell_ids": report.train_survivor_cell_ids,
                "holdout_surviving_cell_ids": report.holdout_surviving_cell_ids,
                "holdout_failed_cell_ids": report.holdout_failed_cell_ids,
                "missing_holdout_cell_ids": report.missing_holdout_cell_ids,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2B-2C1 offline train-holdout comparison runner."
    )
    parser.add_argument("--train-evaluation-manifest", required=True, help="Path to Phase 2B-2A offline_train_evaluation_manifest.json")
    parser.add_argument("--train-evaluation", required=True, help="Path to Phase 2B-2A offline_train_evaluation.json")
    parser.add_argument("--train-survivor-freeze-manifest", required=True, help="Path to Phase 2B-2B1 offline_train_survivor_freeze_manifest.json")
    parser.add_argument("--train-survivor-freeze", required=True, help="Path to Phase 2B-2B1 offline_train_survivor_freeze.json")
    parser.add_argument("--holdout-evaluation-manifest", required=True, help="Path to Phase 2B-2B2 offline_holdout_evaluation_manifest.json")
    parser.add_argument("--holdout-evaluation", required=True, help="Path to Phase 2B-2B2 offline_holdout_evaluation.json")
    parser.add_argument(
        "--comparison-config",
        default=None,
        help="Optional path to comparison config JSON file",
    )
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_train_holdout_comparison",
        help="Base output directory for run-specific comparison artifacts",
    )
    parser.add_argument(
        "--overwrite", action="store_true", default=False, help="Allow overwriting an existing run output directory"
    )
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
