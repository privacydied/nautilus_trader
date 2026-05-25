from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import ArchiveRow, EventRecord, LoadDiagnostics
from examples.strategies.venue_agnostic_signal_observer import liquidation_flush_aftershock_reversal_venue_age_aware_phase0a as phase0a
from examples.strategies.venue_agnostic_signal_observer import liquidation_flush_aftershock_reversal_venue_age_aware_phase0b as phase0b

REPO_ROOT = Path(__file__).resolve().parents[4]
PHASE0A_PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0A_PRECOMMITMENT.md"
PHASE0B_PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0B_PRECOMMITMENT.md"


def row(symbol: str, ts: datetime, price: float, oi: float, order: int = 0) -> ArchiveRow:
    return ArchiveRow(ts, symbol, price, oi, "fixture", order)


def event(symbol: str = "ALT", ts: datetime | None = None, price: float = 100.0, oi: float = 1000.0) -> EventRecord:
    ts = ts or datetime(2024, 1, 1, tzinfo=UTC)
    return EventRecord(
        event_id=f"{symbol}_{ts:%Y%m%dT%H%M%SZ}",
        symbol=symbol,
        event_timestamp_utc=ts.isoformat().replace("+00:00", "Z"),
        price_t=price,
        price_t_minus_8h=110.0,
        oi_t=oi,
        oi_t_minus_8h=1200.0,
        price_return_8h_pct=-9.0,
        oi_change_8h_pct=-10.0,
        cooldown_group_index=1,
        calendar_year=ts.year,
    )


def test_phase0a_jsonl_event_artifact_is_deterministic(tmp_path: Path):
    events = [event("ZED", datetime(2024, 1, 2, tzinfo=UTC)), event("ALT", datetime(2024, 1, 1, tzinfo=UTC))]
    result = phase0a.Phase0AResult(
        summary={
            "status": "fixture", "unlocks_phase0b": False, "phase0b_locked_reason": "fixture",
            "precommitment_sha256": "fixture", "archive_source_path": "fixture",
            "accepted_event_count_after_cooldown": 2, "max_symbol_event_share": 0.0,
            "max_symbol_event_share_symbol": "", "max_calendar_quarter_event_share": 0.0,
            "max_calendar_quarter": None, "max_calendar_month_event_share": 0.0,
            "max_calendar_month": None, "quarter_distribution": {}, "month_distribution": {},
        },
        accepted_events=events,
        symbol_coverage=[],
        rejected_symbols=[],
        year_distribution=[],
        threshold_diagnostics=[],
        warnings=[],
    )
    out = phase0a.write_report_artifacts(result, tmp_path)
    artifact = tmp_path / "accepted_events.jsonl"
    assert artifact.exists()
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    assert [r["symbol"] for r in rows] == ["ALT", "ZED"]
    assert rows[0]["event_direction"] == "downside_liquidation_flush"
    assert out.summary["accepted_events_jsonl_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_precommitment_hash_non_empty_and_matches_doc():
    recorded, computed = phase0b.precommitment_recorded_and_computed(PHASE0B_PRECOMMITMENT)
    assert recorded
    assert computed
    assert recorded == computed


def test_phase0a_event_artifact_count_must_match_summary(tmp_path: Path):
    artifact = tmp_path / "accepted_events.jsonl"
    artifact.write_text(json.dumps(phase0b.phase0a_event_to_json(event())) + "\n")
    summary = {"accepted_event_count_after_cooldown": 2, "accepted_events_jsonl_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    with pytest.raises(phase0b.EventReproductionMismatch):
        phase0b.load_phase0a_event_artifact(artifact, summary)


def test_btc_eth_events_are_rejected():
    for symbol in ["BTC", "ETH"]:
        with pytest.raises(phase0b.EventReproductionMismatch):
            phase0b.validate_event_universe([phase0b.phase0a_event_to_json(event(symbol))])


def test_reproduction_mismatch_hard_fails(tmp_path: Path):
    summary = {"accepted_event_count_after_cooldown": 1, "accepted_events_jsonl_sha256": "bad"}
    artifact = tmp_path / "accepted_events.jsonl"
    artifact.write_text(json.dumps(phase0b.phase0a_event_to_json(event())) + "\n")
    with pytest.raises(phase0b.EventReproductionMismatch):
        phase0b.load_phase0a_event_artifact(artifact, summary)


def test_long_wipe_reversal_return_sign():
    assert phase0b.compute_directional_return_bps("downside_liquidation_flush", 100.0, 102.0) == pytest.approx(200.0)


def test_short_squeeze_reversal_return_sign():
    assert phase0b.compute_directional_return_bps("upside_liquidation_flush", 100.0, 98.0) == pytest.approx(204.08163265306123)


def test_future_horizon_lookup_and_missing_future_data():
    base = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [row("ALT", base, 100, 1000), row("ALT", base + timedelta(hours=6), 106, 1000)]
    series = phase0b.build_price_series(rows)
    assert phase0b.lookup_future_price(series["ALT"], base + timedelta(hours=6)) == 106
    assert phase0b.lookup_future_price(series["ALT"], base + timedelta(hours=12)) is None


def test_50_bps_cost_subtraction():
    assert phase0b.net_bps_after_cost(75.0, 50.0) == 25.0


def test_verdict_classification_pass_and_failures():
    passing = phase0b.HorizonMetrics(24, 250, 80.0, 60.0, 30.0, 10.0, 0.55, 5.0, 0)
    assert phase0b.classify_primary_verdict(passing, null_run=False) == phase0b.STATUS_RETURN_DIAGNOSTIC_NO_NULL
    assert phase0b.classify_primary_verdict(phase0b.HorizonMetrics(24, 250, 40.0, 60.0, -10.0, 10.0, 0.55, 5.0, 0), False) == phase0b.STATUS_REJECTED_COST_WALL
    assert phase0b.classify_primary_verdict(phase0b.HorizonMetrics(24, 250, 80.0, 40.0, 30.0, -10.0, 0.55, 5.0, 0), False) == phase0b.STATUS_REJECTED_NO_REVERSAL


def test_summary_distribution_consistency():
    summary = phase0b.build_summary_distribution([phase0b.phase0a_event_to_json(event("ALT", datetime(2024, 1, 1, tzinfo=UTC)))])
    assert summary["event_month_distribution"] == {"2024-01": 1}
    assert sum(summary["event_month_distribution"].values()) == 1


def test_no_prohibited_execution_constants_in_phase0b_module():
    text = Path(phase0b.__file__).read_text()
    prohibited = ["submit_order", "place_order", "api_key", "private_key", "paper_trading", "shadow_execution", "systemd", "bot path"]
    hits = [line for line in text.splitlines() for term in prohibited if term in line and "No " not in line]
    assert hits == []
