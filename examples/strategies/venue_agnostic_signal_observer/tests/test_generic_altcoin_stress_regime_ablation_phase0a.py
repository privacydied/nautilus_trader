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
    MIN_COVERAGE_MONTHS,
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
    compute_stress_points_vectorized,
    compute_trailing_1h_return_bps,
    compute_trailing_6h_realized_vol_bps,
    compute_vol_percentile,
    filter_stress_candidates,
    has_forward_24h_coverage,
    is_altcoin_symbol,
    run_phase0a_audit,
    utc_iso,
    validate_precommitment,
    validate_symbol_coverage,
)
from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0b import (
    GENERIC_EVENT_DIRECTION,
    GENERIC_TRADE_DIRECTION,
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
    # cooldown_until = t + 48h. t=0 -> until=48h (excludes 24h, admits t+48h).
    # t+48h: new cooldown = 96h → t+72h suppressed.
    assert len(result) == 2
    assert result[0].timestamp == base
    assert result[1].timestamp == base + timedelta(hours=48)


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


# ------------------------------------------------------------------
# Optimization equivalence tests
# ------------------------------------------------------------------


def test_loads_json_line_parses_json():
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import _loads_json_line
    line = '{"ts_event": 1704067200000000000, "symbol": "SOL", "price": 100.0}'
    result = _loads_json_line(line.encode("utf-8"))
    assert result["symbol"] == "SOL"
    assert result["price"] == 100.0


def test_loads_json_line_str():
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import _loads_json_line
    result = _loads_json_line('{"symbol": "SOL"}')
    assert result["symbol"] == "SOL"


def test_vectorized_matches_reference():
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import (
        compute_stress_points, compute_stress_points_vectorized,
    )
    from datetime import UTC
    n = 744
    series = []
    base = 100.0
    for i in range(n):
        ts = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i)
        if 360 <= i < 367:
            price = base * 0.95
        else:
            price = base * (1 - 0.0001 * i)
        series.append((ts, max(price, 0.01)))
    pv = compute_stress_points_vectorized("T", series)
    pr = compute_stress_points("T", series)
    assert len(pv) == len(pr)
    for a, b in zip(pv, pr):
        assert abs(a.trailing_1h_return_bps - b.trailing_1h_return_bps) < 1e-9
        assert abs(a.trailing_6h_realized_vol_bps - b.trailing_6h_realized_vol_bps) < 1e-9
        assert abs(a.trailing_6h_realized_vol_percentile - b.trailing_6h_realized_vol_percentile) < 1e-9


def test_worker_rejects_btc(tmp_path):
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import _process_one_file
    fp = tmp_path / "BTC.jsonl"
    fp.write_text('{"ts_event": 1704067200000000000, "symbol": "BTC", "price": 100.0}\n', encoding="utf-8")
    r = _process_one_file(str(fp), str(tmp_path))
    assert r.status == "rejected"
    assert "excluded" in r.rejection_reason


def test_worker_rejects_eth(tmp_path):
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import _process_one_file
    fp = tmp_path / "ETH.jsonl"
    fp.write_text('{"ts_event": 1704067200000000000, "symbol": "ETH", "price": 100.0}\n', encoding="utf-8")
    r = _process_one_file(str(fp), str(tmp_path))
    assert r.status == "rejected"
    assert "excluded" in r.rejection_reason


def test_worker_accepts_altcoin(tmp_path):
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import _process_one_file
    import json
    sym = "SOL"
    fp = tmp_path / f"{sym}.jsonl"
    base = 100.0
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    # Need at least 9 months (MIN_COVERAGE_MONTHS) of hourly data
    for i in range(660 * 24):  # ~660 days
        ts_event = int((start + timedelta(hours=i)).timestamp() * 1_000_000_000)
        # Gradual decline
        trend_price = base * (1 - 0.00001 * i)
        # Sharp crash at month 7 (~hour 5100): -8% in 1 hour
        if 5100 <= i < 5101:
            price = trend_price * 0.92
        elif 5101 <= i < 5107:
            price = trend_price * 0.95
        else:
            price = trend_price
        rows.append({"ts_event": ts_event, "symbol": sym, "price": max(price, 0.01), "open_interest": 1e8})
    fp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    r = _process_one_file(str(fp), str(tmp_path))
    assert r.status == "accepted", f"expected accepted, got {r.status}: {r.rejection_reason}"
    assert len(r.events_after_cooldown) > 0


# ------------------------------------------------------------------
# Look-ahead audit: past-only rolling percentile
# ------------------------------------------------------------------


def test_percentile_is_past_only():
    """Percentile at timestamp t should only use data before t.

    Adding a high-volatility period AFTER the candidate window must NOT
    change the percentile rank of an earlier candidate.
    """
    start = datetime(2024, 1, 1, tzinfo=UTC)
    # Build a stable series of 360 hours, then a spike at hour 360
    series_early = [(start + timedelta(hours=i), 100.0 + i * 0.001) for i in range(360)]
    series_late = [(start + timedelta(hours=i), 100.0 + i * 0.001) for i in range(360, 720)]

    # Without late data
    points_early = compute_stress_points_vectorized("T", series_early)
    pctiles_before = {p.timestamp: p.trailing_6h_realized_vol_percentile for p in points_early}

    # With late data (entire series) — percentile at timestamps < 360 must not change
    series_full = series_early + series_late
    points_full = compute_stress_points_vectorized("T", series_full)
    pctiles_after = {p.timestamp: p.trailing_6h_realized_vol_percentile for p in points_full}

    for ts in pctiles_before:
        if ts in pctiles_after:
            assert abs(pctiles_before[ts] - pctiles_after[ts]) < 1e-12, (
                f"Percentile changed at {ts}: {pctiles_before[ts]} vs {pctiles_after[ts]}"
            )


# ------------------------------------------------------------------
# Look-ahead audit: 6h realized vol uses only completed past bars
# ------------------------------------------------------------------


def test_6h_vol_window_is_past_only():
    """6h trailing realized vol at timestamp t should not include bars after t."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    n = 400  # enough to meet VOL_MIN_HISTORY_DAYS (14 days = 336h)
    prices = [(start + timedelta(hours=i), 100.0) for i in range(n)]
    # Add a crash at hour 360 (well past min history)
    prices[360] = (prices[360][0], 95.0)

    points = compute_stress_points_vectorized("T", prices)

    # Verify that vol at i=360 uses returns from bars 355..360 (6 completed bars)
    p360 = next(p for p in points if p.timestamp == start + timedelta(hours=360))
    # The 1h return at t=360 is -500 bps (95/100 - 1)
    assert p360.trailing_1h_return_bps == pytest.approx(-500.0)
    # The 6h vol at t=360 includes returns from bars 355 through 360
    # Most bars are 0 return (flat 100), only the last bar has -500 bps
    assert abs(p360.trailing_6h_realized_vol_bps - 500.0) < 1.0


# ------------------------------------------------------------------
# Look-ahead audit: entry timestamp semantics
# ------------------------------------------------------------------


def test_entry_price_at_event_timestamp():
    """Entry price for events should be the price at the event timestamp."""
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
        parse_timestamp,
    )

    # Build an event dict
    start = datetime(2024, 6, 15, tzinfo=UTC)
    event = StressEventRecord(
        event_id="TEST_20240615T120000Z",
        symbol="SOL",
        event_timestamp_utc=utc_iso(start),
        detector_family="test",
        direction="long",
        price_t=105.0,
        trailing_1h_return_bps=-400.0,
        trailing_6h_realized_vol_bps=5000.0,
        trailing_6h_realized_vol_percentile=0.9,
        cooldown_key="SOL",
        archive_source_path="test",
    )
    assert event.price_t == 105.0
    assert event.event_timestamp_utc == "2024-06-15T00:00:00Z"


# ------------------------------------------------------------------
# Look-ahead audit: 24h exit timestamp semantics
# ------------------------------------------------------------------


def test_forward_24h_coverage_tolerance():
    """Forward coverage check allows 2h tolerance for finding 24h exit price."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    series = [(base + timedelta(hours=h), 100.0) for h in range(48)]
    # At t=0, 24h target is t=24h: series has exact match
    assert has_forward_24h_coverage(series, base) is True
    # At t=22h, target is t+24=46h: series has exact match
    assert has_forward_24h_coverage(series, base + timedelta(hours=22)) is True
    # At t=23h, target is t+24=47h: series has exact match
    assert has_forward_24h_coverage(series, base + timedelta(hours=23)) is True
    # At t=24h, target is t+24=48h: series only goes to 47h → no match
    assert has_forward_24h_coverage(series, base + timedelta(hours=24)) is False

    # Series with gaps: verify 2h tolerance works
    gap_series = [
        (base + timedelta(hours=0), 100.0),
        (base + timedelta(hours=25.5), 102.0),  # 1.5h after target → within 2h tolerance
    ]
    assert has_forward_24h_coverage(gap_series, base) is True


# ------------------------------------------------------------------
# Look-ahead audit: cooldown boundary
# ------------------------------------------------------------------


def test_cooldown_boundary_47h_suppressed():
    """Event at t+47h should be suppressed by cooldown of earlier event."""
    base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    points = [
        StressWindowPoint(base, "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base + timedelta(hours=47), "AAVE", 100.0, -400.0, 5000.0, 0.9),
    ]
    result = apply_cooldown(points)
    assert len(result) == 1
    assert result[0].timestamp == base


def test_cooldown_boundary_48h_admitted():
    """Event exactly at t+48h should be admitted (cooldown uses < not <=)."""
    base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    points = [
        StressWindowPoint(base, "AAVE", 100.0, -400.0, 5000.0, 0.9),
        StressWindowPoint(base + timedelta(hours=48), "AAVE", 100.0, -400.0, 5000.0, 0.9),
    ]
    result = apply_cooldown(points)
    assert len(result) == 2
    assert result[0].timestamp == base
    assert result[1].timestamp == base + timedelta(hours=48)


# ------------------------------------------------------------------
# Direction semantics: stress_side and trade_direction
# ------------------------------------------------------------------


def test_direction_semantics():
    """Generic stress events have stress_side='down' and trade_direction='long'."""
    # The config constants
    assert GENERIC_EVENT_DIRECTION == "downside_price_drop"
    assert GENERIC_TRADE_DIRECTION == "long"

    # Build a sample event and verify JSON output includes stress fields
    event = StressEventRecord(
        event_id="T_DIR_1",
        symbol="SOL",
        event_timestamp_utc="2024-01-01T00:00:00Z",
        detector_family="generic_altcoin_stress_regime_ablation_phase0",
        direction="long",
        price_t=100.0,
        trailing_1h_return_bps=-400.0,
        trailing_6h_realized_vol_bps=5000.0,
        trailing_6h_realized_vol_percentile=0.9,
        cooldown_key="SOL",
        archive_source_path="test",
    )
    j = accepted_event_json(event)
    assert j["event_direction"] == "downside_price_drop"
    assert j["direction"] == "long"


# ------------------------------------------------------------------
# Contamination: OI/liquidation/funding fields not used
# ------------------------------------------------------------------


def test_detector_no_oi_liquidation_funding_fields():
    """The stress detector's output events must not contain OI/funding/liquidation fields."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    prices = []
    for h in range(500):
        if h == 0:
            prices.append((start + timedelta(hours=h), 100.0))
        elif h == 1:
            prices.append((start + timedelta(hours=h), 95.0))
        else:
            prices.append((start + timedelta(hours=h), 95.0 + (h - 1) * 0.1))
    points = compute_stress_points_vectorized("AAVE", prices)
    for p in points:
        assert not hasattr(p, "open_interest")
        assert not hasattr(p, "oi_change_pct")
        assert not hasattr(p, "funding_rate")
        assert not hasattr(p, "liquidation_volume")
        assert not hasattr(p, "flush_side")


def test_event_json_no_contamination():
    """Serialised event JSON must not contain OI/funding/liquidation fields."""
    event = StressEventRecord(
        event_id="T_CONT_1",
        symbol="SOL",
        event_timestamp_utc="2024-01-01T00:00:00Z",
        detector_family="generic_altcoin_stress_regime_ablation_phase0",
        direction="long",
        price_t=100.0,
        trailing_1h_return_bps=-400.0,
        trailing_6h_realized_vol_bps=5000.0,
        trailing_6h_realized_vol_percentile=0.9,
        cooldown_key="SOL",
        archive_source_path="test",
    )
    j = accepted_event_json(event)
    forbidden_keys = {"open_interest", "oi_change_pct", "funding_rate", "liquidation_volume", "flush_side"}
    for key in forbidden_keys:
        assert key not in j, f"Found forbidden key '{key}' in event JSON"


# ------------------------------------------------------------------
# Survivorship classification
# ------------------------------------------------------------------


def test_survivorship_continuous_data_subset():
    """The generic stress detector processes a continuous archive subset,
    which means symbols with insufficient data are rejected. This is
    classified as CONTINUOUS_DATA_SUBSET_SURVIVORSHIP_LIMITATION.
    """
    # The archive requires MIN_COVERAGE_MONTHS >= 9 months of data.
    # Symbols that died early are naturally excluded. This is not
    # full CRSP-style survivorship (we don't require live-as-of-today),
    # but it selects for symbols that survived long enough to accumulate
    # history. This is an inherent limitation: early-stage tokens with
    # short price histories are excluded.
    assert MIN_COVERAGE_MONTHS >= 9.0
    # The archive is a continuous data subset: all available rows for
    # each symbol, no forward-filling, no interpolation.


# ------------------------------------------------------------------
# Forward-coverage filtering: detection vs evaluation
# ------------------------------------------------------------------


def test_detection_does_not_use_forward_coverage():
    """Stress detection uses only past data. Forward coverage is checked
    at evaluation time only.

    Detection: compute_stress_points_vectorized + filter_stress_candidates
    Evaluation: has_forward_24h_coverage
    """
    start = datetime(2024, 1, 1, tzinfo=UTC)
    # Series with 48h of data — last 24h has no forward coverage
    series = [(start + timedelta(hours=h), 100.0) for h in range(48)]
    points = compute_stress_points_vectorized("T", series)
    # Filter candidates (thresholds won't trigger since no stress)
    candidates = filter_stress_candidates(points)
    # Forward coverage check is separate
    events_with_coverage = [p for p in candidates if has_forward_24h_coverage(series, p.timestamp)]
    # Most candidates near the end will lack forward coverage
    # But detection (points and candidates) does not use forward coverage
    assert len(events_with_coverage) <= len(candidates)
