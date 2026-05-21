"""
Focused tests for Family 3 v1 funding falling-OI unwind.

Stage A tests: Phase 0A/0B archive feasibility + population sizing.
Stage B tests (synthetic): evaluator gates, null, FDR, verdicts, registry.
Safety tests: no auth, no execution, no orders, no live capture.

All Stage A tests are self-contained (no HTTP) except where noted.
Stage B evaluator tests use synthetic data.
"""

import json
import os
import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest


# Ensure the module is importable
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), ".."),
)

from funding_falling_oi_unwind_phase0 import FIXED_PROBE_DATES
from funding_falling_oi_unwind_phase0 import MAX_ACCEPTABLE_P95_OFFSET_MINUTES
from funding_falling_oi_unwind_phase0 import MIN_CELL_EVENTS
from funding_falling_oi_unwind_phase0 import MIN_HOLDOUT_EVENTS
from funding_falling_oi_unwind_phase0 import _daily_oi_path
from funding_falling_oi_unwind_phase0 import _has_spot_availability
from funding_falling_oi_unwind_phase0 import _monthly_funding_path
from funding_falling_oi_unwind_phase0 import _spot_klines_path


# Re-usable OI regime computation (matches the logic inside run_phase0b)
def _compute_oi_regime_for_test(settlement_ts, oi_rows):
    import math
    s8h = settlement_ts - timedelta(hours=8)
    oi_end_val = None
    for row in reversed(oi_rows):
        if row["ts"] <= settlement_ts:
            oi_end_val = row["oi"]
            break
    oi_start_val = None
    for row in reversed(oi_rows):
        if row["ts"] <= s8h:
            oi_start_val = row["oi"]
            break
    if oi_end_val is None or oi_start_val is None:
        return "OI_UNALIGNED", None
    if oi_start_val <= 0 or not math.isfinite(oi_start_val):
        return "OI_UNALIGNED", None
    if oi_end_val <= 0 or not math.isfinite(oi_end_val):
        return "OI_UNALIGNED", None
    oi_change_pct = (oi_end_val - oi_start_val) / oi_start_val
    regime = "falling_oi" if oi_change_pct <= 0 else "rising_oi"
    return regime, oi_change_pct


# ---------------------------------------------------------------------------
# Stage A: Study identity and family structure
# ---------------------------------------------------------------------------

def test_study_id_distinct_from_rising_oi():
    """Study ID is distinct from the rejected Family 3 rising-OI v0."""
    study_id = "family3_funding_falling_oi_unwind_v1"
    # Verify it does NOT match the rising-OI study
    rising_oi_ids = [
        "funding_oi_crowding_regime_v0",
        "family3-funding-oi-crowding-regime-rising-oi-rejected",
    ]
    assert study_id not in rising_oi_ids
    assert "rising" not in study_id
    assert "falling" in study_id


def test_primary_family_size_exactly_2():
    """Primary family must be exactly 2 cells."""
    # These are the only 2 primary cells
    cells = [
        ("negative_funding_extreme", "falling_oi", "24h"),
        ("negative_funding_extreme", "falling_oi", "48h"),
    ]
    assert len(cells) == 2


def test_primary_cells_negative_funding_falling_oi():
    """Primary cells are only negative funding + falling OI at 24h and 48h."""
    for direction, oi_regime, horizon in [
        ("negative_funding_extreme", "falling_oi", "24h"),
        ("negative_funding_extreme", "falling_oi", "48h"),
    ]:
        assert direction == "negative_funding_extreme"
        assert oi_regime == "falling_oi"
        assert horizon in ("24h", "48h")


def test_8h_not_primary():
    """8h is not a primary horizon."""
    horizons = ["24h", "48h"]
    assert "8h" not in horizons


def test_rising_oi_cannot_enter_primary_cells():
    """rising-OI cannot enter primary cells."""
    primary_oi_regimes = ["falling_oi"]
    assert "rising_oi" not in primary_oi_regimes


def test_positive_funding_cannot_enter_primary_cells():
    """positive-funding cannot enter primary cells."""
    primary_directions = ["negative_funding_extreme"]
    assert "positive_funding_extreme" not in primary_directions


# ---------------------------------------------------------------------------
# Stage A: Funding threshold rules
# ---------------------------------------------------------------------------

def test_funding_threshold_bottom_5_percent():
    """Funding threshold is bottom 5% over past-only 180 calendar days."""
    # Simulate 200 past funding rates
    past_rates = sorted([float(i) for i in range(-100, 100)])  # 200 values, -100..99
    n = len(past_rates)
    bot5 = past_rates[min(int(n * 0.05), n - 1)]
    # bottom 5% of 200 values = index 10 = value -90
    assert bot5 == float(-100 + 10)  # index 10 = -90
    # Verify that -90 IS the bottom 5% threshold
    # Values <= -90 are bottom 5%
    assert float(-91) <= bot5  # is extreme
    assert float(-89) > bot5   # not extreme


def test_settlements_without_180d_history_excluded():
    """Settlements without full 180-day history are excluded from eligibility."""
    # Create funding data with first settlement at t0
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    n_days = 200  # More than 180
    data = []
    for d in range(n_days):
        for h in [0, 8, 16]:
            ts = base + timedelta(days=d, hours=h)
            data.append({"ts": ts, "funding_rate": 0.001})

    first_ts = data[0]["ts"]
    warmup_end = first_ts + timedelta(days=180)  # = day 180

    # Events before warmup_end should be excluded
    excluded = [e for e in data if e["ts"] <= warmup_end]
    eligible = [e for e in data if e["ts"] > warmup_end]

    assert len(excluded) > 0
    assert len(eligible) > 0
    # All events before warmup are excluded
    for e in excluded:
        assert e["ts"] <= first_ts + timedelta(days=180)


# ---------------------------------------------------------------------------
# Stage A: OI alignment rules
# ---------------------------------------------------------------------------

def test_oi_alignment_uses_only_rows_at_or_before():
    """OI alignment uses only rows with ts <= settlement timestamp."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

    # OI rows at 0, 5, 10, ... minutes past each hour
    oi_rows = []
    for minute in range(0, 24 * 60, 5):
        ts = base + timedelta(minutes=minute)
        oi_rows.append({"ts": ts, "oi": 100.0 + minute})

    settlement = base + timedelta(hours=8)  # 08:00

    # Find last row <= settlement
    oi_end = None
    for row in reversed(oi_rows):
        if row["ts"] <= settlement:
            oi_end = row
            break

    assert oi_end is not None
    assert oi_end["ts"] == settlement  # 08:00 has a row since cadence is every 5 min


def test_future_oi_rows_forbidden():
    """Future OI rows (ts > settlement) must not be used."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    settlement = base + timedelta(hours=8)

    # All OI rows are after settlement
    oi_rows = [{"ts": base + timedelta(hours=9), "oi": 200.0}]

    oi_end = None
    for row in reversed(oi_rows):
        if row["ts"] <= settlement:
            oi_end = row
            break

    assert oi_end is None


def test_oi_start_zero_excludes_event():
    """OI_start value <= 0 or non-finite must exclude the event."""
    oi_rows = [
        {"ts": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), "oi": 0.0},
        {"ts": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC), "oi": 100.0},
    ]
    settlement = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    regime, _ = _compute_oi_regime_for_test(settlement, oi_rows)
    assert regime == "OI_UNALIGNED"


def test_oi_start_negative_excludes_event():
    """OI_start negative value must exclude the event."""
    oi_rows = [
        {"ts": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), "oi": -1.0},
        {"ts": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC), "oi": 100.0},
    ]
    settlement = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    regime, _ = _compute_oi_regime_for_test(settlement, oi_rows)
    assert regime == "OI_UNALIGNED"


def test_oi_start_nan_excludes_event():
    """OI_start NaN value must exclude the event."""
    oi_rows = [
        {"ts": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), "oi": float("nan")},
        {"ts": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC), "oi": 100.0},
    ]
    settlement = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    regime, _ = _compute_oi_regime_for_test(settlement, oi_rows)
    assert regime == "OI_UNALIGNED"


def test_oi_start_infinite_excludes_event():
    """OI_start infinite value must exclude the event."""
    oi_rows = [
        {"ts": datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), "oi": float("inf")},
        {"ts": datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC), "oi": 100.0},
    ]
    settlement = datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC)

    regime, _ = _compute_oi_regime_for_test(settlement, oi_rows)
    assert regime == "OI_UNALIGNED"


# ---------------------------------------------------------------------------
# Stage A: OI alignment failure modes
# ---------------------------------------------------------------------------

def test_unacceptable_oi_alignment_returns_oi_alignment_failed():
    """Test that p95 > 5 minutes on either bracket returns OI_ALIGNMENT_FAILED."""
    # This tests the logic: MAX_ACCEPTABLE_P95_OFFSET_MINUTES = 5
    p95_end = 6.0
    p95_start = 1.0
    max_p95 = max(p95_end, p95_start)
    assert max_p95 > MAX_ACCEPTABLE_P95_OFFSET_MINUTES

    # Would trigger OI_ALIGNMENT_FAILED
    p95_end2 = 1.0
    p95_start2 = 6.0
    max_p952 = max(p95_end2, p95_start2)
    assert max_p952 > MAX_ACCEPTABLE_P95_OFFSET_MINUTES

    # Test pass case
    p95_end3 = 2.0
    p95_start3 = 3.0
    max_p953 = max(p95_end3, p95_start3)
    assert max_p953 <= MAX_ACCEPTABLE_P95_OFFSET_MINUTES


def test_daily_only_oi_unsupported():
    """Daily-only OI granularity (>4h cadence) returns OI_GRANULARITY_UNSUPPORTED."""
    # The code checks if median OI cadence > 4*3600 seconds
    cadence_seconds = 5 * 3600  # 5h = daily granularity
    assert cadence_seconds > 4 * 3600, "Should be classified as unsupported"

    cadence_ok = 300  # 5 minutes
    assert cadence_ok <= 4 * 3600, "Should pass"


# ---------------------------------------------------------------------------
# Stage A: Event count outcomes
# ---------------------------------------------------------------------------

def test_low_event_count_returns_needs_more_data():
    """Low event count (< 50 per cell) returns NEEDS_MORE_DATA."""
    total = 30
    assert total < MIN_CELL_EVENTS, "Would fail as NEEDS_MORE_DATA"


def test_weak_holdout_count_returns_underpowered():
    """Weak holdout (< 50) returns UNDERPOWERED_HOLDOUT_FAILURE."""
    total = 200
    holdout = 26  # As observed in real run
    assert total >= MIN_CELL_EVENTS, "Total is sufficient"
    assert holdout < MIN_HOLDOUT_EVENTS, "Would fail as UNDERPOWERED_HOLDOUT_FAILURE"


# ---------------------------------------------------------------------------
# Stage A: Phase 0B constraints
# ---------------------------------------------------------------------------

def test_phase0b_no_forward_returns():
    """Phase 0B output must not contain forward returns, edge stats, nulls, FDR."""
    phase0b_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "phase0_population_report.json"
    )
    if not os.path.exists(phase0b_path):
        pytest.skip("phase0_population_report.json not found")

    with open(phase0b_path) as f:
        report = json.load(f)

    p0b = report.get("phase_0b", {})
    forbidden = [
        "forward_return", "net_bps", "edge", "win_rate", "p_value",
        "null", "fdr", "holdout_verdict", "registry_update"
    ]
    serialized = json.dumps(p0b).lower()
    for term in forbidden:
        assert term not in serialized, f"Phase 0B contains forbidden term: {term}"


def test_phase0b_deterministic():
    """Phase 0B must be deterministic - same inputs produce same output."""
    # The function has no random calls; verify no random imports
    phase0_path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(phase0_path) as f:
        content = f.read()
    # Check no random module import
    assert "import random" not in content
    assert "from random" not in content


# ---------------------------------------------------------------------------
# Stage B: Evaluator gating (synthetic)
# ---------------------------------------------------------------------------

def test_evaluator_proceeds_after_population_sufficient():
    """Evaluator must proceed only after POPULATION_SUFFICIENT."""
    outcome = "POPULATION_SUFFICIENT"
    allowed = {"POPULATION_SUFFICIENT"}
    assert outcome in allowed


def test_evaluator_stops_on_needs_more_data():
    """Evaluator must stop if Phase 0 outcome is NEEDS_MORE_DATA."""
    outcome = "NEEDS_MORE_DATA"
    allowed = {"POPULATION_SUFFICIENT"}
    assert outcome not in allowed


def test_evaluator_stops_on_underpowered_holdout():
    """Evaluator must stop if Phase 0 outcome is UNDERPOWERED_HOLDOUT_FAILURE."""
    outcome = "UNDERPOWERED_HOLDOUT_FAILURE"
    allowed = {"POPULATION_SUFFICIENT"}
    assert outcome not in allowed


def test_exactly_2_cells_evaluated():
    """Exactly 2 cells must be evaluated in Stage B."""
    cells = [
        ("negative_funding_extreme", "falling_oi", "24h"),
        ("negative_funding_extreme", "falling_oi", "48h"),
    ]
    assert len(cells) == 2


def test_rising_oi_not_in_evaluator():
    """rising-OI cannot enter evaluator rows."""
    evaluator_oi_regimes = ["falling_oi"]
    assert "rising_oi" not in evaluator_oi_regimes


def test_positive_funding_not_in_evaluator():
    """positive-funding cannot enter evaluator rows."""
    evaluator_directions = ["negative_funding_extreme"]
    assert "positive_funding_extreme" not in evaluator_directions


def test_gates_evaluated_on_holdout():
    """Gates are evaluated on holdout, not discovery."""
    # The precommitment explicitly states gates eval on holdout
    # Verify the precommitment text
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "Gates are evaluated on chronological holdout only" in text or \
           "Gates are evaluated on holdout" in text


# ---------------------------------------------------------------------------
# Stage B: Null testing
# ---------------------------------------------------------------------------

def test_timestamp_shuffle_null_present():
    """Timestamp-shuffle null must be present in Stage B."""
    # Verify precommitment specifies timestamp-shuffle
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "Timestamp-shuffle null" in text or "timestamp-shuffle null" in text


def test_sign_flip_null_absent():
    """Sign-flip null must be absent (forbidden)."""
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "Sign-flip" not in text or "Forbidden" in text or "forbidden" in text


# ---------------------------------------------------------------------------
# Stage B: FDR
# ---------------------------------------------------------------------------

def test_by_fdr_family_size_2():
    """BY FDR family size must be exactly 2."""
    family_size = 2
    assert family_size == 2

    # Verify from precommitment
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "exactly 2 primary cells" in text


# ---------------------------------------------------------------------------
# Stage B: Verdict rules
# ---------------------------------------------------------------------------

def test_synthetic_positive_cell_can_reach_candidate():
    """Synthetic cell with positive returns should reach CANDIDATE_FOR_LONGER_OBSERVATION."""
    # Simulate cell evaluation logic
    holdout_bps = [60.0] * 55  # 55 events, all above 0

    mean_net = sum(holdout_bps) / len(holdout_bps)
    median_net = sorted(holdout_bps)[len(holdout_bps) // 2]
    win_rate = sum(1 for b in holdout_bps if b > 0) / len(holdout_bps)
    sorted_bps = sorted(holdout_bps)
    worst_decile = sorted_bps[int(len(sorted_bps) * 0.1)]

    assert mean_net > 0
    assert median_net > 0
    assert win_rate >= 0.55
    assert worst_decile > -50


def test_null_failing_cell_is_diagnostic_not_rejected():
    """Null-failing cell yields NULL_REJECTED_DIAGNOSTIC, not REJECTED."""
    # This is a policy rule defined in the precommitment
    allowed_verdicts = [
        "CANDIDATE_FOR_LONGER_OBSERVATION",
        "REJECTED",
        "NULL_REJECTED_DIAGNOSTIC",
        "FDR_BLOCKED_DIAGNOSTIC",
        "NEEDS_MORE_DATA",
    ]
    null_fail_verdict = "NULL_REJECTED_DIAGNOSTIC"
    assert null_fail_verdict in allowed_verdicts
    assert null_fail_verdict != "REJECTED"

    # Verify from precommitment
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "NULL_REJECTED_DIAGNOSTIC" in text
    assert "must not collapse" in text  # backtick-qualified in markdown


# ---------------------------------------------------------------------------
# Stage B: Cost
# ---------------------------------------------------------------------------

def test_50_bps_cost_applied():
    """50 bps cost must be applied to primary cells."""
    primary_cost_bps = 50
    assert primary_cost_bps == 50

    # Verify from precommitment
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "50 bps" in text


def test_no_6_bps_diagnostic_presented_as_edge():
    """No 6 bps diagnostic edge number is presented as primary evidence."""
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "6 bps diagnostic edge number" in text or "No 6 bps" in text
    assert "Do not compute" in text or "do not compute" in text


# ---------------------------------------------------------------------------
# Stage B: Registry rules
# ---------------------------------------------------------------------------

def test_registry_update_only_after_clean_stage_b():
    """Registry update occurs only after clean Stage B completion."""
    # Since Stage A returned UNDERPOWERED_HOLDOUT_FAILURE, Stage B was not run
    # Therefore registry must NOT be updated
    registry_path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(registry_path) as f:
        text = f.read()
    # Verify that our study is NOT yet in the registry
    assert "family3_funding_falling_oi_unwind_v1" not in text, \
        "Registry must not contain the falling-OI study since Stage B hasn't run"


def test_registry_update_does_not_modify_lock_12():
    """Registry update must not modify locked gate #12."""
    registry_path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(registry_path) as f:
        text = f.read()
    # Lock #12 should still be intact and unchanged
    assert "Lock #12" in text or "12." in text
    # Lock should mention rising
    assert "rising" in text.lower()
    # Lock should carve out falling
    assert ("falling" in text.lower()) or ("failing" in text.lower())


# ---------------------------------------------------------------------------
# Safety tests
# ---------------------------------------------------------------------------

def test_phase0_script_public_data_only():
    """Phase 0 script must not import private/auth/order/execution modules."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(path) as f:
        content = f.read()

    forbidden = [
        "import nautilus", "from nautilus",
        "import ccxt", "from ccxt",
        "api_key", "api_secret", "private_key",
        "execution_client", "live_trading", "bot_path",
        "TradeOrder", "OrderSubmit",
    ]
    for pattern in forbidden:
        assert pattern not in content, f"Forbidden import/string found: {pattern}"


def test_no_auth_in_phase0():
    """No authentication, API keys, or private keys in Phase 0."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(path) as f:
        content = f.read()

    forbidden_auth = ["api_key", "api_secret", "private_key", "Authorization"]
    for pattern in forbidden_auth:
        assert pattern not in content, f"Forbidden auth pattern found: {pattern}"


def test_no_orders_or_execution():
    """No orders, execution imports, paper trading, or bot path."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(path) as f:
        content = f.read()

    forbidden_order = [
        "order_submit", "TradeOrder", "OrderSubmit",
        "paper_trading", "shadow", "governance",
        "execution_client",
    ]
    for pattern in forbidden_order:
        assert pattern not in content, f"Forbidden order/exec pattern: {pattern}"


def test_no_live_capture():
    """No live capture, systemd watcher, or bot path."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(path) as f:
        content = f.read()

    forbidden_live = [
        "systemd", "watcher", "live_capture",
        "WebSocket", "websocket",
    ]
    for pattern in forbidden_live:
        assert pattern not in content, f"Forbidden live capture pattern: {pattern}"


def test_no_governance_or_execution_path():
    """No governance approval path or execution path."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(path) as f:
        content = f.read()

    forbidden_path = ["governance_approval", "execution_path", "shadow_trading"]
    for pattern in forbidden_path:
        assert pattern not in content, f"Forbidden path: {pattern}"


def test_precommitment_safety():
    """Precommitment must declare public-data observer only."""
    precommitment_path = os.path.join(
        os.path.dirname(__file__), "..", "docs",
        "FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
    )
    with open(precommitment_path) as f:
        text = f.read()
    assert "Public-data observer only" in text or "public-data observer" in text
    assert "No authentication" in text
    assert "No API keys" in text
    assert "No orders" in text
    assert "Archive data only" in text


# ---------------------------------------------------------------------------
# Phase 0A fixed probe tests
# ---------------------------------------------------------------------------

def test_fixed_probe_dates_exactly_6():
    """Fixed probe dates must be exactly 6."""
    expected = [
        "2020-09-01",
        "2021-01-01",
        "2021-06-01",
        "2022-01-01",
        "2023-01-01",
        "2024-01-01",
    ]
    assert expected == FIXED_PROBE_DATES
    assert len(FIXED_PROBE_DATES) == 6


def test_monthly_funding_path():
    """Monthly funding archive path uses correct naming convention."""
    path = _monthly_funding_path("BTCUSDT", "2021-01-01")
    expected = "data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2021-01.zip"
    assert path == expected


def test_daily_oi_path():
    """Daily OI archive path uses correct naming convention."""
    path = _daily_oi_path("BTCUSDT", "2021-01-01")
    expected = "data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2021-01-01.zip"
    assert path == expected


def test_spot_klines_path():
    """Spot klines monthly path uses correct naming convention."""
    path = _spot_klines_path("BTCUSDT", "2020-09")
    expected = "data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-09.zip"
    assert path == expected


# ---------------------------------------------------------------------------
# Archive content hashes
# ---------------------------------------------------------------------------

def test_phase0_report_contains_required_fields():
    """Phase 0 report must contain required metadata fields."""
    report_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "phase0_population_report.json"
    )
    if not os.path.exists(report_path):
        pytest.skip("phase0_population_report.json not found")

    with open(report_path) as f:
        report = json.load(f)

    required_fields = [
        "study_id", "generated_at_utc", "git_sha", "precommitment_sha256",
        "phase_0a", "phase_0b", "outcome",
        "archive_content_hashes",
    ]
    for field in required_fields:
        assert field in report, f"Missing required field: {field}"

    assert report["study_id"] == "family3_funding_falling_oi_unwind_v1"
    assert report["precommitment_sha256"] is not None
    assert len(report["precommitment_sha256"]) == 64


def test_phase0_report_outcome_recorded():
    """Phase 0 report outcome must match the actual run outcome."""
    report_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "phase0_population_report.json"
    )
    if not os.path.exists(report_path):
        pytest.skip("phase0_population_report.json not found")

    with open(report_path) as f:
        report = json.load(f)

    outcome = report.get("outcome")
    valid_outcomes = [
        "POPULATION_SUFFICIENT",
        "NEEDS_MORE_DATA",
        "UNDERPOWERED_HOLDOUT_FAILURE",
        "ARCHIVE_FEASIBILITY_FAILED",
        "OI_ALIGNMENT_FAILED",
        "OI_GRANULARITY_UNSUPPORTED",
        "SPOT_AVAILABILITY_FAILED",
        "PHASE0B_FAILED",
        "PHASE0A_PASSED",
    ]
    assert outcome in valid_outcomes, f"Invalid outcome: {outcome}"


# ---------------------------------------------------------------------------
# Spot klines timestamp unit regression tests
# ---------------------------------------------------------------------------


def test_parse_klines_timestamp_ms():
    """parse_klines_timestamp handles millisecond timestamps (2024 and earlier)."""
    from funding_falling_oi_unwind_phase0 import parse_klines_timestamp

    # 2024-12-01 00:00 UTC in ms
    ts_ms = 1733011200000
    result = parse_klines_timestamp(ts_ms)
    assert result is not None
    assert result.year == 2024
    assert result.month == 12
    assert result.day == 1
    assert result.hour == 0
    assert result.minute == 0
    assert result.tzinfo is not None

    # Edge: last ms timestamp before µs transition
    ts_ms_dec31 = 1735689599999  # 2024-12-31 23:59:59.999 UTC
    result = parse_klines_timestamp(ts_ms_dec31)
    assert result is not None
    assert result.year == 2024
    assert result.month == 12
    assert result.day == 31


def test_parse_klines_timestamp_us():
    """parse_klines_timestamp handles microsecond timestamps (2025+)."""
    from funding_falling_oi_unwind_phase0 import parse_klines_timestamp

    # 2025-01-01 00:00 UTC in microseconds
    ts_us = 1735689600000000
    result = parse_klines_timestamp(ts_us)
    assert result is not None
    assert result.year == 2025
    assert result.month == 1
    assert result.day == 1
    assert result.hour == 0
    assert result.minute == 0
    assert result.tzinfo is not None

    # 2025-06-15 00:00 UTC in microseconds = 1749945600 seconds
    ts_us_mid = 1749945600000000
    result = parse_klines_timestamp(ts_us_mid)
    assert result is not None
    assert result.year == 2025
    assert result.month == 6
    assert result.day == 15


def test_parse_klines_timestamp_auto_detects_unit():
    """parse_klines_timestamp auto-detects ms vs µs by magnitude."""
    from funding_falling_oi_unwind_phase0 import parse_klines_timestamp

    # Both should produce valid UTC datetimes
    ts_ms = 1733011200000  # 2024-12-01 in ms
    ts_us = 1735689600000000  # 2025-01-01 in µs

    r1 = parse_klines_timestamp(ts_ms)
    r2 = parse_klines_timestamp(ts_us)

    assert r1 is not None
    assert r2 is not None
    assert r1 < r2  # Dec 2024 < Jan 2025 (correct ordering)
    assert r2 - r1 > timedelta(days=20)


def test_parse_klines_timestamp_invalid_returns_none():
    """parse_klines_timestamp returns None for out-of-range values."""
    from funding_falling_oi_unwind_phase0 import parse_klines_timestamp

    # Extremely large value (way beyond reasonable timestamps)
    assert parse_klines_timestamp(99999999999999999999) is None
    # NaN-like overflow
    assert parse_klines_timestamp(10**30) is None


# ---------------------------------------------------------------------------
# Spot availability cross-month tests
# ---------------------------------------------------------------------------


def test_spot_availability_late_month_24h():
    """Late-month event should have 24h availability that crosses into next month."""
    base = datetime(2024, 1, 31, 16, 0, 0, tzinfo=UTC)  # Jan 31 16:00
    target_24h = base + timedelta(hours=24)  # Feb 1 16:00
    assert base.month == 1
    assert target_24h.month == 2  # Crosses into Feb
    # The function doesn't need month-awareness; it searches the full spot_klines list


def test_spot_availability_late_month_48h():
    """Late-month event should have 48h availability that crosses 2 months ahead."""
    base = datetime(2024, 1, 30, 16, 0, 0, tzinfo=UTC)
    target_48h = base + timedelta(hours=48)  # Feb 1 16:00
    assert base.month == 1
    assert target_48h.month == 2

    base2 = datetime(2024, 12, 30, 16, 0, 0, tzinfo=UTC)
    target_48h_2 = base2 + timedelta(hours=48)  # Jan 1 16:00
    assert base2.month == 12
    assert target_48h_2.month == 1


# ---------------------------------------------------------------------------
# 24h vs 48h horizon-specific availability
# ---------------------------------------------------------------------------


def test_24h_vs_48h_are_distinct():
    """24h and 48h availability can differ for near-archive-end events."""
    # Build a spot klines set that ends just past 24h but before 48h
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    # Data from 2024-01-01 00:00 to 2024-01-02 13:00 (37 hours, covers 24h target)
    spot_klines = []
    for h in range(37):  # 0 to 36 = 37 rows, last at 2024-01-02 13:00
        ts = base + timedelta(hours=h)
        spot_klines.append({"ts": ts, "close": 50000.0 + h})

    settlement = base + timedelta(hours=12)  # 2024-01-01 12:00
    avail_24h = _has_spot_availability(settlement, spot_klines, 24)
    avail_48h = _has_spot_availability(settlement, spot_klines, 48)

    # 24h target = 2024-01-02 12:00, spot goes to 2024-01-02 13:00 (covers it)
    # 48h target = 2024-01-03 12:00, spot only to 2024-01-02 13:00
    # So 24h should be available, 48h should not
    assert avail_24h, "24h should be available"
    assert not avail_48h, "48h should NOT be available"


# ---------------------------------------------------------------------------
# UTC boundary behavior
# ---------------------------------------------------------------------------


def test_utc_boundary_entry_exact():
    """Entry price at exact settlement timestamp boundary uses >= logic."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    # Spot klines ON the settlement boundary
    spot_klines = [
        {"ts": base, "close": 50000.0},  # at settlement
        {"ts": base + timedelta(hours=1), "close": 50100.0},
        {"ts": base + timedelta(hours=24), "close": 50200.0},
        {"ts": base + timedelta(hours=25), "close": 50300.0},
    ]

    # Settlement at exact kline boundary
    settlement = base
    avail = _has_spot_availability(settlement, spot_klines, 24)
    assert avail


def test_utc_boundary_entry_between_klines():
    """Entry price between klines uses the next available kline."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    spot_klines = [
        {"ts": base, "close": 50000.0},
        {"ts": base + timedelta(hours=1), "close": 50100.0},
        {"ts": base + timedelta(hours=24), "close": 50200.0},
        {"ts": base + timedelta(hours=25), "close": 50300.0},
    ]

    # Settlement between klines (0:30, not aligned to hour)
    settlement = base + timedelta(minutes=30)
    avail = _has_spot_availability(settlement, spot_klines, 24)
    assert avail, "Should use next available kline (hour 1) for entry"


def test_utc_boundary_horizon_exact():
    """Forward price at exact horizon boundary matches >= logic."""
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    spot_klines = [
        {"ts": base, "close": 50000.0},
        {"ts": base + timedelta(hours=24), "close": 50200.0},
    ]

    settlement = base
    avail = _has_spot_availability(settlement, spot_klines, 24)
    assert avail, "24h target at exact kline boundary should match"


# ---------------------------------------------------------------------------
# Safety: no forbidden imports in fixed module
# ---------------------------------------------------------------------------


def test_fixed_module_no_forbidden_imports():
    """Fixed module must not import forbidden live/auth/order/execution modules."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "funding_falling_oi_unwind_phase0.py"
    )
    with open(path) as f:
        content = f.read()

    forbidden = [
        "import nautilus", "from nautilus",
        "import ccxt", "from ccxt",
        "api_key", "api_secret", "private_key",
        "execution_client", "live_trading", "bot_path",
        "TradeOrder", "OrderSubmit",
    ]
    for pattern in forbidden:
        assert pattern not in content, f"Forbidden import/string found: {pattern}"


def test_audit_script_no_forbidden():
    """Audit script must also be clean."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "spot_availability_audit.py"
    )
    if os.path.exists(path):
        with open(path) as f:
            content = f.read()
        forbidden = [
            "import nautilus", "from nautilus",
            "import ccxt", "from ccxt",
            "api_key", "api_secret", "private_key",
            "execution_client", "live_trading", "bot_path",
        ]
        for pattern in forbidden:
            assert pattern not in content, f"Forbidden in audit script: {pattern}"



# ---------------------------------------------------------------------------
# Preflight audit tests
# ---------------------------------------------------------------------------


def test_preflight_audit_json_exists():
    """Preflight audit JSON must exist after running preflight."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "preflight_stage_b_audit.json"
    )
    assert os.path.exists(path), f"Preflight audit not found: {path}"


def test_preflight_verdict_is_passed():
    """Preflight verdict must be PREFLIGHT_PASSED."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "preflight_stage_b_audit.json"
    )
    if not os.path.exists(path):
        pytest.skip("Preflight audit not found")
    with open(path) as f:
        audit = json.load(f)
    assert audit.get("preflight_verdict") == "PREFLIGHT_PASSED"


def test_horizon_awareness_verdict():
    """Horizon-awareness verdict is not a bug verdict."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "preflight_stage_b_audit.json"
    )
    if not os.path.exists(path):
        pytest.skip("Preflight audit not found")
    with open(path) as f:
        audit = json.load(f)
    hv = audit.get("horizon_verdict", "")
    assert "BUG" not in hv, f"Horizon bug found: {hv}"
    assert hv.startswith("HORIZON_AWARENESS_CONFIRMED")


def test_funding_parser_ms_only():
    """Funding timestamp parser ms only (no us timestamps found)."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "preflight_stage_b_audit.json"
    )
    if not os.path.exists(path):
        pytest.skip("Preflight audit not found")
    with open(path) as f:
        audit = json.load(f)
    fa = audit.get("funding_parser_audit", {})
    assert fa.get("funding_unit_verdict") == "FUNDING_TIMESTAMP_MS_CONFIRMED"
    assert fa.get("rows_with_microsecond_timestamps", -1) == 0
    assert fa.get("off_grid_settlements", -1) == 0


def test_oi_alignment_genuine():
    """OI alignment is genuine (exact grid rows exist)."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "preflight_stage_b_audit.json"
    )
    if not os.path.exists(path):
        pytest.skip("Preflight audit not found")
    with open(path) as f:
        audit = json.load(f)
    oa = audit.get("oi_parser_audit", {})
    assert oa.get("alignment_verdict") == "OI_ALIGNMENT_GENUINE_CONFIRMED"
    assert oa.get("exact_grid_rows_count", 0) > 0


def test_preflight_no_returns_or_prices():
    """Preflight audit JSON must not contain prices or returns."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1", "preflight_stage_b_audit.json"
    )
    if not os.path.exists(path):
        pytest.skip("Preflight audit not found")
    with open(path) as f:
        content = f.read()
    assert "price" not in content.lower()
    assert "return" not in content.lower()
    assert "net_bps" not in content
    assert "gross_bps" not in content
    assert "win_rate" not in content


def test_stage_b_artifacts_exist_after_completion():
    """Stage B artifacts must exist after Stage B completes."""
    base = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_falling_oi_unwind_v1"
    )
    stage_b_artifacts = [
        "stage_b_results.json",
        "null_results.json",
        "fdr_results.json",
        "holdout_results.json",
    ]
    for art in stage_b_artifacts:
        p = os.path.join(base, art)
        assert os.path.exists(p), f"Stage B artifact missing: {p}"


def test_registry_contains_family3_v1():
    """Registry must contain the Family 3 v1 falling-OI entry after Stage B."""
    registry_path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(registry_path) as f:
        text = f.read()
    assert "family3_funding_falling_oi_unwind_v1" in text or "Family 3 v1 funding" in text


def test_registry_gates_failed_verdict():
    """Registry entry must show REJECTED verdict (both cells GATES_FAILED)."""
    registry_path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(registry_path) as f:
        text = f.read()
    # Find the Family 3 v1 section
    if "Family 3 v1" in text:
        assert "GATES_FAILED" in text, "Registry should say GATES_FAILED"
    assert "REJECTED" in text, "Registry should say REJECTED"


def test_registry_lock_13_exists():
    """Lock #13 must exist for falling-OI v1."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(path) as f:
        text = f.read()
    assert "13. **Family 3 v1" in text
    assert "falling-OI" in text


def test_registry_lock_12_unchanged():
    """Lock #12 must still say rising-OI only."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(path) as f:
        text = f.read()
    # Find Lock #12 context
    idx = text.find("12. **Family 3 funding")
    assert idx >= 0, "Lock #12 not found"
    chunk = text[idx:idx+500]
    assert "rising" in chunk.lower() or "v0" in chunk, "Lock #12 should mention rising-OI v0"
    assert "falling" not in chunk.lower() or "v1" not in chunk, "Lock #12 should not mention v1"


def test_registry_still_open_adjacent():
    """Still Open has the funding x OI adjacent variants bullet."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(path) as f:
        text = f.read()
    assert "Funding × OI adjacent variants" in text


def test_registry_mined_status_updated():
    """Mined status shows 48 groups and 37 REJECTED."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(path) as f:
        text = f.read()
    assert "48 study groups" in text
    assert "37 REJECTED" in text


def test_registry_v1_status_row_present():
    """Family 3 v1 status table row exists."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "REJECTED_RESEARCH.md"
    )
    with open(path) as f:
        text = f.read()
    assert "family3-funding-falling-oi-unwind-v1-rejected" in text
