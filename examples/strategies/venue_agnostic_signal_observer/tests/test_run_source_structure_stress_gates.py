"""Tests for run_source_structure_stress_gates.py — CLI runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite


def _make_tick_json(ts_ns: int, price: float, symbol: str = "BTCUSDT") -> str:
    tick = TradeTickLite(
        ts_event=ts_ns, venue="BINANCE", symbol=symbol,
        price=price, size=1.0, side="buy",
    )
    return json.dumps(tick.to_dict())


def _write_ticks(path: Path, prices: list[float], symbol: str = "BTCUSDT"):
    with open(path, "w") as f:
        f.writelines(_make_tick_json(i * 1_000_000_000, p, symbol) + "\n" for i, p in enumerate(prices))


class TestCLIRunner:
    def test_writes_jsonl_files(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0 + i * 0.1 for i in range(200)])

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        rc = main([
            "--source-files", str(src),
            "--out-dir", str(out),
        ])
        assert rc == 0
        assert (out / "source_return_buckets.jsonl").exists()
        assert (out / "source_volatility_events.jsonl").exists()
        assert (out / "hawkes_intensity_points.jsonl").exists()
        assert (out / "permutation_entropy_points.jsonl").exists()
        assert (out / "hawkes_stress_labels.jsonl").exists()
        assert (out / "hawkes_stress_windows.jsonl").exists()
        assert (out / "source_structure_stress_summary.json").exists()

    def test_summary_only_mode(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0 + i * 0.1 for i in range(200)])

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        rc = main([
            "--source-files", str(src),
            "--out-dir", str(out),
            "--summary-only",
        ])
        assert rc == 0
        assert (out / "source_structure_stress_summary.json").exists()
        # JSONL files should not be written in summary-only mode
        assert not (out / "source_return_buckets.jsonl").exists()

    def test_refuses_target_symbols(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0] * 10)

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        with pytest.raises(SystemExit, match="TARGET_INPUT_FORBIDDEN"):
            main([
                "--source-files", str(src),
                "--out-dir", str(out),
                "--target-symbols", "SOLUSDT",
            ])

    def test_refuses_target_files(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0] * 10)

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        with pytest.raises(SystemExit, match="TARGET_INPUT_FORBIDDEN"):
            main([
                "--source-files", str(src),
                "--out-dir", str(out),
                "--target-files", "ticks_solusdt.jsonl",
            ])

    def test_infers_symbol_from_filename(self, tmp_path):
        src = tmp_path / "ticks_ethusdt.jsonl"
        _write_ticks(src, [3000.0 + i * 0.1 for i in range(200)], "ETHUSDT")

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        rc = main([
            "--source-files", str(src),
            "--out-dir", str(out),
        ])
        assert rc == 0
        with open(out / "source_structure_stress_summary.json") as f:
            summary = json.load(f)
        assert "ETHUSDT" in summary["source_symbols"]

    def test_multiple_source_files(self, tmp_path):
        btc = tmp_path / "ticks_btcusdt.jsonl"
        eth = tmp_path / "ticks_ethusdt.jsonl"
        _write_ticks(btc, [50000.0 + i * 0.1 for i in range(200)], "BTCUSDT")
        _write_ticks(eth, [3000.0 + i * 0.1 for i in range(200)], "ETHUSDT")

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        rc = main([
            "--source-files", str(btc), str(eth),
            "--out-dir", str(out),
        ])
        assert rc == 0
        with open(out / "source_structure_stress_summary.json") as f:
            summary = json.load(f)
        assert set(summary["source_symbols"]) == {"BTCUSDT", "ETHUSDT"}

    def test_summary_has_verdicts(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0 + i * 0.1 for i in range(200)])

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        rc = main([
            "--source-files", str(src),
            "--out-dir", str(out),
        ])
        assert rc == 0
        with open(out / "source_structure_stress_summary.json") as f:
            summary = json.load(f)
        assert "hawkes_verdict" in summary
        assert "entropy_verdict" in summary
        assert summary["hawkes_verdict"] in {
            "HAWKES_STRESS_LABELS_READY",
            "HAWKES_INSUFFICIENT_SOURCE_EVENTS",
            "NO_HAWKES_STRESS_LABELS",
        }

    def test_metadata_safety_mode(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0 + i * 0.1 for i in range(200)])

        out = tmp_path / "out"
        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        rc = main([
            "--source-files", str(src),
            "--out-dir", str(out),
        ])
        assert rc == 0
        with open(out / "source_structure_stress_summary.json") as f:
            summary = json.load(f)
        assert summary["_metadata"]["safety_mode"] == "public_data_observer_only"


class TestCLITargetInputRefusal:
    """Test that target-style inputs are rejected."""

    def test_target_symbols_arg_forbidden(self, tmp_path):
        src = tmp_path / "ticks_btcusdt.jsonl"
        _write_ticks(src, [1000.0] * 10)

        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        with pytest.raises(SystemExit):
            main([
                "--source-files", str(src),
                "--out-dir", str(tmp_path / "out"),
                "--target-symbols", "SOLUSDT",
            ])

    def test_forbidden_symbols_in_source_files(self, tmp_path):
        """
        Passing a file named like a target should still work if it's in --source-files.
        The check is on the symbol argument, not the filename.
        """
        src = tmp_path / "ticks_solusdt.jsonl"
        _write_ticks(src, [100.0 + i * 0.1 for i in range(200)], "SOLUSDT")

        from examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates import (
            main,
        )
        # This should raise because SOLUSDT is a forbidden symbol
        with pytest.raises(SystemExit, match="TARGET_INPUT_FORBIDDEN"):
            main([
                "--source-files", str(src),
                "--source-symbols", "SOLUSDT",
                "--out-dir", str(tmp_path / "out"),
            ])
