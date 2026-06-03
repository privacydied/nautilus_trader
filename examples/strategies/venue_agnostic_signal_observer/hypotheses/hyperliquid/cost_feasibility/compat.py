"""Compatibility exports for the Hyperliquid cost-feasibility package."""

from __future__ import annotations

from . import runner as _runner

REQUIRED_LEGACY_SYMBOLS: tuple[str, ...] = (
    "PROCEED_TO_V1_EVALUATION",
    "DO_NOT_PROCEED_COST_WALL_PERSISTS",
    "INSUFFICIENT_LIVE_CAPTURE",
    "AWAITING_STRESS_WINDOWS",
    "DataSource",
    "CoinCostSummary",
    "CostFeasibilityResult",
    "compute_cost_feasibility",
    "write_cost_outputs",
)

OPTIONAL_LEGACY_SYMBOLS: tuple[str, ...] = ()

__all__: tuple[str, ...] = (
    *REQUIRED_LEGACY_SYMBOLS,
    *OPTIONAL_LEGACY_SYMBOLS,
)


def export_namespace() -> dict[str, object]:
    return {name: getattr(_runner, name) for name in __all__}
