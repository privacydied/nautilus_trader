"""Legacy compatibility exports for the generic altcoin stress regime ablation Phase 0A study."""

from __future__ import annotations

from . import runner as _runner

REQUIRED_LEGACY_SYMBOLS: tuple[str, ...] = (
    "ACTIVE_ARCHIVE_PATH",
    "ALTCOIN_EXCLUDED_SYMBOLS",
    "COOLDOWN_HOURS",
    "HAS_ORJSON",
    "LoadDiagnostics",
    "MAX_MONTH_EVENT_SHARE",
    "MAX_QUARTER_EVENT_SHARE",
    "MAX_SYMBOL_EVENT_SHARE",
    "MIN_ACCEPTED_EVENTS",
    "MIN_ACCEPTED_SYMBOLS",
    "MIN_COVERAGE_MONTHS",
    "MIN_DISTINCT_MONTHS",
    "MIN_DISTINCT_QUARTERS",
    "MIN_SYMBOLS_WITH_3_EVENTS",
    "Phase0AResult",
    "STAGE",
    "STATUS_ARCHIVE_MISSING",
    "STATUS_CONCENTRATION_FAILED",
    "STATUS_COVERAGE_FAILED",
    "STATUS_INVALID_INPUT",
    "STATUS_INVALID_PRECOMMITMENT",
    "STATUS_READY",
    "STATUS_UNDERPOWERED",
    "STUDY_ID",
    "StressEventRecord",
    "StressWindowPoint",
    "SymbolAudit",
    "SymbolWorkerResult",
    "TRAILING_1H_RETURN_THRESHOLD_BPS",
    "TRAILING_6H_VOL_PERCENTILE_THRESHOLD",
    "VENUE",
    "accepted_event_json",
    "accepted_event_json_rows",
    "apply_cooldown",
    "compute_quarter_month_distributions",
    "compute_stress_points",
    "compute_stress_points_vectorized",
    "compute_trailing_1h_return_bps",
    "compute_trailing_6h_realized_vol_bps",
    "compute_vol_percentile",
    "determine_status",
    "discover_archive_paths",
    "discover_jsonl_files",
    "filter_stress_candidates",
    "git_metadata",
    "has_forward_24h_coverage",
    "is_altcoin_symbol",
    "run_from_archive_paths",
    "run_phase0a_audit",
    "summary_markdown",
    "utc_iso",
    "validate_precommitment",
    "validate_symbol_coverage",
    "write_report_artifacts",
)

OPTIONAL_LEGACY_SYMBOLS: tuple[str, ...] = (
    "SAFETY_MODE",
    "VOL_LOOKBACK_DAYS",
    "VOL_MIN_HISTORY_DAYS",
    "_DEFAULT_WORKERS",
    "_loads_json_line",
    "_merge_worker_results",
    "_process_one_file",
    "_read_jsonl_lines",
)

__all__: tuple[str, ...] = REQUIRED_LEGACY_SYMBOLS + OPTIONAL_LEGACY_SYMBOLS


def export_namespace() -> dict[str, object]:
    return {name: getattr(_runner, name) for name in __all__}


globals().update(export_namespace())
