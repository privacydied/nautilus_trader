"""Tick-level lead-lag event-study engine.

This module is **research observer only**. It generates signals from source-venue
tick data, measures target-venue forward returns, and compares results against a
random baseline. There is **no execution logic, no order submission, no position
tracking, and no live-trading code** anywhere in this module.

Public API
----------
TickLeadLagGenerator       – scans source-venue trades for lead-lag signals
evaluate_tick_signal       – measures forward returns on the target venue
generate_random_baseline   – builds a random-timestamp control group
evaluate_candidate_group   – gates signals against a baseline heuristic
generate_synthetic_*       – deterministic fixtures for unit tests
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
import uuid
from dataclasses import dataclass

from .tick_models import QuoteTickLite, TickForwardReturn, TickSignalEvent, TradeTickLite

_MS_TO_NS = 1_000_000


# ---------------------------------------------------------------------------
# 1. TickLeadLagGenerator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickLeadLagConfig:
    """Parameters for the tick-level lead-lag signal generator."""

    lookback_ms: list[int]
    threshold_bps: list[float]
    cooldown_ms: int = 10_000
    source_venue: str = "BINANCE"
    target_venue: str = "KRAKEN"
    symbol: str = "BTC/USD"
    asset: str = "BTC"


class TickLeadLagGenerator:
    """Scan sorted source-venue trade ticks for lead-lag price moves.

    For every ``(lookback_ms, threshold_bps)`` pair the generator walks through
    the trade stream, computes the move from the earliest trade inside the
    lookback window to the current trade, and emits a :class:`TickSignalEvent`
    whenever ``abs(move_bps) >= threshold_bps`` (subject to cooldown).

    The generator uses **only data at or before** the current tick timestamp —
    there is no lookahead.
    """

    def __init__(self, config: TickLeadLagConfig) -> None:
        self.config = config
        # Internal tracking: per (lookback_ms, threshold_bps) -> last signal ts (ns)
        self._last_signal_ts: dict[tuple[int, float], int] = {}

    def generate(self, trades: list[TradeTickLite]) -> list[TickSignalEvent]:
        """Generate signals from a sorted list of source-venue trade ticks.

        Parameters
        ----------
        trades :
            Must be sorted by ``ts_event`` ascending.  The method does not
            re-sort; it trusts the caller's ordering invariant.

        Returns
        -------
        list[TickSignalEvent]
        """
        events: list[TickSignalEvent] = []

        for lookback_ms, threshold_bps in self._param_pairs():
            events.extend(
                self._scan(
                    trades,
                    lookback_ms=lookback_ms,
                    threshold_bps=threshold_bps,
                )
            )

        return events

    # -- internal ----------------------------------------------------------

    def _param_pairs(self) -> list[tuple[int, float]]:
        return [
            (lb, th)
            for lb in self.config.lookback_ms
            for th in self.config.threshold_bps
        ]

    def _scan(
        self,
        trades: list[TradeTickLite],
        lookback_ms: int,
        threshold_bps: float,
    ) -> list[TickSignalEvent]:
        lookback_ns = lookback_ms * _MS_TO_NS
        cooldown_ns = self.config.cooldown_ms * _MS_TO_NS
        param_key = (lookback_ms, threshold_bps)

        events: list[TickSignalEvent] = []

        # We maintain a sliding‐window index into *trades*.
        # ``window_start`` points to the earliest trade still inside the
        # lookback window for the current tick.
        window_start = 0

        for i, current in enumerate(trades):
            ts = current.ts_event
            price = current.price

            # Advance window_start so trades[window_start] is the oldest tick
            # within [ts - lookback_ns, ts].
            while window_start < i and (ts - trades[window_start].ts_event) > lookback_ns:
                window_start += 1

            ref = trades[window_start]
            ref_price = ref.price

            if ref_price <= 0 or not math.isfinite(ref_price) or not math.isfinite(price):
                continue

            move_bps = (price - ref_price) / ref_price * 10000.0

            if abs(move_bps) < threshold_bps:
                continue

            # Cooldown check
            last_ts = self._last_signal_ts.get(param_key, -1)
            if ts - last_ts < cooldown_ns:
                continue

            # Emit event
            direction = "long" if move_bps > 0 else "short"
            evt = TickSignalEvent(
                signal_id=str(uuid.uuid4()),
                ts_event=ts,
                source_venue=self.config.source_venue,
                source_symbol=self.config.symbol,
                target_venue=self.config.target_venue,
                target_symbol=self.config.symbol,
                asset=self.config.asset,
                signal_type="tick_lead_lag",
                direction=direction,
                lookback_ms=lookback_ms,
                threshold_bps=threshold_bps,
                source_move_bps=move_bps,
                source_start_price=ref_price,
                source_end_price=price,
                strength=abs(move_bps),
                metadata={"window_start_ts": ref.ts_event, "window_start_idx": window_start},
            )
            events.append(evt)
            self._last_signal_ts[param_key] = ts

        return events


# ---------------------------------------------------------------------------
# 2. Tick Forward Return Evaluator
# ---------------------------------------------------------------------------


def _extract_ts_prices(
    ticks: list[TradeTickLite] | list[QuoteTickLite],
) -> tuple[list[int], list[float]]:
    """Return parallel ``(ts_event, price)`` arrays from a tick list.

    For :class:`TradeTickLite` the ``price`` field is used directly.
    For :class:`QuoteTickLite` the ``mid`` property is used.
    """
    if not ticks:
        return [], []

    if isinstance(ticks[0], TradeTickLite):
        return (
            [t.ts_event for t in ticks],  # type: ignore[arg-type]
            [t.price for t in ticks],  # type: ignore[arg-type]
        )
    # QuoteTickLite
    return (
        [q.ts_event for q in ticks],
        [q.mid for q in ticks],
    )


def evaluate_tick_signal(
    signal: TickSignalEvent,
    target_ticks: list[TradeTickLite] | list[QuoteTickLite],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float = 0.0,
    quote_mismatch: bool = False,
) -> list[TickForwardReturn]:
    """Measure forward returns on the target venue for a single signal.

    Uses :func:`bisect.bisect_left` for efficient timestamp lookup.  The
    ``target_ticks`` list **must** be sorted by ``ts_event`` ascending.

    Parameters
    ----------
    signal :
        The signal event produced by :class:`TickLeadLagGenerator` or the
        random baseline generator.
    target_ticks :
        Sorted list of target-venue ticks (trades or quotes).
    horizons_ms :
        Forward‐return horizons in milliseconds.
    fee_bps, slippage_bps :
        Assumed per‐leg costs.
    quote_mismatch_buffer_bps :
        Additional cost buffer when ``quote_mismatch`` is True.
    quote_mismatch :
        Whether the signal used a quote‐based reference (adds cost buffer).
    """
    timestamps, prices = _extract_ts_prices(target_ticks)
    results: list[TickForwardReturn] = []
    total_cost = fee_bps + slippage_bps + (quote_mismatch_buffer_bps if quote_mismatch else 0.0)

    # Entry: first tick at or after signal.ts_event
    entry_idx = bisect.bisect_left(timestamps, signal.ts_event)

    if entry_idx >= len(timestamps):
        # No entry reference price available
        for horizon_ms in horizons_ms:
            results.append(
                TickForwardReturn(
                    signal_id=signal.signal_id,
                    signal_ts=signal.ts_event,
                    target_venue=signal.target_venue,
                    target_symbol=signal.target_symbol,
                    horizon_ms=horizon_ms,
                    entry_reference_price=None,
                    forward_price=None,
                    raw_return_bps=None,
                    direction_adjusted_return_bps=None,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                    net_return_bps=None,
                    valid=False,
                    rejection_reason="no_entry_reference_price",
                )
            )
        return results

    entry_ts = timestamps[entry_idx]
    entry_price = prices[entry_idx]

    for horizon_ms in horizons_ms:
        horizon_ts = entry_ts + horizon_ms * _MS_TO_NS
        forward_idx = bisect.bisect_left(timestamps, horizon_ts)

        if forward_idx >= len(timestamps):
            results.append(
                TickForwardReturn(
                    signal_id=signal.signal_id,
                    signal_ts=signal.ts_event,
                    target_venue=signal.target_venue,
                    target_symbol=signal.target_symbol,
                    horizon_ms=horizon_ms,
                    entry_reference_price=entry_price,
                    forward_price=None,
                    raw_return_bps=None,
                    direction_adjusted_return_bps=None,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                    net_return_bps=None,
                    valid=False,
                    rejection_reason=f"no_forward_price_at_{horizon_ms}ms",
                )
            )
            continue

        forward_price = prices[forward_idx]
        if not math.isfinite(forward_price):
            results.append(
                TickForwardReturn(
                    signal_id=signal.signal_id,
                    signal_ts=signal.ts_event,
                    target_venue=signal.target_venue,
                    target_symbol=signal.target_symbol,
                    horizon_ms=horizon_ms,
                    entry_reference_price=entry_price,
                    forward_price=forward_price,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                    valid=False,
                    rejection_reason="non_finite_forward_price",
                )
            )
            continue

        raw_return_bps = (forward_price - entry_price) / entry_price * 10000.0

        if signal.direction == "short":
            dir_adj = -raw_return_bps
        else:
            dir_adj = raw_return_bps

        net_return_bps = dir_adj - total_cost

        results.append(
            TickForwardReturn(
                signal_id=signal.signal_id,
                signal_ts=signal.ts_event,
                target_venue=signal.target_venue,
                target_symbol=signal.target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=forward_price,
                raw_return_bps=raw_return_bps,
                direction_adjusted_return_bps=dir_adj,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                net_return_bps=net_return_bps,
                valid=True,
                rejection_reason=None,
            )
        )

    return results


# ---------------------------------------------------------------------------
# 3. Random Baseline Generator
# ---------------------------------------------------------------------------


def generate_random_baseline(
    source_ticks: list[TradeTickLite],
    signal_count: int,
    source_venue: str,
    target_venue: str,
    symbol: str,
    asset: str,
    seed: int = 42,
) -> list[TickSignalEvent]:
    """Generate a random-timestamp control group matching the signal count.

    Timestamps are uniformly distributed across the temporal range of the
    source tick series.  Direction, lookback, and threshold are chosen at
    random.  ``source_move_bps`` is always ``0.0`` because the baseline
    carries no real signal.

    Parameters
    ----------
    source_ticks :
        Used only for ``ts_event`` min/max range.  Must be non-empty.
    signal_count :
        Number of random events to generate.
    seed :
        RNG seed for deterministic reproducibility.
    """
    if not source_ticks:
        raise ValueError("source_ticks must be non-empty")

    rng = random.Random(seed)

    min_ts = min(t.ts_event for t in source_ticks)
    max_ts = max(t.ts_event for t in source_ticks)

    lookback_choices = [1000, 5000, 10000]
    threshold_choices = [5.0, 10.0, 20.0]
    directions = ("long", "short")

    events: list[TickSignalEvent] = []

    for _ in range(signal_count):
        ts = rng.randint(min_ts, max_ts)
        direction = rng.choice(directions)
        lookback = rng.choice(lookback_choices)
        threshold = rng.choice(threshold_choices)

        events.append(
            TickSignalEvent(
                signal_id=str(uuid.uuid4()),
                ts_event=ts,
                source_venue=source_venue,
                source_symbol=symbol,
                target_venue=target_venue,
                target_symbol=symbol,
                asset=asset,
                signal_type="tick_lead_lag",
                direction=direction,
                lookback_ms=lookback,
                threshold_bps=threshold,
                source_move_bps=0.0,
                source_start_price=0.0,
                source_end_price=0.0,
                strength=0.0,
            )
        )

    return events


# ---------------------------------------------------------------------------
# 4. Candidate Gate
# ---------------------------------------------------------------------------


def evaluate_candidate_group(
    forward_returns: list[TickForwardReturn],
    baseline_forward_returns: list[TickForwardReturn],
    min_events: int = 50,
    baseline_margin_bps: float = 1.0,
) -> dict[str, object]:
    """Gate a candidate signal group against a random baseline.

    All six gates must pass for ``candidate`` to be ``True``.

    Returns a dictionary with summary statistics, per-gate results, and a
    machine-readable list of rejection reasons.
    """
    rejection_reasons: list[str] = []

    # Filter to valid forward returns
    valid = [r for r in forward_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
    baseline_valid = [
        r for r in baseline_forward_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)
    ]

    event_count = len(valid)

    # Gate 1: sufficient event count
    if event_count < min_events:
        rejection_reasons.append(
            f"insufficient_events: {event_count} < {min_events}"
        )

    if event_count == 0:
        return _gate_result(
            event_count=event_count,
            rejection_reasons=rejection_reasons,
        )

    net_returns = [r.net_return_bps for r in valid]
    mean_net = statistics.mean(net_returns)
    median_net = statistics.median(net_returns)
    wins = sum(1 for x in net_returns if x > 0)
    win_rate = wins / len(net_returns)

    # Gate 2: mean net return > 0
    if mean_net <= 0:
        rejection_reasons.append(f"mean_net_return_not_positive: {mean_net:.2f} bps")

    # Gate 3: median net return > -5 bps
    if median_net <= -5.0:
        rejection_reasons.append(f"median_net_too_negative: {median_net:.2f} bps")

    # Baseline statistics
    baseline_mean_net: float | None = None
    baseline_win_rate: float | None = None
    beats_baseline = False
    not_single_event_driven = False

    if baseline_valid:
        baseline_nets = [r.net_return_bps for r in baseline_valid]
        baseline_mean_net = statistics.mean(baseline_nets)
        bl_wins = sum(1 for x in baseline_nets if x > 0)
        baseline_win_rate = bl_wins / len(baseline_nets)

        # Gate 4: win rate > 50% OR beats baseline win rate
        passes_win_rate = (win_rate > 0.5) or (
            baseline_win_rate is not None and win_rate > baseline_win_rate
        )
        if not passes_win_rate:
            rejection_reasons.append(
                f"win_rate_fails: {win_rate:.4f} vs baseline {baseline_win_rate:.4f}"
            )

        # Gate 5: mean net > baseline mean + margin
        if baseline_mean_net is not None:
            if mean_net > baseline_mean_net + baseline_margin_bps:
                beats_baseline = True
            else:
                rejection_reasons.append(
                    f"does_not_beat_baseline_by_margin: "
                    f"{mean_net:.2f} <= {baseline_mean_net:.2f} + {baseline_margin_bps:.2f}"
                )
    else:
        # No baseline valid events — gate 4 and 5 cannot be satisfied
        rejection_reasons.append("no_valid_baseline_events")

    # Gate 6: not single-event driven
    if len(net_returns) >= 2:
        best_idx = max(range(len(net_returns)), key=lambda i: net_returns[i])
        without_best = [x for j, x in enumerate(net_returns) if j != best_idx]
        mean_without_best = statistics.mean(without_best)
        if mean_without_best > 0:
            not_single_event_driven = True
        else:
            rejection_reasons.append(
                f"single_event_driven: mean_without_best={mean_without_best:.2f} bps"
            )
    else:
        rejection_reasons.append(
            "single_event_driven: only 1 valid event, trivially driven by one"
        )

    # gate 4/5 when baseline is absent — already in rejection_reasons
    # gate 4 (win rate) also needs checking when baseline is absent
    if not baseline_valid:
        if win_rate <= 0.5:
            rejection_reasons.append(
                f"win_rate_fails: {win_rate:.4f} (no baseline to compare)"
            )
        else:
            # win rate > 50% alone is still not enough if no baseline; gate 4
            # says "win rate > 50% OR beats baseline win rate" — with no
            # baseline we need >50% and gate 5 cannot pass, so overall
            # rejection stands from "does_not_beat_baseline_by_margin" above.
            pass

    candidate = len(rejection_reasons) == 0

    return {
        "candidate": candidate,
        "event_count": event_count,
        "mean_net_return_bps": round(mean_net, 4) if valid else None,
        "median_net_return_bps": round(median_net, 4) if valid else None,
        "win_rate": round(win_rate, 4) if valid else None,
        "baseline_mean_net": round(baseline_mean_net, 4) if baseline_mean_net is not None else None,
        "beats_baseline": beats_baseline,
        "not_single_event_driven": not_single_event_driven,
        "rejection_reasons": rejection_reasons,
    }


def _gate_result(
    event_count: int,
    rejection_reasons: list[str],
) -> dict[str, object]:
    """Return a gate dict when no valid forward returns exist."""
    return {
        "candidate": False,
        "event_count": event_count,
        "mean_net_return_bps": None,
        "median_net_return_bps": None,
        "win_rate": None,
        "baseline_mean_net": None,
        "beats_baseline": False,
        "not_single_event_driven": False,
        "rejection_reasons": rejection_reasons,
    }


# ---------------------------------------------------------------------------
# 5. Synthetic Fixture Generators
# ---------------------------------------------------------------------------


def generate_synthetic_positive_lead_lag_ticks(
    num_ticks: int = 500,
    start_ts_ns: int = 1_700_000_000_000_000_000,
    dt_ns: int = 500_000_000,
    base_price: float = 50_000.0,
    jump_interval: int = 20,
    jump_bps: float = 50.0,
    target_delay_ns: int = 2_000_000_000,
    catch_up_fraction: float = 0.8,
) -> tuple[list[TradeTickLite], list[TradeTickLite]]:
    """Generate synthetic ticks with a known positive lead-lag relationship.

    The source venue receives periodic upward price jumps every
    ``jump_interval`` ticks.  The target venue sees each jump after a fixed
    delay and only catches up ``catch_up_fraction`` of the move.

    Returns
    -------
    (source_ticks, target_ticks)
    """
    source_ticks: list[TradeTickLite] = []
    target_ticks: list[TradeTickLite] = []

    source_price = base_price

    for i in range(num_ticks):
        ts = start_ts_ns + i * dt_ns

        # Apply source jumps
        if i > 0 and i % jump_interval == 0:
            source_price *= 1.0 + jump_bps / 10000.0

        source_ticks.append(
            TradeTickLite(
                ts_event=ts,
                venue="SOURCE",
                symbol="BTC/USD",
                price=source_price,
                size=1.0,
                side="buy",
            )
        )

    # --- target price computation (delayed partial catch-up) ---
    pending = 0.0
    target_base = base_price
    last_source_price = base_price

    for i, src in enumerate(source_ticks):
        src_price = src.price

        if i > 0:
            new_move = src_price - last_source_price
            if abs(new_move) > 0:
                pending += new_move

        last_source_price = src_price

        # Catch up fraction of pending
        if pending != 0.0:
            catch = pending * catch_up_fraction
            target_base += catch
            pending -= catch

        target_ticks.append(
            TradeTickLite(
                ts_event=src.ts_event + target_delay_ns,
                venue="TARGET",
                symbol="BTC/USD",
                price=target_base,
                size=1.0,
                side="buy",
            )
        )

    return source_ticks, target_ticks


def generate_synthetic_no_edge_ticks(
    num_ticks: int = 500,
    start_ts_ns: int = 1_700_000_000_000_000_000,
    dt_ns: int = 500_000_000,
    base_price: float = 50_000.0,
    noise_std_bps: float = 5.0,
) -> tuple[list[TradeTickLite], list[TradeTickLite]]:
    """Generate synthetic ticks with no cross-venue edge.

    Both source and target are independent random walks with identical noise
    characteristics.
    """
    rng_source = random.Random(123)
    rng_target = random.Random(456)
    noise_fraction = noise_std_bps / 10000.0

    source_ticks: list[TradeTickLite] = []
    target_ticks: list[TradeTickLite] = []

    source_price = base_price
    target_price = base_price

    for i in range(num_ticks):
        ts = start_ts_ns + i * dt_ns

        source_price *= 1.0 + rng_source.gauss(0.0, noise_fraction)
        target_price *= 1.0 + rng_target.gauss(0.0, noise_fraction)

        source_ticks.append(
            TradeTickLite(
                ts_event=ts,
                venue="SOURCE",
                symbol="BTC/USD",
                price=source_price,
                size=1.0,
                side="buy",
            )
        )

        target_ticks.append(
            TradeTickLite(
                ts_event=ts,
                venue="TARGET",
                symbol="BTC/USD",
                price=target_price,
                size=1.0,
                side="buy",
            )
        )

    return source_ticks, target_ticks
