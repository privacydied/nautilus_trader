"""Signal generators for the venue-agnostic signal observer."""
from typing import List, Optional, Dict, Any
import csv
import math
import time

from .models import SignalEvent
from .config import SignalSourceConfig


# -------------------------------------------------------------------
# Manual CSV signal source
# -------------------------------------------------------------------

def load_signals_from_csv(path: str, mapping: Optional[Dict[str, str]] = None) -> List[SignalEvent]:
    """Load signal events from a CSV file.

    Expected CSV columns (can be remapped via ``mapping``):
        timestamp, source_venue, source_instrument, target_venue,
        target_instrument, direction, signal_type, strength, metadata
    """
    defaults = {
        "timestamp": "timestamp",
        "source_venue": "source_venue",
        "source_instrument": "source_instrument",
        "target_venue": "target_venue",
        "target_instrument": "target_instrument",
        "direction": "direction",
        "signal_type": "signal_type",
        "strength": "strength",
        "metadata": "metadata",
    }
    if mapping:
        defaults.update(mapping)

    events: List[SignalEvent] = []
    counter = 0

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            counter += 1
            ts_val = row.get(defaults["timestamp"], "")
            try:
                ts = float(ts_val)
            except (ValueError, TypeError):
                continue

            try:
                strength = float(row.get(defaults["strength"], 0) or 0.0)
            except (ValueError, TypeError):
                strength = 0.0
            if not math.isfinite(strength):
                strength = 0.0
            metadata = {"raw": row} if defaults["metadata"] in row else None

            events.append(SignalEvent(
                signal_id=f"csv_{counter:06d}",
                timestamp=ts,
                source_venue=row.get(defaults["source_venue"], ""),
                source_instrument=row.get(defaults["source_instrument"], ""),
                target_venue=row.get(defaults["target_venue"], ""),
                target_instrument=row.get(defaults["target_instrument"], ""),
                signal_type=row.get(defaults["signal_type"], "manual_csv"),
                direction=row.get(defaults["direction"], "long"),
                strength=strength,
                metadata=metadata,
                reason="manual_csv_signal",
            ))

    return events


# -------------------------------------------------------------------
# Cross-market move signal generator
# -------------------------------------------------------------------

class CrossMarketSignalGenerator:
    """Generates signals when a source instrument moves faster than a threshold.

    This generator is strictly sequential and uses only information available
    at or before each price bar's timestamp.  No lookahead.
    """

    def __init__(self, cfg: SignalSourceConfig):
        self.source_venue = cfg.cross_market_source_venue or "BINANCE"
        self.source_instrument = cfg.cross_market_source_instrument or "BTC/USDT"
        self.target_venue = cfg.cross_market_target_venue or "KRAKEN"
        self.target_instrument = cfg.cross_market_target_instrument or "BTC/USD"
        self.threshold_bps = cfg.cross_market_move_threshold_bps
        self.lookback_seconds = cfg.cross_market_lookback_seconds
        self.cooldown_seconds = cfg.cross_market_cooldown_seconds

    def generate(
        self,
        source_timestamps: List[float],
        source_prices: List[float],
    ) -> List[SignalEvent]:
        """Generate cross-market signals from a source price series.

        source_timestamps / source_prices must be aligned, monotonically
        increasing, and represent the *same* index frequency.

        Only prices available up to and including each bar's timestamp are
        used to decide whether a signal fires.
        """
        events: List[SignalEvent] = []
        counter = 0
        last_signal_ts: Optional[float] = None

        for i in range(1, len(source_prices)):
            ts = source_timestamps[i]
            price = source_prices[i]
            if not math.isfinite(price):
                continue

            # Find the oldest price within the lookback window
            window_start = ts - self.lookback_seconds
            # Use binary search to find start index (prices are monotonic in time)
            j = i
            while j > 0 and source_timestamps[j - 1] >= window_start:
                j -= 1
            window_price = source_prices[j]

            if window_price <= 0 or not math.isfinite(window_price):
                continue

            move_bps = (price - window_price) / window_price * 10000.0

            # Cooldown check
            if last_signal_ts is not None and (ts - last_signal_ts) < self.cooldown_seconds:
                continue

            if abs(move_bps) >= self.threshold_bps:
                counter += 1
                direction = "long" if move_bps > 0 else "short"
                events.append(SignalEvent(
                    signal_id=f"cmm_{counter:06d}",
                    timestamp=ts,
                    source_venue=self.source_venue,
                    source_instrument=self.source_instrument,
                    target_venue=self.target_venue,
                    target_instrument=self.target_instrument,
                    signal_type="cross_market_move",
                    direction=direction,
                    strength=abs(move_bps),
                    reason=f"Source moved {move_bps:.1f} bps in {self.lookback_seconds:.0f}s",
                ))
                last_signal_ts = ts

        return events
