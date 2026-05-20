"""Kline prefilter V1 — per-bar high-low candidate day filter.

Replaces compute_kline_candidate_days (which used daily-range and was too loose).

Design: docs/KLINE_PREFILTER_V1_DESIGN.md

SAFETY_MODE = "public_data_observer_only" — no network, no orders, no auth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

SAFETY_MODE = "public_data_observer_only"

# Threshold: per-1m-bar high-low range in basis points.
# Derived from the tick-level stress rule (30s/30bps) with a noise assumption:
# the non-impulse half of the minute exhibits at least ~45 bps of additional
# movement, so a 30s/30bps event projects to a containing bar with HL >= 75 bps.
# See docs/KLINE_PREFILTER_V1_DESIGN.md for the full justification.
HL_THRESHOLD_BPS = 75.0


def compute_kline_candidate_days_v1(
    klines_by_source: Dict[str, List[Dict[str, Any]]],
    *,
    hl_threshold_bps: float = HL_THRESHOLD_BPS,
) -> Set[str]:
    """Identify candidate stress days using per-bar high-low range.

    A day is included if ANY single 1-minute bar within that day has a
    high-low range >= hl_threshold_bps basis points (relative to the bar's open).

    This is a STRICT SUPERSET of the tick-level 30s/30bps stress rule:
    any 30-second window with >= 30bps move is contained in exactly one 1m bar,
    and that bar's HL range must be >= the sub-interval move.

    Threshold K = 75 bps is derived from the noise assumption in
    docs/KLINE_PREFILTER_V1_DESIGN.md: the non-impulse half of the minute
    exhibits at least ~45 bps of additional movement, so a 30s/30bps event
    projects to a containing bar with HL >= 75 bps.

    Parameters
    ----------
    klines_by_source : dict
        source_symbol -> list of kline dicts with keys:
        open_time_ns, open, high, low, close, volume
    hl_threshold_bps : float
        Per-bar high-low threshold in basis points. Default: 75.0.

    Returns
    -------
    Set of date strings (YYYY-MM-DD) that are candidate stress days.
    """
    candidate_dates: Set[str] = set()

    for src_sym, klines in klines_by_source.items():
        if not klines:
            continue

        for k in klines:
            open_price = float(k["open"]) if k.get("open") is not None else 0.0
            high = float(k["high"]) if k.get("high") is not None else 0.0
            low = float(k["low"]) if k.get("low") is not None else 0.0

            if open_price <= 0 or high <= 0 or low <= 0:
                continue

            hl_bps = (high - low) / open_price * 10_000.0

            if hl_bps >= hl_threshold_bps:
                # Extract calendar date from the bar's open time
                ts_ns = k.get("open_time_ns", 0)
                ts_sec = ts_ns // 1_000_000_000
                from datetime import datetime, timezone

                dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
                date_key = dt.strftime("%Y-%m-%d")
                candidate_dates.add(date_key)

    return candidate_dates


# ---------------------------------------------------------------------------
# Deprecated: original compute_kline_candidate_days (daily-range, too loose).
# Kept for A/B comparison only — do not call from the runner.
# ---------------------------------------------------------------------------

def compute_kline_candidate_days_deprecated(
    klines_by_source: Dict[str, List[Dict[str, Any]]],
    *,
    hl_threshold_bps: float = 30.0,
    oc_threshold_bps: float = 50.0,
) -> Set[str]:
    """Original daily-range prefilter. DEPRECATED — too loose for pruning.

    Returns set of candidate dates (simplified interface).
    """
    from datetime import datetime, timezone

    candidate_dates: Set[str] = set()

    for src_sym, klines in klines_by_source.items():
        if not klines:
            continue

        day_bars: Dict[str, List[Dict[str, Any]]] = {}
        for k in klines:
            ts_ns = k.get("open_time_ns", 0)
            ts_sec = ts_ns // 1_000_000_000
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            date_key = dt.strftime("%Y-%m-%d")
            day_bars.setdefault(date_key, []).append(k)

        for date_key, bars in day_bars.items():
            max_hl = 0.0
            max_oc = 0.0

            for k in bars:
                o = float(k["open"]) if k.get("open") is not None else 0.0
                h = float(k["high"]) if k.get("high") is not None else 0.0
                l_val = float(k["low"]) if k.get("low") is not None else 0.0
                c = float(k["close"]) if k.get("close") is not None else 0.0

                if o <= 0:
                    continue

                hl = (h - l_val) / o * 10_000.0 if h > 0 and l_val > 0 else 0.0
                oc = abs(c - o) / o * 10_000.0

                if hl > max_hl:
                    max_hl = hl
                if oc > max_oc:
                    max_oc = oc

            if max_hl >= hl_threshold_bps or max_oc >= oc_threshold_bps:
                candidate_dates.add(date_key)

    return candidate_dates
