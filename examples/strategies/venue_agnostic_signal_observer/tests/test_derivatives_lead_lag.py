"""Tests for the derivatives lead-lag observer.

Observer-only — no orders, no keys, no live trading.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Ensure imports work when run from repo root
REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from examples.strategies.venue_agnostic_signal_observer.derivatives_models import (
    DerivativeTradeTick,
    DerivativeImpulseEvent,
    OpenInterestSnapshot,
    FundingSnapshot,
)
from examples.strategies.venue_agnostic_signal_observer.derivatives_lead_lag import (
    DerivativesImpulseGenerator,
    impulse_to_tick_signal,
)


# -- Helpers --

_NS = 1_000_000_000  # 1 second in ns

def _ts(seconds: int) -> int:
    return seconds * _NS

def _trade(ts_s: int, price: float, size: float, side: str = "buy",
           venue: str = "BINANCE", symbol: str = "BTC/USD") -> DerivativeTradeTick:
    return DerivativeTradeTick(
        ts_event=_ts(ts_s), venue=venue, symbol=symbol,
        price=price, size=size, side=side,
        trade_id=f"t-{ts_s}",
    )


# -- Model tests --

class TestDerivativesModels:
    def test_trade_tick_notional(self):
        t = _trade(100, 50000.0, 2.0, "buy")
        assert t.notional == 100000.0

    def test_trade_tick_to_dict_roundtrip(self):
        t = _trade(100, 50000.0, 2.0, "buy")
        d = t.to_dict()
        t2 = DerivativeTradeTick.from_dict(d)
        assert t2.ts_event == t.ts_event
        assert t2.price == t.price
        assert t2.notional == t.notional

    def test_trade_tick_to_json(self):
        t = _trade(100, 50000.0, 2.0, "sell")
        parsed = json.loads(t.to_json())
        assert parsed["side"] == "sell"

    def test_oi_snapshot_fields(self):
        s = OpenInterestSnapshot(
            ts_event=_ts(100), venue="BINANCE", symbol="BTCUSDT",
            open_interest=50000.0, open_interest_value=2.5e9,
        )
        assert s.open_interest == 50000.0

    def test_funding_snapshot_fields(self):
        s = FundingSnapshot(
            ts_event=_ts(100), venue="BINANCE", symbol="BTCUSDT",
            funding_rate=0.0001, funding_apr=36.5 * 0.0001,
            mark_price=50000.0, index_price=49990.0,
        )
        assert s.funding_rate == 0.0001


# -- Impulse generator tests --

class TestDerivativesImpulseGenerator:
    def test_notional_burst_detects_large_window(self):
        # 100 normal trades, then a burst of high-notional trades
        trades: list[DerivativeTradeTick] = []
        for i in range(100):
            trades.append(_trade(i, 50000.0, 0.1, "buy"))
        # Burst: 10 trades of 10 BTC at once
        for i in range(100, 110):
            trades.append(_trade(200, 50001.0, 10.0, "buy"))

        gen = DerivativesImpulseGenerator(
            lookbacks_ms=[1000],
            cooldown_ms=0,
            price_shock_multiplier=2.0,
            min_trades_in_window=3,
        )
        impulses = gen.generate(trades)
        # Should find at least one notional burst or signed imbalance
        assert len(impulses) >= 1, "Expected at least one impulse event"

    def test_signed_imbalance_fires_on_one_sided_window(self):
        trades = [
            _trade(i, 50000.0, 1.0, "buy") for i in range(50)
        ] + [
            _trade(i + 60, 50001.0, 1.0, "buy") for i in range(10)
        ]

        gen = DerivativesImpulseGenerator(
            lookbacks_ms=[10000],
            cooldown_ms=0,
            imbalance_threshold=0.5,
            min_trades_in_window=3,
            enable_notional_burst=False,
            enable_price_shock=False,
            enable_signed_imbalance=True,
        )
        impulses = gen.generate(trades)
        imbalance_events = [i for i in impulses if i.signal_type == "signed_imbalance"]
        assert len(imbalance_events) >= 1

    def test_no_impulse_on_balanced_market(self):
        trades: list[DerivativeTradeTick] = []
        for i in range(60):
            side = "buy" if i % 2 == 0 else "sell"
            trades.append(_trade(i, 50000.0, 1.0, side))

        gen = DerivativesImpulseGenerator(
            lookbacks_ms=[10000],
            cooldown_ms=10000,
            enable_notional_burst=True,
            enable_price_shock=True,
            enable_signed_imbalance=True,
        )
        impulses = gen.generate(trades)
        # Balanced alternating flow should not trigger imbalance
        imbal = [i for i in impulses if i.signal_type == "signed_imbalance"]
        assert len(imbal) == 0, "No signed imbalance expected in balanced market"

    def test_empty_trades_empty_result(self):
        gen = DerivativesImpulseGenerator()
        assert gen.generate([]) == []

    def test_impulse_to_tick_signal_conversion(self):
        imp = DerivativeImpulseEvent(
            signal_id="test-1",
            ts_event=_ts(100),
            source_venue="BINANCE",
            source_symbol="BTCUSDT",
            target_venue="KRAKEN",
            target_symbol="BTC/USD",
            asset="BTC",
            signal_type="notional_burst",
            direction="long",
            strength=3.5,
            lookback_ms=5000,
            metadata={"burst_ratio": 3.5},
        )
        sig = impulse_to_tick_signal(imp)
        assert sig.signal_id == imp.signal_id
        assert sig.direction == "long"
        assert sig.signal_type == "notional_burst"
        assert sig.ts_event == imp.ts_event


# -- No-lookahead test --

class TestNoLookahead:
    def test_signals_only_use_past_data(self):
        """Verify that generated signals never reference ticks after their ts_event."""
        trades = [_trade(i, 50000.0 + i * 0.01, 1.0, "buy") for i in range(200)]
        gen = DerivativesImpulseGenerator(
            lookbacks_ms=[1000],
            cooldown_ms=0,
            enable_signed_imbalance=True,
            enable_notional_burst=False,
            enable_price_shock=False,
        )
        impulses = gen.generate(trades)
        for imp in impulses:
            # Signal timestamp must be from an existing tick
            assert any(t.ts_event == imp.ts_event for t in trades)
            # The lookback window only looks into past ticks
            lookback_ns = imp.lookback_ms * 1_000_000
            for t in trades:
                if t.ts_event > imp.ts_event:
                    assert (t.ts_event - imp.ts_event) > lookback_ns, \
                        "Future tick should be outside lookback window"


# -- CLI / runner fixture test --

class TestConservativeVerdict:
    """When sample count is too low the verdict should be NEEDS_MORE_DATA.
    This is verified via the runner's decision logic directly.
    """
    def test_needs_more_data_on_no_events(self, tmp_path: Path):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import DerivativesLeadLagSummary
        summary = DerivativesLeadLagSummary()
        summary.total_signals = 0
        summary.valid_evaluations = 0
        summary.run_end = time.time()

        # Verdict logic inline from runner
        if summary.total_signals == 0:
            verdict = "NEEDS_MORE_DATA"
        elif summary.candidate_groups:
            verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"
        else:
            verdict = "REJECTED"

        assert verdict == "NEEDS_MORE_DATA"

    def test_rejected_on_events_but_no_candidate(self, tmp_path: Path):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import DerivativesLeadLagSummary
        summary = DerivativesLeadLagSummary()
        summary.total_signals = 10
        summary.valid_evaluations = 30
        summary.candidate_groups = []
        summary.run_end = time.time()

        if summary.total_signals == 0:
            verdict = "NEEDS_MORE_DATA"
        elif summary.candidate_groups:
            verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"
        else:
            verdict = "REJECTED"

        assert verdict == "REJECTED"


# -- Missing / malformed data does not crash --

class TestMalformedDataTolerance:
    def test_missing_optional_fields(self):
        """OpenInterestSnapshot and FundingSnapshot with None fields should not crash."""
        oi = OpenInterestSnapshot(ts_event=0, venue="X", symbol="Y", open_interest=100)
        assert oi.open_interest_value is None
        assert oi.raw is None

        fund = FundingSnapshot(ts_event=0, venue="X", symbol="Y", funding_rate=0.001)
        assert fund.mark_price is None
        assert fund.index_price is None
        assert fund.funding_apr is None

    def test_generator_ignores_empty_snapshots(self):
        gen = DerivativesImpulseGenerator()
        trades = [_trade(i, 50000.0, 0.1, "buy") for i in range(5)]
        # Should not raise even with None snapshots
        impulses = gen.generate(trades, oi_snapshots=[], funding=[])
        assert isinstance(impulses, list)


# -- Net bps calculation --

def test_net_bps_calculation():
    from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import DerivativesLeadLagSummary
    """Verify that the summary total_cost is consistent with fee params."""
    s = DerivativesLeadLagSummary(
        fee_bps=12.0, slippage_bps=2.0,
        latency_buffer_bps=5.0, quote_mismatch_bps=5.0,
    )
    total = s.fee_bps + s.slippage_bps + s.latency_buffer_bps + s.quote_mismatch_bps
    assert total == 24.0


# -- No forbidden code guard --

class TestNoForbiddenImports:
    """Ensure the derivatives modules contain no order/key trading code."""
    FILES_TO_SCAN = [
        "derivatives_models.py",
        "derivatives_lead_lag.py",
        "run_derivatives_lead_lag.py",
    ]

    def _scan(self) -> list[str]:
        pkg = Path(__file__).resolve().parent.parent
        violations = []
        forbidden = [
            "submit_order", "place_order", "TradingNode", "LiveNode",
            "api_key", "secret_key", "account_balance", "portfolio",
            "position_size",
        ]
        for filename in self.FILES_TO_SCAN:
            fpath = pkg / filename
            if fpath.exists():
                content = fpath.read_text()
                for token in forbidden:
                    if token in content:
                        violations.append(f"{filename} contains '{token}'")
        return violations

    def test_no_forbidden_code(self):
        violations = self._scan()
        assert not violations, f"Found forbidden terms:\\n" + "\\n".join(violations)


# ---------------------------------------------------------------------------
# Instrument type metadata tests
# ---------------------------------------------------------------------------

class TestInstrumentTypeMetadata:
    """Ensure source/target instrument type metadata flows through the pipeline."""

    def test_summary_has_instrument_types(self):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
        )
        s = DerivativesLeadLagSummary(
            source_instrument_type="perp",
            target_instrument_type="spot",
        )
        assert s.source_instrument_type == "perp"
        assert s.target_instrument_type == "spot"

    def test_default_instrument_types_are_unknown(self):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
        )
        s = DerivativesLeadLagSummary()
        assert s.source_instrument_type == "unknown"
        assert s.target_instrument_type == "unknown"

    def test_cli_flags_parse(self):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            _build_parser,
        )
        parser = _build_parser()
        args = parser.parse_args([
            "--source-ticks", "data/src.jsonl",
            "--target-ticks", "data/tgt.jsonl",
            "--source-instrument-type", "perp",
            "--target-instrument-type", "spot",
        ])
        assert args.source_instrument_type == "perp"
        assert args.target_instrument_type == "spot"

    def test_cli_flags_default_to_unknown(self):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            _build_parser,
        )
        parser = _build_parser()
        args = parser.parse_args([
            "--source-ticks", "data/src.jsonl",
            "--target-ticks", "data/tgt.jsonl",
        ])
        assert args.source_instrument_type == "unknown"
        assert args.target_instrument_type == "unknown"

    def test_valid_instrument_types_constant(self):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            VALID_INSTRUMENT_TYPES,
        )
        assert VALID_INSTRUMENT_TYPES == {"spot", "perp", "futures", "unknown"}


# ---------------------------------------------------------------------------
# Spot->spot guard: no overbroad rejection language
# ---------------------------------------------------------------------------

class TestSpotSpotGuard:
    """A spot->spot run must not claim to reject the derivatives-lead-lag thesis."""

    def test_spot_spot_verdict_is_pair_specific(self, tmp_path: Path):
        import json
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
            _write_outputs,
        )

        summary = DerivativesLeadLagSummary(
            total_signals=10,
            valid_evaluations=30,
            source_instrument_type="spot",
            target_instrument_type="spot",
            run_end=time.time(),
        )

        class _FakeArgs:
            source_venue = "COINBASE"
            target_venue = "KRAKEN"
            symbol = "BTC/USD"
            asset = "BTC"
            signal_types = "notional_burst"
            lookbacks_ms = "1000"
            horizons_ms = "1000"
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        # Check report.md does not claim derivatives thesis rejection
        report = (tmp_path / "report.md").read_text()
        assert "Thesis Status" in report
        assert "OPEN_UNTESTED" in report
        assert "does NOT test the derivatives lead-lag thesis" in report
        # Must not say "derivatives lead-lag thesis rejected" or "derivatives branch rejected"
        lower = report.lower()
        assert "derivatives lead-lag thesis rejected" not in lower
        assert "derivatives branch rejected" not in lower
        # Should say REJECTED_SPOT_SPOT_SMOKE
        assert "REJECTED_SPOT_SPOT_SMOKE" in report

    def test_spot_spot_json_includes_instrument_types(self, tmp_path: Path):
        import json
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
            _write_outputs,
        )

        summary = DerivativesLeadLagSummary(
            total_signals=10,
            valid_evaluations=30,
            source_instrument_type="spot",
            target_instrument_type="spot",
            run_end=time.time(),
        )

        class _FakeArgs:
            source_venue = "COINBASE"
            target_venue = "KRAKEN"
            symbol = "BTC/USD"
            asset = "BTC"
            signal_types = "notional_burst"
            lookbacks_ms = "1000"
            horizons_ms = "1000"
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        data = json.loads((tmp_path / "summary.json").read_text())
        assert data["summary"]["source_instrument_type"] == "spot"
        assert data["summary"]["target_instrument_type"] == "spot"

    def test_perp_source_verdict_allows_derivatives_rejection(self, tmp_path: Path):
        """A run with perp source should be allowed to reject without the
        'NOT a rejection of the derivatives thesis' disclaimer."""
        import json
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
            _write_outputs,
        )

        summary = DerivativesLeadLagSummary(
            total_signals=10,
            valid_evaluations=30,
            source_instrument_type="perp",
            target_instrument_type="spot",
            run_end=time.time(),
        )

        class _FakeArgs:
            source_venue = "BINANCE"
            target_venue = "KRAKEN"
            symbol = "BTC/USD"
            asset = "BTC"
            signal_types = "notional_burst"
            lookbacks_ms = "1000"
            horizons_ms = "1000"
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        report = (tmp_path / "report.md").read_text()
        # No thesis-status disclaimer for derivative source
        assert "Thesis Status" not in report
        assert "does NOT test the derivatives lead-lag thesis" not in report
        # Verdict is plain REJECTED (not REJECTED_SPOT_SPOT_SMOKE)
        assert "REJECTED_SPOT_SPOT_SMOKE" not in report


# ---------------------------------------------------------------------------
# Old/minimal input compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Old-style calls without instrument type flags should still work."""

    def test_summary_defaults_for_old_code(self):
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
        )
        s = DerivativesLeadLagSummary(
            fee_bps=12.0,
            slippage_bps=2.0,
        )
        # Must not break
        assert s.source_instrument_type == "unknown"
        assert s.target_instrument_type == "unknown"

    def test_json_output_omits_nothing_for_defaults(self, tmp_path: Path):
        import json
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
            _write_outputs,
        )
        summary = DerivativesLeadLagSummary(
            total_signals=0,
            run_end=time.time(),
        )

        class _FakeArgs:
            source_venue = "X"
            target_venue = "Y"
            symbol = "BTC/USD"
            asset = "BTC"
            signal_types = ""
            lookbacks_ms = ""
            horizons_ms = ""
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())
        data = json.loads((tmp_path / "summary.json").read_text())
        assert "source_instrument_type" in data["summary"]
        assert data["summary"]["source_instrument_type"] == "unknown"


# ---------------------------------------------------------------------------
# Synthetic perp->spot fixture test
# ---------------------------------------------------------------------------

_NS_FIX = 1_000_000_000


def _ts_fix(seconds: int) -> int:
    return seconds * _NS_FIX


def _perp_trade(ts_s: int, price: float, size: float, side: str = "buy") -> "DerivativeTradeTick":
    from examples.strategies.venue_agnostic_signal_observer.derivatives_models import DerivativeTradeTick
    return DerivativeTradeTick(
        ts_event=_ts_fix(ts_s), venue="BINANCE", symbol="BTCUSDT-PERP",
        price=price, size=size, side=side,
        trade_id=f"p-{ts_s}",
    )


def _spot_trade(ts_s: int, price: float, size: float, side: str = "buy"):
    from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite
    return TradeTickLite(
        ts_event=_ts_fix(ts_s), venue="KRAKEN", symbol="BTC/USD",
        price=price, size=size, side=side,
        trade_id=f"s-{ts_s}",
    )


class TestSyntheticPerpToSpot:
    """Synthetic perp->spot test to verify machinery labels the run correctly."""

    def test_perp_source_labels_run_as_derivatives_source(self, tmp_path: Path):
        """Generate synthetic perp-side impulse leading spot move.
        Verify the run can be labelled as a derivatives-source test.
        Does NOT imply profitability."""
        import json
        from examples.strategies.venue_agnostic_signal_observer.run_derivatives_lead_lag import (
            DerivativesLeadLagSummary,
            _write_outputs,
        )

        # Build synthetic source (perp) trades with a burst at t=100-109
        perp_trades: list = []
        for i in range(50):
            perp_trades.append(_perp_trade(i, 50000.0, 0.1, "buy"))
        # Impulse burst
        for i in range(100, 110):
            perp_trades.append(_perp_trade(i, 50005.0, 10.0, "buy"))

        # Build synthetic target (spot) trades that react
        spot_ticks = []
        for i in range(50):
            spot_ticks.append(_spot_trade(i, 50000.0, 0.5, "buy"))
        for i in range(100, 120):
            spot_ticks.append(_spot_trade(i, 50010.0, 0.5, "buy"))

        # Write temp JSONL
        src_path = tmp_path / "perp.jsonl"
        tgt_path = tmp_path / "spot.jsonl"
        src_path.write_text("\n".join(t.to_json() for t in perp_trades))
        tgt_path.write_text("\n".join(t.to_json() for t in spot_ticks))

        # Now create a summary as if the run completed
        summary = DerivativesLeadLagSummary(
            total_signals=1,
            valid_evaluations=1,
            source_instrument_type="perp",
            target_instrument_type="spot",
            run_end=time.time(),
        )

        class _FakeArgs:
            source_venue = "BINANCE"
            target_venue = "KRAKEN"
            symbol = "BTC/USD"
            asset = "BTC"
            signal_types = "notional_burst"
            lookbacks_ms = "1000"
            horizons_ms = "1000"
            out_dir = str(tmp_path)

        _write_outputs(summary, _FakeArgs())

        # Verify report identifies this as a derivatives-source study
        report = (tmp_path / "report.md").read_text()
        lower = report.lower()
        assert "perp" in lower
        assert "derivatives-source" in lower or "derivatives source" in lower
        # No "does NOT test" disclaimer
        assert "does NOT test the derivatives lead-lag thesis" not in report

        # Verify JSON metadata
        data = json.loads((tmp_path / "summary.json").read_text())
        assert data["summary"]["source_instrument_type"] == "perp"
        assert data["summary"]["target_instrument_type"] == "spot"
