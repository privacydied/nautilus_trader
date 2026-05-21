"""
Deterministic offline stress-label generation for Edge Miner corpora.

This module labels stress windows from already-loaded local tick data only. It has
no external I/O beyond the caller writing artifacts, and it contains no execution
or venue connectivity logic.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import math
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Sequence

from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite


LABEL_VERSION = "stress_label.v1"
SOURCE_ASSETS = ("BTC", "ETH")
_NS_PER_SECOND = 1_000_000_000
MIN_LABEL_SPAN_SECONDS = 60
MOVE_30S_THRESHOLD_BPS = 30.0
MOVE_60S_THRESHOLD_BPS = 50.0
RANGE_THRESHOLD_BPS = 75.0
LABEL_COOLDOWN_SECONDS = 30


@dataclass(frozen=True)
class StressLabel:
    label_id: str
    window_start_utc: str
    window_end_utc: str
    source_asset: str
    trigger_reason: str
    impulse_bps: float
    realized_vol_bps: float | None
    range_bps: float | None
    stress_score: float
    label_version: str = LABEL_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StressLabelResult:
    status: str
    labels: tuple[StressLabel, ...]
    label_count: int
    source_assets_evaluated: tuple[str, ...]
    reason: str | None = None

    def summary(self) -> dict:
        return {
            "schema_version": "stress_label_summary.v1",
            "status": self.status,
            "label_count": self.label_count,
            "source_assets_evaluated": list(self.source_assets_evaluated),
            "reason": self.reason,
            "label_version": LABEL_VERSION,
        }


def _utc_iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / _NS_PER_SECOND, tz=UTC).isoformat()


def _label_id(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _move_bps(start_price: float, end_price: float) -> float | None:
    if start_price <= 0:
        return None
    return (end_price / start_price - 1.0) * 10_000


def _realized_vol_bps(prices: Sequence[float]) -> float | None:
    if len(prices) < 3:
        return None
    returns = []
    for prev, cur in itertools.pairwise(prices):
        if prev > 0:
            returns.append((cur / prev - 1.0) * 10_000)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(var), 6)


def _range_bps(prices: Sequence[float]) -> float | None:
    if len(prices) < 2:
        return None
    low = min(prices)
    high = max(prices)
    if low <= 0:
        return None
    return round((high / low - 1.0) * 10_000, 6)


def _price_at_or_before(ticks: Sequence[TradeTickLite], times: Sequence[int], ts_ns: int) -> tuple[int, float] | None:
    idx = bisect.bisect_right(times, ts_ns) - 1
    if idx < 0:
        return None
    return idx, ticks[idx].price


def _window_prices(ticks: Sequence[TradeTickLite], times: Sequence[int], start_ns: int, end_ns: int) -> list[float]:
    start_idx = bisect.bisect_left(times, start_ns)
    end_idx = bisect.bisect_right(times, end_ns)
    return [t.price for t in ticks[start_idx:end_idx] if t.price > 0]


def _labels_for_asset(asset: str, ticks: Sequence[TradeTickLite]) -> list[StressLabel]:
    ordered = sorted((t for t in ticks if t.price > 0), key=lambda t: t.ts_event)
    if len(ordered) < 3:
        return []
    times = [t.ts_event for t in ordered]
    if times[-1] - times[0] < MIN_LABEL_SPAN_SECONDS * _NS_PER_SECOND:
        return []

    labels: list[StressLabel] = []
    cooldown_until = -1
    for tick in ordered:
        if tick.ts_event < cooldown_until:
            continue

        checks: list[tuple[str, int, float]] = [
            ("ABS_30S_MOVE", 30, MOVE_30S_THRESHOLD_BPS),
            ("ABS_60S_MOVE", 60, MOVE_60S_THRESHOLD_BPS),
        ]
        trigger_reason = None
        impulse = 0.0
        window_seconds = 60

        for reason, seconds, threshold in checks:
            ref = _price_at_or_before(ordered, times, tick.ts_event - seconds * _NS_PER_SECOND)
            if ref is None:
                continue
            _ref_idx, ref_price = ref
            move = _move_bps(ref_price, tick.price)
            if move is not None and abs(move) >= threshold:
                trigger_reason = reason
                impulse = move
                window_seconds = seconds
                break

        prices_60s = _window_prices(
            ordered,
            times,
            tick.ts_event - 60 * _NS_PER_SECOND,
            tick.ts_event,
        )
        range_60s = _range_bps(prices_60s)
        if trigger_reason is None and range_60s is not None and range_60s >= RANGE_THRESHOLD_BPS:
            trigger_reason = "ROLLING_RANGE_60S"
            first_price = prices_60s[0]
            impulse = _move_bps(first_price, tick.price) or 0.0
            window_seconds = 60

        if trigger_reason is None:
            continue

        vol = _realized_vol_bps(prices_60s)
        abs_impulse = abs(impulse)
        components = [abs_impulse / MOVE_30S_THRESHOLD_BPS]
        if range_60s is not None:
            components.append(range_60s / RANGE_THRESHOLD_BPS)
        if vol is not None:
            components.append(vol / 10.0)
        stress_score = round(max(components), 6)
        window_start = tick.ts_event - window_seconds * _NS_PER_SECOND
        base = {
            "source_asset": asset,
            "window_start_utc": _utc_iso(window_start),
            "window_end_utc": _utc_iso(tick.ts_event),
            "trigger_reason": trigger_reason,
            "impulse_bps": round(impulse, 6),
            "label_version": LABEL_VERSION,
        }
        labels.append(StressLabel(
            label_id=_label_id(base),
            window_start_utc=base["window_start_utc"],
            window_end_utc=base["window_end_utc"],
            source_asset=asset,
            trigger_reason=trigger_reason,
            impulse_bps=round(impulse, 6),
            realized_vol_bps=vol,
            range_bps=range_60s,
            stress_score=stress_score,
        ))
        cooldown_until = tick.ts_event + LABEL_COOLDOWN_SECONDS * _NS_PER_SECOND

    return labels


def build_stress_labels(ticks_by_asset: dict[str, list[TradeTickLite]]) -> StressLabelResult:
    """Build deterministic stress labels from already-loaded BTC/ETH ticks."""
    available_sources = tuple(asset for asset in SOURCE_ASSETS if ticks_by_asset.get(asset))
    if not available_sources:
        return StressLabelResult(
            status="NO_STRESS_LABELS_DIAGNOSTIC",
            labels=(),
            label_count=0,
            source_assets_evaluated=(),
            reason="No BTC/ETH source ticks available for stress labeling.",
        )

    enough_span = False
    labels: list[StressLabel] = []
    for asset in available_sources:
        ticks = ticks_by_asset[asset]
        ordered = sorted(ticks, key=lambda t: t.ts_event)
        if len(ordered) >= 3 and ordered[-1].ts_event - ordered[0].ts_event >= MIN_LABEL_SPAN_SECONDS * _NS_PER_SECOND:
            enough_span = True
        labels.extend(_labels_for_asset(asset, ticks))

    labels.sort(key=lambda label: (label.window_start_utc, label.source_asset, label.label_id))
    if labels:
        return StressLabelResult(
            status="STRESS_LABELS_AVAILABLE",
            labels=tuple(labels),
            label_count=len(labels),
            source_assets_evaluated=available_sources,
        )
    if not enough_span:
        return StressLabelResult(
            status="INSUFFICIENT_STRESS_DATA",
            labels=(),
            label_count=0,
            source_assets_evaluated=available_sources,
            reason="BTC/ETH source ticks do not span the minimum 60 second stress-label window.",
        )
    return StressLabelResult(
        status="NO_STRESS_LABELS_DIAGNOSTIC",
        labels=(),
        label_count=0,
        source_assets_evaluated=available_sources,
        reason="No BTC/ETH windows crossed deterministic stress thresholds.",
    )
