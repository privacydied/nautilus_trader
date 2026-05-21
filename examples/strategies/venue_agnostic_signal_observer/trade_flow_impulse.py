"""
Trade-flow impulse signal generator.

**RESEARCH MEASUREMENT TOOL ONLY.**  Scans source-venue trade ticks for
abnormal flow (count burst, notional burst, large trades, signed imbalance)
and emits :class:`~.tick_models.TickSignalEvent` records.  There is **no
execution logic, no order submission, no position tracking, and no
live-trading code** anywhere in this module.

Signal types
------------
- ``count_burst``: trade-count in rolling lookback > rolling-median count × multiplier
- ``notional_burst``: notional-volume in rolling lookback > rolling-median notional × multiplier
- ``large_trade``: single trade notional >= configured minimum or > rolling-median × multiplier
- ``signed_imbalance``: (buy_notional - sell_notional) / total_notional exceeds threshold
"""

from __future__ import annotations

import math
import statistics
import uuid
from bisect import bisect_left
from dataclasses import dataclass

from .tick_models import TickSignalEvent
from .tick_models import TradeTickLite


def _finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _finite_notional(price: float, size: float) -> float:
    if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
        return 0.0
    return price * size

_MS_TO_NS = 1_000_000
_VALID_SIGNAL_TYPES = {"count_burst", "notional_burst", "large_trade", "signed_imbalance"}


# -- Config -----------------------------------------------------------------

@dataclass(frozen=True)
class TradeFlowImpulseConfig:
    """Parameters for the trade-flow impulse signal generator."""

    # Core
    source_venue: str = "BINANCE"
    target_venue: str = "KRAKEN"
    symbol: str = "BTC/USD"
    asset: str = "BTC"

    # Flow lookbacks to scan
    flow_lookbacks_ms: list[int] | None = None  # e.g. [1000, 5000, 10000, 30000]
    # Rolling baseline window for medians
    baseline_window_ms: int = 60_000

    # Which signal types to emit
    signal_types: list[str] | None = None  # e.g. ["count_burst","notional_burst","large_trade","signed_imbalance"]

    # Thresholds
    count_burst_multiplier: float = 3.0
    notional_burst_multiplier: float = 3.0
    min_trades_in_window: int = 3
    large_trade_min_notional_usd: float = 0.0       # 0 = always enabled
    large_trade_multiplier: float = 5.0
    imbalance_threshold: float = 0.65               # [-1..1], 0.65 means 82.5% one-sided

    # Tick-rule side proxy
    enable_tick_rule_side_proxy: bool = False

    # Cooldown
    cooldown_ms: int = 10_000

    @property
    def validated_signal_types(self) -> list[str]:
        types = self.signal_types if self.signal_types else list(_VALID_SIGNAL_TYPES)
        return [t for t in types if t in _VALID_SIGNAL_TYPES]


# -- Helper: tick-rule side proxy -------------------------------------------

def _infer_tick_rule_side(
    ticks: list[TradeTickLite],
    idx: int,
) -> str:
    """
    Infer aggressor side using the tick rule (Lee-Ready proxy).

    If the current trade price is >= the previous trade price, classify as
    ``"buy"``; otherwise ``"sell"``.  For the very first tick, returns
    ``"unknown"``.

    This is a clearly labelled proxy, **not** the exchange's true aggressor
    side.  When the exchange already reports a side, this function is not
    used.
    """
    if idx <= 0:
        return "unknown"
    if not math.isfinite(ticks[idx].price) or not math.isfinite(ticks[idx - 1].price):
        return "unknown"
    if ticks[idx].price >= ticks[idx - 1].price:
        return "buy"
    return "sell"


# -- Sliding-window flow scanner --------------------------------------------

class TradeFlowImpulseSignalGenerator:
    """
    Scan sorted source-venue trade ticks for trade-flow impulse signals.

    The generator walks through the trade stream once and, at every tick
    timestamp, evaluates each enabled signal type against a rolling baseline
    window.  It emits a :class:`TickSignalEvent` whenever a threshold is
    breached (subject to cooldown).

    **No lookahead**: only data at or before the current tick's timestamp
    is used to decide whether to emit a signal.
    """

    def __init__(self, config: TradeFlowImpulseConfig) -> None:
        self.config = config
        self._last_signal_ts: dict[str, int] = {}  # per signal-type key

    # -- public API --------------------------------------------------------

    def generate(self, trades: list[TradeTickLite]) -> list[TickSignalEvent]:
        """
        Generate signals from a sorted list of source-venue trade ticks.

        Parameters
        ----------
        trades :
            Must be sorted by ``ts_event`` ascending.

        Returns
        -------
        list[TickSignalEvent]
        """
        if not trades:
            return []

        events: list[TickSignalEvent] = []
        signal_types = self.config.validated_signal_types

        if "count_burst" in signal_types:
            events.extend(self._count_burst(trades))
        if "notional_burst" in signal_types:
            events.extend(self._notional_burst(trades))
        if "large_trade" in signal_types:
            events.extend(self._large_trade(trades))
        if "signed_imbalance" in signal_types:
            events.extend(self._signed_imbalance(trades))

        return events

    def generate_by_type(
        self, trades: list[TradeTickLite], signal_type: str
    ) -> list[TickSignalEvent]:
        """
        Generate signals for a single signal type.

        Useful for the runner to iterate over signal types independently,
        applying per-type parameter sweeps.
        """
        if signal_type == "count_burst":
            return self._count_burst(trades)
        if signal_type == "notional_burst":
            return self._notional_burst(trades)
        if signal_type == "large_trade":
            return self._large_trade(trades)
        if signal_type == "signed_imbalance":
            return self._signed_imbalance(trades)
        return []

    # -- internal: window helpers ------------------------------------------

    def _window_indices(
        self, trades: list[TradeTickLite], idx: int, window_ms: int
    ) -> tuple[int, int]:
        """Return (start, end) indices inclusive for [t - window_ms, t]."""
        window_ns = window_ms * _MS_TO_NS
        t = trades[idx].ts_event
        lo = bisect_left(
            [t.ts_event for t in trades], t - window_ns, 0, idx + 1
        )
        return lo, idx

    def _rolling_baselines(
        self,
        trade_timestamps: list[int],
        trade_values: list[float],
        idx: int,
        baseline_window_ms: int,
        value_name: str | None = None,
    ) -> tuple[float, list[float]]:
        """Return rolling median and full list for the baseline window."""
        baseline_ns = baseline_window_ms * _MS_TO_NS
        t = trade_timestamps[idx]
        lo = bisect_left(trade_timestamps, t - baseline_ns, 0, idx)
        window_vals = trade_values[lo:idx]
        if len(window_vals) >= 2:
            median_val = statistics.median(window_vals)
        elif len(window_vals) == 1:
            median_val = window_vals[0]
        else:
            median_val = 0.0
        return median_val, window_vals

    # -- signal: count burst -----------------------------------------------

    def _count_burst(self, trades: list[TradeTickLite]) -> list[TickSignalEvent]:
        events: list[TickSignalEvent] = []
        cooldown_ns = self.config.cooldown_ms * _MS_TO_NS

        for lookback_ms in self.config.flow_lookbacks_ms or []:
            param_key = f"count_burst_{lookback_ms}"
            last_ts = self._last_signal_ts.get(param_key, -1)
            lookback_ns = lookback_ms * _MS_TO_NS
            timestamps = [t.ts_event for t in trades]

            for idx in range(1, len(trades)):
                ts = trades[idx].ts_event
                if ts - last_ts < cooldown_ns:
                    continue

                # lookback window
                lb_lo = bisect_left(timestamps, ts - lookback_ns, 0, idx + 1)
                count = idx - lb_lo + 1

                if count < self.config.min_trades_in_window:
                    continue

                # baseline window
                bl_ns = self.config.baseline_window_ms * _MS_TO_NS
                bl_lo = bisect_left(timestamps, ts - bl_ns, 0, idx)
                # count baseline trades per lookback-sized sub-windows
                # Approximate: count of trades in baseline_window_ms
                idx - bl_lo
                # Compute rolling median count per lookback_ms-sized slice
                # Using a simpler approach: median of per-second counts
                if idx - bl_lo > 0:
                    per_lb_counts: list[float] = []
                    step = max(1, (idx - bl_lo) // 10)  # up to 10 samples
                    for s in range(bl_lo, idx, step):
                        e = min(s + count, idx)
                        per_lb_counts.append(e - s + 1)
                    median_count = statistics.median(per_lb_counts) if per_lb_counts else 0.0
                else:
                    median_count = 0.0

                if median_count <= 0:
                    continue

                burst_ratio = count / median_count
                if burst_ratio >= self.config.count_burst_multiplier:
                    direction = self._resolve_direction(
                        trades, idx, lookback_ms, "count_burst"
                    )
                    evt = TickSignalEvent(
                        signal_id=str(uuid.uuid4()),
                        ts_event=ts,
                        source_venue=self.config.source_venue,
                        source_symbol=self.config.symbol,
                        target_venue=self.config.target_venue,
                        target_symbol=self.config.symbol,
                        asset=self.config.asset,
                        signal_type="trade_flow_impulse",
                        direction=direction,
                        lookback_ms=lookback_ms,
                        threshold_bps=0.0,
                        source_move_bps=0.0,
                        source_start_price=0.0,
                        source_end_price=0.0,
                        strength=burst_ratio,
                        metadata={
                            "flow_signal_type": "count_burst",
                            "lookback_ms": lookback_ms,
                            "baseline_window_ms": self.config.baseline_window_ms,
                            "trade_count": count,
                            "median_count": round(median_count, 2),
                            "burst_ratio": round(burst_ratio, 4),
                            "side_source": self._side_source(),
                        },
                    )
                    events.append(evt)
                    last_ts = ts
                    self._last_signal_ts[param_key] = ts

        return events

    # -- signal: notional burst --------------------------------------------

    def _notional_burst(self, trades: list[TradeTickLite]) -> list[TickSignalEvent]:
        events: list[TickSignalEvent] = []
        cooldown_ns = self.config.cooldown_ms * _MS_TO_NS

        for lookback_ms in self.config.flow_lookbacks_ms or []:
            param_key = f"notional_burst_{lookback_ms}"
            last_ts = self._last_signal_ts.get(param_key, -1)
            lookback_ns = lookback_ms * _MS_TO_NS
            timestamps = [t.ts_event for t in trades]
            notionals = [_finite_notional(t.price, t.size) for t in trades]

            for idx in range(1, len(trades)):
                ts = trades[idx].ts_event
                if ts - last_ts < cooldown_ns:
                    continue

                # lookback window
                lb_lo = bisect_left(timestamps, ts - lookback_ns, 0, idx + 1)
                window_notional = sum(notionals[lb_lo:idx + 1])

                # baseline window
                bl_ns = self.config.baseline_window_ms * _MS_TO_NS
                bl_lo = bisect_left(timestamps, ts - bl_ns, 0, idx)
                bl_vals = [sum(notionals[s:min(s + max(1, idx - lb_lo), idx + 1)])
                           for s in range(bl_lo, idx, max(1, (idx - bl_lo) // 10))]
                bl_vals = [v for v in bl_vals if math.isfinite(v) and v > 0]
                median_notional = statistics.median(bl_vals) if len(bl_vals) >= 1 else 0.0

                if median_notional <= 0 or not math.isfinite(window_notional):
                    continue

                burst_ratio = window_notional / median_notional
                if not math.isfinite(burst_ratio):
                    continue
                if burst_ratio >= self.config.notional_burst_multiplier:
                    direction = self._resolve_direction(
                        trades, idx, lookback_ms, "notional_burst"
                    )
                    evt = TickSignalEvent(
                        signal_id=str(uuid.uuid4()),
                        ts_event=ts,
                        source_venue=self.config.source_venue,
                        source_symbol=self.config.symbol,
                        target_venue=self.config.target_venue,
                        target_symbol=self.config.symbol,
                        asset=self.config.asset,
                        signal_type="trade_flow_impulse",
                        direction=direction,
                        lookback_ms=lookback_ms,
                        threshold_bps=0.0,
                        source_move_bps=0.0,
                        source_start_price=0.0,
                        source_end_price=0.0,
                        strength=burst_ratio,
                        metadata={
                            "flow_signal_type": "notional_burst",
                            "lookback_ms": lookback_ms,
                            "baseline_window_ms": self.config.baseline_window_ms,
                            "notional": round(window_notional, 2),
                            "median_notional": round(median_notional, 2),
                            "burst_ratio": round(burst_ratio, 4),
                            "side_source": self._side_source(),
                        },
                    )
                    events.append(evt)
                    self._last_signal_ts[param_key] = ts
                    last_ts = ts

        return events

    # -- signal: large trade -----------------------------------------------

    def _large_trade(self, trades: list[TradeTickLite]) -> list[TickSignalEvent]:
        events: list[TickSignalEvent] = []
        cooldown_ns = self.config.cooldown_ms * _MS_TO_NS
        param_key = "large_trade"
        last_ts = self._last_signal_ts.get(param_key, -1)

        notionals = [_finite_notional(t.price, t.size) for t in trades]

        # Rolling median for multiplier check
        window_notionals: list[float] = []
        window_ts: list[int] = []
        bl_ns = self.config.baseline_window_ms * _MS_TO_NS

        for idx, trade in enumerate(trades):
            ts = trade.ts_event
            notional = notionals[idx]
            if notional <= 0 or not math.isfinite(notional):
                continue

            # Maintain rolling window
            while window_ts and ts - window_ts[0] > bl_ns:
                window_notionals.pop(0)
                window_ts.pop(0)
            window_notionals.append(notional)
            window_ts.append(ts)

            if ts - last_ts < cooldown_ns:
                continue

            # Check absolute threshold
            if self.config.large_trade_min_notional_usd > 0:
                if notional < self.config.large_trade_min_notional_usd:
                    continue

            # Check multiplier against rolling median
            if len(window_notionals) >= 5:
                finite_window = [n for n in window_notionals if math.isfinite(n) and n > 0]
                if len(finite_window) < 5:
                    continue
                median_n = statistics.median(finite_window)
                if median_n > 0 and notional < median_n * self.config.large_trade_multiplier:
                    continue

            if trade.side in ("buy", "sell"):
                # Map exchange side terms to our internal direction convention
                direction = "long" if trade.side == "buy" else "short"
            else:
                direction = self._resolve_direction(
                    trades, idx, 1000, "large_trade"
                )

            evt = TickSignalEvent(
                signal_id=str(uuid.uuid4()),
                ts_event=ts,
                source_venue=self.config.source_venue,
                source_symbol=self.config.symbol,
                target_venue=self.config.target_venue,
                target_symbol=self.config.symbol,
                asset=self.config.asset,
                signal_type="trade_flow_impulse",
                direction=direction,
                lookback_ms=0,
                threshold_bps=0.0,
                source_move_bps=0.0,
                source_start_price=trade.price,
                source_end_price=trade.price,
                strength=notional,
                metadata={
                    "flow_signal_type": "large_trade",
                    "large_trade_notional": round(notional, 2),
                    "side_source": self._side_source(),
                },
            )
            events.append(evt)
            last_ts = ts
            self._last_signal_ts[param_key] = ts

        return events

    # -- signal: signed imbalance ------------------------------------------

    def _signed_imbalance(self, trades: list[TradeTickLite]) -> list[TickSignalEvent]:
        events: list[TickSignalEvent] = []
        cooldown_ns = self.config.cooldown_ms * _MS_TO_NS

        for lookback_ms in self.config.flow_lookbacks_ms or []:
            param_key = f"signed_imbalance_{lookback_ms}"
            last_ts = self._last_signal_ts.get(param_key, -1)
            lookback_ns = lookback_ms * _MS_TO_NS
            timestamps = [t.ts_event for t in trades]

            for idx in range(1, len(trades)):
                ts = trades[idx].ts_event
                if ts - last_ts < cooldown_ns:
                    continue

                lb_lo = bisect_left(timestamps, ts - lookback_ns, 0, idx + 1)
                window = trades[lb_lo:idx + 1]

                if len(window) < self.config.min_trades_in_window:
                    continue

                buy_n = 0.0
                sell_n = 0.0
                for i, t_trade in enumerate(window):
                    n = _finite_notional(t_trade.price, t_trade.size)
                    if n <= 0:
                        continue
                    side = t_trade.side
                    if side == "buy":
                        buy_n += n
                    elif side == "sell":
                        sell_n += n
                    elif self.config.enable_tick_rule_side_proxy:
                        global_idx = lb_lo + i
                        inferred = _infer_tick_rule_side(trades, global_idx)
                        if inferred == "buy":
                            buy_n += n
                        elif inferred == "sell":
                            sell_n += n
                        else:
                            continue  # unknown, skip this contribution
                    else:
                        continue  # unknown side, skip

                total_n = buy_n + sell_n
                if total_n <= 0 or not math.isfinite(total_n):
                    continue

                imbalance = (buy_n - sell_n) / total_n

                if abs(imbalance) >= self.config.imbalance_threshold:
                    direction = "long" if imbalance > 0 else "short"
                    evt = TickSignalEvent(
                        signal_id=str(uuid.uuid4()),
                        ts_event=ts,
                        source_venue=self.config.source_venue,
                        source_symbol=self.config.symbol,
                        target_venue=self.config.target_venue,
                        target_symbol=self.config.symbol,
                        asset=self.config.asset,
                        signal_type="trade_flow_impulse",
                        direction=direction,
                        lookback_ms=lookback_ms,
                        threshold_bps=0.0,
                        source_move_bps=0.0,
                        source_start_price=0.0,
                        source_end_price=0.0,
                        strength=abs(imbalance),
                        metadata={
                            "flow_signal_type": "signed_imbalance",
                            "lookback_ms": lookback_ms,
                            "buy_notional": round(buy_n, 2),
                            "sell_notional": round(sell_n, 2),
                            "imbalance": round(imbalance, 4),
                            "side_source": self._side_source(),
                        },
                    )
                    events.append(evt)
                    last_ts = ts
                    self._last_signal_ts[param_key] = ts

        return events

    # -- direction resolution ----------------------------------------------

    def _resolve_direction(
        self,
        trades: list[TradeTickLite],
        idx: int,
        lookback_ms: int,
        signal_type: str,
    ) -> str:
        """
        Resolve signal direction from source price move over the lookback.

        For count_burst and notional_burst, the signal itself has no inherent
        direction.  We label based on whether the source price moved up or
        down over the same lookback window, clearly marking this as
        ``direction_source=source_price_move`` in the metadata.
        """
        lookback_ns = lookback_ms * _MS_TO_NS
        if idx > 0:
            ts = trades[idx].ts_event
            # Find earliest tick in lookback
            ref_idx = idx
            while ref_idx > 0 and ts - trades[ref_idx].ts_event <= lookback_ns:
                ref_idx -= 1
            ref_idx = min(ref_idx + 1, idx)
            ref_price = trades[ref_idx].price
            curr_price = trades[idx].price
            if curr_price >= ref_price:
                return "long"
            return "short"
        # Fallback from side
        if trades[idx].side == "buy":
            return "long"
        if trades[idx].side == "sell":
            return "short"
        return "long"  # default momentum assumption

    def _side_source(self) -> str:
        """Return the side_source label for metadata."""
        return "exchange"  # TradeTickLite.side comes from exchange data
