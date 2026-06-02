from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    HypothesisPackageMetadata,
)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    validate_hypothesis_metadata,
)

METADATA = validate_hypothesis_metadata(
    HypothesisPackageMetadata(
        key="hyperliquid_cost_feasibility",
        study_id="hyperliquid_cost_feasibility",
        family="cost_feasibility",
        venue="hyperliquid",
        phase="feasibility",
        description="Hyperliquid cost-feasibility study.",
        tags=(
            "observer_only",
            "hyperliquid",
            "cost_feasibility",
            "feasibility",
            "compatibility_cli",
        ),
        cli_module="examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_cost_feasibility",
        implementation_module="examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.cost_feasibility.runner",
        package_module="examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.cost_feasibility",
        legacy_module="examples.strategies.venue_agnostic_signal_observer.hyperliquid_cost_feasibility",
    )
)

__all__ = ("METADATA",)
