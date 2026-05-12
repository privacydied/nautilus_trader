#!/usr/bin/env python3
"""Tests for V6 market-structure scanner logic."""
import json
import pytest
from dataclasses import dataclass

from examples.strategies.kraken_market_structure_scanner.symbols import (
    get_spec, parse_standard, STANDARD_SYMBOLS,
)
from examples.strategies.kraken_market_structure_scanner.venues import Ticker
from examples.strategies.kraken_market_structure_scanner.opportunity import calculate_opportunity, Opportunity
from examples.strategies.kraken_market_structure_scanner.config import ScannerConfig, FeeConfig


class TestSymbolNormalization:

    def test_parse_standard(self):
        base, quote = parse_standard("BTC/USD")
        assert base == "BTC"
        assert quote == "USD"

    def test_parse_standard_rejects_invalid(self):
        with pytest.raises(ValueError):
            parse_standard("BTCUSD")

    def test_get_spec_known(self):
        spec = get_spec("BTC/USD")
        assert spec.kraken == "XBTUSD"
        assert spec.coinbase == "BTC-USD"

    def test_usdt_spec_exists(self):
        spec = get_spec("BTC/USDT")
        assert spec.quote == "USDT"


class TestOpportunityCalculation:

    def _t(self, venue, bid, ask, quote="USD"):
        return Ticker(venue, "BTC/USD", quote, bid, ask, bid, None, 0)

    def test_cross_venue_opportunity(self):
        """Venue A ask 100, buy; Venue B bid 101, sell → 100bps edge."""
        t1 = self._t("A", 99, 100)  # sell@99, buy@100
        t2 = self._t("B", 101, 102)  # sell@101, buy@102
        # Best opp: buy on A at 100, sell on B at 101 → 100bps gross
        opp = calculate_opportunity(t1, t2, fee_a_bps=10, fee_b_bps=10)
        assert opp is not None
        assert opp.gross_edge_bps > 0
        assert opp.buy_venue == "A"
        assert opp.sell_venue == "B"
        assert opp.buy_ask == 100
        assert opp.sell_bid == 101

    def test_no_opportunity_same_venue(self):
        t1 = self._t("A", 99, 100)
        opp = calculate_opportunity(t1, t1, fee_a_bps=10)
        assert opp is None

    def test_quote_mismatch_returns_none(self):
        t1 = self._t("A", 99, 100, "USD")
        t2 = self._t("B", 101, 102, "USDT")
        assert calculate_opportunity(t1, t2) is None

    def test_fees_erase_small_edge(self):
        t1 = self._t("A", 99.99, 100.01)
        t2 = self._t("B", 100.01, 100.03)
        opp = calculate_opportunity(t1, t2, fee_a_bps=40, fee_b_bps=40)
        assert opp is not None
        assert not opp.is_profitable  # 80+10 buffer wipes any tiny edge

    def test_missing_bid_ask_returns_none(self):
        t1 = Ticker("A", "BTC/USD", "USD", None, None, None, None, 0)
        t2 = self._t("B", 100, 101)
        assert calculate_opportunity(t1, t2) is None

    def test_latnecy_buffer_reduces_net(self):
        t1 = self._t("A", 99, 100)
        t2 = self._t("B", 103, 104)
        opp = calculate_opportunity(t1, t2, fee_a_bps=0, fee_b_bps=0, latency_buffer_bps=50)
        assert opp is not None
        # Gross ~ 300 bps, minus 0 fees, minus 50 latency = ~250 net
        assert opp.net_edge_bps == round(opp.gross_edge_bps - 50, 4)
