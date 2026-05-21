"""
Phase 2B-2C3A: Native null p-value generation for comparison survivors.

Consumes Phase 2B-2C1 comparison artifact and Phase 2B-2B2 holdout evaluation
artifact. Generates exact/deterministic Monte Carlo sign-flip p-values from
event-level return vectors.

If per-event return vectors are unavailable in upstream artifacts, emits
NULL_EVENT_RETURNS_MISSING. Never invents p-values from summary metrics.

Produces offline_fdr_pvalues.json consumable by the existing FDR layer.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .run_artifacts import atomic_write_json
from .run_artifacts import safe_output_dir


NULL_SCHEMA_VERSION = "offline_native_null_v1"
FDR_PVALUE_SCHEMA_VERSION = "offline_fdr_pvalues_v1"

# --- Status values ---
STATUS_OFFLINE_NATIVE_NULL_READY = "OFFLINE_NATIVE_NULL_READY"
STATUS_NULL_EVENT_RETURNS_MISSING = "NULL_EVENT_RETURNS_MISSING"
STATUS_NO_NULL_ELIGIBLE_CELLS = "NO_NULL_ELIGIBLE_CELLS"
STATUS_INSUFFICIENT_NULL_EVENTS = "INSUFFICIENT_NULL_EVENTS"
STATUS_INVALID_NULL_CONFIG = "INVALID_NULL_CONFIG"
STATUS_INVALID_EVENT_RETURNS = "INVALID_EVENT_RETURNS"
STATUS_COMPARISON_HASH_MISMATCH = "COMPARISON_HASH_MISMATCH"
STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH = "HOLDOUT_EVALUATION_HASH_MISMATCH"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_UNUSABLE_NULL_INPUT = "UNUSABLE_NULL_INPUT"

# --- Exclusion reason constants ---
EXCL_COMPARISON_FAILED = "comparison_failed"
EXCL_NOT_HOLDOUT_SURVIVOR = "not_holdout_survivor"
EXCL_NOT_EDGE_FAMILY = "not_edge_family"
EXCL_FAMILY4_CONDITIONING = "family_4_conditioning_excluded"
EXCL_HOLDOUT_RESULT_MISSING = "holdout_result_missing"
EXCL_EVENT_RETURNS_MISSING = "event_returns_missing"
EXCL_INSUFFICIENT_EVENTS = "insufficient_events"
EXCL_UNSUPPORTED_METHOD = "unsupported_null_method"
EXCL_INVALID_EVENT_RETURNS = "invalid_event_returns"

# Edge family prefixes
_EDGE_FAMILY_PREFIXES = frozenset({"family_1", "family_2", "family_3"})
_CONDITIONING_INDICATORS = frozenset({"family_4", "conditioning"})

_ALLOWED_CONFIG_KEYS = frozenset({
    "method",
    "min_events",
    "exact_max_events",
    "monte_carlo_iterations",
    "random_seed",
    "alternative",
})

# Accepted event-return field names in holdout evaluation cell results
_EVENT_RETURN_FIELDS = frozenset({
    "event_net_bps",
    "event_net_return_bps",
    "event_net_returns_bps",
    "net_return_events_bps",
})


# ============================================================
# Models
# ============================================================


@dataclass(frozen=True)
class OfflineNativeNullConfig:
    method: str = "sign_flip_mean_net_bps_greater_than_zero"
    min_events: int = 2
    exact_max_events: int = 20
    monte_carlo_iterations: int = 10000
    random_seed: int = 1337
    alternative: str = "greater"

    def __post_init__(self) -> None:
        if self.method not in ("sign_flip_mean_net_bps_greater_than_zero",):
            raise ValueError(f"Unknown null method: {self.method!r}")
        if self.min_events < 1:
            raise ValueError(f"min_events must be >= 1, got {self.min_events}")
        if self.exact_max_events < 1:
            raise ValueError(f"exact_max_events must be >= 1, got {self.exact_max_events}")
        if self.monte_carlo_iterations < 100:
            raise ValueError(
                f"monte_carlo_iterations must be >= 100, got {self.monte_carlo_iterations}"
            )
        if not isinstance(self.random_seed, int):
            raise ValueError(f"random_seed must be an integer, got {type(self.random_seed).__name__}")
        if self.alternative not in ("greater",):
            raise ValueError(f"Unknown alternative: {self.alternative!r}")


@dataclass(frozen=True)
class OfflineNativeNullCellResult:
    cell_id: str
    family_id: str
    family_name: str
    signal_variant: str | None
    method: str | None
    alternative: str | None
    event_count: int
    observed_statistic: float | None
    p_value: float | None
    exact: bool
    iterations: int | None
    random_seed: int | None
    status: str
    exclusion_reasons: list[str]
    evidence_hash: str | None
    data_corpus_hash: str
    window_index_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_hash: str
    holdout_evaluation_hash: str
    comparison_hash: str


@dataclass(frozen=True)
class OfflineNativeNullReport:
    status: str
    method: str | None
    alternative: str | None
    eligible_cell_ids: list[str]
    tested_cell_ids: list[str]
    excluded_cell_ids: list[str]
    exclusion_reasons_by_cell: dict[str, list[str]]
    cell_results: list[OfflineNativeNullCellResult]
    pvalue_input: dict[str, Any] | None
    native_null_config_hash: str
    native_null_hash: str
    metadata: dict[str, Any]
    run_id: str
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_hash: str
    holdout_evaluation_hash: str
    comparison_hash: str


# ============================================================
# Hash helpers
# ============================================================


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ============================================================
# Config hash
# ============================================================


def _config_payload(config: OfflineNativeNullConfig) -> dict[str, Any]:
    return {
        "schema_version": NULL_SCHEMA_VERSION,
        "method": config.method,
        "min_events": config.min_events,
        "exact_max_events": config.exact_max_events,
        "monte_carlo_iterations": config.monte_carlo_iterations,
        "random_seed": config.random_seed,
        "alternative": config.alternative,
    }


def compute_native_null_config_hash(config: OfflineNativeNullConfig) -> str:
    return _sha256_json(_config_payload(config))


# ============================================================
# Config validation
# ============================================================


def _validate_config_keys(config_dict: dict[str, Any]) -> None:
    unknown = set(config_dict.keys()) - _ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(f"Unknown null config keys: {sorted(unknown)}")


def _parse_config(raw: dict[str, Any] | None) -> OfflineNativeNullConfig:
    if raw is None:
        return OfflineNativeNullConfig()
    _validate_config_keys(raw)
    return OfflineNativeNullConfig(
        method=str(raw.get("method", "sign_flip_mean_net_bps_greater_than_zero")),
        min_events=int(raw.get("min_events", 2)),
        exact_max_events=int(raw.get("exact_max_events", 20)),
        monte_carlo_iterations=int(raw.get("monte_carlo_iterations", 10000)),
        random_seed=int(raw.get("random_seed", 1337)),
        alternative=str(raw.get("alternative", "greater")),
    )


# ============================================================
# Family helpers
# ============================================================


def _is_edge_family(family_id: str) -> bool:
    return any(family_id.startswith(prefix) for prefix in _EDGE_FAMILY_PREFIXES)


def _is_conditioning_family(family_id: str) -> bool:
    lower = family_id.lower()
    return any(indicator in lower for indicator in _CONDITIONING_INDICATORS)


# ============================================================
# Sign-flip null method
# ============================================================


def _sign_flip_mean_greater(
    event_returns: list[float],
    config: OfflineNativeNullConfig,
) -> tuple[float, bool, int | None]:
    """
    Compute one-sided greater p-value via sign-flip null.

    Args:
        event_returns: Vector of per-event net return bps.
        config: Native null config with method parameters.

    Returns:
        (p_value, exact, iterations):
            p_value: clamped to [0, 1].
            exact: True if exact enumeration was used.
            iterations: number of Monte Carlo iterations, or None for exact.
    """
    n = len(event_returns)
    observed_mean = sum(event_returns) / n

    if n <= config.exact_max_events:
        # Exact enumeration of all 2^n sign flips
        total = 0
        count_extreme = 0
        signs = [1, -1]  # +1 = original sign, -1 = flipped
        for pattern in itertools.product(signs, repeat=n):
            flipped = [event_returns[i] * pattern[i] for i in range(n)]
            null_mean = sum(flipped) / n
            if null_mean >= observed_mean:
                count_extreme += 1
            total += 1
        p_value = count_extreme / total
        iterations = None
    else:
        # Monte Carlo sign flips
        rng = random.Random(config.random_seed)
        count_extreme = 0
        for _ in range(config.monte_carlo_iterations):
            flipped = [r * (1 if rng.random() < 0.5 else -1) for r in event_returns]
            null_mean = sum(flipped) / n
            if null_mean >= observed_mean:
                count_extreme += 1
        # Conservative formula: (count_extreme + 1) / (iterations + 1)
        p_value = (count_extreme + 1) / (config.monte_carlo_iterations + 1)
        iterations = config.monte_carlo_iterations

    # Clamp to [0, 1]
    p_value = max(0.0, min(1.0, p_value))
    exact = n <= config.exact_max_events

    return p_value, exact, iterations


# ============================================================
# Event return extraction
# ============================================================


def _extract_event_returns(
    holdout_cell: dict[str, Any],
) -> list[float] | str:
    """
    Extract event-level return vector from a holdout evaluation cell result.

    Returns:
        list[float] on success.
        str (error reason) if none found or invalid.
    """
    # Look for event return vector fields
    found_field: str | None = None
    for field in _EVENT_RETURN_FIELDS:
        if field in holdout_cell:
            found_field = field
            break

    if found_field is None:
        return EXCL_EVENT_RETURNS_MISSING

    raw = holdout_cell[found_field]
    if not isinstance(raw, list):
        return "event_returns_not_a_list"

    if len(raw) == 0:
        return EXCL_EVENT_RETURNS_MISSING

    returns: list[float] = []
    for i, val in enumerate(raw):
        if not isinstance(val, (int, float)):
            return f"non_numeric_event_return_at_index_{i}"
        fv = float(val)
        if math.isnan(fv) or math.isinf(fv):
            return f"invalid_event_return_at_index_{i}"
        returns.append(fv)

    return returns


# ============================================================
# Evidence hash
# ============================================================


def compute_event_evidence_hash(
    cell_id: str,
    method: str,
    alternative: str,
    event_returns: list[float],
    observed_statistic: float,
    config_hash: str,
    data_corpus_hash: str,
    window_index_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
    comparison_hash: str,
) -> str:
    return _sha256_json({
        "schema_version": NULL_SCHEMA_VERSION,
        "cell_id": cell_id,
        "method": method,
        "alternative": alternative,
        "event_returns": event_returns,
        "observed_statistic": observed_statistic,
        "config_hash": config_hash,
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_hash": survivor_freeze_hash,
        "holdout_evaluation_hash": holdout_evaluation_hash,
        "comparison_hash": comparison_hash,
    })


# ============================================================
# Identity checks
# ============================================================


def _check_lineage_hashes(
    comparison_manifest: dict[str, Any],
    comparison_payload: dict[str, Any],
    holdout_manifest: dict[str, Any],
    holdout_payload: dict[str, Any],
) -> str | None:
    """
    Verify lineage hashes across comparison and holdout artifacts.

    Returns None on success, or a status string on failure.
    """
    # comparison_hash match
    manifest_comp_hash = str(comparison_manifest.get("comparison_hash", ""))
    json_comp_hash = str(comparison_payload.get("comparison_hash", ""))
    if manifest_comp_hash and json_comp_hash and manifest_comp_hash != json_comp_hash:
        return STATUS_COMPARISON_HASH_MISMATCH

    # holdout_evaluation_hash match
    manifest_holdout_hash = str(holdout_manifest.get("holdout_evaluation_hash", ""))
    json_holdout_hash = str(holdout_payload.get("holdout_evaluation_hash", ""))
    if manifest_holdout_hash and json_holdout_hash:
        if manifest_holdout_hash != json_holdout_hash:
            return STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH

    # Cross-artifact lineage hashes
    hash_fields = [
        "data_corpus_hash",
        "window_index_hash",
        "discovery_config_hash",
        "plan_hash",
        "evaluation_hash",
        "survivor_freeze_hash",
        "holdout_evaluation_hash",
    ]

    for field in hash_fields:
        comp_val = str(comparison_payload.get(field, ""))
        holdout_val = str(holdout_payload.get(field, ""))
        if comp_val != holdout_val:
            return STATUS_INPUT_HASH_MISMATCH

    # Precommitment hash compatibility (all null ok)
    comp_precommit = comparison_manifest.get("precommitment_hash")
    holdout_precommit = holdout_manifest.get("precommitment_hash")
    precommits = {v for v in (comp_precommit, holdout_precommit) if v is not None}
    if len(precommits) > 1:
        return STATUS_INPUT_HASH_MISMATCH

    return None


# ============================================================
# Main builder
# ============================================================


def build_offline_native_null_report(
    *,
    comparison_manifest: dict[str, Any],
    comparison_payload: dict[str, Any],
    holdout_evaluation_manifest: dict[str, Any],
    holdout_evaluation_payload: dict[str, Any],
    null_config: OfflineNativeNullConfig | None = None,
) -> OfflineNativeNullReport:
    """
    Build the native null p-value generation report.

    Consumes comparison artifact and holdout evaluation artifact.
    If event-level return vectors are unavailable, emits NULL_EVENT_RETURNS_MISSING.
    """
    config = null_config or OfflineNativeNullConfig()
    config_hash = compute_native_null_config_hash(config)

    # Extract lineage hashes
    data_corpus_hash = str(comparison_payload.get("data_corpus_hash", ""))
    window_index_hash = str(comparison_payload.get("window_index_hash", ""))
    discovery_config_hash = str(comparison_payload.get("discovery_config_hash", ""))
    plan_hash = str(comparison_payload.get("plan_hash", ""))
    evaluation_hash = str(comparison_payload.get("evaluation_hash", ""))
    survivor_freeze_hash = str(comparison_payload.get("survivor_freeze_hash", ""))
    holdout_evaluation_hash = str(comparison_payload.get("holdout_evaluation_hash", ""))
    comparison_hash = str(comparison_payload.get("comparison_hash", ""))

    metadata: dict[str, Any] = {
        "null_config": _config_payload(config),
        "comparison_status": str(comparison_payload.get("status", "")),
        "holdout_evaluation_status": str(holdout_evaluation_payload.get("status", "")),
    }

    # ---- Identity checks ----
    identity_status = _check_lineage_hashes(
        comparison_manifest, comparison_payload,
        holdout_evaluation_manifest, holdout_evaluation_payload,
    )
    if identity_status is not None:
        return _empty_null_result(
            status=identity_status,
            config_hash=config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_hash=comparison_hash,
        )

    # ---- Extract artifacts ----
    comparison_cells = list(comparison_payload.get("comparison_cells", []))
    holdout_surviving_ids = {
        str(cid) for cid in comparison_payload.get("holdout_surviving_cell_ids", [])
    }
    holdout_cell_results = list(holdout_evaluation_payload.get("cell_results", []))

    # Build holdout result lookup
    holdout_result_map: dict[str, dict[str, Any]] = {}
    for cell in holdout_cell_results:
        cid = str(cell.get("cell_id", ""))
        holdout_result_map[cid] = cell

    if not comparison_cells:
        return _empty_null_result(
            status=STATUS_NO_NULL_ELIGIBLE_CELLS,
            config_hash=config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_hash=comparison_hash,
        )

    # ---- Determine eligibility ----
    cell_results: list[OfflineNativeNullCellResult] = []
    exclusion_reasons_by_cell: dict[str, list[str]] = {}
    eligible_cell_ids: list[str] = []
    tested_cell_ids: list[str] = []

    # Check if any eligible cells have event returns
    any_event_returns = False

    for cell in comparison_cells:
        cell_id = str(cell.get("cell_id", ""))
        family_id = str(cell.get("family_id", ""))
        family_name = str(cell.get("family_name", ""))
        signal_variant = cell.get("signal_variant")
        comparison_passed = bool(cell.get("comparison_passed", False))

        reasons: list[str] = []

        # Gate 1: comparison passed
        if not comparison_passed:
            reasons.append(EXCL_COMPARISON_FAILED)

        # Gate 2: holdout survivor
        if cell_id not in holdout_surviving_ids:
            reasons.append(EXCL_NOT_HOLDOUT_SURVIVOR)

        # Gate 3: edge family
        if not _is_edge_family(family_id):
            reasons.append(EXCL_NOT_EDGE_FAMILY)

        # Gate 4: Family 4 exclusion
        if _is_conditioning_family(family_id):
            reasons.append(EXCL_FAMILY4_CONDITIONING)

        # Gate 5: holdout result exists
        holdout_cell = holdout_result_map.get(cell_id)
        if holdout_cell is None:
            reasons.append(EXCL_HOLDOUT_RESULT_MISSING)

        # Gate 6: event-level returns
        event_returns: list[float] | str | None = None
        if holdout_cell is not None:
            er = _extract_event_returns(holdout_cell)
            if isinstance(er, str):
                if er == EXCL_EVENT_RETURNS_MISSING:
                    reasons.append(EXCL_EVENT_RETURNS_MISSING)
                else:
                    reasons.append(f"invalid_event_return:{er}")
            else:
                event_returns = er
                any_event_returns = True

        # Gate 7: min events
        if event_returns is not None and len(event_returns) < config.min_events:
            reasons.append(EXCL_INSUFFICIENT_EVENTS)
            event_returns = None  # cannot test

        # Build cell result
        if reasons:
            exclusion_reasons_by_cell[cell_id] = reasons
            cell_results.append(_make_null_cell_result(
                cell_id=cell_id,
                family_id=family_id,
                family_name=family_name,
                signal_variant=signal_variant,
                event_count=len(event_returns) if event_returns else 0,
                observed_statistic=None,
                p_value=None,
                exact=False,
                iterations=None,
                random_seed=None,
                status="excluded",
                exclusion_reasons=reasons,
                evidence_hash=None,
                data_corpus_hash=data_corpus_hash,
                window_index_hash=window_index_hash,
                plan_hash=plan_hash,
                evaluation_hash=evaluation_hash,
                survivor_freeze_hash=survivor_freeze_hash,
                holdout_evaluation_hash=holdout_evaluation_hash,
                comparison_hash=comparison_hash,
                config=config,
            ))
        else:
            eligible_cell_ids.append(cell_id)
            # Test this cell
            observed_statistic = sum(event_returns) / len(event_returns) if event_returns else None
            p_value, exact, iterations = _sign_flip_mean_greater(
                event_returns, config
            )
            evidence_hash = compute_event_evidence_hash(
                cell_id=cell_id,
                method=config.method,
                alternative=config.alternative,
                event_returns=event_returns,
                observed_statistic=observed_statistic,
                config_hash=config_hash,
                data_corpus_hash=data_corpus_hash,
                window_index_hash=window_index_hash,
                plan_hash=plan_hash,
                evaluation_hash=evaluation_hash,
                survivor_freeze_hash=survivor_freeze_hash,
                holdout_evaluation_hash=holdout_evaluation_hash,
                comparison_hash=comparison_hash,
            )
            tested_cell_ids.append(cell_id)
            cell_results.append(_make_null_cell_result(
                cell_id=cell_id,
                family_id=family_id,
                family_name=family_name,
                signal_variant=signal_variant,
                event_count=len(event_returns),
                observed_statistic=observed_statistic,
                p_value=p_value,
                exact=exact,
                iterations=iterations,
                random_seed=config.random_seed if not exact else None,
                status="tested",
                exclusion_reasons=[],
                evidence_hash=evidence_hash,
                data_corpus_hash=data_corpus_hash,
                window_index_hash=window_index_hash,
                plan_hash=plan_hash,
                evaluation_hash=evaluation_hash,
                survivor_freeze_hash=survivor_freeze_hash,
                holdout_evaluation_hash=holdout_evaluation_hash,
                comparison_hash=comparison_hash,
                config=config,
            ))

    # Sort cell results by cell_id for determinism
    cell_results.sort(key=lambda r: r.cell_id)

    # ---- Determine status ----
    if not any_event_returns and not eligible_cell_ids:
        status = STATUS_NULL_EVENT_RETURNS_MISSING
    elif not eligible_cell_ids:
        status = STATUS_NO_NULL_ELIGIBLE_CELLS
    elif any_event_returns and not tested_cell_ids:
        status = STATUS_INSUFFICIENT_NULL_EVENTS
    else:
        status = STATUS_OFFLINE_NATIVE_NULL_READY

    # Build pvalue input for FDR layer
    pvalue_input: dict[str, Any] | None = None
    if tested_cell_ids:
        pvalue_input = {
            "schema_version": FDR_PVALUE_SCHEMA_VERSION,
            "source": "offline_native_null_v1",
            "pvalues": [
                {
                    "cell_id": cid,
                    "p_value": _find_cell_pvalue(cid, cell_results),
                    "test_name": config.method,
                    "evidence_hash": _find_cell_evidence_hash(cid, cell_results),
                    "metadata": {},
                }
                for cid in sorted(tested_cell_ids)
            ],
        }

    excluded_ids = sorted(exclusion_reasons_by_cell.keys())

    run_id = f"offline_native_null_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    report = OfflineNativeNullReport(
        status=status,
        method=config.method,
        alternative=config.alternative,
        eligible_cell_ids=sorted(eligible_cell_ids),
        tested_cell_ids=sorted(tested_cell_ids),
        excluded_cell_ids=excluded_ids,
        exclusion_reasons_by_cell=exclusion_reasons_by_cell,
        cell_results=cell_results,
        pvalue_input=pvalue_input,
        native_null_config_hash=config_hash,
        native_null_hash="",
        metadata=metadata,
        run_id=run_id,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_hash=comparison_hash,
    )
    object.__setattr__(report, "native_null_hash", compute_native_null_hash(report))
    return report


# ============================================================
# Helpers
# ============================================================


def _make_null_cell_result(
    *,
    cell_id: str,
    family_id: str,
    family_name: str,
    signal_variant: str | None,
    event_count: int,
    observed_statistic: float | None,
    p_value: float | None,
    exact: bool,
    iterations: int | None,
    random_seed: int | None,
    status: str,
    exclusion_reasons: list[str],
    evidence_hash: str | None,
    data_corpus_hash: str,
    window_index_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
    comparison_hash: str,
    config: OfflineNativeNullConfig,
) -> OfflineNativeNullCellResult:
    return OfflineNativeNullCellResult(
        cell_id=cell_id,
        family_id=family_id,
        family_name=family_name,
        signal_variant=signal_variant,
        method=config.method,
        alternative=config.alternative,
        event_count=event_count,
        observed_statistic=observed_statistic,
        p_value=p_value,
        exact=exact,
        iterations=iterations,
        random_seed=random_seed,
        status=status,
        exclusion_reasons=exclusion_reasons,
        evidence_hash=evidence_hash,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_hash=comparison_hash,
    )


def _find_cell_pvalue(cell_id: str, results: list[OfflineNativeNullCellResult]) -> float | None:
    for r in results:
        if r.cell_id == cell_id:
            return r.p_value
    return None


def _find_cell_evidence_hash(cell_id: str, results: list[OfflineNativeNullCellResult]) -> str | None:
    for r in results:
        if r.cell_id == cell_id:
            return r.evidence_hash
    return None


def _empty_null_result(
    *,
    status: str,
    config_hash: str,
    metadata: dict[str, Any],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
    comparison_hash: str,
) -> OfflineNativeNullReport:
    config = OfflineNativeNullConfig()
    run_id = f"offline_native_null_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    report = OfflineNativeNullReport(
        status=status,
        method=config.method,
        alternative=config.alternative,
        eligible_cell_ids=[],
        tested_cell_ids=[],
        excluded_cell_ids=[],
        exclusion_reasons_by_cell={},
        cell_results=[],
        pvalue_input=None,
        native_null_config_hash=config_hash,
        native_null_hash="",
        metadata=metadata,
        run_id=run_id,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_hash=comparison_hash,
    )
    object.__setattr__(report, "native_null_hash", compute_native_null_hash(report))
    return report


# ============================================================
# Native null hash
# ============================================================


def compute_native_null_hash(report: OfflineNativeNullReport) -> str:
    """Deterministic SHA-256 over canonical sorted JSON of null essentials."""
    summaries = [
        {
            "cell_id": cell.cell_id,
            "status": cell.status,
            "event_count": cell.event_count,
            "observed_statistic": cell.observed_statistic,
            "p_value": cell.p_value,
            "exact": cell.exact,
            "exclusion_reasons": cell.exclusion_reasons,
        }
        for cell in report.cell_results
    ]

    return _sha256_json({
        "schema_version": NULL_SCHEMA_VERSION,
        "data_corpus_hash": report.data_corpus_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_hash": report.comparison_hash,
        "native_null_config_hash": report.native_null_config_hash,
        "eligible_cell_ids": report.eligible_cell_ids,
        "tested_cell_ids": report.tested_cell_ids,
        "excluded_cell_ids": report.excluded_cell_ids,
        "exclusion_reasons": report.exclusion_reasons_by_cell,
        "cell_result_summaries": summaries,
        "pvalue_input": report.pvalue_input,
        "status": report.status,
    })


# ============================================================
# Output writers
# ============================================================


def _get_fdr_pvalue_input_hash(pvalue_input: dict[str, Any] | None) -> str | None:
    if pvalue_input is None:
        return None
    return _sha256_json(pvalue_input)


def build_offline_native_null_manifest_payload(
    report: OfflineNativeNullReport,
    *,
    comparison_manifest_path: str,
    holdout_evaluation_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "phase": "offline_native_null",
        "generated_at_utc": _now_utc_iso(),
        "git_sha": _get_git_sha(),
        "schema_version": NULL_SCHEMA_VERSION,
        "comparison_manifest_path": comparison_manifest_path,
        "holdout_evaluation_manifest_path": holdout_evaluation_manifest_path,
        "data_corpus_hash": report.data_corpus_hash,
        "precommitment_hash": None,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_hash": report.comparison_hash,
        "native_null_config_hash": report.native_null_config_hash,
        "native_null_hash": report.native_null_hash,
        "fdr_pvalue_input_hash": _get_fdr_pvalue_input_hash(report.pvalue_input),
        "status": report.status,
        "eligible_cell_count": len(report.eligible_cell_ids),
        "tested_cell_count": len(report.tested_cell_ids),
        "excluded_cell_count": len(report.excluded_cell_ids),
        "safety": "public_data_observer_only",
    }


def write_offline_native_null_outputs(
    report: OfflineNativeNullReport,
    out_dir: Path,
    *,
    comparison_manifest_path: str,
    holdout_evaluation_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Write native null JSON report and manifest to output directory."""
    run_dir = safe_output_dir(Path(out_dir), allow_existing=overwrite)

    result_payload: dict[str, Any] = {
        "schema_version": NULL_SCHEMA_VERSION,
        "status": report.status,
        "method": report.method,
        "alternative": report.alternative,
        "eligible_cell_ids": report.eligible_cell_ids,
        "tested_cell_ids": report.tested_cell_ids,
        "excluded_cell_ids": report.excluded_cell_ids,
        "exclusion_reasons_by_cell": report.exclusion_reasons_by_cell,
        "cell_results": [asdict(cell) for cell in report.cell_results],
        "pvalue_input": report.pvalue_input,
        "native_null_config_hash": report.native_null_config_hash,
        "native_null_hash": report.native_null_hash,
        "metadata": report.metadata,
        "data_corpus_hash": report.data_corpus_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_hash": report.comparison_hash,
    }
    result_path = run_dir / "offline_native_null.json"
    atomic_write_json(result_path, result_payload)

    manifest_path = run_dir / "offline_native_null_manifest.json"
    atomic_write_json(
        manifest_path,
        build_offline_native_null_manifest_payload(
            report,
            comparison_manifest_path=comparison_manifest_path,
            holdout_evaluation_manifest_path=holdout_evaluation_manifest_path,
        ),
    )

    # Write FDR p-values if generated
    fdr_pvalue_path: Path | None = None
    if report.pvalue_input is not None and report.pvalue_input.get("pvalues"):
        fdr_pvalue_path = run_dir / "offline_fdr_pvalues.json"
        atomic_write_json(fdr_pvalue_path, report.pvalue_input)

    return {
        "result_path": result_path,
        "manifest_path": manifest_path,
        "fdr_pvalue_path": fdr_pvalue_path,
    }
