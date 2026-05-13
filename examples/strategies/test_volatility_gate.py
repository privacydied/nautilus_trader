"""Minimal tests for volatility_gate helper functions.

No network mocking needed -- these test pure numeric helpers only.
"""
from examples.strategies.volatility_gate import (
    compute_range,
    compute_range_3h,
    compute_1h_ranges,
    hourly_bar_freshness,
    compute_fast_diagnostic,
)


def _bar(time, open, high, low, close):
    return {"time": time, "open": float(open), "high": float(high), "low": float(low), "close": float(close)}


# --- compute_range ---
def test_range_single_bar():
    bars = [_bar(0, 100, 102, 98, 101)]
    # (102-98)/98 * 10000 = 408.16...
    result = compute_range(bars)
    assert abs(result - 408.16) < 0.1


def test_range_single_bar_correct():
    bars = [_bar(0, 100, 110, 90, 105)]
    result = compute_range(bars)
    expected = ((110 - 90) / 90) * 10000
    assert abs(result - expected) < 0.01


def test_range_multiple_bars():
    bars = [
        _bar(0, 100, 102, 98, 101),
        _bar(60, 101, 105, 95, 100),
        _bar(120, 100, 110, 99, 108),
    ]
    result = compute_range(bars)
    overall_high = 110
    overall_low = 95
    expected = ((overall_high - overall_low) / overall_low) * 10000
    assert abs(result - expected) < 0.01


def test_range_empty():
    assert compute_range([]) is None


# --- compute_range_3h ---
def test_three_hour_range():
    bars = [
        _bar(0, 100, 101, 99, 100),
        _bar(3600, 100, 102, 98, 101),
        _bar(7200, 101, 108, 100, 107),
    ]
    result = compute_range_3h(bars)
    # last 3 bars: highs=[101,102,108], lows=[99,98,100]
    expected = ((108 - 98) / 98) * 10000
    assert abs(result - expected) < 0.01


def test_three_hour_range_too_few():
    bars = [_bar(0, 100, 101, 99, 100), _bar(3600, 100, 102, 98, 101)]
    assert compute_range_3h(bars) is None


# --- compute_1h_ranges ---
def test_one_hour_ranges_two_bars():
    bars = [
        _bar(0, 100, 103, 97, 101),
        _bar(3600, 101, 105, 99, 104),
    ]
    cur, prev = compute_1h_ranges(bars)
    assert abs(cur - ((105 - 99) / 99) * 10000) < 0.01
    assert abs(prev - ((103 - 97) / 97) * 10000) < 0.01


def test_one_hour_ranges_insufficient():
    bars = [_bar(0, 100, 102, 98, 101)]
    cur, prev = compute_1h_ranges(bars)
    assert cur is None
    assert prev is None


# --- hourly_bar_freshness ---
def test_freshness_none():
    f = hourly_bar_freshness(None)
    assert f["gate_data_stale_or_unchanged"] is True
    assert f["latest_hourly_bar_timestamp"] is None


def test_freshness_real_timestamp(monkeypatch):
    """Test that freshness computes age from a known timestamp."""
    bar_ts = 1700000000
    bars = [_bar(bar_ts, 100, 101, 99, 100)]
    f = hourly_bar_freshness(bars)
    assert f["latest_hourly_bar_timestamp"] == bar_ts
    assert f["latest_hourly_bar_age_seconds"] > 0
    assert f["next_hourly_bar_expected_utc"] is not None
    # The bar is very old so not stale
    assert f["gate_data_stale_or_unchanged"] is False


# --- compute_fast_diagnostic ---
def test_fast_diagnostic_insufficient():
    result = compute_fast_diagnostic(None, None)
    assert result["BTC/USD"]["error"] == "insufficient_1m_bars"
    assert result["ETH/USD"]["error"] == "insufficient_1m_bars"


def test_fast_diagnostic_enough_for_15m_only():
    # Create 20 bars -- enough for 15m but not 30m
    bars = []
    base_time = 1700000000
    for i in range(20):
        bars.append(_bar(base_time + i * 60, 100, 101, 99, 100))
    result = compute_fast_diagnostic(bars, None)
    assert "range_15m_bps" in result["BTC/USD"]
    assert result["BTC/USD"]["range_15m_bps"] is not None
    assert result["BTC/USD"]["range_30m_bps"] is None


def test_fast_diagnostic_acceleration():
    # Create 60 bars: flat first 45 min, volatile last 15 min
    bars = []
    base_time = 1700000000
    for i in range(45):
        bars.append(_bar(base_time + i * 60, 100, 100.1, 99.9, 100))
    for i in range(15):
        bars.append(_bar(base_time + (45 + i) * 60, 100, 104, 96, 102))
    result = compute_fast_diagnostic(bars, None)
    btc = result["BTC/USD"]
    assert btc["accel_15m_ratio"] is not None
    # The current 15m range is much larger than previous 15m
    assert btc["accel_15m_ratio"] > 2.0
    assert btc["fast_acceleration_state"] == "ACCELERATING"


def test_fast_diagnostic_building():
    # Create 30 bars with moderate range (~20 bps over 15m, below active threshold of 30)
    bars = []
    base_time = 1700000000
    for i in range(30):
        bars.append(_bar(base_time + i * 60, 100, 100.10, 100 - 0.10, 100.05))
    result = compute_fast_diagnostic(bars, None)
    btc = result["BTC/USD"]
    assert btc["range_15m_bps"] is not None and btc["range_15m_bps"] > 0
    # 10 + 10 = 20 bps range, which is below 30 bps active but >= 15 (half threshold) = BUILDING
    assert btc["fast_market_state"] in ("BUILDING", "QUIET")
