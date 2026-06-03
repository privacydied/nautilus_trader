from __future__ import annotations

import csv
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer import hyperliquid_funding_divergence_phase0 as h
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_divergence_phase0 import main


def _ts(hour: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=hour)).isoformat().replace("+00:00", "Z")


def _write_funding(path: Path, venue: str, asset: str, hours: list[int], rates: list[float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["venue", "asset", "symbol", "timestamp", "funding_rate"])
        for hour, rate in zip(hours, rates, strict=True):
            w.writerow([venue, asset, f"{asset}-PERP", _ts(hour), rate])


def test_funding_normalization_and_csv_units() -> None:
    row = h.normalize_funding_row(
        venue="hyperliquid",
        asset="BTC",
        native_symbol="BTC-PERP",
        timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
        native_funding_rate=0.0003,
        native_interval_hours=1.0,
        source_file_or_endpoint="fixture.csv",
    )
    assert row.hourly_funding_bps == pytest.approx(3.0)
    assert row.projected_8h_funding_bps == pytest.approx(24.0)

    row8 = h.normalize_funding_row(
        venue="binance",
        asset="BTC",
        native_symbol="BTCUSDT",
        timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
        native_funding_rate=0.0008,
        native_interval_hours=8.0,
        source_file_or_endpoint="fixture.csv",
    )
    assert row8.hourly_funding_bps == pytest.approx(1.0)
    assert row8.projected_8h_funding_bps == pytest.approx(8.0)

    for col in h.DIVERGENCE_DISTRIBUTION_COLUMNS + h.BUCKET_COUNT_COLUMNS + h.PERSISTENCE_COLUMNS + h.CALENDAR_COLUMNS:
        if ("funding_bps" in col) or ("divergence_bps" in col):
            assert col.endswith("_bps_hourly") or col.endswith("_bps_projected_8h") or "threshold_bps_hourly" in col
            assert col not in {"funding", "funding_bps", "divergence", "rate"}


def test_past_only_alignment_and_missing_prior() -> None:
    refs = [
        h.NormalizedFundingRow("binance", "BTC", "BTCUSDT", datetime(2026, 1, 1, 0, tzinfo=UTC), 0.0005, 8.0, 0.625, 5.0, "r", "ok"),
        h.NormalizedFundingRow("binance", "BTC", "BTCUSDT", datetime(2026, 1, 1, 8, tzinfo=UTC), 0.004, 8.0, 5.0, 40.0, "r", "ok"),
    ]
    early = h.NormalizedFundingRow("hyperliquid", "BTC", "BTC-PERP", datetime(2025, 12, 31, 23, tzinfo=UTC), 0.001, 1.0, 10.0, 80.0, "h", "ok")
    aligned, missing = h.align_last_observed_reference([early], refs, "binance")
    assert aligned == []
    assert missing == 1

    hl = h.NormalizedFundingRow("hyperliquid", "BTC", "BTC-PERP", datetime(2026, 1, 1, 7, tzinfo=UTC), 0.003, 1.0, 30.0, 240.0, "h", "ok")
    aligned, missing = h.align_last_observed_reference([hl], refs, "binance")
    assert missing == 0
    assert aligned[0].reference_timestamp_utc == refs[0].timestamp_utc
    assert aligned[0].alignment_lag_seconds == 7 * 3600


def test_adversarial_no_lookahead_alignment_exact_sign_and_magnitude() -> None:
    refs = [
        h.NormalizedFundingRow("binance", "BTC", "BTCUSDT", datetime(2026, 1, 1, 0, tzinfo=UTC), 0.004, 8.0, 5.0, 40.0, "past", "ok"),
        h.NormalizedFundingRow("binance", "BTC", "BTCUSDT", datetime(2026, 1, 1, 8, tzinfo=UTC), 0.04, 8.0, 50.0, 400.0, "future", "ok"),
    ]
    hl = h.NormalizedFundingRow("hyperliquid", "BTC", "BTC-PERP", datetime(2026, 1, 1, 4, tzinfo=UTC), 0.003, 1.0, 30.0, 240.0, "h", "ok")
    aligned, _ = h.align_last_observed_reference([hl], refs, "binance")
    assert aligned[0].divergence_bps_hourly == pytest.approx(25.0)
    assert aligned[0].divergence_bps_hourly != pytest.approx(-20.0)


def test_kill_criteria_statuses_and_bound_defaults() -> None:
    assert h.decide_phase0_status({"BTC": {"count": 100, "max": 9.9, "p99": 9.0}, "ETH": {"count": 100, "max": 9.9, "p99": 9.0}}) == h.STATUS_KILLED
    assert h.decide_phase0_status({"BTC": {"count": 100, "max": 12.0, "p99": 7.9}, "ETH": {"count": 100, "max": 12.0, "p99": 7.9}}) == h.STATUS_KILLED
    assert h.decide_phase0_status({"BTC": {"count": 100, "max": 12.0, "p99": 9.0}, "ETH": {"count": 100, "max": 9.0, "p99": 7.0}}) == h.STATUS_SINGLE_ASSET
    assert h.decide_phase0_status({"BTC": {"count": 100, "max": 12.0, "p99": 9.0}, "ETH": {"count": 100, "max": 12.0, "p99": 9.0}}) == h.STATUS_READY
    assert h.decide_phase0_status({"BTC": {"count": 99, "max": 12.0, "p99": 9.0}, "ETH": {"count": 100, "max": 12.0, "p99": 9.0}}) == h.STATUS_NEEDS_MORE_DATA
    sig = inspect.signature(h.decide_phase0_status)
    assert sig.parameters["max_abs_threshold_bps"].default == h.KILL_CRITERION_MAX_ABS_BPS
    assert sig.parameters["p99_abs_threshold_bps"].default == h.KILL_CRITERION_P99_ABS_BPS


def test_runner_pre_data_integrity_and_provenance(tmp_path: Path) -> None:
    hl = tmp_path / "hl_btc.csv"
    bn = tmp_path / "bn_btc.csv"
    hl_eth = tmp_path / "hl_eth.csv"
    bn_eth = tmp_path / "bn_eth.csv"
    _write_funding(hl, "hyperliquid", "BTC", list(range(24)), [0.0001] * 24)
    _write_funding(hl_eth, "hyperliquid", "ETH", list(range(24)), [0.0001] * 24)
    _write_funding(bn, "binance", "BTC", [0, 8, 16], [0.0008] * 3)
    _write_funding(bn_eth, "binance", "ETH", [0, 8, 16], [0.0008] * 3)
    out = tmp_path / "reports"
    rc = main(["--output-root", str(out), "--hyperliquid-btc", str(hl), "--hyperliquid-eth", str(hl_eth), "--binance-btc", str(bn), "--binance-eth", str(bn_eth)])
    assert rc == 0
    run_dir = next(out.iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    crit = manifest["pre_data_kill_criteria"]
    assert crit["recorded_before_data_load"] is True
    assert crit["constant_names"] == ["KILL_CRITERION_MAX_ABS_BPS", "KILL_CRITERION_P99_ABS_BPS"]
    prov = manifest["data_source_provenance"]
    assert prov
    first = prov[0]
    assert first["source_first_row_timestamp_utc"]
    assert first["source_last_row_timestamp_utc"]
    assert first["source_row_count"] > 0
    assert first["source_sha256"]
    assert first["source_timestamp_unit_detected"] == "iso8601"
    assert first["source_native_funding_interval_hours"] in (1.0, 8.0)


def test_load_hyperliquid_archive_jsonl_for_phase0(tmp_path: Path) -> None:
    path = tmp_path / "hl_btc.jsonl"
    rows = [
        {"coin": "BTC", "timestamp_ms": 1704067200000, "funding_rate": 0.0001},
        {"coin": "BTC", "timestamp_ms": 1704070800000, "funding_rate": 0.0002},
        {"coin": "BTC", "timestamp_ms": 1704074400000, "funding_rate": -0.0001},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    loaded, provenance, unusable = h.load_funding_file(path, "hyperliquid", "BTC")

    assert unusable is None
    assert len(loaded) == 3
    assert loaded[0].timestamp_utc == datetime(2024, 1, 1, tzinfo=UTC)
    assert loaded[0].hourly_funding_bps == pytest.approx(1.0)
    assert loaded[1].projected_8h_funding_bps == pytest.approx(16.0)
    assert provenance["source_format"] == "jsonl"
    assert provenance["source_timestamp_unit_detected"] == "ms"
    assert provenance["source_native_funding_interval_hours"] == pytest.approx(1.0)


def test_interval_ambiguity_hard_fail(tmp_path: Path) -> None:
    hl = tmp_path / "hl_btc_bad.csv"
    _write_funding(hl, "hyperliquid", "BTC", [0, 8, 16], [0.0001, 0.0001, 0.0001])
    out = tmp_path / "reports"
    rc = main(["--output-root", str(out), "--hyperliquid-btc", str(hl)])
    assert rc == 0
    manifest = json.loads((next(out.iterdir()) / "manifest.json").read_text())
    assert manifest["final_phase0_status"] == h.STATUS_DATA_UNUSABLE
    assert manifest["interval_ambiguity_detected"] is True
    assert manifest["expected_interval_hours"] == 1.0
    assert manifest["detected_interval_hours"] == 8.0
    assert manifest["offending_source"]
    assert (next(out.iterdir()) / "divergence_distribution.csv").read_text().count("\n") == 1


def test_persistence_half_life_censoring_and_suppression() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        h.AlignedDivergenceRow("BTC", "binance", base + timedelta(hours=i), base, 0, 0, 0, val, abs(val), 0, 0)
        for i, val in enumerate([10.0, 8.0, 4.9, 12.0, 11.0, 7.0])
    ]
    out = h.compute_persistence_half_life(rows)
    b10 = [r for r in out if r["threshold_bps_hourly"] == 10][0]
    assert b10["n_observations"] == 3
    assert b10["n_censored"] == 2
    assert b10["p95_half_life_hours"] == h.INSUFFICIENT_SAMPLES_FOR_PERCENTILE
    assert "n_observations" in b10


def test_calendar_descriptive_only_and_suppression() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [h.AlignedDivergenceRow("BTC", "binance", base, base, 0, 0, 0, 1.0, 1.0, 0, 0)]
    out = h.compute_calendar_stratification(rows)
    assert out
    assert all(r["percentile_status"] == h.INSUFFICIENT_SAMPLES for r in out)
    assert not any("CANDIDATE" in json.dumps(r) or "evaluation_cell" in json.dumps(r) for r in out)


def test_safety_forbidden_statuses_and_strings() -> None:
    forbidden_statuses = {
        "CANDIDATE", "REJECTED", "TRADE_READY", "EXECUTION_READY", "SHADOW_READY", "BOT_READY", "PHASE0_PROMOTED", "READY_FOR_V1", "PRECOMMITMENT_READY",
    }
    assert forbidden_statuses.isdisjoint(h.ALLOWED_STATUSES)
    source = Path(h.__file__).read_text()
    forbidden_runtime_terms = ["submit_order", "TradingNode", "ExecutionClient", "BotGate"]
    for term in forbidden_runtime_terms:
        assert term not in source


def test_cli_smoke_writes_required_outputs_and_safety_manifest(tmp_path: Path) -> None:
    out = tmp_path / "reports"
    rc = main(["--output-root", str(out)])
    assert rc == 0
    run_dir = next(out.iterdir())
    for name in ["summary.json", "alignment_summary.csv", "divergence_distribution.csv", "divergence_bucket_counts.csv", "persistence_half_life.csv", "calendar_stratification.csv", "PHASE0_REPORT.md", "manifest.json"]:
        assert (run_dir / name).exists()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["no_forward_returns_used"] is True
    assert manifest["no_pnl_used"] is True
    assert manifest["phase0_not_precommitment"] is True
    assert "pre_data_kill_criteria" in manifest
    assert manifest["final_phase0_status"] == h.STATUS_SOURCE_UNAVAILABLE
