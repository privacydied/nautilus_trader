"""Independent forward-return recomputation for generic stress audited events.

Recomputes 24h net returns from accepted_events.jsonl using only the price
archive, without reusing any existing Phase 0B forward-return implementation.

Uses existing shared archive-loading infrastructure (load_archive_rows, build_price_series)
to load data, but recomputes returns through an independent code path.

No orders. No private keys. No trading auth. No live execution.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from bisect import bisect_left
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import orjson

    def _loads_json_line(line: bytes | str) -> dict[str, Any]:
        if isinstance(line, bytes):
            return orjson.loads(line)
        return orjson.loads(line.encode("utf-8"))

    HAS_ORJSON = True
except ImportError:
    import json as _json

    def _loads_json_line(line: bytes | str) -> dict[str, Any]:
        if isinstance(line, bytes):
            return _json.loads(line.decode("utf-8"))
        return _json.loads(line)

    HAS_ORJSON = False

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

STUDY_ID = "generic_altcoin_stress_regime_ablation_phase0"
ALTCOIN_EXCLUDED_SYMBOLS = frozenset({"BTC", "ETH"})
GENERIC_EVENT_DIRECTION = "downside_price_drop"
GENERIC_TRADE_DIRECTION = "long"
PRIMARY_HORIZON_HOURS = 24
PRIMARY_COST_BPS = 50.0
STRESS_COSTS_BPS = (75.0, 100.0)
TOLERANCE_SECONDS = 65 * 60  # 65 minutes tolerance for exit price


# ------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------


@dataclass
class IndependentEventResult:
    event_id: str
    symbol: str
    event_timestamp_utc: str
    direction: str
    entry_price: float
    exit_price_24h: float | None
    gross_return_bps_24h: float | None
    net_return_bps_50: float | None
    missing_forward: bool = False


@dataclass
class IndependentAuditResult:
    event_count: int
    evaluated_count: int
    missing_forward_count: int
    gross_mean_bps_24h: float | None
    gross_median_bps_24h: float | None
    net_mean_bps_50: float | None
    net_median_bps_50: float | None
    win_rate_50: float | None
    net_mean_bps_75: float | None
    net_mean_bps_100: float | None
    results: list[IndependentEventResult] = field(default_factory=list)


@dataclass
class NegativeControlResult:
    name: str
    event_count: int
    evaluated_count: int
    gross_mean_bps_24h: float | None
    gross_median_bps_24h: float | None
    net_mean_bps_50: float | None
    net_median_bps_50: float | None
    win_rate_50: float | None
    net_mean_bps_75: float | None
    net_mean_bps_100: float | None
    seed: int | None = None
    description: str = ""


# ------------------------------------------------------------------
# Timestamp helpers
# ------------------------------------------------------------------


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)
    s = str(value).replace("Z", "+00:00").replace("z", "+00:00")
    return datetime.fromisoformat(s).astimezone(UTC)


# ------------------------------------------------------------------
# Price loading via existing infrastructure
# ------------------------------------------------------------------


def load_accepted_events(event_artifact_path: Path) -> list[dict[str, Any]]:
    """Load accepted events from a Phase 0A artifact JSONL file."""
    if not event_artifact_path.exists():
        return []
    events: list[dict[str, Any]] = []
    with event_artifact_path.open("rb") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(_loads_json_line(stripped))
            except Exception:
                continue
    return events


def lookup_price(series: list[tuple[datetime, float]], target: datetime) -> float | None:
    """First price at or after target, within tolerance."""
    if not series:
        return None
    timestamps = [ts for ts, _ in series]
    idx = bisect_left(timestamps, target)
    if idx >= len(series):
        return None
    ts, price = series[idx]
    if abs((ts - target).total_seconds()) > TOLERANCE_SECONDS:
        return None
    return price


# ------------------------------------------------------------------
# Independent return computation (uses pre-loaded price series)
# ------------------------------------------------------------------


def compute_like_long_return(entry_price: float, exit_price: float) -> float:
    """Compute forward return for long trade direction (mean reversion after downside stress)."""
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError("prices must be positive")
    return (exit_price / entry_price - 1.0) * 10000.0


def evaluate_independent_return(
    event: dict[str, Any],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> IndependentEventResult:
    """Evaluate a single event's 24h forward return independently."""
    symbol = str(event.get("symbol", "")).upper()
    event_ts = _parse_timestamp(event.get("event_timestamp_utc", ""))
    entry_price = float(event.get("price_t", 0))
    direction = str(event.get("event_direction", ""))

    if direction != GENERIC_EVENT_DIRECTION:
        return IndependentEventResult(
            event_id=str(event.get("event_id", "")),
            symbol=symbol,
            event_timestamp_utc=str(event.get("event_timestamp_utc", "")),
            direction=direction,
            entry_price=entry_price,
            exit_price_24h=None,
            gross_return_bps_24h=None,
            net_return_bps_50=None,
            missing_forward=False,
        )

    if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
        return IndependentEventResult(
            event_id=str(event.get("event_id", "")),
            symbol=symbol,
            event_timestamp_utc=str(event.get("event_timestamp_utc", "")),
            direction=direction,
            entry_price=entry_price,
            exit_price_24h=None,
            gross_return_bps_24h=None,
            net_return_bps_50=None,
            missing_forward=False,
        )

    series = price_series.get(symbol, [])
    target = event_ts + timedelta(hours=PRIMARY_HORIZON_HOURS)
    exit_price = lookup_price(series, target)

    if exit_price is None:
        return IndependentEventResult(
            event_id=str(event.get("event_id", "")),
            symbol=symbol,
            event_timestamp_utc=str(event.get("event_timestamp_utc", "")),
            direction=direction,
            entry_price=entry_price,
            exit_price_24h=None,
            gross_return_bps_24h=None,
            net_return_bps_50=None,
            missing_forward=True,
        )

    gross = compute_like_long_return(entry_price, exit_price)
    net_50 = gross - PRIMARY_COST_BPS

    return IndependentEventResult(
        event_id=str(event.get("event_id", "")),
        symbol=symbol,
        event_timestamp_utc=str(event.get("event_timestamp_utc", "")),
        direction=direction,
        entry_price=entry_price,
        exit_price_24h=exit_price,
        gross_return_bps_24h=gross,
        net_return_bps_50=net_50,
        missing_forward=False,
    )


def run_independent_audit(
    events: list[dict[str, Any]],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> IndependentAuditResult:
    """Recompute 24h returns independently for all events."""
    results: list[IndependentEventResult] = []
    for event in events:
        result = evaluate_independent_return(event, price_series)
        results.append(result)

    evaluated = [r for r in results if not r.missing_forward and r.gross_return_bps_24h is not None]
    missing = sum(1 for r in results if r.missing_forward)

    if not evaluated:
        return IndependentAuditResult(
            event_count=len(results),
            evaluated_count=0,
            missing_forward_count=missing,
            gross_mean_bps_24h=None,
            gross_median_bps_24h=None,
            net_mean_bps_50=None,
            net_median_bps_50=None,
            win_rate_50=None,
            net_mean_bps_75=None,
            net_mean_bps_100=None,
            results=results,
        )

    gross_vals = [r.gross_return_bps_24h for r in evaluated if r.gross_return_bps_24h is not None]
    net_50 = [r.net_return_bps_50 for r in evaluated if r.net_return_bps_50 is not None]

    if not gross_vals or not net_50:
        return IndependentAuditResult(
            event_count=len(results),
            evaluated_count=0,
            missing_forward_count=missing,
            gross_mean_bps_24h=None,
            gross_median_bps_24h=None,
            net_mean_bps_50=None,
            net_median_bps_50=None,
            win_rate_50=None,
            net_mean_bps_75=None,
            net_mean_bps_100=None,
            results=results,
        )

    gross_mean = statistics.mean(gross_vals)
    gross_median = statistics.median(gross_vals)
    net_mean_50 = statistics.mean(net_50)
    net_median_50 = statistics.median(net_50)
    win_rate_50 = sum(1 for v in net_50 if v > 0) / len(net_50)

    net_75 = [g - 75.0 for g in gross_vals]
    net_100 = [g - 100.0 for g in gross_vals]

    return IndependentAuditResult(
        event_count=len(results),
        evaluated_count=len(evaluated),
        missing_forward_count=missing,
        gross_mean_bps_24h=gross_mean,
        gross_median_bps_24h=gross_median,
        net_mean_bps_50=net_mean_50,
        net_median_bps_50=net_median_50,
        win_rate_50=win_rate_50,
        net_mean_bps_75=statistics.mean(net_75),
        net_mean_bps_100=statistics.mean(net_100),
        results=results,
    )


# ------------------------------------------------------------------
# Negative controls (accept pre-loaded price series)
# ------------------------------------------------------------------


def _apply_cooldown(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """48h per-symbol cooldown (greedy from earliest)."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        by_symbol.setdefault(str(c.get("cooldown_key", c.get("symbol", ""))), []).append(c)

    accepted: list[dict[str, Any]] = []
    for sym, pts in by_symbol.items():
        sorted_pts = sorted(pts, key=lambda p: _parse_timestamp(p.get("event_timestamp_utc", "")))
        cooldown_until: datetime | None = None
        for p in sorted_pts:
            ts = _parse_timestamp(p.get("event_timestamp_utc", ""))
            if cooldown_until is not None and ts < cooldown_until:
                continue
            accepted.append(p)
            cooldown_until = ts + timedelta(hours=48)
    return accepted


def select_boring_events(
    price_series: dict[str, list[tuple[datetime, float]]],
    target_count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Select boring non-stress events."""
    candidates: list[dict[str, Any]] = []

    for symbol, series in price_series.items():
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        series_sorted = sorted(series, key=lambda x: x[0])
        n = len(series_sorted)
        if n < 7:
            continue

        for i in range(6, n):
            ts, price_t = series_sorted[i]
            prev_ts, prev_price = series_sorted[i - 1]
            ret_1h = (price_t / prev_price - 1.0) * 10000.0 if prev_price > 0 else 0.0
            if abs(ret_1h) > 50.0:
                continue

            # 6h realized vol
            abs_returns_6h = []
            for j in range(max(0, i - 5), i + 1):
                if j == 0:
                    continue
                p_prev = series_sorted[j - 1][1]
                p_curr = series_sorted[j][1]
                if p_prev > 0:
                    abs_returns_6h.append(abs(p_curr / p_prev - 1.0) * 10000.0)
            vol_6h = sum(abs_returns_6h)

            # rolling vol percentile (30d)
            lookback_start = max(6, i - 30 * 24 + 6)
            lookback_vols = []
            for j in range(lookback_start, i):
                abs_r = []
                for k in range(max(0, j - 5), j + 1):
                    if k == 0:
                        continue
                    pk_prev = series_sorted[k - 1][1]
                    pk_curr = series_sorted[k][1]
                    if pk_prev > 0:
                        abs_r.append(abs(pk_curr / pk_prev - 1.0) * 10000.0)
                lookback_vols.append(sum(abs_r))

            count_below = sum(1 for v in lookback_vols if v < vol_6h) if lookback_vols else 0
            vol_pctile = count_below / len(lookback_vols) if lookback_vols else 0.5

            if vol_pctile < 0.40 or vol_pctile > 0.60:
                continue

            # forward 24h coverage
            target_24h = ts + timedelta(hours=24)
            has_coverage = False
            for ft, _ in series_sorted:
                if ft >= target_24h:
                    if abs((ft - target_24h).total_seconds()) <= 2 * 3600:
                        has_coverage = True
                    break
            if not has_coverage:
                continue

            candidates.append({
                "symbol": symbol,
                "event_timestamp_utc": ts.isoformat().replace("+00:00", "Z"),
                "price_t": price_t,
                "event_direction": GENERIC_EVENT_DIRECTION,
                "event_id": f"{symbol}_{ts.strftime('%Y%m%dT%H%M%SZ')}",
                "trailing_1h_return_bps": ret_1h,
                "trailing_6h_realized_vol_percentile": vol_pctile,
                "cooldown_key": symbol,
            })

    if not candidates:
        return []

    accepted = _apply_cooldown(candidates)

    if len(accepted) > target_count:
        rng.shuffle(accepted)
        return accepted[:target_count]

    return accepted


def select_random_timestamp_events(
    price_series: dict[str, list[tuple[datetime, float]]],
    target_count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Select random timestamps from eligible symbol-time universe."""
    candidates: list[dict[str, Any]] = []

    for symbol, series in price_series.items():
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        series_sorted = sorted(series, key=lambda x: x[0])
        n = len(series_sorted)
        if n < 7:
            continue

        for i in range(6, n):
            ts, price_t = series_sorted[i]
            # forward 24h coverage
            target_24h = ts + timedelta(hours=24)
            has_coverage = False
            for ft, _ in series_sorted:
                if ft >= target_24h:
                    if abs((ft - target_24h).total_seconds()) <= 2 * 3600:
                        has_coverage = True
                    break
            if not has_coverage:
                continue

            candidates.append({
                "symbol": symbol,
                "event_timestamp_utc": ts.isoformat().replace("+00:00", "Z"),
                "price_t": price_t,
                "event_direction": GENERIC_EVENT_DIRECTION,
                "event_id": f"{symbol}_{ts.strftime('%Y%m%dT%H%M%SZ')}",
                "cooldown_key": symbol,
            })

    if not candidates:
        return []

    accepted = _apply_cooldown(candidates)

    if len(accepted) > target_count:
        rng.shuffle(accepted)
        return accepted[:target_count]

    return accepted


def run_negative_control_events(
    events: list[dict[str, Any]],
    price_series: dict[str, list[tuple[datetime, float]]],
    name: str,
    description: str = "",
) -> NegativeControlResult:
    """Run negative control return computation on a set of events."""
    evaluated_ind = 0
    missing = 0
    gross_vals: list[float] = []
    net_50: list[float] = []

    for event in events:
        symbol = str(event.get("symbol", "")).upper()
        direction = str(event.get("event_direction", ""))
        if direction != GENERIC_EVENT_DIRECTION:
            continue
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            continue

        event_ts = _parse_timestamp(event.get("event_timestamp_utc", ""))
        entry_price = float(event.get("price_t", 0))
        series = price_series.get(symbol, [])
        target = event_ts + timedelta(hours=PRIMARY_HORIZON_HOURS)
        exit_price = lookup_price(series, target)

        if exit_price is None:
            missing += 1
            continue

        gross = compute_like_long_return(entry_price, exit_price)
        gross_vals.append(gross)
        net_50.append(gross - PRIMARY_COST_BPS)
        evaluated_ind += 1

    if not net_50:
        return NegativeControlResult(
            name=name, event_count=len(events), evaluated_count=0,
            gross_mean_bps_24h=None, gross_median_bps_24h=None,
            net_mean_bps_50=None, net_median_bps_50=None,
            win_rate_50=None, net_mean_bps_75=None, net_mean_bps_100=None,
            description=description,
        )

    net_75 = [g - 75.0 for g in gross_vals]
    net_100 = [g - 100.0 for g in gross_vals]

    return NegativeControlResult(
        name=name,
        event_count=len(events),
        evaluated_count=evaluated_ind,
        gross_mean_bps_24h=statistics.mean(gross_vals),
        gross_median_bps_24h=statistics.median(gross_vals),
        net_mean_bps_50=statistics.mean(net_50),
        net_median_bps_50=statistics.median(net_50),
        win_rate_50=sum(1 for v in net_50 if v > 0) / len(net_50),
        net_mean_bps_75=statistics.mean(net_75),
        net_mean_bps_100=statistics.mean(net_100),
        description=description,
    )


# ------------------------------------------------------------------
# Temporal concentration analysis
# ------------------------------------------------------------------


def compute_temporal_concentration(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute year, quarter, month, and symbol concentration metrics."""
    from collections import Counter, defaultdict

    year_counts: dict[int, int] = defaultdict(int)
    quarter_counts: dict[str, int] = defaultdict(int)
    month_counts: dict[str, int] = defaultdict(int)
    symbol_counts: dict[str, int] = defaultdict(int)
    date_counts: Counter[str] = Counter()

    for ev in events:
        ts = _parse_timestamp(ev.get("event_timestamp_utc", ""))
        year_counts[ts.year] += 1
        q = f"{ts.year}Q{(ts.month - 1) // 3 + 1}"
        quarter_counts[q] += 1
        m = f"{ts.year:04d}-{ts.month:02d}"
        month_counts[m] += 1
        symbol_counts[str(ev.get("symbol", "")).upper()] += 1
        date_counts[ts.strftime("%Y-%m-%d")] += 1

    total = len(events)
    max_year_share = max((c / total for c in year_counts.values()), default=0.0)
    max_year = max(year_counts, key=lambda k: year_counts[k]) if year_counts else None
    max_quarter_share = max((c / total for c in quarter_counts.values()), default=0.0)
    max_quarter = max(quarter_counts, key=lambda k: quarter_counts[k]) if quarter_counts else None
    max_month_share = max((c / total for c in month_counts.values()), default=0.0)
    max_month = max(month_counts, key=lambda k: month_counts[k]) if month_counts else None
    max_symbol_share = max((c / total for c in symbol_counts.values()), default=0.0)
    max_symbol = max(symbol_counts, key=lambda k: symbol_counts[k]) if symbol_counts else None
    top5_dates = date_counts.most_common(5)
    top5_share = sum(c for _, c in top5_dates) / total if total else 0.0

    return {
        "total_events": total,
        "year_counts": dict(year_counts),
        "quarter_counts": dict(quarter_counts),
        "month_counts": dict(month_counts),
        "max_year_share": max_year_share,
        "max_year": max_year,
        "max_quarter_share": max_quarter_share,
        "max_quarter": max_quarter,
        "max_month_share": max_month_share,
        "max_month": max_month,
        "max_symbol_event_share": max_symbol_share,
        "max_symbol": max_symbol,
        "top5_dates": top5_dates,
        "top5_dates_share": top5_share,
        "year_concentration_exceeds_50pct": max_year_share > 0.50,
    }