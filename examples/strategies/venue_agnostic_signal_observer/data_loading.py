"""Data loading utilities for the signal observer."""
import csv
from typing import List
from typing import Tuple


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_bars_from_csv(
    path: str,
    timestamp_col: str = "timestamp",
    close_col: str = "close",
) -> Tuple[List[float], List[float]]:
    """
    Load price bars from a CSV file.

    Returns (timestamps, close_prices) as parallel lists, sorted by timestamp.
    """
    timestamps: List[float] = []
    prices: List[float] = []

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts_val = row.get(timestamp_col, "")
            price_val = row.get(close_col, "")
            try:
                ts = float(ts_val)
                price = float(price_val)
            except (ValueError, TypeError):
                continue
            if price > 0:
                timestamps.append(ts)
                prices.append(price)

    combined = sorted(zip(timestamps, prices, strict=False))
    timestamps = [t for t, _ in combined]
    prices = [p for _, p in combined]
    return timestamps, prices


def load_signals_from_csv(path: str) -> List[dict]:
    """Load signal descriptors from a CSV (returns raw dicts)."""
    results = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            results.append(row)
    return results


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

def generate_synthetic_lead_lag(
    num_bars: int = 5_000,
    dt_seconds: float = 10.0,
    start_ts: float = 1_700_000_000.0,
    base_price: float = 50_000.0,
    lag_seconds: float = 30.0,
    target_catch_up_fraction: float = 0.8,
    jump_interval: int = 200,
    jump_bps: float = 50.0,
    noise_std_bps: float = 2.0,
) -> Tuple[List[float], List[float], List[float]]:
    """
    Generate synthetic source → target series where target follows source.

    The source has periodic jumps. The target follows with a *lag* and a
    configurable catch-up fraction.  If ``target_catch_up_fraction`` > 1.0
    (after fee deduction) the signals should show positive net expectancy;
    if < 0 they should be negative.

    Returns (source_timestamps, source_prices, target_prices).
    """
    import random
    random.seed(42)

    timestamps = [start_ts + i * dt_seconds for i in range(num_bars)]
    source_prices = [base_price]
    target_prices = [base_price]

    lag_steps = max(1, int(lag_seconds / dt_seconds))

    for i in range(1, num_bars):
        src = source_prices[-1]

        # Source noise
        src_noise_bps = random.gauss(0, noise_std_bps)
        src_new = src * (1 + src_noise_bps / 10_000.0)

        # Inject jump
        if i % jump_interval == 0:
            direction = random.choice([-1, 1])
            jump = direction * jump_bps
            src_new *= (1 + jump / 10_000.0)

        source_prices.append(src_new)

        # Target: follows source from ``lag_steps`` bars ago
        src_delayed_idx = max(0, i - lag_steps)
        src_delayed = source_prices[src_delayed_idx]

        tgt = target_prices[-1]
        move = (src_delayed - tgt) * target_catch_up_fraction
        tgt_noise = tgt * random.gauss(0, noise_std_bps / 10_000.0)
        target_prices.append(tgt + move + tgt_noise)

    return timestamps, source_prices, target_prices


def generate_synthetic_noise(
    num_bars: int = 5_000,
    dt_seconds: float = 10.0,
    start_ts: float = 1_700_000_000.0,
    base_price: float = 50_000.0,
    noise_std_bps: float = 5.0,
) -> Tuple[List[float], List[float], List[float]]:
    """
    Generate uncorrelated source and target series (no edge).

    Both series are pure random walks with no relationship.
    Any signals detected by the cross-market generator should produce
    negative net returns after fees since the target does not follow.

    Returns (source_timestamps, source_prices, target_prices).
    """
    import random
    random.seed(99)

    timestamps = [start_ts + i * dt_seconds for i in range(num_bars)]
    source_prices = [base_price]
    target_prices = [base_price]

    for _i in range(1, num_bars):
        src_new = source_prices[-1] * (1 + random.gauss(0, noise_std_bps) / 10_000.0)
        tgt_new = target_prices[-1] * (1 + random.gauss(0, noise_std_bps) / 10_000.0)
        source_prices.append(src_new)
        target_prices.append(tgt_new)

    return timestamps, source_prices, target_prices


def generate_synthetic_data(
    num_bars: int = 10_000,
    dt_seconds: float = 10.0,
    start_ts: float = 1_700_000_000.0,
    base_price: float = 50000.0,
    target_follow_delay: float = 30.0,
    target_follow_fraction: float = 0.7,
    noise_std_bps: float = 2.0,
    jump_threshold_bps: float = 50.0,
    jump_interval: int = 200,
) -> Tuple[List[float], List[float], List[float]]:
    """Legacy synthetic generator — delegates to ``generate_synthetic_lead_lag``."""
    return generate_synthetic_lead_lag(
        num_bars=num_bars,
        dt_seconds=dt_seconds,
        start_ts=start_ts,
        base_price=base_price,
        lag_seconds=target_follow_delay,
        target_catch_up_fraction=target_follow_fraction,
        jump_bps=jump_threshold_bps,
        noise_std_bps=noise_std_bps,
        jump_interval=jump_interval,
    )

