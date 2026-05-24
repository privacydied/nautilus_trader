from __future__ import annotations

import ast
import csv
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer import hyperliquid_supertrend_4h1d_phase0 as h
from examples.strategies.venue_agnostic_signal_observer import hyperliquid_supertrend_archive_ingest as ingest


def dt(day: int, hour: int = 0) -> datetime:
    return datetime(2024, 1, day, hour, tzinfo=UTC)


def price_rows(symbol: str, start: datetime, closes: list[float]) -> list[h.PriceRow]:
    rows = []
    for i, close in enumerate(closes):
        ts = start + timedelta(hours=i)
        prev = closes[i - 1] if i else close
        rows.append(h.PriceRow(ts, symbol, prev, max(prev, close) + 0.1, min(prev, close) - 0.1, close, "mark"))
    return rows


def test_past_only_realized_vol_percentile_ignores_future_spike() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    base = [100 + math.sin(i / 5) for i in range(24 * 70)]
    rows = price_rows("HYPE", start, base)
    boundary = start + timedelta(days=50)
    before = h.percentile_at_or_before(h.realized_vol_percentiles(rows), boundary)
    spiked = list(base)
    for i in range(24 * 55, len(spiked)):
        spiked[i] *= 1.0 + (0.25 if i % 2 else -0.25)
    after = h.percentile_at_or_before(h.realized_vol_percentiles(price_rows("HYPE", start, spiked)), boundary)
    assert after == before


def test_supertrend_direction_flips_on_known_reversal() -> None:
    bars = []
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    closes = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 119, 118, 117, 116, 100, 96, 94, 92, 90, 88, 95, 100, 110, 122]
    for i, close in enumerate(closes):
        bars.append(h.Bar(ts + timedelta(hours=4 * (i + 1)), "HYPE", close, close + 1, close - 1, close, "mark"))
    dirs = h.compute_supertrend(bars, period=3, multiplier=1.5)
    flips = h.find_flip_indices(dirs)
    assert flips
    assert -1 in [dirs[i] for i in flips]


def test_resampling_4h_and_1d_drops_partial_and_aligns_utc() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [h.PriceRow(start + timedelta(hours=i), "HYPE", i, i + 2, i - 1, i + 1, "mark") for i in range(29)]
    bars4h = h.resample_bars(rows, "4h")
    bars1d = h.resample_bars(rows, "1d")
    assert len(bars4h) == 7
    assert bars4h[0].timestamp_utc == datetime(2024, 1, 1, 4, tzinfo=UTC)
    assert bars4h[0].open == 0
    assert bars4h[0].high == 5
    assert bars4h[0].low == -1
    assert bars4h[0].close == 4
    assert len(bars1d) == 1
    assert bars1d[0].timestamp_utc == datetime(2024, 1, 2, tzinfo=UTC)
    assert bars1d[0].open == 0
    assert bars1d[0].close == 24


def test_max_hold_cap_exits_exactly_when_no_opposite_flip() -> None:
    bars = [h.Bar(dt(1) + timedelta(hours=4 * i), "HYPE", 100, 101, 99, 100 + i, "mark") for i in range(40)]
    directions = [None] + [1] * 39
    directions[2:] = [-1] * 38
    volp = {b.timestamp_utc: 1.0 for b in bars}
    entries, _ = h.build_entry_plan("HYPE", "4h", bars, directions, volp)
    assert entries[0]["holding_period_bars"] == h.MAX_HOLD_BARS
    assert entries[0]["exit_reason"] == "max_hold"


def test_regime_filter_applied_only_at_entry_timestamp() -> None:
    bars = [h.Bar(dt(1) + timedelta(hours=4 * i), "HYPE", 100, 101, 99, 100 + i, "mark") for i in range(40)]
    directions = [1] * 40
    directions[5] = -1
    directions[6:20] = [-1] * 14
    directions[20:] = [1] * 20
    volp = {b.timestamp_utc: 0.1 for b in bars}
    volp[bars[5].timestamp_utc] = 0.5
    entries, _ = h.build_entry_plan("HYPE", "4h", bars, directions, volp)
    assert len(entries) == 1
    assert entries[0]["entry_idx"] == 5
    assert entries[0]["exit_idx"] == 20


def test_funding_accrual_window_and_sign_convention() -> None:
    rows = [
        h.FundingRow(dt(1, 0), "HYPE", 0.001, "archive"),
        h.FundingRow(dt(1, 1), "HYPE", 0.001, "archive"),
        h.FundingRow(dt(1, 2), "HYPE", -0.002, "archive"),
        h.FundingRow(dt(1, 3), "HYPE", 0.003, "archive"),
    ]
    assert h.funding_accrual_bps(rows, dt(1, 0), dt(1, 2), 1) == 10.0
    assert h.funding_accrual_bps(rows, dt(1, 0), dt(1, 2), -1) == -10.0


def test_direction_convention_for_gross_bps() -> None:
    assert h.gross_return_bps(100, 101, 1) > 0
    assert h.gross_return_bps(100, 99, -1) > 0


def test_phase_blocking_statuses(tmp_path: Path) -> None:
    pre = tmp_path / "pre.md"
    pre.write_text("x", encoding="utf-8")
    hp = tmp_path / "hash.txt"
    hp.write_text(h.sha256_file(pre) + "\n", encoding="utf-8")
    result = h.run_phase0(out_root=tmp_path / "out", precommitment_path=pre, expected_hash_path=hp)
    assert result["summary"]["phase0a_verdict"] == h.PHASE0A_INSUFFICIENT_COVERAGE
    assert result["summary"]["phase0b_verdict_by_timeframe"] == {}
    assert result["summary"]["phase0c_verdict_by_timeframe"] == {}
    assert h.overall_status(h.PHASE0A_PASSED, {"4h": h.PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES, "1d": h.PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES}, {}) == h.PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES


def test_verdict_ladder_ordering() -> None:
    entries = [h.Entry("HYPE", "4h", dt(1), dt(2), 1, 100, 110, 2, "signal_flip", 0.5, 100, 0, 90, 94)]
    assert h.phase0c_summary("4h", entries, {"HYPE": 101})["phase0c_verdict"] == h.PHASE0C_CHOP_REGIME_NOT_RESOLVED
    entries_fd = [h.Entry("HYPE", "4h", dt(1), dt(2), 1, 100, 101, 2, "signal_flip", 0.5, 1, 2, -7, -3)]
    assert h.phase0c_summary("4h", entries_fd, {"HYPE": 1})["phase0c_verdict"] == h.PHASE0C_FUNDING_DOMINATES_NOT_TREND
    entries_no = [h.Entry("HYPE", "4h", dt(1), dt(2), 1, 100, 99, 2, "signal_flip", 0.5, -100, 0, -110, -106)]
    assert h.phase0c_summary("4h", entries_no, {"HYPE": 1})["phase0c_verdict"] == h.PHASE0C_NO_GROSS_EDGE
    verdict, _ = h.classify_phase0c_verdict(timeframe="4h", median_gross_return_bps=12, median_funding_accrual_bps=0, median_net_return_bps_primary=2, win_rate_primary=0.40, max_flip_count_per_symbol_per_year=1)
    assert verdict == h.PHASE0C_POSITIVE_EXPECTANCY_LOW_HIT_RATE
    entries_ready = [h.Entry("HYPE", "4h", dt(1), dt(2), 1, 100, 102, 2, "signal_flip", 0.5, 200, 0, 190, 194)] * 2 + [h.Entry("HYPE", "4h", dt(1), dt(2), 1, 100, 100.2, 2, "signal_flip", 0.5, 2, 0, -8, -4)]
    assert h.phase0c_summary("4h", entries_ready, {"HYPE": 1})["phase0c_verdict"] == h.PHASE0_READY_FOR_V1_PRECOMMITMENT


def test_synthetic_noise_input_fails_chop_or_no_gross() -> None:
    entries = [h.Entry("HYPE", "4h", dt(1), dt(2), 1, 100, 99, 2, "signal_flip", 0.5, -100, 0, -110, -106)] * 5
    assert h.phase0c_summary("4h", entries, {"HYPE": 1})["phase0c_verdict"] in {h.PHASE0C_CHOP_REGIME_NOT_RESOLVED, h.PHASE0C_NO_GROSS_EDGE}


def test_synthetic_clean_trend_input_readiness() -> None:
    entries = [h.Entry("HYPE", "1d", dt(1), dt(3), 1, 100, 103, 2, "signal_flip", 0.5, 300, 0, 290, 294)] * 10
    assert h.phase0c_summary("1d", entries, {"HYPE": 2})["phase0c_verdict"] == h.PHASE0_READY_FOR_V1_PRECOMMITMENT


def test_synthetic_high_funding_dominated_input_fails() -> None:
    entries = [h.Entry("HYPE", "1d", dt(1), dt(3), 1, 100, 101, 2, "signal_flip", 0.5, 100, 200, 290, 294)] * 10
    assert h.phase0c_summary("1d", entries, {"HYPE": 2})["phase0c_verdict"] == h.PHASE0C_FUNDING_DOMINATES_NOT_TREND


def test_forbidden_verdict_strings_not_emitted_from_source() -> None:
    source = Path(h.__file__).read_text(encoding="utf-8")
    forbidden = ["CANDIDATE", "TRADE_READY", "EXECUTION_READY", "PROMOTED", "APPROVED", "AUTHORIZED", "LIVE_READY", "BOT_READY", "SHADOW_READY"]
    constants = {n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for word in forbidden:
        assert word not in constants


def test_safety_import_scan_source_modules() -> None:
    paths = [Path(h.__file__), Path(h.__file__).with_name("run_hyperliquid_supertrend_4h1d_phase0.py")]
    forbidden = re.compile(r"\b(clob_client|signing|wallet|open_interest|oi_velocity|oi_compression|hyperliquid_oi_velocity_compression_phase0)\b")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text)
        tree = ast.parse(text)
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        rendered = "\n".join(ast.unparse(n) for n in imports)
        assert not re.search(r"\b(execution|live|order|bot|private|credential|auth|key)\b", rendered)


def test_funding_quarantine_entry_direction_unaffected() -> None:
    plan = {"symbol": "HYPE", "timeframe": "4h", "entry_ts": dt(1), "exit_ts": dt(2), "direction": 1, "entry_price": 100.0, "exit_price": 101.0, "holding_period_bars": 2, "exit_reason": "signal_flip", "realized_vol_percentile_at_entry": 0.5}
    no_funding = h.materialize_entries([plan], {"HYPE": []})[0]
    high_funding = h.materialize_entries([plan], {"HYPE": [h.FundingRow(dt(1, 1), "HYPE", 1.0, "archive")]})[0]
    assert no_funding.direction == high_funding.direction == 1
    assert no_funding.gross_return_bps == high_funding.gross_return_bps
    assert no_funding.net_return_bps_primary != high_funding.net_return_bps_primary


def test_precommitment_hash_match_and_mismatch(tmp_path: Path) -> None:
    pre = tmp_path / "pre.md"
    pre.write_text("abc", encoding="utf-8")
    hp = tmp_path / "hash.txt"
    hp.write_text(h.sha256_file(pre) + "\n", encoding="utf-8")
    result = h.run_phase0(out_root=tmp_path / "ok", precommitment_path=pre, expected_hash_path=hp)
    assert result["manifest"]["verified_matches_precommitment_file"] is True
    hp.write_text("0" * 64 + "\n", encoding="utf-8")
    bad = h.run_phase0(out_root=tmp_path / "bad", precommitment_path=pre, expected_hash_path=hp)
    assert bad["summary"]["phase0a_verdict"] == h.PRECOMMITMENT_HASH_MISMATCH
    assert bad["summary"]["overall_status"] == h.PRECOMMITMENT_HASH_MISMATCH


def test_archive_ingest_preserves_intraday_timestamps_and_ignores_non_price_fields(tmp_path: Path) -> None:
    source = tmp_path / "asset_ctxs"
    source.mkdir()
    path = source / "HYPE.jsonl"
    path.write_text(
        "\n".join([
            '{"symbol":"HYPE","ts_event":"2025-05-01T00:00:00Z","price":10.0,"price_source":"mark","unused_metric":999}',
            '{"symbol":"HYPE","ts_event":"2025-05-01T00:15:00Z","price":11.0,"price_source":"mark","unused_metric":1000}',
            '{"symbol":"HYPE","ts_event":"2025-05-01T00:45:00Z","price":9.0,"price_source":"mark","unused_metric":1001}',
            '{"symbol":"HYPE","ts_event":"2025-05-01T01:00:00Z","price":12.0,"price_source":"mark","unused_metric":1002}',
            '{"symbol":"HYPE","ts_event":"2025-05-01T01:30:00Z","price":13.0,"price_source":"mark","unused_metric":1003}',
        ]) + "\n",
        encoding="utf-8",
    )
    minute_rows = ingest.load_asset_ctxs_minute_prices(source, ("HYPE",))
    assert minute_rows["HYPE"][1]["timestamp_utc"] == datetime(2025, 5, 1, 0, 15, tzinfo=UTC)
    assert "unused_metric" not in minute_rows["HYPE"][0]
    hourly = ingest.minute_prices_to_hourly_ohlc(minute_rows)
    assert [r["timestamp_utc"] for r in hourly] == ["2025-05-01T00:00:00Z", "2025-05-01T01:00:00Z"]
    assert hourly[0]["open"] == 10.0
    assert hourly[0]["high"] == 11.0
    assert hourly[0]["low"] == 9.0
    assert hourly[0]["close"] == 9.0


def test_phase0a_ingests_canonical_archive_csvs(tmp_path: Path) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    prices = tmp_path / "prices.csv"
    funds = tmp_path / "funding.csv"
    with prices.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp_utc", "symbol", "open", "high", "low", "close", "price_source"])
        writer.writeheader()
        for symbol in h.FROZEN_UNIVERSE:
            for i in range(24 * 397):
                ts = start + timedelta(hours=i)
                writer.writerow({"timestamp_utc": ts.isoformat().replace("+00:00", "Z"), "symbol": symbol, "open": 100, "high": 101, "low": 99, "close": 100 + (i % 7) * 0.01, "price_source": "mark"})
    with funds.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp_utc", "symbol", "funding_rate", "funding_source"])
        writer.writeheader()
        for symbol in h.FROZEN_UNIVERSE:
            for i in range(24 * 397):
                ts = start + timedelta(hours=i)
                writer.writerow({"timestamp_utc": ts.isoformat().replace("+00:00", "Z"), "symbol": symbol, "funding_rate": 0.0, "funding_source": "test"})
    price_rows_loaded = h.load_price_csv(prices)
    funding_rows_loaded = h.load_funding_csv(funds)
    verdict, coverage = h.assess_coverage(h.group_by_symbol(price_rows_loaded), h.group_by_symbol(funding_rows_loaded))
    assert verdict == h.PHASE0A_PASSED
    assert all(row["phase0a_symbol_status"] == h.PHASE0A_PASSED for row in coverage)
    assert max(float(row["max_price_gap_hours"]) for row in coverage) == 1.0


def test_phase0a_failure_blocks_data_outputs_with_headers(tmp_path: Path) -> None:
    pre = tmp_path / "pre.md"
    pre.write_text("abc", encoding="utf-8")
    hp = tmp_path / "hash.txt"
    hp.write_text(h.sha256_file(pre) + "\n", encoding="utf-8")
    result = h.run_phase0(out_root=tmp_path / "out", precommitment_path=pre, expected_hash_path=hp)
    report = Path(result["run_dir"])
    with (report / "entries_preview.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows == [h.ENTRY_COLUMNS]



def test_full_entries_artifact_written_with_all_rows_and_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "report"
    run_dir.mkdir()
    manifest_base = {"run_id": "test_run", "verified_matches_precommitment_file": True, "data_sources_used": []}
    rows = [
        {
            "symbol": "HYPE",
            "timeframe": "4h",
            "entry_ts": "2024-01-01T00:00:00Z",
            "exit_ts": "2024-01-02T00:00:00Z",
            "direction": "long",
            "entry_price": 100.0,
            "exit_price": 101.0,
            "holding_period_bars": 6,
            "hold_bars": 6,
            "max_hold_bars": h.MAX_HOLD_BARS,
            "exit_reason": "signal_flip",
            "realized_vol_percentile_at_entry": 0.5,
            "gross_return_bps": 100.0,
            "funding_accrual_bps": 0.0,
            "net_return_bps_primary": 90.0,
            "net_return_bps_diagnostic": 94.0,
            "source_entry_idx": 10,
            "source_exit_idx": 16,
        },
        {
            "symbol": "HYPE",
            "timeframe": "1d",
            "entry_ts": "2024-01-03T00:00:00Z",
            "exit_ts": "2024-01-06T00:00:00Z",
            "direction": "short",
            "entry_price": 100.0,
            "exit_price": 99.0,
            "holding_period_bars": 3,
            "hold_bars": 3,
            "max_hold_bars": h.MAX_HOLD_BARS,
            "exit_reason": "max_hold",
            "realized_vol_percentile_at_entry": 0.6,
            "gross_return_bps": 100.0,
            "funding_accrual_bps": -5.0,
            "net_return_bps_primary": 85.0,
            "net_return_bps_diagnostic": 89.0,
            "source_entry_idx": 20,
            "source_exit_idx": 23,
        },
    ]
    result = h._write_all(
        run_dir,
        manifest_base,
        [],
        [],
        rows,
        [],
        [],
        [],
        h.PHASE0A_PASSED,
        {"4h": h.PHASE0B_PASSED, "1d": h.PHASE0B_PASSED},
        {},
        [],
    )
    full_path = run_dir / "entries_full.csv"
    preview_path = run_dir / "entries_preview.csv"
    assert full_path.exists()
    assert preview_path.exists()
    with full_path.open(newline="", encoding="utf-8") as f:
        full_rows = list(csv.DictReader(f))
    assert [r["timeframe"] for r in full_rows] == ["4h", "1d"]
    assert len(full_rows) == 2
    assert {"entry_timestamp", "exit_timestamp", "explicit_fee_bps", "realized_total_cost_bps", "hold_bars", "max_hold_bars"}.issubset(full_rows[0])
    summary = result["summary"]
    manifest = result["manifest"]
    assert summary["entry_artifacts"]["entries_full"]["row_count"] == 2
    assert summary["entry_artifacts"]["entries_full"]["sha256"] == h.sha256_file(full_path)
    assert summary["entry_counts_by_timeframe"] == {"4h": 1, "1d": 1}
    assert manifest["entry_artifacts"]["entries_preview"]["artifact_role"] == "preview_truncated_first_200_rows"
    assert manifest["entry_artifacts"]["entries_full"]["artifact_role"] == "primary_full_per_entry_artifact"


def test_summary_counts_and_medians_reconcile_with_entries_full(tmp_path: Path) -> None:
    run_dir = tmp_path / "report"
    run_dir.mkdir()
    manifest_base = {"run_id": "test_run", "verified_matches_precommitment_file": True, "data_sources_used": []}
    rows = []
    for timeframe, gross_values, net_values in [
        ("4h", [-200.0, -100.0, 50.0], [-210.0, -110.0, 40.0]),
        ("1d", [-25.0, 50.0, 125.0], [-35.0, 40.0, 115.0]),
    ]:
        for i, (gross, net) in enumerate(zip(gross_values, net_values, strict=True)):
            rows.append({
                "symbol": "HYPE", "timeframe": timeframe,
                "entry_ts": f"2024-01-0{i+1}T00:00:00Z", "exit_ts": f"2024-01-0{i+2}T00:00:00Z",
                    "direction": "long", "entry_price": 100, "exit_price": 101,
                "holding_period_bars": 1, "hold_bars": 1, "max_hold_bars": h.MAX_HOLD_BARS,
                "exit_reason": "signal_flip", "realized_vol_percentile_at_entry": 0.5,
                "gross_return_bps": gross, "funding_accrual_bps": 0.0,
                "net_return_bps_primary": net, "net_return_bps_diagnostic": net + 4.0,
                "source_entry_idx": i, "source_exit_idx": i + 1,
            })
    result = h._write_all(
        run_dir, manifest_base, [], [], rows, [], [], [], h.PHASE0A_PASSED,
        {"4h": h.PHASE0B_PASSED, "1d": h.PHASE0B_PASSED}, {}, [],
    )
    assert result["summary"]["entry_counts_by_timeframe"] == {"4h": 3, "1d": 3}
    assert result["summary"]["entry_aggregate_reconciliation"]["4h"]["median_gross_return_bps"] == -100.0
    assert result["summary"]["entry_aggregate_reconciliation"]["4h"]["median_net_return_bps_primary"] == -110.0
    assert result["summary"]["entry_aggregate_reconciliation"]["1d"]["median_gross_return_bps"] == 50.0
    assert result["summary"]["entry_aggregate_reconciliation"]["1d"]["median_net_return_bps_primary"] == 40.0


def test_entry_artifact_recovery_does_not_change_frozen_parameters() -> None:
    assert h.TIMEFRAMES == ("4h", "1d")
    assert h.ATR_PERIOD == 10
    assert h.ATR_MULTIPLIER == 3.0
    assert h.MAX_HOLD_BARS == 30
    assert h.PRIMARY_COST_BPS == 10.0
    assert h.DIAGNOSTIC_COST_BPS == 6.0
    assert h.VOL_PERCENTILE_THRESHOLD == 0.50
    assert h.FROZEN_UNIVERSE == (
        "HYPE", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "SUI", "TRX", "LTC",
        "BCH", "TON", "DOT", "AAVE", "UNI", "APT", "ARB", "OP", "SEI", "INJ",
    )
