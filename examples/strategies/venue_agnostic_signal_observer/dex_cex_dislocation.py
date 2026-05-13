"""DEX-CEX spot dislocation signal generator.

Takes a stream of DexPoolSnapshot objects and emits DexDislocationEvent
when price/volume/liquidity crosses configured thresholds.

**Observer-only. No execution, no orders, no private endpoints.**
"""
from __future__ import annotations

import statistics
import uuid
import math

from .dex_models import DexPoolSnapshot, DexDislocationEvent


# ---------------------------------------------------------------------------
# Dislocation detector
# ---------------------------------------------------------------------------

class DexCexDislocationDetector:
    """Scan DEX pool snapshots for dislocation events.

    Three signal types:
      - dex_price_shock: price change over a snapshot window exceeds threshold
      - dex_volume_burst: 5m volume burst exceeds rolling median * multiplier
      - dex_liquidity_shock: liquidity change exceeds threshold
    """

    def __init__(
        self,
        price_shock_threshold_bps: float = 50.0,
        volume_burst_multiplier: float = 3.0,
        volume_median_window: int = 20,
        liquidity_shock_threshold_bps: float = 200.0,
        min_liquidity_usd: float = 500_000.0,
        min_volume_1h_usd: float = 100_000.0,
        cooldown_ns: int = 60_000_000_000,  # 60 s in ns
    ) -> None:
        self.price_shock_threshold_bps = price_shock_threshold_bps
        self.volume_burst_multiplier = volume_burst_multiplier
        self.volume_median_window = volume_median_window
        self.liquidity_shock_threshold_bps = liquidity_shock_threshold_bps
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_1h_usd = min_volume_1h_usd
        self.cooldown_ns = cooldown_ns
        # State per pool
        self._prev_snapshots: dict[str, DexPoolSnapshot] = {}
        self._volume_history: dict[str, list[float]] = {}
        self._last_signal_ts: dict[str, int] = {}

    def scan(
        self,
        snapshots: list[DexPoolSnapshot],
    ) -> tuple[list[DexDislocationEvent], list[str]]:
        """Scan a list of snapshots for dislocation events.

        Returns (events, warnings).
        Snapshots should be roughly sorted by ts_event.
        """
        events: list[DexDislocationEvent] = []
        warnings_list: list[str] = []

        for snap in snapshots:
            pool_key = snap.pair_address
            # Apply hard filters first
            if snap.price_usd is None:
                warnings_list.append(f"Reject {pool_key}: price_usd is None")
                continue
            if snap.liquidity_usd is None:
                warnings_list.append(f"Reject {pool_key}: liquidity_usd is None")
                continue
            if snap.liquidity_usd < self.min_liquidity_usd:
                continue  # silently skip low-liquidity pools
            if snap.volume_1h_usd is None or snap.volume_1h_usd < self.min_volume_1h_usd:
                continue
            # Price shock check
            evt = self._check_price_shock(snap)
            if evt is not None:
                events.append(evt)
            # Volume burst check
            evt = self._check_volume_burst(snap)
            if evt is not None:
                events.append(evt)
            # Liquidity shock check
            evt = self._check_liquidity_shock(snap)
            if evt is not None:
                events.append(evt)
            # Update state
            self._prev_snapshots[pool_key] = snap
            if snap.volume_5m_usd is not None and snap.volume_5m_usd > 0:
                hist = self._volume_history.setdefault(pool_key, [])
                hist.append(snap.volume_5m_usd)
                # Keep only recent history
                if len(hist) > self.volume_median_window * 3:
                    self._volume_history[pool_key] = hist[-self.volume_median_window * 2:]
            # Update cooldown
            ts_key = f"{pool_key}_last"
            self._last_signal_ts[ts_key] = snap.ts_event

        return events, warnings_list

    def _check_price_shock(self, snap: DexPoolSnapshot) -> DexDislocationEvent | None:
        """Fire event if price changed significantly since last snapshot."""
        pkey = snap.pair_address
        prev = self._prev_snapshots.get(pkey)
        if prev is None:
            return None
        prev_price = prev.price_usd
        curr_price = snap.price_usd
        if prev_price is None or curr_price is None or prev_price <= 0:
            return None

        change_pct = ((curr_price - prev_price) / prev_price) * 100.0
        change_bps = abs(change_pct) * 100.0

        if change_bps < self.price_shock_threshold_bps:
            return None

        # Resolve direction from price move
        direction = "long" if change_pct >= 0 else "short"

        return DexDislocationEvent(
            event_id=str(uuid.uuid4()),
            chain=snap.chain,
            dex=snap.dex,
            pair_address=pkey,
            asset=snap.base_symbol,
            quote=snap.quote_symbol,
            signal_type="dex_price_shock",
            direction=direction,
            strength_bps=round(change_bps, 2),
            price_change_bps=round(change_bps, 2),
            volume_zscore=None,
            liquidity_change_bps=None,
            buy_sell_imbalance=snap.buy_sell_imbalance_5m,
            ts_event=snap.ts_event,
            ts_recv=snap.ts_recv,
            metadata={
                "prev_price_usd": prev_price,
                "curr_price_usd": curr_price,
                "change_pct": round(change_pct, 4),
            },
        )

    def _check_volume_burst(self, snap: DexPoolSnapshot) -> DexDislocationEvent | None:
        """Fire event if 5m DEX volume spikes above rolling median."""
        pkey = snap.pair_address
        if snap.volume_5m_usd is None or snap.volume_5m_usd <= 0:
            return None

        hist = self._volume_history.get(pkey, [])
        if len(hist) < 5:
            return None

        median_vol = statistics.median(hist[-self.volume_median_window:])
        if median_vol <= 0:
            return None

        ratio = snap.volume_5m_usd / median_vol
        if ratio < self.volume_burst_multiplier:
            return None

        # Direction from buy/sell imbalance — MUST NOT guess
        imbalance = snap.buy_sell_imbalance_5m
        if imbalance is None:
            # Cannot infer direction -> reject event
            return None

        if abs(imbalance) < 0.1:
            # Imbalance too flat to assign direction
            # Use price change as secondary signal
            direction = self._resolve_direction_from_price(snap)
            if direction == "unknown":
                return None
        else:
            direction = "long" if imbalance > 0 else "short"

        # Z-score
        if len(hist) >= 2:
            mean_vol = statistics.mean(hist)
            std_vol = statistics.stdev(hist)
            if std_vol > 0:
                zscore = (snap.volume_5m_usd - mean_vol) / std_vol
            else:
                zscore = 0.0
        else:
            zscore = None

        prev = self._prev_snapshots.get(pkey)
        price_change_bps = self._compute_price_change_bps(prev, snap)

        return DexDislocationEvent(
            event_id=str(uuid.uuid4()),
            chain=snap.chain,
            dex=snap.dex,
            pair_address=pkey,
            asset=snap.base_symbol,
            quote=snap.quote_symbol,
            signal_type="dex_volume_burst",
            direction=direction,
            strength_bps=round(ratio * 100.0, 2),
            price_change_bps=round(price_change_bps, 2),
            volume_zscore=round(zscore, 4) if zscore is not None else None,
            liquidity_change_bps=None,
            buy_sell_imbalance=imbalance,
            ts_event=snap.ts_event,
            ts_recv=snap.ts_recv,
            metadata={
                "current_vol_5m_usd": round(snap.volume_5m_usd, 2),
                "median_vol_5m_usd": round(median_vol, 2),
                "burst_ratio": round(ratio, 2),
                "volume_zscore": round(zscore, 4) if zscore is not None else None,
            },
        )

    def _check_liquidity_shock(self, snap: DexPoolSnapshot) -> DexDislocationEvent | None:
        """Fire event if pool liquidity changes sharply."""
        pkey = snap.pair_address
        prev = self._prev_snapshots.get(pkey)
        if prev is None:
            return None
        prev_liq = prev.liquidity_usd
        curr_liq = snap.liquidity_usd
        if prev_liq is None or curr_liq is None or prev_liq <= 0:
            return None

        change_pct = ((curr_liq - prev_liq) / prev_liq) * 100.0
        change_bps = abs(change_pct) * 100.0

        if change_bps < self.liquidity_shock_threshold_bps:
            return None

        # Direction unknown by default — try price confirmation
        prev_price = prev.price_usd
        curr_price = snap.price_usd
        if prev_price is not None and curr_price is not None and prev_price > 0:
            price_pct = ((curr_price - prev_price) / prev_price) * 100.0
            direction = "long" if price_pct >= 0 else "short"
            price_change_bps = round(abs(price_pct) * 100.0, 2)
        else:
            direction = "unknown"
            price_change_bps = 0.0

        return DexDislocationEvent(
            event_id=str(uuid.uuid4()),
            chain=snap.chain,
            dex=snap.dex,
            pair_address=pkey,
            asset=snap.base_symbol,
            quote=snap.quote_symbol,
            signal_type="dex_liquidity_shock",
            direction=direction,
            strength_bps=round(change_bps, 2),
            price_change_bps=price_change_bps,
            volume_zscore=None,
            liquidity_change_bps=round(change_bps, 2),
            buy_sell_imbalance=snap.buy_sell_imbalance_5m,
            ts_event=snap.ts_event,
            ts_recv=snap.ts_recv,
            metadata={
                "prev_liquidity_usd": prev_liq,
                "curr_liquidity_usd": curr_liq,
                "change_pct": round(change_pct, 4),
            },
        )

    def _resolve_direction_from_price(self, snap: DexPoolSnapshot) -> str:
        prev = self._prev_snapshots.get(snap.pair_address)
        if prev is None or prev.price_usd is None or snap.price_usd is None:
            return "unknown"
        p0 = prev.price_usd
        p1 = snap.price_usd
        if p0 <= 0:
            return "unknown"
        return "long" if p1 >= p0 else "short"

    @staticmethod
    def _compute_price_change_bps(
        prev: DexPoolSnapshot | None,
        snap: DexPoolSnapshot,
    ) -> float:
        if prev is None or prev.price_usd is None or snap.price_usd is None:
            return 0.0
        p0 = prev.price_usd
        p1 = snap.price_usd
        if p0 <= 0:
            return 0.0
        return abs((p1 - p0) / p0) * 10000.0


# ---------------------------------------------------------------------------
# Forward-return evaluator — reuses existing event-study machinery
# ---------------------------------------------------------------------------

def dex_event_to_tick_signal(event: DexDislocationEvent):
    """Convert a DexDislocationEvent to TickSignalEvent for existing evaluation."""
    from .tick_models import TickSignalEvent

    return TickSignalEvent(
        signal_id=event.event_id,
        ts_event=event.ts_event,
        source_venue=f"dex:{event.dex}",
        source_symbol=f"{event.asset}/{event.quote}",
        target_venue="",  # filled at evaluation time
        target_symbol="",
        asset=event.asset,
        signal_type=event.signal_type,
        direction=event.direction,
        lookback_ms=1,
        threshold_bps=event.strength_bps,
        source_move_bps=event.price_change_bps,
        source_start_price=0.0,
        source_end_price=0.0,
        strength=event.strength_bps,
        metadata=event.metadata,
    )
