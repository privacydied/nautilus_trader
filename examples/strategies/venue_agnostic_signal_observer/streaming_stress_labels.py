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
