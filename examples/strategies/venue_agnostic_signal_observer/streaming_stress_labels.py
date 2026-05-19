"""Streaming stress label generation for cross-asset beta-lag archive.

Reads JSONL tick files line-by-line to avoid loading all ticks into memory.
"""

from __future__ import annotations

import json
import random
from collections import deque
from pathlib import Path
from typing import Any, Iterator

SEED = 42
STRESS_DEDUP_COOLDOWN_NS = 30_000_000_000  # 30 seconds


def _compute_move_bps_from_jsonl(
    jsonl_path: Path,
    lookback_ns: int,
) -> Iterator[dict[str, Any]]:
    """Compute rolling lookback moves by streaming a JSONL tick file."""
    window: deque[tuple[int, float]] = deque()

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            ts_ns = row["ts_event"]
            price = float(row["price"])

            window.append((ts_ns, price))

            cutoff = ts_ns - lookback_ns
            while window and window[0][0] < cutoff:
                window.popleft()

            if len(window) < 2:
                continue

            start_ts = window[0][0]
            start_price = window[0][1]
            end_price = price
            move_bps = ((end_price - start_price) / start_price) * 10_000 if start_price else 0.0

            yield {
                "ts_ns": ts_ns,
                "start_ts_ns": start_ts,
                "move_bps": move_bps,
                "start_price": start_price,
                "end_price": end_price,
            }


def generate_stress_labels_streaming(
    jsonl_path: Path,
    source_symbol: str,
    *,
    lookback_seconds: int,
    threshold_bps: float,
    StressLabel,
) -> list:
    """Generate stress labels by streaming a JSONL tick file."""
    lookback_ns = lookback_seconds * 1_000_000_000
    labels = []
    last_label_ts: int | None = None
    rng = random.Random(SEED)

    for mv in _compute_move_bps_from_jsonl(jsonl_path, lookback_ns):
        abs_move = abs(mv["move_bps"])
        if abs_move < threshold_bps:
            continue
        if last_label_ts is not None and (mv["ts_ns"] - last_label_ts) < STRESS_DEDUP_COOLDOWN_NS:
            continue
        direction = "bullish" if mv["move_bps"] > 0 else "bearish"
        window_id = f"iw_{source_symbol}_{lookback_seconds}s_{mv['start_ts_ns']}_{mv['ts_ns']}_{rng.randint(0, 999999):06d}"

        label = StressLabel(
            label_id=f"sl_{source_symbol}_{lookback_seconds}s_{mv['ts_ns']}_{rng.randint(0, 999999):06d}",
            source_symbol=source_symbol,
            stress_start_ns=mv["start_ts_ns"],
            stress_end_ns=mv["ts_ns"],
            stress_window_seconds=lookback_seconds,
            source_move_bps=mv["move_bps"],
            direction=direction,
            source_start_price=mv["start_price"],
            source_end_price=mv["end_price"],
            independent_window_id=window_id,
            rule_name=f"{lookback_seconds}s_{int(threshold_bps)}bps",
        )
        labels.append(label)
        last_label_ts = mv["ts_ns"]

    return labels


def compute_forward_returns_from_ts_prices(
    stress_label,
    timestamps: list,
    prices: list,
    target_symbol: str,
    horizons_ms: list,
    TickForwardReturn,
    VENUE,
    ENTRY_DELAY_NS,
    MS_TO_NS,
    FEE_BPS,
    SLIPPAGE_BPS,
    QUOTE_MISMATCH_BUFFER_BPS,
) -> list:
    """Compute forward returns using pre-extracted (timestamps, prices) arrays.

    Identical to compute_forward_returns_for_stress but accepts parallel
    arrays instead of TradeTickLite objects. Much more memory-efficient
    for large target tick sets.
    """
    from bisect import bisect_left  # noqa: PLC0415

    entry_ts_ns = stress_label.stress_end_ns + ENTRY_DELAY_NS
    results = []

    entry_idx = bisect_left(timestamps, entry_ts_ns)
    if entry_idx >= len(timestamps):
        for h in horizons_ms:
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=h,
                entry_reference_price=None,
                forward_price=None,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="no_entry_reference_price",
            ))
        return results

    entry_price = prices[entry_idx]

    for horizon_ms in horizons_ms:
        horizon_ns = entry_ts_ns + horizon_ms * MS_TO_NS
        exit_idx = bisect_left(timestamps, horizon_ns)

        if exit_idx >= len(timestamps):
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=None,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="no_forward_price",
            ))
            continue

        forward_price = prices[exit_idx]
        if entry_price <= 0 or not math.isfinite(entry_price):
            raw_return_bps = 0.0
        else:
            raw_return_bps = ((forward_price - entry_price) / entry_price) * 10_000

        direction = stress_label.direction
        if direction == "bearish":
            adjusted_return = -raw_return_bps
        else:
            adjusted_return = raw_return_bps

        total_cost = FEE_BPS + SLIPPAGE_BPS + QUOTE_MISMATCH_BUFFER_BPS
        net_return = adjusted_return - total_cost

        results.append(TickForwardReturn(
            signal_id=stress_label.label_id,
            signal_ts=entry_ts_ns,
            target_venue=VENUE,
            target_symbol=target_symbol,
            horizon_ms=horizon_ms,
            entry_reference_price=entry_price,
            forward_price=forward_price,
            raw_return_bps=raw_return_bps,
            direction_adjusted_return_bps=adjusted_return,
            fee_bps=FEE_BPS,
            slippage_bps=SLIPPAGE_BPS,
            quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
            net_return_bps=net_return,
            valid=True,
        ))

    return results


def generate_baseline_from_ts_prices(
    target_data: dict,
    source_symbol: str,
    label_count: int,
    *,
    seed: int,
    TickForwardReturn,
    VENUE,
    ENTRY_DELAY_NS,
    MS_TO_NS,
    FEE_BPS,
    SLIPPAGE_BPS,
    QUOTE_MISMATCH_BUFFER_BPS,
    HORIZONS_MS,
) -> list:
    """Generate baseline forward returns from (timestamps, prices) arrays."""
    import random as _random  # noqa: PLC0415
    from bisect import bisect_left  # noqa: PLC0415

    if not target_data:
        return []

    rng = _random.Random(seed)

    all_ts = []
    all_prices_by_ts = {}
    for sym, (ts_arr, pr_arr) in target_data.items():
        if ts_arr:
            all_ts.extend(ts_arr)
            for t, p in zip(ts_arr, pr_arr):
                all_prices_by_ts[t] = p

    if not all_ts:
        return []

    min_ts = min(all_ts)
    max_ts = max(all_ts)
    sorted_ts = sorted(all_ts)

    baseline_frs = []
    for i in range(label_count):
        entry_ts_ns = rng.randint(min_ts, max_ts)
        # Find closest tick at or after entry_ts_ns
        idx = bisect_left(sorted_ts, entry_ts_ns)
        if idx >= len(sorted_ts):
            continue
        actual_entry_ts = sorted_ts[idx]
        entry_price = all_prices_by_ts.get(actual_entry_ts)
        if entry_price is None or entry_price <= 0 or not math.isfinite(entry_price):
            continue

        total_cost = FEE_BPS + SLIPPAGE_BPS + QUOTE_MISMATCH_BUFFER_BPS

        for horizon_ms in HORIZONS_MS:
            horizon_ns = actual_entry_ts + horizon_ms * MS_TO_NS
            exit_idx = bisect_left(sorted_ts, horizon_ns)
            if exit_idx >= len(sorted_ts):
                continue
            forward_price = all_prices_by_ts.get(sorted_ts[exit_idx])
            if forward_price is None:
                continue
            raw_return = ((forward_price - entry_price) / entry_price) * 10_000
            net_return = raw_return - total_cost

            baseline_frs.append(TickForwardReturn(
                signal_id=f"baseline_{source_symbol}_{i}_{horizon_ms}",
                signal_ts=actual_entry_ts,
                target_venue=VENUE,
                target_symbol="BASELINE",
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=forward_price,
                raw_return_bps=raw_return,
                direction_adjusted_return_bps=raw_return,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                net_return_bps=net_return,
                valid=True,
            ))

    return baseline_frs


def check_target_coverage_from_ts_prices(
    target_data: dict,
    entry_ns: int,
    max_horizon_ns: int,
    buffer_ns: int = 5_000_000_000,
) -> tuple:
    """Check target coverage using (timestamps, prices) arrays.

    Returns (all_covered, coverage_map).
    """
    horizon_end = entry_ns + max_horizon_ns + buffer_ns
    coverage = {}

    for sym, (ts_arr, pr_arr) in target_data.items():
        if not ts_arr:
            coverage[sym] = {
                "symbol": sym, "tick_count": 0,
                "first_ts_ns": 0, "last_ts_ns": 0,
                "has_coverage": False,
            }
            continue

        first_ts = ts_arr[0]
        last_ts = ts_arr[-1]
        has_coverage = first_ts <= entry_ns and last_ts >= horizon_end

        if has_coverage:
            from bisect import bisect_right, bisect_left  # noqa: PLC0415
            left = bisect_left(ts_arr, entry_ns)
            right = bisect_right(ts_arr, horizon_end)
            tick_count = right - left
            if tick_count == 0:
                has_coverage = False
        else:
            tick_count = 0

        coverage[sym] = {
            "symbol": sym, "tick_count": tick_count,
            "first_ts_ns": first_ts, "last_ts_ns": last_ts,
            "has_coverage": has_coverage,
        }

    all_covered = all(v["has_coverage"] for v in coverage.values())
    return all_covered, coverage
