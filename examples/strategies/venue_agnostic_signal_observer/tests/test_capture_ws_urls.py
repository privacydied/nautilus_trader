"""Unit tests for capture runner WebSocket URL construction and diagnostics.

Verifies:
- Binance perp aggTrade combined stream URL uses /market routed path.
- Kraken WebSocket URL uses v2 endpoint.
- StreamStats diagnostics field works correctly.
"""
import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_derivatives_spot_capture import (
    BINANCE_PERP_WS_BASE,
    KRAKEN_WS_URL,
    build_binance_perp_ws_url,
    StreamStats,
)


# ---------------------------------------------------------------------------
# Binance perp URL builder
# ---------------------------------------------------------------------------

class TestBinancePerpUrlBuilder:

    def test_url_uses_market_routed_path(self):
        """The URL must use /market/stream, not /stream."""
        url = build_binance_perp_ws_url(["BTC/USDT"])
        assert "/market/stream?" in url, (
            f"Binance perp URL must use /market routed path, got: {url}"
        )

    def test_url_not_using_unrouted_path(self):
        """The URL must NOT use the old /stream path without /market."""
        url = build_binance_perp_ws_url(["BTC/USDT"])
        # The old broken URL was wss://fstream.binance.com/stream?streams=...
        # The correct URL is wss://fstream.binance.com/market/stream?streams=...
        # Check that the path includes /market before /stream
        from urllib.parse import urlparse
        parsed = urlparse(url)
        assert parsed.path == "/market/stream", (
            f"URL path must be /market/stream, got: {parsed.path}"
        )

    def test_single_symbol(self):
        url = build_binance_perp_ws_url(["BTC/USDT"])
        assert url == f"{BINANCE_PERP_WS_BASE}?streams=btcusdt@aggTrade"

    def test_multiple_symbols(self):
        url = build_binance_perp_ws_url(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        assert url == (
            f"{BINANCE_PERP_WS_BASE}"
            "?streams=btcusdt@aggTrade/ethusdt@aggTrade/solusdt@aggTrade"
        )

    def test_base_constant_value(self):
        """The base URL constant must be the /market routed endpoint."""
        assert BINANCE_PERP_WS_BASE == "wss://fstream.binance.com/market/stream", (
            f"BINANCE_PERP_WS_BASE should be the /market routed path, "
            f"got: {BINANCE_PERP_WS_BASE}"
        )

    def test_stream_names_are_lowercase(self):
        url = build_binance_perp_ws_url(["BTC/USDT"])
        assert "btcusdt@aggTrade" in url
        assert "BTCUSDT" not in url.split("?")[1]  # query part must be lowercase


# ---------------------------------------------------------------------------
# Kraken URL constant
# ---------------------------------------------------------------------------

class TestKrakenUrl:

    def test_kraken_uses_v2_endpoint(self):
        """Kraken must use the v2 WebSocket API endpoint."""
        assert KRAKEN_WS_URL == "wss://ws.kraken.com/v2", (
            f"Kraken WS URL must use v2 endpoint, got: {KRAKEN_WS_URL}"
        )

    def test_kraken_not_using_v1_endpoint(self):
        """Must NOT use the old v1 endpoint which only works with v1 format."""
        assert KRAKEN_WS_URL != "wss://ws.kraken.com", (
            "Kraken WS URL must not be the v1-only endpoint"
        )


# ---------------------------------------------------------------------------
# StreamStats diagnostics
# ---------------------------------------------------------------------------

class TestStreamStatsDiagnostics:

    def test_diagnostics_in_to_dict(self):
        s = StreamStats("test_stream")
        s.diagnostics.append("subscribe_ack: BTC/USD success=True")
        d = s.to_dict()
        assert "diagnostics" in d
        assert d["diagnostics"] == ["subscribe_ack: BTC/USD success=True"]

    def test_empty_diagnostics_default(self):
        s = StreamStats("test_stream")
        d = s.to_dict()
        assert "diagnostics" in d
        assert d["diagnostics"] == []

    def test_zero_tick_no_ws_data_diagnostic(self):
        """Diagnostic scenario: 0 ticks with no WS data frames."""
        s = StreamStats("binance_perp_BTC/USDT")
        s.status = "ok"
        s.tick_count = 0
        # No ws_received_first_message diagnostic
        assert "ws_received_first_message" not in s.diagnostics
        assert s.tick_count == 0