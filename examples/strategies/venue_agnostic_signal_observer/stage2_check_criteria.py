#!/usr/bin/env python3
"""
Stage 2 criteria checker — apply committed acceptance criteria.

Applies the precommitted acceptance criteria to discovery and (later)
sealed test results.  Reads thresholds from stage2_precommitment.json.

Modes:
- discovery: uses discovery run IDs, selects frozen configs, writes
  frozen_discovery_configs.json.  Must not read test summaries.
  If the final discovery-criteria survivor set is empty, writes a burn
  record for the full validated corpus.
- holdout: uses frozen_discovery_configs.json and test run IDs only.
  Must not add configs, retune thresholds, or inspect configs outside
  the frozen list.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .stage2_precommitment_utils import (
    CollectionLock,
    load_precommitment,
    get_signal_family,
    _get_git_sha,
)
from .run_artifacts import atomic_write_json, atomic_write_text
from .quarantine import get_quarantined_run_ids
from .burn import burn_corpus, get_burned_run_ids


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _load_json(path: Path) -> dict[str, Any] | list[Any]:
    """Load a JSON file, raising on error."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _compute_ceil_fraction(
    n_captures: int, fraction: float
) -> int:
    """Compute ceil(fraction * n_captures)."""
    return math.ceil(fraction * n_captures)


def run_discovery_check(
    precommit: dict[str, Any],
    fdr_result: dict[str, Any],
    evaluator_summaries: list[dict[str, Any]],
    discovery_runs: list[dict[str, Any]],
    *,
    quarantined_run_ids: set[str] | None = None,
    burned_run_ids: set[str] | None = None,
    test_run_ids: list[str] | None = None,
    lock: CollectionLock | None = None,
) -> dict[str, Any]:
    """Run the discovery criteria check.

    Returns a result dict with keys including:
        mode, status, frozen_configs, survivors, burn_record, errors.
    """
    errors: list[str] = []

    # Lock verification
    if lock and lock.exists():
        lock_ok, lock_reason = lock.verify()
        if not lock_ok:
            errors.append(f"Lock verification failed: {lock_reason}")

    # Markdown/JSON match
    from .stage2_precommitment_utils import validate_markdown_json_match
    match_ok, match_reason = validate_markdown_json_match(precommit)
    if not match_ok:
        errors.append(f"Markdown/JSON mismatch: {match_reason}")

    discovery_accept = precommit.get("discovery_acceptance", {})
    min_events = discovery_accept.get("minimum_valid_events_per_config", 50)
    mean_bps_threshold = discovery_accept.get(
        "minimum_aggregate_mean_net_bps_per_event", 2.0
    )
    same_sign_frac = discovery_accept.get(
        "minimum_same_sign_capture_fraction", 0.70
    )
    same_sign_rounding = discovery_accept.get("same_sign_rounding", "ceil")
    worst_floor = discovery_accept.get("worst_capture_mean_net_bps_floor", -5.0)
    requires_bh = discovery_accept.get("requires_primary_bh_survival", True)

    burned_ids = burned_run_ids or set()
    quarantined_ids = quarantined_run_ids or set()

    # Collect discovery run IDs
    discovery_run_ids = set()
    for r in discovery_runs:
        rid = r.get("run_id")
        if rid and rid not in quarantined_ids and rid not in burned_ids:
            discovery_run_ids.add(rid)

    # Process evaluator summaries
    survivors: list[dict[str, Any]] = []
    bh_failed_configs: list[dict[str, Any]] = []

    # Check FDR result
    fdr_status = fdr_result.get("status", "")
    if fdr_status == "FDR_FAILED":
        errors.extend(fdr_result.get("errors", ["FDR step failed"]))
    elif fdr_status == "NO_DATA":
        errors.append("FDR has no data")

    # Process each evaluator summary — collect ALL capture-group data
    all_capture_data: list[dict[str, Any]] = []
    for summary in evaluator_summaries:
        run_id = summary.get("run_id") or summary.get("_metadata", {}).get("run_id")
        if not run_id:
            continue
        if run_id not in discovery_run_ids:
            continue

        # Get results by group from summary
        groups = summary.get("results_by_group", summary.get("results", []))
        if not groups:
            continue

        for group in groups:
            mean_bps = group.get("mean_net_bps_per_event")
            event_count = group.get("valid_event_count", group.get("n_events", 0))
            p_value = group.get("p_value")

            # Extract dimensions matching test family
            dims = {}
            for key in (
                "source_venue", "target_venue", "symbol",
                "signal_type", "lookback_ms", "horizon_ms",
            ):
                val = group.get(key) if group.get(key) is not None else summary.get(key)
                if val is not None:
                    dims[key] = val

            dims["run_id"] = run_id

            # Minimum events — always apply
            if event_count < min_events:
                continue

            # Skip groups without a computable mean net bps — cannot evaluate criteria
            if mean_bps is None:
                continue

            all_capture_data.append({
                **dims,
                "mean_net_bps_per_event": mean_bps,
                "valid_event_count": event_count,
                "p_value": p_value,
            })

    # Group by config and evaluate criteria
    def _config_key_dict(key_str: str) -> dict[str, Any]:
        try:
            return json.loads(key_str)
        except json.JSONDecodeError:
            return {}

    def _config_key(s: dict[str, Any]) -> str:
        return json.dumps(
            {k: s[k] for k in (
                "source_venue", "target_venue", "symbol",
                "signal_type", "lookback_ms", "horizon_ms",
            ) if k in s},
            sort_keys=True,
        )

    # Group by config
    config_data: dict[str, dict[str, Any]] = {}
    for cap in all_capture_data:
        key = _config_key(cap)
        if key not in config_data:
            config_data[key] = {
                **_config_key_dict(key),
                "captures": [],
                "mean_bps_values": [],
                "signs": [],
                "event_counts": [],
                "p_values": [],
            }
        config_data[key]["captures"].append(cap)
        config_data[key]["mean_bps_values"].append(cap["mean_net_bps_per_event"])
        if cap["mean_net_bps_per_event"] is not None:
            config_data[key]["signs"].append(
                1 if cap["mean_net_bps_per_event"] > 0 else -1
            )
        config_data[key]["event_counts"].append(cap["valid_event_count"])
        if cap["p_value"] is not None:
            config_data[key]["p_values"].append(cap["p_value"])

    # Apply criteria per config
    frozen_configs: list[dict[str, Any]] = []
    n_discovery = len(discovery_run_ids)
    required_same_sign = _compute_ceil_fraction(n_discovery, same_sign_frac)

    for key_str, cfg_data in config_data.items():
        mean_bps_values = cfg_data["mean_bps_values"]
        signs = cfg_data["signs"]

        if not mean_bps_values:
            continue

        # Aggregate mean — economic bar
        agg_mean = sum(mean_bps_values) / len(mean_bps_values)
        if agg_mean < mean_bps_threshold:
            continue

        # Worst capture — tail bar (checked across ALL captures)
        worst = min(mean_bps_values)
        if worst < worst_floor:
            continue

        # Consistency bar (same-sign)
        if not signs:
            continue
        aggregate_sign = 1 if sum(signs) > 0 else -1
        same_sign_count = sum(1 for s in signs if s == aggregate_sign)
        if same_sign_count < required_same_sign:
            continue

        # BH survival — check if any capture for this config was rejected by BH
        if requires_bh:
            found_bh_survivor = False
            for cap in cfg_data["captures"]:
                pv = cap.get("p_value")
                if pv is not None:
                    for bh_item in (fdr_result.get("primary_result") or []):
                        match = all(
                            bh_item.get(k) == cap.get(k)
                            for k in ("source_venue", "target_venue", "symbol",
                                      "signal_type", "lookback_ms", "horizon_ms")
                            if k in cap and k in bh_item
                        ) if cap and bh_item else False
                        if match and bh_item.get("rejected", False):
                            found_bh_survivor = True
                            break
                if found_bh_survivor:
                    break
            if not found_bh_survivor:
                # This config failed BH survival — no capture was significant at FDR q threshold.
                # Variable named `bh_failed_configs` (not `rejected_by_bh`) to avoid the semantic
                # pitfall: in BH/FDR terminology "rejected" means the null WAS rejected and the
                # finding IS significant; this list contains configs that did NOT survive BH.
                bh_failed_configs.append(cfg_data)
                continue

        frozen_configs.append({
            **_config_key_dict(key_str),
            "aggregate_mean_net_bps": round(agg_mean, 4),
            "n_captures": len(cfg_data["mean_bps_values"]),
            "same_sign_count": same_sign_count,
            "required_same_sign": required_same_sign,
            "worst_capture_mean_bps": round(worst, 4),
            "survived_discovery": True,
        })

    burn_record: dict[str, Any] | None = None

    if not frozen_configs:
        # Burn the entire validated corpus
        discovery_run_ids_list = [r.get("run_id") for r in discovery_runs if r.get("run_id")]
        test_run_ids_list = test_run_ids or []
        all_run_ids = list(set(
            [r for r in discovery_run_ids_list if r] +
            [r for r in test_run_ids_list if r]
        ))

        signal_family = get_signal_family(precommit)
        git_sha = _get_git_sha()

        burnt_path = burn_corpus(
            signal_family=signal_family,
            reason="Final discovery-criteria survivor set empty",
            precommitment_git_sha=git_sha,
            precommitment_json=precommit,
            run_ids=all_run_ids,
            discovery_run_ids=discovery_run_ids_list,
            test_run_ids=test_run_ids_list,
            burned_by="stage2_check_criteria",
            notes="Automated burn: final discovery-criteria survivor set empty",
        )

        burn_record = {
            "burned": True,
            "burn_path": str(burnt_path.resolve()),
            "reason": "Final discovery-criteria survivor set empty",
            "discovery_run_ids": discovery_run_ids_list,
            "test_run_ids": test_run_ids_list,
        }

    result: dict[str, Any] = {
        "mode": "discovery",
        "checked_at": _ts_now_iso(),
        "signal_family": get_signal_family(precommit),
        "discovery_run_count": len(discovery_run_ids),
        "total_groups_processed": sum(
            len(s.get("results_by_group", s.get("results", [])))
            for s in evaluator_summaries
        ),
        "survivors": survivors,
        "frozen_configs": frozen_configs,
        "frozen_config_count": len(frozen_configs),
        "bh_failed_configs": len(bh_failed_configs),
        "required_same_sign": required_same_sign,
        "n_discovery_captures": n_discovery,
        "errors": errors,
        "burn_record": burn_record,
        "status": (
            "DISCOVERY_COMPLETED_WITH_SURVIVORS" if frozen_configs
            else "DISCOVERY_COMPLETED_NO_SURVIVORS"
        ),
    }

    return result


def run_holdout_check(
    precommit: dict[str, Any],
    evaluator_summaries: list[dict[str, Any]],
    frozen_configs: list[dict[str, Any]],
    test_runs: list[dict[str, Any]],
    *,
    lock: CollectionLock | None = None,
) -> dict[str, Any]:
    """Run the holdout criteria check.

    Returns a result dict with keys including:
        mode, status, survivors, errors.
    """
    errors: list[str] = []

    # Lock verification
    if lock and lock.exists():
        lock_ok, lock_reason = lock.verify()
        if not lock_ok:
            errors.append(f"Lock verification failed: {lock_reason}")

    holdout_accept = precommit.get("holdout_acceptance", {})
    min_events = holdout_accept.get("minimum_valid_events_per_config", 50)
    mean_bps_threshold = holdout_accept.get(
        "minimum_aggregate_mean_net_bps_per_event", 2.0
    )
    worst_floor = holdout_accept.get("worst_capture_mean_net_bps_floor", -5.0)
    frozen_only = holdout_accept.get("frozen_config_only", True)

    test_run_ids = {r.get("run_id") for r in test_runs if r.get("run_id")}

    # Build frozen config set
    frozen_set: set[str] = set()
    for fc in frozen_configs:
        key = json.dumps(
            {k: fc[k] for k in (
                "source_venue", "target_venue", "symbol",
                "signal_type", "lookback_ms", "horizon_ms",
            ) if k in fc},
            sort_keys=True,
        )
        frozen_set.add(key)

    survivors: list[dict[str, Any]] = []

    for summary in evaluator_summaries:
        run_id = summary.get("run_id")
        if not run_id or run_id not in test_run_ids:
            continue

        groups = summary.get("results_by_group", summary.get("results", []))
        for group in groups:
            mean_bps = group.get("mean_net_bps_per_event")
            event_count = group.get("valid_event_count", group.get("n_events", 0))

            dims = {}
            for key in (
                "source_venue", "target_venue", "symbol",
                "signal_type", "lookback_ms", "horizon_ms",
            ):
                val = group.get(key) if group.get(key) is not None else summary.get(key)
                if val is not None:
                    dims[key] = val

            # Check frozen
            if frozen_only:
                config_key = json.dumps(dims, sort_keys=True)
                if config_key not in frozen_set:
                    continue

            # Minimum events
            if event_count < min_events:
                continue

            # Mean bps
            if mean_bps is None or mean_bps < mean_bps_threshold:
                continue

            # Tail
            if mean_bps is not None and mean_bps < worst_floor:
                continue

            survivors.append({
                **dims,
                "run_id": run_id,
                "mean_net_bps_per_event": mean_bps,
                "valid_event_count": event_count,
            })

    result: dict[str, Any] = {
        "mode": "holdout",
        "checked_at": _ts_now_iso(),
        "test_run_count": len(test_run_ids),
        "frozen_config_count": len(frozen_configs),
        "survivors": survivors,
        "survivor_count": len(survivors),
        "errors": errors,
        "status": "HOLDOUT_COMPLETED" if not errors else "HOLDOUT_FAILED",
    }

    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 2 criteria checker — apply committed "
                    "acceptance criteria."
    )
    p.add_argument(
        "--mode", type=str, required=True,
        choices=["discovery", "holdout"],
        help="Mode: discovery (select frozen configs) or holdout (test only)",
    )
    p.add_argument(
        "--fdr-result", type=str, default=None,
        help="Path to FDR result JSON (required for discovery mode)",
    )
    p.add_argument(
        "--discovery-runs", type=str, default=None,
        help="Path to discovery runs JSON (required for discovery mode)",
    )
    p.add_argument(
        "--test-runs", type=str, default=None,
        help="Path to test runs JSON (required for holdout mode)",
    )
    p.add_argument(
        "--frozen-configs", type=str, default=None,
        help="Path to frozen configs JSON (required for holdout mode)",
    )
    p.add_argument(
        "--evaluator-summaries-dir", type=str, default=None,
        help="Directory containing evaluator summary.json files",
    )
    p.add_argument(
        "--out-dir", type=str, default=None,
        help="Output directory (default: reports/stage2_criteria)",
    )
    p.add_argument(
        "--precommit-path", type=str, default=None,
        help="Path to stage2_precommitment.json",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    precommit = load_precommitment(
        Path(args.precommit_path) if args.precommit_path else None
    )

    root = Path.cwd().resolve()
    out_dir = Path(args.out_dir) if args.out_dir else root / "reports" / "stage2_criteria"
    out_dir.mkdir(parents=True, exist_ok=True)

    lock = CollectionLock()

    if args.mode == "discovery":
        # Load FDR result
        fdr_path = Path(args.fdr_result) if args.fdr_result else root / "reports" / "stage2_fdr" / "stage2_fdr_result.json"
        if not fdr_path.exists():
            print(f"ERROR: FDR result not found: {fdr_path}", file=sys.stderr)
            sys.exit(1)
        fdr_result: dict[str, Any] = _load_json(fdr_path)

        # Load discovery runs
        disc_path = Path(args.discovery_runs) if args.discovery_runs else root / "reports" / "stage2_split" / "stage2_discovery_runs.json"
        discovery_runs: list[dict[str, Any]] = _load_json(disc_path) if disc_path.exists() else []

        # Load evaluator summaries dir
        eval_dir = Path(args.evaluator_summaries_dir) if args.evaluator_summaries_dir else root / "reports"
        summaries: list[dict[str, Any]] = []
        if eval_dir.exists():
            for d in eval_dir.iterdir():
                if d.is_dir():
                    sp = d / "summary.json"
                    if sp.exists():
                        try:
                            summaries.append(_load_json(sp))
                        except (json.JSONDecodeError, OSError):
                            pass

        result = run_discovery_check(
            precommit=precommit,
            fdr_result=fdr_result,
            evaluator_summaries=summaries,
            discovery_runs=discovery_runs,
            lock=lock,
        )

        # Write results
        disc_summary_path = out_dir / "stage2_discovery_criteria_summary.json"
        atomic_write_json(disc_summary_path, result)

        # Write markdown report
        report_lines = [
            "# Stage 2 Discovery Criteria Report",
            "",
            f"- **Status:** {result.get('status', '?')}",
            f"- **Checked at:** {result.get('checked_at', '?')}",
            f"- **Frozen configs:** {result.get('frozen_config_count', 0)}",
            f"- **Discovery runs:** {result.get('discovery_run_count', 0)}",
            f"- **Required same-sign:** {result.get('required_same_sign', 0)}/{result.get('n_discovery_captures', 0)}",
            "",
        ]

        if result.get("frozen_configs"):
            report_lines.append("## Frozen Discovery Configs")
            report_lines.append("")
            for fc in result["frozen_configs"]:
                report_lines.append(
                    f"- {fc.get('source_venue', '?')} → {fc.get('target_venue', '?')} "
                    f"{fc.get('symbol', '?')} {fc.get('signal_type', '?')} "
                    f"lookback={fc.get('lookback_ms', '?')}ms "
                    f"horizon={fc.get('horizon_ms', '?')}ms "
                    f"| agg_mean={fc.get('aggregate_mean_net_bps', '?')} "
                    f"n_captures={fc.get('n_captures', 0)} "
                    f"same_sign={fc.get('same_sign_count', 0)}"
                )
            report_lines.append("")

        if result.get("burn_record"):
            report_lines.append("## Burn Record")
            report_lines.append("")
            br = result["burn_record"]
            report_lines.append(f"- **Burned:** {br.get('burned', False)}")
            report_lines.append(f"- **Reason:** {br.get('reason', '?')}")
            report_lines.append(f"- **Burn path:** {br.get('burn_path', '?')}")
            report_lines.append("")

        if result.get("errors"):
            report_lines.append("## Errors")
            report_lines.append("")
            for err in result["errors"]:
                report_lines.append(f"- ERROR: {err}")
            report_lines.append("")

        report_text = "\n".join(report_lines)
        report_path = out_dir / "stage2_discovery_criteria_report.md"
        atomic_write_text(report_path, report_text)

        # Write frozen configs
        frozen_path = out_dir / "frozen_discovery_configs.json"
        atomic_write_json(frozen_path, result.get("frozen_configs", []))

        print(f"  Mode:            discovery")
        print(f"  Status:          {result.get('status', '?')}")
        print(f"  Frozen configs:  {result.get('frozen_config_count', 0)}")
        print(f"  Burned:          {result.get('burn_record', {}).get('burned', False)}")
        for err in result.get("errors", []):
            print(f"  ERROR: {err}", file=sys.stderr)

        if result.get("errors"):
            sys.exit(1)

    elif args.mode == "holdout":
        # Load test runs
        test_path = Path(args.test_runs) if args.test_runs else root / "reports" / "stage2_split" / "stage2_test_runs.json"
        test_runs: list[dict[str, Any]] = _load_json(test_path) if test_path.exists() else []

        # Load frozen configs
        frozen_path = Path(args.frozen_configs) if args.frozen_configs else out_dir / "frozen_discovery_configs.json"
        frozen_configs: list[dict[str, Any]] = _load_json(frozen_path) if frozen_path.exists() else []

        # Load evaluator summaries
        eval_dir = Path(args.evaluator_summaries_dir) if args.evaluator_summaries_dir else root / "reports"
        summaries = []
        if eval_dir.exists():
            for d in eval_dir.iterdir():
                if d.is_dir():
                    sp = d / "summary.json"
                    if sp.exists():
                        try:
                            summaries.append(_load_json(sp))
                        except (json.JSONDecodeError, OSError):
                            pass

        result = run_holdout_check(
            precommit=precommit,
            evaluator_summaries=summaries,
            frozen_configs=frozen_configs,
            test_runs=test_runs,
            lock=lock,
        )

        holdout_path = out_dir / "stage2_holdout_criteria_summary.json"
        atomic_write_json(holdout_path, result)

        print(f"  Mode:            holdout")
        print(f"  Status:          {result.get('status', '?')}")
        print(f"  Survivors:       {result.get('survivor_count', 0)}")
        for err in result.get("errors", []):
            print(f"  ERROR: {err}", file=sys.stderr)

        if result.get("errors") or result.get("status") == "HOLDOUT_FAILED":
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
