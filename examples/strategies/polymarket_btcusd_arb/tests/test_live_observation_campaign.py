"""Tests for Phase 2B campaign runner — mocked, no live network."""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _campaign_module():
    from examples.strategies.polymarket_btcusd_arb.live_observation_campaign import (
        build_campaign_summary, write_campaign_report
    )
    return build_campaign_summary, write_campaign_report


def test_campaign_dry_run_discovers_markets():
    """Dry-run discovers markets and exits cleanly."""
    from examples.strategies.polymarket_btcusd_arb.live_observation_campaign import main
    with patch("examples.strategies.polymarket_btcusd_arb.live_observation_campaign.discover_markets") as mock_disc:
        from examples.strategies.polymarket_btcusd_arb.live_market_discovery import UpDownMarketInfo
        mock_disc.return_value = [
            UpDownMarketInfo(slug="btc-updown-15m-test", question="Test?", active=True, closed=False,
                             condition_id="cond1", yes_token_id="tok1", no_token_id="tok2",
                             start_ns=1000, end_ns=2000, series_slug="btc", resolution_source="gamma",
                             price_to_beat=100000.0, price_to_beat_source="slug_epoch")
        ]
        with patch("sys.argv", ["prog", "--dry-discover"]):
            rc = main()
        assert rc == 0
        mock_disc.assert_called_once()


def test_campaign_handles_no_active_markets():
    """Campaign exits with error when no active markets."""
    from examples.strategies.polymarket_btcusd_arb.live_observation_campaign import main
    with patch("examples.strategies.polymarket_btcusd_arb.live_observation_campaign.discover_markets") as mock_disc:
        mock_disc.return_value = []
        with patch("sys.argv", ["prog", "--windows", "1"]):
            rc = main()
        assert rc == 1


def test_campaign_records_completed_window():
    """Completed window is counted correctly in campaign summary."""
    build_campaign_summary, _ = _campaign_module()
    windows = [
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 3280,
         "grid_rejection_counts": {"spread_too_wide": 3060, "stale_or_missing_binance": 220},
         "rejection_counts": {"spread_too_wide": 3060, "stale_or_missing_binance": 220},
         "replay_candidate_match": True, "replay_grid_rejection_match": True}
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["btc-updown-15m-test"],
        start_time=1000, end_time=2000, params={"requested_windows": 1, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert summary["completed_windows"] == 1
    assert summary["failed_windows"] == 0
    assert summary["total_candidates"] == 0
    assert summary["total_grid_rejections"] == 3280


def test_campaign_records_failed_window():
    """Failed window is counted in campaign summary."""
    build_campaign_summary, _ = _campaign_module()
    windows = [
        {"completed": False, "candidate_count": 0, "grid_rejection_count": 0,
         "grid_rejection_counts": {}, "rejection_counts": {},
         "replay_candidate_match": False, "replay_grid_rejection_match": False}
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["none"],
        start_time=1000, end_time=2000, params={"requested_windows": 1, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert summary["completed_windows"] == 0
    assert summary["failed_windows"] == 1
    assert summary["verdict"] == "NEEDS_MORE_DATA"


def test_campaign_requires_replay_candidate_match():
    """Campaign summary requires replay candidate match for all_replay_checks_passed."""
    build_campaign_summary, _ = _campaign_module()
    windows = [
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 100,
         "grid_rejection_counts": {"spread_too_wide": 100}, "rejection_counts": {"spread_too_wide": 100},
         "replay_candidate_match": False, "replay_grid_rejection_match": True}
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["test"],
        start_time=1000, end_time=2000, params={"requested_windows": 1, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert summary["all_replay_checks_passed"] is False


def test_campaign_requires_replay_grid_rejection_match():
    """Campaign summary requires grid rejection match for all_replay_checks_passed."""
    build_campaign_summary, _ = _campaign_module()
    windows = [
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 100,
         "grid_rejection_counts": {"spread_too_wide": 100}, "rejection_counts": {"spread_too_wide": 100},
         "replay_candidate_match": True, "replay_grid_rejection_match": False}
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["test"],
        start_time=1000, end_time=2000, params={"requested_windows": 1, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert summary["all_replay_checks_passed"] is False


def test_campaign_aggregates_rejection_counts():
    """Rejection counts aggregate across windows."""
    build_campaign_summary, _ = _campaign_module()
    windows = [
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 100,
         "grid_rejection_counts": {"spread_too_wide": 80, "stale_or_missing_binance": 20},
         "rejection_counts": {"spread_too_wide": 80, "stale_or_missing_binance": 20},
         "replay_candidate_match": True, "replay_grid_rejection_match": True},
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 200,
         "grid_rejection_counts": {"spread_too_wide": 180, "stale_or_missing_binance": 20},
         "rejection_counts": {"spread_too_wide": 180, "stale_or_missing_binance": 20},
         "replay_candidate_match": True, "replay_grid_rejection_match": True},
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["m1", "m2"],
        start_time=1000, end_time=2000, params={"requested_windows": 2, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert summary["total_grid_rejections"] == 300
    assert summary["rejection_counts_by_reason"]["spread_too_wide"] == 260
    assert summary["rejection_counts_by_reason"]["stale_or_missing_binance"] == 40
    assert summary["spread_too_wide_rate"] > 0.8


def test_campaign_report_contains_safety_statement():
    """Campaign report markdown contains safety statements."""
    _, write_report = _campaign_module()
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        summary = {
            "campaign_id": "test", "branch": "polymarket-btcusd-arb-phase2b-observer-campaign",
            "start_time_utc": "2026-01-01T00:00:00Z", "end_time_utc": "2026-01-01T00:15:00Z",
            "requested_windows": 2, "completed_windows": 2, "failed_windows": 0,
            "duration_seconds_per_window": 900, "thresholds": "5,10,20,40", "lookbacks": "default",
            "markets_observed": ["btc-test"], "total_candidates": 0, "total_grid_rejections": 100,
            "rejection_counts_by_reason": {"spread_too_wide": 100},
            "spread_too_wide_rate": 1.0, "stale_or_missing_binance_rate": 0.0,
            "windows_with_candidates": 0, "windows_with_zero_candidates": 2,
            "all_replay_checks_passed": True, "safety_check_status": {"ok": True, "violations": []},
            "verdict": "NEEDS_MORE_DATA", "verdict_reason": "test",
            "recommendation": "Do not recommend Phase 3.",
            "limitations": ["Observer-only.", "Limited sample."],
        }
        windows = [
            {"window_idx": 0, "market_slug": "btc-test", "completed": True,
             "evaluated_event_count": 100, "candidate_count": 0, "grid_rejection_count": 100,
             "spread_too_wide": 100, "stale_or_missing_binance": 0,
             "replay_candidate_match": True, "replay_grid_rejection_match": True,
             "accounting_contract": "grid_level"},
        ]
        write_report(rd, summary, windows)
        report_md = (rd/"campaign_report.md").read_text()
        assert "observer-only" in report_md.lower() or "No execution" in report_md
        assert "No orders" in report_md or "NO_KEYS" in report_md or "observer-only" in report_md.lower()


def test_campaign_does_not_recommend_execution():
    """Campaign verdict never recommends execution."""
    build_campaign_summary, _ = _campaign_module()
    # Even with candidates, recommendation should not say execution
    windows = [
        {"completed": True, "candidate_count": 5, "grid_rejection_count": 100,
         "grid_rejection_counts": {"spread_too_wide": 100}, "rejection_counts": {"spread_too_wide": 100},
         "replay_candidate_match": True, "replay_grid_rejection_match": True}
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["test"],
        start_time=1000, end_time=2000, params={"requested_windows": 1, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert "execution" not in summary["recommendation"].lower() or "do not" in summary["recommendation"].lower()


def test_parity_status_wording_is_explicit():
    """Verify STATUS.md clearly states parity fixture provenance."""
    from pathlib import Path
    status = Path("examples/strategies/polymarket_btcusd_arb/STATUS.md").read_text()
    # Must mention Rust-generated
    assert "Rust" in status or "rust" in status
    # Must mention fixture provenance
    assert "parity" in status.lower() or "fixture" in status.lower()


def test_rejected_for_current_live_conditions_verdict():
    """Three+ completed zero-candidate windows produces REJECTED_FOR_CURRENT_LIVE_CONDITIONS."""
    build_campaign_summary, _ = _campaign_module()
    windows = [
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 100,
         "grid_rejection_counts": {"spread_too_wide": 100}, "rejection_counts": {"spread_too_wide": 100},
         "replay_candidate_match": True, "replay_grid_rejection_match": True},
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 200,
         "grid_rejection_counts": {"spread_too_wide": 200}, "rejection_counts": {"spread_too_wide": 200},
         "replay_candidate_match": True, "replay_grid_rejection_match": True},
        {"completed": True, "candidate_count": 0, "grid_rejection_count": 150,
         "grid_rejection_counts": {"spread_too_wide": 150}, "rejection_counts": {"spread_too_wide": 150},
         "replay_candidate_match": True, "replay_grid_rejection_match": True},
    ]
    summary = build_campaign_summary(
        campaign_id="test-camp", windows=windows, markets_observed=["m1","m2","m3"],
        start_time=1000, end_time=2000, params={"requested_windows": 3, "duration_seconds": 900},
        safety={"ok": True, "violations": []}
    )
    assert summary["verdict"] == "REJECTED_FOR_CURRENT_LIVE_CONDITIONS"