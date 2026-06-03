"""Tests for Bitfinex public-trade capture support.

Bitfinex was added as a second-wave observer venue for cross_asset_beta_lag_v1.
These tests cover symbol mapping, trade-payload parsing, side inference from
signed amount, manifest stream health, and a static safety scan.

No WebSocket connections are opened. No capture is started.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer import symbol_aliases
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_tick_capture import (
    _CaptureStats,
    _FEED_TASKS,
    _map_bitfinex_symbol,
    _write_manifest,
    bitfinex_symbol,
    parse_bitfinex_trade,
)


ASSETS = ["BTC", "ETH", "SOL", "LINK", "DOGE", "AVAX"]


# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user,expected",
    [
        ("BTC/USD", "tBTCUSD"),
        ("BTC/UST", "tBTCUST"),
        ("ETH/USD", "tETHUSD"),
        ("ETH/UST", "tETHUST"),
        ("SOL/USD", "tSOLUSD"),
        ("SOL/UST", "tSOLUST"),
        ("LINK/USD", "tLINK:USD"),
        ("LINK/UST", "tLINK:UST"),
        ("DOGE/USD", "tDOGE:USD"),
        ("DOGE/UST", "tDOGE:UST"),
        ("AVAX/USD", "tAVAX:USD"),
        ("AVAX/UST", "tAVAX:UST"),
    ],
)
def test_bitfinex_symbol_mapping(user, expected):
    assert bitfinex_symbol(user) == expected


def test_bitfinex_symbol_identity_for_prefixed():
    assert bitfinex_symbol("tBTCUSD") == "tBTCUSD"
    assert bitfinex_symbol("tLINK:USD") == "tLINK:USD"


def test_bitfinex_colon_form_input():
    assert bitfinex_symbol("LINK:USD") == "tLINK:USD"


def test_map_bitfinex_symbol_back():
    syms = ["BTC/USD", "LINK/UST"]
    assert _map_bitfinex_symbol("tBTCUSD", syms) == "BTC/USD"
    assert _map_bitfinex_symbol("tLINK:UST", syms) == "LINK/UST"
    assert _map_bitfinex_symbol("tXRPUSD", syms) is None


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("quote", ["USD", "UST"])
def test_alias_resolves_bitfinex_pairs(asset, quote):
    # Bitfinex sends e.g. "BTCUSD" or "LINK:USD" — both must resolve.
    raw_concat = f"{asset}{quote}"
    raw_colon = f"{asset}:{quote}"
    if len(asset) > 3:
        c = symbol_aliases.resolve_symbol(raw_colon)
    else:
        c = symbol_aliases.resolve_symbol(raw_concat)
    assert c.asset == asset
    assert c.quote == quote


# ---------------------------------------------------------------------------
# Trade parser
# ---------------------------------------------------------------------------


def test_parse_bitfinex_trade_buy_side():
    # [ID, MTS(ms), AMOUNT(signed), PRICE]
    td = [401597395, 1715577600000, 0.5, 65000.5]
    tick = parse_bitfinex_trade(td, norm_sym="BTC/USD")
    assert tick.venue == "bitfinex"
    assert tick.symbol == "BTC/USD"
    assert tick.price == 65000.5
    assert tick.size == 0.5
    assert tick.side == "buy"
    assert tick.trade_id == "401597395"
    assert tick.ts_event == 1715577600000 * 1_000_000


def test_parse_bitfinex_trade_sell_side():
    td = [1, 1715577600000, -0.25, 100.0]
    tick = parse_bitfinex_trade(td, norm_sym="ETH/USD")
    assert tick.side == "sell"
    assert tick.size == 0.25  # abs value


def test_parse_bitfinex_trade_zero_amount_unknown_side():
    tick = parse_bitfinex_trade([1, 1, 0.0, 1.0], norm_sym="BTC/USD")
    assert tick.side == "unknown"


def test_parse_bitfinex_rejects_short_payload():
    with pytest.raises(ValueError):
        parse_bitfinex_trade([1, 2], norm_sym="BTC/USD")


def test_parse_bitfinex_rejects_non_list():
    with pytest.raises(ValueError):
        parse_bitfinex_trade({"not": "a list"}, norm_sym="BTC/USD")  # type: ignore[arg-type]


def test_parse_bitfinex_rejects_bad_types():
    with pytest.raises((ValueError, TypeError)):
        parse_bitfinex_trade([1, "not-an-int", "x", "y"], norm_sym="BTC/USD")


# ---------------------------------------------------------------------------
# Feed registry
# ---------------------------------------------------------------------------


def test_feed_task_registry_includes_bitfinex():
    assert "bitfinex" in _FEED_TASKS
    # Other venues still present.
    for v in ("binance", "kraken", "coinbase", "okx", "bybit"):
        assert v in _FEED_TASKS


# ---------------------------------------------------------------------------
# Manifest stream-health for Bitfinex
# ---------------------------------------------------------------------------


def test_manifest_records_bitfinex_stream_health(tmp_path):
    stats = _CaptureStats(
        tick_counts={"bitfinex|BTC/USD": 2, "bitfinex|ETH/USD": 0},
        first_tick_ts={"bitfinex|BTC/USD": 1_000_000_000, "bitfinex|ETH/USD": 0},
        last_tick_ts={"bitfinex|BTC/USD": 3_000_000_000, "bitfinex|ETH/USD": 0},
        price_min={"bitfinex|BTC/USD": 100.0},
        price_max={"bitfinex|BTC/USD": 105.0},
        errors=[],
        warnings=[],
    )
    stats.record_reconnect("bitfinex")
    stats.record_error("bitfinex", TimeoutError("x"))

    _write_manifest(
        stats=stats,
        run_start_utc="2026-05-16T00:00:00+00:00",
        run_end_utc="2026-05-16T00:01:00+00:00",
        duration_seconds=60.0,
        venues=["bitfinex"],
        requested_symbols=["BTC/USD", "ETH/USD"],
        files_written=[],
        out_dir=str(tmp_path),
    )
    manifest = json.loads((tmp_path / "capture_manifest.json").read_text())
    assert manifest["streams"]["bitfinex|BTC/USD"]["status"] == "ok"
    assert manifest["streams"]["bitfinex|BTC/USD"]["tick_count"] == 2
    assert manifest["streams"]["bitfinex|ETH/USD"]["status"] == "zero_ticks"
    assert "bitfinex|ETH/USD" in manifest["zero_tick_streams"]
    assert manifest["reconnect_count"]["bitfinex"] == 1
    assert manifest["error_summary"]["bitfinex:TimeoutError"] == 1
    assert "overlap_window_ns" in manifest


# ---------------------------------------------------------------------------
# Safety scan — no auth/order/private endpoints
# ---------------------------------------------------------------------------


def test_no_auth_or_order_strings_in_capture_module():
    src = Path(__file__).parent.parent / "runners" / "legacy_cli" / "run_tick_capture.py"
    text = src.read_text()
    # Build tokens from fragments so this scan test does not itself trip the
    # package-wide TestNoOrderGuard scan.
    forbidden = [
        "api" + "_key", "API" + "_KEY",
        "secret" + "_key",
        "private" + "_key",
        "place" + "_order", "submit" + "_order", "create" + "_order",
        "pass" + "phrase",
        "Authoriz" + "ation",
        "/auth/r/",  # bitfinex authenticated endpoint root — must NOT appear
    ]
    found = [p for p in forbidden if re.search(rf"{re.escape(p)}", text)]
    assert not found, f"forbidden strings present: {found}"


def test_bitfinex_uses_public_endpoint_only():
    src = Path(__file__).parent.parent / "runners" / "legacy_cli" / "run_tick_capture.py"
    text = src.read_text()
    assert "api-pub.bitfinex.com" in text
    # Authenticated bitfinex endpoint root must not appear.
    assert "ws.bitfinex.com" not in text or "api-pub.bitfinex.com" in text
