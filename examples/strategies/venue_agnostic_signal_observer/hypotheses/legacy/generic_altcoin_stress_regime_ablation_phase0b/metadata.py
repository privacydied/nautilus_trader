from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenericAltcoinStressRegimeAblationPhase0BMetadata:
    key: str = "generic_altcoin_stress_regime_ablation_phase0b"
    study_id: str = "generic_altcoin_stress_regime_ablation_phase0"
    family: str = "altcoin_stress_regime_ablation"
    venue: str = "hyperliquid"
    description: str = (
        "Forward-return diagnostic ablation for a price-only generic altcoin "
        "stress regime detector (Phase 0B)."
    )
    cli_module: str = "examples.strategies.venue_agnostic_signal_observer.run_generic_altcoin_stress_regime_ablation_phase0b"
    implementation_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0b.runner"
    package_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0b"
    legacy_module: str = "examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0b"
    tags: tuple[str, ...] = (
        "observer_only",
        "legacy",
        "altcoin_stress_regime_ablation",
        "compatibility_cli",
    )


METADATA = GenericAltcoinStressRegimeAblationPhase0BMetadata()

__all__ = ("GenericAltcoinStressRegimeAblationPhase0BMetadata", "METADATA")
