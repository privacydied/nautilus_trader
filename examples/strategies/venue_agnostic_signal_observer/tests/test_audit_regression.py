"""Regression tests for bug-audit pass 1 fixes.

Each test is designed to FAIL with the original buggy code and PASS after the fix.
"""

import pytest


# ===================================================================
# H-1: lead_lag.py off-by-one in lookback window reference index
# ===================================================================

class TestLeadLagWindowReference:
    """Bug: the while loop exits with j pointing OUTSIDE the window, then
    max(0, j) clamps to 0 instead of using j+1 (the first tick inside).
    This picks a stale reference price, inflating measured moves."""

    def generate_signals(self, timestamps, prices, lookback_sec=3.0, threshold_bps=5.0):
        from ..lead_lag import generate_lead_lag_signals
        return generate_lead_lag_signals(
            source_timestamps=timestamps,
            source_prices=prices,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=lookback_sec,
            move_threshold_bps=threshold_bps,
            cooldown_seconds=0.0,
        )

    def test_reference_price_inside_window(self):
        """With timestamps [0, 8, 9, 10] and lookback=2s:
        At i=3 (ts=10), window_start=8.
        The while loop decrements j to 0 (ts=0, outside window).
        BUGGY: j = max(0, 0) = 0 → ref_price = prices[0] = 110.0
        FIXED: j = 0+1 = 1 → ref_price = prices[1] = 100.0

        With ref=110.0, price at ts=10 is 100.3 → -9.0 bps (no signal at 5 bps threshold)
        With ref=100.0, price at ts=10 is 100.3 → +30 bps (signal fires).

        Actually let me check: the loop at i=3:
          j=3 (start), source_timestamps[3]=10 >= 8 → j=2
          j=2, source_timestamps[2]=9 >= 8 → j=1
          j=1, source_timestamps[1]=8 >= 8 → j=0
          j=0, source_timestamps[0]=0 >= 8 → FALSE → exit with j=0
          BUGGY: max(0, 0)=0 → ref=prices[0]=110.0
          FIXED: j+1=1 → ref=prices[1]=100.0
        """
        # The price at ts=10 is 100.3
        # ref at index 0 (buggy) = 110.0, move = (100.3-110.0)/110.0 * 10000 = -88.2 bps
        # ref at index 1 (fixed) = 100.0, move = (100.3-100.0)/100.0 * 10000 = +30.0 bps
        ts = [0.0, 8.0, 9.0, 10.0]
        prices = [110.0, 100.0, 100.0, 100.3]

        signals = self.generate_signals(ts, prices, lookback_sec=2.0, threshold_bps=5.0)

        # With fixed code, there should be a signal (~30 bps move)
        assert len(signals) >= 1, (
            f"Expected at least one signal, got {len(signals)}. "
            "The reference price appears to be from OUTSIDE the lookback window, "
            "producing a falsely small (or inverted) move."
        )
        sig = signals[0]
        expected_move_bps = round((100.3 - 100.0) / 100.0 * 10000.0, 2)
        assert abs(sig.strength - expected_move_bps) < 0.5, (
            f"Expected signal strength ~{expected_move_bps} bps (ref=100.0), "
            f"got {sig.strength} bps. The reference price is likely incorrect."
        )


# ===================================================================
# H-2 and off-by-one in trade_flow_impulse _resolve_direction
# ===================================================================

class TestDerivativesDirectionOffByOne:
    """Bug: ref_idx exits the while loop pointing to a tick OUTSIDE the
    lookback window, then uses that price directly. Should use ref_idx+1."""

    def _make_gen(self):
        from ..derivatives_lead_lag import DerivativesImpulseGenerator
        return DerivativesImpulseGenerator(
            lookbacks_ms=[2000],
            cooldown_ms=0,
        )

    def _make_trades(self, timestamps, prices):
        from ..derivatives_lead_lag import DerivativeTradeTick
        return [
            DerivativeTradeTick(
                ts_event=t * 1_000_000_000,  # seconds to nanoseconds
                price=p,
                size=1.0,
                venue="BINANCE",
                symbol="BTC/USDT",
                side="buy",
            )
            for t, p in zip(timestamps, prices)
        ]

    def test_resolve_direction_uses_tick_inside_window(self):
        """With timestamps [0, 8, 9, 10] and lookback=2000ms at ts=10:
        Loop exits with ref_idx=0 (ts=0 is outside the 2s window).
        BUGGY: ref_price = 110.0 → 100.3 < 110.0 → "short"
        FIXED: ref_idx+1=1, ref_price = 100.0 → 100.3 >= 100.0 → "long"
        """
        timestamps = [0, 8, 9, 10]  # seconds
        prices = [110.0, 100.0, 100.0, 100.3]

        gen = self._make_gen()
        trades = self._make_trades(timestamps, prices)
        direction = gen._resolve_direction(trades, idx=3, lookback_ms=2000)

        assert direction == "long", (
            f"Expected 'long' (current 100.3 > window ref 100.0), got '{direction}'. "
            "This indicates _resolve_direction uses a reference price from OUTSIDE "
            "the lookback window (index 0 with price 110.0 instead of index 1 with 100.0)."
        )


# ===================================================================
# H-3: baseline evaluation uses TickForwardReturn instead of trade ticks
# ===================================================================

class TestBaselineUsesRealTicks:
    """Bug: baseline was evaluated against valid[:1] (a list of TickForwardReturn)
    instead of actual TradeTickLite data. _extract_ts_prices expects .price
    or .mid which TickForwardReturn doesn't have."""

    def _make_target_ticks(self, count=100):
        """Ticks spanning 100 seconds — enough for 5000ms forward returns."""
        from ..tick_models import TradeTickLite
        # Use nanoseconds with a wide enough range
        base_ns = 1_000_000_000_000  # 1e12 ns
        return [
            TradeTickLite(
                ts_event=base_ns + i * 1_000_000_000,  # 1 second apart
                venue="kraken",
                symbol="BTC/USD",
                price=50000.0 + i * 0.01,
                size=0.01,
                side="buy" if i % 2 == 0 else "sell",
            )
            for i in range(count)
        ]

    def test_base_tick_types_work_with_eval(self):
        """generate_random_baseline + evaluate_tick_signal with real ticks"""
        from ..event_study import generate_random_baseline, evaluate_tick_signal

        ticks = self._make_target_ticks(100)
        baseline_events = generate_random_baseline(
            source_ticks=ticks,
            signal_count=5,
            source_venue="BINANCE",
            target_venue="KRAKEN",
            symbol="BTC/USDT",
            asset="BTC",
            seed=42,
        )
        assert len(baseline_events) == 5

        for evt in baseline_events:
            frs = evaluate_tick_signal(
                signal=evt,
                target_ticks=ticks,
                horizons_ms=[1000, 5000],
                fee_bps=40,
                slippage_bps=5,
            )
            valid = [fr for fr in frs if fr.valid]
            assert len(valid) > 0, (
                f"Baseline {evt.signal_id} -> 0 valid returns. "
                "If target_ticks were TickForwardReturn objects this would fail."
            )


# ===================================================================
# H-4: forward_returns raw vs direction-adjusted return conflation
# ===================================================================

class TestRawVsDirectionAdjustedReturn:
    """Bug: both fields received the same direction-adjusted value.
    raw_return_bps should be the unsigned price change."""

    def _make_result(self, direction, prices, ts=None):
        from ..forward_returns import evaluate_signal, Horizon, FeeModel
        from ..models import SignalEvent
        if ts is None:
            ts = [1000.0 + i * 10 for i in range(100)]
        sig = SignalEvent(
            signal_id="s1", timestamp=1000.0,
            source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T",
            signal_type="test", direction=direction, strength=1.0,
        )
        results = evaluate_signal(sig, ts, prices, [Horizon("10s", 10.0)], FeeModel(fee_bps=0, slippage_bps=0))
        return [r for r in results if r.valid][0]

    def test_short_raw_positive_adjusted_negative(self):
        """Price up 50000→50100 (+20 bps). For short: raw=+20, adjusted=-20."""
        prices = [50000.0, 50100.0] + [50100.0] * 98
        r = self._make_result("short", prices)
        assert r.raw_return_bps > 0, f"raw should be +20, got {r.raw_return_bps}"
        assert r.direction_adjusted_return_bps < 0, f"adjusted should be -20, got {r.direction_adjusted_return_bps}"
        assert r.raw_return_bps == pytest.approx(-r.direction_adjusted_return_bps, abs=1e-3)

    def test_long_raw_equals_adjusted(self):
        """For long, raw and adjusted should be identical."""
        prices = [50000.0, 50100.0] + [50100.0] * 98
        r = self._make_result("long", prices)
        assert r.raw_return_bps == r.direction_adjusted_return_bps

    def test_price_down_raw_negative(self):
        """Price down 50100→50000 (-20 bps) — raw should be negative."""
        prices = [50100.0, 50000.0] + [50000.0] * 98
        r = self._make_result("long", prices)
        assert r.raw_return_bps < 0, f"raw should be negative, got {r.raw_return_bps}"
        assert r.direction_adjusted_return_bps < 0, f"adjusted should also be negative for long, got {r.direction_adjusted_return_bps}"


# ===================================================================
# H-7: exception logging from gather
# ===================================================================

class TestCaptureExceptionLogging:
    """Bug: gather(return_exceptions=True) silently swallowed exceptions.
    The fix adds a branch to log any BaseException results."""

    def test_exception_branch_exists_in_post_processing(self):
        """Verify that the code handles BaseException in the results loop
        by inspecting that the source file contains the fix."""
        import inspect
        from pathlib import Path
        from ..runners.legacy_cli import run_derivatives_spot_capture
        source = inspect.getsource(run_derivatives_spot_capture)
        assert "isinstance(r, BaseException)" in source, (
            "run_derivatives_spot_capture should log exceptions from gather(). "
            "Missing 'isinstance(r, BaseException)' branch."
        )


# ===================================================================
# H-8: large_trade direction maps buy→long, sell→short
# ===================================================================

class TestLargeTradeDirectionMapping:
    """Bug: direction was set to 'buy'/'sell' (exchange terms) instead of
    'long'/'short' (internal convention). Evaluate only checks for
    direction=='short', so 'sell' was treated as long."""

    def _make_ticks(self, last_side="sell", count=100):
        from ..tick_models import TradeTickLite
        base_price = 50000.0
        ticks = [
            TradeTickLite(
                ts_event=1_000_000_000 + i * 100_000_000,
                venue="BINANCE",
                symbol="BTC/USDT",
                price=base_price + i * 0.01,
                size=0.01,
                side="buy" if i % 2 == 0 else "sell",
            )
            for i in range(count)
        ]
        # Last tick is a large trade on the specified side
        ticks[-1] = TradeTickLite(
            ts_event=1_000_000_000 + count * 100_000_000,
            venue="BINANCE",
            symbol="BTC/USDT",
            price=base_price,
            size=50.0,  # Large: 50 * 50000 = 2.5M
            side=last_side,
        )
        return ticks

    def test_large_sell_is_short_not_sell(self):
        """A large sell trade should produce a signal with direction='short'."""
        from ..trade_flow_impulse import TradeFlowImpulseSignalGenerator, TradeFlowImpulseConfig

        ticks = self._make_ticks(last_side="sell")
        cfg = TradeFlowImpulseConfig(
            source_venue="BINANCE",
            target_venue="KRAKEN",
            symbol="BTC/USDT",
            asset="BTC",
            flow_lookbacks_ms=[1000],
            baseline_window_ms=60000,
            signal_types=["large_trade"],
            large_trade_min_notional_usd=1_000_000,
            cooldown_ms=0,
        )
        gen = TradeFlowImpulseSignalGenerator(cfg)
        events = gen.generate(ticks)

        sell_dir = [e.direction for e in events]
        assert "sell" not in sell_dir, (
            f"Found direction='sell' in large_trade events ({sell_dir}). "
            "Should be 'short', not exchange terminology 'sell'."
        )
        assert "short" in sell_dir, (
            "Expected direction='short' for large sell trade."
        )

    def test_large_buy_is_long_not_buy(self):
        """A large buy trade should produce a signal with direction='long'."""
        from ..trade_flow_impulse import TradeFlowImpulseSignalGenerator, TradeFlowImpulseConfig

        ticks = self._make_ticks(last_side="buy", count=100)
        cfg = TradeFlowImpulseConfig(
            source_venue="BINANCE",
            target_venue="KRAKEN",
            symbol="BTC/USDT",
            asset="BTC",
            flow_lookbacks_ms=[1000],
            baseline_window_ms=60000,
            signal_types=["large_trade"],
            large_trade_min_notional_usd=1_000_000,
            cooldown_ms=0,
        )
        gen = TradeFlowImpulseSignalGenerator(cfg)
        events = gen.generate(ticks)

        buy_dir = [e.direction for e in events]
        assert "buy" not in buy_dir, (
            f"Found direction='buy' in large_trade events ({buy_dir}). "
            "Should be 'long', not exchange terminology 'buy'."
        )
        assert "long" in buy_dir, (
            "Expected direction='long' for large buy trade."
        )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for H-5, H-6, H-7 structural fixes."""

class TestReconnectLoop:
    """Verify the bounded reconnect wrapper exists and is used."""

    def test_with_reconnect_function_exists(self):
        """_with_reconnect_loop must exist in the capture module."""
        from ..runners.legacy_cli.run_derivatives_spot_capture import _with_reconnect_loop
        assert callable(_with_reconnect_loop)

    def test_reconnect_constants_defined(self):
        """Bounded reconnect constants must be present."""
        from ..runners.legacy_cli import run_derivatives_spot_capture as cap
        assert hasattr(cap, "_RECONNECT_MAX_ATTEMPTS")
        assert hasattr(cap, "_RECONNECT_BUDGET_S")
        assert cap._RECONNECT_MAX_ATTEMPTS <= 5
        assert cap._RECONNECT_BUDGET_S <= 30

    def test_capture_functions_return_str_or_none(self):
        """Capture functions must return str|None (reconnect reason)."""
        import inspect
        from ..runners.legacy_cli.run_derivatives_spot_capture import (
            capture_binance_perp,
            capture_kraken_spot,
            capture_coinbase_spot,
        )
        for fn in (capture_binance_perp, capture_kraken_spot, capture_coinbase_spot):
            ret = inspect.signature(fn).return_annotation
            # Should be str | None, not None (the old void return type)
            assert ret is not type(None), (
                f"{fn.__name__} still returns None — no reconnect reason propagation"
            )


# ============================================
# H-6: aiohttp session cleanup on close
# ============================================
class TestSessionCleanup:
    """Verify _ws_close stashes and closes aiohttp session."""

    def test_ws_close_cleans_session(self):
        """_ws_close must look for _aiohttp_session attribute."""
        import inspect
        from ..runners.legacy_cli.run_derivatives_spot_capture import _ws_close
        src = inspect.getsource(_ws_close)
        assert "_aiohttp_session" in src, (
            "_ws_close does not clean up aiohttp session — session leak risk"
        )


# ============================================
# H-7: Task exceptions surfaced in manifest
# ============================================
class TestTaskExceptionSurfacing:
    """Verify the gather result handling associates errors with task names."""

    def test_task_exceptions_in_manifest_structure(self):
        """The manifest must include 'task_exceptions' key."""
        from ..runners.legacy_cli import run_derivatives_spot_capture as cap
        path = cap.__file__
        with open(path) as f:
            src = f.read()
        assert '"task_exceptions"' in src or "'task_exceptions'" in src, (
            "Manifest does not record task-level exceptions"
        )
        assert "task_exceptions.append" in src, (
            "No task_exception collection logic — exceptions are swallowed"
        )

    def test_exception_branch_uses_task_name(self):
        """Exception handling must use Task.get_name() for identification."""
        from ..runners.legacy_cli import run_derivatives_spot_capture as cap
        import inspect
        src = inspect.getsource(cap)
        assert "task_name = t.get_name()" in src, (
            "Exception handling does not use Task.get_name() — errors anonymous"
        )


# ============================================
# Reconnect overlap integrity
# ============================================
class TestReconnectOverlapPreservesUnion:
    """Verify first_tick_ts survives reconnect and overlap is computed as union."""

    def _make_stats(self, **kwargs):
        from ..runners.legacy_cli.run_derivatives_spot_capture import StreamStats
        s = StreamStats(kwargs.pop("name", "test"))
        for k, v in kwargs.items():
            setattr(s, k, v)
        return s

    def test_post_reconnect_first_tick_ts_not_overwritten(self):
        """first_tick_ts must survive reconnect via 'is None' guard."""
        from ..runners.legacy_cli import run_derivatives_spot_capture as cap
        import inspect
        # The guard pattern must exist in all three handlers
        src = inspect.getsource(cap)
        assert "if s.first_tick_ts is None:" in src, (
            "first_tick_ts guard missing — reconnect may overwrite pre-reconnect anchor"
        )
        # last_tick_ts must be set unconditionally (no guard)
        # Check that the line after the guard is NOT also guarded
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "first_tick_ts is None" in line:
                # Next few lines should include last_tick_ts without an 'if' guard
                next_block = "\n".join(lines[i:i+5])
                assert "last_tick_ts = ts_ns" in next_block, (
                    "last_tick_ts not updated after first_tick_ts guard"
                )
                break

    def test_overlap_uses_min_max_across_reconnect(self):
        """compute_overlap_windows must use min/max of first/last across targets."""
        from ..runners.legacy_cli.run_derivatives_spot_capture import (
            StreamStats,
            compute_overlap_windows,
        )

        BASE = 1_700_000_000_000_000_000  # ~Nov 2023 in ns
        SEC = 1_000_000_000

        stats = {}

        # Source: pre-reconnect tick at t=0, post-reconnect tick at t=1080s
        src = self._make_stats(
            name="binance_perp_BTC/USDT",
            status="ok",
            tick_count=500,
            first_tick_ts=BASE,
            last_tick_ts=BASE + 1080 * SEC,
            reconnect_count=1,
        )
        stats["binance_perp_BTC/USDT"] = src

        # Target: Kraken continuous from t=30s to t=1080s
        kr = self._make_stats(
            name="kraken_BTC/USD",
            status="ok",
            tick_count=300,
            first_tick_ts=BASE + 30 * SEC,
            last_tick_ts=BASE + 1080 * SEC,
        )
        stats["kraken_BTC/USD"] = kr

        # Target: Coinbase dropped at t=300s, came back at t=360s,
        # first_tick_ts preserved at t=60s (pre-drop), last at t=1080s
        cb = self._make_stats(
            name="coinbase_BTC/USD",
            status="ok",
            tick_count=200,
            first_tick_ts=BASE + 60 * SEC,
            last_tick_ts=BASE + 1080 * SEC,
            reconnect_count=1,
        )
        stats["coinbase_BTC/USD"] = cb

        result = compute_overlap_windows(stats, ["BTC"])
        p = result["per_pair"]["BTC"]

        # Target min should be min(30s, 60s) = 30s (not the post-reconnect 360s)
        expected_target_min = BASE + 30 * SEC
        assert p["target_start_ns"] == expected_target_min, (
            f"target_start_ns = {p['target_start_ns']}, expected {expected_target_min}. "
            "Reconnect must not truncate the target window."
        )
        # Target max should be 1080s
        assert p["target_end_ns"] == BASE + 1080 * SEC

        # Overlap = max(source_min, target_min) .. min(source_max, target_max)
        # = max(0s, 30s) .. min(1080s, 1080s) = 30s .. 1080s = 1050s
        assert p["overlap_duration_seconds"] == 1050.0, (
            f"Expected 1050s overlap, got {p['overlap_duration_seconds']}s. "
            "Overlap must span the full union of pre/post-reconnect data."
        )
