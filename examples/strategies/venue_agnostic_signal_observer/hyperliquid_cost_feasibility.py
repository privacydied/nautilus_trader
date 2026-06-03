"""Compatibility wrapper for the Hyperliquid cost-feasibility study."""

from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.cost_feasibility.compat import (
    __all__ as __all__,
)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.cost_feasibility.compat import (
    export_namespace as _export_namespace,
)

globals().update(_export_namespace())

del _export_namespace
