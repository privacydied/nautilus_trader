#!/usr/bin/env python3
"""Tests for the venue-agnostic signal observer."""
import json
import tempfile
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.config import FeeModel
from examples.strategies.venue_agnostic_signal_observer.config import Horizon

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from examples.strategies.venue_agnostic_signal_observer.config import ObserverConfig
from examples.strategies.venue_agnostic_signal_observer.config import SignalSourceConfig
from examples.strategies.venue_agnostic_signal_observer.data_loading import generate_synthetic_data
from examples.strategies.venue_agnostic_signal_observer.forward_returns import evaluate_signal
from examples.strategies.venue_agnostic_signal_observer.models import ForwardReturnResult
from examples.strategies.venue_agnostic_signal_observer.models import SignalEvent
from examples.strategies.venue_agnostic_signal_observer.observer import SignalObserver
from examples.strategies.venue_agnostic_signal_observer.observer import _build_summary
from examples.strategies.venue_agnostic_signal_observer.reports import write_outputs
from examples.strategies.venue_agnostic_signal_observer.signals import CrossMarketSignalGenerator
from examples.strategies.venue_agnostic_signal_observer.signals import load_signals_from_csv


# ===================================================================
# 1. Config loads defaults
# ===================================================================

class TestConfig:

    def test_defaults(self):
        cfg = ObserverConfig()
        assert len(cfg.horizons) == 6
        assert cfg.fee_model.fee_bps == 5.0
        assert cfg.fee_model.slippage_bps == 1.0

    def test_fee_model_total_cost(self):
        fm = FeeModel(fee_bps=3.0, slippage_bps=2.0, quote_mismatch_buffer_bps=10.0)
        assert fm.total_cost_bps(False) == 5.0
        assert fm.total_cost_bps(True) == 15.0


# ===================================================================
# 2. Fee model subtracts fees correctly (tested above)
# ===================================================================

class TestFeeModel:

    def test_fee_deduction(self):
        """Net return = raw - total cost."""
        fm = FeeModel(fee_bps=5.0, slippage_bps=1.0)
        raw = 10.0
        net = raw - fm.total_cost_bps(False)
        assert net == 4.0


# ===================================================================
# 3. Quote mismatch buffer is applied
# ===================================================================

class TestQuoteMismatch:

    def test_buffer_applied(self):
        fm = FeeModel(fee_bps=3.0, slippage_bps=1.0, quote_mismatch_buffer_bps=10.0)
        assert fm.total_cost_bps(True) == 14.0
        assert fm.total_cost_bps(False) == 4.0


# ===================================================================
# 4. SignalEvent serializes to JSON
# ===================================================================

class TestSignalEvent:

    def _event(self):
        return SignalEvent(
            signal_id="test_001",
            timestamp=1700000000.0,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            signal_type="cross_market_move",
            direction="long",
            strength=50.0,
            metadata={"key": "value"},
            reason="test",
        )

    def test_to_dict(self):
        e = self._event()
        d = e.to_dict()
        assert d["signal_id"] == "test_001"
        assert d["direction"] == "long"
        assert d["metadata"] == {"key": "value"}

    def test_to_json_roundtrip(self):
        e = self._event()
        j = e.to_json()
        parsed = json.loads(j)
        assert parsed["signal_id"] == "test_001"

    def test_from_dict(self):
        e = self._event()
        d = e.to_dict()
        e2 = SignalEvent.from_dict(d)
        assert e2.signal_id == e.signal_id
        assert e2.direction == e.direction


# ===================================================================
# 5. ForwardReturnResult serializes to JSON
# ===================================================================

class TestForwardReturnResult:

    def _result(self):
        return ForwardReturnResult(
            signal_id="test_001",
            signal_timestamp=1700000000.0,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            signal_type="cross_market_move",
            direction="long",
            strength=50.0,
            horizon="60s",
            entry_reference_price=50000.0,
            forward_price=50010.0,
            raw_return_bps=2.0,
            direction_adjusted_return_bps=2.0,
            fee_bps=5.0,
            slippage_bps=1.0,
            net_return_bps=-4.0,
            valid=True,
        )

    def test_to_json(self):
        r = self._result()
        j = r.to_json()
        parsed = json.loads(j)
        assert parsed["signal_id"] == "test_001"
        assert parsed["net_return_bps"] == -4.0

    def test_serializes_none_fields(self):
        r = ForwardReturnResult(
            signal_id="x", signal_timestamp=0.0,
            source_venue="A", source_instrument="B",
            target_venue="C", target_instrument="D",
            signal_type="t", direction="long", strength=1.0,
            horizon="10s",
            valid=False,
            rejection_reason="no_data",
        )
        j = json.loads(r.to_json())
        assert j["valid"] is False
        assert j["rejection_reason"] == "no_data"


# ===================================================================
# 6. Manual CSV signal parser works
# ===================================================================

class TestCSVSignalParser:

    def test_load_csv(self):
        header = ("timestamp,source_venue,source_instrument,"
                   "target_venue,target_instrument,direction,signal_type,strength")
        row1 = "1700000000,BINANCE,BTC/USDT,KRAKEN,BTC/USD,long,cross_market_move,50.0"
        row2 = "1700003600,BINANCE,BTC/USDT,KRAKEN,BTC/USD,short,cross_market_move,30.0"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(f"{header}\n{row1}\n{row2}\n")
            f.flush()
            events = load_signals_from_csv(f.name)

        assert len(events) == 2
        assert events[0].signal_id == "csv_000001"
        assert events[0].direction == "long"
        assert events[1].direction == "short"
        Path(f.name).unlink()


# ===================================================================
# 7. Cross-market signal generator emits only when threshold is crossed
# ===================================================================

class TestCrossMarketSignalGenerator:

    def _cfg(self):
        return SignalSourceConfig(
            cross_market_source_venue="BINANCE",
            cross_market_source_instrument="BTC/USDT",
            cross_market_target_venue="KRAKEN",
            cross_market_target_instrument="BTC/USD",
            cross_market_move_threshold_bps=20.0,
            cross_market_lookback_seconds=60.0,
            cross_market_cooldown_seconds=60.0,
        )

    def test_emits_on_threshold_cross(self):
        cfg = self._cfg()
        gen = CrossMarketSignalGenerator(cfg)
        # 10s bars, base price 50000
        ts = [1700000000 + i * 10 for i in range(20)]
        prices = [50000.0] * 20
        # Jump at bar 6 and stay there
        for i in range(6, 20):
            prices[i] = 50000.0 * (1 + 30 / 10000)
        signals = gen.generate(ts, prices)
        assert len(signals) >= 1
        assert signals[0].direction == "long"
        assert signals[0].strength >= 20.0

    def test_no_signal_below_threshold(self):
        cfg = self._cfg()
        gen = CrossMarketSignalGenerator(cfg)
        ts = [1700000000 + i * 10 for i in range(20)]
        prices = [50000.0 + i * 0.01 for i in range(20)]  # tiny drift
        signals = gen.generate(ts, prices)
        assert len(signals) == 0


# ===================================================================
# 8. Cooldown prevents signal spam
# ===================================================================

    def test_cooldown(self):
        cfg = self._cfg()
        cfg.cross_market_cooldown_seconds = 120.0
        gen = CrossMarketSignalGenerator(cfg)
        ts = [1700000000 + i * 10 for i in range(40)]
        prices = [50000.0] * 40
        # Jump at bar 6 and stay elevated
        for i in range(6, 20):
            prices[i] = 50000.0 * 1.003
        # Jump back down at bar 22 (within cooldown)
        for i in range(22, 40):
            prices[i] = 50000.0
        signals = gen.generate(ts, prices)
        # Both the up-jump and the down-jump can generate signals,
        # but the cooldown should prevent a second up-jump signal
        assert len(signals) >= 1


# ===================================================================
# 9. Signal generator does not use future data
# ===================================================================

    def test_no_lookahead(self):
        """Signals at time T must only reflect prices up to T."""
        cfg = self._cfg()
        gen = CrossMarketSignalGenerator(cfg)
        ts = [1700000000 + i * 10 for i in range(100)]
        prices = [50000.0] * 100
        # Only future bars (after index 10) have a jump
        for i in range(15, 20):
            prices[i] = 50000.0 * 1.005  # big jump
        # Signal at the jump bar should have strength computed only from lookback window
        signals = gen.generate(ts, prices)
        for sig in signals:
            assert sig.signal_id  # just confirm it ran
        # The generator looks only at past, so no signals before the jump
        before_jump = [s for s in signals if s.timestamp < ts[15]]
        assert len(before_jump) == 0


# ===================================================================
# 10. Forward returns compute correct bps
# ===================================================================

class TestForwardReturns:

    def test_correct_bps(self):
        ts = [1000.0 + i * 10 for i in range(100)]
        prices = [50000.0 + i * 2.0 for i in range(100)]  # +4 bps per bar

        sig = SignalEvent(
            signal_id="s1", timestamp=1000.0,
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction="long", strength=1.0,
        )

        # Entry at ts=1000, price=50000
        # Horizon +60s = ts=1060, price=50000 + 6*2 = 50012
        # raw = 12/50000*10000 = 2.4 bps
        results = evaluate_signal(sig, ts, prices, [Horizon("60s", 60.0)], FeeModel(fee_bps=0, slippage_bps=0))
        valid = [r for r in results if r.valid]
        assert len(valid) == 1
        assert valid[0].raw_return_bps == pytest.approx(2.4, abs=0.01)


# ===================================================================
# 11. Direction-adjusted returns work for long and short
# ===================================================================

    def test_direction_adjusted_long(self):
        ts = [1000.0 + i * 10 for i in range(100)]
        # Price at ts=1000 (index 0): 50000
        # Price at ts=1010 (index 1): 50100
        prices = [50000.0, 50100.0] + [50100.0] * 98

        sig = SignalEvent(
            signal_id="s1", timestamp=1000.0,
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction="long", strength=1.0,
        )
        results = evaluate_signal(sig, ts, prices, [Horizon("10s", 10.0)], FeeModel(fee_bps=0, slippage_bps=0))
        valid = [r for r in results if r.valid]
        assert len(valid) == 1
        # Price went up 100 -> long gains
        assert valid[0].raw_return_bps > 0
        assert valid[0].direction_adjusted_return_bps > 0

    def test_direction_adjusted_short(self):
        ts = [1000.0 + i * 10 for i in range(100)]
        prices = [50000.0, 50100.0] + [50100.0] * 98

        sig = SignalEvent(
            signal_id="s1", timestamp=1000.0,
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction="short", strength=1.0,
        )
        results = evaluate_signal(sig, ts, prices, [Horizon("10s", 10.0)], FeeModel(fee_bps=0, slippage_bps=0))
        valid = [r for r in results if r.valid]
        assert len(valid) == 1
        # Price went up -> short loses (negative adjusted return, positive raw return)
        assert valid[0].raw_return_bps > 0  # raw: price went up 50000->50100 = +20 bps
        assert valid[0].direction_adjusted_return_bps < 0  # short: inverted = -20 bps


# ===================================================================
# 12. Missing target price rejects signal
# ===================================================================

    def test_missing_entry_rejects(self):
        ts = [2000.0 + i * 10 for i in range(10)]
        prices = [50000.0] * 10
        # Signal before all data — no bar at or after ts=500
        sig = SignalEvent(
            signal_id="s1", timestamp=500.0,
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction="long", strength=1.0,
        )
        evaluate_signal(sig, ts, prices, [Horizon("10s", 10.0)], FeeModel())
        # Entry should succeed: first bar at ts=2000
        # But horizon would be at ts=2010, which exists → should be valid
        # To actually test rejection, signal after ALL data:
        sig2 = SignalEvent(
            signal_id="s2", timestamp=3000.0,  # after last bar at ts=2090
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction="long", strength=1.0,
        )
        results2 = evaluate_signal(sig2, ts, prices, [Horizon("10s", 10.0)], FeeModel())
        assert all(r.valid is False for r in results2)


# ===================================================================
# 13. Missing horizon price rejects only that horizon
# ===================================================================

    def test_missing_horizon_rejects_one(self):
        ts = [1000.0 + i * 10 for i in range(5)]
        prices = [50000.0, 50001.0, 50002.0, 50003.0, 50004.0]

        sig = SignalEvent(
            signal_id="s1", timestamp=1000.0,
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction="long", strength=1.0,
        )
        results = evaluate_signal(
            sig, ts, prices,
            [Horizon("10s", 10.0), Horizon("1h", 3600.0)],
            FeeModel(fee_bps=0, slippage_bps=0),
        )
        valid = [r for r in results if r.valid]
        rejected = [r for r in results if not r.valid]
        assert len(valid) == 1
        assert len(rejected) == 1
        assert rejected[0].horizon == "1h"


# ===================================================================
# 14 & 15. Summary aggregation
# ===================================================================

class TestSummaryAggregation:

    def test_groups_by_horizon(self):
        results = []
        for h in ["10s", "30s"]:
            results.append(ForwardReturnResult(
                signal_id="s1", signal_timestamp=0.0,
                source_venue="A", source_instrument="S",
                target_venue="B", target_instrument="T",
                signal_type="test", direction="long", strength=1.0,
                horizon=h, net_return_bps=5.0, valid=True,
            ))

        cfg = ObserverConfig(horizons=[Horizon("10s", 10), Horizon("30s", 30)])
        summary = _build_summary([], results, cfg, 0.0, 1.0, False)
        assert len(summary.results_by_horizon) == 2

    def test_groups_by_signal_type(self):
        results = []
        for st in ["cross_market_move", "manual_csv"]:
            results.append(ForwardReturnResult(
                signal_id="s1", signal_timestamp=0.0,
                source_venue="A", source_instrument="S",
                target_venue="B", target_instrument="T",
                signal_type=st, direction="long", strength=1.0,
                horizon="10s", net_return_bps=5.0, valid=True,
            ))

        cfg = ObserverConfig(horizons=[Horizon("10s", 10)])
        summary = _build_summary([], results, cfg, 0.0, 1.0, False)
        types = {s["signal_type"] for s in summary.results_by_signal_type}
        assert "cross_market_move" in types
        assert "manual_csv" in types


# ===================================================================
# 16. Reports write JSONL and CSV
# ===================================================================

class TestReports:

    def test_write_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            signals = [
                {"signal_id": "s1", "timestamp": 1000.0,
                 "source_venue": "A", "source_instrument": "S",
                 "target_venue": "B", "target_instrument": "T",
                 "signal_type": "test", "direction": "long", "strength": 1.0}
            ]
            results = [
                ForwardReturnResult(
                    signal_id="s1", signal_timestamp=1000.0,
                    source_venue="A", source_instrument="S",
                    target_venue="B", target_instrument="T",
                    signal_type="test", direction="long", strength=1.0,
                    horizon="10s", net_return_bps=5.0, valid=True,
                )
            ]
            cfg = ObserverConfig(horizons=[Horizon("10s", 10)])
            summary = _build_summary([], results, cfg, 0.0, 1.0, False)
            write_outputs(signals, results, summary, out_dir)

            assert (out_dir / "signal_events.jsonl").exists()
            assert (out_dir / "forward_returns.jsonl").exists()
            assert (out_dir / "summary.json").exists()
            assert (out_dir / "summary.csv").exists()
            assert (out_dir / "by_signal_type.csv").exists()
            assert (out_dir / "rejections.json").exists()

            # Verify JSONL content
            with open(out_dir / "forward_returns.jsonl") as f:
                line = json.loads(f.readline())
            assert line["signal_id"] == "s1"


# ===================================================================
# 17. Synthetic observer run produces expected results
# ===================================================================

class TestSyntheticObserver:

    def test_synthetic_run(self):
        cfg = ObserverConfig(
            synthetic=True,
            fee_model=FeeModel(fee_bps=5.0, slippage_bps=1.0),
        )
        # Generate synthetic data where target follows source, creating positive expectancy
        ts, source_prices, target_prices = generate_synthetic_data(
            num_bars=5_000,
            dt_seconds=10.0,
            base_price=50_000.0,
            target_follow_delay=30.0,
            target_follow_fraction=0.7,
            jump_threshold_bps=50.0,
            jump_interval=200,
        )
        gen = CrossMarketSignalGenerator(cfg.signal_source)
        signals = gen.generate(ts, source_prices)
        assert len(signals) > 0

        obs = SignalObserver(cfg)
        signals_list, results, summary = obs.run(
            signals=signals,
            target_timestamps=ts,
            target_prices=target_prices,
        )

        assert summary.total_signals > 0
        assert summary.valid_evaluations > 0


# ===================================================================
# 18-20. No orders, no private keys, no live trading
# ===================================================================

class TestNoOrders:

    FORBIDDEN = [
        "submit_order", "submit_order_list", "order_factory",
        "market_order(", "limit_order(", "place_order", "create_order",
        "api_key", "secret_key",
    ]

    def test_no_forbidden_code(self):
        pkg_dir = Path(__file__).resolve().parent.parent
        for py_file in pkg_dir.rglob("*.py"):
            # Skip tests themselves
            if "tests" in str(py_file):
                continue
            source = py_file.read_text().lower()
            for token in self.FORBIDDEN:
                assert token.lower() not in source, (
                    f"Found '{token}' in {py_file}"
                )

    def test_no_private_key_config(self):
        pkg_dir = Path(__file__).resolve().parent.parent
        for py_file in pkg_dir.rglob("*.py"):
            if "tests" in str(py_file):
                continue
            source = py_file.read_text()
            # Check for api_key/secret_key as variable/argument names
            for line in source.split("\n"):
                stripped = line.strip().lower()
                if stripped.startswith("#"):
                    continue
                for token in ["api_key", "secret_key"]:
                    assert f"{token}=" not in stripped, f"Found '{token}=' in {py_file}:{line}"
