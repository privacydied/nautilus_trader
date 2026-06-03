"""Metadata for Hyperliquid OI velocity compression Phase 0."""

from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    HypothesisPackageMetadata,
)

METADATA = HypothesisPackageMetadata(
    key="hyperliquid_oi_velocity_compression_phase0",
    study_id="hyperliquid_oi_velocity_compression_phase0",
    family="oi_velocity_compression",
    venue="hyperliquid",
    phase="phase0",
    description="Hyperliquid OI velocity compression Phase 0 study.",
    tags=(
        "observer_only",
        "hyperliquid",
        "oi_velocity",
        "open_interest",
        "phase0",
        "compatibility_cli",
    ),
    cli_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "runners.legacy_cli.run_hyperliquid_oi_velocity_compression_phase0"
    ),
    implementation_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "hypotheses.hyperliquid.oi_velocity_compression_phase0.runner"
    ),
    package_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "hypotheses.hyperliquid.oi_velocity_compression_phase0"
    ),
    legacy_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "hyperliquid_oi_velocity_compression_phase0"
    ),
)

__all__ = ("METADATA",)
