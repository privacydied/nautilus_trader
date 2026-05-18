"""Forward-return measurement for signal events."""
from typing import List, Optional, Tuple
from bisect import bisect_right
import math

from .models import SignalEvent, ForwardReturnResult
from .config import Horizon, FeeModel


def get_entry_price(
    timestamps: List[float],
    prices: List[float],
    signal_ts: float,
) -> Optional[Tuple[float, float]]:
    """Find entry reference price: bar at or after signal timestamp.

    Prefer exact match if it exists.
    """
    if not timestamps or not prices:
        return None
    idx = bisect_right(timestamps, signal_ts)
    # Exact match check
    if idx > 0 and abs(timestamps[idx - 1] - signal_ts) < 1e-9:
        return timestamps[idx - 1], prices[idx - 1]
    # First bar strictly after
    if idx < len(timestamps):
        return timestamps[idx], prices[idx]
    return None


def find_price_at_or_after(
    timestamps: List[float],
    prices: List[float],
    target_ts: float,
) -> Optional[Tuple[float, float]]:
    """Find the first price bar strictly after ``target_ts``.

    For forward return horizons we want the price *at or after* the horizon
    timestamp, but since the horizon is computed from the entry timestamp
    (which already matched a bar), we need the first bar at or after it.
    """
    if not timestamps or not prices:
        return None
    idx = bisect_right(timestamps, target_ts)
    # Exact match
    if idx > 0 and abs(timestamps[idx - 1] - target_ts) < 1e-9:
        return timestamps[idx - 1], prices[idx - 1]
    # First strictly after
    if idx < len(timestamps):
        return timestamps[idx], prices[idx]
    return None


def compute_forward_return(
    entry_price: float,
    forward_price: float,
    direction: str,
) -> float:
    """Return in bps, direction-adjusted."""
    if entry_price <= 0 or not math.isfinite(entry_price) or not math.isfinite(forward_price):
        raise ValueError("non-finite or non-positive price for forward return")
    raw_bps = (forward_price - entry_price) / entry_price * 10000.0
    if direction == "short":
        raw_bps = -raw_bps
    return raw_bps


def compute_excursions(
    timestamps: List[float],
    prices: List[float],
    entry_ts: float,
    entry_price: float,
    forward_ts: float,
    direction: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Compute max favorable and adverse excursion between entry and horizon.

    Returns (max_favorable_bps, max_adverse_bps), direction-adjusted.
    """
    if entry_price <= 0 or not math.isfinite(entry_price):
        return None, None

    start_idx = bisect_right(timestamps, entry_ts) - 1
    if start_idx < 0:
        start_idx = 0
    end_idx = bisect_right(timestamps, forward_ts)
    if end_idx > len(timestamps):
        end_idx = len(timestamps)

    fav = None
    adv = None

    for i in range(start_idx + 1, end_idx):
        p = prices[i]
        if not math.isfinite(p):
            continue
        raw_bps = (p - entry_price) / entry_price * 10000.0
        if direction == "short":
            raw_bps = -raw_bps
        if fav is None or raw_bps > fav:
            fav = raw_bps
        if adv is None or raw_bps < adv:
            adv = raw_bps

    return fav, adv


def evaluate_signal(
    signal: SignalEvent,
    target_timestamps: List[float],
    target_prices: List[float],
    horizons: List[Horizon],
    fee_model: FeeModel,
    quote_mismatch: bool = False,
) -> List[ForwardReturnResult]:
    """Evaluate one signal across all horizons.

    Returns a list of ForwardReturnResult, one per horizon.
    """
    results: List[ForwardReturnResult] = []

    # Find entry price (first bar at or after signal timestamp)
    entry_info = get_entry_price(target_timestamps, target_prices, signal.timestamp)
    if entry_info is None:
        for h in horizons:
            results.append(ForwardReturnResult(
                signal_id=signal.signal_id,
                signal_timestamp=signal.timestamp,
                source_venue=signal.source_venue,
                source_instrument=signal.source_instrument,
                target_venue=signal.target_venue,
                target_instrument=signal.target_instrument,
                signal_type=signal.signal_type,
                direction=signal.direction,
                strength=signal.strength,
                horizon=h.name,
                valid=False,
                rejection_reason="no_entry_price",
            ))
        return results

    entry_ts, entry_price = entry_info
    if entry_price <= 0 or not math.isfinite(entry_price):
        reason = "zero_entry_price" if entry_price == 0 else "non_finite_entry_price"
        for h in horizons:
            results.append(ForwardReturnResult(
                signal_id=signal.signal_id,
                signal_timestamp=signal.timestamp,
                source_venue=signal.source_venue,
                source_instrument=signal.source_instrument,
                target_venue=signal.target_venue,
                target_instrument=signal.target_instrument,
                signal_type=signal.signal_type,
                direction=signal.direction,
                strength=signal.strength,
                horizon=h.name,
                entry_reference_price=entry_price,
                valid=False,
                rejection_reason=reason,
            ))
        return results

    total_cost = fee_model.total_cost_bps(quote_mismatch)

    for h in horizons:
        horizon_ts = entry_ts + h.seconds
        forward_info = find_price_at_or_after(
            target_timestamps, target_prices, horizon_ts,
        )

        if forward_info is None:
            results.append(ForwardReturnResult(
                signal_id=signal.signal_id,
                signal_timestamp=signal.timestamp,
                source_venue=signal.source_venue,
                source_instrument=signal.source_instrument,
                target_venue=signal.target_venue,
                target_instrument=signal.target_instrument,
                signal_type=signal.signal_type,
                direction=signal.direction,
                strength=signal.strength,
                horizon=h.name,
                entry_reference_price=entry_price,
                forward_price=None,
                raw_return_bps=None,
                direction_adjusted_return_bps=None,
                fee_bps=fee_model.fee_bps,
                slippage_bps=fee_model.slippage_bps,
                quote_mismatch_buffer_bps=fee_model.quote_mismatch_buffer_bps if quote_mismatch else 0.0,
                net_return_bps=None,
                valid=False,
                rejection_reason=f"no_price_at_{h.name}",
            ))
            continue

        forward_ts, forward_price = forward_info
        if not math.isfinite(forward_price) or forward_price <= 0:
            results.append(ForwardReturnResult(
                signal_id=signal.signal_id,
                signal_timestamp=signal.timestamp,
                source_venue=signal.source_venue,
                source_instrument=signal.source_instrument,
                target_venue=signal.target_venue,
                target_instrument=signal.target_instrument,
                signal_type=signal.signal_type,
                direction=signal.direction,
                strength=signal.strength,
                horizon=h.name,
                entry_reference_price=entry_price,
                forward_price=forward_price,
                valid=False,
                rejection_reason="non_finite_forward_price",
            ))
            continue

        try:
            dir_adj_bps = compute_forward_return(entry_price, forward_price, signal.direction)
        except ValueError:
            results.append(ForwardReturnResult(
                signal_id=signal.signal_id,
                signal_timestamp=signal.timestamp,
                source_venue=signal.source_venue,
                source_instrument=signal.source_instrument,
                target_venue=signal.target_venue,
                target_instrument=signal.target_instrument,
                signal_type=signal.signal_type,
                direction=signal.direction,
                strength=signal.strength,
                horizon=h.name,
                entry_reference_price=entry_price,
                forward_price=forward_price,
                valid=False,
                rejection_reason="invalid_prices_in_compute_forward_return",
            ))
            continue
        # raw_bps is the unsigned price move (not direction-adjusted)
        raw_bps = (forward_price - entry_price) / entry_price * 10000.0

        fav_bps, adv_bps = compute_excursions(
            target_timestamps, target_prices, entry_ts, entry_price,
            forward_ts, signal.direction,
        )

        net_bps = dir_adj_bps - total_cost

        results.append(ForwardReturnResult(
            signal_id=signal.signal_id,
            signal_timestamp=signal.timestamp,
            source_venue=signal.source_venue,
            source_instrument=signal.source_instrument,
            target_venue=signal.target_venue,
            target_instrument=signal.target_instrument,
            signal_type=signal.signal_type,
            direction=signal.direction,
            strength=signal.strength,
            horizon=h.name,
            entry_reference_price=entry_price,
            forward_price=forward_price,
            raw_return_bps=round(raw_bps, 4),
            direction_adjusted_return_bps=round(dir_adj_bps, 4),
            fee_bps=fee_model.fee_bps,
            slippage_bps=fee_model.slippage_bps,
            quote_mismatch_buffer_bps=fee_model.quote_mismatch_buffer_bps if quote_mismatch else 0.0,
            net_return_bps=round(net_bps, 4),
            max_favorable_excursion_bps=round(fav_bps, 4) if fav_bps is not None else None,
            max_adverse_excursion_bps=round(adv_bps, 4) if adv_bps is not None else None,
            valid=True,
            rejection_reason=None,
        ))

    return results
