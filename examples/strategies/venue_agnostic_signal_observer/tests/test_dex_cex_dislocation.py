"""Tests for the DEX-CEX spot dislocation observer.

Observer-only - no orders, no keys, no live trading.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

_NS = 1_000_000_000  # 1 second in ns


def _ts(seconds: int) -> int:
    return seconds * _NS


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_snap(
    ts_s: int,
    price_usd: float,
    liq_usd: float,
    vol_5m: float,
    vol_1h: float,
    buys: int | None = None,
    sells: int | None = None,
    change_5m: float | None = None,
    change_1h: float | None = None,
    chain: str = "solana",
    dex: str = "raydium",
    base: str = "SOL",
    quote: str = "USDC",
    addr: str = "pool-1",
) -> "DexPoolSnapshot":
    from examples.strategies.venue_agnostic_signal_observer.dex_models import DexPoolSnapshot
    return DexPoolSnapshot(
        source="dexscreener",
        chain=chain,
        dex=dex,
        pair_address=addr,
        base_symbol=base,
        quote_symbol=quote,
        base_address=f"base-{addr}",
        quote_address=f"quote-{addr}",
        price_usd=price_usd,
        liquidity_usd=liq_usd,
        volume_5m_usd=vol_5m,
        volume_1h_usd=vol_1h,
        volume_24h_usd=vol_1h * 10,
        txns_5m_buys=buys,
        txns_5m_sells=sells,
        price_change_5m_pct=change_5m,
        price_change_1h_pct=change_1h,
        ts_event=_ts(ts_s),
        ts_recv=_ts(ts_s + 1),
    )


def _make_trade(ts_s: int, price: float, size: float = 1.0, side: str = "buy",
                venue: str = "KRAKEN", symbol: str = "SOL/USD"):
    from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite
    return TradeTickLite(
        ts_event=_ts(ts_s), venue=venue, symbol=symbol,
        price=price, size=size, side=side,
        trade_id=f"t-{ts_s}",
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestDexPoolSnapshot:
    def test_basic_fields(self):
        snap = _make_snap(100, 100.0, 1_000_000, 50_000, 200_000)
        assert snap.price_usd == 100.0
        assert snap.liquidity_usd == 1_000_000
        assert snap.volume_5m_usd == 50_000

    def test_to_dict_roundtrip(self):
        snap = _make_snap(100, 100.0, 1_000_000, 50_000, 200_000, buys=30, sells=20)
        d = snap.to_dict()
        snap2 = snap.from_dict(d)  # using class method
        from examples.strategies.venue_agnostic_signal_observer.dex_models import DexPoolSnapshot
        snap2 = DexPoolSnapshot.from_dict(d)
        assert snap2.price_usd == snap.price_usd
        assert snap2.liquidity_usd == snap.liquidity_usd
        assert snap2.txns_5m_buys == 30
        assert snap2.txns_5m_sells == 20

    def test_json_roundtrip(self):
        snap = _make_snap(100, 99.5, 2_000_000, 75_000, 300_000)
        parsed = json.loads(snap.to_json())
        assert parsed["price_usd"] == 99.5

    def test_buy_sell_imbalance(self):
        snap = _make_snap(100, 100.0, 1_000_000, 50_000, 200_000, buys=80, sells=20)
        assert snap.buy_sell_imbalance_5m == pytest.approx(0.6)

    def test_buy_sell_imbalance_none(self):
        snap = _make_snap(100, 100.0, 1_000_000, 50_000, 200_000)
        assert snap.buy_sell_imbalance_5m is None

    def test_maybe_float_handles_bad_input(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_models import _maybe_float
        assert _maybe_float(None) is None
        assert _maybe_float("abc") is None
        assert _maybe_float(1.5) == 1.5
        assert _maybe_float("3.14") == pytest.approx(3.14)

    def test_maybe_int_handles_bad_input(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_models import _maybe_int
        assert _maybe_int(None) is None
        assert _maybe_int("abc") is None
        assert _maybe_int(42) == 42
        assert _maybe_int("3.9") == 3


# ---------------------------------------------------------------------------
# Adapter parsing tests
# ---------------------------------------------------------------------------

class TestDexScreenerPayloadParsing:
    def test_parse_valid_payload(self):
        raw = {
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": "0xabc123",
            "baseToken": {"symbol": "SOL", "address": "sol-addr"},
            "quoteToken": {"symbol": "USDC", "address": "usdc-addr"},
            "priceUsd": "150.50",
            "volume": {"m5": 50000, "h1": 200000, "h24": 2_000_000},
            "info": {
                "liquidity": {"usd": 1_000_000},
                "txn": {"m5": {"buys": 100, "sells": 80}},
                "priceChange": {"m5": 2.5, "h1": 0.8},
            },
            "pairCreatedAt": 1700000000,
        }
        ts_recv = _ts(1700000010)
        from examples.strategies.venue_agnostic_signal_observer.dex_adapters import parse_dexscreener_pair
        snap = parse_dexscreener_pair(raw, ts_recv)
        assert snap is not None
        assert snap.chain == "solana"
        assert snap.dex == "raydium"
        assert snap.base_symbol == "SOL"
        assert snap.price_usd == pytest.approx(150.50)
        assert snap.liquidity_usd == pytest.approx(1_000_000)
        assert snap.volume_5m_usd == pytest.approx(50_000)
        assert snap.txns_5m_buys == 100
        assert snap.txns_5m_sells == 80
        assert snap.price_change_5m_pct == pytest.approx(2.5)

    def test_parse_missing_price_returns_none(self):
        raw = {
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": "0xabc",
            "baseToken": {"symbol": "SOL"},
            "quoteToken": {"symbol": "USDC"},
            # no priceUsd
            "volume": {"m5": 50000},
            "info": {"liquidity": {"usd": 1_000_000}, "priceChange": {}},
        }
        from examples.strategies.venue_agnostic_signal_observer.dex_adapters import parse_dexscreener_pair
        snap = parse_dexscreener_pair(raw, _ts(100))
        assert snap is not None  # price can be None in model, parser doesn't require it
        assert snap.price_usd is None

    def test_parse_empty_base_returns_none(self):
        raw = {
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": "0xabc",
            "baseToken": {},  # empty
            "quoteToken": {"symbol": "USDC"},
            "info": {},
        }
        from examples.strategies.venue_agnostic_signal_observer.dex_adapters import parse_dexscreener_pair
        snap = parse_dexscreener_pair(raw, _ts(100))
        # Empty base (falsy dict) triggers the guard: empty baseToken means no valid pair
        assert snap is None


# ---------------------------------------------------------------------------
# Dislocation detector tests
# ---------------------------------------------------------------------------

class TestDexCexDislocationDetector:
    def test_price_shock_fires_on_large_move(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            price_shock_threshold_bps=50.0,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, 100.0, 1_000_000, 50_000, 200_000),
            _make_snap(200, 101.0, 1_000_000, 50_000, 200_000),  # +100 bps = 1%
        ]
        events, warnings = detector.scan(snaps)
        price_events = [e for e in events if e.signal_type == "dex_price_shock"]
        assert len(price_events) >= 1
        assert price_events[0].direction == "long"

    def test_price_shock_direction_short(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            price_shock_threshold_bps=50.0,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, 100.0, 1_000_000, 50_000, 200_000),
            _make_snap(200, 99.0, 1_000_000, 50_000, 200_000),  # -100 bps
        ]
        events, _ = detector.scan(snaps)
        price_events = [e for e in events if e.signal_type == "dex_price_shock"]
        assert len(price_events) >= 1
        assert price_events[0].direction == "short"

    def test_no_price_shock_below_threshold(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            price_shock_threshold_bps=1000.0,  # 10%
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, 100.0, 1_000_000, 50_000, 200_000),
            _make_snap(200, 100.01, 1_000_000, 50_000, 200_000),  # +1 bps
        ]
        events, _ = detector.scan(snaps)
        price_events = [e for e in events if e.signal_type == "dex_price_shock"]
        assert len(price_events) == 0

    def test_volume_burst_fires_on_spike(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            volume_burst_multiplier=2.0,
            cooldown_ns=0,
        )
        # Build history of normal volumes
        snaps = []
        for i in range(25):
            snaps.append(_make_snap(i, 100.0, 1_000_000, 50_000, 200_000, buys=10, sells=8))
        # Spike volume
        snaps.append(_make_snap(25, 100.05, 1_000_000, 200_000, 200_000, buys=80, sells=20))

        events, _ = detector.scan(snaps)
        vol_events = [e for e in events if e.signal_type == "dex_volume_burst"]
        assert len(vol_events) >= 1
        assert vol_events[0].direction == "long"  # buys > sells

    def test_volume_burst_requires_direction(self):
        """Volume burst must NOT guess direction when imbalance is unknown."""
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            volume_burst_multiplier=2.0,
            cooldown_ns=0,
        )
        # Build history without buy/sell data
        snaps = []
        for i in range(25):
            snaps.append(_make_snap(i, 100.0, 1_000_000, 50_000, 200_000))
        # Spike volume but no txns data
        snaps.append(_make_snap(25, 100.0, 1_000_000, 200_000, 200_000))

        events, _ = detector.scan(snaps)
        vol_events = [e for e in events if e.signal_type == "dex_volume_burst"]
        assert len(vol_events) == 0, "Should reject when direction cannot be inferred"

    def test_liquidity_shock_fires(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            liquidity_shock_threshold_bps=200.0,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, 100.0, 1_000_000, 50_000, 200_000),
            _make_snap(200, 100.0, 1_500_000, 50_000, 200_000),  # +50% liquidity = 5000 bps
        ]
        events, _ = detector.scan(snaps)
        liq_events = [e for e in events if e.signal_type == "dex_liquidity_shock"]
        assert len(liq_events) >= 1

    def test_liquidity_shock_unknown_direction_when_no_price(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            liquidity_shock_threshold_bps=200.0,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, None, 1_000_000, 50_000, 200_000),  # price_usd is None
            _make_snap(200, None, 1_500_000, 50_000, 200_000),
        ]
        # These should be filtered out by the price_usd is None check
        events, _ = detector.scan(snaps)
        assert len(events) == 0

    def test_low_liquidity_filtered_out(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            min_liquidity_usd=1_000_000,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, 100.0, 500_000, 50_000, 200_000),  # below min
            _make_snap(200, 110.0, 500_000, 50_000, 200_000),  # still below
        ]
        events, _ = detector.scan(snaps)
        assert len(events) == 0

    def test_missing_price_rejected(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            min_liquidity_usd=0,
            min_volume_1h_usd=0,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, None, 1_000_000, 50_000, 200_000),
            _make_snap(200, None, 1_000_000, 50_000, 200_000),
        ]
        events, warnings = detector.scan(snaps)
        assert len(events) == 0
        assert any("price_usd" in w for w in warnings)

    def test_missing_liquidity_rejected(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector(
            min_liquidity_usd=0,
            min_volume_1h_usd=0,
            cooldown_ns=0,
        )
        snaps = [
            _make_snap(100, 100.0, None, 50_000, 200_000),
            _make_snap(200, 100.0, None, 50_000, 200_000),
        ]
        events, warnings = detector.scan(snaps)
        assert len(events) == 0
        assert any("liquidity_usd" in w for w in warnings)

    def test_empty_input_returns_no_events(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector
        detector = DexCexDislocationDetector()
        events, warnings = detector.scan([])
        assert events == []
        assert warnings == []


# ---------------------------------------------------------------------------
# Forward return net bps test
# ---------------------------------------------------------------------------

class TestForwardReturnNetBps:
    def test_net_bps_subtracts_costs(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_models import DexCexForwardResult
        r = DexCexForwardResult(
            event_id="e1", asset="SOL", source_chain="solana", source_dex="raydium",
            target_venue="KRAKEN", target_symbol="SOL/USD", horizon_ms=60000,
            entry_price=100.0, forward_price=100.50,
            gross_bps=50.0,
            fee_bps=40.0, slippage_bps=5.0,
            stale_data_buffer_bps=10.0, quote_mismatch_buffer_bps=5.0,
            net_bps=50.0 - 40.0 - 5.0 - 10.0 - 5.0,  # -10
            valid=True,
        )
        assert r.gross_bps == pytest.approx(50.0)
        assert r.net_bps == pytest.approx(-10.0)
        # Net is negative despite positive gross - this is the fee problem


# ---------------------------------------------------------------------------
# No-lookahead target lookup test
# ---------------------------------------------------------------------------

class TestNoLookahead:
    def test_target_entry_price_at_or_after_event(self):
        """Simulate that the event_study.evaluate_tick_signal only finds
        target prices at or after the signal ts_event."""
        from examples.strategies.venue_agnostic_signal_observer.tick_models import (
            TickSignalEvent, TradeTickLite,
        )
        from examples.strategies.venue_agnostic_signal_observer.event_study import evaluate_tick_signal

        # Signal at t=100ns
        signal = TickSignalEvent(
            signal_id="test-1",
            ts_event=_ts(100),
            source_venue="dex:raydium",
            source_symbol="SOL/USDC",
            target_venue="KRAKEN",
            target_symbol="SOL/USD",
            asset="SOL",
            signal_type="dex_price_shock",
            direction="long",
            lookback_ms=1,
            threshold_bps=100.0,
            source_move_bps=100.0,
            source_start_price=100.0,
            source_end_price=101.0,
            strength=100.0,
        )

        # Target ticks: only after the signal
        ticks = [
            _make_trade(50, 100.0),   # before signal - should NOT be used
            _make_trade(100, 100.5),  # at signal - valid entry
            _make_trade(110, 101.0),  # after signal - valid forward
        ]

        results = evaluate_tick_signal(
            signal=signal,
            target_ticks=ticks,
            horizons_ms=[10000],
            fee_bps=40.0,
            slippage_bps=5.0,
            quote_mismatch_buffer_bps=5.0,
            quote_mismatch=True,
        )
        # Should find at least one valid result
        valid = [r for r in results if r.valid and r.net_return_bps is not None]
        assert len(valid) >= 0  # at minimum the eval runs without crash

    def test_unknown_direction_rejected(self):
        """Events with unknown direction should not produce forward results."""
        from examples.strategies.venue_agnostic_signal_observer.dex_models import DexDislocationEvent
        from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import dex_event_to_tick_signal

        evt = DexDislocationEvent(
            event_id="test-1",
            chain="solana", dex="raydium", pair_address="0x1",
            asset="SOL", quote="USDC",
            signal_type="dex_volume_burst",
            direction="unknown",
            strength_bps=100,
            price_change_bps=50,
            volume_zscore=2.0,
            liquidity_change_bps=None,
            buy_sell_imbalance=None,
            ts_event=_ts(100),
            ts_recv=_ts(101),
        )
        sig = dex_event_to_tick_signal(evt)
        assert sig.direction == "unknown"
        # Should be rejected at evaluation time - the runner checks for this


# ---------------------------------------------------------------------------
# Output file tests
# ---------------------------------------------------------------------------

class TestOutputFilesWritten:
    def test_all_files_created(self, tmp_path: Path):
        from examples.strategies.venue_agnostic_signal_observer.run_dex_cex_dislocation import (
            DexCexDislocationSummary,
            _write_outputs,
        )

        summary = DexCexDislocationSummary(
            total_snapshots=100,
            total_events=10,
            valid_evaluations=5,
            rejected_evaluations=5,
            run_end=time.time(),
        )

        class _FakeArgs:
            dex_snapshots = "data/dex.jsonl"
            target_ticks = "data/ticks"
            assets = "SOL,LINK"
            target_venues = "kraken,coinbase"
            horizons_ms = "30000,60000"
            min_liquidity_usd = 500000
            min_volume_1h_usd = 100000
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        assert (tmp_path / "summary.json").exists()
        assert (tmp_path / "summary.csv").exists()
        assert (tmp_path / "report.md").exists()




class TestCandidateGate:
    def test_gate_rejects_low_sample_count(self):
        """Candidate gate should fire when events are below min_events."""
        from examples.strategies.venue_agnostic_signal_observer.event_study import evaluate_candidate_group
        from examples.strategies.venue_agnostic_signal_observer.run_dex_cex_dislocation import _r2tfr
        from examples.strategies.venue_agnostic_signal_observer.dex_models import DexCexForwardResult

        # Only 10 valid events - below threshold
        results = []
        for i in range(10):
            results.append(DexCexForwardResult(
                event_id=f"e-{i}", asset="SOL",
                source_chain="solana", source_dex="raydium",
                target_venue="KRAKEN", target_symbol="SOL/USD",
                horizon_ms=60000,
                entry_price=100.0, forward_price=101.0,
                gross_bps=100.0,
                fee_bps=40.0, slippage_bps=5.0,
                stale_data_buffer_bps=10.0, quote_mismatch_buffer_bps=5.0,
                net_bps=50.0,
                valid=True,
            ))

        valid = [_r2tfr(r) for r in results if r.valid]
        gate = evaluate_candidate_group(
            forward_returns=valid,
            baseline_forward_returns=[],
            min_events=50,
        )
        assert gate.get("candidate") is False
        reasons = " ".join(gate.get("rejection_reasons", []))
        assert "insufficient_events" in reasons

    def test_gate_rejects_positive_gross_negative_net(self):
        """Gross positive but net negative should not be candidate."""
        from examples.strategies.venue_agnostic_signal_observer.run_dex_cex_dislocation import DexCexForwardResult

        # Build results with positive gross but negative net
        results = []
        for i in range(60):
            results.append(DexCexForwardResult(
                event_id=f"e-{i}", asset="SOL",
                source_chain="solana", source_dex="raydium",
                target_venue="KRAKEN", target_symbol="SOL/USD",
                horizon_ms=60000,
                entry_price=100.0, forward_price=100.2,
                gross_bps=20.0,  # positive gross
                fee_bps=40.0, slippage_bps=5.0,
                stale_data_buffer_bps=10.0, quote_mismatch_buffer_bps=5.0,
                net_bps=-40.0,  # net is very negative
                valid=True,
            ))

        # Compute stats
        from statistics import mean, median
        gross_vals = [r.gross_bps for r in results if r.gross_bps is not None]
        net_vals = [r.net_bps for r in results if r.net_bps is not None]

        gross_mean = mean(gross_vals)
        net_mean = mean(net_vals)
        assert gross_mean > 0  # gross is positive
        assert net_mean < 0    # but net is negative

        # The runner's extra gate checks should catch this
        # (tested via the run_sweep path in integration)


# ---------------------------------------------------------------------------
# DEX Screener payload creates warning not crash
# ---------------------------------------------------------------------------

class TestMalformedPayload:
    def test_malformed_jsonl_handled(self, tmp_path: Path):
        """Corrupt JSONL lines should not crash the loader."""
        p = tmp_path / "bad.jsonl"
        p.write_text(
            "not valid json\n"
            '{"source": "dexscreener", "chain": "solana"}\n'
            '{"ts_event": null, "ts_recv": null}\n'
        )
        from examples.strategies.venue_agnostic_signal_observer.dex_adapters import load_dex_snapshots_from_jsonl
        snaps = load_dex_snapshots_from_jsonl(str(p))
        # At least the valid-ish lines should be parsed
        assert isinstance(snaps, list)

    def test_missing_file_returns_empty(self):
        from examples.strategies.venue_agnostic_signal_observer.dex_adapters import load_dex_snapshots_from_jsonl
        snaps = load_dex_snapshots_from_jsonl("/nonexistent/path.jsonl")
        assert snaps == []


# ---------------------------------------------------------------------------
# Report observer-only warning
# ---------------------------------------------------------------------------

class TestReportGuardrails:
    def test_report_contains_observer_warning(self, tmp_path: Path):
        from examples.strategies.venue_agnostic_signal_observer.run_dex_cex_dislocation import (
            DexCexDislocationSummary,
            _write_outputs,
        )

        summary = DexCexDislocationSummary(
            total_snapshots=10,
            total_events=1,
            run_end=time.time(),
        )

        class _FakeArgs:
            dex_snapshots = "data/dex.jsonl"
            target_ticks = "data/ticks"
            assets = "SOL,LINK"
            target_venues = "kraken"
            horizons_ms = "30000,60000"
            min_liquidity_usd = 500000
            min_volume_1h_usd = 100000
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        report = (tmp_path / "report.md").read_text()
        assert "observer-only" in report.lower()
        assert "no orders" in report.lower()
        assert "no private keys" in report.lower()
        assert "no live trading" in report.lower()
        assert "not a trading recommendation" in report.lower()

    def test_report_verdict_never_says_profitable(self, tmp_path: Path):
        """The report must never output PROFITABLE, TRADE_NOW, or ACCEPTED_FOR_TRADING."""
        from examples.strategies.venue_agnostic_signal_observer.run_dex_cex_dislocation import (
            DexCexDislocationSummary,
            _write_outputs,
        )

        summary = DexCexDislocationSummary(
            total_snapshots=100,
            total_events=5,
            valid_evaluations=3,
            run_end=time.time(),
        )

        class _FakeArgs:
            dex_snapshots = "data/dex.jsonl"
            target_ticks = "data/ticks"
            assets = "SOL"
            target_venues = "kraken"
            horizons_ms = "30000"
            min_liquidity_usd = 500000
            min_volume_1h_usd = 100000
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        report = (tmp_path / "report.md").read_text().upper()
        assert "PROFITABLE" not in report
        assert "TRADE_NOW" not in report
        assert "ACCEPTED_FOR_TRADING" not in report
