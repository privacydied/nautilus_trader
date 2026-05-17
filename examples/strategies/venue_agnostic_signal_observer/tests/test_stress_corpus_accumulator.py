"""Tests for local-only Edge Miner stress corpus accumulator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture import (
    build_parser as build_capture_parser,
)
from examples.strategies.venue_agnostic_signal_observer.stress_corpus import (
    TARGET_ASSETS,
    _asset_from_filename,
)
from examples.strategies.venue_agnostic_signal_observer.stress_corpus_accumulator import (
    MIN_READY_USABLE_WINDOWS,
    build_accumulated_stress_corpus,
    load_accumulated_corpus_manifest,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite

_NS = 1_000_000_000


def _write_ticks(path: Path, ticks: list[TradeTickLite]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(tick.to_json() + "\n" for tick in ticks))


def _source_stress_ticks(asset: str = "BTC", offset_s: int = 0) -> list[TradeTickLite]:
    base = offset_s * _NS
    return [
        TradeTickLite(ts_event=base, venue="local", symbol=f"{asset}-USD", price=100.0, size=1.0, side="buy"),
        TradeTickLite(ts_event=base + 30 * _NS, venue="local", symbol=f"{asset}-USD", price=100.4, size=1.0, side="buy"),
        TradeTickLite(ts_event=base + 60 * _NS, venue="local", symbol=f"{asset}-USD", price=100.8, size=1.0, side="buy"),
        TradeTickLite(ts_event=base + 420 * _NS, venue="local", symbol=f"{asset}-USD", price=100.9, size=1.0, side="buy"),
    ]


def _source_quiet_ticks(asset: str = "BTC") -> list[TradeTickLite]:
    return [
        TradeTickLite(ts_event=0, venue="local", symbol=f"{asset}-USD", price=100.0, size=1.0, side="buy"),
        TradeTickLite(ts_event=30 * _NS, venue="local", symbol=f"{asset}-USD", price=100.01, size=1.0, side="buy"),
        TradeTickLite(ts_event=60 * _NS, venue="local", symbol=f"{asset}-USD", price=100.02, size=1.0, side="buy"),
        TradeTickLite(ts_event=420 * _NS, venue="local", symbol=f"{asset}-USD", price=100.03, size=1.0, side="buy"),
    ]


def _target_ticks(asset: str, start_s: int = -120, end_s: int = 100_000, final_price: float = 50.0) -> list[TradeTickLite]:
    return [
        TradeTickLite(ts_event=start_s * _NS, venue="local", symbol=f"{asset}-USD", price=50.0, size=1.0, side="buy"),
        TradeTickLite(ts_event=end_s * _NS, venue="local", symbol=f"{asset}-USD", price=final_price, size=1.0, side="sell"),
    ]


def _write_all_targets(data_dir: Path, final_price: float = 50.0) -> None:
    for target in ("SOL", "LINK", "DOGE", "AVAX"):
        _write_ticks(data_dir / f"trades_local_{target}-USD_1.jsonl", _target_ticks(target, final_price=final_price))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _split_symbols(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def test_capture_default_target_contract_includes_all_stress_beta_targets():
    args = build_capture_parser().parse_args([])
    target_symbols = _split_symbols(args.target_symbols)

    assert set(TARGET_ASSETS) == {"SOL", "LINK", "DOGE", "AVAX"}
    assert {f"{asset}/USD" for asset in TARGET_ASSETS}.issubset(target_symbols)
    if "AVAX" in TARGET_ASSETS:
        assert "AVAX/USD" in target_symbols


def test_unresolved_capture_filename_prefix_is_parsed_by_artifact_layer():
    assert _asset_from_filename(Path("trades_coinbase_UNRESOLVED:AVAX-USD_1.jsonl")) == "AVAX"
    assert _asset_from_filename(Path("trades_kraken_UNRESOLVED:XDG-USD_1.jsonl")) == "DOGE"


class TestStressCorpusAccumulator:
    def test_quiet_input_produces_no_new_stress_windows(self, tmp_path: Path):
        data = tmp_path / "data"
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", _source_quiet_ticks())
        _write_all_targets(data)

        result = build_accumulated_stress_corpus((data,), tmp_path / "out", created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "NO_NEW_STRESS_WINDOWS"
        assert result.stress_window_count == 0
        assert result.usable_window_count == 0

    def test_overlapping_stress_labels_are_deduplicated_deterministically(self, tmp_path: Path):
        data = tmp_path / "data"
        ticks = _source_stress_ticks("BTC", 0) + _source_stress_ticks("BTC", 10)
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", ticks)
        _write_all_targets(data)

        first = build_accumulated_stress_corpus((data,), tmp_path / "out_a", created_at_utc="2026-01-01T00:00:00+00:00")
        second = build_accumulated_stress_corpus((data,), tmp_path / "out_b", created_at_utc="2026-01-02T00:00:00+00:00")

        assert first.stress_window_count == 1
        assert second.stress_window_count == 1
        assert first.corpus_hash == second.corpus_hash

    def test_source_stress_without_target_coverage_is_rejected(self, tmp_path: Path):
        data = tmp_path / "data"
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_ticks(data / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL"))

        result = build_accumulated_stress_corpus((data,), tmp_path / "out", created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "TARGET_COVERAGE_LIMITED"
        assert result.usable_window_count == 0
        manifest = _read_json(result.manifest_path)
        assert manifest["stress_windows"][0]["target_assets_missing"]

    def test_partial_target_coverage_summary_counts_all_stress_windows(self, tmp_path: Path):
        data = tmp_path / "data"
        ticks: list[TradeTickLite] = []
        for idx in range(6):
            ticks.extend(_source_stress_ticks("BTC", offset_s=idx * 60 * 60))
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", ticks)
        _write_ticks(data / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL"))
        for target in ("LINK", "DOGE"):
            _write_ticks(data / f"trades_local_{target}-USD_1.jsonl", _target_ticks(target, end_s=60 * 60 + 420 + 360))

        result = build_accumulated_stress_corpus((data,), tmp_path / "out", created_at_utc="2026-01-01T00:00:00+00:00")

        summary = _read_json(result.output_dir / "target_coverage_summary.json")
        assert result.status == "TARGET_COVERAGE_LIMITED"
        assert result.stress_window_count == 6
        assert result.usable_window_count == 0
        assert summary["status"] == "TARGET_COVERAGE_LIMITED"
        assert summary["coverage_by_target"] == {
            "SOL": 6,
            "LINK": 2,
            "DOGE": 2,
            "AVAX": 0,
        }
        assert summary["usable_window_count"] == 0

    def test_source_stress_with_target_coverage_is_accepted_but_accumulating(self, tmp_path: Path):
        data = tmp_path / "data"
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_all_targets(data)

        result = build_accumulated_stress_corpus((data,), tmp_path / "out", created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "ACCUMULATING"
        assert result.usable_window_count == 1
        assert result.ready_for_rerun is False

    def test_corpus_ready_at_20_independent_usable_windows(self, tmp_path: Path):
        data = tmp_path / "data"
        ticks: list[TradeTickLite] = []
        for idx in range(MIN_READY_USABLE_WINDOWS):
            asset = "BTC" if idx % 2 == 0 else "ETH"
            ticks.extend(_source_stress_ticks(asset, offset_s=idx * 31 * 60))
        btc_ticks = [tick for tick in ticks if tick.symbol.startswith("BTC")]
        eth_ticks = [tick for tick in ticks if tick.symbol.startswith("ETH")]
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", btc_ticks)
        _write_ticks(data / "trades_local_ETH-USD_1.jsonl", eth_ticks)
        _write_all_targets(data)

        result = build_accumulated_stress_corpus((data,), tmp_path / "out", created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "CORPUS_READY_FOR_RERUN"
        assert result.usable_window_count == MIN_READY_USABLE_WINDOWS
        summary = _read_json(result.output_dir / "target_coverage_summary.json")
        assert summary["coverage_by_target"] == {target: MIN_READY_USABLE_WINDOWS for target in TARGET_ASSETS}
        manifest = load_accumulated_corpus_manifest(result.manifest_path)
        assert manifest["ready_for_rerun"] is True

    def test_target_returns_are_not_inspected_for_inclusion(self, tmp_path: Path):
        data_a = tmp_path / "a"
        data_b = tmp_path / "b"
        _write_ticks(data_a / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_ticks(data_b / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_all_targets(data_a, final_price=50.0)
        _write_all_targets(data_b, final_price=500.0)

        first = build_accumulated_stress_corpus((data_a,), tmp_path / "out_a", created_at_utc="2026-01-01T00:00:00+00:00")
        second = build_accumulated_stress_corpus((data_b,), tmp_path / "out_b", created_at_utc="2026-01-01T00:00:00+00:00")
        windows_a = _read_json(first.manifest_path)["stress_windows"]
        windows_b = _read_json(second.manifest_path)["stress_windows"]
        summary_a = _read_json(first.output_dir / "target_coverage_summary.json")
        summary_b = _read_json(second.output_dir / "target_coverage_summary.json")

        comparable_a = [{k: v for k, v in row.items() if k != "stress_window_id"} for row in windows_a]
        comparable_b = [{k: v for k, v in row.items() if k != "stress_window_id"} for row in windows_b]
        assert comparable_a == comparable_b
        assert summary_a["coverage_by_target"] == summary_b["coverage_by_target"]

    def test_corpus_hash_is_stable(self, tmp_path: Path):
        data = tmp_path / "data"
        _write_ticks(data / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_all_targets(data)

        first = build_accumulated_stress_corpus((data,), tmp_path / "out_a", created_at_utc="2026-01-01T00:00:00+00:00")
        second = build_accumulated_stress_corpus((data,), tmp_path / "out_b", created_at_utc="2026-02-01T00:00:00+00:00")

        assert first.corpus_hash == second.corpus_hash

    def test_invalid_or_corrupt_manifest_is_rejected(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"status":"ACCUMULATING"}\n')
        with pytest.raises(ValueError):
            load_accumulated_corpus_manifest(bad)
