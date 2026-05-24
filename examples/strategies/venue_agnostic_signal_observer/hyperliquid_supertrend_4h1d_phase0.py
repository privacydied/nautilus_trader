"""Hyperliquid Supertrend 4h/1d Phase 0 observer-only scaffold.

Public archive feasibility diagnostics only. No network is used by this module.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Final

from .run_artifacts import atomic_write_json, atomic_write_text, create_run_id

STUDY_ID: Final[str] = "hyperliquid_supertrend_4h1d_altcoin_perp_v0"
FROZEN_UNIVERSE: Final[tuple[str, ...]] = (
    "HYPE", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "SUI", "TRX", "LTC",
    "BCH", "TON", "DOT", "AAVE", "UNI", "APT", "ARB", "OP", "SEI", "INJ",
)
UNIVERSE_HASH: Final[str] = "2ef0bbb8ae5b4790a8abf4ba479a508671d3d43acb52fe93650654fe4d186eed"
TIMEFRAMES: Final[tuple[str, str]] = ("4h", "1d")
ATR_PERIOD: Final[int] = 10
ATR_MULTIPLIER: Final[float] = 3.0
MAX_HOLD_BARS: Final[int] = 30
PRIMARY_COST_BPS: Final[float] = 10.0
DIAGNOSTIC_COST_BPS: Final[float] = 6.0
VOL_PERCENTILE_THRESHOLD: Final[float] = 0.50
WARMUP_DAYS: Final[int] = 30
MIN_USABLE_DAYS: Final[float] = 365.0
MAX_SINGLE_GAP_HOURS: Final[float] = 48.0
MAX_TOTAL_GAP_FRACTION: Final[float] = 0.05
SAFETY_MODE: Final[str] = "public_archive_observer_phase0_only"

PHASE0A_PASSED = "PHASE0A_PASSED"
PHASE0A_INSUFFICIENT_COVERAGE = "PHASE0A_INSUFFICIENT_COVERAGE"
PRECOMMITMENT_HASH_MISMATCH = "PRECOMMITMENT_HASH_MISMATCH"
ARCHIVE_SCHEMA_UNSUPPORTED = "ARCHIVE_SCHEMA_UNSUPPORTED"
ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED = "ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED"
PHASE0B_PASSED = "PHASE0B_PASSED"
PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES = "PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES"
PHASE0B_CONCENTRATED_SINGLE_SYMBOL = "PHASE0B_CONCENTRATED_SINGLE_SYMBOL"
PHASE0B_HOLD_PERIOD_DEGENERATE = "PHASE0B_HOLD_PERIOD_DEGENERATE"
PHASE0B_NO_TIMEFRAME_PASSED = "PHASE0B_NO_TIMEFRAME_PASSED"
PHASE0C_CHOP_REGIME_NOT_RESOLVED = "PHASE0C_CHOP_REGIME_NOT_RESOLVED"
PHASE0C_FUNDING_DOMINATES_NOT_TREND = "PHASE0C_FUNDING_DOMINATES_NOT_TREND"
PHASE0C_NO_GROSS_EDGE = "PHASE0C_NO_GROSS_EDGE"
PHASE0C_GROSS_POSITIVE_NET_NEGATIVE = "PHASE0C_GROSS_POSITIVE_NET_NEGATIVE"
PHASE0C_POSITIVE_EXPECTANCY_LOW_HIT_RATE = "PHASE0C_POSITIVE_EXPECTANCY_LOW_HIT_RATE"
PHASE0_READY_FOR_V1_PRECOMMITMENT = "PHASE0_READY_FOR_V1_PRECOMMITMENT"
PHASE0_READY_FOR_V1_PRECOMMITMENT_DIAGNOSTIC_ONLY = "PHASE0_READY_FOR_V1_PRECOMMITMENT_DIAGNOSTIC_ONLY"

COVERAGE_COLUMNS: Final[list[str]] = [
    "symbol", "price_source", "funding_source", "first_price_ts", "last_price_ts",
    "first_funding_ts", "last_funding_ts", "usable_start_ts", "usable_end_ts",
    "usable_days_after_warmup", "price_row_count", "funding_row_count",
    "max_price_gap_hours", "total_price_gap_fraction", "duplicate_price_timestamps",
    "phase0a_symbol_status",
]
REGIME_COLUMNS: Final[list[str]] = [
    "symbol", "timeframe", "n_unconditional_flips", "n_regime_qualified_entries",
    "regime_qualification_rate", "median_holding_period_bars", "mean_holding_period_bars",
    "max_holding_period_bars", "n_exit_signal_flip", "n_exit_max_hold", "phase0b_cell_status",
]
ENTRY_COLUMNS: Final[list[str]] = [
    "symbol", "timeframe", "entry_ts", "exit_ts", "direction", "entry_price", "exit_price",
    "holding_period_bars", "exit_reason", "realized_vol_percentile_at_entry",
    "gross_return_bps", "funding_accrual_bps", "net_return_bps_primary",
    "net_return_bps_diagnostic",
]
FULL_ENTRY_COLUMNS: Final[list[str]] = [
    "symbol", "timeframe", "entry_ts", "exit_ts", "entry_timestamp", "exit_timestamp",
    "entry_price", "exit_price", "direction", "gross_return_bps", "net_return_bps",
    "net_return_bps_primary", "net_return_bps_diagnostic", "funding_accrual_bps", "funding_bps",
    "explicit_fee_bps", "realized_total_cost_bps", "exit_reason", "holding_period_bars",
    "hold_bars", "max_hold_bars", "realized_vol_percentile_at_entry", "source_entry_idx",
    "source_exit_idx",
]
MECHANISM_COLUMNS: Final[list[str]] = [
    "timeframe", "n_entries", "mean_gross_return_bps", "median_gross_return_bps",
    "mean_funding_accrual_bps", "median_funding_accrual_bps", "mean_net_return_bps_primary",
    "median_net_return_bps_primary", "mean_net_return_bps_diagnostic",
    "median_net_return_bps_diagnostic", "win_rate_primary", "worst_decile_net_return_bps_primary",
    "median_abs_funding_to_abs_gross_ratio", "max_flip_count_per_symbol_per_year",
    "median_flip_count_per_symbol_per_year", "phase0c_verdict",
]


@dataclass(frozen=True)
class PriceRow:
    timestamp_utc: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    price_source: str


@dataclass(frozen=True)
class FundingRow:
    timestamp_utc: datetime
    symbol: str
    funding_rate: float
    funding_source: str


@dataclass(frozen=True)
class Bar:
    timestamp_utc: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    price_source: str


@dataclass(frozen=True)
class Entry:
    symbol: str
    timeframe: str
    entry_ts: datetime
    exit_ts: datetime
    direction: int
    entry_price: float
    exit_price: float
    holding_period_bars: int
    exit_reason: str
    realized_vol_percentile_at_entry: float
    gross_return_bps: float
    funding_accrual_bps: float
    net_return_bps_primary: float
    net_return_bps_diagnostic: float
    source_entry_idx: int | None = None
    source_exit_idx: int | None = None


def universe_hash(symbols: tuple[str, ...] = FROZEN_UNIVERSE) -> str:
    return hashlib.sha256("\n".join(symbols).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if any(c in text for c in "TZ:-"):
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    n = int(float(text))
    digits = len(str(abs(n)))
    if digits <= 10:
        return datetime.fromtimestamp(n, tz=UTC)
    if digits <= 13:
        return datetime.fromtimestamp(n / 1_000, tz=UTC)
    if digits <= 16:
        return datetime.fromtimestamp(n / 1_000_000, tz=UTC)
    return datetime.fromtimestamp(n / 1_000_000_000, tz=UTC)


def _floor_dt(ts: datetime, hours: int) -> datetime:
    ts = ts.astimezone(UTC)
    if hours == 24:
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    block = (ts.hour // hours) * hours
    return ts.replace(hour=block, minute=0, second=0, microsecond=0)


def load_price_csv(path: Path) -> list[PriceRow]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"timestamp_utc", "symbol", "open", "high", "low", "close"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(ARCHIVE_SCHEMA_UNSUPPORTED)
        rows = [
            PriceRow(
                timestamp_utc=parse_timestamp(r["timestamp_utc"]),
                symbol=str(r["symbol"]).upper(),
                open=float(r["open"]), high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
                price_source=str(r.get("price_source") or "unknown"),
            )
            for r in reader
        ]
    return sorted(rows, key=lambda r: (r.symbol, r.timestamp_utc))


def load_funding_csv(path: Path) -> list[FundingRow]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"timestamp_utc", "symbol", "funding_rate"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(ARCHIVE_SCHEMA_UNSUPPORTED)
        rows = [
            FundingRow(
                timestamp_utc=parse_timestamp(r["timestamp_utc"]),
                symbol=str(r["symbol"]).upper(),
                funding_rate=float(r["funding_rate"]),
                funding_source=str(r.get("funding_source") or "unknown"),
            )
            for r in reader
        ]
    return sorted(rows, key=lambda r: (r.symbol, r.timestamp_utc))


def group_by_symbol(rows: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {s: [] for s in FROZEN_UNIVERSE}
    for row in rows:
        if row.symbol in grouped:
            grouped[row.symbol].append(row)
    for values in grouped.values():
        values.sort(key=lambda r: r.timestamp_utc)
    return grouped


def assess_coverage(price_by_symbol: dict[str, list[PriceRow]], funding_by_symbol: dict[str, list[FundingRow]]) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    statuses: list[str] = []
    for symbol in FROZEN_UNIVERSE:
        prices = price_by_symbol.get(symbol, [])
        funds = funding_by_symbol.get(symbol, [])
        duplicate_count = len(prices) - len({p.timestamp_utc for p in prices})
        if prices and any(p.timestamp_utc.hour != 0 or p.timestamp_utc.minute != 0 for p in prices):
            timestamp_status = None
        elif len(prices) > 48:
            timestamp_status = ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED
        else:
            timestamp_status = None
        first_price = prices[0].timestamp_utc if prices else None
        last_price = prices[-1].timestamp_utc if prices else None
        first_funding = funds[0].timestamp_utc if funds else None
        last_funding = funds[-1].timestamp_utc if funds else None
        usable_start = first_price + timedelta(days=WARMUP_DAYS) if first_price else None
        usable_end = last_price
        usable_days = ((usable_end - usable_start).total_seconds() / 86400.0) if usable_start and usable_end and usable_end > usable_start else 0.0
        diffs = [(b.timestamp_utc - a.timestamp_utc).total_seconds() / 3600.0 for a, b in zip(prices, prices[1:])]
        max_gap = max(diffs) if diffs else None
        expected_rows = ((last_price - first_price).total_seconds() / 3600.0 + 1.0) if first_price and last_price else 0.0
        total_gap_fraction = max(0.0, (expected_rows - len({p.timestamp_utc for p in prices})) / expected_rows) if expected_rows else 1.0
        funding_overlap = bool(first_funding and last_funding and usable_start and usable_end and first_funding <= usable_end and last_funding >= usable_start)
        status = PHASE0A_PASSED
        if timestamp_status:
            status = timestamp_status
        if duplicate_count > 0:
            status = ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED
        if not prices or not funds or usable_days < MIN_USABLE_DAYS or not funding_overlap:
            status = PHASE0A_INSUFFICIENT_COVERAGE
        if max_gap is None or max_gap >= MAX_SINGLE_GAP_HOURS or total_gap_fraction >= MAX_TOTAL_GAP_FRACTION:
            status = PHASE0A_INSUFFICIENT_COVERAGE
        statuses.append(status)
        rows.append({
            "symbol": symbol,
            "price_source": prices[0].price_source if prices else "",
            "funding_source": funds[0].funding_source if funds else "",
            "first_price_ts": _iso(first_price), "last_price_ts": _iso(last_price),
            "first_funding_ts": _iso(first_funding), "last_funding_ts": _iso(last_funding),
            "usable_start_ts": _iso(usable_start), "usable_end_ts": _iso(usable_end),
            "usable_days_after_warmup": round(usable_days, 6),
            "price_row_count": len(prices), "funding_row_count": len(funds),
            "max_price_gap_hours": max_gap, "total_price_gap_fraction": total_gap_fraction,
            "duplicate_price_timestamps": duplicate_count, "phase0a_symbol_status": status,
        })
    if any(s == ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED for s in statuses):
        return ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED, rows
    return (PHASE0A_PASSED if all(s == PHASE0A_PASSED for s in statuses) else PHASE0A_INSUFFICIENT_COVERAGE), rows


def resample_bars(hourly: list[PriceRow], timeframe: str) -> list[Bar]:
    hours = {"4h": 4, "1d": 24}[timeframe]
    if not hourly:
        return []
    buckets: dict[datetime, list[PriceRow]] = defaultdict(list)
    for row in sorted(hourly, key=lambda r: r.timestamp_utc):
        buckets[_floor_dt(row.timestamp_utc, hours)].append(row)
    bars: list[Bar] = []
    for start in sorted(buckets):
        rows = sorted(buckets[start], key=lambda r: r.timestamp_utc)
        if len(rows) != hours:
            continue
        expected = [start + timedelta(hours=i) for i in range(hours)]
        if [r.timestamp_utc for r in rows] != expected:
            continue
        bars.append(Bar(start + timedelta(hours=hours), rows[0].symbol, rows[0].open, max(r.high for r in rows), min(r.low for r in rows), rows[-1].close, rows[0].price_source))
    return bars


def realized_vol_percentiles(hourly: list[PriceRow]) -> dict[datetime, float | None]:
    rows = sorted(hourly, key=lambda r: r.timestamp_utc)
    log_returns: list[tuple[datetime, float]] = []
    for prev, cur in zip(rows, rows[1:]):
        if prev.close > 0 and cur.close > 0:
            log_returns.append((cur.timestamp_utc, math.log(cur.close / prev.close)))
    vols: list[tuple[datetime, float]] = []
    for i in range(23, len(log_returns)):
        window = [r for _, r in log_returns[i - 23:i + 1]]
        mu = mean(window)
        var = sum((x - mu) ** 2 for x in window) / len(window)
        vols.append((log_returns[i][0], math.sqrt(var) * math.sqrt(24 * 365)))
    result: dict[datetime, float | None] = {}
    for i, (ts, vol) in enumerate(vols):
        cutoff = ts - timedelta(days=30)
        prior = [v for t, v in vols[max(0, i - 24 * 31):i] if cutoff <= t < ts]
        if not prior:
            result[ts] = None
        else:
            result[ts] = sum(1 for v in prior if v <= vol) / len(prior)
    return result


def percentile_at_or_before(values: dict[datetime, float | None], ts: datetime) -> float | None:
    eligible = [t for t, v in values.items() if v is not None and t <= ts]
    if not eligible:
        return None
    return values[max(eligible)]


def compute_supertrend(bars: list[Bar], period: int = ATR_PERIOD, multiplier: float = ATR_MULTIPLIER) -> list[int | None]:
    if not bars:
        return []
    tr: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr.append(bar.high - bar.low)
        else:
            prev_close = bars[i - 1].close
            tr.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
    atr: list[float | None] = [None] * len(bars)
    if len(bars) >= period:
        atr[period - 1] = sum(tr[:period]) / period
        for i in range(period, len(bars)):
            atr[i] = ((atr[i - 1] or 0.0) * (period - 1) + tr[i]) / period
    final_upper: list[float | None] = [None] * len(bars)
    final_lower: list[float | None] = [None] * len(bars)
    direction: list[int | None] = [None] * len(bars)
    for i, bar in enumerate(bars):
        if atr[i] is None:
            continue
        hl2 = (bar.high + bar.low) / 2.0
        basic_upper = hl2 + multiplier * atr[i]
        basic_lower = hl2 - multiplier * atr[i]
        if i == 0 or final_upper[i - 1] is None or final_lower[i - 1] is None:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            direction[i] = 1
            continue
        final_upper[i] = basic_upper if basic_upper < final_upper[i - 1] or bars[i - 1].close > final_upper[i - 1] else final_upper[i - 1]
        final_lower[i] = basic_lower if basic_lower > final_lower[i - 1] or bars[i - 1].close < final_lower[i - 1] else final_lower[i - 1]
        prev_dir = direction[i - 1] if direction[i - 1] is not None else 1
        if prev_dir == -1 and bar.close > final_upper[i]:
            direction[i] = 1
        elif prev_dir == 1 and bar.close < final_lower[i]:
            direction[i] = -1
        else:
            direction[i] = prev_dir
    return direction


def find_flip_indices(directions: list[int | None]) -> list[int]:
    flips: list[int] = []
    for i in range(1, len(directions)):
        if directions[i] is not None and directions[i - 1] is not None and directions[i] != directions[i - 1]:
            flips.append(i)
    return flips


def build_entry_plan(symbol: str, timeframe: str, bars: list[Bar], directions: list[int | None], vol_percentiles: dict[datetime, float | None]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    flips = find_flip_indices(directions)
    entries: list[dict[str, Any]] = []
    for pos, idx in enumerate(flips):
        pct = percentile_at_or_before(vol_percentiles, bars[idx].timestamp_utc)
        if pct is None or pct < VOL_PERCENTILE_THRESHOLD:
            continue
        next_flip = next((j for j in flips[pos + 1:] if directions[j] == -directions[idx]), None)
        cap_idx = min(idx + MAX_HOLD_BARS, len(bars) - 1)
        if next_flip is not None and next_flip <= cap_idx:
            exit_idx = next_flip
            reason = "signal_flip"
        else:
            exit_idx = cap_idx
            reason = "max_hold"
        if exit_idx <= idx:
            continue
        entries.append({
            "symbol": symbol, "timeframe": timeframe, "entry_idx": idx, "exit_idx": exit_idx,
            "entry_ts": bars[idx].timestamp_utc, "exit_ts": bars[exit_idx].timestamp_utc,
            "direction": int(directions[idx] or 0), "entry_price": bars[idx].close, "exit_price": bars[exit_idx].close,
            "holding_period_bars": exit_idx - idx, "exit_reason": reason,
            "realized_vol_percentile_at_entry": pct,
        })
    holds = [e["holding_period_bars"] for e in entries]
    cell_status = "OK" if entries else PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES
    row = {
        "symbol": symbol, "timeframe": timeframe, "n_unconditional_flips": len(flips),
        "n_regime_qualified_entries": len(entries),
        "regime_qualification_rate": (len(entries) / len(flips)) if flips else 0.0,
        "median_holding_period_bars": median(holds) if holds else None,
        "mean_holding_period_bars": mean(holds) if holds else None,
        "max_holding_period_bars": max(holds) if holds else None,
        "n_exit_signal_flip": sum(1 for e in entries if e["exit_reason"] == "signal_flip"),
        "n_exit_max_hold": sum(1 for e in entries if e["exit_reason"] == "max_hold"),
        "phase0b_cell_status": cell_status,
    }
    return entries, row


def phase0b_verdict(timeframe: str, entries: list[dict[str, Any]]) -> str:
    min_total = 200 if timeframe == "4h" else 100
    if len(entries) < min_total:
        return PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES
    by_symbol: dict[str, int] = defaultdict(int)
    for e in entries:
        by_symbol[e["symbol"]] += 1
    if sum(1 for c in by_symbol.values() if c >= 5) < 10:
        return PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES
    if max(by_symbol.values(), default=0) / len(entries) > 0.30:
        return PHASE0B_CONCENTRATED_SINGLE_SYMBOL
    med_hold = median([e["holding_period_bars"] for e in entries])
    if med_hold < 2 or med_hold > 30:
        return PHASE0B_HOLD_PERIOD_DEGENERATE
    return PHASE0B_PASSED


def funding_accrual_bps(funding_rows: list[FundingRow], entry_ts: datetime, exit_ts: datetime, position_sign: int) -> float:
    return sum(-position_sign * r.funding_rate * 10000.0 for r in funding_rows if entry_ts < r.timestamp_utc <= exit_ts)


def gross_return_bps(entry_price: float, exit_price: float, direction: int) -> float:
    return direction * ((exit_price - entry_price) / entry_price) * 10000.0


def materialize_entries(plans: list[dict[str, Any]], funding_by_symbol: dict[str, list[FundingRow]]) -> list[Entry]:
    entries: list[Entry] = []
    for plan in plans:
        gross = gross_return_bps(plan["entry_price"], plan["exit_price"], plan["direction"])
        fund = funding_accrual_bps(funding_by_symbol.get(plan["symbol"], []), plan["entry_ts"], plan["exit_ts"], plan["direction"])
        entries.append(Entry(
            symbol=plan["symbol"], timeframe=plan["timeframe"], entry_ts=plan["entry_ts"], exit_ts=plan["exit_ts"],
            direction=plan["direction"], entry_price=plan["entry_price"], exit_price=plan["exit_price"],
            holding_period_bars=plan["holding_period_bars"], exit_reason=plan["exit_reason"],
            realized_vol_percentile_at_entry=plan["realized_vol_percentile_at_entry"],
            gross_return_bps=gross, funding_accrual_bps=fund,
            net_return_bps_primary=gross + fund - PRIMARY_COST_BPS,
            net_return_bps_diagnostic=gross + fund - DIAGNOSTIC_COST_BPS,
            source_entry_idx=plan.get("entry_idx"),
            source_exit_idx=plan.get("exit_idx"),
        ))
    return entries


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def flip_counts_per_symbol_year(cell_rows: list[dict[str, Any]], price_by_symbol: dict[str, list[PriceRow]], timeframe: str) -> dict[str, float]:
    counts = {r["symbol"]: int(r["n_unconditional_flips"]) for r in cell_rows if r["timeframe"] == timeframe}
    out: dict[str, float] = {}
    for symbol, count in counts.items():
        rows = price_by_symbol.get(symbol, [])
        if len(rows) < 2:
            out[symbol] = 0.0
        else:
            years = max((rows[-1].timestamp_utc - rows[0].timestamp_utc).total_seconds() / (365.0 * 86400.0), 1e-9)
            out[symbol] = count / years
    return out


def classify_phase0c_verdict(*, timeframe: str, median_gross_return_bps: float, median_funding_accrual_bps: float, median_net_return_bps_primary: float, win_rate_primary: float, max_flip_count_per_symbol_per_year: float) -> tuple[str, float]:
    chop_limit = 100.0 if timeframe == "4h" else 30.0
    if abs(median_gross_return_bps) < 1e-12:
        dominance = math.inf if abs(median_funding_accrual_bps) >= 1e-12 else 0.0
    else:
        dominance = abs(median_funding_accrual_bps) / abs(median_gross_return_bps)
    if max_flip_count_per_symbol_per_year > chop_limit:
        return PHASE0C_CHOP_REGIME_NOT_RESOLVED, dominance
    if dominance > 1.0:
        return PHASE0C_FUNDING_DOMINATES_NOT_TREND, dominance
    if median_gross_return_bps <= 0:
        return PHASE0C_NO_GROSS_EDGE, dominance
    if median_net_return_bps_primary <= 0 and median_gross_return_bps > 0:
        return PHASE0C_GROSS_POSITIVE_NET_NEGATIVE, dominance
    if median_net_return_bps_primary > 0 and win_rate_primary < 0.45:
        return PHASE0C_POSITIVE_EXPECTANCY_LOW_HIT_RATE, dominance
    return PHASE0_READY_FOR_V1_PRECOMMITMENT, dominance


def phase0c_summary(timeframe: str, entries: list[Entry], flips_per_year: dict[str, float]) -> dict[str, Any]:
    gross = [e.gross_return_bps for e in entries]
    fund = [e.funding_accrual_bps for e in entries]
    netp = [e.net_return_bps_primary for e in entries]
    netd = [e.net_return_bps_diagnostic for e in entries]
    med_gross = median(gross) if gross else 0.0
    med_fund = median(fund) if fund else 0.0
    max_flip = max(flips_per_year.values(), default=0.0)
    win_rate = sum(1 for x in netp if x > 0) / len(netp) if netp else 0.0
    med_netp = median(netp) if netp else 0.0
    verdict, dominance = classify_phase0c_verdict(
        timeframe=timeframe,
        median_gross_return_bps=med_gross,
        median_funding_accrual_bps=med_fund,
        median_net_return_bps_primary=med_netp,
        win_rate_primary=win_rate,
        max_flip_count_per_symbol_per_year=max_flip,
    )
    return {
        "timeframe": timeframe, "n_entries": len(entries),
        "mean_gross_return_bps": mean(gross) if gross else None,
        "median_gross_return_bps": med_gross if gross else None,
        "mean_funding_accrual_bps": mean(fund) if fund else None,
        "median_funding_accrual_bps": med_fund if fund else None,
        "mean_net_return_bps_primary": mean(netp) if netp else None,
        "median_net_return_bps_primary": med_netp if netp else None,
        "mean_net_return_bps_diagnostic": mean(netd) if netd else None,
        "median_net_return_bps_diagnostic": median(netd) if netd else None,
        "win_rate_primary": win_rate,
        "worst_decile_net_return_bps_primary": percentile(netp, 0.10),
        "median_abs_funding_to_abs_gross_ratio": dominance,
        "max_flip_count_per_symbol_per_year": max_flip,
        "median_flip_count_per_symbol_per_year": median(list(flips_per_year.values())) if flips_per_year else 0.0,
        "phase0c_verdict": verdict,
    }


def overall_status(phase0a: str, phase0b: dict[str, str], phase0c: dict[str, str]) -> str:
    if phase0a != PHASE0A_PASSED:
        return phase0a
    if not any(v == PHASE0B_PASSED for v in phase0b.values()):
        return PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES
    if any(v == PHASE0_READY_FOR_V1_PRECOMMITMENT for v in phase0c.values()):
        return PHASE0_READY_FOR_V1_PRECOMMITMENT_DIAGNOSTIC_ONLY
    for reason in [PHASE0C_CHOP_REGIME_NOT_RESOLVED, PHASE0C_FUNDING_DOMINATES_NOT_TREND, PHASE0C_NO_GROSS_EDGE, PHASE0C_GROSS_POSITIVE_NET_NEGATIVE, PHASE0C_POSITIVE_EXPECTANCY_LOW_HIT_RATE]:
        if reason in phase0c.values():
            return reason
    return PHASE0B_NO_TIMEFRAME_PASSED


def verify_precommitment_hash(precommitment_path: Path, expected_hash_path: Path) -> tuple[bool, str, str]:
    expected = expected_hash_path.read_text(encoding="utf-8").strip()
    actual = sha256_file(precommitment_path)
    return expected == actual, expected, actual


def run_phase0(*, out_root: Path, precommitment_path: Path, expected_hash_path: Path, price_csv: Path | None = None, funding_csv: Path | None = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    run_id = create_run_id("hyperliquid_supertrend_4h1d_phase0")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    hash_ok, expected_hash, actual_hash = verify_precommitment_hash(precommitment_path, expected_hash_path)
    manifest_base = _manifest_base(run_id, args or {}, precommitment_path, expected_hash, actual_hash, hash_ok)
    atomic_write_text(run_dir / "precommitment_hash.txt", actual_hash + "\n")
    if not hash_ok:
        return _write_all(run_dir, manifest_base, [], [], [], [], [], [], PRECOMMITMENT_HASH_MISMATCH, {}, {}, [])
    if price_csv is None or funding_csv is None:
        coverage = [_empty_coverage_row(symbol) for symbol in FROZEN_UNIVERSE]
        return _write_all(run_dir, manifest_base, coverage, [], [], [], [], [], PHASE0A_INSUFFICIENT_COVERAGE, {}, {}, [])
    try:
        prices = load_price_csv(price_csv)
        funding = load_funding_csv(funding_csv)
    except ValueError as exc:
        verdict = str(exc) if str(exc) in {ARCHIVE_SCHEMA_UNSUPPORTED, ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED} else ARCHIVE_SCHEMA_UNSUPPORTED
        return _write_all(run_dir, manifest_base, [], [], [], [], [], [], verdict, {}, {}, [])
    price_by_symbol = group_by_symbol(prices)
    funding_by_symbol = group_by_symbol(funding)
    phase0a, coverage = assess_coverage(price_by_symbol, funding_by_symbol)
    if phase0a != PHASE0A_PASSED:
        return _write_all(run_dir, manifest_base, coverage, [], [], [], [], [], phase0a, {}, {}, [])
    all_plans_by_tf: dict[str, list[dict[str, Any]]] = {tf: [] for tf in TIMEFRAMES}
    cell_rows: list[dict[str, Any]] = []
    for symbol in FROZEN_UNIVERSE:
        volp = realized_vol_percentiles(price_by_symbol[symbol])
        for tf in TIMEFRAMES:
            bars = resample_bars(price_by_symbol[symbol], tf)
            dirs = compute_supertrend(bars)
            plans, row = build_entry_plan(symbol, tf, bars, dirs, volp)
            all_plans_by_tf[tf].extend(plans)
            cell_rows.append(row)
    phase0b = {tf: phase0b_verdict(tf, plans) for tf, plans in all_plans_by_tf.items()}
    if not any(v == PHASE0B_PASSED for v in phase0b.values()):
        phase0b = {tf: (v if v != PHASE0B_PASSED else v) for tf, v in phase0b.items()}
        return _write_all(run_dir, manifest_base, coverage, cell_rows, [], [], [], [], phase0a, phase0b, {}, [])
    entry_rows: list[dict[str, Any]] = []
    mech_rows: list[dict[str, Any]] = []
    flip_rows: list[dict[str, Any]] = []
    decomp_rows: list[dict[str, Any]] = []
    phase0c: dict[str, str] = {}
    for tf, verdict in phase0b.items():
        if verdict != PHASE0B_PASSED:
            continue
        entries = materialize_entries(all_plans_by_tf[tf], funding_by_symbol)
        entry_rows.extend(_entry_to_row(e) for e in entries)
        flips = flip_counts_per_symbol_year(cell_rows, price_by_symbol, tf)
        flip_rows.extend({"symbol": s, "timeframe": tf, "flip_count_per_symbol_per_year": v} for s, v in sorted(flips.items()))
        summary = phase0c_summary(tf, entries, flips)
        phase0c[tf] = summary["phase0c_verdict"]
        mech_rows.append(summary)
        decomp_rows.append({"timeframe": tf, "median_gross_return_bps": summary["median_gross_return_bps"], "median_funding_accrual_bps": summary["median_funding_accrual_bps"], "median_abs_funding_to_abs_gross_ratio": summary["median_abs_funding_to_abs_gross_ratio"]})
    return _write_all(run_dir, manifest_base, coverage, cell_rows, entry_rows, mech_rows, decomp_rows, flip_rows, phase0a, phase0b, phase0c, [str(price_csv), str(funding_csv)])


def _manifest_base(run_id: str, args: dict[str, Any], precommitment_path: Path, expected_hash: str, actual_hash: str, matched: bool) -> dict[str, Any]:
    return {
        "run_id": run_id, "generated_at_utc": _iso(datetime.now(UTC)),
        "git_sha": _git(["rev-parse", "HEAD"]), "git_branch": _git(["branch", "--show-current"]),
        "git_dirty": bool(_git(["status", "--porcelain"])), "repo_root": _git(["rev-parse", "--show-toplevel"]),
        "safety_mode": SAFETY_MODE, "study_id": STUDY_ID, "args": args,
        "precommitment_path": str(precommitment_path), "precommitment_hash_expected": expected_hash,
        "precommitment_hash_actual": actual_hash, "verified_matches_precommitment_file": matched,
        "data_sources_used": [], "network_used": False, "notes": [],
    }


def _write_all(run_dir: Path, manifest_base: dict[str, Any], coverage_rows: list[dict[str, Any]], cell_rows: list[dict[str, Any]], entry_rows: list[dict[str, Any]], mech_rows: list[dict[str, Any]], decomp_rows: list[dict[str, Any]], flip_rows: list[dict[str, Any]], phase0a: str, phase0b: dict[str, str], phase0c: dict[str, str], data_sources: list[str]) -> dict[str, Any]:
    status = overall_status(phase0a, phase0b, phase0c)
    manifest = dict(manifest_base)
    manifest.update({
        "phase0a_verdict": phase0a,
        "phase0b_verdict_by_timeframe": phase0b,
        "phase0c_verdict_by_timeframe": phase0c,
        "overall_status": status,
        "data_sources_used": data_sources,
    })
    entries_full_path = run_dir / "entries_full.csv"
    entries_preview_path = run_dir / "entries_preview.csv"
    _write_csv(run_dir / "coverage_by_symbol.csv", COVERAGE_COLUMNS, coverage_rows)
    _write_csv(run_dir / "regime_qualification_by_symbol.csv", REGIME_COLUMNS, cell_rows)
    _write_csv(entries_full_path, FULL_ENTRY_COLUMNS, entry_rows)
    _write_csv(entries_preview_path, ENTRY_COLUMNS, entry_rows[:200])
    _write_csv(run_dir / "mechanism_sanity_by_timeframe.csv", MECHANISM_COLUMNS, mech_rows)
    _write_csv(run_dir / "funding_vs_gross_decomposition.csv", ["timeframe", "median_gross_return_bps", "median_funding_accrual_bps", "median_abs_funding_to_abs_gross_ratio"], decomp_rows)
    _write_csv(run_dir / "flip_count_by_symbol.csv", ["symbol", "timeframe", "flip_count_per_symbol_per_year"], flip_rows)
    entry_artifacts = _entry_artifact_metadata(entries_full_path, entries_preview_path, entry_rows)
    entry_counts = _entry_counts_by_timeframe(entry_rows)
    entry_reconciliation = _entry_aggregate_reconciliation(entry_rows)
    manifest["entry_artifacts"] = entry_artifacts
    manifest["entry_counts_by_timeframe"] = entry_counts
    summary = {
        "run_id": manifest["run_id"],
        "phase0a_verdict": phase0a,
        "phase0b_verdict_by_timeframe": phase0b,
        "phase0c_verdict_by_timeframe": phase0c,
        "overall_status": status,
        "report_dir": str(run_dir),
        "entry_artifacts": entry_artifacts,
        "entry_counts_by_timeframe": entry_counts,
        "entry_aggregate_reconciliation": entry_reconciliation,
    }
    atomic_write_json(run_dir / "summary.json", summary)
    atomic_write_text(run_dir / "summary.md", _summary_md(summary, manifest))
    atomic_write_json(run_dir / "manifest.json", manifest)
    return {"run_dir": str(run_dir), "summary": summary, "manifest": manifest}


def _entry_counts_by_timeframe(entry_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in entry_rows:
        timeframe = str(row.get("timeframe") or "")
        if timeframe:
            counts[timeframe] = counts.get(timeframe, 0) + 1
    return dict(sorted(counts.items()))


def _entry_aggregate_reconciliation(entry_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in entry_rows:
        grouped[str(row.get("timeframe") or "")].append(row)
    out: dict[str, dict[str, Any]] = {}
    for timeframe, rows in sorted(grouped.items()):
        if not timeframe:
            continue
        gross = [float(r["gross_return_bps"]) for r in rows if r.get("gross_return_bps") not in (None, "")]
        net = [float(r["net_return_bps_primary"]) for r in rows if r.get("net_return_bps_primary") not in (None, "")]
        out[timeframe] = {
            "n_entries": len(rows),
            "median_gross_return_bps": median(gross) if gross else None,
            "median_net_return_bps_primary": median(net) if net else None,
        }
    return out


def _entry_artifact_metadata(full_path: Path, preview_path: Path, entry_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    full_rows = len(entry_rows)
    preview_rows = min(full_rows, 200)
    return {
        "entries_full": {
            "path": str(full_path),
            "sha256": sha256_file(full_path),
            "row_count": full_rows,
            "artifact_role": "primary_full_per_entry_artifact",
            "truncated": False,
        },
        "entries_preview": {
            "path": str(preview_path),
            "sha256": sha256_file(preview_path),
            "row_count": preview_rows,
            "artifact_role": "preview_truncated_first_200_rows",
            "truncated": full_rows > preview_rows,
        },
    }


def _empty_coverage_row(symbol: str) -> dict[str, Any]:
    return {k: "" for k in COVERAGE_COLUMNS} | {"symbol": symbol, "phase0a_symbol_status": PHASE0A_INSUFFICIENT_COVERAGE, "price_row_count": 0, "funding_row_count": 0, "duplicate_price_timestamps": 0}


def _entry_to_row(e: Entry) -> dict[str, Any]:
    row = asdict(e)
    entry_ts = _iso(e.entry_ts)
    exit_ts = _iso(e.exit_ts)
    row["entry_ts"] = entry_ts
    row["exit_ts"] = exit_ts
    row["entry_timestamp"] = entry_ts
    row["exit_timestamp"] = exit_ts
    row["direction"] = "long" if e.direction > 0 else "short"
    row["hold_bars"] = e.holding_period_bars
    row["max_hold_bars"] = MAX_HOLD_BARS
    row["funding_bps"] = e.funding_accrual_bps
    row["explicit_fee_bps"] = PRIMARY_COST_BPS
    row["realized_total_cost_bps"] = e.gross_return_bps - e.net_return_bps_primary
    row["net_return_bps"] = e.net_return_bps_primary
    return row


def _summary_md(summary: dict[str, Any], manifest: dict[str, Any]) -> str:
    return "\n".join([
        "# Hyperliquid Supertrend 4h/1d Phase 0",
        "",
        f"Study ID: `{STUDY_ID}`",
        f"Precommitment hash matched: `{manifest['verified_matches_precommitment_file']}`",
        f"Phase 0A: `{summary['phase0a_verdict']}`",
        f"Phase 0B: `{summary['phase0b_verdict_by_timeframe']}`",
        f"Phase 0C: `{summary['phase0c_verdict_by_timeframe']}`",
        f"Overall: `{summary['overall_status']}`",
        "",
        "Diagnostic-only Phase 0 feasibility scaffold. No registry update was made by this runner.",
    ]) + "\n"


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    os.replace(tmp, path)


def _git(args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except Exception:
        return ""


def _iso(ts: datetime | None) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z") if ts else ""
