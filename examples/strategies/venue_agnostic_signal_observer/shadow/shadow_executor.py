"""
Shadow executor — runs the fill model over a sequence of signal events.

Shadow must not place orders. All results carry uncertainty/error bars.
A positive mean with wider uncertainty than the edge is not a pass.

Example from the plan: shadow net +8 bps with ±15 bps fill-model uncertainty
is not a valid pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

from .fill_model import CrossVenueFillModel, FillModelConfig, FillEvent


@dataclass
class ShadowResult:
    candidate_hash: str | None
    grid_hash: str | None
    n_events: int
    shadow_fill_rate: float
    missed_fill_rate: float
    partial_fill_rate: float
    forced_exit_rate: float
    entry_spread: float
    exit_spread: float
    queue_penalty: float
    staleness_rejects: int
    staleness_reject_rate: float
    shadow_net_bps: float | None
    shadow_net_bps_std: float | None
    fill_model_uncertainty_estimate: float
    lower_confidence_bound: float | None
    capacity_estimate: int | None
    shadow_pass: bool
    shadow_fail_reason: str | None
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def run_shadow(
    signal_events: list[dict[str, Any]],
    config: FillModelConfig,
    candidate_hash: str | None = None,
    grid_hash: str | None = None,
    seed: int = 42,
) -> ShadowResult:
    """Run shadow simulation over a sequence of signal events.

    Each event must have 'trigger_time_seconds' and 'target_mid_move_bps'.

    Shadow must not place orders — this is pure simulation.
    All results carry fill_model_uncertainty_estimate.
    A pass requires: shadow_net_bps > 0 AND lower_confidence_bound > 0.
    """
    model = CrossVenueFillModel(config=config)
    model.seed(seed)

    fill_events: list[FillEvent] = []
    for evt in signal_events:
        fe = model.simulate_fill(
            trigger_time_seconds=evt["trigger_time_seconds"],
            target_mid_move_bps=evt.get("target_mid_move_bps", 0.0),
        )
        fill_events.append(fe)

    n = len(fill_events)
    if n == 0:
        return ShadowResult(
            candidate_hash=candidate_hash,
            grid_hash=grid_hash,
            n_events=0,
            shadow_fill_rate=0.0,
            missed_fill_rate=0.0,
            partial_fill_rate=0.0,
            forced_exit_rate=0.0,
            entry_spread=0.0,
            exit_spread=0.0,
            queue_penalty=0.0,
            staleness_rejects=0,
            staleness_reject_rate=0.0,
            shadow_net_bps=None,
            shadow_net_bps_std=None,
            fill_model_uncertainty_estimate=config.fill_model_uncertainty_bps,
            lower_confidence_bound=None,
            capacity_estimate=None,
            shadow_pass=False,
            shadow_fail_reason="No signal events provided",
            config=asdict(config),
        )

    staleness_rejects = sum(1 for f in fill_events if f.fill_type == "staleness_reject")
    filled = [f for f in fill_events if f.fill_type != "staleness_reject"]
    missed = [f for f in fill_events if f.fill_type == "missed"]
    forced = [f for f in fill_events if f.forced_exit]

    fill_rate = len(filled) / n
    miss_rate = len(missed) / n
    stale_rate = staleness_rejects / n
    forced_rate = len(forced) / len(filled) if filled else 0.0

    net_bps_series = [f.net_bps for f in filled]
    mean_net = _mean(net_bps_series)
    std_net = _std(net_bps_series)
    uncertainty = config.fill_model_uncertainty_bps

    entry_spread = _mean([f.entry_price_bps_from_mid for f in filled]) or 0.0
    exit_spread = _mean([f.exit_price_bps_from_mid for f in filled]) or 0.0
    queue_penalty = _mean([f.queue_penalty_bps for f in filled]) or 0.0

    # Lower confidence bound: mean - 1 std (conservative)
    if mean_net is not None and std_net is not None:
        lower_cb = mean_net - std_net
    elif mean_net is not None:
        lower_cb = mean_net - uncertainty
    else:
        lower_cb = None

    # Capacity estimate: max events per day given entry delay + horizon
    slots_per_day = 86400 / (config.entry_delay_seconds + config.exit_horizon_seconds)
    capacity = int(slots_per_day * fill_rate)

    # Shadow pass: mean > 0 AND lower_confidence_bound > 0
    # Positive mean but uncertainty wider than edge is NOT a pass
    fail_reason = None
    if mean_net is None or mean_net <= 0:
        shadow_pass = False
        fail_reason = f"Non-positive shadow net bps: {mean_net}"
    elif lower_cb is None or lower_cb <= 0:
        shadow_pass = False
        fail_reason = (
            f"Lower confidence bound non-positive: {lower_cb:.2f} bps "
            f"(mean={mean_net:.2f}, uncertainty=±{uncertainty:.2f})"
        )
    else:
        shadow_pass = True

    return ShadowResult(
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        n_events=n,
        shadow_fill_rate=round(fill_rate, 4),
        missed_fill_rate=round(miss_rate, 4),
        partial_fill_rate=0.0,
        forced_exit_rate=round(forced_rate, 4),
        entry_spread=round(entry_spread, 4),
        exit_spread=round(exit_spread, 4),
        queue_penalty=round(queue_penalty, 4),
        staleness_rejects=staleness_rejects,
        staleness_reject_rate=round(stale_rate, 4),
        shadow_net_bps=round(mean_net, 4) if mean_net is not None else None,
        shadow_net_bps_std=round(std_net, 4) if std_net is not None else None,
        fill_model_uncertainty_estimate=uncertainty,
        lower_confidence_bound=round(lower_cb, 4) if lower_cb is not None else None,
        capacity_estimate=capacity,
        shadow_pass=shadow_pass,
        shadow_fail_reason=fail_reason,
        config=asdict(config),
    )
