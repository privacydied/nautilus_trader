"""
Cross-venue trigger → target book fill model.

Extends the V7 L2 maker paper simulator into cross-venue semantics:
- source event on one venue (e.g. Binance/Bybit perp)
- target entry on another venue (e.g. Kraken/Coinbase spot)
- configurable entry delay
- target book staleness constraints
- maker-or-taker decision on target venue
- exit model after target catches up or signal expires

The model is an estimate, not ground truth. All results carry
uncertainty/error bars. Shadow must not place orders.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FillModelConfig:
    source_venue: str
    target_venue: str
    entry_delay_seconds: float = 5.0
    max_staleness_seconds: float = 2.0
    maker_probability: float = 0.6
    maker_rebate_bps: float = 2.0
    taker_fee_bps: float = 8.0
    spread_bps: float = 4.0
    queue_depth_levels: int = 3
    exit_horizon_seconds: float = 180.0
    slippage_model_std_bps: float = 3.0
    fill_model_uncertainty_bps: float = 15.0


@dataclass
class FillEvent:
    trigger_time_seconds: float
    entry_delay_seconds: float
    staleness_at_entry: float
    fill_type: str  # maker | taker | missed | staleness_reject
    entry_price_bps_from_mid: float
    exit_price_bps_from_mid: float
    net_bps: float
    queue_penalty_bps: float
    forced_exit: bool


@dataclass
class CrossVenueFillModel:
    """Cross-venue fill model for a single signal/candidate configuration."""
    config: FillModelConfig
    _rng: random.Random = field(default_factory=lambda: random.Random(42), repr=False)

    def seed(self, s: int) -> None:
        self._rng = random.Random(s)

    def simulate_fill(
        self,
        trigger_time_seconds: float,
        target_mid_move_bps: float,
    ) -> FillEvent:
        """Simulate one fill cycle: source trigger → target entry → exit.

        Parameters
        ----------
        trigger_time_seconds:
            Wall-clock time of the source trigger event (seconds offset).
        target_mid_move_bps:
            Expected target mid-price move in bps if signal is correct.

        Returns
        -------
        FillEvent with net_bps and fill_type.
        """
        cfg = self.config

        # Staleness: how stale is the target book at the moment of entry
        entry_time = trigger_time_seconds + cfg.entry_delay_seconds
        staleness = self._rng.expovariate(1.0 / (cfg.max_staleness_seconds / 2))

        if staleness > cfg.max_staleness_seconds:
            return FillEvent(
                trigger_time_seconds=trigger_time_seconds,
                entry_delay_seconds=cfg.entry_delay_seconds,
                staleness_at_entry=staleness,
                fill_type="staleness_reject",
                entry_price_bps_from_mid=0.0,
                exit_price_bps_from_mid=0.0,
                net_bps=0.0,
                queue_penalty_bps=0.0,
                forced_exit=False,
            )

        # Maker/taker decision
        is_maker = self._rng.random() < cfg.maker_probability
        fill_type = "maker" if is_maker else "taker"

        # Entry price
        spread_half = cfg.spread_bps / 2
        queue_penalty = self._rng.uniform(0, spread_half) if is_maker else 0.0
        entry_slip = self._rng.gauss(0, cfg.slippage_model_std_bps)

        if is_maker:
            entry_cost = -cfg.maker_rebate_bps + queue_penalty + entry_slip
        else:
            entry_cost = cfg.taker_fee_bps + spread_half + entry_slip

        # Exit: did target catch up?
        exit_slip = self._rng.gauss(0, cfg.slippage_model_std_bps)
        forced_exit = self._rng.random() > 0.8
        exit_cost = cfg.taker_fee_bps + spread_half + exit_slip

        gross_capture = target_mid_move_bps * self._rng.uniform(0.5, 1.0)
        net = gross_capture - entry_cost - exit_cost

        return FillEvent(
            trigger_time_seconds=trigger_time_seconds,
            entry_delay_seconds=cfg.entry_delay_seconds,
            staleness_at_entry=round(staleness, 4),
            fill_type=fill_type,
            entry_price_bps_from_mid=round(entry_cost, 4),
            exit_price_bps_from_mid=round(exit_cost, 4),
            net_bps=round(net, 4),
            queue_penalty_bps=round(queue_penalty, 4),
            forced_exit=forced_exit,
        )
