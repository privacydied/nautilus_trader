"""Legacy compatibility exports for the HIP-3 FLX stale-oracle funding-bias Phase -2 v0 study."""

from __future__ import annotations

from . import runner as _runner

REQUIRED_LEGACY_SYMBOLS: tuple[str, ...] = (
    "ALLOWED_STATUSES",
    "BiasDecision",
    "DEFAULT_BOOTSTRAP_ITERATIONS",
    "DEFAULT_DOWNLOAD_BUDGET_BYTES",
    "DEFAULT_EXTEND_BACKWARD_DAYS",
    "DEFAULT_FUNDING_INTERVAL_SECONDS",
    "DEFAULT_LAG_CORRELATION_THRESHOLD",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MIN_ALIGNED_OBSERVATIONS",
    "DEFAULT_MIN_FUNDING_CLOCK_PERSISTENCE_SHARE",
    "DEFAULT_MIN_REFERENCE_UPDATES",
    "DEFAULT_MIN_TARGET_UPDATES",
    "DEFAULT_REFERENCE_DEXES",
    "DEFAULT_RESIDUAL_EPSILON_BPS",
    "DEFAULT_SEED",
    "DEFAULT_SYMBOLS",
    "DEFAULT_TARGET_DEX",
    "FORBIDDEN_STATUSES",
    "FREQUENCY_SCAN_REFERENCE_DEXES",
    "FREQUENCY_SCAN_TARGET_DEX",
    "FREQUENCY_SCAN_TARGET_SYMBOLS",
    "OracleAlignmentRow",
    "OracleUpdate",
    "REPLICA_CMDS_BUCKET",
    "REPLICA_CMDS_PREFIX",
    "SAFETY_MODE",
    "compute_alignment_rows",
    "compute_bias_decision",
    "normalize_dex_symbol",
)

OPTIONAL_LEGACY_SYMBOLS: tuple[str, ...] = (
    "_bootstrap_ci",
    "_compute_frequency_scan_status",
    "_decode_lz4_json_records",
    "_dumps_json",
    "_dumps_json_pretty",
    "_extract_oracle_payloads_from_record",
    "_hash_bytes",
    "_loads_json",
    "_pearson_correlation",
    "_percentile",
    "_safe_float",
    "_safe_int",
)

__all__: tuple[str, ...] = REQUIRED_LEGACY_SYMBOLS + OPTIONAL_LEGACY_SYMBOLS


def export_namespace() -> dict[str, object]:
    return {name: getattr(_runner, name) for name in __all__}


globals().update(export_namespace())
