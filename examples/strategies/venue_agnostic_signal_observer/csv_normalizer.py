"""Normalize and align multi-venue OHLCV CSVs to a common timestamp grid.

For lead-lag experiments we need source and target series on the same
timebase so we can compute price moves at a given lookback and measure
forward returns cleanly.

Strategy:
  - Load two CSVs (source venue, target venue)
  - Resample both to a common uniform grid (e.g. every 1s, 10s, 1m)
  - Forward-fill gaps (up to a configurable max gap)
  - Return aligned (timestamps, source_prices, target_prices) triples
"""
import csv
from typing import List, Tuple, Optional


def load_ohlc_csv(path: str) -> List[dict]:
    """Load OHLCV CSV into a list of dicts."""
    rows: List[dict] = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                ts_str = row.get("timestamp", "")
                close_str = row.get("close", "")
                if not ts_str or not close_str:
                    continue
                ts = float(ts_str)
                close = float(close_str)
                if close > 0:
                    open_str = row.get("open", close_str)
                    high_str = row.get("high", close_str)
                    low_str = row.get("low", close_str)
                    vol_str = row.get("volume", "0")
                    rows.append({
                        "timestamp": ts,
                        "open": float(open_str) if open_str else close,
                        "high": float(high_str) if high_str else close,
                        "low": float(low_str) if low_str else close,
                        "close": close,
                        "volume": float(vol_str) if vol_str else 0,
                    })
            except (ValueError, TypeError):
                continue
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def resample_to_grid(
    rows: List[dict],
    grid_start: float,
    grid_end: float,
    grid_interval: float,
    max_gap: float = 0.0,
    method: str = "ffill",
) -> Tuple[List[float], List[float]]:
    """Resample irregular price data to a uniform time grid.

    Args:
        rows: sorted OHLCV dicts from load_ohlc_csv
        grid_start: start of the uniform grid (unix seconds)
        grid_end: end of the uniform grid
        grid_interval: spacing between grid points in seconds
        max_gap: maximum forward-fill gap in seconds. Bars older than
                 this relative to the grid point are set to None.
                 If 0, no max_gap applied (fill from any prior bar).
        method: "ffill" (forward-fill, default) or "none" (no filling)

    Returns:
        (timestamps, prices) on the uniform grid. None prices are excluded.
    """
    if not rows:
        return [], []

    timestamps: List[float] = []
    prices: List[float] = []

    row_idx = 0
    n_rows = len(rows)
    last_valid_price: Optional[float] = None
    last_valid_ts: Optional[float] = None

    grid_ts = grid_start
    while grid_ts <= grid_end:
        # Advance row_idx to include all rows <= grid_ts
        while row_idx < n_rows and rows[row_idx]["timestamp"] <= grid_ts:
            last_valid_price = rows[row_idx]["close"]
            last_valid_ts = rows[row_idx]["timestamp"]
            row_idx += 1

        price = None
        if method == "ffill" and last_valid_price is not None:
            if max_gap > 0 and last_valid_ts is not None:
                gap = grid_ts - last_valid_ts
                if gap <= max_gap:
                    price = last_valid_price
            else:
                price = last_valid_price
        elif method == "none" and last_valid_ts is not None and last_valid_ts == grid_ts:
            price = last_valid_price

        if price is not None:
            timestamps.append(grid_ts)
            prices.append(price)

        grid_ts += grid_interval

    return timestamps, prices


def align_venues(
    source_csv: str,
    target_csv: str,
    grid_interval: float,
    max_gap: float = 0.0,
    overlap_only: bool = True,
) -> Tuple[List[float], List[float], List[float]]:
    """Load two venue CSVs and resample them to a shared grid.

    Args:
        source_csv: path to source venue OHLCV CSV
        target_csv: path to target venue OHLCV CSV
        grid_interval: common grid spacing in seconds
        max_gap: forward-fill gap limit in seconds
        overlap_only: if True, restrict grid to the time range where
                      both venues have data

    Returns:
        (timestamps, source_prices, target_prices) — same length lists
    """
    source_rows = load_ohlc_csv(source_csv)
    target_rows = load_ohlc_csv(target_csv)

    if not source_rows or not target_rows:
        raise ValueError("One or both CSV files have no valid data")

    if overlap_only:
        grid_start = max(source_rows[0]["timestamp"], target_rows[0]["timestamp"])
        grid_end = min(source_rows[-1]["timestamp"], target_rows[-1]["timestamp"])
    else:
        grid_start = min(source_rows[0]["timestamp"], target_rows[0]["timestamp"])
        grid_end = max(source_rows[-1]["timestamp"], target_rows[-1]["timestamp"])

    # Align grid_start to the interval
    grid_start = grid_start - (grid_start % grid_interval)

    source_ts, source_prices = resample_to_grid(
        source_rows, grid_start, grid_end, grid_interval, max_gap=max_gap,
    )
    target_ts, target_prices = resample_to_grid(
        target_rows, grid_start, grid_end, grid_interval, max_gap=max_gap,
    )

    # Interleave: keep only timestamps present in both
    ts_set = set(source_ts) & set(target_ts)
    common = sorted(ts_set)

    # Price lookup
    src_price = dict(zip(source_ts, source_prices))
    tgt_price = dict(zip(target_ts, target_prices))

    timestamps = common
    s_prices = [src_price[t] for t in common]
    t_prices = [tgt_price[t] for t in common]

    return timestamps, s_prices, t_prices
