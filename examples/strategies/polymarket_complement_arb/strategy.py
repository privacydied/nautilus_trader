"""
Strategy class for Polymarket complement arb.

This is a Nautilus Strategy that manages paired YES+NO positions across
multiple binary markets. In observe mode, only detection and passive fill
estimation run. In live mode, order submission is guarded.

The strategy owns: market filtering, opportunity detection, state machine,
order construction, and event handling. It delegates to:
- edge_model.py for cost/edge math
- detector.py for opportunity detection
- passive_fill_estimator.py for fill plausibility
- sizing.py for position sizing
- state_machine.py for lifecycle management
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.model.data import Bar, QuoteTick, TradeTick
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.orders import Order
from nautilus_trader.trading import Strategy


class ComplementArbStrategy(Strategy):
    """
    Polymarket Global complementary YES+NO arbitrage strategy.

    Resting maker quotes on both legs of same-condition binary pairs.
    If one leg fills, attempts to close the second via taker if profitable,
    or rests the second leg with timeout-based unwind.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config)
        self.instrument_provider = None
        self.state_machines: dict[str, Any] = {}

    def on_start(self) -> None:
        """Called when the strategy starts."""
        self.log.info("ComplementArbStrategy started")

    def on_stop(self) -> None:
        """Called when the strategy stops."""
        self.log.info("ComplementArbStrategy stopped")

    def on_resume(self) -> None:
        """Called when the strategy resumes."""
        self.log.info("ComplementArbStrategy resumed")

    def on_reset(self) -> None:
        """Called when the strategy is reset."""
        self.state_machines.clear()
        self.log.info("ComplementArbStrategy reset")

    def on_save(self) -> dict[str, Any]:
        """Save strategy state."""
        return {"state_machine_count": len(self.state_machines)}

    def on_load(self, state: dict[str, Any]) -> None:
        """Load strategy state."""
        self.log.info(f"Loaded state: {state}")

    def on_dispose(self) -> None:
        """Called when the strategy is disposed."""
        self.state_machines.clear()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Called when a quote tick is received."""
        pass

    def on_trade_tick(self, tick: TradeTick) -> None:
        """Called when a trade tick is received."""
        pass

    def on_bar(self, bar: Bar) -> None:
        """Called when a bar is received."""
        pass

    def on_order_filled(self, order: Order) -> None:
        """Called when an order is filled."""
        pass

    def on_order_rejected(self, order: Order) -> None:
        """Called when an order is rejected."""
        pass

    def on_order_canceled(self, order: Order) -> None:
        """Called when an order is cancelled."""
        pass

    def on_order_expired(self, order: Order) -> None:
        """Called when an order expires."""
        pass

    def on_order_triggered(self, order: Order) -> None:
        """Called when a stop order is triggered."""
        pass

    def on_order_updated(self, order: Order) -> None:
        """Called when an order is updated."""
        pass
