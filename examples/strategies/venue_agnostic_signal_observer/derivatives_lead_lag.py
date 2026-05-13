"""Derivatives lead-lag impulse signal generator.

Generates impulse events from derivatives-market trade-tick data and
converts them into TickSignalEvent so the existing event_study.evaluate_tick_signal
can be reused for forward-return evaluation.

**Observer-only. No execution, no orders, no private endpoints.**
"""
from __future__ import annotations

import bisect
import statistics
import uuid

from .tick_models import TickSignalEvent
from .derivatives_models import (
    DerivativeTradeTick,
    DerivativeImpulseEvent,
    OpenInterestSnapshot,
    FundingSnapshot,
)

_MS_TO_NS = 1_000_000


class DerivativesImpulseGenerator:
    """Scan sorted derivative trade ticks for impulse events."""

    def __init__(
        self,
        lookbacks_ms: list[int] | None = None,
        cooldown_ms: int = 10_000,
        source_venue: str = "BINANCE",
        target_venue: str = "KRAKEN",
        symbol: str = "BTC/USD",
        asset: str = "BTC",
        notional_zscore_threshold: float = 4.0,
        price_shock_multiplier: float = 3.0,
        imbalance_threshold: float = 0.6,
        min_trades_in_window: int = 3,
        enable_notional_burst: bool = True,
        enable_price_shock: bool = True,
        enable_signed_imbalance: bool = True,
    ) -> None:
        self.lookbacks_ms = lookbacks_ms or [1000, 5000, 10000, 30000]
        self.cooldown_ms = cooldown_ms
        self.source_venue = source_venue
        self.target_venue = target_venue
        self.symbol = symbol
        self.asset = asset
        self.notional_zscore_threshold = notional_zscore_threshold
        self.price_shock_multiplier = price_shock_multiplier
        self.imbalance_threshold = imbalance_threshold
        self.min_trades_in_window = min_trades_in_window
        self.enable_notional_burst = enable_notional_burst
        self.enable_price_shock = enable_price_shock
        self.enable_signed_imbalance = enable_signed_imbalance
        self._last_signal_ts: dict[str, int] = {}

    def generate(
        self,
        trades: list[DerivativeTradeTick],
        oi_snapshots: list[OpenInterestSnapshot] | None = None,
        funding: list[FundingSnapshot] | None = None,
    ) -> list[DerivativeImpulseEvent]:
        events: list[DerivativeImpulseEvent] = []
        if not trades:
            return events
        if self.enable_notional_burst:
            events.extend(self._notional_burst(trades))
        if self.enable_price_shock:
            events.extend(self._price_shock(trades))
        if self.enable_signed_imbalance:
            events.extend(self._signed_imbalance(trades))
        return events

    def _notional_burst(
        self, trades: list[DerivativeTradeTick]
    ) -> list[DerivativeImpulseEvent]:
        events: list[DerivativeImpulseEvent] = []
        cooldown_ns = self.cooldown_ms * _MS_TO_NS
        ts_list = [t.ts_event for t in trades]
        notionals = [t.notional for t in trades]

        for lb_ms in self.lookbacks_ms:
            param_key = f"notional_burst_{lb_ms}"
            last_ts = self._last_signal_ts.get(param_key, -1)
            lb_ns = lb_ms * _MS_TO_NS

            for idx in range(1, len(trades)):
                ts = trades[idx].ts_event
                if ts - last_ts < cooldown_ns:
                    continue

                lo = bisect.bisect_left(ts_list, ts - lb_ns, 0, idx + 1)
                window = notionals[lo:idx + 1]
                if len(window) < self.min_trades_in_window:
                    continue

                baseline_start = max(0, lo - len(window))
                baseline = notionals[baseline_start:lo]
                if len(baseline) < 2:
                    continue

                median_base = statistics.median(baseline)
                if median_base <= 0:
                    continue

                burst_ratio = sum(window) / median_base
                if burst_ratio >= self.price_shock_multiplier:
                    direction = self._resolve_direction(trades, idx, lb_ms)
                    evt = DerivativeImpulseEvent(
                        signal_id=str(uuid.uuid4()),
                        ts_event=ts,
                        source_venue=self.source_venue,
                        source_symbol=self.symbol,
                        target_venue=self.target_venue,
                        target_symbol=self.symbol,
                        asset=self.asset,
                        signal_type="notional_burst",
                        direction=direction,
                        strength=burst_ratio,
                        lookback_ms=lb_ms,
                        metadata={
                            "burst_ratio": round(burst_ratio, 4),
                            "median_notional": round(median_base, 2),
                            "window_notional": round(sum(window), 2),
                        },
                    )
                    events.append(evt)
                    last_ts = ts
                    self._last_signal_ts[param_key] = ts

        return events

    def _price_shock(
        self, trades: list[DerivativeTradeTick]
    ) -> list[DerivativeImpulseEvent]:
        events: list[DerivativeImpulseEvent] = []
        cooldown_ns = self.cooldown_ms * _MS_TO_NS
        ts_list = [t.ts_event for t in trades]

        for lb_ms in self.lookbacks_ms:
            param_key = f"price_shock_{lb_ms}"
            last_ts = self._last_signal_ts.get(param_key, -1)
            lb_ns = lb_ms * _MS_TO_NS

            for idx in range(1, len(trades)):
                ts = trades[idx].ts_event
                if ts - last_ts < cooldown_ns:
                    continue

                lo = bisect.bisect_left(ts_list, ts - lb_ns, 0, idx + 1)
                if lo == idx:
                    continue

                ref_price = trades[lo].price
                curr_price = trades[idx].price
                bps_move = abs(curr_price - ref_price) / ref_price * 10000

                baseline_start = max(0, lo - (idx - lo))
                if baseline_start >= lo:
                    continue

                baseline_abs_moves: list[float] = []
                step = max(1, (lo - baseline_start) // 10)
                window_len = idx - lo
                for s in range(baseline_start, lo, step):
                    e = min(s + window_len, lo)
                    if e > s + 1:
                        ref = trades[s].price
                        bp = abs(trades[e].price - ref) / ref * 10000
                        baseline_abs_moves.append(bp)

                if not baseline_abs_moves:
                    continue

                median_base_move = statistics.median(baseline_abs_moves)
                if median_base_move <= 0:
                    continue

                if bps_move >= median_base_move * self.price_shock_multiplier:
                    direction = "long" if curr_price >= ref_price else "short"
                    evt = DerivativeImpulseEvent(
                        signal_id=str(uuid.uuid4()),
                        ts_event=ts,
                        source_venue=self.source_venue,
                        source_symbol=self.symbol,
                        target_venue=self.target_venue,
                        target_symbol=self.symbol,
                        asset=self.asset,
                        signal_type="price_shock",
                        direction=direction,
                        strength=bps_move / median_base_move,
                        lookback_ms=lb_ms,
                        metadata={
                            "bps_move": round(bps_move, 4),
                            "median_bps_move": round(median_base_move, 4),
                        },
                    )
                    events.append(evt)
                    last_ts = ts
                    self._last_signal_ts[param_key] = ts

        return events

    def _signed_imbalance(
        self, trades: list[DerivativeTradeTick]
    ) -> list[DerivativeImpulseEvent]:
        events: list[DerivativeImpulseEvent] = []
        cooldown_ns = self.cooldown_ms * _MS_TO_NS
        ts_list = [t.ts_event for t in trades]

        for lb_ms in self.lookbacks_ms:
            param_key = f"signed_imbalance_{lb_ms}"
            last_ts = self._last_signal_ts.get(param_key, -1)
            lb_ns = lb_ms * _MS_TO_NS

            for idx in range(1, len(trades)):
                ts = trades[idx].ts_event
                if ts - last_ts < cooldown_ns:
                    continue

                lo = bisect.bisect_left(ts_list, ts - lb_ns, 0, idx + 1)
                window = trades[lo:idx + 1]
                if len(window) < self.min_trades_in_window:
                    continue

                buy_n = sum(t.notional for t in window if t.side == "buy")
                sell_n = sum(t.notional for t in window if t.side == "sell")
                total = buy_n + sell_n
                if total <= 0:
                    continue

                imbalance = (buy_n - sell_n) / total
                if abs(imbalance) < self.imbalance_threshold:
                    continue

                direction = "long" if imbalance > 0 else "short"
                evt = DerivativeImpulseEvent(
                    signal_id=str(uuid.uuid4()),
                    ts_event=ts,
                    source_venue=self.source_venue,
                    source_symbol=self.symbol,
                    target_venue=self.target_venue,
                    target_symbol=self.symbol,
                    asset=self.asset,
                    signal_type="signed_imbalance",
                    direction=direction,
                    strength=abs(imbalance),
                    lookback_ms=lb_ms,
                    metadata={
                        "buy_notional": round(buy_n, 2),
                        "sell_notional": round(sell_n, 2),
                        "imbalance": round(imbalance, 4),
                    },
                )
                events.append(evt)
                last_ts = ts
                self._last_signal_ts[param_key] = ts

        return events

    def _resolve_direction(
        self, trades: list[DerivativeTradeTick], idx: int, lookback_ms: int
    ) -> str:
        lookback_ns = lookback_ms * _MS_TO_NS
        ts = trades[idx].ts_event
        ref_idx = idx
        while ref_idx > 0 and ts - trades[ref_idx].ts_event <= lookback_ns:
            ref_idx -= 1
        ref_price = trades[ref_idx].price if ref_idx >= 0 else trades[0].price
        curr_price = trades[idx].price
        return "long" if curr_price >= ref_price else "short"


def impulse_to_tick_signal(impulse: DerivativeImpulseEvent) -> TickSignalEvent:
    """Convert a derivatives impulse event into a TickSignalEvent for existing evaluation."""
    return TickSignalEvent(
        signal_id=impulse.signal_id,
        ts_event=impulse.ts_event,
        source_venue=impulse.source_venue,
        source_symbol=impulse.source_symbol,
        target_venue=impulse.target_venue,
        target_symbol=impulse.target_symbol,
        asset=impulse.asset,
        signal_type=impulse.signal_type,
        direction=impulse.direction,
        lookback_ms=impulse.lookback_ms,
        threshold_bps=0.0,
        source_move_bps=impulse.strength * 100.0,
        source_start_price=0.0,
        source_end_price=0.0,
        strength=impulse.strength,
        metadata=impulse.metadata,
    )
