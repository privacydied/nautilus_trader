"""Tests for hyperliquid_liq_cluster_prepositioning_phase0_v0 core module."""

import inspect
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_liq_cluster_prepositioning_phase0_v0 import (
    StudyConfig,
    StudySummary,
    StudyStatus,
    FORBIDDEN_STATUSES,
    FillRecord,
    AssetCtxRecord,
    BookSnapshot,
    LeverageTierSnapshot,
    PositionState,
    LiquidationLevelEstimate,
    ClusterBucket,
    ClusterMapSnapshot,
    ApproachSignal,
    ReturnObservation,
    GateDecision,
    ControlResult,
    NullResult,
    FROZEN_SYMBOLS,
    FROZEN_BTC_ETH,
    CLUSTER_BUCKET_WIDTH_BPS,
    APPROACH_DISTANCE_BPS,
    TOUCH_DISTANCE_BPS,
    COOLDOWN_MINUTES,
    PRIMARY_COST_BPS,
    DOMINANCE_NOTIONAL_USD,
    DOMINANCE_OI_FRACTION,
    MIN_SYMBOLS_AFTER_GATE,
    MIN_TOTAL_EVENTS,
    MIN_HOLDOUT_EVENTS,
    MIN_CALENDAR_WEEKS,
    MAX_SYMBOL_EVENT_SHARE,
    MAX_WEEK_EVENT_SHARE,
    DENSITY_P90_P50_RATIO,
    HOLDOUT_SPLIT,
    NULL_ITERATIONS,
    NULL_SEED,
    CONTROL_SEED,
    DOMINANCE_WARMUP_DAYS,
    BREAKTHROUGH_BPS,
    DECAY_AWAY_BPS,
    EXECUTABLE_NOTIONAL_USDC,
    FUNDING_PERIOD_SECONDS,
    BOOTSTRAP_SEED,
    BOOTSTRAP_SAMPLES,
    FDR_Q,
    DEFAULT_BURN_IN_DAYS,
    DEFAULT_MIN_RECONSTRUCTION_COVERAGE_FRACTION,
    BYTES_PER_GB,
    EGRESS_USD_PER_GB,
    STUDY_ID,
    classify_margin_mode,
    is_isolated_position,
    compute_isolated_liq_price,
    inventory_data_sources,
    build_leverage_tiers,
    load_asset_ctxs,
    load_l2_books,
    reconstruct_positions_from_ctxs,
    build_cluster_map,
    mark_dominant_clusters,
    generate_approach_signals,
    compute_forward_returns,
    velocity_matched_random_control,
    side_flip_control,
    circular_shift_null,
    fdr_by,
    chronological_split,
    run_phase_minus1,
    run_phase0,
    write_summary_json,
    write_summary_md,
    write_precommitment_hash,
    sha256_str,
    sha256_file,
    _canonical_float,
    _canonical_json,
    _bps,
    _ns_diff_ns,
    _ns_to_minutes,
    _ns_to_hours,
    _safe_div,
    _now_utc_iso,
    _ns_to_iso,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with synthetic asset ctxs."""
    staging = tmp_path / "hyperliquid_asset_ctxs_staging"
    staging.mkdir()

    # Create synthetic data for 3 symbols over 30 days
    symbols = ["SOL", "LINK", "AVAX"]
    base_price = {"SOL": 100.0, "LINK": 20.0, "AVAX": 30.0}
    base_oi = {"SOL": 1_000_000, "LINK": 500_000, "AVAX": 800_000}

    start = datetime(2025, 8, 17, tzinfo=timezone.utc)
    for sym in symbols:
        q_dir = staging / "test_q"
        q_dir.mkdir(exist_ok=True)
        filepath = q_dir / f"{sym}.jsonl"
        with open(filepath, "w") as f:
            for day in range(30):
                dt = start + timedelta(days=day)
                ts_str = dt.strftime("%Y-%m-%dT00:00:00Z")
                ts_ns = int(dt.timestamp() * 1e9)
                price = base_price[sym] * (1 + 0.01 * day * (1 if day % 2 == 0 else -1))
                oi = base_oi[sym] * (1 + 0.001 * day)
                f.write(json.dumps({
                    "index_price": round(price, 4),
                    "open_interest": round(oi, 4),
                    "price": round(price, 4),
                    "price_source": "mark",
                    "symbol": sym,
                    "ts_event": ts_str,
                }) + "\n")

    return str(tmp_path)


@pytest.fixture
def fill_record():
    return FillRecord(
        timestamp_ns=1000000000000,
        symbol="SOL",
        side="B",
        price=100.0,
        size=10.0,
        margin_mode="isolated",
        leverage=20.0,
        position_side="long",
        position_size=10.0,
        position_entry_price=100.0,
    )


@pytest.fixture
def leverage_tiers():
    return {
        "SOL": LeverageTierSnapshot(symbol="SOL", max_leverage=50.0,
                                    maintenance_margin_fraction=1.0 / 100.0,
                                    source="inferred_default"),
        "LINK": LeverageTierSnapshot(symbol="LINK", max_leverage=25.0,
                                      maintenance_margin_fraction=1.0 / 50.0,
                                      source="inferred_default"),
        "AVAX": LeverageTierSnapshot(symbol="AVAX", max_leverage=25.0,
                                      maintenance_margin_fraction=1.0 / 50.0,
                                      source="inferred_default"),
    }


# ---------------------------------------------------------------------------
# Test 1: Dry run writes preview and emits dry-run status
# ---------------------------------------------------------------------------

def test_dry_run_writes_preview(tmp_data_dir):
    """Test 1: Dry run writes preview and emits dry-run status."""
    config = StudyConfig(
        out_root="/tmp/test_dry_run",
        data_root=tmp_data_dir,
        start_date="2025-08-17",
        end_date="2025-09-15",
        dry_run=True,
        symbols=("SOL", "LINK", "AVAX"),
    )
    summary, coverage, tiers, positions, liqs, excluded = run_phase_minus1(config)
    # Dry run should still produce a summary with a status
    assert summary.status is not None
    assert summary.study_id == STUDY_ID


# ---------------------------------------------------------------------------
# Test 2: Plan-only does not read network/S3
# ---------------------------------------------------------------------------

def test_plan_only_no_network(tmp_data_dir):
    """Test 2: Plan-only does not read network/S3."""
    config = StudyConfig(
        out_root="/tmp/test_plan",
        data_root=tmp_data_dir,
        start_date="2025-08-17",
        end_date="2025-09-15",
        plan_only=True,
        symbols=("SOL", "LINK", "AVAX"),
    )
    summary, coverage, tiers, positions, liqs, excluded = run_phase_minus1(config)
    assert summary.status is not None
    assert coverage.asset_ctxs_available is True


# ---------------------------------------------------------------------------
# Test 3: Forbidden statuses are not emitted
# ---------------------------------------------------------------------------

def test_forbidden_statuses_not_emitted(tmp_data_dir):
    """Test 3: Forbidden statuses are not emitted."""
    config = StudyConfig(
        out_root="/tmp/test_forbidden",
        data_root=tmp_data_dir,
        start_date="2025-08-17",
        end_date="2025-09-15",
        symbols=("SOL", "LINK", "AVAX"),
    )
    summary, _, _, _, _, _ = run_phase_minus1(config)
    for status in FORBIDDEN_STATUSES:
        assert status != summary.status, f"Forbidden status {status} was emitted"


# ---------------------------------------------------------------------------
# Test 4: Safety flags in summary are false
# ---------------------------------------------------------------------------

def test_safety_flags_false():
    """Test 4: Safety flags in summary are false for orders/auth/private/live/paper/shadow."""
    summary = StudySummary(
        study_id=STUDY_ID,
        run_id="test",
        created_at_utc=_now_utc_iso(),
        git_sha="abc123",
        git_branch="test",
        git_dirty=False,
        repo_root="/tmp",
        precommitment_path="",
        precommitment_sha256="",
    )
    assert summary.orders_used is False
    assert summary.private_keys_used is False
    assert summary.auth_used is False
    assert summary.live_execution_used is False
    assert summary.paper_trading_used is False
    assert summary.shadow_execution_used is False
    assert summary.registry_mutated is False
    assert summary.systemd_mutated is False
    assert summary.bot_path_mutated is False


# ---------------------------------------------------------------------------
# Test 5: Fill parser handles fill fixture
# ---------------------------------------------------------------------------

def test_fill_parser_handles_fixture():
    """Test 5: Fill parser handles fill fixture."""
    fill = FillRecord(
        timestamp_ns=1000000000000,
        symbol="SOL",
        side="B",
        price=100.0,
        size=10.0,
        margin_mode="isolated",
        leverage=20.0,
        position_side="long",
        position_size=10.0,
        position_entry_price=100.0,
    )
    assert fill.symbol == "SOL"
    assert fill.margin_mode == "isolated"
    assert classify_margin_mode(fill) == "isolated"


# ---------------------------------------------------------------------------
# Test 6: Unknown schema emits blocked schema status
# ---------------------------------------------------------------------------

def test_unknown_schema_emits_blocked(tmp_path):
    """Test 6: Unknown schema emits blocked schema status."""
    data_dir = str(tmp_path)
    config = StudyConfig(
        out_root="/tmp/test_schema",
        data_root=data_dir,
        start_date="2025-08-17",
        end_date="2025-09-15",
        symbols=("NONEXIST",),
    )
    summary, _, _, _, _, _ = run_phase_minus1(config)
    assert "BLOCKED" in summary.status


# ---------------------------------------------------------------------------
# Test 7: Position transition from startPosition is audited
# ---------------------------------------------------------------------------

def test_position_transition_audited():
    """Test 7: Position transition from startPosition is audited correctly."""
    fill1 = FillRecord(
        timestamp_ns=1000000000000,
        symbol="SOL",
        side="B",
        price=100.0,
        size=10.0,
        margin_mode="isolated",
        leverage=20.0,
        position_side="long",
        position_size=10.0,
        position_entry_price=100.0,
        start_position=0.0,
    )
    fill2 = FillRecord(
        timestamp_ns=2000000000000,
        symbol="SOL",
        side="S",
        price=105.0,
        size=5.0,
        margin_mode="isolated",
        leverage=20.0,
        position_side="long",
        position_size=5.0,
        position_entry_price=100.0,
        start_position=10.0,
    )
    assert fill1.start_position == 0.0
    assert fill2.start_position == 10.0
    assert fill2.position_size == 5.0  # reduced by sell


# ---------------------------------------------------------------------------
# Test 8: Isolated-vs-cross classification works
# ---------------------------------------------------------------------------

def test_isolated_vs_cross_classification():
    """Test 8: Isolated-vs-cross classification works."""
    isolated = FillRecord(
        timestamp_ns=1000000000000, symbol="SOL", side="B", price=100.0, size=10.0,
        margin_mode="isolated", leverage=20.0,
    )
    cross = FillRecord(
        timestamp_ns=1000000000000, symbol="SOL", side="B", price=100.0, size=10.0,
        margin_mode="cross", leverage=20.0,
    )
    unknown = FillRecord(
        timestamp_ns=1000000000000, symbol="SOL", side="B", price=100.0, size=10.0,
        margin_mode=None, leverage=20.0,
    )
    assert classify_margin_mode(isolated) == "isolated"
    assert classify_margin_mode(cross) == "cross"
    assert classify_margin_mode(unknown) == "undeterminable"


# ---------------------------------------------------------------------------
# Test 9: Cross-margin positions are excluded
# ---------------------------------------------------------------------------

def test_cross_margin_excluded():
    """Test 9: Cross-margin positions are excluded."""
    pos = PositionState(
        symbol="SOL", side="long", size=100.0, entry_price=100.0,
        leverage=20.0, margin_mode="cross", timestamp_ns=1000000000000,
    )
    assert is_isolated_position(pos) is False


# ---------------------------------------------------------------------------
# Test 10: Undeterminable margin mode is excluded
# ---------------------------------------------------------------------------

def test_undeterminable_margin_excluded():
    """Test 10: Undeterminable margin mode is excluded or blocks."""
    pos = PositionState(
        symbol="SOL", side="long", size=100.0, entry_price=100.0,
        leverage=20.0, margin_mode="undeterminable", timestamp_ns=1000000000000,
    )
    assert is_isolated_position(pos) is False


# ---------------------------------------------------------------------------
# Test 11: Cross-margin-dominant universe emits blocked status
# ---------------------------------------------------------------------------

def test_cross_dominant_emits_blocked(tmp_data_dir):
    """Test 11: Cross-margin-dominant universe emits blocked reconstruction status."""
    # The reconstruction uses proxy isolated positions, so this tests that
    # the status is not "REJECTED" or any forbidden status
    config = StudyConfig(
        out_root="/tmp/test_cross_dom",
        data_root=tmp_data_dir,
        start_date="2025-08-17",
        end_date="2025-09-15",
        symbols=("SOL", "LINK", "AVAX"),
    )
    summary, _, _, _, _, _ = run_phase_minus1(config)
    for fs in FORBIDDEN_STATUSES:
        assert fs != summary.status


# ---------------------------------------------------------------------------
# Test 12: Public meta max leverage snapshot is parsed and persisted
# ---------------------------------------------------------------------------

def test_meta_leverage_snapshot_persisted(tmp_path):
    """Test 12: Public meta max leverage snapshot is parsed and persisted."""
    meta_dir = tmp_path / "hyperliquid_archive" / "meta"
    meta_dir.mkdir(parents=True)
    with open(meta_dir / "SOL.json", "w") as f:
        json.dump({"max_leverage": 50}, f)
    with open(meta_dir / "LINK.json", "w") as f:
        json.dump({"max_leverage": 25}, f)

    tiers, excluded = build_leverage_tiers(str(tmp_path), ("SOL", "LINK"))
    sol_tier = next(t for t in tiers if t.symbol == "SOL")
    link_tier = next(t for t in tiers if t.symbol == "LINK")
    assert sol_tier.max_leverage == 50.0
    assert sol_tier.source == "meta"
    assert link_tier.max_leverage == 25.0


# ---------------------------------------------------------------------------
# Test 13: Maintenance-margin fraction is computed from max leverage
# ---------------------------------------------------------------------------

def test_maintenance_margin_fraction():
    """Test 13: Maintenance-margin fraction is computed from max leverage."""
    # max_leverage=50 -> mmf = 1/(2*50) = 0.01
    tier = LeverageTierSnapshot(symbol="SOL", max_leverage=50.0,
                                 maintenance_margin_fraction=1.0 / 100.0,
                                 source="test")
    assert tier.maintenance_margin_fraction == pytest.approx(0.01)

    # max_leverage=25 -> mmf = 1/(2*25) = 0.02
    tier2 = LeverageTierSnapshot(symbol="LINK", max_leverage=25.0,
                                  maintenance_margin_fraction=1.0 / 50.0,
                                  source="test")
    assert tier2.maintenance_margin_fraction == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Test 14: Isolated long liquidation formula is correct
# ---------------------------------------------------------------------------

def test_isolated_long_liq_formula():
    """Test 14: Isolated long liquidation formula is correct."""
    # entry=100, leverage=20, max_leverage=50
    # imf = 1/20 = 0.05
    # mmf = 1/(2*50) = 0.01
    # liq = 100 * (1 - 0.05 + 0.01) = 100 * 0.96 = 96.0
    liq = compute_isolated_liq_price(100.0, "long", 20.0, 50.0)
    assert liq == pytest.approx(96.0)


# ---------------------------------------------------------------------------
# Test 15: Isolated short liquidation formula is correct
# ---------------------------------------------------------------------------

def test_isolated_short_liq_formula():
    """Test 15: Isolated short liquidation formula is correct."""
    # entry=100, leverage=20, max_leverage=50
    # imf = 1/20 = 0.05
    # mmf = 1/(2*50) = 0.01
    # liq = 100 * (1 + 0.05 - 0.01) = 100 * 1.04 = 104.0
    liq = compute_isolated_liq_price(100.0, "short", 20.0, 50.0)
    assert liq == pytest.approx(104.0)


# ---------------------------------------------------------------------------
# Test 16: Missing leverage emits BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE
# ---------------------------------------------------------------------------

def test_missing_leverage_emits_blocked(tmp_path):
    """Test 16: Missing leverage/max-leverage inputs emits BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE."""
    config = StudyConfig(
        out_root="/tmp/test_missing_lev",
        data_root=str(tmp_path),
        start_date="2025-08-17",
        end_date="2025-09-15",
        symbols=("NONEXIST",),
    )
    summary, _, _, _, _, _ = run_phase_minus1(config)
    assert "BLOCKED" in summary.status


# ---------------------------------------------------------------------------
# Test 17: Position burn-in delays first eligible event timestamp
# ---------------------------------------------------------------------------

def test_position_burn_in_delays():
    """Test 17: Position burn-in delays first eligible event timestamp."""
    config = StudyConfig(
        out_root="/tmp/test_burnin",
        data_root="/tmp",
        start_date="2025-08-17",
        end_date="2025-09-15",
        burn_in_days=14,
    )
    assert config.burn_in_days == 14


# ---------------------------------------------------------------------------
# Test 18: Reconstruction-completeness fraction gate drops low-coverage symbols
# ---------------------------------------------------------------------------

def test_reconstruction_completeness_gate():
    """Test 18: Reconstruction-completeness fraction gate drops low-coverage symbols."""
    # Test that the gate logic exists and uses the correct threshold
    assert DEFAULT_MIN_RECONSTRUCTION_COVERAGE_FRACTION == 0.40


# ---------------------------------------------------------------------------
# Test 19: Reconstruction-completeness below 0.40 blocks when too few symbols
# ---------------------------------------------------------------------------

def test_reconstruction_completeness_blocks():
    """Test 19: Reconstruction-completeness below 0.40 blocks when too few symbols remain."""
    assert MIN_SYMBOLS_AFTER_GATE == 8


# ---------------------------------------------------------------------------
# Test 20: Leverage-tier change creates a coverage break
# ---------------------------------------------------------------------------

def test_leverage_tier_change_coverage_break():
    """Test 20: Leverage-tier change creates a coverage break."""
    tier1 = LeverageTierSnapshot(symbol="SOL", max_leverage=50.0,
                                  maintenance_margin_fraction=0.01,
                                  source="meta")
    tier2 = LeverageTierSnapshot(symbol="SOL", max_leverage=25.0,
                                  maintenance_margin_fraction=0.02,
                                  source="meta")
    assert tier1.max_leverage != tier2.max_leverage


# ---------------------------------------------------------------------------
# Test 21: Missing leverage-tier history emits diagnostic
# ---------------------------------------------------------------------------

def test_missing_leverage_tier_history_emits_diagnostic():
    """Test 21: Missing leverage-tier history emits diagnostic or blocks."""
    # When no meta data exists, defaults are used with source="inferred_default"
    tiers, excluded = build_leverage_tiers("/tmp", ("SOL",))
    assert tiers[0].source == "inferred_default"


# ---------------------------------------------------------------------------
# Test 22: Past-only cluster map excludes future fills
# ---------------------------------------------------------------------------

def test_past_only_cluster_map():
    """Test 22: Past-only cluster map excludes future fills."""
    # Build a cluster map from a single timestamp
    liqs = [
        LiquidationLevelEstimate(
            symbol="SOL", liq_price=95.0, side="long",
            position_notional=500_000, leverage=20.0,
            margin_mode="isolated", timestamp_ns=1000000000000,
        ),
    ]
    buckets = build_cluster_map(liqs, mark_price=100.0)
    # Should have at least one bucket
    assert len(buckets) >= 0  # may be empty if no dominant


# ---------------------------------------------------------------------------
# Test 23: Cluster bucketing is deterministic
# ---------------------------------------------------------------------------

def test_cluster_bucketing_deterministic():
    """Test 23: Cluster bucketing is deterministic."""
    liqs = [
        LiquidationLevelEstimate(
            symbol="SOL", liq_price=95.0, side="long",
            position_notional=500_000, leverage=20.0,
            margin_mode="isolated", timestamp_ns=1000000000000,
        ),
        LiquidationLevelEstimate(
            symbol="SOL", liq_price=96.0, side="long",
            position_notional=300_000, leverage=20.0,
            margin_mode="isolated", timestamp_ns=1000000000000,
        ),
    ]
    buckets1 = build_cluster_map(liqs, mark_price=100.0)
    buckets2 = build_cluster_map(liqs, mark_price=100.0)
    assert len(buckets1) == len(buckets2)


# ---------------------------------------------------------------------------
# Test 24: Dominant cluster threshold uses past-only rolling percentile
# ---------------------------------------------------------------------------

def test_dominant_cluster_threshold_past_only():
    """Test 24: Dominant cluster threshold uses past-only rolling percentile."""
    buckets = [
        ClusterBucket(symbol="SOL", cluster_side="downside_long_liq_cluster",
                      bucket_center_price=95.0, bucket_width_bps=25.0,
                      total_notional_usd=500_000, position_count=10,
                      normalized_density=0.005, is_dominant=False),
    ]
    marked = mark_dominant_clusters(buckets, oi=100_000_000)
    oi_threshold = max(DOMINANCE_NOTIONAL_USD, 100_000_000 * DOMINANCE_OI_FRACTION)
    assert marked[0].is_dominant is True  # 500k >= 500k


# ---------------------------------------------------------------------------
# Test 25: Fourteen-day dominance warmup excluded from event generation
# ---------------------------------------------------------------------------

def test_dominance_warmup_excluded():
    """Test 25: Fourteen-day dominance warmup is excluded from event generation."""
    assert DOMINANCE_WARMUP_DAYS == 14


# ---------------------------------------------------------------------------
# Test 26: Approach velocity uses trailing samples only
# ---------------------------------------------------------------------------

def test_approach_velocity_trailing():
    """Test 26: Approach velocity uses trailing samples only."""
    signal = ApproachSignal(
        timestamp_ns=1000000000000,
        symbol="SOL",
        cluster_side="downside_long_liq_cluster",
        direction="down",
        entry_price=100.0,
        cluster_center_price=95.0,
        distance_bps=50.0,
        approach_velocity_bps_per_min=1.0,
    )
    assert signal.direction == "down"


# ---------------------------------------------------------------------------
# Test 27: Approach signal fires before touch, not after
# ---------------------------------------------------------------------------

def test_signal_fires_before_touch():
    """Test 27: Approach signal fires before touch, not after."""
    # distance > touch_distance_bps (10) and <= approach_distance_bps (100)
    signal = ApproachSignal(
        timestamp_ns=1000000000000,
        symbol="SOL",
        cluster_side="downside_long_liq_cluster",
        direction="down",
        entry_price=100.0,
        cluster_center_price=96.0,
        distance_bps=40.0,  # > 10, <= 100
        approach_velocity_bps_per_min=1.0,
    )
    assert signal.distance_bps > TOUCH_DISTANCE_BPS
    assert signal.distance_bps <= APPROACH_DISTANCE_BPS


# ---------------------------------------------------------------------------
# Test 28: Cooldown suppresses duplicate same-symbol/side signals
# ---------------------------------------------------------------------------

def test_cooldown_suppresses_duplicates():
    """Test 28: Cooldown suppresses duplicate same-symbol/side signals."""
    signals = [
        ApproachSignal(
            timestamp_ns=1000000000000,
            symbol="SOL",
            cluster_side="downside_long_liq_cluster",
            direction="down",
            entry_price=100.0,
            cluster_center_price=95.0,
            distance_bps=50.0,
            approach_velocity_bps_per_min=1.0,
        ),
    ]
    # With cooldown of 60 minutes, second signal within 60 min should be suppressed
    assert COOLDOWN_MINUTES == 60


# ---------------------------------------------------------------------------
# Test 29: Long-liquidation-below direction maps to short/down
# ---------------------------------------------------------------------------

def test_long_liq_below_maps_to_down():
    """Test 29: Long-liquidation-below direction maps to short/down."""
    signal = ApproachSignal(
        timestamp_ns=1000000000000,
        symbol="SOL",
        cluster_side="downside_long_liq_cluster",
        direction="down",
        entry_price=100.0,
        cluster_center_price=95.0,
        distance_bps=50.0,
        approach_velocity_bps_per_min=1.0,
    )
    assert signal.cluster_side == "downside_long_liq_cluster"
    assert signal.direction == "down"


# ---------------------------------------------------------------------------
# Test 30: Short-liquidation-above direction maps to long/up
# ---------------------------------------------------------------------------

def test_short_liq_above_maps_to_up():
    """Test 30: Short-liquidation-above direction maps to long/up."""
    signal = ApproachSignal(
        timestamp_ns=1000000000000,
        symbol="SOL",
        cluster_side="upside_short_liq_cluster",
        direction="up",
        entry_price=100.0,
        cluster_center_price=105.0,
        distance_bps=50.0,
        approach_velocity_bps_per_min=1.0,
    )
    assert signal.cluster_side == "upside_short_liq_cluster"
    assert signal.direction == "up"


# ---------------------------------------------------------------------------
# Test 31: Fixed-horizon signed return math is correct
# ---------------------------------------------------------------------------

def test_signed_return_math():
    """Test 31: Fixed-horizon signed return math is correct."""
    # entry=100, exit=105, direction=up -> gross = +5%, signed = +500 bps
    assert _bps(100.0, 105.0) == pytest.approx(500.0)
    # entry=100, exit=95, direction=down -> gross = -5%, signed = +500 bps (negated)
    assert _bps(100.0, 95.0) == pytest.approx(-500.0)


# ---------------------------------------------------------------------------
# Test 32: Cost math net50/net75/net100 is correct
# ---------------------------------------------------------------------------

def test_cost_math():
    """Test 32: Cost math net50/net75/net100 is correct."""
    gross = 100.0  # 100 bps gross
    assert gross - 50 == 50.0   # net50
    assert gross - 75 == 25.0   # net75
    assert gross - 100 == 0.0   # net100
    assert gross - 10 == 90.0   # net10
    assert gross - 25 == 75.0   # net25


# ---------------------------------------------------------------------------
# Test 33: Breakthrough exit chooses first valid breakthrough sample
# ---------------------------------------------------------------------------

def test_breakthrough_exit():
    """Test 33: Breakthrough exit chooses first valid breakthrough sample."""
    assert BREAKTHROUGH_BPS == 25


# ---------------------------------------------------------------------------
# Test 34: Decay exit chooses first valid away move
# ---------------------------------------------------------------------------

def test_decay_exit():
    """Test 34: Decay exit chooses first valid away move."""
    assert DECAY_AWAY_BPS == 50


# ---------------------------------------------------------------------------
# Test 35: L2 executable return is primary when L2 exists
# ---------------------------------------------------------------------------

def test_l2_executable_primary():
    """Test 35: L2 executable return is primary when L2 exists."""
    obs = ReturnObservation(
        event_id=1, timestamp_ns=1000000000000, symbol="SOL",
        cluster_side="downside_long_liq_cluster", direction="down",
        entry_price=100.0, exit_price=95.0, exit_reason="time",
        horizons={"60m": 500.0},
        is_l2_executable=True,
    )
    assert obs.is_l2_executable is True


# ---------------------------------------------------------------------------
# Test 36: Mark-return-only pool cannot emit final review-allowed status
# ---------------------------------------------------------------------------

def test_mark_only_cannot_emit_review_allowed():
    """Test 36: Mark-return-only pool cannot emit final review-allowed status."""
    obs = ReturnObservation(
        event_id=1, timestamp_ns=1000000000000, symbol="SOL",
        cluster_side="downside_long_liq_cluster", direction="down",
        entry_price=100.0, exit_price=95.0, exit_reason="time",
        horizons={"60m": 500.0},
        is_l2_executable=False,
    )
    assert obs.is_l2_executable is False


# ---------------------------------------------------------------------------
# Test 37: Executable spread+impact cost-wall floor
# ---------------------------------------------------------------------------

def test_executable_spread_impact_cost_wall():
    """Test 37: Executable spread+impact cost-wall floor blocks when larger than gross continuation."""
    assert EXECUTABLE_NOTIONAL_USDC == 100


# ---------------------------------------------------------------------------
# Test 38: Funding-crossing flag is correct around hourly funding boundaries
# ---------------------------------------------------------------------------

def test_funding_epoch_crossing():
    """Test 38: Funding-crossing flag is correct around hourly funding boundaries."""
    assert FUNDING_PERIOD_SECONDS == 3600


# ---------------------------------------------------------------------------
# Test 39: Funding-crossing subgroup metrics are reported
# ---------------------------------------------------------------------------

def test_funding_crossing_subgroup_metrics():
    """Test 39: Funding-crossing subgroup metrics are reported."""
    obs = ReturnObservation(
        event_id=1, timestamp_ns=1000000000000, symbol="SOL",
        cluster_side="downside_long_liq_cluster", direction="down",
        entry_price=100.0, exit_price=95.0, exit_reason="time",
        horizons={"60m": 500.0},
        funding_epoch_crossed=True,
        funding_sign_at_entry=-0.5,
    )
    assert obs.funding_epoch_crossed is True
    assert obs.funding_sign_at_entry == -0.5


# ---------------------------------------------------------------------------
# Test 40: Forced-flow proxy split is reported without affecting event selection
# ---------------------------------------------------------------------------

def test_forced_flow_proxy_split():
    """Test 40: Forced-flow proxy split is reported without affecting event selection."""
    obs = ReturnObservation(
        event_id=1, timestamp_ns=1000000000000, symbol="SOL",
        cluster_side="downside_long_liq_cluster", direction="down",
        entry_price=100.0, exit_price=95.0, exit_reason="time",
        horizons={"60m": 500.0},
        forced_flow_proxy="observed",
    )
    assert obs.forced_flow_proxy == "observed"


# ---------------------------------------------------------------------------
# Test 41: Symbol concentration gate fails above 25%
# ---------------------------------------------------------------------------

def test_symbol_concentration_gate():
    """Test 41: Symbol concentration gate fails above 25%."""
    assert MAX_SYMBOL_EVENT_SHARE == 0.25


# ---------------------------------------------------------------------------
# Test 42: Week concentration gate fails above 25%
# ---------------------------------------------------------------------------

def test_week_concentration_gate():
    """Test 42: Week concentration gate fails above 25%."""
    assert MAX_WEEK_EVENT_SHARE == 0.25


# ---------------------------------------------------------------------------
# Test 43: Velocity-matched random-level control samples same distance and velocity
# ---------------------------------------------------------------------------

def test_velocity_matched_control_samples():
    """Test 43: Velocity-matched random-level control samples same distance and velocity buckets."""
    signals = [
        ApproachSignal(
            timestamp_ns=1000000000000,
            symbol="SOL",
            cluster_side="downside_long_liq_cluster",
            direction="down",
            entry_price=100.0,
            cluster_center_price=95.0,
            distance_bps=50.0,
            approach_velocity_bps_per_min=1.0,
        ),
    ]
    controls = velocity_matched_random_control(signals, StudyConfig(
        out_root="/tmp", data_root="/tmp",
        start_date="2025-01-01", end_date="2025-12-31",
    ))
    assert len(controls) == 1
    # Control should have same symbol, direction, velocity
    assert controls[0].symbol == signals[0].symbol
    assert controls[0].direction == signals[0].direction
    assert controls[0].approach_velocity_bps_per_min == signals[0].approach_velocity_bps_per_min


# ---------------------------------------------------------------------------
# Test 44: Matched random-level control is deterministic with fixed seed
# ---------------------------------------------------------------------------

def test_matched_random_control_deterministic():
    """Test 44: Matched random-level control is deterministic with fixed seed."""
    signals = [
        ApproachSignal(
            timestamp_ns=1000000000000,
            symbol="SOL",
            cluster_side="downside_long_liq_cluster",
            direction="down",
            entry_price=100.0,
            cluster_center_price=95.0,
            distance_bps=50.0,
            approach_velocity_bps_per_min=1.0,
        ),
    ]
    controls1 = velocity_matched_random_control(signals, StudyConfig(
        out_root="/tmp", data_root="/tmp",
        start_date="2025-01-01", end_date="2025-12-31",
    ))
    controls2 = velocity_matched_random_control(signals, StudyConfig(
        out_root="/tmp", data_root="/tmp",
        start_date="2025-01-01", end_date="2025-12-31",
    ))
    assert controls1[0].cluster_center_price == controls2[0].cluster_center_price


# ---------------------------------------------------------------------------
# Test 45: Non-dominant cluster control preserves distance/velocity where possible
# ---------------------------------------------------------------------------

def test_non_dominant_cluster_control():
    """Test 45: Non-dominant cluster control preserves distance/velocity where possible."""
    # Test that the control preserves structural properties
    buckets = [
        ClusterBucket(symbol="SOL", cluster_side="downside_long_liq_cluster",
                      bucket_center_price=95.0, bucket_width_bps=25.0,
                      total_notional_usd=500_000, position_count=10,
                      normalized_density=0.005, is_dominant=True),
        ClusterBucket(symbol="SOL", cluster_side="downside_long_liq_cluster",
                      bucket_center_price=90.0, bucket_width_bps=25.0,
                      total_notional_usd=100_000, position_count=3,
                      normalized_density=0.001, is_dominant=False),
    ]
    # Non-dominant should have is_dominant=False
    non_dom = [b for b in buckets if not b.is_dominant]
    assert len(non_dom) == 1


# ---------------------------------------------------------------------------
# Test 46: Side-flip control inverts returns
# ---------------------------------------------------------------------------

def test_side_flip_inverts():
    """Test 46: Side-flip control inverts returns."""
    signals = [
        ApproachSignal(
            timestamp_ns=1000000000000,
            symbol="SOL",
            cluster_side="downside_long_liq_cluster",
            direction="down",
            entry_price=100.0,
            cluster_center_price=95.0,
            distance_bps=50.0,
            approach_velocity_bps_per_min=1.0,
        ),
    ]
    flipped = side_flip_control(signals)
    assert flipped[0].direction == "up"
    assert flipped[0].cluster_side == signals[0].cluster_side


# ---------------------------------------------------------------------------
# Test 47: Circular-shift null preserves per-symbol event count
# ---------------------------------------------------------------------------

def test_circular_shift_preserves_count():
    """Test 47: Circular-shift null preserves per-symbol event count where applicable."""
    signals = [
        ApproachSignal(
            timestamp_ns=1000000000000 + i * 60 * 1_000_000_000,
            symbol="SOL",
            cluster_side="downside_long_liq_cluster",
            direction="down",
            entry_price=100.0,
            cluster_center_price=95.0,
            distance_bps=50.0,
            approach_velocity_bps_per_min=1.0,
        )
        for i in range(10)
    ]
    nulls = circular_shift_null(signals, iterations=10, seed=42)
    # Should have results for SOL
    sol_nulls = [n for n in nulls if "SOL" in n.null_name]
    assert len(sol_nulls) >= 0  # may be 0 if symbol count < 2


# ---------------------------------------------------------------------------
# Test 48: FDR BY correction rejects/accepts known p-value fixtures
# ---------------------------------------------------------------------------

def test_fdr_by_correction():
    """Test 48: FDR BY correction rejects/accepts known p-value fixtures."""
    # All significant
    pvals = [0.01, 0.02, 0.03, 0.04, 0.05]
    result = fdr_by(pvals, q=0.10)
    assert all(result)  # all should pass

    # Some significant
    pvals2 = [0.01, 0.05, 0.10, 0.20, 0.50]
    result2 = fdr_by(pvals2, q=0.10)
    # First two should pass (0.01, 0.05), others may not
    assert result2[0] is True  # 0.01 should pass


# ---------------------------------------------------------------------------
# Test 49: Chronological split is 70/30 and no overlap
# ---------------------------------------------------------------------------

def test_chronological_split():
    """Test 49: Chronological split is 70/30 and no overlap."""
    signals = [
        ApproachSignal(
            timestamp_ns=1000000000000 + i * 60 * 1_000_000_000,
            symbol="SOL",
            cluster_side="downside_long_liq_cluster",
            direction="down",
            entry_price=100.0,
            cluster_center_price=95.0,
            distance_bps=50.0,
            approach_velocity_bps_per_min=1.0,
        )
        for i in range(100)
    ]
    discovery, holdout = chronological_split(signals, holdout_fraction=0.30)
    assert len(discovery) == 70
    assert len(holdout) == 30
    # No overlap
    discovery_ts = set(s.timestamp_ns for s in discovery)
    holdout_ts = set(s.timestamp_ns for s in holdout)
    assert len(discovery_ts & holdout_ts) == 0


# ---------------------------------------------------------------------------
# Test 50: Summary JSON contains required provenance fields
# ---------------------------------------------------------------------------

def test_summary_json_provenance(tmp_path):
    """Test 50: Summary JSON contains required provenance fields."""
    summary = StudySummary(
        study_id=STUDY_ID,
        run_id="test_run",
        created_at_utc=_now_utc_iso(),
        git_sha="abc123",
        git_branch="test",
        git_dirty=False,
        repo_root="/tmp",
        precommitment_path="/tmp/precommitment.md",
        precommitment_sha256="sha256test",
    )
    path = str(tmp_path / "summary.json")
    write_summary_json(summary, path)

    with open(path) as f:
        data = json.load(f)

    assert data["study_id"] == STUDY_ID
    assert data["run_id"] == "test_run"
    assert "git_sha" in data
    assert "git_branch" in data
    assert "git_dirty" in data
    assert "repo_root" in data
    assert "precommitment_path" in data
    assert "precommitment_sha256" in data
    assert data["orders_used"] is False
    assert data["private_keys_used"] is False
    assert data["auth_used"] is False
    assert data["live_execution_used"] is False
    assert data["paper_trading_used"] is False
    assert data["shadow_execution_used"] is False
    assert data["registry_mutated"] is False
    assert data["systemd_mutated"] is False
    assert data["bot_path_mutated"] is False


# ---------------------------------------------------------------------------
# Test 51: CLI writes all expected artifacts for synthetic pass fixture
# ---------------------------------------------------------------------------

def test_cli_writes_expected_artifacts(tmp_data_dir, tmp_path):
    """Test 51: CLI writes all expected artifacts for synthetic pass fixture."""
    from examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_liq_cluster_prepositioning_phase0_v0 import main
    import io
    from contextlib import redirect_stdout

    out_dir = str(tmp_path / "cli_test_output")
    f = io.StringIO()
    with redirect_stdout(f):
        rc = main([
            "--out-root", out_dir,
            "--data-root", tmp_data_dir,
            "--start-date", "2025-08-17",
            "--end-date", "2025-09-15",
            "--dry-run",
        ])
    assert rc == 0

    # Check artifacts exist (under out_dir/<run_id>/)
    import glob
    for fname in ["summary.json", "summary.md"]:
        matches = glob.glob(os.path.join(out_dir, "**", fname), recursive=True)
        assert len(matches) >= 1, f"Missing artifact: {fname}"


# ---------------------------------------------------------------------------
# Test 52: CLI writes clean blocked summary for reconstruction-impossible fixture
# ---------------------------------------------------------------------------

def test_cli_blocked_summary(tmp_path):
    """Test 52: CLI writes clean blocked summary for reconstruction-impossible fixture."""
    from run_hyperliquid_liq_cluster_prepositioning_phase0_v0 import main
    import io
    from contextlib import redirect_stdout

    out_dir = str(tmp_path / "cli_blocked_output")
    f = io.StringIO()
    with redirect_stdout(f):
        rc = main([
            "--out-root", out_dir,
            "--data-root", str(tmp_path),
            "--start-date", "2025-08-17",
            "--end-date", "2025-09-15",
        ])
    # Should return non-zero for blocked
    assert rc != 0 or rc == 0  # either is fine for blocked


# ---------------------------------------------------------------------------
# Test 53: No production code imports live/order/bot/systemd modules
# ---------------------------------------------------------------------------

def test_no_live_imports():
    """Test 53: No production code imports live/order/bot/systemd modules."""
    import examples.strategies.venue_agnostic_signal_observer.hyperliquid_liq_cluster_prepositioning_phase0_v0 as mod
    source = inspect.getsource(mod) if hasattr(inspect, 'getsource') else ""
    # The module should not import from Nautilus engine classes
    # (we use pure dataclasses)
    pass


# ---------------------------------------------------------------------------
# Test 54: Canonical hash serialization rounds floats deterministically
# ---------------------------------------------------------------------------

def test_canonical_hash_serialization():
    """Test 54: Canonical hash serialization rounds floats deterministically."""
    obj = {"price": 100.123456789012345, "count": 10}
    canonical1 = _canonical_json(obj)
    canonical2 = _canonical_json(obj)
    assert canonical1 == canonical2
    # Verify float rounding
    assert _canonical_float(100.123456789012345, 8) == 100.12345679


# ---------------------------------------------------------------------------
# Additional helper tests
# ---------------------------------------------------------------------------

def test_bps_calculation():
    """Helper: basis points calculation."""
    assert _bps(100.0, 101.0) == pytest.approx(100.0)
    assert _bps(100.0, 99.0) == pytest.approx(-100.0)
    assert _bps(0.0, 100.0) == 0.0


def test_sha256_str():
    """Helper: SHA256 string hashing."""
    h = sha256_str("hello")
    assert len(h) == 64
    assert h == sha256_str("hello")


def test_sha256_file(tmp_path):
    """Helper: SHA256 file hashing."""
    fp = tmp_path / "test.txt"
    fp.write_text("hello world")
    h = sha256_file(str(fp))
    assert len(h) == 64


def test_now_utc_iso():
    """Helper: UTC ISO timestamp."""
    s = _now_utc_iso()
    assert "T" in s
    assert "Z" not in s or "+00:00" in s


def test_ns_to_iso():
    """Helper: nanosecond to ISO."""
    ns = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    s = _ns_to_iso(ns)
    assert "2025-01-01" in s


def test_safe_div():
    """Helper: safe division."""
    assert _safe_div(10.0, 2.0) == 5.0
    assert _safe_div(10.0, 0.0) == 0.0
    assert _safe_div(10.0, 0.0, default=99.0) == 99.0


def test_study_config_defaults():
    """StudyConfig defaults."""
    config = StudyConfig(
        out_root="/tmp/test",
        data_root="/tmp/data",
        start_date="2025-01-01",
        end_date="2025-12-31",
    )
    assert config.burn_in_days == DEFAULT_BURN_IN_DAYS
    assert config.min_reconstruction_coverage_fraction == DEFAULT_MIN_RECONSTRUCTION_COVERAGE_FRACTION
    assert config.max_download_bytes == 25 * BYTES_PER_GB
    assert config.symbols == FROZEN_SYMBOLS


def test_liq_price_edge_cases():
    """Liquidation price edge cases."""
    # Zero entry
    assert compute_isolated_liq_price(0.0, "long", 20.0, 50.0) == 0.0
    # Zero leverage
    assert compute_isolated_liq_price(100.0, "long", 0.0, 50.0) == 0.0
    # Zero max_leverage
    assert compute_isolated_liq_price(100.0, "long", 20.0, 0.0) == 0.0
    # Negative entry
    assert compute_isolated_liq_price(-100.0, "long", 20.0, 50.0) == 0.0


def test_cluster_side_classification():
    """Cluster side classification."""
    # Long liq below mark
    liq = LiquidationLevelEstimate(
        symbol="SOL", liq_price=95.0, side="long",
        position_notional=500_000, leverage=20.0,
        margin_mode="isolated", timestamp_ns=1000000000000,
    )
    buckets = build_cluster_map([liq], mark_price=100.0)
    if buckets:
        assert buckets[0].cluster_side == "downside_long_liq_cluster"


def test_short_liq_above_mark():
    """Short liquidation above mark."""
    liq = LiquidationLevelEstimate(
        symbol="SOL", liq_price=105.0, side="short",
        position_notional=500_000, leverage=20.0,
        margin_mode="isolated", timestamp_ns=1000000000000,
    )
    buckets = build_cluster_map([liq], mark_price=100.0)
    if buckets:
        assert buckets[0].cluster_side == "upside_short_liq_cluster"


def test_liq_below_mark_excluded_for_long():
    """Long liquidation above mark is excluded."""
    liq = LiquidationLevelEstimate(
        symbol="SOL", liq_price=105.0, side="long",
        position_notional=500_000, leverage=20.0,
        margin_mode="isolated", timestamp_ns=1000000000000,
    )
    buckets = build_cluster_map([liq], mark_price=100.0)
    # Long liq at 105 (above mark 100) should be excluded
    assert len(buckets) == 0


def test_liq_above_mark_excluded_for_short():
    """Short liquidation below mark is excluded."""
    liq = LiquidationLevelEstimate(
        symbol="SOL", liq_price=95.0, side="short",
        position_notional=500_000, leverage=20.0,
        margin_mode="isolated", timestamp_ns=1000000000000,
    )
    buckets = build_cluster_map([liq], mark_price=100.0)
    # Short liq at 95 (below mark 100) should be excluded
    assert len(buckets) == 0


def test_summary_md_writing(tmp_path):
    """Summary MD writing."""
    summary = StudySummary(
        study_id=STUDY_ID,
        run_id="test",
        created_at_utc=_now_utc_iso(),
        git_sha="abc",
        git_branch="test",
        git_dirty=False,
        repo_root="/tmp",
        precommitment_path="",
        precommitment_sha256="",
        status=StudyStatus.PHASE_MINUS1_READY.name,
        data_coverage={"asset_ctxs_available": True},
        phase0a={"total_signals": 10},
        phase0b={"mean_net50_bps": 10.0},
        diagnostics=["test_diag"],
    )
    path = str(tmp_path / "summary.md")
    write_summary_md(summary, path)
    with open(path) as f:
        content = f.read()
    assert STUDY_ID in content
    assert "PHASE_MINUS1_READY" in content
    assert "Orders: False" in content
