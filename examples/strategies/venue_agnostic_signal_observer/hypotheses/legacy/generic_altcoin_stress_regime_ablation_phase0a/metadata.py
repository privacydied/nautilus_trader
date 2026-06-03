from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenericAltcoinStressRegimeAblationPhase0AMetadata:
    key: str = "generic_altcoin_stress_regime_ablation_phase0a"
    study_id: str = "generic_altcoin_stress_regime_ablation_phase0"
    family: str = "altcoin_stress_regime_ablation"
    venue: str = "hyperliquid"
    description: str = (
        "Generic altcoin stress regime ablation Phase 0A feasibility/coverage audit."
    )
    cli_module: str = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0a"
    implementation_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a.runner"
    package_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a"
    legacy_module: str = "examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a"
    tags: tuple[str, ...] = (
        "observer_only",
        "legacy",
        "altcoin_stress_regime_ablation",
        "compatibility_cli",
    )


METADATA = GenericAltcoinStressRegimeAblationPhase0AMetadata()

__all__ = ("GenericAltcoinStressRegimeAblationPhase0AMetadata", "METADATA")
