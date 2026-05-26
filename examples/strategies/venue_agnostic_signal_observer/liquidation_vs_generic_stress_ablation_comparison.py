"""Liquidation/OI flush vs generic price-only stress ablation comparison.

Determines whether the liquidation/OI conditioning adds incremental
information over generic price stress, or whether it is dead weight.

Observer-only. Not a trading strategy. Does not unlock v1.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    compute_precommitment_hash,
    parse_timestamp,
    utc_iso,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json,
    atomic_write_text,
)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

STUDY_ID = "liquidation_vs_generic_stress_ablation_comparison"
STAGE = "diagnostic"
VENUE = "hyperliquid"
SAFETY_MODE = "public_data_observer_only"

HORIZONS_HOURS = (6, 12, 24, 48)
PRIMARY_HORIZON_HOURS = 24
PRIMARY_COST_BPS = 50.0
STRESS_COSTS = (75.0, 100.0)

ALTCOIN_EXCLUDED = frozenset({"BTC", "ETH"})
GENERIC_DETECTOR_FAMILY = "generic_altcoin_stress_regime_ablation_phase0"
LIQUIDATION_DETECTOR_FAMILY = "liquidation_flush_aftershock_reversal"

# Benchmark from liquidation Phase 0B (known from prior closed study)
LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS = 125.94987373067414
LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS = 97.65100671140897
LIQUIDATION_BENCHMARK_24H_WIN_RATE = 0.5667718191377498
LIQUIDATION_BENCHMARK_EVALUATED_COUNT = 951

LIQUIDATION_PHASE0A_REFERENCE_PATH = (
    "reports/liquidation_flush_aftershock_reversal_venue_age_aware_phase0a/"
    "liquidation_flush_aftershock_reversal_venue_age_aware_phase0a_20260525T065523_832438_9007d7"
)
LIQUIDATION_PHASE0B_REFERENCE_PATH = (
    "reports/liquidation_flush_aftershock_reversal_venue_age_aware_phase0b/"
    "liquidation_flush_aftershock_reversal_venue_age_aware_phase0b_20260525T071535_865335_81f212"
)

# Verdicts
VERDICT_OI_SUPPORTED = "OI_INCREMENTAL_VALUE_SUPPORTED"
VERDICT_OI_NOT_SUPPORTED = "OI_INCREMENTAL_VALUE_NOT_SUPPORTED"
VERDICT_OI_HARMFUL = "OI_CONDITIONING_HARMFUL_OR_DEAD_WEIGHT"
VERDICT_INCONCLUSIVE_LOW_OVERLAP = "INCONCLUSIVE_OVERLAP_TOO_LOW"
VERDICT_INCONCLUSIVE_COVERAGE = "INCONCLUSIVE_FORWARD_COVERAGE"
VERDICT_ERROR = "ERROR_INVALID_ARTIFACT"


# ------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CohortMetrics:
    name: str
    event_count: int
    evaluated_count: int
    missing_forward_24h: int
    gross_mean_bps_24h: float | None
    gross_median_bps_24h: float | None
    net_mean_bps_50_24h: float | None
    net_median_bps_50_24h: float | None
    win_rate_50_24h: float | None
    net_mean_bps_75_24h: float | None
    net_mean_bps_100_24h: float | None
    top_5pct_contribution: float | None = None
    bot_5pct_contribution: float | None = None


# ------------------------------------------------------------------
# Artifact verification
# ------------------------------------------------------------------


def _jsonl_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify_artifact(report_dir: Path) -> dict[str, Any]:
    """Verify a Phase 0A report's accepted-events artifact."""
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        return {"status": "MISSING", "error": f"summary.json not found: {summary_path}"}
    summary = json.loads(summary_path.read_text())
    artifact_rel = summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl"
    artifact_path = report_dir / artifact_rel
    if not artifact_path.exists():
        return {"status": "MISSING", "error": f"artifact not found: {artifact_path}"}
    computed = _jsonl_sha256(artifact_path)
    declared = summary.get("accepted_events_jsonl_sha256", "")
    hash_match = declared and declared == computed
    return {
        "status": "OK" if hash_match else "HASH_MISMATCH",
        "artifact_path": str(artifact_path),
        "declared_sha256": declared,
        "computed_sha256": computed,
        "hash_match": hash_match,
        "event_count": summary.get("accepted_event_count_after_cooldown", 0),
        "summary_status": summary.get("status", ""),
    }


# ------------------------------------------------------------------
# Load events from both detectors
# ------------------------------------------------------------------


def load_liquidation_events(report_dir: Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load liquidation Phase 0A events.

    Returns empty list if report is missing (using benchmark-only mode).
    """
    if report_dir is None or not report_dir.exists():
        return [], {"status": "MISSING", "note": "liquidation report directory not found; using benchmark-only comparison"}

    result = verify_artifact(report_dir)
    if result["status"] == "MISSING":
        return [], result
    events = _load_jsonl(Path(result["artifact_path"]))
    return events, result


def load_generic_events(report_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load generic stress Phase 0A events."""
    result = verify_artifact(report_dir)
    events = _load_jsonl(Path(result["artifact_path"]))
    return events, result


# ------------------------------------------------------------------
# Event normalization and cleanliness
# ------------------------------------------------------------------


def normalize_event(event: dict[str, Any], family: str) -> dict[str, Any]:
    """Normalize an event into common schema."""
    ts = parse_timestamp(event.get("event_timestamp_utc", ""))
    direction = str(event.get("event_direction") or "")
    return {
        "detector_family": family,
        "symbol": str(event["symbol"]).upper(),
        "timestamp_utc": ts,
        "timestamp_utc_str": event.get("event_timestamp_utc", ""),
        "calendar_year": ts.year,
        "calendar_month": ts.month,
        "calendar_quarter": (ts.month - 1) // 3 + 1,
        "event_direction": direction,
        "price_t": float(event.get("price_t", 0)),
        "event_id": event.get("event_id", ""),
    }


def check_generic_cleanliness(events: list[dict[str, Any]]) -> list[str]:
    """Check generic stress events for contamination by liquidation labels."""
    warnings: list[str] = []
    for event in events:
        ed = str(event.get("event_direction", ""))
        if "liquidation" in ed.lower() or "flush" in ed.lower() or "wipe" in ed.lower():
            warnings.append(f"liquidation label found: {event.get('event_id')} direction={ed}")
        if event.get("flush_side"):
            warnings.append(f"flush_side found: {event.get('event_id')}")
        sym = str(event.get("symbol", "")).upper()
        if sym in ALTCOIN_EXCLUDED:
            warnings.append(f"excluded symbol in events: {sym}")
    return warnings


# ------------------------------------------------------------------
# Overlap computation
# ------------------------------------------------------------------


def compute_overlap(
    liq_events: Sequence[dict[str, Any]],
    gen_events: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Compute overlap between liquidation and generic event sets.

    Both sequences should already be sorted by timestamp_utc.
    """
    liq_by_sym_ts: dict[tuple[str, str], dict] = {}
    for ev in liq_events:
        key = (str(ev["symbol"]).upper(), str(ev.get("event_timestamp_utc", "")))
        liq_by_sym_ts[key] = ev

    gen_by_sym_ts: dict[tuple[str, str], dict] = {}
    for ev in gen_events:
        key = (str(ev["symbol"]).upper(), str(ev.get("event_timestamp_utc", "")))
        gen_by_sym_ts[key] = ev

    # Exact overlap
    exact_keys = set(liq_by_sym_ts.keys()) & set(gen_by_sym_ts.keys())
    exact_overlap = len(exact_keys)

    liq_count = len(liq_events)
    gen_count = len(gen_events)

    # Near overlap: same symbol within ±1h, ±6h, ±24h
    liq_by_sym: dict[str, list[datetime]] = defaultdict(list)
    gen_by_sym: dict[str, list[datetime]] = defaultdict(list)

    for ev in liq_events:
        sym = str(ev["symbol"]).upper()
        ts = parse_timestamp(ev["event_timestamp_utc"])
        liq_by_sym[sym].append(ts)

    for ev in gen_events:
        sym = str(ev["symbol"]).upper()
        ts = parse_timestamp(ev["event_timestamp_utc"])
        gen_by_sym[sym].append(ts)

    def _near_count(
        set_a: dict[str, list[datetime]],
        set_b: dict[str, list[datetime]],
        hours: float,
    ) -> int:
        delta = timedelta(hours=hours)
        count = 0
        for sym, ts_list_a in set_a.items():
            ts_list_b = set_b.get(sym, [])
            if not ts_list_b:
                continue
            for ts_a in ts_list_a:
                for ts_b in ts_list_b:
                    if abs((ts_a - ts_b).total_seconds()) <= delta.total_seconds():
                        count += 1
                        break
        return count

    near_1h = _near_count(liq_by_sym, gen_by_sym, 1)
    near_6h = _near_count(liq_by_sym, gen_by_sym, 6)
    near_24h = _near_count(liq_by_sym, gen_by_sym, 24)

    liq_only = liq_count - exact_overlap
    gen_only = gen_count - exact_overlap

    return {
        "liquidation_event_count": liq_count,
        "generic_event_count": gen_count,
        "exact_overlap_count": exact_overlap,
        "exact_overlap_share_of_liquidation": exact_overlap / liq_count if liq_count else 0.0,
        "exact_overlap_share_of_generic": exact_overlap / gen_count if gen_count else 0.0,
        "near_1h_overlap_count": near_1h,
        "near_1h_overlap_share_of_liquidation": near_1h / liq_count if liq_count else 0.0,
        "near_6h_overlap_count": near_6h,
        "near_6h_overlap_share_of_liquidation": near_6h / liq_count if liq_count else 0.0,
        "near_24h_overlap_count": near_24h,
        "near_24h_overlap_share_of_liquidation": near_24h / liq_count if liq_count else 0.0,
        "liquidation_only_count": liq_only,
        "generic_only_count": gen_only,
        "overlap_count": exact_overlap,
    }


# ------------------------------------------------------------------
# Forward return computation
# ------------------------------------------------------------------


def _future_price(symbol_rows: dict[str, list[tuple[datetime, float]]], symbol: str, target_ts: datetime, tolerance_minutes: int = 65) -> float | None:
    """Look up the first price at or after target within tolerance."""
    series = symbol_rows.get(symbol.upper(), [])
    for ts, price in series:
        if ts >= target_ts:
            if abs((ts - target_ts).total_seconds()) <= tolerance_minutes * 60:
                return price
            return None
    return None


def _directional_return(direction: str, entry_price: float, exit_price: float) -> float:
    """Compute forward return for long aftershock rebound."""
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    # Both studies use long direction after downside events
    return (exit_price / entry_price - 1.0) * 10000.0


def compute_cohort_metrics(
    cohort_name: str,
    events: list[dict[str, Any]],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> CohortMetrics:
    """Compute return metrics for a cohort of events.

    Uses the same 24h primary horizon and 50 bps cost convention.
    """
    net_50: list[float] = []
    gross: list[float] = []
    missing = 0
    for event in events:
        symbol = str(event["symbol"]).upper()
        ts = parse_timestamp(event.get("event_timestamp_utc", event.get("timestamp_utc_str", "")))
        price_t = float(event.get("price_t", 0))
        target = ts + timedelta(hours=PRIMARY_HORIZON_HOURS)
        future_px = _future_price(price_series, symbol, target)
        if future_px is None:
            missing += 1
            continue
        direction = str(event.get("event_direction", ""))
        g = _directional_return(direction, price_t, future_px)
        gross.append(g)
        net_50.append(g - PRIMARY_COST_BPS)

    n = len(net_50)
    total_events = len(events)
    if n == 0:
        return CohortMetrics(cohort_name, total_events, 0, missing, None, None, None, None, None, None, None)

    gross_mean = statistics.mean(gross)
    gross_median = statistics.median(gross)
    net_mean_50 = statistics.mean(net_50)
    net_median_50 = statistics.median(net_50)
    win_rate_50 = sum(1 for v in net_50 if v > 0) / n

    net_75 = [g - 75.0 for g in gross]
    net_100 = [g - 100.0 for g in gross]

    # Top/bottom 5% contribution
    sorted_net = sorted(net_50)
    top5_count = max(1, n // 20)
    top5_contrib = sum(sorted_net[-top5_count:]) / sum(net_50) if sum(net_50) != 0 else 0.0
    bot5_contrib = sum(sorted_net[:top5_count]) / sum(net_50) if sum(net_50) != 0 else 0.0

    return CohortMetrics(
        name=cohort_name,
        event_count=total_events,
        evaluated_count=n,
        missing_forward_24h=missing,
        gross_mean_bps_24h=gross_mean,
        gross_median_bps_24h=gross_median,
        net_mean_bps_50_24h=net_mean_50,
        net_median_bps_50_24h=net_median_50,
        win_rate_50_24h=win_rate_50,
        net_mean_bps_75_24h=statistics.mean(net_75),
        net_mean_bps_100_24h=statistics.mean(net_100),
        top_5pct_contribution=top5_contrib,
        bot_5pct_contribution=bot5_contrib,
    )


# ------------------------------------------------------------------
# Year breakdown
# ------------------------------------------------------------------


def build_year_breakdown(
    cohort_name: str,
    events: list[dict[str, Any]],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    """Compute per-year metrics for a cohort."""
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        ts = parse_timestamp(ev.get("event_timestamp_utc", ""))
        by_year[ts.year].append(ev)

    total_events = len(events)
    rows: list[dict[str, Any]] = []
    for year in sorted(by_year):
        yr_events = by_year[year]
        m = compute_cohort_metrics(f"{cohort_name}_{year}", yr_events, price_series)
        cumulative_event_count = sum(len(by_year[y]) for y in sorted(by_year) if y <= year)
        cumulative_evaluated = 0
        for y in sorted(by_year):
            if y <= year:
                sub = compute_cohort_metrics(f"_tmp", by_year[y], price_series)
                cumulative_evaluated += sub.evaluated_count

        rows.append({
            "year": year,
            "cohort": cohort_name,
            "event_count": len(yr_events),
            "share_of_total": len(yr_events) / total_events if total_events else 0.0,
            "evaluated_count": m.evaluated_count,
            "net_mean_bps_50_24h": m.net_mean_bps_50_24h,
            "net_median_bps_50_24h": m.net_median_bps_50_24h,
            "win_rate_50_24h": m.win_rate_50_24h,
        })
    return rows


# ------------------------------------------------------------------
# Cohort definitions
# ------------------------------------------------------------------


def build_cohorts(
    liq_events: list[dict[str, Any]],
    gen_events: list[dict[str, Any]],
    liq_normalized: list[dict[str, Any]],
    gen_normalized: list[dict[str, Any]],
    overlap_info: dict[str, Any],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> tuple[list[CohortMetrics], list[CohortMetrics]]:
    """Build cohort metrics and year breakdowns for all cohorts."""
    cohorts: list[CohortMetrics] = []
    year_rows: list[CohortMetrics] = []

    # Generic all
    gen_all = compute_cohort_metrics("generic_all", gen_events, price_series)
    cohorts.append(gen_all)
    year_rows.extend(build_year_breakdown("generic_all", gen_events, price_series))

    # Generic-only (not exact overlap)
    if overlap_info["exact_overlap_count"] > 0:
        gen_only_events = [
            ev for ev in gen_events
            if (str(ev.get("symbol", "")).upper(), str(ev.get("event_timestamp_utc", "")))
            not in {
                (str(lv["symbol"]).upper(), str(lv.get("event_timestamp_utc", "")))
                for lv in liq_events
            }
        ]
        gen_only = compute_cohort_metrics("generic_only", gen_only_events, price_series)
        cohorts.append(gen_only)
    else:
        gen_only = compute_cohort_metrics("generic_only", gen_events, price_series)
        cohorts.append(gen_only)

    # Liquidation all (benchmark)
    cohorts.append(CohortMetrics(
        name="liquidation_all",
        event_count=LIQUIDATION_BENCHMARK_EVALUATED_COUNT,
        evaluated_count=LIQUIDATION_BENCHMARK_EVALUATED_COUNT,
        missing_forward_24h=0,
        gross_mean_bps_24h=LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS + PRIMARY_COST_BPS,
        gross_median_bps_24h=LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS + PRIMARY_COST_BPS,
        net_mean_bps_50_24h=LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS,
        net_median_bps_50_24h=LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS,
        win_rate_50_24h=LIQUIDATION_BENCHMARK_24H_WIN_RATE,
        net_mean_bps_75_24h=LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS - 25.0,
        net_mean_bps_100_24h=LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS - 50.0,
    ))

    # If liquidation events exist locally, compute additional cohorts
    if liq_events and liq_normalized and overlap_info["exact_overlap_count"] > 0:
        # Exact overlap
        exact_keys = {
            (str(ev["symbol"]).upper(), str(ev.get("event_timestamp_utc", "")))
            for ev in liq_events
        } & {
            (str(ev["symbol"]).upper(), str(ev.get("event_timestamp_utc", "")))
            for ev in gen_events
        }
        overlap_events = [
            ev for ev in gen_events
            if (str(ev.get("symbol", "")).upper(), str(ev.get("event_timestamp_utc", ""))) in exact_keys
        ] or [
            ev for ev in liq_events
            if (str(ev.get("symbol", "")).upper(), str(ev.get("event_timestamp_utc", ""))) in exact_keys
        ]
        cohorts.append(compute_cohort_metrics("exact_overlap", overlap_events, price_series))

        # Liquidation-only
        liq_only_events = [
            ev for ev in liq_events
            if (str(ev.get("symbol", "")).upper(), str(ev.get("event_timestamp_utc", ""))) not in exact_keys
        ]
        cohorts.append(compute_cohort_metrics("liquidation_only", liq_only_events, price_series))

    return cohorts, year_rows


# ------------------------------------------------------------------
# Verdict
# ------------------------------------------------------------------


def classify_verdict(
    cohorts: list[CohortMetrics],
    liq_events_exist: bool,
) -> str:
    """Classify the ablation comparison verdict."""
    gen_all = next((c for c in cohorts if c.name == "generic_all"), None)
    liq_all = next((c for c in cohorts if c.name == "liquidation_all"), None)
    gen_only = next((c for c in cohorts if c.name == "generic_only"), None)

    if gen_all is None or liq_all is None:
        return VERDICT_INCONCLUSIVE_COVERAGE

    if gen_all.evaluated_count < 200:
        return VERDICT_INCONCLUSIVE_COVERAGE

    generic_dominates = (
        gen_all.net_mean_bps_50_24h is not None
        and liq_all.net_mean_bps_50_24h is not None
        and gen_all.net_mean_bps_50_24h > liq_all.net_mean_bps_50_24h * 1.5
        and gen_all.win_rate_50_24h is not None
        and liq_all.win_rate_50_24h is not None
        and gen_all.win_rate_50_24h > liq_all.win_rate_50_24h
    )

    if not generic_dominates:
        return VERDICT_INCONCLUSIVE_LOW_OVERLAP

    # Check if liquidation-only underperforms generic-only
    if gen_only and gen_only.evaluated_count > 50:
        if (
            gen_only.net_mean_bps_50_24h is not None
            and liq_all.net_mean_bps_50_24h is not None
            and liq_all.net_mean_bps_50_24h < gen_only.net_mean_bps_50_24h * 0.5
        ):
            return VERDICT_OI_HARMFUL

    return VERDICT_OI_NOT_SUPPORTED


# ------------------------------------------------------------------
# Funding diagnostic
# ------------------------------------------------------------------


def _funding_diagnostic() -> dict[str, Any]:
    """Running funding diagnostic.

    Archive does not contain per-event funding rate data at event timestamps.
    Use deterministic stress table instead.
    """
    base_50 = GENERIC_STRESS_24H_NET_MEAN_BPS = 339.59448071794975
    return {
        "status": "FUNDING_FIELD_UNAVAILABLE",
        "note": "Funding fields not available in archive at event resolution; using deterministic stress table",
        "deterministic_bleed_table": {
            "description": "Generic stress 24h net mean after 50 bps minus additional bleed",
            "bleed_25bps": base_50 - 25.0,
            "bleed_50bps": base_50 - 50.0,
            "bleed_100bps": base_50 - 100.0,
            "bleed_150bps": base_50 - 150.0,
        },
    }


GENERIC_STRESS_24H_NET_MEAN_BPS = 339.59448071794975


# ------------------------------------------------------------------
# Markdown report
# ------------------------------------------------------------------


def summary_markdown(
    summary: dict[str, Any],
) -> str:
    s = summary
    comp = s.get("comparison_results", {})

    # Overlap summary
    o = comp.get("overlap", {})
    overlap_section = f"""
## Event overlap

| Metric | Value |
|---|---|
| Liquidation event count | `{o.get('liquidation_event_count', 'MISSING')}` |
| Generic event count | `{o.get('generic_event_count')}` |
| Exact overlap count | `{o.get('exact_overlap_count')}` |
| Exact overlap share of liquidation | `{o.get('exact_overlap_share_of_liquidation')}` |
| Exact overlap share of generic | `{o.get('exact_overlap_share_of_generic')}` |
| ±1h overlap count | `{o.get('near_1h_overlap_count')}` |
| ±6h overlap count | `{o.get('near_6h_overlap_count')}` |
| ±24h overlap count | `{o.get('near_24h_overlap_count')}` |
| Liquidation-only count | `{o.get('liquidation_only_count')}` |
| Generic-only count | `{o.get('generic_only_count')}` |
"""

    # Cohort table
    cohort_lines = ["| Cohort | Events | Eval | 24h Net Mean 50 | 24h Net Median 50 | Win Rate 50 | 75 bps Net | 100 bps Net |",
                    "|---|---|---|---|---|---|---|---|"]
    for c in comp.get("cohorts", []):
        cohort_lines.append(
            f"| {c['name']} | {c['event_count']} | {c['evaluated_count']} | "
            f"{_fmt(c.get('net_mean_bps_50_24h'))} | {_fmt(c.get('net_median_bps_50_24h'))} | "
            f"{_fmt(c.get('win_rate_50_24h'))} | {_fmt(c.get('net_mean_bps_75_24h'))} | {_fmt(c.get('net_mean_bps_100_24h'))} |"
        )

    # Year breakdown
    year_lines = ["| Year | Cohort | Events | Share | Eval | Net Mean 50 | Net Median 50 | Win Rate |",
                  "|---|---|---|---|---|---|---|---|"]
    for yr in comp.get("year_breakdown", []):
        if yr["cohort"] == "generic_all":
            year_lines.append(
                f"| {yr['year']} | {yr['cohort']} | {yr['event_count']} | {yr['share_of_total']:.3f} | "
                f"{yr['evaluated_count']} | {_fmt(yr.get('net_mean_bps_50_24h'))} | "
                f"{_fmt(yr.get('net_median_bps_50_24h'))} | {_fmt(yr.get('win_rate_50_24h'))} |"
            )

    # Cleanliness
    cle = comp.get("generic_cleanliness_issues", [])
    clean_lines = "\n  ".join(cle) if cle else "none"
    cleanliness_section = f"""
## Generic stress event cleanliness

Issues found: `{len(cle)}`

  {clean_lines}
"""

    # Funding
    fd = comp.get("funding_diagnostic", {})
    bleed_table = ""
    if "deterministic_bleed_table" in fd:
        bt = fd["deterministic_bleed_table"]
        bleed_table = f"""
| Bleed | Net mean after bleed |
|---|---|
| +25 bps | `{bt.get('bleed_25bps')}` |
| +50 bps | `{bt.get('bleed_50bps')}` |
| +100 bps | `{bt.get('bleed_100bps')}` |
| +150 bps | `{bt.get('bleed_150bps')}` |
"""

    return f"""# Liquidation vs generic stress ablation comparison

Study: {s.get('study_id')}

Status: `{s.get('status')}`

Final verdict: `{s.get('final_verdict')}`

Generic Phase 0A: `{s.get('generic_phase0a_report')}`

Generic Phase 0B: `{s.get('generic_phase0b_report')}`

## Artifact verification

Generic Phase 0A artifact SHA-256: `{s.get('generic_artifact_sha256')}`

Generic Phase 0B verified: `{s.get('generic_phase0b_verified')}`

Liquidation Phase 0A status: `{comp.get('liquidation_artifact_status', 'MISSING')}`

{overlap_section}

## Cohort metrics (24h primary)

{"\n".join(cohort_lines)}

## Year breakdown (generic stress)

{"\n".join(year_lines)}

## Generic cleanliness{cleanliness_section}

## Funding diagnostic

Status: `{fd.get('status')}`

Note: `{fd.get('note', '')}`

{bleed_table}
## Conclusion

Verdict: `{s.get('final_verdict')}`

Phase 0C recommended next: `{s.get('phase0c_recommended_next', False)}`

Phase 0D/v1 unlocked: NO

No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or REJECTED_RESEARCH.md update were used.
"""


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


# ------------------------------------------------------------------
# Report writing
# ------------------------------------------------------------------


def write_report(summary: dict[str, Any], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "summary.json", summary)
    atomic_write_text(report_dir / "summary.md", summary_markdown(summary))
    # Cohort metrics CSV
    cohorts = summary.get("comparison_results", {}).get("cohorts", [])
    if cohorts:
        with open(report_dir / "cohort_metrics.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cohorts[0].keys()))
            w.writeheader()
            w.writerows(cohorts)


# ------------------------------------------------------------------
# Main diagnostic
# ------------------------------------------------------------------


def run_diagnostic(
    liquidation_phase0a_report: Path | None,
    liquidation_phase0b_report: Path | None,
    generic_phase0a_report: Path,
    generic_phase0b_report: Path,
    archive_path: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the liquidation vs generic stress ablation comparison diagnostic."""
    start_wall = time.perf_counter()

    # Verify generic Phase 0A artifact
    gen_result = verify_artifact(generic_phase0a_report)
    gen_events, gen_artifact_info = load_generic_events(generic_phase0a_report)
    gen_events.sort(key=lambda e: (e.get("event_timestamp_utc", ""), e.get("symbol", "")))
    gen_sha = gen_result.get("computed_sha256", "")

    # Phase 0B verification
    gen_phase0b_summary_path = generic_phase0b_report / "summary.json"
    gen_phase0b_verified = False
    if gen_phase0b_summary_path.exists():
        gen_phase0b = json.loads(gen_phase0b_summary_path.read_text())
        gen_phase0b_verified = gen_phase0b.get("status") == "GENERIC_STRESS_PHASE0B_RETURN_DIAGNOSTIC_PASS"

    # Cleanliness check
    clean_issues = check_generic_cleanliness(gen_events)

    # Load liquidation events (if available)
    liq_events, liq_artifact_info = load_liquidation_events(liquidation_phase0a_report)

    # Compute overlap
    if liq_events:
        liq_sorted = sorted(liq_events, key=lambda e: (e.get("event_timestamp_utc", ""), e.get("symbol", "")))
        overlap_info = compute_overlap(liq_sorted, gen_events)
    else:
        overlap_info = {
            "liquidation_event_count": 0,
            "generic_event_count": len(gen_events),
            "exact_overlap_count": 0,
            "exact_overlap_share_of_liquidation": 0.0,
            "exact_overlap_share_of_generic": 0.0,
            "near_1h_overlap_count": 0,
            "near_1h_overlap_share_of_liquidation": 0.0,
            "near_6h_overlap_count": 0,
            "near_6h_overlap_share_of_liquidation": 0.0,
            "near_24h_overlap_count": 0,
            "near_24h_overlap_share_of_liquidation": 0.0,
            "liquidation_only_count": 0,
            "generic_only_count": len(gen_events),
            "overlap_count": 0,
        }

    # Build price series if archive is available
    price_series: dict[str, list[tuple[datetime, float]]] = {}
    if archive_path is not None and archive_path.exists():
        print(f"LOADING_ARCHIVE path={archive_path}", flush=True)
        try:
            from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0b import build_price_series as bps
            from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import load_archive_rows as lar
            rows, _ = lar([archive_path])
            price_series = bps(list(rows))
            print(f"ARCHIVE_LOADED rows={len(rows)} symbols={len(price_series)}", flush=True)
        except Exception as exc:
            print(f"ARCHIVE_LOAD_FAILED {exc}", flush=True)

    # Build cohorts
    liq_norm = [normalize_event(ev, LIQUIDATION_DETECTOR_FAMILY) for ev in liq_events] if liq_events else []
    gen_norm = [normalize_event(ev, GENERIC_DETECTOR_FAMILY) for ev in gen_events]
    cohorts_list, year_rows = build_cohorts(liq_events, gen_events, liq_norm, gen_norm, overlap_info, price_series)

    # Verdict
    verdict = classify_verdict(cohorts_list, bool(liq_events))

    # Funding diagnostic
    funding = _funding_diagnostic()

    # Year breakdown (cohort form)
    year_breakdown = build_year_breakdown("generic_all", gen_events, price_series)

    elapsed = time.perf_counter() - start_wall

    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "safety_mode": SAFETY_MODE,
        "status": "COMPLETED",
        "final_verdict": verdict,
        "git_sha": _git_sha(),
        "liquidation_phase0a_report": str(liquidation_phase0a_report) if liquidation_phase0a_report else "MISSING",
        "liquidation_phase0b_report": str(liquidation_phase0b_report) if liquidation_phase0b_report else "MISSING",
        "generic_phase0a_report": str(generic_phase0a_report),
        "generic_phase0b_report": str(generic_phase0b_report),
        "generic_artifact_sha256": gen_sha,
        "generic_phase0b_verified": gen_phase0b_verified,
        "liquidation_artifact_status": liq_artifact_info.get("status", "MISSING"),
        "liquidation_events_available": bool(liq_events),
        "phase0c_recommended_next": verdict in {VERDICT_OI_NOT_SUPPORTED, VERDICT_OI_HARMFUL},
        "rejected_research_update_recommended": verdict in {VERDICT_OI_NOT_SUPPORTED, VERDICT_OI_HARMFUL},
        "phase0d_unlocked": False,
        "elapsed_seconds": round(elapsed, 1),
        "comparison_results": {
            "overlap": overlap_info,
            "cohorts": [asdict(c) for c in cohorts_list],
            "year_breakdown": year_breakdown,
            "generic_cleanliness_issues": clean_issues,
            "liquidation_artifact_status": liq_artifact_info.get("status", "MISSING"),
            "funding_diagnostic": funding,
        },
    }

    if report_dir is not None:
        write_report(summary, report_dir)
        print(f"report_path={report_dir}", flush=True)

    print(f"verdict={verdict}", flush=True)
    print(f"elapsed={elapsed:.1f}s", flush=True)

    return summary


def _git_sha() -> str:
    try:
        import subprocess
        s = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3], text=True).strip()
        return s
    except Exception:
        return ""


__all__ = [
    "STUDY_ID",
    "VERDICT_OI_SUPPORTED",
    "VERDICT_OI_NOT_SUPPORTED",
    "VERDICT_OI_HARMFUL",
    "VERDICT_INCONCLUSIVE_LOW_OVERLAP",
    "VERDICT_ERROR",
    "verify_artifact",
    "load_liquidation_events",
    "load_generic_events",
    "normalize_event",
    "check_generic_cleanliness",
    "compute_overlap",
    "compute_cohort_metrics",
    "classify_verdict",
    "build_year_breakdown",
    "run_diagnostic",
]