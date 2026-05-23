#!/usr/bin/env python3
"""
Synthetic breakout backtest smoke test — engine-backed.

Runs the strategy through the proper BacktestEngine lifecycle
and asserts at least one long entry occurs on synthetic breakout data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from nautilus_trader.model.enums import OrderSide

from examples.strategies.kraken_btcusd_research.tests.helpers import (
    run_synthetic_btcusd_backtest,
)


def test_synthetic_breakout():
    """
    Verify the strategy generates at least one BUY order on synthetic
    breakout data, and never sells to open a short position.

    Uses the shared run_synthetic_btcusd_backtest helper — never calls
    on_bar() directly.
    """
    ctx = run_synthetic_btcusd_backtest(trader_id="BREAKOUT-TEST-001")
    engine = ctx["engine"]

    try:
        # Bars were loaded
        assert len(ctx["bars"]) > 0, "No synthetic bars"

        # Engine run completed without exception (we got here)
        assert ctx["result"] is not None, "BacktestResult is None"

        # At least one order was submitted
        orders = list(engine.cache.orders())
        assert len(orders) > 0, "No orders submitted during backtest"

        # At least one BUY (long entry) order
        buy_orders = [o for o in orders if o.is_buy]
        assert len(buy_orders) > 0, "No BUY (long) orders — breakout entry did not trigger"

        # No SELL order opens a short — the strategy is long-only.
        # SELL orders are allowed only as exits from a long position.
        sell_orders = [o for o in orders if o.is_sell]
        total_bought = sum(float(o.quantity.as_decimal()) for o in buy_orders)
        total_sold = sum(float(o.quantity.as_decimal()) for o in sell_orders)
        assert total_sold <= total_bought + 1e-8, (
            f"SELL quantity {total_sold} exceeds BUY quantity {total_bought} — possible short"
        )

        # Instrument ID must be BTC/USD.KRAKEN
        iid_str = str(ctx["iid"])
        assert iid_str == "BTC/USD.KRAKEN", f"Wrong instrument: {iid_str}"

        # No futures symbols
        assert "PI_XBT" not in iid_str
        assert "PF_XBT" not in iid_str
        assert "XBT/USD.KRAKEN" not in iid_str

        # Fills — at least one long fill
        fills_report = engine.trader.generate_order_fills_report()
        assert len(fills_report) > 0, "No fills despite orders"
        print(f"  {len(orders)} order(s), {len(fills_report)} fill(s)")

    finally:
        engine.dispose()


if __name__ == "__main__":
    test_synthetic_breakout()
    print("OK")
