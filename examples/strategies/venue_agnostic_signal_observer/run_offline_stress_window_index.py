"""CLI runner for Phase 2A offline stress-window indexing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .offline_historical_models import WINDOW_MODE_CAUSAL, WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC
from .offline_stress_windows import (
    STATUS_DATA_CORPUS_HASH_MISMATCH,
    StressRuleConfig,
    index_prepared_manifest_reload,
    write_stress_window_outputs,
)


def _default_rules() -> list[StressRuleConfig]:
    return [
        StressRuleConfig(
            rule_name="rolling_range_bps",
            rule_version="v1",
            lookback_seconds=600,
            threshold_bps=150.0,
            cooldown_seconds=900,
            pre_window_seconds=60,
            post_window_seconds=300,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade", "bar"),
            trigger_metric="range_bps",
        ),
        StressRuleConfig(
            rule_name="rolling_absolute_return_bps",
            rule_version="v1",
            lookback_seconds=600,
            threshold_bps=100.0,
            cooldown_seconds=900,
            pre_window_seconds=60,
            post_window_seconds=300,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade", "bar"),
            trigger_metric="absolute_return_bps",
        ),
        StressRuleConfig(
            rule_name="tick_only_burst_placeholder",
            rule_version="v1",
            lookback_seconds=60,
            threshold_bps=1.0,
            cooldown_seconds=120,
            pre_window_seconds=30,
            post_window_seconds=60,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade"),
            trigger_metric="tick_only_placeholder",
        ),
    ]


def run(args: argparse.Namespace) -> int:
    selection_mode = args.selection_mode
    if selection_mode not in {WINDOW_MODE_CAUSAL, WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC}:
        print(f"ERROR: invalid selection mode {selection_mode!r}", file=sys.stderr)
        return 1

    result = index_prepared_manifest_reload(
        prepared_manifest_path=Path(args.prepared_manifest),
        source_config_path=Path(args.source_config),
        stress_rules=_default_rules(),
        selection_mode=selection_mode,
        force_rehash=args.force_rehash,
    )

    run_dir = Path(args.out) / result.manifest_metadata["run_id"]
    write_stress_window_outputs(result, run_dir, overwrite=args.overwrite)

    print(json.dumps({
        "status": result.status,
        "run_id": result.manifest_metadata["run_id"],
        "manifest_path": str(run_dir / "stress_window_manifest.json"),
        "stress_windows_path": str(run_dir / "stress_windows.json"),
        "window_count": result.manifest_metadata["window_count"],
        "window_index_hash": result.manifest_metadata.get("window_index_hash"),
        "data_corpus_hash": result.manifest_metadata.get("data_corpus_hash"),
    }, indent=2, sort_keys=True))
    return 2 if result.status == STATUS_DATA_CORPUS_HASH_MISMATCH else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2A offline stress-window indexing runner.")
    parser.add_argument("--prepared-manifest", required=True, help="Path to Phase 1 offline_prepare_manifest.json")
    parser.add_argument("--source-config", required=True, help="Path to offline_sources.json used for Phase 1 prepare")
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_stress_windows",
        help="Base output directory for run-specific stress-window artifacts",
    )
    parser.add_argument(
        "--selection-mode",
        default=WINDOW_MODE_CAUSAL,
        choices=[WINDOW_MODE_CAUSAL, WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC],
        help="Stress-window selection mode",
    )
    parser.add_argument("--force-rehash", action="store_true", default=False, help="Force recomputation of source file hashes")
    parser.add_argument("--overwrite", action="store_true", default=False, help="Allow overwriting an existing run output directory")
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
