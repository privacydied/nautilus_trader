"""
Frozen constants for Family 2 funding crowding reversal precommitment.

Phase 0 precommitment constants only. No evaluation, no data fetching,
no report writing, no execution code.

Contains frozen threshold definitions, horizon definitions, direction
definitions, invariant labels, and helper utilities for past-only
percentile threshold computation.

Do NOT add:
- data fetching
- Binance API response parsing
- return evaluation
- report generation
- order or execution code
"""

from __future__ import annotations

import math
from collections.abc import Sequence


# ---------------------------------------------------------------------------
# FDR family
# ---------------------------------------------------------------------------

BTC_PRIMARY_FDR_FAMILY_SIZE: int = 60

# ---------------------------------------------------------------------------
# Threshold definitions
# ---------------------------------------------------------------------------

ABSOLUTE_THRESHOLDS: list[dict[str, object]] = [
    {"label": "abs_funding_ge_5bp", "min_abs_rate": 0.0005},
    {"label": "abs_funding_ge_10bp", "min_abs_rate": 0.0010},
    {"label": "abs_funding_ge_25bp", "min_abs_rate": 0.0025},
]

PERCENTILE_THRESHOLDS: list[dict[str, object]] = [
    {"label": "pct_funding_top_bottom_5pct", "percentile_rank": 5.0},
    {"label": "pct_funding_top_bottom_2.5pct", "percentile_rank": 2.5},
    {"label": "pct_funding_top_bottom_1pct", "percentile_rank": 1.0},
]

PERCENTILE_LOOKBACK_CALENDAR_DAYS: int = 180

ALL_THRESHOLDS: list[dict[str, object]] = list(ABSOLUTE_THRESHOLDS) + list(
    PERCENTILE_THRESHOLDS
)

# ---------------------------------------------------------------------------
# Horizon definitions (seconds)
# ---------------------------------------------------------------------------

HORIZONS: dict[str, int] = {
    "h4": 4 * 3600,       # 14,400 s
    "h8": 8 * 3600,       # 28,800 s
    "h12": 12 * 3600,     # 43,200 s
    "h24": 24 * 3600,     # 86,400 s
    "h48": 48 * 3600,     # 172,800 s
}

PRIMARY_HORIZON: str = "h24"
SECONDARY_HORIZONS: list[str] = ["h4", "h8", "h12", "h48"]

# ---------------------------------------------------------------------------
# Directions
# ---------------------------------------------------------------------------

DIRECTION_POSITIVE_FUNDING: str = "positive_funding_extreme"
DIRECTION_NEGATIVE_FUNDING: str = "negative_funding_extreme"

DIRECTIONS: list[str] = [DIRECTION_POSITIVE_FUNDING, DIRECTION_NEGATIVE_FUNDING]

# ---------------------------------------------------------------------------
# Cost model (from existing FeeModel defaults in config.py)
# Cost model (from existing FeeModel defaults in config.py)
# PRIMARY_COST_BPS: primary candidate-gating cost
# OPTIMISTIC_COST_BPS: diagnostic sensitivity tier (never gates promotion)

FEE_BPS: float = 5.0
SLIPPAGE_BPS: float = 1.0
QUOTE_MISMATCH_BUFFER_BPS: float = 0.0
TOTAL_COST_BPS: float = FEE_BPS + SLIPPAGE_BPS  # 6.0

PRIMARY_COST_BPS: float = 50.0  # 40 fee + 5 slippage + 5 buffer (gates promotion)
OPTIMISTIC_COST_BPS: float = TOTAL_COST_BPS  # 6.0 (diagnostic sensitivity only)

# ---------------------------------------------------------------------------
# Acceptance gates
# ---------------------------------------------------------------------------

MIN_VALID_EVENTS_FOR_DIAGNOSTIC: int = 50
MIN_VALID_EVENTS_FOR_CANDIDATE: int = 100
MIN_WIN_RATE: float = 0.55
BASELINE_BEAT_BPS: float = 10.0

# ---------------------------------------------------------------------------
# Train / holdout split
# ---------------------------------------------------------------------------

TRAIN_FRACTION: float = 0.70
HOLDOUT_FRACTION: float = 0.30

# ---------------------------------------------------------------------------
# Invariant labels (pinned strings for precommitment enforcement)
# ---------------------------------------------------------------------------

INVARIANT_BTC_FAMILY_SIZE: str = "BTC_PRIMARY_FDR_FAMILY_SIZE = 60"
INVARIANT_PAST_ONLY_PERCENTILE: str = "PAST_ONLY_PERCENTILE_INVARIANT"
INVARIANT_FUNDING_INTERVAL_METADATA: str = "FUNDING_INTERVAL_METADATA_REQUIRED"
INVARIANT_EVENT_DEDUP: str = "EVENT_DEDUP_RULE"
INVARIANT_CLUSTERING_UNADJUSTED: str = "FUNDING_EVENT_CLUSTERING_UNADJUSTED"
INVARIANT_DIRECTION_MATCHED_BASELINE: str = "DIRECTION_MATCHED_BASELINE_REQUIRED"
INVARIANT_TIMESTAMP_SHUFFLE_NULL_ONLY: str = "TIMESTAMP_SHUFFLE_NULL_ONLY"
INVARIANT_FDR_METHOD: str = "FDR_METHOD = BY (Benjamini-Yekutieli)"

# ---------------------------------------------------------------------------
# Direction mapping helpers
# ---------------------------------------------------------------------------


def signal_return_bps_for_positive_funding(
    forward_btc_spot_return_bps: float,
) -> float:
    """
    For positive funding extreme, signal return is negative BTC return.

    The hypothesis: positive funding (crowded long) predicts reversal down.
    """
    return -forward_btc_spot_return_bps


def signal_return_bps_for_negative_funding(
    forward_btc_spot_return_bps: float,
) -> float:
    """
    For negative funding extreme, signal return is positive BTC return.

    The hypothesis: negative funding (crowded short) predicts reversal up.
    """
    return forward_btc_spot_return_bps


def net_signal_return_bps(
    forward_btc_spot_return_bps: float,
    *,
    funding_positive: bool,
    total_cost_bps: float = PRIMARY_COST_BPS,
) -> float:
    """Compute net reversal return after cost."""
    raw = (
        signal_return_bps_for_negative_funding(forward_btc_spot_return_bps)
        if funding_positive is False
        else signal_return_bps_for_positive_funding(forward_btc_spot_return_bps)
    )
    return raw - total_cost_bps


# ---------------------------------------------------------------------------
# Past-only percentile helper (behavioral, needs sorted funding observations)
# ---------------------------------------------------------------------------


def compute_past_only_percentile_threshold(
    funding_rates_before_t: Sequence[float],
    percentile_rank: float,
) -> float:
    """
    Compute the percentile threshold using only observations before time t.

    Parameters
    ----------
    funding_rates_before_t:
        Sorted (chronological) funding rates observed strictly before the
        event timestamp. Must not include the event timestamp itself.
        Must have at least 1 observation.
    percentile_rank:
        Percentile rank in [0, 100]. E.g., 5.0 means top 5% / bottom 5%.

    Returns
    -------
    float
        The threshold value at the requested percentile. For the top
        percentile, use (100 - percentile_rank). For the bottom percentile,
        use percentile_rank.

    Raises
    ------
    ValueError
        If fewer than 1 observation provided.
    """
    n = len(funding_rates_before_t)
    if n < 1:
        raise ValueError(
            f"Need at least 1 observation before t, got {n}"
        )

    # Sort ascending for percentile computation
    sorted_rates = sorted(funding_rates_before_t)

    # Linear interpolation (same method as permutation_null.py _percentile)
    if n == 1:
        return sorted_rates[0]

    rank = percentile_rank / 100.0 * (n - 1)
    lower = math.floor(rank)
    upper = min(lower + 1, n - 1)
    frac = rank - lower

    return sorted_rates[lower] + frac * (sorted_rates[upper] - sorted_rates[lower])
