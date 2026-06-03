"""Generic altcoin stress regime ablation Phase 0B.

Forward-return diagnostic for a price-only generic altcoin stress detector.
Reuses shared helpers from the liquidation-flush Phase 0B stack where feasible.

This is an ablation. Phase 0B is return diagnostic only.
Specificity cannot be judged until Phase 0C null/clustering falsification.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import traceback
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Sequence

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    LoadDiagnostics,
    _candidate_archive_files,
    compute_precommitment_hash,
    load_archive_rows,
    parse_timestamp,
)
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0b import (
    HORIZONS_HOURS,
    PRIMARY_HORIZON_HOURS,
    PRIMARY_COST_BPS,
    SECONDARY_COST_BPS,
    MIN_EFFECTIVE_EVENTS,
    ALTCOIN_EXCLUDED_SYMBOLS,
    HorizonEvaluation,
    HorizonMetrics,
    Phase0BResult,
    EventReproductionMismatch,
    _jsonl_sha256,
    _load_jsonl,
    validate_event_universe,
    load_phase0a_event_artifact,
    build_price_series,
    lookup_future_price,
    _mean_lcb_95,
    _metrics_for_horizon,
    build_summary_distribution,
    net_bps_after_cost,
    precommitment_recorded_and_computed,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json,
    atomic_write_text,
)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

STUDY_ID = "generic_altcoin_stress_regime_ablation_phase0"
STAGE = "phase0b_return_diagnostic"
VENUE = "hyperliquid"

PHASE0A_REQUIRED_STATUS = "GENERIC_STRESS_PHASE0A_READY"
GENERIC_EVENT_DIRECTION = "downside_price_drop"
GENERIC_TRADE_DIRECTION = "long"

# Secondary stress costs for reporting (in addition to primary 50 bps)
STRESS_COST_BPS = (75.0, 100.0)

# Statuses
STATUS_RETURN_DIAGNOSTIC_PASS = "GENERIC_STRESS_PHASE0B_RETURN_DIAGNOSTIC_PASS"
STATUS_RETURN_DIAGNOSTIC_FAIL = "GENERIC_STRESS_PHASE0B_RETURN_DIAGNOSTIC_FAIL"
STATUS_INSUFFICIENT_FORWARD_COVERAGE = "GENERIC_STRESS_PHASE0B_INSUFFICIENT_FORWARD_COVERAGE"
STATUS_ERROR_INVALID_PHASE0A = "GENERIC_STRESS_PHASE0B_ERROR_INVALID_PHASE0A_ARTIFACT"
STATUS_ERROR_PRECOMMITMENT = "GENERIC_STRESS_PHASE0B_ERROR_PRECOMMITMENT_MISMATCH"
STATUS_ERROR = "GENERIC_STRESS_PHASE0B_ERROR"

# Benchmark comparison (liquidation-flush Phase 0B venue-age-aware)
LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS = 125.94987373067414
LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS = 97.65100671140897
LIQUIDATION_BENCHMARK_24H_WIN_RATE = 0.5667718191377498


# ------------------------------------------------------------------
# Direction handling for generic stress
# ------------------------------------------------------------------


def compute_directional_return_bps(event_direction: str, event_price: float, future_price: float) -> float:
    """Compute forward return for a generic downside stress event.

    All generic stress events are downside_price_drop → long direction
    (bet on mean reversion / aftershock rebound).
    """
    if event_price <= 0 or future_price <= 0:
        raise ValueError("prices must be positive")
    if event_direction == GENERIC_EVENT_DIRECTION:
        return (future_price / event_price - 1.0) * 10000.0
    raise ValueError(f"ambiguous event direction: {event_direction}")


# ------------------------------------------------------------------
# Phase 0A report loading (generic status)
# ------------------------------------------------------------------


def load_generic_phase0a_report(report_dir: Path) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    """Load and validate a generic stress Phase 0A report.

    Checks for GENERIC_STRESS_PHASE0A_READY status.
    Reuses the shared event-artifact validation pipeline.
    """
    summary_path = report_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    if summary.get("status") != PHASE0A_REQUIRED_STATUS or summary.get("unlocks_phase0b") is not True:
        raise EventReproductionMismatch(
            f"Phase 0A status is {summary.get('status')}, expected {PHASE0A_REQUIRED_STATUS}"
        )
    artifact_rel = summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl"
    artifact_path = report_dir / artifact_rel
    events = load_phase0a_event_artifact(artifact_path, summary)
    return summary, artifact_path, events


# ------------------------------------------------------------------
# Event evaluation (generic direction)
# ------------------------------------------------------------------


def evaluate_events(
    events: Sequence[dict[str, Any]],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> tuple[list[HorizonEvaluation], list[HorizonMetrics], int]:
    """Evaluate forward returns for generic stress events.

    Only accepts events with event_direction == 'downside_price_drop'.
    Evaluates long direction (mean reversion after downside stress).
    """
    evaluations: list[HorizonEvaluation] = []
    excluded = 0
    for event in events:
        symbol = str(event["symbol"]).upper()
        direction = str(event.get("event_direction") or "")
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            excluded += 1
            continue
        if direction != GENERIC_EVENT_DIRECTION:
            excluded += 1
            continue
        event_ts = parse_timestamp(event["event_timestamp_utc"])
        event_price = float(event["price_t"])
        series = price_series.get(symbol, [])
        for horizon in HORIZONS_HOURS:
            future_price = lookup_future_price(series, event_ts + timedelta(hours=horizon))
            if future_price is None:
                evaluations.append(
                    HorizonEvaluation(
                        str(event.get("event_id", "")),
                        symbol,
                        event["event_timestamp_utc"],
                        direction,
                        horizon,
                        event_price,
                        None, None, None, None, True,
                    )
                )
            else:
                gross = compute_directional_return_bps(direction, event_price, future_price)
                evaluations.append(
                    HorizonEvaluation(
                        str(event.get("event_id", "")),
                        symbol,
                        event["event_timestamp_utc"],
                        direction,
                        horizon,
                        event_price,
                        future_price,
                        gross,
                        net_bps_after_cost(gross, PRIMARY_COST_BPS),
                        net_bps_after_cost(gross, SECONDARY_COST_BPS),
                        False,
                    )
                )
    metrics = [_metrics_for_horizon(h, evaluations) for h in HORIZONS_HOURS]
    return evaluations, metrics, excluded


# ------------------------------------------------------------------
# Stress-cost reporting (75 bps, 100 bps)
# ------------------------------------------------------------------


def compute_stress_cost_metrics(
    evaluations: Sequence[HorizonEvaluation],
    horizon: int,
    cost_bps: float,
) -> dict[str, float | None]:
    """Compute net metrics at a given stress cost for a horizon."""
    vals = [e for e in evaluations if e.horizon_hours == horizon and e.gross_return_bps is not None]
    net_vals = [e.gross_return_bps - cost_bps for e in vals]
    if not net_vals:
        return {"net_mean_bps": None, "net_median_bps": None}
    return {
        "net_mean_bps": mean(net_vals),
        "net_median_bps": median(net_vals),
    }


# ------------------------------------------------------------------
# Primary verdict
# ------------------------------------------------------------------


def classify_primary_verdict(primary: HorizonMetrics) -> str:
    """Classify the primary horizon verdict.

    Uses the same logic as the liquidation Phase 0B but with
    generic status names.
    """
    if primary.evaluated_event_count < MIN_EFFECTIVE_EVENTS:
        return STATUS_INSUFFICIENT_FORWARD_COVERAGE
    if primary.net_mean_bps_50bps is None or primary.net_mean_bps_50bps <= 0:
        return STATUS_RETURN_DIAGNOSTIC_FAIL
    if primary.net_median_bps_50bps is None or primary.net_median_bps_50bps <= 0:
        return STATUS_RETURN_DIAGNOSTIC_FAIL
    if primary.win_rate_50bps is None or primary.win_rate_50bps <= 0.50:
        return STATUS_RETURN_DIAGNOSTIC_FAIL
    if primary.net_mean_bps_50bps <= 10 or primary.win_rate_50bps <= 0.53:
        return STATUS_RETURN_DIAGNOSTIC_FAIL
    if primary.mean_lcb_95_bps_50bps is None or primary.mean_lcb_95_bps_50bps <= 0:
        return STATUS_RETURN_DIAGNOSTIC_FAIL
    return STATUS_RETURN_DIAGNOSTIC_PASS


# ------------------------------------------------------------------
# Comparison block
# ------------------------------------------------------------------


def build_comparison_block(
    generic_metrics: list[HorizonMetrics],
) -> dict[str, Any]:
    """Build comparison block vs liquidation-flush Phase 0B benchmark."""
    primary = next((m for m in generic_metrics if m.horizon_hours == PRIMARY_HORIZON_HOURS), None)
    if primary is None:
        return {"comparison": "no_primary_metrics"}
    return {
        "comparison_vs_liquidation_flush": {
            "liquidation_flush_24h_net_mean_bps": LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS,
            "liquidation_flush_24h_net_median_bps": LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS,
            "liquidation_flush_24h_win_rate": LIQUIDATION_BENCHMARK_24H_WIN_RATE,
            "generic_stress_24h_net_mean_bps": primary.net_mean_bps_50bps,
            "generic_stress_24h_net_median_bps": primary.net_median_bps_50bps,
            "generic_stress_24h_win_rate": primary.win_rate_50bps,
            "generic_stress_24h_evaluated_count": primary.evaluated_event_count,
            "generic_stress_24h_missing_forward": primary.missing_forward_count,
            "ratio_net_mean_generic_vs_liquidation": (
                primary.net_mean_bps_50bps / LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS
                if primary.net_mean_bps_50bps and LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS
                else None
            ),
            "disclaimer": (
                "Phase 0B is return diagnostic only. "
                "Specificity cannot be judged until Phase 0C null/clustering "
                "falsification is run."
            ),
        }
    }


# ------------------------------------------------------------------
# Markdown report
# ------------------------------------------------------------------


def summary_markdown(result: Phase0BResult) -> str:
    s = result.summary
    primary = next((m for m in s.get("horizon_metrics", []) if m.get("horizon_hours") == PRIMARY_HORIZON_HOURS), {})
    comparison = s.get("comparison_vs_liquidation_flush", {})

    # Stress costs section
    stress_lines = ""
    for cost_label in STRESS_COST_BPS:
        cost_metrics = s.get(f"stress_cost_{int(cost_label)}bps", {})
        stress_lines += (
            f"\n  {int(cost_label)} bps net mean: `{cost_metrics.get('net_mean_bps')}`\n"
            f"  {int(cost_label)} bps net median: `{cost_metrics.get('net_median_bps')}`\n"
        )

    # Comparison section
    comp_lines = ""
    if comparison:
        comp_lines = f"""

## Comparison vs liquidation-flush Phase 0B (venue-age-aware)

| Metric | Liquidation flush | Generic stress (this study) |
|---|---|---|
| 24h net mean after 50 bps | `{comparison.get('liquidation_flush_24h_net_mean_bps')}` | `{comparison.get('generic_stress_24h_net_mean_bps')}` |
| 24h net median after 50 bps | `{comparison.get('liquidation_flush_24h_net_median_bps')}` | `{comparison.get('generic_stress_24h_net_median_bps')}` |
| 24h win rate after 50 bps | `{comparison.get('liquidation_flush_24h_win_rate')}` | `{comparison.get('generic_stress_24h_win_rate')}` |
| Evaluated event count | — | `{comparison.get('generic_stress_24h_evaluated_count')}` |
| Missing forward coverage | — | `{comparison.get('generic_stress_24h_missing_forward')}` |

Net mean ratio (generic / liquidation): `{comparison.get('ratio_net_mean_generic_vs_liquidation')}`

{comparison.get('disclaimer', '')}
"""

    return f"""# Generic altcoin stress regime ablation Phase 0B

Study: `{STUDY_ID}`
Stage: `{STAGE}`

Status: `{s.get('status')}`

Final verdict: `{s.get('phase0b_final_verdict')}`

Phase 0A report: `{s.get('phase0a_report_path')}`

Phase 0A event artifact SHA-256: `{s.get('phase0a_event_artifact_sha256')}`

Event direction: `{GENERIC_EVENT_DIRECTION}` → {GENERIC_TRADE_DIRECTION} (long)

## Primary horizon (24h)

  Evaluated event count: `{primary.get('evaluated_event_count')}`
  Missing forward coverage: `{primary.get('missing_forward_count')}`
  Gross mean bps: `{primary.get('gross_mean_bps')}`
  Gross median bps: `{primary.get('gross_median_bps')}`
  Net mean bps after 50 bps cost: `{primary.get('net_mean_bps_50bps')}`
  Net median bps after 50 bps cost: `{primary.get('net_median_bps_50bps')}`
  Win rate after 50 bps cost: `{primary.get('win_rate_50bps')}`
  95% lower confidence bound (50 bps): `{primary.get('mean_lcb_95_bps_50bps')}`

## Stress cost sensitivity{stress_lines}

{comp_lines}
## Scope

Price-only generic altcoin stress detector. No OI, liquidation, or funding inputs.
No returns, PnL, null tests, FDR, live execution, paper trading, shadow execution,
orders, private keys, or trading auth were used.

Phase 0B is return diagnostic only. Specificity cannot be judged until Phase 0C
null/clustering falsification is run.

No orders, private keys, trading auth, live execution, paper trading, shadow execution,
systemd watcher, bot path, or REJECTED_RESEARCH.md update were used.
"""


# ------------------------------------------------------------------
# Report writing
# ------------------------------------------------------------------


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report_artifacts(result: Phase0BResult, report_dir: Path) -> Phase0BResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "summary.json", result.summary)
    atomic_write_text(report_dir / "summary.md", summary_markdown(result))
    _write_csv(report_dir / "horizon_metrics.csv", [asdict(m) for m in result.horizon_metrics])
    _write_csv(report_dir / "event_horizon_evaluations.csv", [asdict(e) for e in result.evaluations])
    manifest = {"artifacts": ["summary.json", "summary.md", "horizon_metrics.csv", "event_horizon_evaluations.csv"]}
    atomic_write_json(report_dir / "artifact_manifest.json", manifest)
    result.report_dir = str(report_dir)
    return result


# ------------------------------------------------------------------
# Main run function
# ------------------------------------------------------------------


def run_phase0b(
    phase0a_report: Path,
    archive_paths: Sequence[Path],
    precommitment_path: Path,
    report_dir: Path | None = None,
) -> Phase0BResult:
    """Run the generic altcoin stress Phase 0B return diagnostic."""
    # Precommitment validation
    recorded, computed = precommitment_recorded_and_computed(precommitment_path)
    if not recorded or recorded != computed:
        summary = {
            "status": STATUS_ERROR_PRECOMMITMENT,
            "phase0b_final_verdict": STATUS_ERROR_PRECOMMITMENT,
            "precommitment_sha256": computed,
            "unlocks_phase0c": False,
        }
        return Phase0BResult(summary, [], [])

    try:
        phase0a_summary, event_artifact, events = load_generic_phase0a_report(phase0a_report)
    except EventReproductionMismatch as exc:
        summary = {
            "status": STATUS_ERROR_INVALID_PHASE0A,
            "phase0b_final_verdict": STATUS_ERROR_INVALID_PHASE0A,
            "phase0b_locked_reason": str(exc),
            "precommitment_sha256": computed,
            "unlocks_phase0c": False,
        }
        return Phase0BResult(summary, [], [])

    # Load archive data
    rows, diagnostics = load_archive_rows(archive_paths)

    # Build price series
    price_series = build_price_series(rows)

    # Evaluate events
    evaluations, metrics, excluded_count = evaluate_events(events, price_series)

    # Primary horizon
    primary = next(m for m in metrics if m.horizon_hours == PRIMARY_HORIZON_HOURS)
    verdict = classify_primary_verdict(primary)

    # Stress cost sensitivity
    stress_75 = compute_stress_cost_metrics(evaluations, PRIMARY_HORIZON_HOURS, 75.0)
    stress_100 = compute_stress_cost_metrics(evaluations, PRIMARY_HORIZON_HOURS, 100.0)

    # Missing forward by horizon
    missing_by_horizon = {str(m.horizon_hours): m.missing_forward_count for m in metrics}

    # Comparison block
    comparison = build_comparison_block(metrics)

    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "status": verdict,
        "phase0b_final_verdict": verdict,
        "phase0b_locked_reason": "",
        "precommitment_sha256": computed,
        "phase0a_report_path": str(phase0a_report),
        "phase0a_precommitment_sha256": phase0a_summary.get("precommitment_sha256"),
        "phase0a_event_artifact_path": str(event_artifact),
        "phase0a_event_artifact_sha256": _jsonl_sha256(event_artifact),
        "phase0a_event_count": len(events),
        "phase0b_event_count_evaluated": primary.evaluated_event_count,
        "excluded_event_count": excluded_count,
        "missing_forward_count_by_horizon": missing_by_horizon,
        "horizon_metrics": [asdict(m) for m in metrics],
        "primary_horizon_hours": PRIMARY_HORIZON_HOURS,
        "primary_cost_bps": PRIMARY_COST_BPS,
        "stress_cost_75bps": stress_75,
        "stress_cost_100bps": stress_100,
        "direction": GENERIC_EVENT_DIRECTION,
        "trade_direction": GENERIC_TRADE_DIRECTION,
        "archive_source_path": ":".join(str(p) for p in archive_paths),
        "archive_rows_loaded": diagnostics.loaded_rows,
        "altcoin_only": True,
        "btc_eth_excluded": True,
        "null_run": False,
        "null_result": None,
        **comparison,
        **build_summary_distribution(events),
        "unlocks_phase0c": verdict == STATUS_RETURN_DIAGNOSTIC_PASS,
    }

    result = Phase0BResult(summary, metrics, evaluations)

    if report_dir is not None:
        try:
            write_report_artifacts(result, report_dir)
        except Exception as exc:
            summary["status"] = STATUS_ERROR
            summary["phase0b_final_verdict"] = STATUS_ERROR
            summary["phase0b_locked_reason"] = f"report write failed: {exc}"
            result = Phase0BResult(summary, metrics, evaluations)

    return result


__all__ = [
    "STUDY_ID",
    "STAGE",
    "VENUE",
    "PHASE0A_REQUIRED_STATUS",
    "GENERIC_EVENT_DIRECTION",
    "GENERIC_TRADE_DIRECTION",
    "STATUS_RETURN_DIAGNOSTIC_PASS",
    "STATUS_RETURN_DIAGNOSTIC_FAIL",
    "STATUS_INSUFFICIENT_FORWARD_COVERAGE",
    "STATUS_ERROR_INVALID_PHASE0A",
    "STATUS_ERROR_PRECOMMITMENT",
    "STATUS_ERROR",
    "STRESS_COST_BPS",
    "LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS",
    "LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS",
    "LIQUIDATION_BENCHMARK_24H_WIN_RATE",
    "compute_directional_return_bps",
    "load_generic_phase0a_report",
    "evaluate_events",
    "compute_stress_cost_metrics",
    "classify_primary_verdict",
    "build_comparison_block",
    "summary_markdown",
    "write_report_artifacts",
    "run_phase0b",
]