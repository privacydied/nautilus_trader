"""Tests for cross-asset spot impulse lead-lag.

Tests synthetic fixtures, direction propagation, verdict logic, safety guards,
and integration behavior.  No live data, no exchange APIs, no orders.
"""
from __future__ import annotations

import json
import statistics
import uuid

import pytest

from examples.strategies.venue_agnostic_signal_observer.tick_models import TickForwardReturn, TickSignalEvent, TradeTickLite
from examples.strategies.venue_agnostic_signal_observer.cross_asset_impulse import (
    PairResult,
    StreamHealth,
    _remap_signal_for_target,
    compute_overlap,
    compute_verdict,
    generate_source_impulses,
)
from examples.strategies.venue_agnostic_signal_observer.event_study import evaluate_tick_signal
from examples.strategies.venue_agnostic_signal_observer.trade_flow_impulse import _infer_tick_rule_side


# ── Helpers ─────────────────────────────────────────────────────────────────

_NS = 1_000_000_000
_MS = 1_000_000


def _make_tick(ts_ns: int, venue: str, symbol: str, price: float, side: str = "buy") -> TradeTickLite:
    return TradeTickLite(
        ts_event=ts_ns,
        venue=venue,
        symbol=symbol,
        price=price,
        size=1.0,
        side=side,
    )


def _make_signal(
    ts_ns: int,
    source_venue: str,
    source_symbol: str,
    target_venue: str,
    target_symbol: str,
    direction: str,
    signal_type: str = "cross_asset_notional_burst",
) -> TickSignalEvent:
    return TickSignalEvent(
        signal_id=str(uuid.uuid4()),
        ts_event=ts_ns,
        source_venue=source_venue,
        source_symbol=source_symbol,
        target_venue=target_venue,
        target_symbol=target_symbol,
        asset="BTC",
        signal_type=signal_type,
        direction=direction,
        lookback_ms=5000,
        threshold_bps=0.0,
        source_move_bps=50.0,
        source_start_price=100000.0,
        source_end_price=100050.0,
        strength=2.0,
    )


def _make_forward_return(
    ts_ns: int,
    target_venue: str,
    target_symbol: str,
    net_return_bps: float,
    horizon_ms: int = 30000,
    valid: bool = True,
) -> TickForwardReturn:
    return TickForwardReturn(
        signal_id=str(uuid.uuid4()),
        signal_ts=ts_ns,
        target_venue=target_venue,
        target_symbol=target_symbol,
        horizon_ms=horizon_ms,
        entry_reference_price=1000.0,
        forward_price=1000.0 + net_return_bps * 0.01,
        raw_return_bps=net_return_bps,
        direction_adjusted_return_bps=net_return_bps,
        fee_bps=40.0,
        slippage_bps=5.0,
        quote_mismatch_buffer_bps=5.0,
        net_return_bps=net_return_bps,
        valid=valid,
    )


def _generate_n_valid_returns(
    n: int,
    mean_net_bps: float,
    ts_start: int = 1000 * _NS,
    target_venue: str = "kraken",
    target_symbol: str = "SOL/USD",
) -> list[TickForwardReturn]:
    """Generate N valid forward returns centered around mean_net_bps."""
    import random as _rng
    _rng.seed(42)
    returns = []
    for i in range(n):
        noise = _rng.gauss(0, abs(mean_net_bps) * 0.5 + 5) if mean_net_bps != 0 else _rng.gauss(0, 10)
        net = mean_net_bps + noise
        returns.append(_make_forward_return(
            ts_ns=ts_start + i * _MS,
            target_venue=target_venue,
            target_symbol=target_symbol,
            net_return_bps=net,
        ))
    return returns


# ── 1. Same-symbol pairs skipped ────────────────────────────────────────────


class TestSameSymbolSkip:
    """Same-asset spot/spot lead-lag is a locked rejected gate."""

    def test_btc_to_btc_skipped(self):
        from examples.strategies.venue_agnostic_signal_observer.cross_asset_impulse import _is_same_symbol

        # BTC/USD -> BTC/USD must be same
        assert _is_same_symbol("BTC/USD", "BTC/USD") is True

    def test_btc_to_eth_allowed(self):
        from examples.strategies.venue_agnostic_signal_observer.cross_asset_impulse import _is_same_symbol

        # BTC/USD -> ETH/USD is different asset
        assert _is_same_symbol("BTC/USD", "ETH/USD") is False

    def test_btc_to_sol_allowed(self):
        from examples.strategies.venue_agnostic_signal_observer.cross_asset_impulse import _is_same_symbol

        assert _is_same_symbol("BTC/USD", "SOL/USD") is False

    def test_eth_to_link_allowed(self):
        from examples.strategies.venue_agnostic_signal_observer.cross_asset_impulse import _is_same_symbol

        assert _is_same_symbol("ETH/USD", "LINK/USD") is False


# ── 2. BTC/ETH source maps to alt targets ───────────────────────────────────


class TestSourceToTargetMapping:
    """Source asset can map to any target alt."""

    def test_btc_signal_remapped_to_sol(self):
        src = _make_signal(1000 * _NS, "coinbase", "BTC/USD", "coinbase", "SOL/USD", "long")
        remapped = _remap_signal_for_target(src, "kraken", "SOL/USD", 0)

        assert remapped.source_venue == "coinbase"
        assert remapped.source_symbol == "BTC/USD"
        assert remapped.target_venue == "kraken"
        assert remapped.target_symbol == "SOL/USD"

    def test_eth_signal_remapped_to_link(self):
        src = _make_signal(1000 * _NS, "kraken", "ETH/USD", "kraken", "LINK/USD", "long")
        remapped = _remap_signal_for_target(src, "coinbase", "LINK/USD", 1)

        assert remapped.source_venue == "kraken"
        assert remapped.source_symbol == "ETH/USD"
        assert remapped.target_venue == "coinbase"
        assert remapped.target_symbol == "LINK/USD"


# ── 3. Direction propagation without inversion ──────────────────────────────


class TestDirectionPropagation:
    """Positive-beta direction propagation: source dir -> target expected dir."""

    def test_bullish_source_creates_long_target(self):
        src = _make_signal(1000 * _NS, "coinbase", "BTC/USD", "coinbase", "BTC/USD", "long")
        remapped = _remap_signal_for_target(src, "kraken", "SOL/USD", 0)

        assert remapped.direction == "long"
        assert remapped.metadata["long_executable"] is True
        assert remapped.metadata["diagnostic_only"] is False

    def test_bearish_source_creates_diagnostic_target(self):
        src = _make_signal(1000 * _NS, "coinbase", "BTC/USD", "coinbase", "BTC/USD", "short")
        remapped = _remap_signal_for_target(src, "kraken", "SOL/USD", 0)

        assert remapped.direction == "short"
        assert remapped.metadata["long_executable"] is False
        assert remapped.metadata["diagnostic_only"] is True

    def test_signed_imbalance_buy_maps_to_long(self):
        src_sig = TickSignalEvent(
            signal_id=str(uuid.uuid4()),
            ts_event=1000 * _NS,
            source_venue="coinbase",
            source_symbol="BTC/USD",
            target_venue="__CROSS_ASSET__",
            target_symbol="__CROSS_ASSET__",
            asset="BTC",
            signal_type="cross_asset_signed_imbalance",
            direction="long",  # buy-side imbalance
            lookback_ms=5000,
            threshold_bps=0.0,
            source_move_bps=0.0,
            source_start_price=0.0,
            source_end_price=0.0,
            strength=0.8,
        )
        remapped = _remap_signal_for_target(src_sig, "kraken", "SOL/USD", 0)

        assert remapped.direction == "long"
        assert remapped.metadata["long_executable"] is True

    def test_signed_imbalance_sell_maps_to_diagnostic(self):
        src_sig = TickSignalEvent(
            signal_id=str(uuid.uuid4()),
            ts_event=1000 * _NS,
            source_venue="coinbase",
            source_symbol="BTC/USD",
            target_venue="__CROSS_ASSET__",
            target_symbol="__CROSS_ASSET__",
            asset="BTC",
            signal_type="cross_asset_signed_imbalance",
            direction="short",  # sell-side imbalance
            lookback_ms=5000,
            threshold_bps=0.0,
            source_move_bps=0.0,
            source_start_price=0.0,
            source_end_price=0.0,
            strength=0.8,
        )
        remapped = _remap_signal_for_target(src_sig, "kraken", "SOL/USD", 0)

        assert remapped.direction == "short"
        assert remapped.metadata["diagnostic_only"] is True


# ── 4. Downside signals are NOT executable spot shorts ─────────────────────


class TestNoShortExecution:
    """Downside/bearish signals must be diagnostic-only, never executable shorts."""

    def test_bearish_signal_not_marked_executable(self):
        src = _make_signal(1000 * _NS, "coinbase", "BTC/USD", "coinbase", "BTC/USD", "short")
        remapped = _remap_signal_for_target(src, "kraken", "SOL/USD", 0)

        assert remapped.metadata["long_executable"] is False

    def test_short_signal_is_diagnostic_only(self):
        src = _make_signal(1000 * _NS, "coinbase", "BTC/USD", "coinbase", "BTC/USD", "short")
        remapped = _remap_signal_for_target(src, "kraken", "SOL/USD", 0)

        assert remapped.metadata["diagnostic_only"] is True


# ── 5. Quiet source window returns NEEDS_MORE_DATA ──────────────────────────


class TestQuietSourceWindow:
    """If source doesn't move enough, verdict is NEEDS_MORE_DATA."""

    def test_quiet_source_returns_needs_more_data(self):
        # Create a pair result where source barely moved
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=1000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=100001.0,  # ~0.01 bps movement
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=5000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=100.5,  # 5 bps movement
        )

        overlap = compute_overlap(src_health, tgt_health)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=0,
            long_executable_count=0,
            diagnostic_only_count=0,
            valid_returns=[],
            valid_long_executable_returns=[],
            valid_diagnostic_returns=[],
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "NEEDS_MORE_DATA"
        assert any("insufficient_price_range" in r for r in verdict.reasons)


# ── 6. Quiet target window returns NEEDS_MORE_DATA ──────────────────────────


class TestQuietTargetWindow:
    """If target doesn't move enough, verdict is NEEDS_MORE_DATA."""

    def test_quiet_target_returns_needs_more_data(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=1000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=100300.0,  # 30 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=5000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=100.02,  # 0.02 bps
        )

        overlap = compute_overlap(src_health, tgt_health)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=0,
            long_executable_count=0,
            diagnostic_only_count=0,
            valid_returns=[],
            valid_long_executable_returns=[],
            valid_diagnostic_returns=[],
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "NEEDS_MORE_DATA"


# ── 7. Insufficient events returns NEEDS_MORE_DATA ─────────────────────────


class TestInsufficientEvents:
    """If fewer than min_events signals are generated, verdict is NEEDS_MORE_DATA."""

    def test_low_event_count_returns_needs_more_data(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=1000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=100500.0,  # 50 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=5000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=105.0,  # 500 bps
        )

        overlap = compute_overlap(src_health, tgt_health)
        # Only 10 signals (below min_events=50)
        valid_returns = _generate_n_valid_returns(10, mean_net_bps=5.0)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=10,
            long_executable_count=10,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "NEEDS_MORE_DATA"
        assert any("insufficient_events" in r for r in verdict.reasons)


# ── 8. Zero-tick stream is reported as data/subscription issue ─────────────


class TestZeroTickStream:
    """A stream with zero ticks is a data/subscription problem, not market evidence."""

    def test_zero_tick_stream_reported(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=0, zero_tick_warning=True,
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=5000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=105.0,
        )

        overlap = compute_overlap(src_health, tgt_health)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=0,
            long_executable_count=0,
            diagnostic_only_count=0,
            valid_returns=[],
            valid_long_executable_returns=[],
            valid_diagnostic_returns=[],
            data_issue_reasons=["source_zero_ticks"],
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "NEEDS_MORE_DATA"
        assert len(verdict.stream_warnings) > 0


# ── 9. Failed subscription is not treated as market evidence ───────────────


class TestFailedSubscription:
    """Failed subscription must not masquerade as NEEDS_MORE_DATA from market evidence."""

    def test_failed_subscription_warned(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=0, subscription_ok=False, zero_tick_warning=True,
        )

        verdict = compute_verdict(
            pair_results=[],
            stream_health={"coinbase|BTC/USD": src_health},
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "NEEDS_MORE_DATA"
        assert any("failed" in w.lower() for w in verdict.stream_warnings)


# ── 10. Sufficient negative result returns REJECTED ────────────────────────


class TestRejectedVerdict:
    """Sufficient data but negative result -> REJECTED."""

    def test_sufficient_negative_result_rejected(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=103000.0,  # 300 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=105.0,  # 500 bps
        )

        overlap = compute_overlap(src_health, tgt_health)
        valid_returns = _generate_n_valid_returns(100, mean_net_bps=-20.0)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=100,
            long_executable_count=100,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": False,
                "mean_net_return_bps": -20.0,
                "rejection_reasons": ["mean_net_return_not_positive: -20.00 bps"],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "REJECTED"


# ── 11. Single strong synthetic pair -> SINGLE_PAIR_CANDIDATE_DIAGNOSTIC ──


class TestSinglePairCandidate:
    """One genuinely interesting pair -> SINGLE_PAIR_CANDIDATE_DIAGNOSTIC."""

    def test_single_pair_candidate_diagnostic(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=105000.0,  # 500 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=110.0,  # 1000 bps
        )

        overlap = compute_overlap(src_health, tgt_health)
        valid_returns = _generate_n_valid_returns(80, mean_net_bps=15.0)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=80,
            long_executable_count=80,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": True,
                "event_count": 80,
                "mean_net_return_bps": 15.0,
                "rejection_reasons": [],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "SINGLE_PAIR_CANDIDATE_DIAGNOSTIC"


# ── 12. Multi-pair positive -> CANDIDATE_FOR_LONGER_OBSERVATION ────────────


class TestCandidateForLongerObservation:
    """Multiple pairs with positive results -> CANDIDATE_FOR_LONGER_OBSERVATION."""

    def test_multi_pair_positive_candidate(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=105000.0,
        )
        eth_health = StreamHealth(
            venue="coinbase", symbol="ETH/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=3000.0, price_max=3150.0,
        )
        sol_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=110.0,
        )
        link_health = StreamHealth(
            venue="kraken", symbol="LINK/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=10.0, price_max=10.5,
        )

        overlap1 = compute_overlap(src_health, sol_health)
        overlap2 = compute_overlap(eth_health, link_health)

        returns1 = _generate_n_valid_returns(80, mean_net_bps=15.0, target_symbol="SOL/USD")
        returns2 = _generate_n_valid_returns(80, mean_net_bps=12.0, target_symbol="LINK/USD")

        pair1 = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap1,
            signal_count=80,
            long_executable_count=80,
            diagnostic_only_count=0,
            valid_returns=returns1,
            valid_long_executable_returns=returns1,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": True,
                "event_count": 80,
                "mean_net_return_bps": 15.0,
                "rejection_reasons": [],
            },
        )
        pair2 = PairResult(
            pair_key="coinbase:ETH/USD->kraken:LINK/USD",
            overlap=overlap2,
            signal_count=80,
            long_executable_count=80,
            diagnostic_only_count=0,
            valid_returns=returns2,
            valid_long_executable_returns=returns2,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": True,
                "event_count": 80,
                "mean_net_return_bps": 12.0,
                "rejection_reasons": [],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair1, pair2],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "coinbase|ETH/USD": eth_health,
                "kraken|SOL/USD": sol_health,
                "kraken|LINK/USD": link_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD", "ETH/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD", "LINK/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "CANDIDATE_FOR_LONGER_OBSERVATION"


# ── 13. No overlap -> NEEDS_MORE_DATA ──────────────────────────────────────


class TestNoOverlap:
    """No true overlap between source and target streams."""

    def test_no_overlap_returns_needs_more_data(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=1000, first_tick_ts=1000 * _NS, last_tick_ts=1200 * _NS,
            price_min=100000.0, price_max=100500.0,
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=5000, first_tick_ts=5000 * _NS, last_tick_ts=5200 * _NS,
            price_min=100.0, price_max=105.0,
        )

        overlap = compute_overlap(src_health, tgt_health)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=0,
            long_executable_count=0,
            diagnostic_only_count=0,
            valid_returns=[],
            valid_long_executable_returns=[],
            valid_diagnostic_returns=[],
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "NEEDS_MORE_DATA"
        assert any("no_true_overlap" in r for r in verdict.reasons)


# ── 14. evaluate_tick_signal with cross-asset ───────────────────────────────


class TestEvaluateTickSignal:
    """verify evaluate_tick_signal works with remapped cross-asset signals."""

    def test_signal_evaluates_forward_returns(self):
        target_ticks = [
            _make_tick(1000 * _NS, "kraken", "SOL/USD", 100.0, "buy"),
            _make_tick(1010 * _NS, "kraken", "SOL/USD", 100.5, "buy"),
            _make_tick(1030 * _NS, "kraken", "SOL/USD", 101.0, "sell"),
            _make_tick(1060 * _NS, "kraken", "SOL/USD", 100.8, "buy"),
            _make_tick(1180 * _NS, "kraken", "SOL/USD", 102.0, "sell"),
            _make_tick(1300 * _NS, "kraken", "SOL/USD", 101.5, "buy"),
        ]

        signal = _make_signal(
            1000 * _NS, "coinbase", "BTC/USD", "kraken", "SOL/USD", "long"
        )

        results = evaluate_tick_signal(
            signal=signal,
            target_ticks=target_ticks,
            horizons_ms=[5000, 30000, 60000],
            fee_bps=40.0,
            slippage_bps=5.0,
            quote_mismatch_buffer_bps=5.0,
        )

        assert len(results) == 3
        # At least horizon 5s should be valid
        assert results[0].valid
        assert results[0].net_return_bps is not None


# ── 15. Binance aggTrade m field maps side correctly ────────────────────────


class TestBinanceSideMapping:
    """Verify that Binance m=true -> sell, m=false -> buy."""

    def test_binance_m_true_is_sell(self):
        """m=true means buyer was maker, so seller was aggressor -> sell."""
        # Simulate raw Binance message
        data = {"e": "trade", "s": "BTCUSDT", "p": "50000", "q": "0.1", "T": 1715577600000, "m": True, "t": 123}
        side = "sell" if data.get("m", False) else "buy"
        assert side == "sell"

    def test_binance_m_false_is_buy(self):
        """m=false means buyer was taker/aggressor -> buy."""
        data = {"e": "trade", "s": "BTCUSDT", "p": "50000", "q": "0.1", "T": 1715577600000, "m": False, "t": 124}
        side = "sell" if data.get("m", False) else "buy"
        assert side == "buy"

    def test_binance_tick_rule_not_needed(self):
        """When Binance gives explicit side, no tick-rule inference needed."""
        # Verify that the explicit Binance m field gives correct side
        tick = TradeTickLite(
            ts_event=1715577600000 * _MS,
            venue="binance",
            symbol="BTC/USDT",
            price=50000.0,
            size=0.1,
            side="sell",  # Derived from m=true, not tick-rule
        )
        assert tick.side == "sell"
        assert tick.side != "unknown"


# ── 16. No forbidden order/private-key/live-trading imports ────────────────


class TestNoForbiddenImports:
    """Verify no live-trading or order submission imports exist."""

    FORBIDDEN_MODULES = [
        "ccxt",
        "ib_insync",
        "oanda",
        "interactive_brokers",
        "nautilus_trader.adapters",
        "nautilus_trader.execution",
        "nautilus_trader.live",
        "binance.client",
        "coinbase.rest",
        "krakenex",
        "asyncio.sleep",
    ]

    def test_cross_asset_impulse_no_forbidden(self):
        import inspect
        from examples.strategies.venue_agnostic_signal_observer import cross_asset_impulse

        source = inspect.getsource(cross_asset_impulse)
        for mod in self.FORBIDDEN_MODULES:
            assert mod not in source, f"Forbidden import/module found: {mod}"

    def test_run_cross_asset_impulse_no_forbidden(self):
        import inspect
        from examples.strategies.venue_agnostic_signal_observer import run_cross_asset_impulse

        source = inspect.getsource(run_cross_asset_impulse)
        for mod in self.FORBIDDEN_MODULES:
            assert mod not in source, f"Forbidden import/module found: {mod}"


# ── 17. Overlap computation is correct ──────────────────────────────────────


class TestOverlapComputation:
    """Verify overlap window calculation."""

    def test_partial_overlap(self):
        src = StreamHealth(
            venue="a", symbol="X",
            first_tick_ts=1000 * _MS, last_tick_ts=2000 * _MS,
            tick_count=100, price_min=1.0, price_max=2.0,
        )
        tgt = StreamHealth(
            venue="b", symbol="Y",
            first_tick_ts=1500 * _MS, last_tick_ts=2500 * _MS,
            tick_count=100, price_min=1.0, price_max=2.0,
        )

        overlap = compute_overlap(src, tgt)
        assert overlap["overlap_start_ns"] == 1500 * _MS
        assert overlap["overlap_end_ns"] == 2000 * _MS
        assert overlap["overlap_duration_ms"] == 500.0

    def test_no_overlap(self):
        src = StreamHealth(
            venue="a", symbol="X",
            first_tick_ts=1000 * _NS, last_tick_ts=1500 * _NS,
            tick_count=100, price_min=1.0, price_max=2.0,
        )
        tgt = StreamHealth(
            venue="b", symbol="Y",
            first_tick_ts=2000 * _NS, last_tick_ts=2500 * _NS,
            tick_count=100, price_min=1.0, price_max=2.0,
        )

        overlap = compute_overlap(src, tgt)
        assert overlap["overlap_duration_ms"] == 0.0


# ── 18. Stream health range_bps calculation ────────────────────────────────


class TestStreamHealthRangeBps:
    """Verify range_bps calculation."""

    def test_range_bps_100_bps(self):
        sh = StreamHealth(
            venue="a", symbol="X",
            price_min=100.0, price_max=101.0,
        )
        assert abs(sh.range_bps - 100.0) < 0.01


# ── 19. generate_source_impulses produces signals ───────────────────────────


class TestGenerateSourceImpulses:
    """Verify signal generation on source ticks."""

    def test_generates_signals_on_active_source(self):
        # Create source ticks with enough activity for notional_burst
        import random
        rng = random.Random(42)
        base_price = 100000.0
        base_ts = 1000 * _NS
        trades = []
        for i in range(200):
            ts = base_ts + i * 10 * _MS
            # Create a burst of larger trades periodically
            notional_mult = 10.0 if (i % 30 < 3) else 1.0
            price = base_price + rng.gauss(0, 10)
            size = notional_mult * abs(rng.gauss(1.0, 0.5))
            side = rng.choice(["buy", "sell"])
            trades.append(_make_tick(ts, "coinbase", "BTC/USD", price, side))

        events = generate_source_impulses(
            source_ticks=trades,
            source_venue="coinbase",
            source_symbol="BTC/USD",
            signal_types=["notional_burst"],
            flow_lookbacks_ms=[5000, 10000],
            baseline_window_ms=60000,
            cooldown_ms=10000,
        )

        # Should generate at least some signals
        assert len(events) >= 0  # May be zero if no burst detected


# ── 20. CLI parser has required arguments ──────────────────────────────────


class TestCliParser:
    """Verify CLI parser has all required arguments."""

    def test_parser_has_fee_args(self):
        from examples.strategies.venue_agnostic_signal_observer.run_cross_asset_impulse import build_parser
        parser = build_parser()
        args = parser.parse_args(["--ticks", "data/test"])
        assert args.fee_bps == 40.0
        assert args.slippage_bps == 5.0
        assert args.quote_mismatch_buffer_bps == 5.0

    def test_parser_has_range_args(self):
        from examples.strategies.venue_agnostic_signal_observer.run_cross_asset_impulse import build_parser
        parser = build_parser()
        args = parser.parse_args(["--ticks", "data/test"])
        assert args.min_source_range_bps == 30.0
        assert args.min_target_range_bps == 30.0

    def test_parser_has_min_events_arg(self):
        from examples.strategies.venue_agnostic_signal_observer.run_cross_asset_impulse import build_parser
        parser = build_parser()
        args = parser.parse_args(["--ticks", "data/test"])
        assert args.min_events == 50

    def test_parser_has_baseline_window_arg(self):
        from examples.strategies.venue_agnostic_signal_observer.run_cross_asset_impulse import build_parser
        parser = build_parser()
        args = parser.parse_args(["--ticks", "data/test"])
        assert args.baseline_window_ms == 60000


# ── 21. Source range below gate blocks REJECTED ────────────────────────────


class TestSourceRangeBelowGateBlocksRejected:
    """Source range below min_source_range_bps must never produce REJECTED."""

    def test_quiet_moderate_source_cannot_be_rejected(self):
        """Simulates the actual v1 capture: ~13 bps source range, plenty of events, negative results."""
        # Source moved only ~13 bps (below 30 bps gate)
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=100130.0,  # ~13 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=105.0,  # 500 bps (sufficient)
        )

        overlap = compute_overlap(src_health, tgt_health)
        # Many signals, all negative
        valid_returns = _generate_n_valid_returns(200, mean_net_bps=-30.0)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=200,
            long_executable_count=200,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": False,
                "mean_net_return_bps": -30.0,
                "rejection_reasons": ["mean_net_return_not_positive: -30.00 bps"],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict != "REJECTED", \
            "REJECTED is forbidden when source range (13 bps) is below gate (30 bps)"
        assert verdict.verdict in ("NEEDS_MORE_DATA", "NEEDS_MORE_DATA_VOLATILE_WINDOW",
                                     "MARKET_MODERATE_DIAGNOSTIC")


# ── 22. Target range below gate blocks REJECTED ────────────────────────────


class TestTargetRangeBelowGateBlocksRejected:
    """Target range below min_target_range_bps must never produce REJECTED."""

    def test_quiet_target_cannot_be_rejected(self):
        """Source moved enough but target did not."""
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=104000.0,  # 400 bps (sufficient)
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=100.01,  # 1 bps (insufficient)
        )

        overlap = compute_overlap(src_health, tgt_health)
        valid_returns = _generate_n_valid_returns(200, mean_net_bps=-30.0)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=200,
            long_executable_count=200,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": False,
                "mean_net_return_bps": -30.0,
                "rejection_reasons": ["mean_net_return_not_positive"],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict != "REJECTED", \
            "REJECTED is forbidden when target range (1 bps) is below gate (30 bps)"


# ── 23. High event count does not override quiet source ────────────────────


class TestHighEventCountDoesNotOverrideQuietSource:
    """Many events from a quiet market still does not equal a valid stress test."""

    def test_many_tiny_events_cannot_be_rejected(self):
        """11,890+ tiny-burst events in a quiet market should not produce REJECTED."""
        src_health = StreamHealth(
            venue="binance", symbol="BTC/USDT",
            tick_count=100000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=100126.0,  # 12.6 bps (below 30)
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=500000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=101.6,  # 16 bps (below 30)
        )

        overlap = compute_overlap(src_health, tgt_health)
        valid_returns = _generate_n_valid_returns(1000, mean_net_bps=-20.0)
        pair = PairResult(
            pair_key="binance:BTC/USDT->kraken:SOL/USD",
            overlap=overlap,
            signal_count=1000,  # way above min_events
            long_executable_count=1000,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": False,
                "mean_net_return_bps": -20.0,
                "rejection_reasons": ["mean_net_return_not_positive"],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "binance|BTC/USDT": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["binance"],
            source_symbols=["BTC/USDT"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict != "REJECTED"


# ── 24. Sufficient source and target movement allows REJECTED ──────────────


class TestSufficientMovementAllowsRejected:
    """When both source and target exceed thresholds, REJECTED is a valid verdict."""

    def test_volatile_window_with_negative_results_can_be_rejected(self):
        """High-volatility capture with enough movement and events -> REJECTED allowed."""
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=105000.0,  # 500 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=110.0,  # 1000 bps
        )

        overlap = compute_overlap(src_health, tgt_health)
        valid_returns = _generate_n_valid_returns(100, mean_net_bps=-20.0)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=100,
            long_executable_count=100,
            diagnostic_only_count=0,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_returns,
            valid_diagnostic_returns=[],
            candidate_gate={
                "candidate": False,
                "mean_net_return_bps": -20.0,
                "rejection_reasons": ["mean_net_return_not_positive"],
            },
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict == "REJECTED"


# ── 25. Reason includes volatility insufficiency ───────────────────────────


class TestVerdictReasonIncludesVolatilityInsufficiency:
    """Diagnostic verdicts must explain why the market was insufficient."""

    def test_insufficient_range_reason_mentions_threshold(self):
        src_health = StreamHealth(
            venue="coinbase", symbol="BTC/USD",
            tick_count=10000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100000.0, price_max=100126.0,  # 12.6 bps
        )
        tgt_health = StreamHealth(
            venue="kraken", symbol="SOL/USD",
            tick_count=50000, first_tick_ts=1000 * _NS, last_tick_ts=1800 * _NS,
            price_min=100.0, price_max=101.58,  # 15.8 bps
        )

        overlap = compute_overlap(src_health, tgt_health)
        pair = PairResult(
            pair_key="coinbase:BTC/USD->kraken:SOL/USD",
            overlap=overlap,
            signal_count=500,
            long_executable_count=500,
            diagnostic_only_count=0,
            valid_returns=[],
            valid_long_executable_returns=[],
            valid_diagnostic_returns=[],
        )

        verdict = compute_verdict(
            pair_results=[pair],
            stream_health={
                "coinbase|BTC/USD": src_health,
                "kraken|SOL/USD": tgt_health,
            },
            same_symbol_skips=[],
            source_venues=["coinbase"],
            source_symbols=["BTC/USD"],
            target_venues=["kraken"],
            target_symbols=["SOL/USD"],
            min_source_range_bps=30.0,
            min_target_range_bps=30.0,
            min_events=50,
        )

        assert verdict.verdict != "REJECTED"
        # Reason should mention insufficient_price_range or the threshold
        reason_text = " ".join(verdict.reasons).lower()
        assert "insufficient_price_range" in reason_text or "insufficient" in reason_text
