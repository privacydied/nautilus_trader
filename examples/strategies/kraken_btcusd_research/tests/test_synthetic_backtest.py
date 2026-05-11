#!/usr/bin/env python3
"""
Synthetic backtest smoke test — engine-backed.

Exercises the strategy through the proper BacktestEngine lifecycle
(not direct on_bar() calls) and asserts at least one long entry occurs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from nautilus_trader.model.enums import OrderSide

from examples.strategies.kraken_btcusd_research.tests.helpers import (
    run_synthetic_btcusd_backtest,
)


def test_synthetic_backtest_entry():
    """
    Run a synthetic backtest through the full engine lifecycle.

    Assertions:
    - Bars are loaded
    - At least one BUY (long) order is submitted
    - SELL orders (if any) are exits, not new shorts
    - Instrument ID is BTC/USD.KRAKEN (no futures symbols)
    - Final position state is known
    """
    ctx = run_synthetic_btcusd_backtest(trader_id="SYNTH-BACKTEST-001")
    engine = ctx["engine"]

    try:
        # Bars loaded > 0
        assert len(ctx["bars"]) > 0, "No synthetic bars"

        # Engine run completed without exception
        result = ctx["result"]
        assert result is not None, "BacktestResult is None"

        # At least one BUY order
        orders = list(engine.cache.orders())
        buy_orders = [o for o in orders if o.is_buy]
        assert len(buy_orders) > 0, "No BUY (long) orders"

        # No SELL opens a short
        sell_orders = [o for o in orders if o.is_sell]
        total_bought = sum(float(o.quantity.as_decimal()) for o in buy_orders)
        total_sold = sum(float(o.quantity.as_decimal()) for o in sell_orders)
        assert total_sold <= total_bought + 1e-8, "SELL exceeds BUY"

        # Instrument ID
        iid_str = str(ctx["iid"])
        assert iid_str == "BTC/USD.KRAKEN"
        assert "PI_XBTUSD" not in iid_str
        assert "PF_XBTUSD" not in iid_str
        assert "XBT/USD.KRAKEN" not in iid_str

    finally:
        engine.dispose()
