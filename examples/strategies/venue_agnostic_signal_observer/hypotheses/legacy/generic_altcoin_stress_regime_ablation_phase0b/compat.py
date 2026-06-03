"""Legacy compatibility exports for the generic altcoin stress regime ablation Phase 0B study."""

from __future__ import annotations

from . import runner as _runner

REQUIRED_LEGACY_SYMBOLS: tuple[str, ...] = (
    "GENERIC_EVENT_DIRECTION",
    "GENERIC_TRADE_DIRECTION",
    "LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS",
    "LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS",
    "LIQUIDATION_BENCHMARK_24H_WIN_RATE",
    "PHASE0A_REQUIRED_STATUS",
    "STAGE",
    "STATUS_ERROR",
    "STATUS_ERROR_INVALID_PHASE0A",
    "STATUS_ERROR_PRECOMMITMENT",
    "STATUS_INSUFFICIENT_FORWARD_COVERAGE",
    "STATUS_RETURN_DIAGNOSTIC_FAIL",
    "STATUS_RETURN_DIAGNOSTIC_PASS",
    "STRESS_COST_BPS",
    "STUDY_ID",
    "VENUE",
    "build_comparison_block",
    "classify_primary_verdict",
    "compute_directional_return_bps",
    "compute_stress_cost_metrics",
    "evaluate_events",
    "load_generic_phase0a_report",
    "run_phase0b",
    "summary_markdown",
    "write_report_artifacts",
)

OPTIONAL_LEGACY_SYMBOLS: tuple[str, ...] = ()

__all__: tuple[str, ...] = REQUIRED_LEGACY_SYMBOLS + OPTIONAL_LEGACY_SYMBOLS


def export_namespace() -> dict[str, object]:
    return {name: getattr(_runner, name) for name in __all__}


globals().update(export_namespace())
