from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Hip3FlxStaleOracleFundingBiasPhaseMinus2V0Metadata:
    key: str = "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
    study_id: str = "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
    family: str = "hip3_flx_stale_oracle_funding_bias"
    venue: str = "hyperliquid"
    description: str = (
        "HIP-3 FLX stale-oracle funding-bias Phase -2 v0 standalone study "
        "(observer-only oracle alignment / bias decision diagnostic)."
    )
    cli_module: str = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
    implementation_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0.runner"
    package_module: str = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
    legacy_module: str = "examples.strategies.venue_agnostic_signal_observer.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
    tags: tuple[str, ...] = (
        "observer_only",
        "legacy",
        "hip3_flx_stale_oracle_funding_bias",
        "compatibility_cli",
    )


METADATA = Hip3FlxStaleOracleFundingBiasPhaseMinus2V0Metadata()

__all__ = ("Hip3FlxStaleOracleFundingBiasPhaseMinus2V0Metadata", "METADATA")
