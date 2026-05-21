"""
Tests for symbol_aliases.py module.

Explicit mapping layer for venue-specific symbols to canonical (asset, quote) pairs.
"""

import pytest

from examples.strategies.venue_agnostic_signal_observer.symbol_aliases import CanonicalSymbol
from examples.strategies.venue_agnostic_signal_observer.symbol_aliases import quote_mismatch
from examples.strategies.venue_agnostic_signal_observer.symbol_aliases import resolve_symbol
from examples.strategies.venue_agnostic_signal_observer.symbol_aliases import same_asset
from examples.strategies.venue_agnostic_signal_observer.symbol_aliases import same_quote
from examples.strategies.venue_agnostic_signal_observer.symbol_aliases import symbols_match


class TestSymbolResolution:
    """Each venue-specific symbol resolves to the correct canonical form."""

    def test_coinbase_btc_usd(self):
        c = resolve_symbol("BTC-USD")
        assert c == CanonicalSymbol("BTC", "USD")

    def test_coinbase_btc_usd_lowercase(self):
        c = resolve_symbol("btc-usd")
        assert c == CanonicalSymbol("BTC", "USD")

    def test_coinbase_eth_usd(self):
        c = resolve_symbol("ETH-USD")
        assert c == CanonicalSymbol("ETH", "USD")

    def test_coinbase_sol_usd(self):
        c = resolve_symbol("SOL-USD")
        assert c == CanonicalSymbol("SOL", "USD")

    def test_kraken_xbt_usd(self):
        """Kraken XBT/USD maps to BTC/USD"""
        c = resolve_symbol("XBT/USD")
        assert c == CanonicalSymbol("BTC", "USD")

    def test_kraken_xxbtzusd(self):
        """Kraken XXBTZUSD maps to BTC/USD"""
        c = resolve_symbol("XXBTZUSD")
        assert c == CanonicalSymbol("BTC", "USD")

    def test_kraken_xbtzusd(self):
        c = resolve_symbol("XBTZUSD")
        assert c == CanonicalSymbol("BTC", "USD")

    def test_kraken_btc_usd(self):
        """Kraken BTC/USD (modern format) maps to BTC/USD"""
        c = resolve_symbol("BTC/USD")
        assert c == CanonicalSymbol("BTC", "USD")

    def test_kraken_eth_usd(self):
        c = resolve_symbol("ETH/USD")
        assert c == CanonicalSymbol("ETH", "USD")

    @pytest.mark.parametrize("raw", ["AVAX/USD", "AVAX-USD"])
    def test_avax_usd_variants(self, raw: str):
        c = resolve_symbol(raw)
        assert c == CanonicalSymbol("AVAX", "USD")

    @pytest.mark.parametrize("raw", ["UNRESOLVED:AVAX/USD", "UNRESOLVED:AVAX-USD"])
    def test_unresolved_prefix_is_not_a_symbol_alias(self, raw: str):
        with pytest.raises(ValueError):
            resolve_symbol(raw)

    def test_kraken_xethzusd(self):
        c = resolve_symbol("XETHZUSD")
        assert c == CanonicalSymbol("ETH", "USD")

    def test_binance_btcusdt(self):
        c = resolve_symbol("BTCUSDT")
        assert c == CanonicalSymbol("BTC", "USDT")

    def test_binance_ethusd(self):
        c = resolve_symbol("ETHUSD")
        assert c == CanonicalSymbol("ETH", "USD")


class TestUnknownSymbols:
    """Unknown symbols fail clearly rather than being silently inferred."""

    def test_unknown_symbol_raises(self):
        with pytest.raises(ValueError):
            resolve_symbol("FOO-USD")

    def test_unknown_symbol_with_spaces(self):
        with pytest.raises(ValueError):
            resolve_symbol("  FOO BAR  ")

    def test_unknown_slash_raises(self):
        with pytest.raises(ValueError):
            resolve_symbol("FOO/USD")


class TestSymbolComparison:
    """Cross-venue symbol comparison uses canonical form."""

    def test_coinbase_kraken_btc_match(self):
        """Coinbase BTC-USD and Kraken XBT/USD are the same canonical symbol."""
        assert symbols_match("BTC-USD", "XBT/USD")
        assert symbols_match("BTC-USD", "XXBTZUSD")
        assert same_asset("BTC-USD", "XBT/USD")
        assert same_quote("BTC-USD", "XBT/USD")

    def test_coinbase_kraken_eth_match(self):
        assert symbols_match("ETH-USD", "ETH/USD")
        assert symbols_match("ETH-USD", "XETHZUSD")

    def test_different_assets(self):
        assert not same_asset("BTC-USD", "ETH-USD")
        assert not symbols_match("BTC-USD", "ETH-USD")

    def test_different_quotes(self):
        """BTC/USD vs BTC/USDT -- same asset, different quote."""
        assert same_asset("BTCUSD", "BTCUSDT")
        assert not same_quote("BTCUSD", "BTCUSDT")
        assert quote_mismatch("BTCUSD", "BTCUSDT")

    def test_quote_mismatch_flag(self):
        assert quote_mismatch("BTCUSDT", "BTC-USD")
        assert not quote_mismatch("BTC-USD", "XBT/USD")
