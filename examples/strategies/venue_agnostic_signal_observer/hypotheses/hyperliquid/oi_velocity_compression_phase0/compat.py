"""Legacy compatibility exports for Hyperliquid OI velocity compression Phase 0."""

from __future__ import annotations

from . import runner as _runner

REQUIRED_LEGACY_SYMBOLS: tuple[str, ...] = (
    "FROZEN_SYMBOLS",
    "PHASE0_READY_FOR_V1_PRECOMMITMENT",
    "TERMINAL_VERDICTS",
    "PRICE_FIELD",
    "SECOND_DIRECTION_PROXY",
    "WARMUP_DAYS",
    "HORIZONS_H",
    "Phase0Config",
    "CoverageResult",
    "parse_ts",
    "compute_symbol_list_hash",
    "sha256_file",
    "verify_precommitment_hash",
    "_module_names_from_ast",
    "enforce_funding_quarantine",
    "_read_rows",
    "_symbol_path",
    "_float_field",
    "load_symbol_frame",
    "inspect_symbol_coverage",
    "compute_oi_velocity_bps",
    "compute_realized_vol_bps",
    "compute_past_percentile_ranks",
    "select_first_wins_events",
    "_build_symbol_events",
    "evaluate_phase0b",
    "_median",
    "evaluate_phase0c",
    "_git_metadata",
    "_write_csv",
    "_jsonable",
    "run_phase0_pipeline",
)

OPTIONAL_LEGACY_SYMBOLS: tuple[str, ...] = ()

__all__: tuple[str, ...] = REQUIRED_LEGACY_SYMBOLS + OPTIONAL_LEGACY_SYMBOLS


def export_namespace() -> dict[str, object]:
    return {name: getattr(_runner, name) for name in __all__}


globals().update(export_namespace())
