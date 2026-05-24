from __future__ import annotations

import ast
import csv
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer import hyperliquid_supertrend_4h1d_phase0 as h


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
