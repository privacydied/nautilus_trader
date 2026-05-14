"""Lead-lag signal generator and random baseline.

Generates signal events when a source venue price moves beyond a threshold
within a lookback window, then measures whether the target venue follows.

Includes a random baseline that places the same number of events at random
timestamps for the same data window — a signal is only interesting if it
beats random at the same frequency.
"""

import math
import random
from dataclasses import dataclass, field

from .models import SignalEvent


@dataclass
class LeadLagConfig:
    """Parameters for the lead-lag experiment sweep."""
    lookback_windows: list[float] = field(default_factory=lambda: [10.0, 30.0, 60.0])
    move_thresholds_bps: list[float] = field(default_factory=lambda: [5.0, 10.0, 20.0])
    cooldown_seconds: float = 30.0


def generate_lead_lag_signals(
    source_timestamps: list[float],
    source_prices: list[float],
    source_venue: str,
    source_instrument: str,
    target_venue: str,
    target_instrument: str,
    lookback_seconds: float,
    move_threshold_bps: float,
    cooldown_seconds: float = 30.0,
) -> list[SignalEvent]:
    """Generate signals when source venue moves beyond the threshold.

    A signal fires when the price change over the lookback window exceeds
    move_threshold_bps in either direction.  Cooldown prevents double-counting.

    Args:
        source_timestamps: sorted unix second timestamps for the source venue
        source_prices: close prices aligned with timestamps
        source_venue: e.g. "BINANCE"
        source_instrument: e.g. "BTC/USDT"
        target_venue: e.g. "KRAKEN"
        target_instrument: e.g. "BTC/USD"
        lookback_seconds: how far back to measure the move
        move_threshold_bps: minimum move in basis points to trigger a signal
        cooldown_seconds: minimum gap between consecutive signals

    Returns:
        List of SignalEvent objects — one per qualifying move.
    """
    signals: list[SignalEvent] = []
    last_signal_ts: float | None = None
    counter = 0

    for i in range(1, len(source_prices)):
        ts = source_timestamps[i]
        price = source_prices[i]
        if not math.isfinite(price):
            continue

        # Find the oldest data point still within the lookback window
        window_start = ts - lookback_seconds
        j = i - 1
        while j >= 0 and source_timestamps[j] >= window_start:
            j -= 1
        # j now points OUTSIDE the window (or -1); use j+1 for the first tick inside
        j = j + 1 if j + 1 < len(source_prices) else 0

        ref_price = source_prices[j]
        if ref_price <= 0 or not math.isfinite(ref_price):
            continue

        move_bps = (price - ref_price) / ref_price * 10000.0

        # Cooldown
        if last_signal_ts is not None and (ts - last_signal_ts) < cooldown_seconds:
            continue

        if abs(move_bps) >= move_threshold_bps:
            counter += 1
            direction = "long" if move_bps > 0 else "short"
            signals.append(SignalEvent(
                signal_id=f"ll_{counter:06d}",
                timestamp=ts,
                source_venue=source_venue,
                source_instrument=source_instrument,
                target_venue=target_venue,
                target_instrument=target_instrument,
                signal_type="lead_lag_move",
                direction=direction,
                strength=abs(move_bps),
                reason=(
                    f"Source moved {move_bps:+.1f} bps in "
                    f"{lookback_seconds:.0f}s (threshold={move_threshold_bps:.0f} bps)"
                ),
            ))
            last_signal_ts = ts

    return signals


def generate_random_baseline(
    source_timestamps: list[float],
    signal_count: int,
    source_venue: str,
    source_instrument: str,
    target_venue: str,
    target_instrument: str,
    seed: int = 42,
) -> list[SignalEvent]:
    """Random baseline: same number of events, random timestamps from the data window.

    Each random event is assigned a random direction (long/short) to match the
    symmetry of the real signal generator.

    Args:
        source_timestamps: all available timestamps (defines the data window)
        signal_count: how many random events to generate
        source_venue: passed through to SignalEvent
        source_instrument: passed through to SignalEvent
        target_venue: passed through to SignalEvent
        target_instrument: passed through to SignalEvent
        seed: random seed for reproducibility

    Returns:
        List of SignalEvent objects with random timestamps and directions.
    """
    if not source_timestamps or signal_count <= 0:
        return []

    rng = random.Random(seed)
    ts_min = source_timestamps[0]
    ts_max = source_timestamps[-1]
    span = ts_max - ts_min

    if span <= 0:
        return []

    signals: list[SignalEvent] = []
    used_ts: set[int] = set()

    for i in range(signal_count):
        # Pick a random offset within the data window
        offset = rng.uniform(0, span)
        ts_key = int(ts_min + offset)
        # Avoid exact duplicates
        while ts_key in used_ts and len(used_ts) < int(span):
            offset = rng.uniform(0, span)
            ts_key = int(ts_min + offset)
        used_ts.add(ts_key)
        ts = ts_min + offset
        direction = rng.choice(["long", "short"])

        signals.append(SignalEvent(
            signal_id=f"rnd_{i+1:06d}",
            timestamp=ts,
            source_venue=source_venue,
            source_instrument=source_instrument,
            target_venue=target_venue,
            target_instrument=target_instrument,
            signal_type="random_baseline",
            direction=direction,
            strength=0.0,
            reason="random_baseline_event",
        ))

    return signals
