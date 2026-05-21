"""
CLI runner for Phase 2B-2C3A offline native null p-value generation.

Consumes Phase 2B-2C1 comparison artifact and Phase 2B-2B2 holdout evaluation
artifact. Generates sign-flip p-values from event-level return vectors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_native_null import OfflineNativeNullConfig
from .offline_native_null import _parse_config
from .offline_native_null import _validate_config_keys
from .offline_native_null import build_offline_native_null_report
from .offline_native_null import write_offline_native_null_outputs


def run(args: argparse.Namespace) -> int:
    comparison_manifest = json.loads(Path(args.comparison_manifest).read_text(encoding="utf-8"))
    comparison_payload = json.loads(Path(args.comparison).read_text(encoding="utf-8"))
    holdout_manifest = json.loads(Path(args.holdout_evaluation_manifest).read_text(encoding="utf-8"))
    holdout_payload = json.loads(Path(args.holdout_evaluation).read_text(encoding="utf-8"))

    null_config: OfflineNativeNullConfig | None = None
    if args.null_config:
        config_dict = json.loads(Path(args.null_config).read_text(encoding="utf-8"))
        _validate_config_keys(config_dict)
        null_config = _parse_config(config_dict)

    report = build_offline_native_null_report(
        comparison_manifest=comparison_manifest,
        comparison_payload=comparison_payload,
        holdout_evaluation_manifest=holdout_manifest,
        holdout_evaluation_payload=holdout_payload,
        null_config=null_config,
    )

    run_dir = Path(args.out) / report.run_id
    outputs = write_offline_native_null_outputs(
        report,
        run_dir,
        comparison_manifest_path=str(Path(args.comparison_manifest).resolve()),
        holdout_evaluation_manifest_path=str(Path(args.holdout_evaluation_manifest).resolve()),
        overwrite=args.overwrite,
    )

    print(
        json.dumps(
            {
                "status": report.status,
                "run_id": report.run_id,
                "manifest_path": str(outputs["manifest_path"]),
                "result_path": str(outputs["result_path"]),
                "fdr_pvalue_path": str(outputs["fdr_pvalue_path"]) if outputs.get("fdr_pvalue_path") else None,
                "native_null_config_hash": report.native_null_config_hash,
                "native_null_hash": report.native_null_hash,
                "eligible_cell_count": len(report.eligible_cell_ids),
                "tested_cell_count": len(report.tested_cell_ids),
                "excluded_cell_count": len(report.excluded_cell_ids),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2B-2C3A offline native null p-value generation."
    )
    parser.add_argument(
        "--comparison-manifest",
        required=True,
        help="Path to Phase 2B-2C1 offline_train_holdout_comparison_manifest.json",
    )
    parser.add_argument(
        "--comparison",
        required=True,
        help="Path to Phase 2B-2C1 offline_train_holdout_comparison.json",
    )
    parser.add_argument(
        "--holdout-evaluation-manifest",
        required=True,
        help="Path to Phase 2B-2B2 offline_holdout_evaluation_manifest.json",
    )
    parser.add_argument(
        "--holdout-evaluation",
        required=True,
        help="Path to Phase 2B-2B2 offline_holdout_evaluation.json",
    )
    parser.add_argument(
        "--null-config",
        default=None,
        help="Optional path to native null config JSON file",
    )
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_native_null",
        help="Base output directory for run-specific native null artifacts",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting existing output directories",
    )

    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
