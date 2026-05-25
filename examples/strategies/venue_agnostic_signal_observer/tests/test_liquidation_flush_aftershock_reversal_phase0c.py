from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import ArchiveRow
from examples.strategies.venue_agnostic_signal_observer import liquidation_flush_aftershock_reversal_venue_age_aware_phase0c as phase0c

REPO_ROOT = Path(__file__).resolve().parents[4]
PHASE0C_PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0C_NULL_PRECOMMITMENT.md"


def archive_row(symbol: str, ts: datetime, price: float, oi: float = 1000.0) -> ArchiveRow:
    return ArchiveRow(ts, symbol, price, oi, "fixture", 0)


def event(symbol: str = "ALT", ts: datetime | None = None, direction: str = "downside_liquidation_flush", price: float = 100.0) -> dict:
    ts = ts or datetime(2024, 1, 15, 12, tzinfo=UTC)
    return {
        "event_id": f"{symbol}_{ts:%Y%m%dT%H%M%SZ}",
        "symbol": symbol,
        "event_timestamp_utc": ts.isoformat().replace("+00:00", "Z"),
        "event_direction": direction,
        "flush_side": "long_wipe" if direction == "downside_liquidation_flush" else "short_squeeze",
        "price_t": price,
        "oi_t": 1000.0,
        "price_return_8h_pct": -9.0 if direction == "downside_liquidation_flush" else 9.0,
        "oi_change_8h_pct": -10.0,
        "cooldown_group_index": 1,
    }


def test_precommitment_hash_non_empty_and_matches_doc():
    recorded, computed = phase0c.precommitment_recorded_and_computed(PHASE0C_PRECOMMITMENT)
    assert recorded
    assert computed
    assert recorded == computed


def test_source_artifact_hash_mismatch_hard_fails(tmp_path: Path):
    phase0a = tmp_path / "phase0a"
    phase0a.mkdir()
    artifact = phase0a / "accepted_events.jsonl"
    artifact.write_text(json.dumps(event()) + "\n")
    (phase0a / "summary.json").write_text(json.dumps({
        "status": "PHASE0A_EVENT_POPULATION_READY",
        "accepted_event_count_after_cooldown": 1,
        "accepted_events_jsonl_path": "accepted_events.jsonl",
        "accepted_events_jsonl_sha256": "bad",
    }))
    with pytest.raises(phase0c.SourceArtifactMismatch):
        phase0c.verify_phase0a_source(phase0a, expected_event_hash="expected")


def test_btc_eth_events_are_rejected():
    with pytest.raises(phase0c.SourceArtifactMismatch):
        phase0c.validate_event_universe([event("BTC"), event("ETH")])


def test_matched_placebo_preserves_symbol_and_month():
    base = datetime(2024, 1, 15, 12, tzinfo=UTC)
    candidates = phase0c.build_symbol_month_candidates([
        archive_row("ALT", base - timedelta(days=5), 90),
        archive_row("ALT", base + timedelta(days=5), 95),
        archive_row("ALT", datetime(2024, 2, 1, tzinfo=UTC), 99),
    ])
    sampled = phase0c.sample_matched_placebo_events([event("ALT", base)], candidates, seed=7, horizon_hours=24)
    assert sampled[0]["symbol"] == "ALT"
    assert sampled[0]["event_timestamp_utc"].startswith("2024-01")
    assert sampled[0]["event_direction"] == "downside_liquidation_flush"


def test_matched_placebo_does_not_use_future_data_for_selecting_events():
    base = datetime(2024, 1, 15, 12, tzinfo=UTC)
    candidates = phase0c.build_symbol_month_candidates([
        archive_row("ALT", base, 100),
        archive_row("ALT", base + timedelta(hours=23), 101),
        archive_row("ALT", base + timedelta(hours=25), 102),
    ])
    sampled = phase0c.sample_matched_placebo_events([event("ALT", base)], candidates, seed=1, horizon_hours=24)
    assert phase0c.parse_ts(sampled[0]["event_timestamp_utc"]) <= base + timedelta(hours=1)


def test_direction_shuffle_flips_direction_deterministically_with_seed():
    events = [event("ALT", datetime(2024, 1, 1, tzinfo=UTC)), event("ALT", datetime(2024, 1, 2, tzinfo=UTC))]
    a = phase0c.shuffle_event_directions(events, seed=20260526)
    b = phase0c.shuffle_event_directions(events, seed=20260526)
    assert a == b
    assert {e["event_direction"] for e in a} <= {"downside_liquidation_flush", "upside_liquidation_flush"}


def test_empirical_p_value_calculation():
    assert phase0c.empirical_p_value(real=10.0, null_values=[1.0, 2.0, 11.0], higher_is_better=True) == pytest.approx(0.5)


def test_null_coverage_below_threshold_hard_fails():
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(100.0, 50.0, 0.6, 100),
        primary=phase0c.NullSummary(10, 0.7, 20.0, 20.0, 90.0, 95.0, 0.01, 80.0),
        month=phase0c.NullSummary(10, 0.7, 20.0, 20.0, 90.0, 95.0, 0.01, 80.0),
        circular_shift=phase0c.NullSummary(10, 0.7, 20.0, 20.0, 90.0, 95.0, 0.01, 80.0),
        direction_shuffle_p_value=0.01,
        all_events_same_direction=True,
    )
    assert verdict == phase0c.STATUS_INSUFFICIENT_NULL_COVERAGE


def test_real_return_above_placebo_95th_percentile_passes():
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(120.0, 90.0, 0.57, 950),
        primary=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.02, 100.0, win_rate_p95=0.55, median_distribution_median=20.0),
        month=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.02, 100.0, win_rate_p95=0.55, median_distribution_median=20.0),
        circular_shift=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.02, 100.0, win_rate_p95=0.55, median_distribution_median=20.0),
        direction_shuffle_p_value=0.01,
        all_events_same_direction=True,
    )
    assert verdict == phase0c.STATUS_NULL_VALIDATED_PASS


def test_real_return_below_placebo_95th_percentile_fails():
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(70.0, 50.0, 0.57, 950),
        primary=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.10, -10.0, win_rate_p95=0.55, median_distribution_median=20.0),
        month=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.10, -10.0, win_rate_p95=0.55, median_distribution_median=20.0),
        circular_shift=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.10, -10.0, win_rate_p95=0.55, median_distribution_median=20.0),
        direction_shuffle_p_value=0.01,
        all_events_same_direction=True,
    )
    assert verdict == phase0c.STATUS_NULL_REJECTED_PLACEBO_MATCH


def test_summary_contains_expected_null_fields():
    summary = phase0c.build_summary(
        status=phase0c.STATUS_NULL_VALIDATED_PASS,
        precommitment_sha256="abc",
        phase0a_report_path="p0a",
        phase0b_report_path="p0b",
        phase0a_artifact_hash_verified=True,
        phase0b_report_verified=True,
        real=phase0c.RealPrimaryMetrics(120.0, 90.0, 0.57, 951),
        primary=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.02, 100.0),
        month=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.02, 100.0),
        circular_shift=phase0c.NullSummary(1000, 0.9, 20.0, 20.0, 80.0, 95.0, 0.02, 100.0),
        direction_shuffle_p_value=0.01,
        all_events_same_direction=True,
        survivorship_audit={},
    )
    for key in ["primary_placebo_iterations", "primary_empirical_p_value", "real_24h_net_mean_bps", "direction_shuffle_p_value"]:
        assert key in summary


def test_no_prohibited_execution_constants_in_phase0c_module():
    text = Path(phase0c.__file__).read_text()
    prohibited = ["submit_order", "place_order", "api_key", "private_key", "paper_trading", "shadow_execution", "systemd", "bot path"]
    hits = [line for line in text.splitlines() for term in prohibited if term in line and "No " not in line]
    assert hits == []
