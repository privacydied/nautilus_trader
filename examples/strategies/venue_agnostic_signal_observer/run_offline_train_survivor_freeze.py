"""CLI runner for Phase 2B-2B1 offline train survivor freeze."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_train_survivor_freeze import OfflineTrainSurvivorFreezeConfig
from .offline_train_survivor_freeze import build_offline_train_survivor_freeze_result
from .offline_train_survivor_freeze import write_offline_train_survivor_freeze_outputs


def _load_freeze_config(path: str | None) -> OfflineTrainSurvivorFreezeConfig:
    if not path:
        return OfflineTrainSurvivorFreezeConfig()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return OfflineTrainSurvivorFreezeConfig(
        min_valid_events=int(payload.get("min_valid_events", 2)),
        min_net_mean_bps=None if payload.get("min_net_mean_bps") is None else float(payload.get("min_net_mean_bps")),
        min_net_median_bps=None if payload.get("min_net_median_bps") is None else float(payload.get("min_net_median_bps")),
        min_win_rate=None if payload.get("min_win_rate") is None else float(payload.get("min_win_rate")),
        min_worst_net_bps=None if payload.get("min_worst_net_bps") is None else float(payload.get("min_worst_net_bps")),
        max_survivors=None if payload.get("max_survivors") is None else int(payload.get("max_survivors")),
    )


def run(args: argparse.Namespace) -> int:
    train_evaluation_manifest = json.loads(Path(args.train_evaluation_manifest).read_text(encoding="utf-8"))
    train_evaluation_payload = json.loads(Path(args.train_evaluation).read_text(encoding="utf-8"))
    freeze_config = _load_freeze_config(args.freeze_config)
    result = build_offline_train_survivor_freeze_result(
        train_evaluation_manifest=train_evaluation_manifest,
        train_evaluation_payload=train_evaluation_payload,
        freeze_config=freeze_config,
    )
    run_dir = Path(args.out) / result.run_id
    outputs = write_offline_train_survivor_freeze_outputs(
        result,
        run_dir,
        train_evaluation_manifest_path=args.train_evaluation_manifest,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "run_id": result.run_id,
                "manifest_path": str(outputs["manifest_path"]),
                "result_path": str(outputs["result_path"]),
                "evaluation_hash": result.evaluation_hash,
                "survivor_freeze_config_hash": result.survivor_freeze_config_hash,
                "survivor_freeze_hash": result.survivor_freeze_hash,
                "train_survivor_cell_ids": result.train_survivor_cell_ids,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2B-2B1 offline train survivor freeze runner.")
    parser.add_argument("--train-evaluation-manifest", required=True, help="Path to Phase 2B-2A offline_train_evaluation_manifest.json")
    parser.add_argument("--train-evaluation", required=True, help="Path to Phase 2B-2A offline_train_evaluation.json")
    parser.add_argument("--freeze-config", default=None, help="Optional JSON file overriding survivor freeze thresholds")
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_train_survivor_freeze",
        help="Base output directory for run-specific train-survivor-freeze artifacts",
    )
    parser.add_argument("--overwrite", action="store_true", default=False, help="Allow overwriting an existing run output directory")
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
