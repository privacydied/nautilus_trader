"""Manual opt-in runner CLI specs.

This module is metadata-only. Importing it must not import runner implementations,
legacy run_*.py modules, registry output code, or execution paths.
"""

from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
    RunnerCliRisk,
    RunnerCliSpec,
    validate_runner_cli_spec,
)

HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC = validate_runner_cli_spec(
    RunnerCliSpec(
        key="hyperliquid_cost_feasibility",
        description="Hyperliquid cost-feasibility CLI metadata.",
        entry_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_cost_feasibility"
        ),
        implementation_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.cost_feasibility.runner"
        ),
        metadata_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.cost_feasibility.metadata"
        ),
        build_parser_callable=None,
        main_callable=None,
        level=RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA,
        risk=RunnerCliRisk.PURE_WRAPPER_READY,
        supports_help_only=True,
        import_safe=True,
        writes_artifacts=False,
        requires_network=False,
        requires_archive_data=False,
        requires_reports_dir=False,
        observer_only=True,
        paper_or_governance_sensitive=False,
        live_or_service_sensitive=False,
        registered=True,
        legacy_entrypoint_path=(
            "examples/strategies/venue_agnostic_signal_observer/"
            "run_hyperliquid_cost_feasibility.py"
        ),
        test_modules=(
            "examples.strategies.venue_agnostic_signal_observer.tests."
            "unit.hypotheses.hyperliquid."
            "test_hyperliquid_cost_feasibility_contract",
        ),
    )
)

HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC = validate_runner_cli_spec(
    RunnerCliSpec(
        key="hyperliquid_oi_velocity_compression_phase0",
        description="Hyperliquid OI velocity compression Phase 0 study.",
        entry_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_oi_velocity_compression_phase0"
        ),
        implementation_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.oi_velocity_compression_phase0.runner"
        ),
        metadata_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.oi_velocity_compression_phase0.metadata"
        ),
        build_parser_callable=None,
        main_callable=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_oi_velocity_compression_phase0:main"
        ),
        level=RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES,
        risk=RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY,
        supports_help_only=True,
        import_safe=True,
        writes_artifacts=False,
        requires_network=False,
        requires_archive_data=False,
        requires_reports_dir=False,
        observer_only=True,
        paper_or_governance_sensitive=False,
        live_or_service_sensitive=False,
        registered=True,
        legacy_entrypoint_path=(
            "examples/strategies/venue_agnostic_signal_observer/"
            "run_hyperliquid_oi_velocity_compression_phase0.py"
        ),
        test_modules=(
            "examples.strategies.venue_agnostic_signal_observer.tests.unit.hypotheses.hyperliquid.test_hyperliquid_oi_velocity_compression_package_contract",
        ),
    )
)

GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC = validate_runner_cli_spec(
    RunnerCliSpec(
        key="generic_altcoin_stress_regime_ablation_phase0b",
        description=(
            "Generic altcoin stress regime ablation Phase 0B legacy CLI metadata "
            "(help-safe only)."
        ),
        entry_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_generic_altcoin_stress_regime_ablation_phase0b"
        ),
        implementation_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0b.runner"
        ),
        metadata_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0b.metadata"
        ),
        build_parser_callable=None,
        main_callable=None,
        level=RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA,
        risk=RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY,
        supports_help_only=True,
        import_safe=True,
        writes_artifacts=False,
        requires_network=False,
        requires_archive_data=False,
        requires_reports_dir=False,
        observer_only=True,
        paper_or_governance_sensitive=False,
        live_or_service_sensitive=False,
        registered=True,
        legacy_entrypoint_path=(
            "examples/strategies/venue_agnostic_signal_observer/"
            "run_generic_altcoin_stress_regime_ablation_phase0b.py"
        ),
        test_modules=(
            "examples.strategies.venue_agnostic_signal_observer.tests."
            "unit.hypotheses.legacy."
            "test_generic_altcoin_stress_regime_ablation_phase0b_package_contract",
        ),
    )
)

GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC = validate_runner_cli_spec(
    RunnerCliSpec(
        key="generic_altcoin_stress_regime_ablation_phase0a",
        description=(
            "Generic altcoin stress regime ablation Phase 0A legacy CLI metadata "
            "(help-safe only)."
        ),
        entry_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_generic_altcoin_stress_regime_ablation_phase0a"
        ),
        implementation_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a.runner"
        ),
        metadata_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a.metadata"
        ),
        build_parser_callable=None,
        main_callable=None,
        level=RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA,
        risk=RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY,
        supports_help_only=True,
        import_safe=True,
        writes_artifacts=False,
        requires_network=False,
        requires_archive_data=False,
        requires_reports_dir=False,
        observer_only=True,
        paper_or_governance_sensitive=False,
        live_or_service_sensitive=False,
        registered=True,
        legacy_entrypoint_path=(
            "examples/strategies/venue_agnostic_signal_observer/"
            "run_generic_altcoin_stress_regime_ablation_phase0a.py"
        ),
        test_modules=(
            "examples.strategies.venue_agnostic_signal_observer.tests."
            "unit.hypotheses.legacy."
            "test_generic_altcoin_stress_regime_ablation_phase0a_package_contract",
        ),
    )
)

ALL_RUNNER_CLI_SPECS: tuple[RunnerCliSpec, ...] = (
    HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC,
)


def iter_runner_cli_specs() -> tuple[RunnerCliSpec, ...]:
    return ALL_RUNNER_CLI_SPECS


def get_runner_cli_spec(key: str) -> RunnerCliSpec | None:
    stripped = key.strip()
    for spec in ALL_RUNNER_CLI_SPECS:
        if spec.key == stripped:
            return spec
    return None


def require_runner_cli_spec(key: str) -> RunnerCliSpec:
    spec = get_runner_cli_spec(key)
    if spec is None:
        raise KeyError(f"Unknown runner CLI spec: {key!r}")
    return spec


__all__ = (
    "ALL_RUNNER_CLI_SPECS",
    "GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC",
    "GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC",
    "HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC",
    "HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC",
    "get_runner_cli_spec",
    "iter_runner_cli_specs",
    "require_runner_cli_spec",
)
