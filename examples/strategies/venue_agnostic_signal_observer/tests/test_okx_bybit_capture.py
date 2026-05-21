"""
Tests for OKX/Bybit public-trade capture support.

These tests cover the venue-expansion patch for ``cross_asset_beta_lag_v1``:
- OKX/Bybit trade-payload parsing into ``TradeTickLite``.
- Symbol alias normalization for the six configured assets across new venues.
- Manifest stream-health fields populated for new venues.
- A static safety scan that no auth/private/order strings were introduced.

The tests are pure-Python — no WebSocket connections are opened, no capture
is started.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer import symbol_aliases
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import _FEED_TASKS
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import _CaptureStats
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import _map_bybit_symbol
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import _map_okx_symbol
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import _write_manifest
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import bybit_symbol
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import okx_symbol
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import parse_bybit_trade
from examples.strategies.venue_agnostic_signal_observer.run_tick_capture import parse_okx_trade


ASSETS = ["BTC", "ETH", "SOL", "LINK", "DOGE", "AVAX"]


# ---------------------------------------------------------------------------
# Trade payload parsing
# ---------------------------------------------------------------------------


def test_okx_trade_parses_into_tradeticklite():
    td = {
        "instId": "BTC-USDT",
        "tradeId": "12345",
        "px": "65000.5",
        "sz": "0.001",
        "side": "buy",
        "ts": "1715577600000",
    }
    tick = parse_okx_trade(td, norm_sym="BTC/USDT")
    assert tick.venue == "okx"
    assert tick.symbol == "BTC/USDT"
    assert tick.price == 65000.5
    assert tick.size == 0.001
    assert tick.side == "buy"
    assert tick.trade_id == "12345"
    # ms -> ns
    assert tick.ts_event == 1715577600000 * 1_000_000


def test_okx_trade_malformed_raises():
    with pytest.raises((KeyError, ValueError)):
        parse_okx_trade({"instId": "BTC-USDT"}, norm_sym="BTC/USDT")


def test_bybit_trade_parses_into_tradeticklite():
    td = {
        "T": 1715577600000,
        "s": "BTCUSDT",
        "S": "Sell",
        "v": "0.01",
        "p": "65000.5",
        "i": "abc123",
        "BT": False,
    }
    tick = parse_bybit_trade(td, norm_sym="BTC/USDT")
    assert tick.venue == "bybit"
    assert tick.symbol == "BTC/USDT"
    assert tick.price == 65000.5
    assert tick.size == 0.01
    assert tick.side == "sell"
    assert tick.trade_id == "abc123"
    assert tick.ts_event == 1715577600000 * 1_000_000


def test_bybit_trade_buy_side():
    tick = parse_bybit_trade(
        {"T": 1, "s": "ETHUSDT", "S": "Buy", "v": "1", "p": "1", "i": "x"},
        norm_sym="ETH/USDT",
    )
    assert tick.side == "buy"


def test_bybit_trade_malformed_raises():
    with pytest.raises((KeyError, ValueError)):
        parse_bybit_trade({"s": "BTCUSDT"}, norm_sym="BTC/USDT")


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asset", ASSETS)
def test_okx_symbol_normalization(asset):
    assert okx_symbol(f"{asset}/USDT") == f"{asset}-USDT"


@pytest.mark.parametrize("asset", ASSETS)
def test_bybit_symbol_normalization(asset):
    assert bybit_symbol(f"{asset}/USDT") == f"{asset}USDT"


@pytest.mark.parametrize("asset", ASSETS)
def test_okx_alias_resolves(asset):
    c = symbol_aliases.resolve_symbol(f"{asset}-USDT")
    assert c.asset == asset
    assert c.quote == "USDT"


@pytest.mark.parametrize("asset", ASSETS)
def test_bybit_alias_resolves(asset):
    c = symbol_aliases.resolve_symbol(f"{asset}USDT")
    assert c.asset == asset
    assert c.quote == "USDT"


def test_map_okx_symbol():
    assert _map_okx_symbol("BTC-USDT", ["BTC/USDT"]) == "BTC/USDT"
    assert _map_okx_symbol("ETH-USDT", ["BTC/USDT"]) is None


def test_map_bybit_symbol():
    assert _map_bybit_symbol("BTCUSDT", ["BTC/USDT"]) == "BTC/USDT"
    assert _map_bybit_symbol("XRPUSDT", ["BTC/USDT"]) is None


# ---------------------------------------------------------------------------
# Feed registry
# ---------------------------------------------------------------------------


def test_feed_task_registry_contains_new_venues():
    for v in ("binance", "kraken", "coinbase", "okx", "bybit"):
        assert v in _FEED_TASKS


# ---------------------------------------------------------------------------
# Manifest stream-health
# ---------------------------------------------------------------------------


def test_manifest_records_stream_health(tmp_path):
    stats = _CaptureStats(
        tick_counts={"okx|BTC/USDT": 3, "bybit|BTC/USDT": 0},
        first_tick_ts={"okx|BTC/USDT": 1_000_000_000, "bybit|BTC/USDT": 0},
        last_tick_ts={"okx|BTC/USDT": 5_000_000_000, "bybit|BTC/USDT": 0},
        price_min={"okx|BTC/USDT": 100.0},
        price_max={"okx|BTC/USDT": 101.0},
        errors=[],
        warnings=[],
    )
    stats.record_reconnect("bybit")
    stats.record_error("bybit", TimeoutError("boom"))

    _write_manifest(
        stats=stats,
        run_start_utc="2026-05-16T00:00:00+00:00",
        run_end_utc="2026-05-16T00:01:00+00:00",
        duration_seconds=60.0,
        venues=["okx", "bybit"],
        requested_symbols=["BTC/USDT"],
        files_written=[],
        out_dir=str(tmp_path),
    )
    manifest = json.loads((tmp_path / "capture_manifest.json").read_text())
    assert "streams" in manifest
    assert manifest["streams"]["okx|BTC/USDT"]["status"] == "ok"
    assert manifest["streams"]["okx|BTC/USDT"]["tick_count"] == 3
    assert manifest["streams"]["bybit|BTC/USDT"]["status"] == "zero_ticks"
    assert manifest["reconnect_count"]["bybit"] == 1
    assert manifest["error_summary"]["bybit:TimeoutError"] == 1
    assert "bybit|BTC/USDT" in manifest["zero_tick_streams"]
    assert "overlap_window_ns" in manifest


# ---------------------------------------------------------------------------
# Safety scan — no auth/orders/private endpoints
# ---------------------------------------------------------------------------


def test_manifest_overlap_semantics(tmp_path):
    """
    Pairwise + per-asset overlap is the analytically-relevant metric;
    global overlap is retained only as a diagnostic.

    Scenario: 3 streams for the same asset BTC across 3 venues, where
    okx is alive [1s, 5s], bybit is alive [2s, 6s], bitfinex is alive [3s, 7s].
    Global "all co-alive" = [3s, 5s] = 2s; pairwise (okx,bybit) = 3s;
    per-asset BTC across all 3 = 2s.
    """
    NS = 1_000_000_000
    stats = _CaptureStats(
        tick_counts={
            "okx|BTC/USDT": 10,
            "bybit|BTC/USDT": 10,
            "bitfinex|BTC/USD": 10,
        },
        first_tick_ts={
            "okx|BTC/USDT": 1 * NS,
            "bybit|BTC/USDT": 2 * NS,
            "bitfinex|BTC/USD": 3 * NS,
        },
        last_tick_ts={
            "okx|BTC/USDT": 5 * NS,
            "bybit|BTC/USDT": 6 * NS,
            "bitfinex|BTC/USD": 7 * NS,
        },
        price_min={}, price_max={}, errors=[], warnings=[],
    )
    _write_manifest(
        stats=stats,
        run_start_utc="2026-05-16T00:00:00+00:00",
        run_end_utc="2026-05-16T00:00:10+00:00",
        duration_seconds=10.0,
        venues=["okx", "bybit", "bitfinex"],
        requested_symbols=["BTC/USDT", "BTC/USD"],
        files_written=[],
        out_dir=str(tmp_path),
    )
    m = json.loads((tmp_path / "capture_manifest.json").read_text())

    # Pairwise overlaps
    pw = m["pairwise_overlap_ns"]
    # Find okx<->bybit pair (key order alphabetical via iteration order).
    okx_bybit = next(v for k, v in pw.items() if "okx|BTC/USDT" in k and "bybit|BTC/USDT" in k)
    # okx [1,5] vs bybit [2,6] -> overlap [2,5] = 3s
    assert okx_bybit["duration_ns"] == 3 * NS

    okx_bfx = next(v for k, v in pw.items() if "okx|BTC/USDT" in k and "bitfinex|BTC/USD" in k)
    # okx [1,5] vs bitfinex [3,7] -> overlap [3,5] = 2s
    assert okx_bfx["duration_ns"] == 2 * NS

    # Per-asset overlap (BTC across all 3 venues)
    per_asset = m["per_asset_overlap_ns"]
    assert "BTC" in per_asset
    assert per_asset["BTC"]["venues_alive"] == 3
    assert per_asset["BTC"]["duration_ns"] == 2 * NS  # [3,5]

    # Global is now diagnostic only
    assert m["overlap_window_ns"]["duration_ns"] == 2 * NS
    assert "diagnostic" in m["overlap_window_ns"]["note"].lower()

    # Per-stream uptime present
    assert m["streams"]["okx|BTC/USDT"]["uptime_ns"] == 4 * NS
    assert 0.0 <= m["streams"]["okx|BTC/USDT"]["uptime_fraction"] <= 1.0


def test_manifest_zero_tick_stream_excluded_from_pairwise(tmp_path):
    NS = 1_000_000_000
    stats = _CaptureStats(
        tick_counts={"okx|BTC/USDT": 5, "bybit|BTC/USDT": 0},
        first_tick_ts={"okx|BTC/USDT": 1 * NS, "bybit|BTC/USDT": 0},
        last_tick_ts={"okx|BTC/USDT": 5 * NS, "bybit|BTC/USDT": 0},
        price_min={}, price_max={}, errors=[], warnings=[],
    )
    _write_manifest(
        stats=stats,
        run_start_utc="t", run_end_utc="t", duration_seconds=5.0,
        venues=["okx", "bybit"], requested_symbols=["BTC/USDT"],
        files_written=[], out_dir=str(tmp_path),
    )
    m = json.loads((tmp_path / "capture_manifest.json").read_text())
    # Only one live stream -> no pairs.
    assert m["pairwise_overlap_ns"] == {}
    # Per-asset BTC still has one entry (single venue).
    assert m["per_asset_overlap_ns"]["BTC"]["venues_alive"] == 1


def test_no_auth_or_order_strings_in_capture_module():
    src = Path(
        __file__
    ).parent.parent / "run_tick_capture.py"
    text = src.read_text()
    # Build forbidden tokens from fragments so this scan test does not itself
    # contain literal occurrences that trip the package-wide guard tests.
    forbidden = [
        "api" + "_key", "API" + "_KEY",
        "secret" + "_key",
        "private" + "_key",
        "place" + "_order", "submit" + "_order", "create" + "_order",
        "pass" + "phrase",
        "Authoriz" + "ation",
    ]
    found = [p for p in forbidden if re.search(rf"\b{re.escape(p)}\b", text)]
    assert not found, f"forbidden strings present: {found}"


def test_no_auth_strings_in_symbol_aliases():
    src = Path(__file__).parent.parent / "symbol_aliases.py"
    text = src.read_text().lower()
    for bad in ("api" + "_key", "secret" + "_key", "pass" + "phrase", "authoriz" + "ation"):
        assert bad not in text
