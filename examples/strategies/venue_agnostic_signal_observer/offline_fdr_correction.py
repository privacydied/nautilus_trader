"""Phase 2B-2C2: Shared edge FDR over comparison survivors.

Consumes the Phase 2B-2C1 train-holdout comparison artifact and applies a
deterministic false-discovery-rate correction across eligible edge-family
comparison survivors.

Supports two modes:
  1. pvalue_input mode — accepts explicit per-cell p-values, applies BH/BY.
  2. fdr_not_applicable mode — no p-values supplied, emits diagnostic report.

No null generation, no MCPT, no permutation tests, no cost sensitivity,
no candidate falsification, no Family 4 conditioning, no latency diagnostics,
no final offline verdicts, no live/candidate/trading statuses.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .run_artifacts import atomic_write_json, safe_output_dir

FDR_SCHEMA_VERSION = "offline_fdr_correction_v1"
PVALUE_INPUT_SCHEMA_VERSION = "offline_fdr_pvalues_v1"

# --- Status values ---
STATUS_OFFLINE_FDR_CORRECTION_READY = "OFFLINE_FDR_CORRECTION_READY"
STATUS_FDR_INPUT_PVALUES_MISSING = "FDR_INPUT_PVALUES_MISSING"
STATUS_NO_FDR_ELIGIBLE_CELLS = "NO_FDR_ELIGIBLE_CELLS"
STATUS_COMPARISON_HASH_MISMATCH = "COMPARISON_HASH_MISMATCH"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_INVALID_FDR_CONFIG = "INVALID_FDR_CONFIG"
STATUS_INVALID_PVALUE_INPUT = "INVALID_PVALUE_INPUT"
STATUS_UNUSABLE_FDR_INPUT = "UNUSABLE_FDR_INPUT"

# --- Exclusion reason constants ---
EXCL_COMPARISON_FAILED = "comparison_failed"
EXCL_FAMILY_NOT_ELIGIBLE = "family_not_eligible"
EXCL_NOT_EDGE_FAMILY = "not_edge_family"
EXCL_FAMILY4_CONDITIONING = "family_4_conditioning_excluded"
EXCL_MISSING_PVALUE = "missing_pvalue"
EXCL_INVALID_PVALUE = "invalid_pvalue"
EXCL_DUPLICATE_PVALUE = "duplicate_pvalue"
EXCL_UNKNOWN_PVALUE_CELL = "unknown_pvalue_cell"

# --- Forbidden statuses (must not appear in output) ---
_FORBIDDEN_STATUSES = frozenset({
    "CANDIDATE",
    "CANDIDATE_FOR_LIVE",
    "TRADE_READY",
    "EXECUTION_READY",
    "EDGE_FOUND",
    "BOT_ALLOWED",
    "REJECTED",
    "NO_EDGE_AFTER_COSTS",
})

# Edge family prefixes for the `require_edge_family` gate
_EDGE_FAMILY_PREFIXES = frozenset({"family_1", "family_2", "family_3"})

# Conditioning/ Family 4 indicators for the `exclude_conditioning_families` gate
_CONDITIONING_INDICATORS = frozenset({"family_4", "conditioning"})

_ALLOWED_CONFIG_KEYS = frozenset({
    "method",
    "alpha",
    "eligible_families",
    "exclude_conditioning_families",
    "require_comparison_passed",
    "require_edge_family",
})

# --- Config ---


@dataclass(frozen=True)
class OfflineFdrConfig:
    """Strict FDR config with defaults.

    Default method is BY (Benjamini-Yekutieli) because tests/cells are likely
    correlated.
    """

    method: str = "by"
    alpha: float = 0.05
    eligible_families: tuple[str, ...] = ("family_1", "family_2", "family_3")
    exclude_conditioning_families: bool = True
    require_comparison_passed: bool = True
    require_edge_family: bool = True

    def __post_init__(self) -> None:
        """Validate config at construction."""
        if self.method not in ("bh", "by"):
            raise ValueError(f"Unknown FDR method: {self.method!r}. Must be 'bh' or 'by'.")
        if not 0 < self.alpha < 1:
            raise ValueError(f"alpha must be in (0, 1), got {self.alpha}")
        if not self.eligible_families:
            raise ValueError("eligible_families must be non-empty")


# --- P-value input models ---


@dataclass(frozen=True)
class OfflineFdrPValue:
    """Single p-value entry from external input."""

    cell_id: str
    p_value: float
    test_name: str = ""
    evidence_hash: str | None = None
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.p_value, (int, float)):
            raise ValueError(f"p_value must be numeric, got {type(self.p_value).__name__}")
        if not 0.0 <= self.p_value <= 1.0:
            raise ValueError(f"p_value must be in [0, 1], got {self.p_value}")
        if not self.cell_id:
            raise ValueError("cell_id must be non-empty")
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


def parse_pvalue_input(raw: dict[str, Any]) -> dict[str, OfflineFdrPValue]:
    """Parse and validate a p-value input dict.

    Returns a dict mapping cell_id -> OfflineFdrPValue.

    Raises ValueError on structural issues.
    """
    schema_ver = raw.get("schema_version", "")
    if schema_ver != PVALUE_INPUT_SCHEMA_VERSION:
        raise ValueError(
            f"Unknown p-value input schema version: {schema_ver!r}. "
            f"Expected {PVALUE_INPUT_SCHEMA_VERSION!r}."
        )

    raw_pvalues = list(raw.get("pvalues", []))
    if not raw_pvalues:
        raise ValueError("pvalue input contains no pvalues array")

    parsed: dict[str, OfflineFdrPValue] = {}
    for i, entry in enumerate(raw_pvalues):
        if not isinstance(entry, dict):
            raise ValueError(f"pvalues[{i}] is not a dict")
        cell_id = entry.get("cell_id", "")
        if not cell_id:
            raise ValueError(f"pvalues[{i}] missing cell_id")

        if cell_id in parsed:
            raise ValueError(f"Duplicate p-value for cell {cell_id!r} at index {i}")

        p_val = entry.get("p_value")
        if p_val is None:
            raise ValueError(f"pvalues[{i}] missing p_value for cell {cell_id!r}")

        parsed[cell_id] = OfflineFdrPValue(
            cell_id=cell_id,
            p_value=float(p_val),
            test_name=str(entry.get("test_name", "")),
            evidence_hash=entry.get("evidence_hash"),
            metadata=dict(entry.get("metadata", {})),
        )

    return parsed


# --- Output models ---


@dataclass(frozen=True)
class OfflineFdrCellResult:
    """One FDR result row per cell."""

    cell_id: str
    family_id: str
    family_name: str
    signal_variant: str | None
    lookback_ms: int
    horizon_ms: int
    comparison_passed: bool
    p_value: float | None
    q_value: float | None
    rank: int | None
    method: str | None
    alpha: float | None
    fdr_passed: bool | None
    exclusion_reasons: list[str]
    data_corpus_hash: str
    window_index_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_hash: str
    holdout_evaluation_hash: str
    comparison_hash: str


@dataclass(frozen=True)
class OfflineFdrCorrectionReport:
    """Complete FDR correction report."""

    status: str
    method: str | None
    alpha: float | None
    fdr_family_size: int
    eligible_cell_ids: list[str]
    fdr_passed_cell_ids: list[str]
    fdr_failed_cell_ids: list[str]
    excluded_cell_ids: list[str]
    exclusion_reasons_by_cell: dict[str, list[str]]
    fdr_config_hash: str
    pvalue_input_hash: str | None
    fdr_hash: str
    cell_results: list[OfflineFdrCellResult]
    metadata: dict[str, Any]
    run_id: str
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_hash: str
    holdout_evaluation_hash: str
    comparison_config_hash: str
    comparison_hash: str
    comparison_manifest_path: str


# --- Hash helpers ---


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


# --- Config hash ---


def _config_payload(config: OfflineFdrConfig) -> dict[str, Any]:
    return {
        "schema_version": FDR_SCHEMA_VERSION,
        "method": config.method,
        "alpha": config.alpha,
        "eligible_families": sorted(config.eligible_families),
        "exclude_conditioning_families": config.exclude_conditioning_families,
        "require_comparison_passed": config.require_comparison_passed,
        "require_edge_family": config.require_edge_family,
    }


def compute_fdr_config_hash(config: OfflineFdrConfig) -> str:
    return _sha256_json(_config_payload(config))


# --- P-value input hash ---


def compute_pvalue_input_hash(pvalues: dict[str, OfflineFdrPValue]) -> str:
    return _sha256_json({
        "schema_version": PVALUE_INPUT_SCHEMA_VERSION,
        "pvalues": sorted(
            (
                {
                    "cell_id": p.cell_id,
                    "p_value": p.p_value,
                    "test_name": p.test_name,
                    "evidence_hash": p.evidence_hash,
                    "metadata": p.metadata,
                }
                for p in pvalues.values()
            ),
            key=lambda x: x["cell_id"],
        ),
    })


# --- Validate config dict ---


def _validate_config_keys(config_dict: dict[str, Any]) -> None:
    unknown = set(config_dict.keys()) - _ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(f"Unknown FDR config keys: {sorted(unknown)}")


def _parse_config(raw: dict[str, Any] | None) -> OfflineFdrConfig:
    """Parse optional raw config dict. Return defaults if None."""
    if raw is None:
        return OfflineFdrConfig()

    _validate_config_keys(raw)
    method = str(raw.get("method", "by"))
    alpha = float(raw.get("alpha", 0.05))
    eligible = tuple(raw.get("eligible_families", ["family_1", "family_2", "family_3"]))
    exclude_cond = bool(raw.get("exclude_conditioning_families", True))
    require_comp = bool(raw.get("require_comparison_passed", True))
    require_edge = bool(raw.get("require_edge_family", True))

    return OfflineFdrConfig(
        method=method,
        alpha=alpha,
        eligible_families=eligible,
        exclude_conditioning_families=exclude_cond,
        require_comparison_passed=require_comp,
        require_edge_family=require_edge,
    )


# --- Family helpers ---


def _is_edge_family(family_id: str) -> bool:
    """Check if family_id starts with a known edge-family prefix."""
    for prefix in _EDGE_FAMILY_PREFIXES:
        if family_id.startswith(prefix):
            return True
    return False


def _is_conditioning_family(family_id: str) -> bool:
    """Check if family_id indicates a conditioning or Family 4 cell."""
    lower = family_id.lower()
    for indicator in _CONDITIONING_INDICATORS:
        if indicator in lower:
            return True
    return False


def _is_family_eligible(family_id: str, eligible_families: tuple[str, ...]) -> bool:
    """Check if family_id matches any eligible family prefix."""
    for eligible in eligible_families:
        if family_id.startswith(eligible):
            return True
    return False


# --- BH and BY correction ---


def _harmonic_sum(n: int) -> float:
    """Compute sum_{j=1..n} 1/j."""
    s = 0.0
    for j in range(1, n + 1):
        s += 1.0 / j
    return s


def _bh_correction(
    pvalues: list[tuple[str, float]],
    alpha: float,
) -> dict[str, dict[str, Any]]:
    """Apply Benjamini-Hochberg correction.

    Returns dict cell_id -> {rank, p_value, q_value, threshold, fdr_passed}.
    """
    m = len(pvalues)
    if m == 0:
        return {}

    # Sort by p-value ascending, then cell_id ascending for ties
    sorted_pv = sorted(pvalues, key=lambda x: (x[1], x[0]))

    results: dict[str, dict[str, Any]] = {}
    max_q = 0.0
    # Process in sorted order (smallest p-value first = rank 1)
    for i, (cell_id, p_val) in enumerate(sorted_pv):
        rank = i + 1  # 1-indexed
        threshold = (rank / m) * alpha
        raw_q = (p_val * m) / rank if rank > 0 else p_val
        # Monotonic: q-value must be >= previous
        q_val = max(raw_q, max_q)
        max_q = q_val
        q_val = max(0.0, min(1.0, q_val))  # clamp to [0, 1]

        fdr_passed = p_val <= threshold

        results[cell_id] = {
            "rank": rank,
            "p_value": p_val,
            "q_value": q_val,
            "threshold": threshold,
            "fdr_passed": fdr_passed,
        }

    # After processing, apply monotonicity adjustment backwards:
    # all larger p-values should have at least as large q-value
    # And fdr_passed should be stepwise: once one fails, all larger p-values fail
    sorted_ids = [r[0] for r in sorted_pv]
    # Find the first cell that fails (highest rank that still passes)
    last_passing_rank = 0
    for cell_id in sorted_ids:
        if results[cell_id]["fdr_passed"]:
            last_passing_rank = results[cell_id]["rank"]

    # All ranks > last_passing_rank should have fdr_passed=False
    # Monotonic adjustment: ensure q-values are nondecreasing
    prev_q = 0.0
    for cell_id in sorted_ids:
        cur_q = results[cell_id]["q_value"]
        if cur_q < prev_q:
            results[cell_id]["q_value"] = prev_q
        else:
            prev_q = results[cell_id]["q_value"]

        # Stepwise pass/fail: if rank > last_passing_rank, fail
        if results[cell_id]["rank"] > last_passing_rank:
            results[cell_id]["fdr_passed"] = False

    return results


def _by_correction(
    pvalues: list[tuple[str, float]],
    alpha: float,
) -> dict[str, dict[str, Any]]:
    """Apply Benjamini-Yekutieli correction.

    More conservative than BH when tests are correlated.
    """
    m = len(pvalues)
    if m == 0:
        return {}

    c_m = _harmonic_sum(m)  # c_m = sum(1/j for j in 1..m)

    # Sort by p-value ascending, then cell_id ascending for ties
    sorted_pv = sorted(pvalues, key=lambda x: (x[1], x[0]))

    results: dict[str, dict[str, Any]] = {}
    max_q = 0.0

    for i, (cell_id, p_val) in enumerate(sorted_pv):
        rank = i + 1  # 1-indexed
        threshold = (rank / (m * c_m)) * alpha
        raw_q = (p_val * m * c_m) / rank if rank > 0 else p_val
        # Monotonic: q-value must be >= previous
        q_val = max(raw_q, max_q)
        max_q = q_val
        q_val = max(0.0, min(1.0, q_val))  # clamp to [0, 1]

        fdr_passed = p_val <= threshold

        results[cell_id] = {
            "rank": rank,
            "p_value": p_val,
            "q_value": q_val,
            "threshold": threshold,
            "fdr_passed": fdr_passed,
        }

    # Monotonic adjustment and stepwise pass/fail
    sorted_ids = [r[0] for r in sorted_pv]
    last_passing_rank = 0
    for cell_id in sorted_ids:
        if results[cell_id]["fdr_passed"]:
            last_passing_rank = results[cell_id]["rank"]

    prev_q = 0.0
    for cell_id in sorted_ids:
        cur_q = results[cell_id]["q_value"]
        if cur_q < prev_q:
            results[cell_id]["q_value"] = prev_q
        else:
            prev_q = results[cell_id]["q_value"]

        if results[cell_id]["rank"] > last_passing_rank:
            results[cell_id]["fdr_passed"] = False

    return results


def apply_fdr_correction(
    pvalues: list[tuple[str, float]],
    method: str,
    alpha: float,
) -> dict[str, dict[str, Any]]:
    """Apply FDR correction using specified method.

    Returns dict cell_id -> {rank, p_value, q_value, threshold, fdr_passed}.
    """
    if method == "bh":
        return _bh_correction(pvalues, alpha)
    elif method == "by":
        return _by_correction(pvalues, alpha)
    else:
        raise ValueError(f"Unknown FDR method: {method!r}")


# --- Identity checks ---


def _check_lineage_hashes(
    manifest: dict[str, Any],
    comparison_json: dict[str, Any],
) -> str | None:
    """Verify lineage hashes between manifest and comparison JSON.

    Returns None on success, or a status string on failure.
    """
    # comparison_hash match
    manifest_comparison_hash = str(manifest.get("comparison_hash", ""))
    json_comparison_hash = str(comparison_json.get("comparison_hash", ""))
    if manifest_comparison_hash and json_comparison_hash:
        if manifest_comparison_hash != json_comparison_hash:
            return STATUS_COMPARISON_HASH_MISMATCH

    # Lineage hashes
    hash_fields = [
        "data_corpus_hash",
        "window_index_hash",
        "discovery_config_hash",
        "plan_hash",
        "evaluation_hash",
        "survivor_freeze_hash",
        "holdout_evaluation_hash",
        "comparison_config_hash",
        "comparison_hash",
    ]

    for field in hash_fields:
        manifest_val = str(manifest.get(field, ""))
        json_val = str(comparison_json.get(field, ""))
        if manifest_val != json_val:
            return STATUS_INPUT_HASH_MISMATCH

    # Precommitment hash compatibility (all null is ok)
    precommit = manifest.get("precommitment_hash")
    # We only need to check the manifest's precommitment_hash since comparison
    # JSON may not have it. If it does, check compatibility.
    json_precommit = comparison_json.get("precommitment_hash")
    if precommit is not None and json_precommit is not None:
        if str(precommit) != str(json_precommit):
            return STATUS_INPUT_HASH_MISMATCH

    return None


# --- Family construction ---


def _build_cell_exclusion_reasons(
    cell: dict[str, Any],
    config: OfflineFdrConfig,
) -> list[str]:
    """Determine exclusion reasons for a comparison cell.

    Returns empty list if the cell is eligible for FDR.
    """
    reasons: list[str] = []
    family_id = str(cell.get("family_id", ""))
    comparison_passed = bool(cell.get("comparison_passed", False))

    # Gate 1: comparison passed
    if config.require_comparison_passed and not comparison_passed:
        reasons.append(EXCL_COMPARISON_FAILED)

    # Gate 2: eligible family
    if not _is_family_eligible(family_id, config.eligible_families):
        reasons.append(EXCL_FAMILY_NOT_ELIGIBLE)

    # Gate 3: edge family
    if config.require_edge_family and not _is_edge_family(family_id):
        reasons.append(EXCL_NOT_EDGE_FAMILY)

    # Gate 4: exclude conditioning / Family 4
    if config.exclude_conditioning_families and _is_conditioning_family(family_id):
        reasons.append(EXCL_FAMILY4_CONDITIONING)

    return reasons


# --- Main builder ---


def build_offline_fdr_correction_report(
    *,
    comparison_manifest: dict[str, Any],
    comparison_payload: dict[str, Any],
    fdr_config: OfflineFdrConfig | None = None,
    pvalue_input: dict[str, OfflineFdrPValue] | None = None,
    comparison_manifest_path: str = "",
) -> OfflineFdrCorrectionReport:
    """Build the FDR correction report.

    Consumes the Phase 2B-2C1 comparison artifact and applies deterministic
    FDR correction across eligible edge-family comparison survivors.

    Args:
        comparison_manifest: The comparison output manifest.
        comparison_payload: The comparison output JSON (full payload).
        fdr_config: Optional FDR config. Defaults to OfflineFdrConfig().
        pvalue_input: Optional parsed p-value input (cell_id -> OfflineFdrPValue).
        comparison_manifest_path: Path to the comparison manifest file.

    Returns:
        OfflineFdrCorrectionReport with status and cell results.
    """
    config = fdr_config or OfflineFdrConfig()
    fdr_config_hash = compute_fdr_config_hash(config)

    pv_in_use = pvalue_input is not None
    pv_input_hash: str | None = (
        compute_pvalue_input_hash(pvalue_input) if pv_in_use else None
    )

    # Extract lineage hashes
    data_corpus_hash = str(comparison_payload.get("data_corpus_hash", ""))
    window_index_hash = str(comparison_payload.get("window_index_hash", ""))
    discovery_config_hash = str(comparison_payload.get("discovery_config_hash", ""))
    plan_hash = str(comparison_payload.get("plan_hash", ""))
    evaluation_hash = str(comparison_payload.get("evaluation_hash", ""))
    survivor_freeze_hash = str(comparison_payload.get("survivor_freeze_hash", ""))
    holdout_evaluation_hash = str(comparison_payload.get("holdout_evaluation_hash", ""))
    comparison_config_hash = str(comparison_payload.get("comparison_config_hash", ""))
    comparison_hash = str(comparison_payload.get("comparison_hash", ""))

    metadata: dict[str, Any] = {
        "fdr_config": _config_payload(config),
        "pvalue_input_provided": pv_in_use,
        "comparison_status": str(comparison_payload.get("status", "")),
    }

    # ---- Identity checks ----
    identity_status = _check_lineage_hashes(comparison_manifest, comparison_payload)
    if identity_status is not None:
        return _empty_fdr_result(
            status=identity_status,
            fdr_config_hash=fdr_config_hash,
            pvalue_input_hash=pv_input_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_config_hash=comparison_config_hash,
            comparison_hash=comparison_hash,
            comparison_manifest_path=comparison_manifest_path,
        )

    # ---- Extract comparison cells ----
    comparison_cells = list(comparison_payload.get("comparison_cells", []))
    comparison_cell_ids: set[str] = {str(c.get("cell_id", "")) for c in comparison_cells}

    # ---- Validate p-value input against known comparison cells ----
    # Unknown p-value cell_ids must reject loudly — no silent ignoring.
    if pv_in_use and pvalue_input is not None:
        unknown_cells: list[str] = []
        for pv_cell_id in pvalue_input:
            if pv_cell_id not in comparison_cell_ids:
                unknown_cells.append(pv_cell_id)
        if unknown_cells:
            unknown_cells.sort()
            metadata["unknown_pvalue_cells"] = unknown_cells
            metadata["unknown_pvalue_reasons"] = [
                f"unknown_pvalue_cell:{cid}" for cid in unknown_cells
            ]
            return _empty_fdr_result(
                status=STATUS_INVALID_PVALUE_INPUT,
                fdr_config_hash=fdr_config_hash,
                pvalue_input_hash=pv_input_hash,
                metadata=metadata,
                data_corpus_hash=data_corpus_hash,
                window_index_hash=window_index_hash,
                discovery_config_hash=discovery_config_hash,
                plan_hash=plan_hash,
                evaluation_hash=evaluation_hash,
                survivor_freeze_hash=survivor_freeze_hash,
                holdout_evaluation_hash=holdout_evaluation_hash,
                comparison_config_hash=comparison_config_hash,
                comparison_hash=comparison_hash,
                comparison_manifest_path=comparison_manifest_path,
            )

    if not comparison_cells:
        return _empty_fdr_result(
            status=STATUS_NO_FDR_ELIGIBLE_CELLS,
            fdr_config_hash=fdr_config_hash,
            pvalue_input_hash=pv_input_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_config_hash=comparison_config_hash,
            comparison_hash=comparison_hash,
            comparison_manifest_path=comparison_manifest_path,
        )

    # ---- Build cell results with exclusions ----
    cell_results: list[OfflineFdrCellResult] = []
    exclusion_reasons_by_cell: dict[str, list[str]] = {}
    eligible_cell_ids: list[str] = []

    for cell in comparison_cells:
        cell_id = str(cell.get("cell_id", ""))
        family_id = str(cell.get("family_id", ""))
        comparison_passed = bool(cell.get("comparison_passed", False))

        # Determine exclusion reasons
        reasons = _build_cell_exclusion_reasons(cell, config)

        # Check p-value availability
        if pv_in_use:
            if cell_id in pvalue_input:
                # Already validated on parse - p_value is in [0,1]
                pass  # eligible if no other reasons
            else:
                reasons.append(EXCL_MISSING_PVALUE)

        if reasons:
            exclusion_reasons_by_cell[cell_id] = reasons
        else:
            eligible_cell_ids.append(cell_id)

    # ---- No p-value input -> FDR_INPUT_PVALUES_MISSING ----
    if not pv_in_use:
        # All cells get missing_pvalue exclusion
        enriched_cell_results = _build_all_cell_results(
            comparison_cells=comparison_cells,
            pvalue_input={},
            fdr_results={},
            fdr_config=config,
            exclusion_reasons_by_cell=exclusion_reasons_by_cell,
            eligible_cell_ids=[],
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_hash=comparison_hash,
        )

        # Mark all cells as missing pvalue
        for cell in comparison_cells:
            cell_id = str(cell.get("cell_id", ""))
            if cell_id not in exclusion_reasons_by_cell:
                exclusion_reasons_by_cell[cell_id] = [EXCL_MISSING_PVALUE]
            elif EXCL_MISSING_PVALUE not in exclusion_reasons_by_cell[cell_id]:
                exclusion_reasons_by_cell[cell_id].append(EXCL_MISSING_PVALUE)

        return _build_fdr_report(
            status=STATUS_FDR_INPUT_PVALUES_MISSING,
            fdr_config=config,
            eligible_cell_ids=[],
            fdr_passed_cell_ids=[],
            fdr_failed_cell_ids=[],
            excluded_cell_ids=sorted(exclusion_reasons_by_cell.keys()),
            exclusion_reasons_by_cell=exclusion_reasons_by_cell,
            fdr_config_hash=fdr_config_hash,
            pvalue_input_hash=None,
            cell_results=enriched_cell_results,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_config_hash=comparison_config_hash,
            comparison_hash=comparison_hash,
            comparison_manifest_path=comparison_manifest_path,
        )

    # ---- No eligible cells ----
    if not eligible_cell_ids:
        enriched_cell_results = _build_all_cell_results(
            comparison_cells=comparison_cells,
            pvalue_input=pvalue_input,
            fdr_results={},
            fdr_config=config,
            exclusion_reasons_by_cell=exclusion_reasons_by_cell,
            eligible_cell_ids=[],
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_hash=comparison_hash,
        )

        return _build_fdr_report(
            status=STATUS_NO_FDR_ELIGIBLE_CELLS,
            fdr_config=config,
            eligible_cell_ids=[],
            fdr_passed_cell_ids=[],
            fdr_failed_cell_ids=[],
            excluded_cell_ids=sorted(exclusion_reasons_by_cell.keys()),
            exclusion_reasons_by_cell=exclusion_reasons_by_cell,
            fdr_config_hash=fdr_config_hash,
            pvalue_input_hash=pv_input_hash,
            cell_results=enriched_cell_results,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_evaluation_hash,
            comparison_config_hash=comparison_config_hash,
            comparison_hash=comparison_hash,
            comparison_manifest_path=comparison_manifest_path,
        )

    # ---- Build p-value list for FDR ----
    fdr_input: list[tuple[str, float]] = []
    for cell_id in eligible_cell_ids:
        fdr_input.append((cell_id, pvalue_input[cell_id].p_value))

    # ---- Apply FDR correction ----
    fdr_results = apply_fdr_correction(fdr_input, config.method, config.alpha)

    # ---- Build enriched cell results ----
    enriched_cell_results = _build_all_cell_results(
        comparison_cells=comparison_cells,
        pvalue_input=pvalue_input,
        fdr_results=fdr_results,
        fdr_config=config,
        exclusion_reasons_by_cell=exclusion_reasons_by_cell,
        eligible_cell_ids=eligible_cell_ids,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_hash=comparison_hash,
    )

    # ---- Separate pass/fail ----
    fdr_passed_cell_ids = sorted(
        cid for cid in eligible_cell_ids if fdr_results.get(cid, {}).get("fdr_passed", False)
    )
    fdr_failed_cell_ids = sorted(
        cid for cid in eligible_cell_ids if not fdr_results.get(cid, {}).get("fdr_passed", False)
    )
    excluded_cell_ids = sorted(exclusion_reasons_by_cell.keys())

    return _build_fdr_report(
        status=STATUS_OFFLINE_FDR_CORRECTION_READY,
        fdr_config=config,
        eligible_cell_ids=sorted(eligible_cell_ids),
        fdr_passed_cell_ids=fdr_passed_cell_ids,
        fdr_failed_cell_ids=fdr_failed_cell_ids,
        excluded_cell_ids=excluded_cell_ids,
        exclusion_reasons_by_cell=exclusion_reasons_by_cell,
        fdr_config_hash=fdr_config_hash,
        pvalue_input_hash=pv_input_hash,
        cell_results=enriched_cell_results,
        metadata=metadata,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_config_hash=comparison_config_hash,
        comparison_hash=comparison_hash,
        comparison_manifest_path=comparison_manifest_path,
    )


# --- Helper: empty result ---


def _empty_fdr_result(
    *,
    status: str,
    fdr_config_hash: str,
    pvalue_input_hash: str | None,
    metadata: dict[str, Any],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
    comparison_config_hash: str,
    comparison_hash: str,
    comparison_manifest_path: str,
) -> OfflineFdrCorrectionReport:
    return _build_fdr_report(
        status=status,
        fdr_config=OfflineFdrConfig(),
        eligible_cell_ids=[],
        fdr_passed_cell_ids=[],
        fdr_failed_cell_ids=[],
        excluded_cell_ids=[],
        exclusion_reasons_by_cell={},
        fdr_config_hash=fdr_config_hash,
        pvalue_input_hash=pvalue_input_hash,
        cell_results=[],
        metadata=metadata,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_config_hash=comparison_config_hash,
        comparison_hash=comparison_hash,
        comparison_manifest_path=comparison_manifest_path,
    )


# --- Helper: build cell results ---


def _build_all_cell_results(
    *,
    comparison_cells: list[dict[str, Any]],
    pvalue_input: dict[str, OfflineFdrPValue],
    fdr_results: dict[str, dict[str, Any]],
    fdr_config: OfflineFdrConfig,
    exclusion_reasons_by_cell: dict[str, list[str]],
    eligible_cell_ids: list[str],
    data_corpus_hash: str,
    window_index_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
    comparison_hash: str,
) -> list[OfflineFdrCellResult]:
    results: list[OfflineFdrCellResult] = []

    for cell in comparison_cells:
        cell_id = str(cell.get("cell_id", ""))
        family_id = str(cell.get("family_id", ""))
        family_name = str(cell.get("family_name", ""))
        signal_variant = cell.get("signal_variant")
        lookback_ms = int(cell.get("lookback_ms", 0))
        horizon_ms = int(cell.get("horizon_ms", 0))
        comp_passed = bool(cell.get("comparison_passed", False))

        reasons = exclusion_reasons_by_cell.get(cell_id, [])

        is_eligible = cell_id in eligible_cell_ids

        if is_eligible and cell_id in fdr_results:
            fr = fdr_results[cell_id]
            p_val = pvalue_input[cell_id].p_value
            results.append(OfflineFdrCellResult(
                cell_id=cell_id,
                family_id=family_id,
                family_name=family_name,
                signal_variant=signal_variant,
                lookback_ms=lookback_ms,
                horizon_ms=horizon_ms,
                comparison_passed=comp_passed,
                p_value=p_val,
                q_value=fr["q_value"],
                rank=fr["rank"],
                method=fdr_config.method,
                alpha=fdr_config.alpha,
                fdr_passed=fr["fdr_passed"],
                exclusion_reasons=reasons,
                data_corpus_hash=data_corpus_hash,
                window_index_hash=window_index_hash,
                plan_hash=plan_hash,
                evaluation_hash=evaluation_hash,
                survivor_freeze_hash=survivor_freeze_hash,
                holdout_evaluation_hash=holdout_evaluation_hash,
                comparison_hash=comparison_hash,
            ))
        else:
            # Excluded or non-eligible cell — no FDR result
            p_val = None
            q_val = None
            rank = None
            fdr_passed = None
            if is_eligible and cell_id in pvalue_input:
                p_val = pvalue_input[cell_id].p_value
            elif not is_eligible and cell_id in pvalue_input:
                # P-value was supplied but cell was excluded — still record it
                p_val = pvalue_input[cell_id].p_value

            results.append(OfflineFdrCellResult(
                cell_id=cell_id,
                family_id=family_id,
                family_name=family_name,
                signal_variant=signal_variant,
                lookback_ms=lookback_ms,
                horizon_ms=horizon_ms,
                comparison_passed=comp_passed,
                p_value=p_val,
                q_value=q_val,
                rank=rank,
                method=None,
                alpha=None,
                fdr_passed=fdr_passed,
                exclusion_reasons=reasons,
                data_corpus_hash=data_corpus_hash,
                window_index_hash=window_index_hash,
                plan_hash=plan_hash,
                evaluation_hash=evaluation_hash,
                survivor_freeze_hash=survivor_freeze_hash,
                holdout_evaluation_hash=holdout_evaluation_hash,
                comparison_hash=comparison_hash,
            ))

    # Sort by cell_id for deterministic output
    results.sort(key=lambda r: r.cell_id)
    return results


# --- Helper: build report ---


def _build_fdr_report(
    *,
    status: str,
    fdr_config: OfflineFdrConfig,
    eligible_cell_ids: list[str],
    fdr_passed_cell_ids: list[str],
    fdr_failed_cell_ids: list[str],
    excluded_cell_ids: list[str],
    exclusion_reasons_by_cell: dict[str, list[str]],
    fdr_config_hash: str,
    pvalue_input_hash: str | None,
    cell_results: list[OfflineFdrCellResult],
    metadata: dict[str, Any],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
    comparison_config_hash: str,
    comparison_hash: str,
    comparison_manifest_path: str,
) -> OfflineFdrCorrectionReport:
    fdr_family_size = len(eligible_cell_ids)

    run_id = f"offline_fdr_correction_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    method = fdr_config.method if status not in (
        STATUS_FDR_INPUT_PVALUES_MISSING,
        STATUS_COMPARISON_HASH_MISMATCH,
        STATUS_INPUT_HASH_MISMATCH,
        STATUS_UNUSABLE_FDR_INPUT,
        STATUS_INVALID_FDR_CONFIG,
        STATUS_INVALID_PVALUE_INPUT,
    ) else None

    alpha = fdr_config.alpha if status not in (
        STATUS_FDR_INPUT_PVALUES_MISSING,
        STATUS_COMPARISON_HASH_MISMATCH,
        STATUS_INPUT_HASH_MISMATCH,
        STATUS_UNUSABLE_FDR_INPUT,
        STATUS_INVALID_FDR_CONFIG,
        STATUS_INVALID_PVALUE_INPUT,
    ) else None

    report = OfflineFdrCorrectionReport(
        status=status,
        method=method,
        alpha=alpha,
        fdr_family_size=fdr_family_size,
        eligible_cell_ids=sorted(eligible_cell_ids),
        fdr_passed_cell_ids=sorted(fdr_passed_cell_ids),
        fdr_failed_cell_ids=sorted(fdr_failed_cell_ids),
        excluded_cell_ids=sorted(excluded_cell_ids),
        exclusion_reasons_by_cell=exclusion_reasons_by_cell,
        fdr_config_hash=fdr_config_hash,
        pvalue_input_hash=pvalue_input_hash,
        fdr_hash="",
        cell_results=cell_results,
        metadata=metadata,
        run_id=run_id,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
        comparison_config_hash=comparison_config_hash,
        comparison_hash=comparison_hash,
        comparison_manifest_path=comparison_manifest_path,
    )

    object.__setattr__(report, "fdr_hash", compute_fdr_hash(report))
    return report


# --- FDR hash ---


def compute_fdr_hash(report: OfflineFdrCorrectionReport) -> str:
    """Deterministic SHA-256 over canonical sorted JSON of FDR essentials."""
    summaries = [
        {
            "cell_id": cell.cell_id,
            "p_value": cell.p_value,
            "q_value": cell.q_value,
            "rank": cell.rank,
            "method": cell.method,
            "alpha": cell.alpha,
            "fdr_passed": cell.fdr_passed,
            "exclusion_reasons": cell.exclusion_reasons,
        }
        for cell in report.cell_results
    ]

    return _sha256_json({
        "schema_version": FDR_SCHEMA_VERSION,
        "data_corpus_hash": report.data_corpus_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_config_hash": report.comparison_config_hash,
        "comparison_hash": report.comparison_hash,
        "fdr_config_hash": report.fdr_config_hash,
        "pvalue_input_hash": report.pvalue_input_hash,
        "method": report.method,
        "alpha": report.alpha,
        "eligible_cell_ids": report.eligible_cell_ids,
        "fdr_passed_cell_ids": report.fdr_passed_cell_ids,
        "fdr_failed_cell_ids": report.fdr_failed_cell_ids,
        "excluded_cell_ids": report.excluded_cell_ids,
        "exclusion_reasons": report.exclusion_reasons_by_cell,
        "cell_result_summaries": summaries,
        "status": report.status,
    })


# --- Output writers ---


def build_offline_fdr_manifest_payload(
    report: OfflineFdrCorrectionReport,
    *,
    comparison_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "phase": "offline_fdr_correction",
        "generated_at_utc": _now_utc_iso(),
        "git_sha": _get_git_sha(),
        "schema_version": FDR_SCHEMA_VERSION,
        "comparison_manifest_path": comparison_manifest_path,
        "data_corpus_hash": report.data_corpus_hash,
        "precommitment_hash": None,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_config_hash": report.comparison_config_hash,
        "comparison_hash": report.comparison_hash,
        "fdr_config_hash": report.fdr_config_hash,
        "pvalue_input_hash": report.pvalue_input_hash,
        "fdr_hash": report.fdr_hash,
        "method": report.method,
        "alpha": report.alpha,
        "fdr_family_size": report.fdr_family_size,
        "fdr_passed_cell_count": len(report.fdr_passed_cell_ids),
        "fdr_failed_cell_count": len(report.fdr_failed_cell_ids),
        "excluded_cell_count": len(report.excluded_cell_ids),
        "status": report.status,
        "safety": "public_data_observer_only",
    }


def write_offline_fdr_correction_outputs(
    report: OfflineFdrCorrectionReport,
    out_dir: Path,
    *,
    comparison_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Write FDR correction JSON report and manifest to output directory."""
    run_dir = safe_output_dir(Path(out_dir), allow_existing=overwrite)

    result_payload: dict[str, Any] = {
        "schema_version": FDR_SCHEMA_VERSION,
        "status": report.status,
        "method": report.method,
        "alpha": report.alpha,
        "fdr_family_size": report.fdr_family_size,
        "eligible_cell_ids": report.eligible_cell_ids,
        "fdr_passed_cell_ids": report.fdr_passed_cell_ids,
        "fdr_failed_cell_ids": report.fdr_failed_cell_ids,
        "excluded_cell_ids": report.excluded_cell_ids,
        "exclusion_reasons_by_cell": report.exclusion_reasons_by_cell,
        "fdr_config_hash": report.fdr_config_hash,
        "pvalue_input_hash": report.pvalue_input_hash,
        "fdr_hash": report.fdr_hash,
        "cell_results": [asdict(cell) for cell in report.cell_results],
        "metadata": report.metadata,
        "data_corpus_hash": report.data_corpus_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_config_hash": report.comparison_config_hash,
        "comparison_hash": report.comparison_hash,
    }
    result_path = run_dir / "offline_fdr_correction.json"
    atomic_write_json(result_path, result_payload)

    manifest_path = run_dir / "offline_fdr_correction_manifest.json"
    atomic_write_json(
        manifest_path,
        build_offline_fdr_manifest_payload(
            report,
            comparison_manifest_path=comparison_manifest_path,
        ),
    )

    return {"result_path": result_path, "manifest_path": manifest_path}
