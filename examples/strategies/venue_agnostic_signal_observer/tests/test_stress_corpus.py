"""Tests for offline Edge Miner stress corpus assembly."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.stress_corpus import (
    build_stress_corpus,
    load_stress_corpus_manifest,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite

_NS = 1_000_000_000


def _write_ticks(path: Path, ticks: list[TradeTickLite]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(tick.to_json() + "\n" for tick in ticks))


def _source_stress_ticks(asset: str = "BTC") -> list[TradeTickLite]:
    return [
        TradeTickLite(ts_event=0, venue="local", symbol=f"{asset}-USD", price=100.0, size=1.0, side="buy"),
        TradeTickLite(ts_event=30 * _NS, venue="local", symbol=f"{asset}-USD", price=100.4, size=1.0, side="buy"),
        TradeTickLite(ts_event=60 * _NS, venue="local", symbol=f"{asset}-USD", price=100.8, size=1.0, side="buy"),
        TradeTickLite(ts_event=390 * _NS, venue="local", symbol=f"{asset}-USD", price=100.9, size=1.0, side="buy"),
    ]


def _source_quiet_ticks(asset: str = "BTC") -> list[TradeTickLite]:
    return [
        TradeTickLite(ts_event=0, venue="local", symbol=f"{asset}-USD", price=100.0, size=1.0, side="buy"),
        TradeTickLite(ts_event=30 * _NS, venue="local", symbol=f"{asset}-USD", price=100.01, size=1.0, side="buy"),
        TradeTickLite(ts_event=60 * _NS, venue="local", symbol=f"{asset}-USD", price=100.02, size=1.0, side="buy"),
        TradeTickLite(ts_event=390 * _NS, venue="local", symbol=f"{asset}-USD", price=100.03, size=1.0, side="buy"),
    ]


def _target_ticks(asset: str, final_price: float = 50.0) -> list[TradeTickLite]:
    return [
        TradeTickLite(ts_event=0, venue="local", symbol=f"{asset}-USD", price=50.0, size=1.0, side="buy"),
        TradeTickLite(ts_event=390 * _NS, venue="local", symbol=f"{asset}-USD", price=final_price, size=1.0, side="sell"),
    ]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


class TestStressCorpusBuilder:
    def test_quiet_corpus_produces_no_stress_windows(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        out = tmp_path / "out"
        _write_ticks(data_dir / "trades_local_BTC-USD_1.jsonl", _source_quiet_ticks())
        _write_ticks(data_dir / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL"))

        result = build_stress_corpus(input_dirs=(data_dir,), output_dir=out, created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "NO_STRESS_WINDOWS_FOUND"
        assert result.stress_window_count == 0
        assert result.usable_window_count == 0
        manifest = _read_json(result.manifest_path)
        assert manifest["status"] == "NO_STRESS_WINDOWS_FOUND"
        assert manifest["stress_windows"] == []

    def test_source_stress_without_target_coverage_is_insufficient(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        out = tmp_path / "out"
        _write_ticks(data_dir / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())

        result = build_stress_corpus(input_dirs=(data_dir,), output_dir=out, created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "INSUFFICIENT_TARGET_COVERAGE"
        assert result.stress_window_count >= 1
        assert result.usable_window_count == 0
        manifest = _read_json(result.manifest_path)
        assert manifest["rejected_window_count"] == manifest["stress_window_count"]

    def test_source_stress_with_target_coverage_is_available(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        out = tmp_path / "out"
        _write_ticks(data_dir / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_ticks(data_dir / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL"))

        result = build_stress_corpus(input_dirs=(data_dir,), output_dir=out, created_at_utc="2026-01-01T00:00:00+00:00")

        assert result.status == "STRESS_CORPUS_AVAILABLE"
        assert result.usable_window_count >= 1
        manifest = load_stress_corpus_manifest(result.manifest_path)
        assert manifest["status"] == "STRESS_CORPUS_AVAILABLE"
        assert manifest["usable_window_count"] == result.usable_window_count

    def test_target_future_returns_do_not_change_stress_window_selection(self, tmp_path: Path):
        data_a = tmp_path / "a"
        data_b = tmp_path / "b"
        out_a = tmp_path / "out_a"
        out_b = tmp_path / "out_b"
        _write_ticks(data_a / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_ticks(data_a / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL", final_price=50.0))
        _write_ticks(data_b / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_ticks(data_b / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL", final_price=500.0))

        build_stress_corpus(input_dirs=(data_a,), output_dir=out_a, created_at_utc="2026-01-01T00:00:00+00:00")
        build_stress_corpus(input_dirs=(data_b,), output_dir=out_b, created_at_utc="2026-01-01T00:00:00+00:00")
        windows_a = _read_json(out_a / "stress_window_summary.json")["windows"]
        windows_b = _read_json(out_b / "stress_window_summary.json")["windows"]

        comparable_a = [
            {k: v for k, v in row.items() if k not in {"stress_window_id"}}
            for row in windows_a
        ]
        comparable_b = [
            {k: v for k, v in row.items() if k not in {"stress_window_id"}}
            for row in windows_b
        ]
        assert comparable_a == comparable_b

    def test_corpus_hash_is_stable(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        out_a = tmp_path / "out_a"
        out_b = tmp_path / "out_b"
        _write_ticks(data_dir / "trades_local_BTC-USD_1.jsonl", _source_stress_ticks())
        _write_ticks(data_dir / "trades_local_SOL-USD_1.jsonl", _target_ticks("SOL"))

        first = build_stress_corpus(input_dirs=(data_dir,), output_dir=out_a, created_at_utc="2026-01-01T00:00:00+00:00")
        second = build_stress_corpus(input_dirs=(data_dir,), output_dir=out_b, created_at_utc="2026-02-01T00:00:00+00:00")

        assert first.corpus_hash == second.corpus_hash

    def test_refuses_invalid_or_unusable_manifest(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"status":"NO_STRESS_WINDOWS_FOUND"}\n')
        with pytest.raises(ValueError):
            load_stress_corpus_manifest(bad)
