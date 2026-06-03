from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HyperliquidFundingDivergencePhase0Metadata:
    key: str = "hyperliquid_funding_divergence_phase0"
    study_id: str = "hyperliquid_funding_divergence_phase0"
    family: str = "funding_divergence"
    venue: str = "hyperliquid"
    description: str = "Distribution-only Hyperliquid funding divergence Phase 0 audit."
    cli_module: str = "examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_divergence_phase0"
    implementation_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0.runner"
    package_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0"
    legacy_module: str = "examples.strategies.venue_agnostic_signal_observer.hyperliquid_funding_divergence_phase0"
    tags: tuple[str, ...] = (
        "observer_only",
        "hyperliquid",
        "funding_divergence",
        "compatibility_cli",
    )


METADATA = HyperliquidFundingDivergencePhase0Metadata()

__all__ = ("HyperliquidFundingDivergencePhase0Metadata", "METADATA")
