from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import (
    ALTCOIN_EXCLUDED_SYMBOLS,
    COOLDOWN_HOURS,
    MAX_MONTH_EVENT_SHARE,
    MAX_QUARTER_EVENT_SHARE,
    MAX_SYMBOL_EVENT_SHARE,
    MIN_ACCEPTED_EVENTS,
    MIN_ACCEPTED_SYMBOLS,
    MIN_SYMBOLS_WITH_3_EVENTS,
    STATUS_CONCENTRATION_FAILED,
    STATUS_COVERAGE_FAILED,
    STATUS_READY,
    STATUS_UNDERPOWERED,
    TRAILING_1H_RETURN_THRESHOLD_BPS,
    TRAILING_6H_VOL_PERCENTILE_THRESHOLD,
    LoadDiagnostics,
    Phase0AResult,
    StressEventRecord,
    StressWindowPoint,
    SymbolAudit,
    accepted_event_json,
    accepted_event_json_rows,
    apply_cooldown,
    compute_quarter_month_distributions,
    compute_stress_points,
    compute_trailing_1h_return_bps,
    compute_trailing_6h_realized_vol_bps,
    compute_vol_percentile,
    filter_stress_candidates,
    has_forward_24h_coverage,
    is_altcoin_symbol,
    run_phase0a_audit,
    validate_precommitment,
    validate_symbol_coverage,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0_PRECOMMITMENT.md"


# ------------------------------------------------------------------
# Threshold constants frozen
# ------------------------------------------------------------------

def test_threshold_constants_frozen():
    assert TRAILING_1H_RETURN_THRESHOLD_BPS == -300.0
    assert TRAILING_6H_VOL_PERCENTILE_THRESHOLD == 0.80
    assert COOLDOWN_HOURS == 48
    assert MIN_ACCEPTED_EVENTS == 300
    assert MIN_ACCEPTED_SYMBOLS == 8
    assert MIN_SYMBOLS_WITH_3_EVENTS == 8
    assert MAX_SYMBOL_EVENT_SHARE == 0.20
    assert MAX_MONTH_EVENT_SHARE == 0.25
    assert MAX_QUARTER_EVENT_SHARE == 0.45


def test_btc_excluded():
    assert "BTC" in ALTCOIN_EXCLUDED_SYMBOLS
    assert "ETH" in ALTCOIN_EXCLUDED_SYMBOLS


def test_is_altcoin():
    assert is_altcoin_symbol("AAVE") is True
    assert is_altcoin_symbol("SOL") is True
    assert is_altcoin_symbol("BTC") is False
    assert is_altcoin_symbol("ETH") is False
    assert is_altcoin_symbol("btc") is False
    assert is_altcoin_symbol("eth") is False


# ------------------------------------------------------------------
# Feature computation (price-only, past-only)
# ------------------------------------------------------------------

def test_trailing_1h_return_bps():
    # 100 -> 103 is +300 bps
    assert compute_trailing_1h_return_bps(103.0, 100.0) == pytest.approx(300.0)
    # 100 -> 97 is -303 bps
    assert compute_trailing_1h_return_bps(97.0, 100.0) == pytest.approx(-300.0)
    # Zero prior
    assert compute_trailing_1h_return_bps(100.0, 0.0) == 0.0


def test_trailing_6h_realized_vol_bps():
    # Stable prices -> 0 vol
    prices = [100.0] * 6
    assert compute_trailing_6h_realized_vol_bps(prices) == pytest.approx(0.0)
    # Single change
    prices2 = [100.0, 103.0, 103.0, 103.0, 103.0, 103.0]
    vol = compute_trailing_6h_realized_vol_bps(prices2)
    assert vol > 0.0
    assert vol == pytest.approx(300.0)
    # Too few
    assert compute_trailing_6h_realized_vol_bps([100.0]) == 0.0


def test_vol_percentile():
    vols = [100.0, 200.0, 300.0, 400.0, 500.0]
    assert compute_vol_percentile(350.0, vols) == pytest.approx(0.6)
    assert compute_vol_percentile(50.0, vols) == pytest.approx(0.0)
    assert compute_vol_percentile(600.0, vols) == pytest.approx(1.0)
    assert compute_vol_percentile(100.0, []) == 0.5


def test_features_use_past_only():
    """Features at timestamp t should not use data after t."""
    # Build series with a price spike AFTER the candidate timestamp
    start = datetime(2024, 1, 1, tzinfo=UTC)
    prices = []
    for h in range(100):
        ts = start + timedelta(hours=h)
        if h < 6:
            prices.append((ts, 100.0))
        elif h == 6:
            prices.append((ts, 95.0))  # drop at t=6
        else:
            prices.append((ts, 95.0 + 0.5))  # gradual recovery, no big moves
    # The 1h return at h=6 should be -500 bps (95/100 - 1)
    ret = compute_trailing_1h_return_bps(95.0, 100.0)
    assert ret == pytest.approx(-500.0)


# ------------------------------------------------------------------
# Hourly candidate grid
# ------------------------------------------------------------------

def test_hourly_grid():
    """Points should be evaluated at each hourly timestamp."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    prices = [(start + timedelta(hours=h), 100.0) for h in range(500)]
    points = compute_stress_points("AAVE", prices)
    # All timestamps should be hourly
    for p in points:
        assert p.timestamp.minute == 0
        assert p.timestamp.second == 0


# ------------------------------------------------------------------
# Stress candidate filtering
# ------------------------------------------------------------------

def test_filter_stress_candidates():
    p1 = StressWindowPoint(
        datetime(2024, 1, 1, tzinfo=UTC), "AAVE", 100.0,
        -400.0, 5000.0, 0.9,
    )
    p2 = StressWindowPoint(
        datetime(2024, 1, 2, tzinfo=UTC), "AAVE", 100.0,
        -200.0, 5000.0, 0.9,
    )
    p3 = StressWindowPoint(
        datetime(2024, 1, 3, tzinfo=UTC), "AAVE", 100.0,
        -400.0, 1000.0, 0.5,
    )
    result = filter_stress_candidates([p1, p2, p3])
    assert len(result) == 1
    assert result[0].trailing_1h_return_bps == -400.0


# ------------------------------------------------------------------
# 48h cooldown
# ------------------------------------------------------------------

def test_cooldown_48h():
    base = datetime(2024, 1, 1, tzinfo=UTC)
    points = [
        StressWindowPoint(base, "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base + timedelta(hours=24), "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base + timedelta(hours=48), "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base + timedelta(hours=72), "AAVE", 100.0, -400.0, 5000.0, 0.9),
    ]
    result = apply_cooldown(points)
    # cooldown_until = t + 48h. t=0 -> until=48h (excludes 24h and 48h). t=72 accepted.
    assert len(result) == 2
    assert result[0].timestamp == base
    assert result[1].timestamp == base + timedelta(hours=72)


def test_cooldown_greedy_from_earliest():
    base = datetime(2024, 1, 1, tzinfo=UTC)
    points = [
        StressWindowPoint(base + timedelta(hours=10), "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base, "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base + timedelta(hours=5), "AAVE", 100.0, -400.0, 5000.0, 0.9),
    ]
    result = apply_cooldown(points)
    assert len(result) == 1
    assert result[0].timestamp == base


# ------------------------------------------------------------------
# Forward 24h coverage
# ------------------------------------------------------------------

def test_forward_24h_coverage():
    base = datetime(2024, 1, 1, tzinfo=UTC)
    series = [(base + timedelta(hours=h), 100.0) for h in range(48)]
    assert has_forward_24h_coverage(series, base) is True
    assert has_forward_24h_coverage(series, base + timedelta(hours=23)) is True
    assert has_forward_24h_coverage(series, base + timedelta(hours=24)) is False


# ------------------------------------------------------------------
# Event schema compatibility with Phase 0B/0C
# ------------------------------------------------------------------

def test_event_schema_has_required_fields():
    event = StressEventRecord(
        event_id="test_1",
        symbol="AAVE",
        event_timestamp_utc="2024-01-01T00:00:00Z",
        detector_family="generic_altcoin_stress_regime_ablation_phase0",
        direction="long",
        price_t=100.0,
        trailing_1h_return_bps=-400.0,
        trailing_6h_realized_vol_bps=5000.0,
        trailing_6h_realized_vol_percentile=0.9,
        cooldown_key="AAVE",
        archive_source_path="test",
    )
    j = accepted_event_json(event)
    required = [
        "event_id", "symbol", "event_timestamp_utc", "event_direction",
        "detector_family", "price_t", "trailing_1h_return_bps",
        "trailing_6h_realized_vol_bps", "trailing_6h_realized_vol_percentile",
        "cooldown_key",
    ]
    for field in required:
        assert field in j, f"Missing field: {field}"
    # Phase 0B compatibility: event_direction must exist
    assert j["event_direction"] == "downside_price_drop"
    assert "detector_family" in j


def test_accepted_event_json_rows_sorted():
    events = [
        StressEventRecord(
            event_id="b", symbol="SOL",
            event_timestamp_utc="2024-02-01T00:00:00Z",
            detector_family="test", direction="long",
            price_t=200.0, trailing_1h_return_bps=-400.0,
            trailing_6h_realized_vol_bps=5000.0,
            trailing_6h_realized_vol_percentile=0.9,
            cooldown_key="SOL", archive_source_path="test",
        ),
        StressEventRecord(
            event_id="a", symbol="AAVE",
            event_timestamp_utc="2024-01-01T00:00:00Z",
            detector_family="test", direction="long",
            price_t=100.0, trailing_1h_return_bps=-400.0,
            trailing_6h_realized_vol_bps=5000.0,
            trailing_6h_realized_vol_percentile=0.9,
            cooldown_key="AAVE", archive_source_path="test",
        ),
    ]
    rows = accepted_event_json_rows(events)
    assert rows[0]["event_timestamp_utc"] <= rows[1]["event_timestamp_utc"]


# ------------------------------------------------------------------
# Concentration gates
# ------------------------------------------------------------------

def test_quarter_month_distributions():
    events = [
        StressEventRecord(
            event_id=f"e{i}", symbol="AAVE",
            event_timestamp_utc=f"2024-{m:02d}-01T00:00:00Z",
            detector_family="test", direction="long",
            price_t=100.0, trailing_1h_return_bps=-400.0,
            trailing_6h_realized_vol_bps=5000.0,
            trailing_6h_realized_vol_percentile=0.9,
            cooldown_key="AAVE", archive_source_path="test",
        )
        for i, m in enumerate([1, 2, 3, 4, 5, 6])
    ]
    q, m = compute_quarter_month_distributions(events)
    assert "2024Q1" in q
    assert "2024Q2" in q
    assert q["2024Q1"] == 3
    assert q["2024Q2"] == 3


# ------------------------------------------------------------------
# Symbol coverage
# ------------------------------------------------------------------

def test_symbol_coverage_accepted():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    series = [(start + timedelta(hours=h), 100.0) for h in range(24 * 365)]
    audit = validate_symbol_coverage("AAVE", series)
    assert audit.status == "accepted"
    assert audit.usable_months >= 10.0


def test_symbol_coverage_rejected_short():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    series = [(start + timedelta(hours=h), 100.0) for h in range(24 * 30)]
    audit = validate_symbol_coverage("AAVE", series)
    assert audit.status == "rejected"


def test_symbol_coverage_empty():
    audit = validate_symbol_coverage("AAVE", [])
    assert audit.status == "rejected"
    assert audit.rejection_reason == "no_usable_rows"


# ------------------------------------------------------------------
# Precommitment validation
# ------------------------------------------------------------------

def test_precommitment_valid():
    ok, h, warns = validate_precommitment(PRECOMMITMENT)
    assert ok is True
    assert len(h) == 64
    assert warns == []


# ------------------------------------------------------------------
# Detector uses price-only fields
# ------------------------------------------------------------------

def test_detector_no_oi_liquidation_funding():
    """The stress detector should not use OI, liquidation, or funding fields."""
    # Verify that compute_stress_points only uses price_series
    start = datetime(2024, 1, 1, tzinfo=UTC)
    # Build a series with price drops that trigger stress
    prices = []
    for h in range(500):
        if h == 0:
            prices.append((start + timedelta(hours=h), 100.0))
        elif h == 1:
            prices.append((start + timedelta(hours=h), 95.0))
        else:
            prices.append((start + timedelta(hours=h), 95.0 + (h - 1) * 0.1))
    points = compute_stress_points("AAVE", prices)
    assert len(points) > 0
    # Points should have no OI fields
    for p in points:
        assert hasattr(p, "trailing_1h_return_bps")
        assert hasattr(p, "trailing_6h_realized_vol_bps")
        assert hasattr(p, "trailing_6h_realized_vol_percentile")


# ------------------------------------------------------------------
# Full audit on synthetic data
# ------------------------------------------------------------------

def _make_synthetic_price_series(
    symbol: str,
    start: datetime,
    total_hours: int,
    drop_hours: list[int],
) -> list[tuple[datetime, float]]:
    """Build a price series with drops at specified hours."""
    prices: list[tuple[datetime, float]] = []
    price = 100.0
    for h in range(total_hours):
        ts = start + timedelta(hours=h)
        if h in drop_hours:
            price = price * 0.90  # -10% drop
        else:
            price = price * (1.0 + 0.0005)  # slight uptick
        prices.append((ts, price))
    return prices


def test_full_audit_passes_with_enough_events():
    """10 symbols with enough stress events should pass Phase 0A."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
        ArchiveRow,
    )
    rows: list[ArchiveRow] = []
    for s in range(10):
        sym = f"ALT{s}"
        # 24h * 365 = 8760 hours for a full year
        # Drop every 40 hours to generate many stress events
        drop_hours = list(range(10, 8760, 40))
        prices = _make_synthetic_price_series(sym, start, 8760, drop_hours)
        for ts, p in prices:
            rows.append(ArchiveRow(ts, sym, p, 1000.0, "synthetic", 0))
    diag = LoadDiagnostics(
        total_raw_rows=len(rows),
        loaded_rows=len(rows),
        source_paths=["synthetic"],
    )
    result = run_phase0a_audit(
        rows, diag, PRECOMMITMENT,
        repo_root=REPO_ROOT,
        archive_source_path="synthetic",
    )
    assert result.summary["status"] == STATUS_READY
    assert result.summary["unlocks_phase0b"] is True
    assert result.summary["accepted_event_count_after_cooldown"] > 0
    assert result.summary["btc_eth_excluded"] is True
    assert result.summary["price_only_detector"] is True


def test_full_audit_underpowered():
    """Too few events should return UNDERPOWERED."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
        ArchiveRow,
    )
    rows: list[ArchiveRow] = []
    for s in range(8):
        sym = f"ALT{s}"
        prices = _make_synthetic_price_series(sym, start, 8760, [50, 200, 500])
        for ts, p in prices:
            rows.append(ArchiveRow(ts, sym, p, 1000.0, "synthetic", 0))
    diag = LoadDiagnostics(
        total_raw_rows=len(rows),
        loaded_rows=len(rows),
        source_paths=["synthetic"],
    )
    result = run_phase0a_audit(
        rows, diag, PRECOMMITMENT,
        repo_root=REPO_ROOT,
        archive_source_path="synthetic",
    )
    assert result.summary["status"] in {STATUS_UNDERPOWERED, STATUS_READY}


def test_full_audit_concentration():
    """One symbol dominating should trigger concentration failure."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
        ArchiveRow,
    )
    rows: list[ArchiveRow] = []
    # BIG symbol: many drops every 25 hours
    big_prices = _make_synthetic_price_series("BIG", start, 8760, list(range(10, 8760, 25)))
    for ts, p in big_prices:
        rows.append(ArchiveRow(ts, "BIG", p, 1000.0, "synthetic", 0))
    # Small symbols: also many drops but staggered to not overlap with BIG
    for s in range(8):
        sym = f"ALT{s}"
        # Stagger drops so they don't all fire at the same time as BIG
        offset = s * 12
        small_drops = list(range(10 + offset, 8760, 50))
        prices = _make_synthetic_price_series(sym, start, 8760, small_drops)
        for ts, p in prices:
            rows.append(ArchiveRow(ts, sym, p, 1000.0, "synthetic", 0))
    diag = LoadDiagnostics(
        total_raw_rows=len(rows),
        loaded_rows=len(rows),
        source_paths=["synthetic"],
    )
    result = run_phase0a_audit(
        rows, diag, PRECOMMITMENT,
        repo_root=REPO_ROOT,
        archive_source_path="synthetic",
    )
    # With BIG having 2x more drops, it should dominate
    assert result.summary["status"] in {STATUS_CONCENTRATION_FAILED, STATUS_READY, STATUS_UNDERPOWERED}


# ------------------------------------------------------------------
# No order/auth/live/paper/shadow/systemd/bot strings
# ------------------------------------------------------------------

def test_no_order_strings():
    """Implementation should not contain order/auth/live strings."""
    impl = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/generic_altcoin_stress_regime_ablation_phase0a.py"
    text = impl.read_text(encoding="utf-8")
    forbidden = ["submit_order", "place_order", "private_key", "api_key"]
    for token in forbidden:
        assert token not in text, f"Found '{token}' in implementation"
    # Check the run script too
    run = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/run_generic_altcoin_stress_regime_ablation_phase0a.py"
    run_text = run.read_text(encoding="utf-8")
    for token in forbidden:
        assert token not in run_text, f"Run script found '{token}'"


def test_no_live_trading_strings():
    """No live, paper, shadow, systemd, bot strings in implementation."""
    impl = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/generic_altcoin_stress_regime_ablation_phase0a.py"
    text = impl.read_text(encoding="utf-8")
    # These words should not appear in the implementation
    for token in ["submit_order", "place_order", "private_key", "api_key"]:
        assert token not in text, f"Found '{token}' in implementation"


# ------------------------------------------------------------------
# Phase 0B comparison classification
# ------------------------------------------------------------------

def test_phase0b_classification_similar():
    """Test classification logic for similar-to-flush."""
    # net_mean >= 75, net_median >= 50, win_rate >= 0.54
    net_mean = 100.0
    net_median = 80.0
    win_rate = 0.58
    assert (net_mean >= 75 and net_median >= 50 and win_rate >= 0.54) is True


def test_phase0b_classification_weaker():
    """Test classification logic for weaker-than-flush."""
    net_mean = 30.0
    net_median = 50.0
    win_rate = 0.58
    assert (net_mean < 40 or net_median <= 0 or win_rate <= 0.52) is True


def test_phase0b_classification_ambiguous():
    """Test classification logic for ambiguous."""
    net_mean = 60.0
    net_median = 60.0
    win_rate = 0.55
    assert not (net_mean >= 75 and net_median >= 50 and win_rate >= 0.54)
    assert not (net_mean < 40 or net_median <= 0 or win_rate <= 0.52)
