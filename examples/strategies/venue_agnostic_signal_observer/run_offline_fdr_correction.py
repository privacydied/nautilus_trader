"""CLI runner for Phase 2B-2C2 offline FDR correction.

Consumes Phase 2B-2C1 train-holdout comparison artifact and applies
deterministic FDR correction across eligible edge-family comparison survivors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_fdr_correction import (
    FDR_SCHEMA_VERSION,
    OfflineFdrConfig,
    build_offline_fdr_correction_report,
    compute_fdr_config_hash,
    parse_pvalue_input,
    write_offline_fdr_correction_outputs,
    _parse_config,
    _validate_config_keys,
)


def run(args: argparse.Namespace) -> int:
    comparison_manifest = json.loads(Path(args.comparison_manifest).read_text(encoding="utf-8"))
    comparison_payload = json.loads(Path(args.comparison).read_text(encoding="utf-8"))

    fdr_config: OfflineFdrConfig | None = None
    if args.fdr_config:
        config_dict = json.loads(Path(args.fdr_config).read_text(encoding="utf-8"))
        _validate_config_keys(config_dict)
        fdr_config = _parse_config(config_dict)

    pvalue_input = None
    if args.pvalue_input:
        raw_pvalues = json.loads(Path(args.pvalue_input).read_text(encoding="utf-8"))
        pvalue_input = parse_pvalue_input(raw_pvalues)

    report = build_offline_fdr_correction_report(
        comparison_manifest=comparison_manifest,
        comparison_payload=comparison_payload,
        fdr_config=fdr_config,
        pvalue_input=pvalue_input,
        comparison_manifest_path=str(Path(args.comparison_manifest).resolve()),
    )

    run_dir = Path(args.out) / report.run_id
    outputs = write_offline_fdr_correction_outputs(
        report,
        run_dir,
        comparison_manifest_path=str(Path(args.comparison_manifest).resolve()),
        overwrite=args.overwrite,
    )

    print(
        json.dumps(
            {
                "status": report.status,
                "run_id": report.run_id,
                "manifest_path": str(outputs["manifest_path"]),
                "result_path": str(outputs["result_path"]),
                "fdr_config_hash": report.fdr_config_hash,
                "pvalue_input_hash": report.pvalue_input_hash,
                "fdr_hash": report.fdr_hash,
                "method": report.method,
                "alpha": report.alpha,
                "fdr_family_size": report.fdr_family_size,
                "fdr_passed_cell_count": len(report.fdr_passed_cell_ids),
                "fdr_failed_cell_count": len(report.fdr_failed_cell_ids),
                "excluded_cell_count": len(report.excluded_cell_ids),
                "eligible_cell_ids": report.eligible_cell_ids,
                "fdr_passed_cell_ids": report.fdr_passed_cell_ids,
                "fdr_failed_cell_ids": report.fdr_failed_cell_ids,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 2B-2C2 offline FDR correction runner."
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
        "--fdr-config",
        default=None,
        help="Optional path to FDR config JSON file",
    )
    parser.add_argument(
        "--pvalue-input",
        default=None,
        help="Optional path to p-value input JSON file",
    )
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_fdr_correction",
        help="Base output directory for run-specific FDR correction artifacts",
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
