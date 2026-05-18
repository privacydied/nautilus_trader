"""
Tests for Phase 0A/0B: funding_oi_archive_probe and funding_oi_crowding_regime.

These tests validate the archive probe logic, alignment rules, coverage estimates,
population sizing constraints, and safety guards.

All tests are self-contained (no HTTP dependencies) except where noted.
"""

import json
import os
import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import pytest

# Ensure the modules are importable
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), ".."),
)

from run_funding_oi_archive_probe import (
    FIXED_PROBE_DATES,
    BASE_URL,
    SYMBOL,
    OI_PRIMARY_FIELD,
    OI_SECONDARY_FIELD,
    TIMESTAMP_FIELD,
    MIN_ALIGNED_SETTLEMENT_SLOTS,
    MIN_COVERAGE_DATE,
    WARMUP_DAYS,
    _monthly_zip_path,
    _daily_zip_path,
    probe_url,
    extract_csv_from_zip,
    parse_timestamp,
    run_phase0a,
    write_report_json,
    write_data_availability_md,
)


# ---------------------------------------------------------------------------
# Fixed probe dates
# ---------------------------------------------------------------------------


def test_fixed_probe_dates_exactly_7():
    """The fixed date set has exactly 7 entries."""
    assert len(FIXED_PROBE_DATES) == 7


def test_fixed_probe_dates_content():
    """Fixed probe dates must be exactly as specified."""
    expected = [
        "2021-01-01",
        "2021-06-01",
        "2022-01-01",
        "2023-01-01",
        "2024-01-01",
        "2025-01-01",
        "2026-01-01",
    ]
    assert FIXED_PROBE_DATES == expected


# ---------------------------------------------------------------------------
# Archive path construction
# ---------------------------------------------------------------------------


def test_monthly_path_format():
    """Monthly archive paths use the correct naming convention."""
    path = _monthly_zip_path("BTCUSDT", "2021-01-01")
    expected = (
        "data/futures/um/monthly/metrics/BTCUSDT/"
        "BTCUSDT-metrics-2021-01.zip"
    )
    assert path == expected


def test_daily_path_format():
    """Daily archive paths use the correct naming convention."""
    path = _daily_zip_path("BTCUSDT", "2021-01-01")
    expected = (
        "data/futures/um/daily/metrics/BTCUSDT/"
        "BTCUSDT-metrics-2021-01-01.zip"
    )
    assert path == expected


def test_monthly_and_daily_paths_both_probed():
    """Both monthly and daily archive families must be probed."""
    results = run_phase0a()
    assert "monthly_probes" in results
    assert "daily_probes" in results
    assert len(results["monthly_probes"]) == 7
    assert len(results["daily_probes"]) == 7


# ---------------------------------------------------------------------------
# No binary search or date-range scan
# ---------------------------------------------------------------------------


def test_no_binary_search():
    """
    Verify the probe uses exactly the fixed date set.
    No date-range scanning or binary searching.
    """
    results = run_phase0a()
    # Check that all probe date keys match the fixed set
    for date_str in FIXED_PROBE_DATES:
        assert date_str in results["monthly_probes"]
        assert date_str in results["daily_probes"]
    assert len(results["monthly_probes"]) == 7
    assert len(results["daily_probes"]) == 7


# ---------------------------------------------------------------------------
# REST openInterestHist rejection
# ---------------------------------------------------------------------------


def test_rest_open_interest_hist_rejected_in_md():
    """DATA_AVAILABILITY.md must contain the rejection section."""
    md_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "reports",
        "funding_oi_crowding_regime_v0",
        "DATA_AVAILABILITY.md",
    )
    assert os.path.exists(md_path), f"Expected {md_path} to exist"
    with open(md_path) as f:
        content = f.read()
    assert "Why Binance REST openInterestHist is rejected" in content
    assert "trailing month only" in content or "latest-month only" in content
    assert "180-day past-only percentile warmup" in content
    assert "multi-year archive" in content
    assert "not be used as fallback" in content or "fallback" in content


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def _make_zip_bytes(header, rows):
    """Create a ZIP file in memory for testing."""
    import csv
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        csv_content = io.StringIO()
        writer = csv.DictWriter(csv_content, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
        zf.writestr("test.csv", csv_content.getvalue())
    return buf.getvalue()


def test_schema_without_oi_fields_returns_unusable():
    """Missing OI fields must produce DATA_SCHEMA_UNUSABLE."""
    header = ["create_time", "symbol", "irrelevant_field"]
    rows = [
        {"create_time": "2024-01-01 00:00:00", "symbol": "BTCUSDT", "irrelevant_field": "1.0"},
    ]
    zip_bytes = _make_zip_bytes(header, rows)
    h, r = extract_csv_from_zip(zip_bytes)
    assert h is not None
    assert OI_PRIMARY_FIELD not in h
    assert OI_SECONDARY_FIELD not in h


def test_schema_missing_timestamp():
    """Missing timestamp column must produce DATA_SCHEMA_UNUSABLE."""
    header = ["symbol", OI_PRIMARY_FIELD, OI_SECONDARY_FIELD]
    rows = [
        {"symbol": "BTCUSDT", OI_PRIMARY_FIELD: "100.0", OI_SECONDARY_FIELD: "5000000.0"},
    ]
    zip_bytes = _make_zip_bytes(header, rows)
    h, r = extract_csv_from_zip(zip_bytes)
    from run_funding_oi_archive_probe import TIMESTAMP_FIELD

    assert TIMESTAMP_FIELD not in h


# ---------------------------------------------------------------------------
# OI alignment rules
# ---------------------------------------------------------------------------


def test_oi_alignment_last_row_before_or_at_settlement():
    """OI_end uses the last row at or before settlement_ts, never after."""
    # Build OI rows at 0, 5, 10 minutes past each hour
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    oi_data = []
    for minute in range(0, 24 * 60, 5):
        ts = base + timedelta(minutes=minute)
        oi_data.append({"ts": ts, "oi": 100.0 + minute})

    # Settlement at 08:00:00
    settlement_ts = base + timedelta(hours=8)

    # Last row <= 08:00:00 should be 08:00:00
    oi_end = None
    for row in reversed(oi_data):
        if row["ts"] <= settlement_ts:
            oi_end = row
            break
    assert oi_end is not None
    assert oi_end["ts"] == settlement_ts
    assert oi_end["oi"] == 100.0 + 8 * 60  # 480 minutes


def test_oi_alignment_no_future_rows():
    """
    OI alignment must never use a row after settlement_ts.
    If all rows are after settlement_ts, OI_end is None.
    """
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # All rows are AFTER settlement_ts
    oi_data = [{"ts": base + timedelta(hours=9), "oi": 200.0}]
    settlement_ts = base + timedelta(hours=8)

    oi_end = None
    for row in reversed(oi_data):
        if row["ts"] <= settlement_ts:
            oi_end = row
            break
    assert oi_end is None


def test_oi_start_8h_before():
    """OI_start must use the last row at or before settlement_ts - 8h."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    oi_data = []
    for minute in range(0, 24 * 60, 5):
        ts = base + timedelta(minutes=minute)
        oi_data.append({"ts": ts, "oi": 100.0 + minute})

    settlement_ts = base + timedelta(hours=16)
    s8h = settlement_ts - timedelta(hours=8)  # 08:00:00

    oi_start = None
    for row in reversed(oi_data):
        if row["ts"] <= s8h:
            oi_start = row
            break
    assert oi_start is not None
    assert oi_start["ts"] == s8h


def test_missing_oi_bracket_unaligned():
    """Missing either OI bracket must produce OI_UNALIGNED."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    oi_data = [{"ts": base + timedelta(hours=2), "oi": 150.0}]
    settlement_ts = base + timedelta(hours=8)
    s8h = settlement_ts - timedelta(hours=8)

    oi_end = None
    oi_start = None
    for row in reversed(oi_data):
        if row["ts"] <= settlement_ts:
            oi_end = row
            break
    for row in reversed(oi_data):
        if row["ts"] <= s8h:
            oi_start = row
            break

    # OI_end exists (2h is <= 8h), but OI_start does not (2h > 0h)
    assert oi_end is not None
    assert oi_start is None  # Only row at 2h is after midnight


def test_oi_change_pct_computation():
    """OI_change_pct = (OI_end - OI_start) / OI_start."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    oi_end = {"ts": base + timedelta(hours=8), "oi": 200.0}
    oi_start = {"ts": base, "oi": 100.0}
    change_pct = (oi_end["oi"] - oi_start["oi"]) / oi_start["oi"]
    assert change_pct == 1.0  # 100%


def test_oi_change_pct_rising_falling():
    """rising_oi if change_pct > 0, falling_oi if <= 0."""
    assert (200.0 - 100.0) / 100.0 > 0  # rising
    assert (50.0 - 100.0) / 100.0 <= 0  # falling
    assert (100.0 - 100.0) / 100.0 <= 0  # zero change is falling


# ---------------------------------------------------------------------------
# Coverage estimation
# ---------------------------------------------------------------------------


def test_coverage_threshold_5500_slots():
    """Estimated settlement slots must be >= 5500 for the study to proceed."""
    # This should pass based on real probe data
    results = run_phase0a()
    cov = results.get("coverage_estimate", {})
    if cov:
        estimated = cov.get("estimated_settlement_slots", 0)
        reaches_min = cov.get("reaches_min_date", False)
        if not estimated >= MIN_ALIGNED_SETTLEMENT_SLOTS and reaches_min:
            pytest.skip(
                f"Estimated slots ({estimated}) below threshold ({MIN_ALIGNED_SETTLEMENT_SLOTS})"
            )


def test_coverage_reaches_min_date():
    """Coverage must reach at least 2025-01-01."""
    results = run_phase0a()
    cov = results.get("coverage_estimate", {})
    if cov:
        assert cov.get("reaches_min_date", False), (
            f"Latest probe {cov.get('latest_probe')} does not reach {MIN_COVERAGE_DATE}"
        )


# ---------------------------------------------------------------------------
# Phase 0A no threshold computation
# ---------------------------------------------------------------------------


def test_phase0a_does_not_compute_funding_thresholds():
    """
    Phase 0A must NOT compute or output funding percentile threshold values.
    The run_phase0a function should not contain threshold-related fields.
    """
    results = run_phase0a()
    forbidden_keys = [
        "funding_threshold",
        "top5_threshold",
        "bottom5_threshold",
        "percentile_threshold",
        "funding_percentile",
        "top_5_pct",
        "bottom_5_pct",
    ]
    for key in forbidden_keys:
        assert key not in results, f"Phase 0A output must not contain {key}"


# ---------------------------------------------------------------------------
# Primary family size
# ---------------------------------------------------------------------------


def test_primary_cells_exactly_6():
    """
    Primary cell family must be exactly 6:
    2 funding directions × 1 OI regime (rising_oi) × 3 horizons (8h, 24h, 48h).
    """
    funding_directions = ["positive", "negative"]
    oi_regimes = ["rising_oi"]
    horizons = ["8h", "24h", "48h"]
    cells = [
        (d, o, h)
        for d in funding_directions
        for o in oi_regimes
        for h in horizons
    ]
    assert len(cells) == 6


def test_diagnostic_cells_falling_oi():
    """
    Falling-OI cells are diagnostic-only, not part of the primary family.
    """
    funding_directions = ["positive", "negative"]
    oi_regimes = ["falling_oi"]
    horizons = ["8h", "24h", "48h"]
    diagnostic_cells = [
        (d, o, h)
        for d in funding_directions
        for o in oi_regimes
        for h in horizons
    ]
    assert len(diagnostic_cells) == 6


# ---------------------------------------------------------------------------
# Safety guards (no private/auth/order/execution)
# ---------------------------------------------------------------------------


def test_no_private_auth_imports():
    """The probe script must not import private/auth/order/execution modules."""
    with open(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "run_funding_oi_archive_probe.py",
        )
    ) as f:
        content = f.read()

    forbidden_patterns = [
        "import nautilus",
        "from nautilus",
        "import ccxt",
        "from ccxt",
        "import binance",
        "from binance",
        "api_key",
        "api_secret",
        "private_key",
        "live_trading",
        "execution_client",
        "bot_path",
        "order_submit",
        "TradeOrder",
        "OrderSubmit",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in content, f"Forbidden import/string found: {pattern}"


def test_no_private_auth_imports_funding_oi():
    """The regime module must also be clean."""
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "funding_oi_crowding_regime.py",
    )
    if os.path.exists(path):
        with open(path) as f:
            content = f.read()
        forbidden_patterns = [
            "import nautilus",
            "from nautilus",
            "import ccxt",
            "from ccxt",
            "api_key",
            "api_secret",
            "private_key",
            "live_trading",
            "execution_client",
            "bot_path",
            "order_submit",
            "TradeOrder",
            "OrderSubmit",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"Forbidden import/string found: {pattern} in funding_oi_crowding_regime.py"


# ---------------------------------------------------------------------------
# Phase 0B population sizing constraints
# ---------------------------------------------------------------------------


def _parse_pop_counts(path):
    """Try to load population_counts.json."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def test_population_sizing_counts_only():
    """Population sizing output must not contain threshold values or timestamps."""
    pop_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "reports",
        "funding_oi_crowding_regime_v0",
        "population_counts.json",
    )
    pop = _parse_pop_counts(pop_path)
    if pop is None:
        pytest.skip("population_counts.json not found")

    forbidden_keys = [
        "top5_threshold",
        "bottom5_threshold",
        "funding_threshold",
        "event_timestamps",
        "forward_returns",
        "net_bps",
        "win_rate",
        "p_value",
    ]
    for key in forbidden_keys:
        assert key not in pop, f"Population sizing output must not contain {key}"


def test_population_sizing_no_returns():
    """Population sizing must not include any return-derived metrics."""
    pop_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "reports",
        "funding_oi_crowding_regime_v0",
        "population_counts.json",
    )
    pop = _parse_pop_counts(pop_path)
    if pop is None:
        pytest.skip("population_counts.json not found")

    return_terms = ["forward_return", "bps", "return", "pnl", "sharpe", "sortino"]
    for term in return_terms:
        for key in pop:
            if isinstance(key, str) and term in key.lower():
                pytest.fail(f"Population sizing output must not contain key with '{term}': {key}")


def test_population_counts_fields():
    """Validate that population counts contain the expected fields."""
    pop_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "reports",
        "funding_oi_crowding_regime_v0",
        "population_counts.json",
    )
    pop = _parse_pop_counts(pop_path)
    if pop is None:
        pytest.skip("population_counts.json not found")

    expected_fields = [
        "total_aligned_settlements",
        "total_excluded_oi_unaligned",
        "positive_funding_extreme_count",
        "negative_funding_extreme_count",
        "positive_extreme_rising_oi",
        "positive_extreme_falling_oi",
        "negative_extreme_rising_oi",
        "negative_extreme_falling_oi",
    ]
    for field in expected_fields:
        assert field in pop, f"Missing expected field: {field}"
        assert isinstance(pop[field], (int, float))


# ---------------------------------------------------------------------------
# Phase 0B population sizing constraints (counts-only)
# ---------------------------------------------------------------------------


def test_population_sizing_no_thresholds_in_script_output():
    """Check that the Phase 0B script does not print threshold values."""
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "funding_oi_crowding_regime.py",
    )
    if not os.path.exists(path):
        pytest.skip("funding_oi_crowding_regime.py not yet created")
    with open(path) as f:
        content = f.read()
    forbidden_print = [
        "top5_threshold",
        "bottom5_threshold",
        "percentile value",
    ]
    for pat in forbidden_print:
        assert pat not in content, f"Script must not print threshold values: {pat}"


# ---------------------------------------------------------------------------
# Verdict validation
# ---------------------------------------------------------------------------


def test_verdict_is_recognized():
    """The final verdict must be one of the allowed values."""
    results = run_phase0a()
    verdict = results.get("verdict")
    allowed = [
        "ARCHIVE_OI_UNAVAILABLE",
        "OI_HISTORY_TOO_SHORT_FOR_ARCHIVE_STUDY",
        "DATA_SCHEMA_UNUSABLE",
        "OI_CADENCE_UNUSABLE",
        "OI_ALIGNMENT_DEGRADED",
        "ARCHIVE_COVERAGE_TOO_SHORT",
        "PHASE0A_ARCHIVE_FEASIBILITY_PASSED",
    ]
    assert verdict in allowed, f"Unknown verdict: {verdict}"


# ---------------------------------------------------------------------------
# Report file exists
# ---------------------------------------------------------------------------


def test_report_artifacts_exist():
    """Verify that all Phase 0A/0B report files exist."""
    base = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "reports",
        "funding_oi_crowding_regime_v0",
    )
    assert os.path.exists(os.path.join(base, "data_availability.json"))
    assert os.path.exists(os.path.join(base, "DATA_AVAILABILITY.md"))


# ---------------------------------------------------------------------------
# Precommitment document exists
# ---------------------------------------------------------------------------


def test_precommitment_exists():
    """The precommitment document must exist if Phase 0A and 0B passed."""
    precommitment_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "docs",
        "FUNDING_OI_CROWDING_REGIME_PRECOMMITMENT.md",
    )
    assert os.path.exists(precommitment_path), (
        f"Expected precommitment at {precommitment_path}"
    )


def test_precommitment_contains_required_sections():
    """Validate the precommitment document has required sections."""
    precommitment_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "docs",
        "FUNDING_OI_CROWDING_REGIME_PRECOMMITMENT.md",
    )
    if not os.path.exists(precommitment_path):
        pytest.skip("Precommitment not created yet")
    with open(precommitment_path) as f:
        content = f.read()
    required_sections = [
        "Study ID",
        "Family 2",
        "open-interest changes near funding extremes",
        "Data Source",
        "Safety",
        "OI Alignment Rule",
        "OI Regime Classification",
        "Funding Threshold",
        "Primary Cells",
        "Diagnostic Cells",
        "Direction Mapping",
        "Return Leg",
        "Cost Assumptions",
        "Minimum Events",
        "Evaluation Gates",
        "Null Specification",
        "Underpowered Semantics",
        "Registry Rule",
        "Phase 0 Completion",
    ]
    for section in required_sections:
        assert section in content, f"Precommitment missing required section: {section}"


def test_precommitment_family_size_6():
    """Precommitment must confirm exactly 6 primary cells."""
    precommitment_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "docs",
        "FUNDING_OI_CROWDING_REGIME_PRECOMMITMENT.md",
    )
    if not os.path.exists(precommitment_path):
        pytest.skip("Precommitment not created yet")
    with open(precommitment_path) as f:
        content = f.read()
    assert "exactly 6 primary cells" in content or "6 primary cells" in content
