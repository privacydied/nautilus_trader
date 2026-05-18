"""Shared Family 1 same-venue quote-basis tick-level signal computation.

Precommitted signal definition:

  basis_bps(t)      = 10_000 * (P_usdt(t) - P_usd(t)) / P_usd(t)
  basis_change_bps  = basis_bps(t1) - basis_bps(t0)
  t0                = trigger_timestamp_ns - lookback_ms * 1_000_000
  t1                = trigger_timestamp_ns

  direction: basis_change_bps < 0 → long (+1), basis_change_bps > 0 → short (-1)
  raw_return_bps: direction * target_return_bps (unscaled by signal magnitude)

Timestamp matching: first tick at or after requested timestamp.
Missing tick rule: explicit exclusion, no interpolation, no fabrication.
Minimum lookback: 30_000 ms for Kraken trade history.

This module is observer-only. No execution, no orders, no private keys,
no trading adapters, no live trading.
"""

from __future__ import annotations

from typing import Any

_MS_TO_NS: int = 1_000_000


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def compute_family1_tick_signal(
    source_a_prices: list[Any],
    source_b_prices: list[Any],
    target_prices: list[Any],
    window_ids: list[str],
    windows: dict[str, dict[str, Any]],
    lookback_ms: int,
    horizon_ms: int,
    cost_total: float,
) -> tuple[list[float], list[float], int, int, list[str]]:
    """Compute Family 1 tick-basis signal and forward returns.

    Parameters
    ----------
    source_a_prices :
        Sorted list of price points for the first source leg (e.g. BTC/USD).
        Each item must have ``.timestamp_ns`` (int) and ``.price`` (float).
    source_b_prices :
        Sorted list of price points for the second source leg (e.g. BTC/USDT).
    target_prices :
        Sorted list of price points for the target (forward-return) leg.
    window_ids :
        Window IDs to evaluate.
    windows :
        Mapping from window_id to window dict (must contain
        ``trigger_timestamp_ns``).
    lookback_ms :
        Lookback window in milliseconds.
    horizon_ms :
        Forward-return horizon in milliseconds.
    cost_total :
        Total per-event cost in bps (fees + slippage + quote_mismatch).

    Returns
    -------
    raw_returns, net_returns, evaluated_count, valid_count, exclusions
    """
    exclusions: list[str] = []
    raw_returns: list[float] = []
    net_returns: list[float] = []
    evaluated = 0

    for window_id in window_ids:
        window = windows.get(window_id)
        if not window:
            continue

        trigger_ts = int(window["trigger_timestamp_ns"])
        t0_ts = trigger_ts - lookback_ms * _MS_TO_NS

        # --- prices at t0 (start of lookback) ---
        price_a_t0 = _first_at_or_after(source_a_prices, t0_ts)
        price_b_t0 = _first_at_or_after(source_b_prices, t0_ts)

        # --- prices at t1 (trigger = end of lookback) ---
        price_a_t1 = _first_at_or_after(source_a_prices, trigger_ts)
        price_b_t1 = _first_at_or_after(source_b_prices, trigger_ts)

        # --- entry and exit prices for forward return ---
        entry_price = _first_at_or_after(target_prices, trigger_ts)
        exit_price = _first_at_or_after(
            target_prices, trigger_ts + horizon_ms * _MS_TO_NS
        )

        evaluated += 1

        # --- missing-tick exclusions ---
        if None in (price_a_t0, price_b_t0):
            exclusions.append(f"missing_lookback_start:{window_id}")
            continue
        if None in (price_a_t1, price_b_t1):
            exclusions.append(f"missing_lookback_end:{window_id}")
            continue
        if entry_price is None:
            exclusions.append(f"missing_entry_price:{window_id}")
            continue
        if exit_price is None:
            exclusions.append(f"missing_exit_price:{window_id}")
            continue

        # --- basis computation ---
        # Precommitted formula: basis_bps(t) = 10_000 * (P_usdt - P_usd) / P_usd
        basis_bps_t0 = _basis_bps(price_a_t0, price_b_t0)
        basis_bps_t1 = _basis_bps(price_a_t1, price_b_t1)
        basis_change_bps = basis_bps_t1 - basis_bps_t0

        # --- direction from basis change sign ---
        # Precommitted mapping:
        #   basis_change_bps < 0 → long (+1)  — USDT cheaper, expect recovery
        #   basis_change_bps > 0 → short (-1) — USDT expensive, expect reversion
        direction = 1.0 if basis_change_bps < 0.0 else -1.0

        # --- forward return (unscaled by signal magnitude) ---
        target_return_bps = ((exit_price / entry_price) - 1.0) * 10_000.0
        raw_bps = direction * target_return_bps
        net_bps = raw_bps - cost_total

        raw_returns.append(raw_bps)
        net_returns.append(net_bps)

    return raw_returns, net_returns, evaluated, len(raw_returns), exclusions


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _basis_bps(price_a: float, price_b: float) -> float:
    """Compute signed basis in bps: (b - a) / a * 10000."""
    if price_a == 0.0:
        return 0.0
    return (price_b - price_a) / price_a * 10_000.0


def _first_at_or_after(
    points: list[Any], timestamp_ns: int
) -> float | None:
    """Return the price of the first point at or after *timestamp_ns*.

    Each *point* must have ``.timestamp_ns`` (int) and ``.price`` (float).
    Returns ``None`` if no such point exists.
    """
    for p in points:
        if p.timestamp_ns >= timestamp_ns:
            return p.price
    return None
