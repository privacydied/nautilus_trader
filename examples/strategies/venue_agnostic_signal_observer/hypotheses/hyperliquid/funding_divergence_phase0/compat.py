"""Legacy compatibility exports for Hyperliquid funding divergence Phase 0."""

from __future__ import annotations

from . import runner as _runner

REQUIRED_LEGACY_SYMBOLS: tuple[str, ...] = (
    "ALIGNMENT_COLUMNS",
    "ALLOWED_STATUSES",
    "ASSETS",
    "AlignedDivergenceRow",
    "BUCKETS_BPS_HOURLY",
    "BUCKET_COUNT_COLUMNS",
    "CALENDAR_COLUMNS",
    "DIVERGENCE_DISTRIBUTION_COLUMNS",
    "EXPECTED_INTERVAL_HOURS",
    "INSUFFICIENT_SAMPLES",
    "INSUFFICIENT_SAMPLES_FOR_PERCENTILE",
    "KILL_CRITERION_MAX_ABS_BPS",
    "KILL_CRITERION_P99_ABS_BPS",
    "MIN_ALIGNED_ROWS_PER_ASSET",
    "NormalizedFundingRow",
    "PERSISTENCE_COLUMNS",
    "SAFETY_MODE",
    "STATUS_ALIGNMENT_FAILED",
    "STATUS_DATA_UNUSABLE",
    "STATUS_KILLED",
    "STATUS_NEEDS_MORE_DATA",
    "STATUS_READY",
    "STATUS_SINGLE_ASSET",
    "STATUS_SOURCE_UNAVAILABLE",
    "align_last_observed_reference",
    "align_median_reference",
    "atomic_json",
    "compute_bucket_counts",
    "compute_calendar_stratification",
    "compute_persistence_half_life",
    "decide_phase0_status",
    "distribution_summary",
    "load_funding_csv",
    "load_funding_file",
    "load_funding_jsonl",
    "normalize_funding_row",
    "percentile",
    "pre_data_kill_criteria",
    "write_csv",
)

OPTIONAL_LEGACY_SYMBOLS: tuple[str, ...] = ()

__all__: tuple[str, ...] = REQUIRED_LEGACY_SYMBOLS + OPTIONAL_LEGACY_SYMBOLS


def export_namespace() -> dict[str, object]:
    return {name: getattr(_runner, name) for name in __all__}


globals().update(export_namespace())
