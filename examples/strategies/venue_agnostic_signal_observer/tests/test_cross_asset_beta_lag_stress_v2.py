"""Tests for cross_asset_beta_lag_stress_v2 trigger and worktree wiring."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.strategies.venue_agnostic_signal_observer import stage2_gate_watcher as watcher


def _ticks(asset: str, prices: list[tuple[float, float]]) -> list[dict[str, float | str]]:
    return [
        {"asset": asset, "ts_seconds": ts_seconds, "price": price}
        for ts_seconds, price in prices
    ]


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_stress_trigger_fires_for_30bps_30s_move_with_active_stress(asset: str) -> None:
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=_ticks(asset, [(0.0, 100.0), (30.0, 100.3)]),
        stress_confirmation_active=True,
    )

    assert result.triggered is True
    assert result.source_asset == asset
    assert result.source_move_bps_30s == pytest.approx(30.0)
    assert result.trigger_name == "source_30bps_30s_and_crypto_native_stress"


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_stress_trigger_does_not_fire_at_29_99bps(asset: str) -> None:
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=_ticks(asset, [(0.0, 100.0), (30.0, 100.2999)]),
        stress_confirmation_active=True,
    )

    assert result.triggered is False
    assert result.source_move_bps_30s == pytest.approx(29.99)


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_stress_trigger_requires_stress_confirmation(asset: str) -> None:
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=_ticks(asset, [(0.0, 100.0), (30.0, 100.3)]),
        stress_confirmation_active=False,
    )

    assert result.triggered is False
    assert result.impulse_condition_met is True
    assert result.stress_confirmation_status == "INACTIVE"


def test_stress_trigger_logic_is_and_not_or() -> None:
    no_impulse = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=_ticks("BTC", [(0.0, 100.0), (30.0, 100.1)]),
        stress_confirmation_active=True,
    )
    no_stress = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=_ticks("BTC", [(0.0, 100.0), (30.0, 100.3)]),
        stress_confirmation_active=False,
    )

    assert no_impulse.triggered is False
    assert no_stress.triggered is False


def test_150bps_marker_is_metadata_only() -> None:
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=_ticks("BTC", [(0.0, 100.0), (30.0, 101.5)]),
        stress_confirmation_active=True,
    )

    assert result.triggered is True
    assert result.contains_150bps_impulse is True
    assert result.trigger_threshold_bps == 30.0
    assert result.metadata_threshold_bps == 150.0


def test_trigger_feed_state_computes_30bps_btc_move_inside_30_seconds() -> None:
    feed = watcher.StressV2TriggerFeed(start_background=False)
    feed.record_price("BTC", 100.0, ts_seconds=1000.0)
    feed.record_price("BTC", 100.3, ts_seconds=1030.0)

    ticks, reason = feed.snapshot_ticks(now_seconds=1030.0)
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=ticks,
        stress_confirmation_active=True,
    )

    assert reason is None
    assert result.triggered is True
    assert result.source_asset == "BTC"
    assert result.source_move_bps_30s == pytest.approx(30.0)


def test_trigger_feed_state_computes_30bps_eth_move_inside_30_seconds() -> None:
    feed = watcher.StressV2TriggerFeed(start_background=False)
    feed.record_price("ETH", 100.0, ts_seconds=2000.0)
    feed.record_price("ETH", 99.7, ts_seconds=2030.0)

    ticks, reason = feed.snapshot_ticks(now_seconds=2030.0)
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=ticks,
        stress_confirmation_active=True,
    )

    assert reason is None
    assert result.triggered is True
    assert result.source_asset == "ETH"
    assert result.source_move_bps_30s == pytest.approx(30.0)


def test_trigger_feed_state_does_not_trigger_below_30bps() -> None:
    feed = watcher.StressV2TriggerFeed(start_background=False)
    feed.record_price("BTC", 100.0, ts_seconds=3000.0)
    feed.record_price("BTC", 100.2999, ts_seconds=3030.0)

    ticks, reason = feed.snapshot_ticks(now_seconds=3030.0)
    result = watcher.evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=ticks,
        stress_confirmation_active=True,
    )

    assert reason is None
    assert result.triggered is False
    assert result.source_move_bps_30s == pytest.approx(29.99)


def test_trigger_feed_state_rejects_stale_observations() -> None:
    feed = watcher.StressV2TriggerFeed(start_background=False, stale_after_seconds=8.0)
    feed.record_price("BTC", 100.0, ts_seconds=4000.0)
    feed.record_price("BTC", 100.3, ts_seconds=4030.0)

    ticks, reason = feed.snapshot_ticks(now_seconds=4045.0)

    assert ticks == []
    assert reason == "STRESS_V2_TRIGGER_FEED_STALE"


def test_trigger_feed_state_handles_insufficient_history_safely() -> None:
    feed = watcher.StressV2TriggerFeed(start_background=False)
    feed.record_price("BTC", 100.0, ts_seconds=5000.0)

    ticks, reason = feed.snapshot_ticks(now_seconds=5000.0)

    assert ticks == []
    assert reason == "STRESS_V2_WAITING_FOR_TRIGGER"


def test_stress_v2_decision_requires_impulse_and_stress_confirmation() -> None:
    gate = {"market_verdict": "MARKET_ACTIVE", "accel_verdict": "ACCELERATING"}

    result = watcher._evaluate_stress_v2_capture_gate(
        gate,
        _ticks("ETH", [(0.0, 100.0), (30.0, 100.3)]),
    )

    assert result.triggered is True
    assert result.source_asset == "ETH"
    assert result.reason == "STRESS_V2_GATE_PASSED"


def test_stress_v2_decision_does_not_capture_when_impulse_true_but_stress_false() -> None:
    gate = {"market_verdict": "MARKET_ACTIVE", "accel_verdict": "NOT_ACCELERATING"}

    result = watcher._evaluate_stress_v2_capture_gate(
        gate,
        _ticks("BTC", [(0.0, 100.0), (30.0, 100.3)]),
    )

    assert result.triggered is False
    assert result.impulse_condition_met is True
    assert result.stress_confirmation_status == "INACTIVE"
    assert result.reason == "STRESS_V2_IMPULSE_TRUE_STRESS_FALSE"


def test_stress_v2_decision_does_not_capture_when_stress_true_but_impulse_false() -> None:
    gate = {"market_verdict": "MARKET_ACTIVE", "accel_verdict": "ACCELERATING"}

    result = watcher._evaluate_stress_v2_capture_gate(
        gate,
        _ticks("BTC", [(0.0, 100.0), (30.0, 100.1)]),
    )

    assert result.triggered is False
    assert result.impulse_condition_met is False
    assert result.stress_confirmation_status == "ACTIVE"
    assert result.reason == "STRESS_V2_STRESS_TRUE_IMPULSE_FALSE"


def test_stress_v2_decision_ignores_legacy_150bps_1h_gate_as_primary_gate() -> None:
    gate = {
        "gate_passed": True,
        "btc_1h_bps": 175.0,
        "market_verdict": "MARKET_ACTIVE",
        "accel_verdict": "ACCELERATING",
    }

    result = watcher._evaluate_stress_v2_capture_gate(
        gate,
        _ticks("BTC", [(0.0, 100.0), (30.0, 100.1)]),
    )

    assert result.triggered is False
    assert result.reason == "STRESS_V2_STRESS_TRUE_IMPULSE_FALSE"
    assert result.contains_150bps_impulse is False


def test_watcher_does_not_capture_when_trigger_feed_is_stale(tmp_path: Path) -> None:
    _run_single_stress_v2_cycle(
        tmp_path,
        gate={"market_verdict": "MARKET_ACTIVE", "accel_verdict": "ACCELERATING", "btc_1h_bps": 175.0},
        ticks=[],
        feed_reason="STRESS_V2_TRIGGER_FEED_STALE",
        expected_capture_status="STRESS_V2_TRIGGER_FEED_STALE",
    )


def test_watcher_does_not_capture_when_impulse_true_but_stress_false(tmp_path: Path) -> None:
    _run_single_stress_v2_cycle(
        tmp_path,
        gate={"market_verdict": "MARKET_ACTIVE", "accel_verdict": "NOT_ACCELERATING", "btc_1h_bps": 175.0},
        ticks=_ticks("BTC", [(0.0, 100.0), (30.0, 100.3)]),
        feed_reason=None,
        expected_capture_status="STRESS_V2_IMPULSE_TRUE_STRESS_FALSE",
    )


def test_watcher_does_not_capture_when_stress_true_but_impulse_false(tmp_path: Path) -> None:
    _run_single_stress_v2_cycle(
        tmp_path,
        gate={"market_verdict": "MARKET_ACTIVE", "accel_verdict": "ACCELERATING", "btc_1h_bps": 175.0},
        ticks=_ticks("ETH", [(0.0, 100.0), (30.0, 100.1)]),
        feed_reason=None,
        expected_capture_status="STRESS_V2_STRESS_TRUE_IMPULSE_FALSE",
    )


def test_watcher_reaches_capture_admission_only_when_impulse_and_stress_true(tmp_path: Path) -> None:
    status = _run_single_stress_v2_cycle(
        tmp_path,
        gate={"market_verdict": "MARKET_ACTIVE", "accel_verdict": "ACCELERATING", "btc_1h_bps": 20.0},
        ticks=_ticks("ETH", [(0.0, 100.0), (30.0, 100.3)]),
        feed_reason=None,
        expected_capture_status="STRESS_V2_GATE_PASSED",
    )

    assert status["gate_status"] == "PASSED"
    assert status["last_capture_status"] == "SKIPPED_NO_CAPTURE_MODE"


def _run_single_stress_v2_cycle(
    tmp_path: Path,
    *,
    gate: dict[str, object],
    ticks: list[dict[str, float | str]],
    feed_reason: str | None,
    expected_capture_status: str,
) -> dict[str, object]:
    status_path = tmp_path / "reports" / "stage2_gate_watcher_status.json"
    old_reports = watcher._REPORTS_ROOT
    old_data = watcher._DATA_ROOT
    old_repo = watcher._REPO_ROOT
    old_status = watcher._STATUS_PATH_OVERRIDE
    old_signal_family = watcher._SIGNAL_FAMILY
    watcher._REPORTS_ROOT = tmp_path / "reports"
    watcher._DATA_ROOT = tmp_path / "data"
    watcher._REPO_ROOT = tmp_path
    watcher._STATUS_PATH_OVERRIDE = status_path
    watcher._SIGNAL_FAMILY = "cross_asset_beta_lag_stress_v2"
    args = Namespace(poll_seconds=1, stress_bps=150.0, once=True, no_capture=True)
    log = watcher.WatcherLogger()
    try:
        with (
            patch.object(watcher, "_run_readiness", return_value={"ready": True, "hard_blockers": []}),
            patch.object(watcher, "_run_gate", return_value=gate),
            patch.object(watcher, "_fetch_stress_v2_trigger_ticks", return_value=(ticks, feed_reason)),
            patch.object(watcher, "_count_validated_full_active", return_value=0),
            patch.object(watcher, "_run_capture") as run_capture,
        ):
            watcher._run_watcher_cycle(log, args, watcher.WatcherState(min_gap_seconds=3600))
            run_capture.assert_not_called()
        status = json.loads(status_path.read_text())
    finally:
        watcher._REPORTS_ROOT = old_reports
        watcher._DATA_ROOT = old_data
        watcher._REPO_ROOT = old_repo
        watcher._STATUS_PATH_OVERRIDE = old_status
        watcher._SIGNAL_FAMILY = old_signal_family
    assert status["capture_status"] == expected_capture_status
    return status


def test_stress_v2_unwired_trigger_feed_fails_safe() -> None:
    gate = {"market_verdict": "MARKET_ACTIVE", "accel_verdict": "ACCELERATING"}

    result = watcher._evaluate_stress_v2_capture_gate(
        gate,
        [],
        trigger_feed_reason="STRESS_V2_TRIGGER_FEED_STALE",
    )

    assert result.triggered is False
    assert result.stress_confirmation_status == "ACTIVE"
    assert result.reason == "STRESS_V2_TRIGGER_FEED_STALE"


def test_cooldown_prevents_duplicate_stress_event_capture(tmp_path: Path) -> None:
    watcher._REPORTS_ROOT = tmp_path / "reports"
    watcher._DATA_ROOT = tmp_path / "data"
    watcher._REPO_ROOT = tmp_path
    try:
        state = watcher.WatcherState(min_gap_seconds=3600)
        assert state.cooldown_remaining_seconds() is None
        state.record_capture("run_1", "capture_dir")
        remaining = state.cooldown_remaining_seconds()
        assert remaining is not None
        assert remaining > 0
    finally:
        watcher._REPORTS_ROOT = None
        watcher._DATA_ROOT = None
        watcher._REPO_ROOT = None


def test_status_json_includes_worktree_provenance_and_existing_fields(tmp_path: Path) -> None:
    status_path = tmp_path / "reports" / "stage2_gate_watcher_status.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    watcher._REPORTS_ROOT = tmp_path / "reports"
    watcher._DATA_ROOT = tmp_path / "data"
    watcher._REPO_ROOT = worktree
    watcher._STATUS_PATH_OVERRIDE = status_path
    try:
        watcher._write_status(
            readiness_status="PASSED",
            gate_status="NOT_PASSED",
            capture_status="IDLE",
            last_capture_status="SKIPPED_LOW_STRESS",
            trigger_name="source_30bps_30s_and_crypto_native_stress",
            trigger_threshold_bps=30.0,
            trigger_window_seconds=30,
            stress_confirmation_status="ACTIVE",
            reason="STRESS_V2_GATE_PASSED",
        )
        data = json.loads(status_path.read_text())
    finally:
        watcher._REPORTS_ROOT = None
        watcher._DATA_ROOT = None
        watcher._REPO_ROOT = None
        watcher._STATUS_PATH_OVERRIDE = None

    for field in [
        "updated_utc",
        "git_sha",
        "git_worktree_root",
        "git_dirty",
        "signal_family",
        "safety",
        "readiness_status",
        "gate_status",
        "capture_status",
        "last_capture_status",
        "trigger_name",
        "trigger_threshold_bps",
        "trigger_window_seconds",
        "trigger_price_source",
        "trigger_poll_seconds",
        "trigger_stale_after_seconds",
        "stress_confirmation_status",
        "reason",
    ]:
        assert field in data
    assert data["git_worktree_root"] == str(worktree.resolve())


def test_service_env_rendering_points_working_directory_at_stress_worktree() -> None:
    service_path = (
        Path(__file__).resolve().parents[1]
        / "systemd"
        / "nautilus-stage2-gate-watcher.service"
    )
    content = service_path.read_text()

    assert "EnvironmentFile=-%h/.config/nautilus/stage2-gate-watcher.env" in content
    assert "WorkingDirectory=/mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress" in content
    assert "WorkingDirectory=${NAUTILUS_STAGE2_WORKTREE}" not in content
    assert "NAUTILUS_STAGE2_SIGNAL_FAMILY=cross_asset_beta_lag_stress_v2" in content
    assert "NAUTILUS_STAGE2_WORKTREE=/mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress" in content


def test_precommitment_json_exists_and_locks_required_values() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "precommitments"
        / "cross_asset_beta_lag_stress_v2.json"
    )
    data = json.loads(path.read_text())

    assert data["signal_family"] == "cross_asset_beta_lag_stress_v2"
    assert data["event_gate"]["minimum_source_move_bps"] == 30.0
    assert data["event_gate"]["source_move_window_seconds"] == 30
    assert data["event_gate"]["trigger_logic"] == "source_impulse_AND_stress_confirmation"
    assert data["event_gate"]["metadata_only_thresholds_bps"] == [150.0]
    assert data["hypothesis_config"]["target_assets"] == ["SOL", "LINK", "DOGE", "AVAX", "ADA"]
    assert data["hypothesis_config"]["primary_horizons_ms"] == [60000, 120000, 300000]
    assert data["cost_and_fill"]["live_orders_allowed"] is False
    assert data["diagnostics"]["watcher_may_decide_candidate_or_rejected_verdict"] is False


def test_no_live_order_secret_imports_in_new_trigger_code() -> None:
    content = Path(watcher.__file__).read_text()
    forbidden = [
        "OrderFactory",
        "submit" + "_order",
        "private" + "_key",
        "wallet",
        "nautilus_trader.execution",
        "PolymarketExecution",
    ]
    for token in forbidden:
        assert token not in content
