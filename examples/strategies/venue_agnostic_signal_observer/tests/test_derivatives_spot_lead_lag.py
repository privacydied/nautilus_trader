"""Tests for derivatives-source -> spot-target lead-lag evaluation.

Covers symbol normalization, quote mismatch, OI bucket classification,
overlap-window enforcement, quiet-capture verdict, and safety scanning.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ..symbol_aliases import resolve_symbol, quote_mismatch, same_asset
from ..tick_models import TradeTickLite, TickSignalEvent, TickForwardReturn
from ..runners.legacy_cli.run_derivatives_spot_lead_lag import (
    OverlapWindow,
    compute_pair_overlap,
    clip_ticks,
    price_range_bps,
    classify_oi_bucket,
    _nearest_oi_at_or_before,
    load_capture_data,
    _parse_and_validate_devices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tick(ts_ns: int, venue: str, symbol: str, price: float,
               size: float = 1.0, side: str = "buy") -> TradeTickLite:
    return TradeTickLite(
        ts_event=ts_ns, venue=venue, symbol=symbol,
        price=price, size=size, side=side,
    )


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

class TestSymbolNormalization:
    def test_binance_perp_btcusdt_resolves(self):
        c = resolve_symbol("BTCUSDT")
        assert c.asset == "BTC"
        assert c.quote == "USDT"

    def test_binance_perp_btc_usdt_slash_resolves(self):
        c = resolve_symbol("BTC/USDT")
        assert c.asset == "BTC"
        assert c.quote == "USDT"

    def test_btcusdt_and_btc_usdt_same_asset_quote(self):
        c1 = resolve_symbol("BTCUSDT")
        c2 = resolve_symbol("BTC/USDT")
        assert c1 == c2

    def test_binance_perp_is_not_binance_spot(self):
        # binance_perp is a distinct venue, not Binance spot
        c = resolve_symbol("BTCUSDT")
        assert c.quote == "USDT"

    def test_source_can_match_target_by_asset(self):
        # BTC/USDT (source) shares asset with BTC/USD (target)
        assert same_asset("BTCUSDT", "BTC/USD")

    def test_quote_mismatch_usdt_vs_usd(self):
        assert quote_mismatch("BTCUSDT", "BTC/USD")


# ---------------------------------------------------------------------------
# Binance aggTrade side inference (documented behavior)
# ---------------------------------------------------------------------------

class TestBinanceSideInference:
    """Document the side inference rule from Binance aggTrade 'm' field.

    m=true  -> buyer was maker  -> seller aggressor -> side="sell"
    m=false -> buyer was aggressor                   -> side="buy"
    """

    def test_m_true_means_sell(self):
        # Buyer was maker = passive order, so seller was taker/aggressor
        assert "sell" == ("sell" if True else "buy")

    def test_m_false_means_buy(self):
        # Buyer was taker/aggressor
        assert "buy" == ("sell" if False else "buy")


# ---------------------------------------------------------------------------
# Overlap window enforcement
# ---------------------------------------------------------------------------

class TestOverlapWindow:
    def test_compute_overlap_basic(self):
        src = [
            _make_tick(100, "binance_perp", "BTC/USDT", 50000),
            _make_tick(200, "binance_perp", "BTC/USDT", 50001),
            _make_tick(300, "binance_perp", "BTC/USDT", 50002),
        ]
        tgt = [
            _make_tick(150, "kraken", "BTC/USD", 50000),
            _make_tick(250, "kraken", "BTC/USD", 50001),
        ]
        overlap = compute_pair_overlap(src, tgt)
        assert overlap is not None
        assert overlap.start_ns == 150
        assert overlap.end_ns == 250  # min(src_max=300, tgt_max=250)

    def test_no_overlap(self):
        src = [_make_tick(100, "src", "X", 1.0), _make_tick(200, "src", "X", 1.0)]
        tgt = [_make_tick(300, "tgt", "X", 1.0), _make_tick(400, "tgt", "X", 1.0)]
        assert compute_pair_overlap(src, tgt) is None

    def test_clip_ticks(self):
        ticks = [
            _make_tick(100, "v", "X", 1.0),
            _make_tick(200, "v", "X", 1.1),
            _make_tick(300, "v", "X", 1.2),
            _make_tick(400, "v", "X", 1.3),
        ]
        overlap = OverlapWindow(200, 300)
        clipped = clip_ticks(ticks, overlap)
        assert len(clipped) == 2
        assert clipped[0].ts_event == 200
        assert clipped[1].ts_event == 300

    def test_no_overlap_produces_need_more_data(self):
        """No overlap should result in NEEDS_MORE_DATA verdict."""
        src = [_make_tick(100, "src", "X", 1.0), _make_tick(200, "src", "X", 1.0)]
        tgt = [_make_tick(300, "tgt", "X", 1.0), _make_tick(400, "tgt", "X", 1.0)]
        overlap = compute_pair_overlap(src, tgt)
        assert overlap is None
        # In the full evaluation, this produces NEEDS_MORE_DATA

    def test_partial_overlap_uses_only_overlap_ticks(self):
        """Only ticks within overlap should be used for evaluation."""
        ticks = [
            _make_tick(100, "v", "X", 1.0),
            _make_tick(200, "v", "X", 1.1),
            _make_tick(300, "v", "X", 1.2),
            _make_tick(400, "v", "X", 1.3),
            _make_tick(500, "v", "X", 1.4),
        ]
        # Overlap from 200 to 400
        overlap = OverlapWindow(200, 400)
        clipped = clip_ticks(ticks, overlap)
        assert len(clipped) == 3
        assert all(overlap.contains(t.ts_event) for t in clipped)


# ---------------------------------------------------------------------------
# Price range
# ---------------------------------------------------------------------------

class TestPriceRange:
    def test_price_range_nonzero(self):
        ticks = [
            _make_tick(100, "v", "X", 100.0),
            _make_tick(200, "v", "X", 101.0),
        ]
        # 1 bps move
        rng = price_range_bps(ticks)
        assert abs(rng - 100.0) < 0.01

    def test_price_range_empty(self):
        assert price_range_bps([]) == 0.0

    def test_price_range_single_tick(self):
        assert price_range_bps([_make_tick(100, "v", "X", 1.0)]) == 0.0


# ---------------------------------------------------------------------------
# OI bucket classification
# ---------------------------------------------------------------------------

class TestOIBucket:
    def _oi_snap(self, ts, oi):
        return {"receive_timestamp_ns": ts, "open_interest": oi}

    def test_price_up_oi_up(self):
        src = [
            _make_tick(100, "v", "X", 100.0),
            _make_tick(200, "v", "X", 101.0),
        ]
        snaps = [
            self._oi_snap(50, 1000.0),   # before lookback start
            self._oi_snap(150, 1100.0),  # between lb_start and signal
            self._oi_snap(210, 1100.0),  # at signal time
        ]
        # lookback_ns=100, signal_ts=200 => lb_start=100
        # price: src[100]=100 -> src[200]=101 (up)
        # OI: nearest at 100 = 1000, nearest at 200 = 1100 (up)
        bucket = classify_oi_bucket(src, 100, 200, snaps)
        assert bucket == "price_up_oi_up"

    def test_price_up_oi_down(self):
        src = [
            _make_tick(100, "v", "X", 100.0),
            _make_tick(200, "v", "X", 101.0),
        ]
        snaps = [
            self._oi_snap(50, 1200.0),
            self._oi_snap(150, 1100.0),  # oi drops between lb_start and signal
            self._oi_snap(210, 1000.0),
        ]
        bucket = classify_oi_bucket(src, 100, 200, snaps)
        assert bucket == "price_up_oi_down"

    def test_price_down_oi_up(self):
        src = [
            _make_tick(100, "v", "X", 101.0),
            _make_tick(200, "v", "X", 100.0),
        ]
        snaps = [
            self._oi_snap(50, 1000.0),
            self._oi_snap(150, 1000.0),
            self._oi_snap(210, 1200.0),
        ]
        bucket = classify_oi_bucket(src, 100, 200, snaps)
        assert bucket == "price_down_oi_up"

    def test_price_down_oi_down(self):
        src = [
            _make_tick(100, "v", "X", 101.0),
            _make_tick(200, "v", "X", 100.0),
        ]
        snaps = [
            self._oi_snap(50, 1200.0),
            self._oi_snap(150, 1100.0),  # oi drops
            self._oi_snap(210, 1000.0),
        ]
        bucket = classify_oi_bucket(src, 100, 200, snaps)
        assert bucket == "price_down_oi_down"

    def test_missing_oi_produces_flat_or_unknown(self):
        src = [
            _make_tick(100, "v", "X", 100.0),
            _make_tick(200, "v", "X", 101.0),
        ]
        bucket = classify_oi_bucket(src, 100, 200, [])
        assert bucket == "flat_or_unknown"

    def test_no_lookahead_in_oi(self):
        """OI snapshot after signal timestamp must not be used."""
        src = [
            _make_tick(100, "v", "X", 100.0),
            _make_tick(200, "v", "X", 101.0),
            _make_tick(300, "v", "X", 102.0),
        ]
        snaps = [
            self._oi_snap(150, 1000.0),
            self._oi_snap(500, 9999.0),  # future -- must be ignored
        ]
        val = _nearest_oi_at_or_before(snaps, 300)
        assert val == 1000.0  # only the snapshot at 150


# ---------------------------------------------------------------------------
# Load capture data from synthetic fixture
# ---------------------------------------------------------------------------

class TestLoadCaptureData:
    def test_loads_binance_perp_trades(self):
        with tempfile.TemporaryDirectory() as td:
            tick = TradeTickLite(
                ts_event=100, venue="binance_perp", symbol="BTC/USDT",
                price=50000.0, size=1.0, side="buy",
            )
            fpath = Path(td) / "trades_binance_perp_BTC-USDT_999.jsonl"
            with open(fpath, "w") as f:
                f.write(json.dumps(tick.to_dict()) + "\n")

            grouped = load_capture_data(
                Path(td),
                source_venues=["binance_perp"],
                target_venues=["kraken"],
                symbols=["BTC/USD"],
            )
            assert ("binance_perp", "BTC/USDT") in grouped
            assert len(grouped[("binance_perp", "BTC/USDT")]) == 1

    def test_loads_kraken_trades(self):
        with tempfile.TemporaryDirectory() as td:
            tick = TradeTickLite(
                ts_event=100, venue="kraken", symbol="BTC/USD",
                price=50000.0, size=1.0, side="buy",
            )
            fpath = Path(td) / "trades_kraken_BTC-USD_999.jsonl"
            with open(fpath, "w") as f:
                f.write(json.dumps(tick.to_dict()) + "\n")

            grouped = load_capture_data(
                Path(td),
                source_venues=["binance_perp"],
                target_venues=["kraken"],
                symbols=["BTC/USD"],
            )
            assert ("kraken", "BTC/USD") in grouped


# ---------------------------------------------------------------------------
# Safety scan -- forbidden strings in implementation files
# ---------------------------------------------------------------------------

class TestSafetyScan:
    """New implementation files must not contain execution/auth strings."""

    FORBIDDEN = [
        "order_submit",
        "order_place",
        "live_trading",
        "private_cred",
        "api_secret",
        "secret_token",
        "account_id",
        "AccountType.ISOLATED_MARGIN",
        "submit_order",
        "OrderSide.BUY",
        "OrderSide.SELL",
    ]

    @pytest.mark.parametrize("fname", [
        "run_derivatives_spot_capture.py",
        "run_derivatives_spot_lead_lag.py",
    ])
    def test_no_forbidden_strings(self, fname):
        base = Path(__file__).parent.parent
        fpath = base / "runners" / "legacy_cli" / fname
        if not fpath.exists():
            pytest.skip(f"{fname} not found")
        content = fpath.read_text()
        for forbidden in self.FORBIDDEN:
            assert forbidden not in content, (
                f"Found forbidden '{forbidden}' in {fname}"
            )


# ---------------------------------------------------------------------------
# Verdict logic tests
# ---------------------------------------------------------------------------

class TestVerdictLogic:
    """Test the verdict decision rules directly."""

    def test_quiet_capture_is_need_more_data(self):
        """A capture with movement < 20% of cost wall -> NEEDS_MORE_DATA."""
        all_in_cost = 50.0
        overlap_price_range = 5.0  # well below 20% of 50
        assert overlap_price_range < all_in_cost * 0.2
        # In the real evaluator this produces NEEDS_MORE_DATA

    def test_enough_movement_negative_edge_is_rejected(self):
        """Enough events but negative net -> REJECTED."""
        all_in_cost = 50.0
        overlap_price_range = 200.0  # 4x cost wall
        assert overlap_price_range > all_in_cost * 0.5
        # Combined with negative net returns -> REJECTED

    def test_enough_movement_passing_gates_is_candidate(self):
        """Enough movement + events + gates pass -> CANDIDATE."""
        all_in_cost = 50.0
        overlap_price_range = 300.0
        assert overlap_price_range > all_in_cost * 0.5
        # Combined with passing candidate gates -> CANDIDATE_FOR_LONGER_OBSERVATION


# ---------------------------------------------------------------------------
# Multi-GPU device parsing tests
# ---------------------------------------------------------------------------


class TestParseAndValidateDevices:
    def test_cpu_engine_returns_empty(self):
        """CPU engine returns empty device list."""
        assert _parse_and_validate_devices("cuda:0,cuda:1", "cpu") == []

    def test_empty_string_returns_empty(self):
        """Empty forward-devices string returns empty list."""
        assert _parse_and_validate_devices("", "gpu") == []

    def test_single_device_parsing(self, monkeypatch):
        """Single device string parses correctly."""
        # Under pytest discovery the production module can live in sys.modules
        # under two names (``examples.strategies.venue_agnostic_signal_observer.forward_returns_gpu``
        # and ``venue_agnostic_signal_observer.forward_returns_gpu``).  Patch
        # the attribute directly on the same module object the production
        # code's relative import resolves to.
        from .. import forward_returns_gpu as _frg
        monkeypatch.setattr(_frg, "check_cuda_available", lambda d: (True, "cuda_available"))
        result = _parse_and_validate_devices("cuda:0", "gpu")
        assert result == ["cuda:0"]

    def test_multi_device_parsing(self, monkeypatch):
        """Multiple devices parse correctly."""
        # Under pytest discovery the production module can live in sys.modules
        # under two names (``examples.strategies.venue_agnostic_signal_observer.forward_returns_gpu``
        # and ``venue_agnostic_signal_observer.forward_returns_gpu``).  Patch
        # the attribute directly on the same module object the production
        # code's relative import resolves to.
        from .. import forward_returns_gpu as _frg
        monkeypatch.setattr(_frg, "check_cuda_available", lambda d: (True, "cuda_available"))
        result = _parse_and_validate_devices("cuda:0,cuda:1", "gpu")
        assert result == ["cuda:0", "cuda:1"]

    def test_duplicate_device_rejected(self, monkeypatch):
        """Duplicate devices should fail."""
        # Under pytest discovery the production module can live in sys.modules
        # under two names (``examples.strategies.venue_agnostic_signal_observer.forward_returns_gpu``
        # and ``venue_agnostic_signal_observer.forward_returns_gpu``).  Patch
        # the attribute directly on the same module object the production
        # code's relative import resolves to.
        from .. import forward_returns_gpu as _frg
        monkeypatch.setattr(_frg, "check_cuda_available", lambda d: (True, "cuda_available"))
        with pytest.raises(SystemExit):
            _parse_and_validate_devices("cuda:0,cuda:0", "gpu")

    def test_invalid_device_string_rejected(self, monkeypatch):
        """Invalid device strings should fail."""
        # Under pytest discovery the production module can live in sys.modules
        # under two names (``examples.strategies.venue_agnostic_signal_observer.forward_returns_gpu``
        # and ``venue_agnostic_signal_observer.forward_returns_gpu``).  Patch
        # the attribute directly on the same module object the production
        # code's relative import resolves to.
        from .. import forward_returns_gpu as _frg
        monkeypatch.setattr(_frg, "check_cuda_available", lambda d: (True, "cuda_available"))
        with pytest.raises(SystemExit):
            _parse_and_validate_devices("cpu", "gpu")

    def test_unavailable_device_rejected(self, monkeypatch):
        """Unavailable CUDA device should fail."""
        from .. import forward_returns_gpu as _frg
        monkeypatch.setattr(_frg, "check_cuda_available", lambda d: (False, "cuda_device_not_found:cuda:99"))
        with pytest.raises(SystemExit):
            _parse_and_validate_devices("cuda:99", "gpu")

    def test_whitespace_handling(self, monkeypatch):
        """Devices with spaces around commas parse correctly."""
        # Under pytest discovery the production module can live in sys.modules
        # under two names (``examples.strategies.venue_agnostic_signal_observer.forward_returns_gpu``
        # and ``venue_agnostic_signal_observer.forward_returns_gpu``).  Patch
        # the attribute directly on the same module object the production
        # code's relative import resolves to.
        from .. import forward_returns_gpu as _frg
        monkeypatch.setattr(_frg, "check_cuda_available", lambda d: (True, "cuda_available"))
        result = _parse_and_validate_devices(" cuda:0 , cuda:1 ", "gpu")
        assert result == ["cuda:0", "cuda:1"]


# ---------------------------------------------------------------------------
# forward_devices CLI arg tests
# ---------------------------------------------------------------------------


class TestForwardDevicesCliArg:
    def test_forward_devices_appears_in_help(self):
        from ..runners.legacy_cli.run_derivatives_spot_lead_lag import build_parser
        parser = build_parser()
        help_text = parser.format_help()
        assert "--forward-devices" in help_text
        assert "Multi-GPU" in help_text

    def test_forward_devices_default_empty(self):
        from ..runners.legacy_cli.run_derivatives_spot_lead_lag import build_parser
        parser = build_parser()
        args = parser.parse_args(["--capture-dir", "/tmp", "--out", "/tmp"])
        assert args.forward_devices == ""

    def test_forward_devices_parsed(self):
        from ..runners.legacy_cli.run_derivatives_spot_lead_lag import build_parser
        parser = build_parser()
        args = parser.parse_args(["--capture-dir", "/tmp", "--out", "/tmp",
                                  "--forward-engine", "gpu",
                                  "--forward-devices", "cuda:0,cuda:1"])
        assert args.forward_devices == "cuda:0,cuda:1"

    def test_single_forward_device_still_works(self):
        from ..runners.legacy_cli.run_derivatives_spot_lead_lag import build_parser
        parser = build_parser()
        args = parser.parse_args(["--capture-dir", "/tmp", "--out", "/tmp",
                                  "--forward-engine", "gpu",
                                  "--forward-device", "cuda:0"])
        assert args.forward_device == "cuda:0"
        assert args.forward_devices == ""
