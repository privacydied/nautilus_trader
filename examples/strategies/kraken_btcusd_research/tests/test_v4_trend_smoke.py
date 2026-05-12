#!/usr/bin/env python3
"""
V4 1h Trend-Following Smoke Tests.

Tests for the V4 1h trend strategy.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import pytest
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.objects import Price, Quantity


from examples.strategies.kraken_btcusd_research.config_v4 import (
    INSTRUMENT_ID,
    EMA_FAST_PERIODS,
    EMA_SLOW_PERIODS,
    DONCHIAN_WINDOW,
    TRAILING_STOP_ATR_MULTIPLIER,
    TIMEFRAME_BARS,
)


class TestV4Config:
    """V4 config constants."""

    def test_parameters_set(self):
        assert EMA_FAST_PERIODS == 50
        assert EMA_SLOW_PERIODS == 200
        assert DONCHIAN_WINDOW == 100
        assert TRAILING_STOP_ATR_MULTIPLIER == 4.0
        assert TIMEFRAME_BARS == 60

    def test_instrument_is_btcusd_kraken(self):
        iid = InstrumentId.from_str(INSTRUMENT_ID)
        assert iid.venue.value == "KRAKEN"
        assert iid.symbol.value == "BTC/USD"

    def test_no_futures_symbols(self):
        assert "PI_XBT" not in INSTRUMENT_ID
        assert "PF_XBT" not in INSTRUMENT_ID


class TestV4StrategyImports:
    """Import smoke tests."""

    def test_config_imports(self):
        from examples.strategies.kraken_btcusd_research.config_v4 import (
            INSTRUMENT_ID, EMA_FAST_PERIODS, EMA_SLOW_PERIODS,
        )
        assert EMA_FAST_PERIODS < EMA_SLOW_PERIODS

    def test_strategy_imports(self):
        from examples.strategies.kraken_btcusd_research.strategy_v4 import (
            KrakenBTCUSDV4TrendStrategy,
            KrakenBTCUSDV4TrendConfig,
            _calculate_position_size,
        )

    def test_no_market_instrument(self):
        """Ensure no futures/margin/leverage/short symbols."""
        iid = InstrumentId.from_str(INSTRUMENT_ID)
        assert "PI_XBT" not in str(iid)
        assert "PF_XBT" not in str(iid)
        assert "XBT" not in str(iid)


class TestV4PositionSize:
    """Test position sizing logic."""

    def test_basic_position_size(self):
        from examples.strategies.kraken_btcusd_research.strategy_v4 import (
            _calculate_position_size,
        )
        size = _calculate_position_size(
            account_value=Decimal("10000"),
            risk_percent=0.0025,
            entry_price=Decimal("50000"),
            stop_price=Decimal("48000"),
            notional_limit=0.30,
            min_size=0.001,
            fee_rate=0.004,
        )
        assert size is not None
        assert float(size.as_decimal()) > 0
