"""Venue-age-aware Phase 0B return diagnostic for liquidation flush aftershock reversal."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Sequence

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    EventRecord,
    LoadDiagnostics,
    _candidate_archive_files,
    compute_precommitment_hash,
    load_archive_rows,
    normalize_raw_row,
    parse_timestamp,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import atomic_write_json, atomic_write_text

STATUS_READY_FOR_EVALUATION = "PHASE0B_READY_FOR_EVALUATION"
STATUS_EVENT_REPRODUCTION_MISMATCH = "PHASE0B_EVENT_REPRODUCTION_MISMATCH"
STATUS_INVALID_PRECOMMITMENT = "PHASE0B_INVALID_PRECOMMITMENT"
STATUS_INSUFFICIENT_FORWARD_COVERAGE = "PHASE0B_INSUFFICIENT_FORWARD_COVERAGE"
STATUS_REJECTED_COST_WALL = "PHASE0B_REJECTED_COST_WALL"
STATUS_REJECTED_NO_REVERSAL = "PHASE0B_REJECTED_NO_REVERSAL"
STATUS_RETURN_DIAGNOSTIC_PASS = "PHASE0B_RETURN_DIAGNOSTIC_PASS"
STATUS_RETURN_DIAGNOSTIC_NO_NULL = "PHASE0B_RETURN_DIAGNOSTIC_NO_NULL"
STATUS_ERROR = "PHASE0B_ERROR"

ALTCOIN_EXCLUDED_SYMBOLS = {"BTC", "ETH"}
HORIZONS_HOURS = (6, 12, 24, 48)
PRIMARY_HORIZON_HOURS = 24
PRIMARY_COST_BPS = 50.0
SECONDARY_COST_BPS = 25.0
MIN_EFFECTIVE_EVENTS = 200
PHASE0A_REQUIRED_STATUS = "PHASE0A_EVENT_POPULATION_READY"
PHASE0A_REQUIRED_PRECOMMITMENT_HASH = None
PHASE0A_REQUIRED_EVENT_COUNT = 0


class Phase0BError(Exception):
    pass


class EventReproductionMismatch(Phase0BError):
    pass


@dataclass(frozen=True)
class HorizonEvaluation:
    event_id: str
    symbol: str
    event_timestamp_utc: str
    event_direction: str
    horizon_hours: int
    event_price: float
    future_price: float | None
    gross_return_bps: float | None
    net_return_bps_50bps: float | None
    net_return_bps_25bps: float | None
    missing_forward: bool


@dataclass(frozen=True)
class HorizonMetrics:
    horizon_hours: int
    evaluated_event_count: int
    gross_mean_bps: float | None
    gross_median_bps: float | None
    net_mean_bps_50bps: float | None
    net_median_bps_50bps: float | None
    win_rate_50bps: float | None
    mean_lcb_95_bps_50bps: float | None
    missing_forward_count: int


@dataclass
class Phase0BResult:
    summary: dict[str, Any]
    horizon_metrics: list[HorizonMetrics]
    evaluations: list[HorizonEvaluation]
    report_dir: str | None = None


def precommitment_recorded_and_computed(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    recorded = ""
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("Precommitment SHA-256 (self):"):
            recorded = line.split(":", 1)[1].strip()
            break
    return recorded, compute_precommitment_hash(path)


def phase0a_event_to_json(event: EventRecord) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "symbol": event.symbol,
        "event_timestamp_utc": event.event_timestamp_utc,
        "event_direction": "downside_liquidation_flush",
        "flush_side": "long_wipe",
        "price_t": event.price_t,
        "price_t_minus_8h": event.price_t_minus_8h,
        "oi_t": event.oi_t,
        "oi_t_minus_8h": event.oi_t_minus_8h,
        "price_return_8h_pct": event.price_return_8h_pct,
        "oi_change_8h_pct": event.oi_change_8h_pct,
        "cooldown_group_index": event.cooldown_group_index,
        "calendar_year": event.calendar_year,
    }


def _jsonl_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate_event_universe(events: Sequence[dict[str, Any]]) -> None:
    bad = sorted({str(event.get("symbol", "")).upper() for event in events if str(event.get("symbol", "")).upper() in ALTCOIN_EXCLUDED_SYMBOLS})
    if bad:
        raise EventReproductionMismatch(f"BTC/ETH events included in Phase 0A artifact: {bad}")


def load_phase0a_event_artifact(artifact_path: Path, phase0a_summary: dict[str, Any]) -> list[dict[str, Any]]:
    if not artifact_path.exists():
        raise EventReproductionMismatch(f"accepted event artifact missing: {artifact_path}")
    events = _load_jsonl(artifact_path)
    expected_count = int(phase0a_summary.get("accepted_event_count_after_cooldown", -1))
    if len(events) != expected_count:
        raise EventReproductionMismatch(f"event artifact count {len(events)} != Phase 0A summary count {expected_count}")
    expected_hash = phase0a_summary.get("accepted_events_jsonl_sha256")
    if expected_hash and _jsonl_sha256(artifact_path) != expected_hash:
        raise EventReproductionMismatch("event artifact SHA-256 does not match Phase 0A summary")
    validate_event_universe(events)
    return sorted(events, key=lambda e: (e["event_timestamp_utc"], e["symbol"], e.get("event_id", "")))


def load_phase0a_report(report_dir: Path) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    summary_path = report_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    if summary.get("status") != PHASE0A_REQUIRED_STATUS or summary.get("unlocks_phase0b") is not True:
        raise EventReproductionMismatch("Phase 0A report is not unlocked")
    if PHASE0A_REQUIRED_PRECOMMITMENT_HASH is not None and summary.get("precommitment_sha256") != PHASE0A_REQUIRED_PRECOMMITMENT_HASH:
        raise EventReproductionMismatch("Phase 0A precommitment hash mismatch")
    artifact_rel = summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl"
    artifact_path = report_dir / artifact_rel
    events = load_phase0a_event_artifact(artifact_path, summary)
    if PHASE0A_REQUIRED_EVENT_COUNT > 0 and len(events) != PHASE0A_REQUIRED_EVENT_COUNT:
        raise EventReproductionMismatch(f"Phase 0A accepted event count {len(events)} != frozen required count {PHASE0A_REQUIRED_EVENT_COUNT}")
    return summary, artifact_path, events


def compute_directional_return_bps(event_direction: str, event_price: float, future_price: float) -> float:
    if event_price <= 0 or future_price <= 0:
        raise ValueError("prices must be positive")
    if event_direction in {"downside_liquidation_flush", "long_wipe"}:
        return (future_price / event_price - 1.0) * 10000.0
    if event_direction in {"upside_liquidation_flush", "short_squeeze"}:
        return (event_price / future_price - 1.0) * 10000.0
    raise ValueError(f"ambiguous event direction: {event_direction}")


def net_bps_after_cost(gross_bps: float, cost_bps: float) -> float:
    return gross_bps - cost_bps


def build_price_series(rows: Sequence[ArchiveRow]) -> dict[str, list[tuple[datetime, float]]]:
    series: dict[str, list[tuple[datetime, float]]] = {}
    for row in rows:
        sym = row.symbol.upper()
        if sym in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        series.setdefault(sym, []).append((row.timestamp, row.price))
    for sym in series:
        series[sym].sort(key=lambda x: x[0])
    return series


def lookup_future_price(series: Sequence[tuple[datetime, float]], target: datetime) -> float | None:
    if not series:
        return None
    timestamps = [ts for ts, _ in series]
    idx = bisect_left(timestamps, target)
    if idx >= len(series):
        return None
    ts, price = series[idx]
    if abs((ts - target).total_seconds()) > 65 * 60:
        return None
    return price


def _mean_lcb_95(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    m = mean(values)
    var = sum((x - m) ** 2 for x in values) / (n - 1)
    return m - 1.96 * math.sqrt(var / n)


def _metrics_for_horizon(horizon: int, evaluations: Sequence[HorizonEvaluation]) -> HorizonMetrics:
    vals = [e for e in evaluations if e.horizon_hours == horizon]
    valid = [e for e in vals if e.gross_return_bps is not None and e.net_return_bps_50bps is not None]
    missing = sum(1 for e in vals if e.missing_forward)
    gross = [float(e.gross_return_bps) for e in valid]
    net50 = [float(e.net_return_bps_50bps) for e in valid]
    return HorizonMetrics(
        horizon_hours=horizon,
        evaluated_event_count=len(valid),
        gross_mean_bps=mean(gross) if gross else None,
        gross_median_bps=median(gross) if gross else None,
        net_mean_bps_50bps=mean(net50) if net50 else None,
        net_median_bps_50bps=median(net50) if net50 else None,
        win_rate_50bps=sum(1 for x in net50 if x > 0) / len(net50) if net50 else None,
        mean_lcb_95_bps_50bps=_mean_lcb_95(net50),
        missing_forward_count=missing,
    )


def classify_primary_verdict(primary: HorizonMetrics, null_run: bool) -> str:
    if primary.evaluated_event_count < MIN_EFFECTIVE_EVENTS:
        return STATUS_INSUFFICIENT_FORWARD_COVERAGE
    if primary.net_mean_bps_50bps is None or primary.net_mean_bps_50bps <= 0:
        return STATUS_REJECTED_COST_WALL
    if primary.net_median_bps_50bps is None or primary.net_median_bps_50bps <= 0:
        return STATUS_REJECTED_NO_REVERSAL
    if primary.win_rate_50bps is None or primary.win_rate_50bps <= 0.50:
        return STATUS_REJECTED_NO_REVERSAL
    if primary.net_mean_bps_50bps <= 10 or primary.win_rate_50bps <= 0.53:
        return STATUS_REJECTED_COST_WALL
    if primary.mean_lcb_95_bps_50bps is None or primary.mean_lcb_95_bps_50bps <= 0:
        return STATUS_REJECTED_COST_WALL
    return STATUS_RETURN_DIAGNOSTIC_PASS if null_run else STATUS_RETURN_DIAGNOSTIC_NO_NULL


def build_summary_distribution(events: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    months: dict[str, int] = {}
    quarters: dict[str, int] = {}
    for event in events:
        ts = parse_timestamp(event["event_timestamp_utc"])
        month = f"{ts.year:04d}-{ts.month:02d}"
        quarter = f"{ts.year}Q{((ts.month - 1) // 3) + 1}"
        months[month] = months.get(month, 0) + 1
        quarters[quarter] = quarters.get(quarter, 0) + 1
    return {"event_month_distribution": dict(sorted(months.items())), "event_quarter_distribution": dict(sorted(quarters.items()))}


def evaluate_events(events: Sequence[dict[str, Any]], price_series: dict[str, list[tuple[datetime, float]]]) -> tuple[list[HorizonEvaluation], list[HorizonMetrics], int]:
    evaluations: list[HorizonEvaluation] = []
    excluded = 0
    for event in events:
        symbol = str(event["symbol"]).upper()
        direction = str(event.get("event_direction") or event.get("flush_side") or "")
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            excluded += 1
            continue
        if direction not in {"downside_liquidation_flush", "long_wipe", "upside_liquidation_flush", "short_squeeze"}:
            excluded += 1
            continue
        event_ts = parse_timestamp(event["event_timestamp_utc"])
        event_price = float(event["price_t"])
        series = price_series.get(symbol, [])
        for horizon in HORIZONS_HOURS:
            future_price = lookup_future_price(series, event_ts + timedelta(hours=horizon))
            if future_price is None:
                evaluations.append(HorizonEvaluation(str(event.get("event_id", "")), symbol, event["event_timestamp_utc"], direction, horizon, event_price, None, None, None, None, True))
            else:
                gross = compute_directional_return_bps(direction, event_price, future_price)
                evaluations.append(HorizonEvaluation(str(event.get("event_id", "")), symbol, event["event_timestamp_utc"], direction, horizon, event_price, future_price, gross, net_bps_after_cost(gross, PRIMARY_COST_BPS), net_bps_after_cost(gross, SECONDARY_COST_BPS), False))
    metrics = [_metrics_for_horizon(h, evaluations) for h in HORIZONS_HOURS]
    return evaluations, metrics, excluded


def run_phase0b(
    phase0a_report: Path,
    archive_paths: Sequence[Path],
    precommitment_path: Path,
    report_dir: Path | None = None,
) -> Phase0BResult:
    recorded, computed = precommitment_recorded_and_computed(precommitment_path)
    if not recorded or recorded != computed:
        summary = {"status": STATUS_INVALID_PRECOMMITMENT, "precommitment_sha256": computed, "unlocks_phase0c": False}
        return Phase0BResult(summary, [], [])
    try:
        phase0a_summary, event_artifact, events = load_phase0a_report(phase0a_report)
    except EventReproductionMismatch as exc:
        summary = {"status": STATUS_EVENT_REPRODUCTION_MISMATCH, "phase0b_final_verdict": STATUS_EVENT_REPRODUCTION_MISMATCH, "phase0b_locked_reason": str(exc), "precommitment_sha256": computed, "unlocks_phase0c": False}
        return Phase0BResult(summary, [], [])
    rows, diagnostics = load_archive_rows(archive_paths)
    price_series = build_price_series(rows)
    evaluations, metrics, excluded_count = evaluate_events(events, price_series)
    primary = next(m for m in metrics if m.horizon_hours == PRIMARY_HORIZON_HOURS)
    status = classify_primary_verdict(primary, null_run=False)
    final_verdict = status
    missing_by_horizon = {str(m.horizon_hours): m.missing_forward_count for m in metrics}
    summary = {
        "status": status,
        "phase0b_final_verdict": final_verdict,
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
        "secondary_cost_bps": SECONDARY_COST_BPS,
        "null_run": False,
        "null_result": None,
        "archive_source_path": ":".join(str(p) for p in archive_paths),
        "archive_rows_loaded": diagnostics.loaded_rows,
        "altcoin_only": True,
        "btc_eth_excluded": True,
        **build_summary_distribution(events),
        "unlocks_phase0c": status in {STATUS_RETURN_DIAGNOSTIC_PASS, STATUS_RETURN_DIAGNOSTIC_NO_NULL},
    }
    result = Phase0BResult(summary, metrics, evaluations)
    if report_dir is not None:
        write_report_artifacts(result, report_dir)
    return result


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summary_markdown(result: Phase0BResult) -> str:
    s = result.summary
    primary = next((m for m in s.get("horizon_metrics", []) if m.get("horizon_hours") == PRIMARY_HORIZON_HOURS), {})
    return f"""# Liquidation flush aftershock reversal venue-age-aware Phase 0B

Status: `{s.get('status')}`

Final verdict: `{s.get('phase0b_final_verdict')}`

Phase 0A report: `{s.get('phase0a_report_path')}`

Phase 0A event artifact SHA-256: `{s.get('phase0a_event_artifact_sha256')}`

Primary 24h net mean bps after 50 bps cost: `{primary.get('net_mean_bps_50bps')}`

Primary 24h net median bps after 50 bps cost: `{primary.get('net_median_bps_50bps')}`

Primary 24h win rate after 50 bps cost: `{primary.get('win_rate_50bps')}`

Null run: `False`

No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or REJECTED_RESEARCH.md update were used.
"""


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
