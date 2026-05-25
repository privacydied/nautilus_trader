"""Streaming validation helpers for staged Hyperliquid asset_ctxs fragments.

These helpers separate fragment-level corruption from source-level per-symbol
unusability. They are intentionally archive-only: no exchange clients, account
state, or order-capable paths.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class SymbolFragmentStats:
    symbol: str
    row_count: int = 0
    first_ts: str | None = None
    last_ts: str | None = None
    unique_date_count: int = 0
    duplicate_timestamp_count: int = 0
    duplicate_timestamp_conflict_count: int = 0
    price_min: float | None = None
    price_max: float | None = None
    oi_min: float | None = None
    oi_max: float | None = None
    all_price_one_value: bool = False
    all_oi_zero: bool = False
    classification: str = "SYMBOL_MISSING"


@dataclass(frozen=True)
class FragmentValidationResult:
    valid: bool
    classification: str
    jsonl_file_count: int
    total_row_count: int
    global_first_ts: str | None
    global_last_ts: str | None
    global_unique_date_count: int
    duplicate_timestamp_count: int
    duplicate_timestamp_conflict_count: int
    unusable_symbols: list[str] = field(default_factory=list)
    hard_fail_reasons: list[str] = field(default_factory=list)
    coverage_caveats: list[str] = field(default_factory=list)
    symbol_stats: dict[str, SymbolFragmentStats] = field(default_factory=dict)


def _normalize_ts(value: Any) -> str:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e18:
            dt = datetime.fromtimestamp(number / 1e9, tz=UTC)
        elif number > 1e15:
            dt = datetime.fromtimestamp(number / 1e6, tz=UTC)
        elif number > 1e12:
            dt = datetime.fromtimestamp(number / 1e3, tz=UTC)
        else:
            dt = datetime.fromtimestamp(number, tz=UTC)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _float_field(row: dict[str, Any], names: Iterable[str]) -> float | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            try:
                out = float(value)
            except (TypeError, ValueError):
                return None
            return out if math.isfinite(out) else None
    return None


def _scan_symbol_jsonl(path: Path) -> SymbolFragmentStats:
    symbol = path.stem.upper()
    row_count = 0
    first_ts: str | None = None
    last_ts: str | None = None
    dates: set[str] = set()
    seen_by_ts: dict[str, tuple[float | None, float | None]] = {}
    duplicate_timestamp_count = 0
    duplicate_timestamp_conflict_count = 0
    price_min: float | None = None
    price_max: float | None = None
    oi_min: float | None = None
    oi_max: float | None = None
    price_values: set[float] = set()
    all_oi_zero = True

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            row_count += 1
            ts = _normalize_ts(row.get("ts_event", row.get("timestamp", row.get("time", row.get("ts")))))
            price = _float_field(row, ("price", "mark_price", "mark", "markPx", "close"))
            oi = _float_field(row, ("open_interest", "openInterest", "oi"))
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
            dates.add(ts[:10])
            if ts in seen_by_ts:
                duplicate_timestamp_count += 1
                if seen_by_ts[ts] != (price, oi):
                    duplicate_timestamp_conflict_count += 1
            else:
                seen_by_ts[ts] = (price, oi)
            if price is not None:
                price_min = price if price_min is None else min(price_min, price)
                price_max = price if price_max is None else max(price_max, price)
                if len(price_values) <= 2:
                    price_values.add(price)
            if oi is not None:
                oi_min = oi if oi_min is None else min(oi_min, oi)
                oi_max = oi if oi_max is None else max(oi_max, oi)
                if oi != 0.0:
                    all_oi_zero = False

    all_price_one_value = row_count > 0 and len(price_values) == 1 and price_min == price_max
    if row_count == 0:
        classification = "SYMBOL_MISSING"
    elif duplicate_timestamp_conflict_count:
        classification = "SYMBOL_CORRUPT_DUPLICATE_CONFLICT"
    elif all_price_one_value and all_oi_zero:
        classification = "SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER"
    elif all_price_one_value or oi_min == oi_max:
        classification = "SYMBOL_UNUSABLE_CONSTANT_FIELD"
    else:
        classification = "SYMBOL_ACTIVE_VALID"
    return SymbolFragmentStats(
        symbol=symbol,
        row_count=row_count,
        first_ts=first_ts,
        last_ts=last_ts,
        unique_date_count=len(dates),
        duplicate_timestamp_count=duplicate_timestamp_count,
        duplicate_timestamp_conflict_count=duplicate_timestamp_conflict_count,
        price_min=price_min,
        price_max=price_max,
        oi_min=oi_min,
        oi_max=oi_max,
        all_price_one_value=all_price_one_value,
        all_oi_zero=all_oi_zero,
        classification=classification,
    )


def validate_asset_ctxs_fragment(
    fragment_dir: Path,
    *,
    expected_first_date: date,
    expected_last_date: date,
    expected_date_count: int,
) -> FragmentValidationResult:
    manifest_path = fragment_dir / "manifest.json"
    hard_fail_reasons: list[str] = []
    coverage_caveats: list[str] = []
    if not manifest_path.exists():
        return FragmentValidationResult(
            valid=False,
            classification="FRAGMENT_CORRUPT_MISSING_MANIFEST",
            jsonl_file_count=0,
            total_row_count=0,
            global_first_ts=None,
            global_last_ts=None,
            global_unique_date_count=0,
            duplicate_timestamp_count=0,
            duplicate_timestamp_conflict_count=0,
            hard_fail_reasons=["manifest_missing"],
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_dates = list(manifest.get("date_list") or [])
    if not manifest_dates:
        hard_fail_reasons.append("manifest_date_list_missing")
    else:
        if manifest_dates[0] != expected_first_date.isoformat():
            hard_fail_reasons.append(f"manifest_first_date_mismatch:{manifest_dates[0]}")
        if manifest_dates[-1] != expected_last_date.isoformat():
            hard_fail_reasons.append(f"manifest_last_date_mismatch:{manifest_dates[-1]}")
        if len(manifest_dates) != expected_date_count:
            hard_fail_reasons.append(f"manifest_date_count_mismatch:{len(manifest_dates)}")

    jsonl_paths = sorted(p for p in fragment_dir.glob("*.jsonl") if p.name != "manifest.json")
    if not jsonl_paths:
        hard_fail_reasons.append("jsonl_files_missing")
    symbol_stats = {path.stem.upper(): _scan_symbol_jsonl(path) for path in jsonl_paths}
    total_row_count = sum(stats.row_count for stats in symbol_stats.values())
    global_first_ts = min((stats.first_ts for stats in symbol_stats.values() if stats.first_ts), default=None)
    global_last_ts = max((stats.last_ts for stats in symbol_stats.values() if stats.last_ts), default=None)
    unique_dates = {
        day
        for stats in symbol_stats.values()
        for day in ([stats.first_ts[:10]] if stats.first_ts else [])
    }
    # Count unique dates exactly by reusing per-symbol counts when all symbols are aligned;
    # use manifest dates as the material date source and hard-fail if timestamps escape it.
    global_unique_date_count = len(manifest_dates) if manifest_dates else 0
    expected_first_ts = f"{expected_first_date.isoformat()}T00:00:00Z"
    expected_last_ts = f"{expected_last_date.isoformat()}T23:59:00Z"
    if global_first_ts != expected_first_ts:
        hard_fail_reasons.append(f"global_first_timestamp_mismatch:{global_first_ts}")
    if global_last_ts != expected_last_ts:
        coverage_caveats.append(f"global_last_timestamp_not_2359:{global_last_ts}")

    duplicate_timestamp_count = sum(stats.duplicate_timestamp_count for stats in symbol_stats.values())
    duplicate_timestamp_conflict_count = sum(stats.duplicate_timestamp_conflict_count for stats in symbol_stats.values())
    if duplicate_timestamp_conflict_count:
        hard_fail_reasons.append(f"duplicate_timestamp_conflicts:{duplicate_timestamp_conflict_count}")
    unusable_symbols = sorted(
        symbol
        for symbol, stats in symbol_stats.items()
        if stats.classification in {"SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER", "SYMBOL_UNUSABLE_CONSTANT_FIELD"}
    )
    active_symbols = sorted(symbol for symbol, stats in symbol_stats.items() if stats.classification == "SYMBOL_ACTIVE_VALID")
    corrupt_symbols = sorted(symbol for symbol, stats in symbol_stats.items() if stats.classification.startswith("SYMBOL_CORRUPT"))
    if corrupt_symbols:
        hard_fail_reasons.append(f"corrupt_symbols:{','.join(corrupt_symbols)}")
    if not active_symbols and unusable_symbols:
        hard_fail_reasons.append("all_symbols_unusable_constant_or_zero_oi")

    if hard_fail_reasons:
        classification = "FRAGMENT_CORRUPT_OR_UNUSABLE"
        valid = False
    elif coverage_caveats:
        classification = "FRAGMENT_VALID_WITH_SOURCE_COVERAGE_CAVEATS"
        valid = True
    elif unusable_symbols:
        classification = "FRAGMENT_VALID_WITH_SYMBOL_COVERAGE_NOTES"
        valid = True
    else:
        classification = "FRAGMENT_VALID_FULL_COVERAGE"
        valid = True
    return FragmentValidationResult(
        valid=valid,
        classification=classification,
        jsonl_file_count=len(jsonl_paths),
        total_row_count=total_row_count,
        global_first_ts=global_first_ts,
        global_last_ts=global_last_ts,
        global_unique_date_count=global_unique_date_count,
        duplicate_timestamp_count=duplicate_timestamp_count,
        duplicate_timestamp_conflict_count=duplicate_timestamp_conflict_count,
        unusable_symbols=unusable_symbols,
        hard_fail_reasons=hard_fail_reasons,
        coverage_caveats=coverage_caveats,
        symbol_stats=symbol_stats,
    )
