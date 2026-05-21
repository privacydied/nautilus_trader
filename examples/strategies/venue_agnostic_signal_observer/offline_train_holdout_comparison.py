"""
Phase 2B-2C1: Train-vs-holdout survival comparison.

Consumes train evaluation, train survivor freeze, and holdout evaluation
artifacts. Compares frozen train survivor metrics against holdout evaluation
metrics and produces a deterministic descriptive comparison report.

No FDR, no null tests, no cost sensitivity, no candidate falsification,
no Shadow Executor, no Family 4 conditioning, no latency diagnostics,
no final offline verdicts, no live/candidate/trading statuses.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .run_artifacts import atomic_write_json
from .run_artifacts import safe_output_dir


COMPARISON_SCHEMA_VERSION = "offline_train_holdout_comparison_v1"

# Status values
STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY = "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY"
STATUS_NO_TRAIN_SURVIVORS = "NO_TRAIN_SURVIVORS"
STATUS_NO_HOLDOUT_EVALUATED_SURVIVORS = "NO_HOLDOUT_EVALUATED_SURVIVORS"
STATUS_NO_HOLDOUT_SURVIVORS = "NO_HOLDOUT_SURVIVORS"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_TRAIN_EVALUATION_HASH_MISMATCH = "TRAIN_EVALUATION_HASH_MISMATCH"
STATUS_SURVIVOR_FREEZE_HASH_MISMATCH = "SURVIVOR_FREEZE_HASH_MISMATCH"
STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH = "HOLDOUT_EVALUATION_HASH_MISMATCH"
STATUS_UNUSABLE_COMPARISON_INPUT = "UNUSABLE_COMPARISON_INPUT"

_HOLDOUT_EVALUABLE_STATUSES = frozenset({"OFFLINE_HOLDOUT_EVALUATION_READY"})

# Holdout evaluation cell statuses that are non-evaluable
_HOLDOUT_NON_EVALUABLE_STATUSES = frozenset({
    "NO_TRAIN_SURVIVORS",
    "NO_HOLDOUT_WINDOWS",
    "NO_EVALUABLE_SURVIVORS",
    "INSUFFICIENT_HOLDOUT_EVENTS",
    "LATENCY_GATE_REQUIRED_NOT_RUN",
    "UNSUPPORTED_SURVIVOR_CELL",
    "CONDITIONING_NOT_RUN_PHASE_2B2B2",
})


@dataclass(frozen=True)
class OfflineTrainHoldoutComparisonConfig:
    """Strict comparison thresholds for train-vs-holdout survival."""

    min_holdout_valid_events: int = 1
    min_holdout_net_mean_bps: float = 0.0
    min_holdout_net_median_bps: float = 0.0
    min_holdout_win_rate: float = 0.5
    min_holdout_worst_net_bps: float | None = None
    max_train_to_holdout_net_mean_decay_ratio: float | None = None


@dataclass(frozen=True)
class OfflineTrainHoldoutComparisonCell:
    """One comparison row per frozen train survivor."""

    cell_id: str
    family_id: str
    family_name: str
    signal_variant: str | None
    lookback_ms: int
    horizon_ms: int
    train_valid_event_count: int
    holdout_valid_event_count: int
    train_net_mean_bps: float | None
    holdout_net_mean_bps: float | None
    train_net_median_bps: float | None
    holdout_net_median_bps: float | None
    train_win_rate: float | None
    holdout_win_rate: float | None
    train_worst_net_bps: float | None
    holdout_worst_net_bps: float | None
    net_mean_decay_bps: float | None
    net_mean_decay_ratio: float | None
    holdout_survival_status: str
    comparison_passed: bool
    failure_reasons: list[str]
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_hash: str
    holdout_evaluation_hash: str


@dataclass(frozen=True)
class OfflineTrainHoldoutComparisonReport:
    """Complete train-vs-holdout comparison report."""

    status: str
    comparison_cells: list[OfflineTrainHoldoutComparisonCell]
    train_survivor_cell_ids: list[str]
    holdout_surviving_cell_ids: list[str]
    holdout_failed_cell_ids: list[str]
    missing_holdout_cell_ids: list[str]
    comparison_config_hash: str
    comparison_hash: str
    metadata: dict[str, Any]
    run_id: str
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_config_hash: str
    survivor_freeze_hash: str
    holdout_evaluation_hash: str


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


def _sorted_unique_strings(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values})


def _metric_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _config_payload(config: OfflineTrainHoldoutComparisonConfig) -> dict[str, Any]:
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "min_holdout_valid_events": config.min_holdout_valid_events,
        "min_holdout_net_mean_bps": config.min_holdout_net_mean_bps,
        "min_holdout_net_median_bps": config.min_holdout_net_median_bps,
        "min_holdout_win_rate": config.min_holdout_win_rate,
        "min_holdout_worst_net_bps": config.min_holdout_worst_net_bps,
        "max_train_to_holdout_net_mean_decay_ratio": config.max_train_to_holdout_net_mean_decay_ratio,
    }


def compute_comparison_config_hash(config: OfflineTrainHoldoutComparisonConfig) -> str:
    return _sha256_json(_config_payload(config))


def compute_comparison_hash(report: OfflineTrainHoldoutComparisonReport) -> str:
    """Deterministic SHA-256 over canonical sorted JSON of comparison essentials."""
    summaries = [
        {
            "cell_id": cell.cell_id,
            "holdout_survival_status": cell.holdout_survival_status,
            "comparison_passed": cell.comparison_passed,
            "failure_reasons": cell.failure_reasons,
            "holdout_net_mean_bps": cell.holdout_net_mean_bps,
            "holdout_net_median_bps": cell.holdout_net_median_bps,
            "holdout_win_rate": cell.holdout_win_rate,
            "holdout_worst_net_bps": cell.holdout_worst_net_bps,
            "net_mean_decay_bps": cell.net_mean_decay_bps,
            "net_mean_decay_ratio": cell.net_mean_decay_ratio,
        }
        for cell in report.comparison_cells
    ]
    return _sha256_json(
        {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "data_corpus_hash": report.data_corpus_hash,
            "window_index_hash": report.window_index_hash,
            "discovery_config_hash": report.discovery_config_hash,
            "plan_hash": report.plan_hash,
            "evaluation_hash": report.evaluation_hash,
            "survivor_freeze_hash": report.survivor_freeze_hash,
            "holdout_evaluation_hash": report.holdout_evaluation_hash,
            "comparison_config_hash": report.comparison_config_hash,
            "train_survivor_cell_ids": report.train_survivor_cell_ids,
            "holdout_surviving_cell_ids": report.holdout_surviving_cell_ids,
            "holdout_failed_cell_ids": report.holdout_failed_cell_ids,
            "missing_holdout_cell_ids": report.missing_holdout_cell_ids,
            "comparison_cell_summaries": summaries,
            "status": report.status,
        }
    )


def _empty_comparison_result(
    *,
    status: str,
    comparison_config_hash: str,
    metadata: dict[str, Any],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_config_hash: str,
    survivor_freeze_hash: str,
    holdout_evaluation_hash: str,
) -> OfflineTrainHoldoutComparisonReport:
    report = OfflineTrainHoldoutComparisonReport(
        status=status,
        comparison_cells=[],
        train_survivor_cell_ids=[],
        holdout_surviving_cell_ids=[],
        holdout_failed_cell_ids=[],
        missing_holdout_cell_ids=[],
        comparison_config_hash=comparison_config_hash,
        comparison_hash="",
        metadata=metadata,
        run_id=f"offline_train_holdout_comparison_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_config_hash=survivor_freeze_config_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_evaluation_hash,
    )
    object.__setattr__(report, "comparison_hash", compute_comparison_hash(report))
    return report


def _find_train_result(cell_id: str, cells: list[dict[str, Any]]) -> dict[str, Any] | None:
    for cell in cells:
        if str(cell.get("cell_id", "")) == cell_id:
            return cell
    return None


def _find_holdout_result(cell_id: str, cells: list[dict[str, Any]]) -> dict[str, Any] | None:
    for cell in cells:
        if str(cell.get("cell_id", "")) == cell_id:
            return cell
    return None


def _find_survivor_cell(cell_id: str, survivors: list[dict[str, Any]]) -> dict[str, Any] | None:
    for cell in survivors:
        if str(cell.get("cell_id", "")) == cell_id:
            return cell
    return None


def _check_holdout_cell_thresholds(
    holdout_cell: dict[str, Any],
    config: OfflineTrainHoldoutComparisonConfig,
) -> list[str]:
    """Check holdout cell metrics against configured thresholds."""
    reasons: list[str] = []

    status = str(holdout_cell.get("status", ""))
    if status not in _HOLDOUT_EVALUABLE_STATUSES:
        reasons.append(f"holdout_status_not_evaluable:{status}")
        return reasons

    valid_events = int(holdout_cell.get("valid_event_count", 0))
    if valid_events < config.min_holdout_valid_events:
        reasons.append(
            f"below_min_holdout_valid_events:{valid_events}<{config.min_holdout_valid_events}"
        )
        return reasons

    net_mean = _metric_or_none(holdout_cell.get("net_mean_bps"))
    if net_mean is not None and net_mean <= config.min_holdout_net_mean_bps:
        reasons.append(
            f"below_min_holdout_net_mean_bps:{net_mean}<={config.min_holdout_net_mean_bps}"
        )
    elif net_mean is None:
        reasons.append("holdout_net_mean_bps_missing")

    net_median = _metric_or_none(holdout_cell.get("net_median_bps"))
    if net_median is not None and net_median < config.min_holdout_net_median_bps:
        reasons.append(
            f"below_min_holdout_net_median_bps:{net_median}<{config.min_holdout_net_median_bps}"
        )
    elif net_median is None:
        reasons.append("holdout_net_median_bps_missing")

    win_rate = _metric_or_none(holdout_cell.get("win_rate"))
    if win_rate is not None and win_rate < config.min_holdout_win_rate:
        reasons.append(
            f"below_min_holdout_win_rate:{win_rate}<{config.min_holdout_win_rate}"
        )
    elif win_rate is None:
        reasons.append("holdout_win_rate_missing")

    if config.min_holdout_worst_net_bps is not None:
        worst_net = _metric_or_none(holdout_cell.get("worst_net_bps"))
        if worst_net is not None and worst_net < config.min_holdout_worst_net_bps:
            reasons.append(
                f"below_min_holdout_worst_net_bps:{worst_net}<{config.min_holdout_worst_net_bps}"
            )
        elif worst_net is None:
            reasons.append("holdout_worst_net_bps_missing")

    return reasons


def _compute_decay_metrics(
    train_net_mean: float | None,
    holdout_net_mean: float | None,
) -> tuple[float | None, float | None]:
    """Compute decay metrics between train and holdout net means."""
    if train_net_mean is not None and holdout_net_mean is not None:
        decay_bps = train_net_mean - holdout_net_mean
        if train_net_mean > 0.0:
            decay_ratio = decay_bps / abs(train_net_mean)
        else:
            decay_ratio = None
        return decay_bps, decay_ratio
    return None, None


def _build_comparison_cell(
    train_cell: dict[str, Any],
    survivor_cell: dict[str, Any],
    holdout_cell: dict[str, Any] | None,
    config: OfflineTrainHoldoutComparisonConfig,
    eval_hash: str,
    survivor_freeze_hash: str,
    holdout_eval_hash: str,
    plan_hash: str,
) -> OfflineTrainHoldoutComparisonCell:
    """Build a single comparison row for one frozen train survivor."""
    cell_id = str(train_cell.get("cell_id", ""))
    family_id = str(train_cell.get("family_id", ""))

    train_net_mean = _metric_or_none(train_cell.get("net_mean_bps"))
    train_net_median = _metric_or_none(train_cell.get("net_median_bps"))
    train_win_rate = _metric_or_none(train_cell.get("win_rate"))
    train_worst_net = _metric_or_none(train_cell.get("worst_net_bps"))
    train_valid_events = int(train_cell.get("valid_event_count", 0))

    if holdout_cell is None:
        return OfflineTrainHoldoutComparisonCell(
            cell_id=cell_id,
            family_id=family_id,
            family_name=str(train_cell.get("family_name", "")),
            signal_variant=train_cell.get("signal_variant"),
            lookback_ms=int(train_cell.get("lookback_ms", 0)),
            horizon_ms=int(train_cell.get("horizon_ms", 0)),
            train_valid_event_count=train_valid_events,
            holdout_valid_event_count=0,
            train_net_mean_bps=train_net_mean,
            holdout_net_mean_bps=None,
            train_net_median_bps=train_net_median,
            holdout_net_median_bps=None,
            train_win_rate=train_win_rate,
            holdout_win_rate=None,
            train_worst_net_bps=train_worst_net,
            holdout_worst_net_bps=None,
            net_mean_decay_bps=None,
            net_mean_decay_ratio=None,
            holdout_survival_status="MISSING_HOLDOUT_RESULT",
            comparison_passed=False,
            failure_reasons=["missing_holdout_result"],
            data_corpus_hash=str(train_cell.get("data_corpus_hash", "")),
            window_index_hash=str(train_cell.get("window_index_hash", "")),
            discovery_config_hash=str(survivor_cell.get("discovery_config_hash", "")),
            plan_hash=plan_hash,
            evaluation_hash=eval_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    holdout_net_mean = _metric_or_none(holdout_cell.get("net_mean_bps"))
    holdout_net_median = _metric_or_none(holdout_cell.get("net_median_bps"))
    holdout_win_rate = _metric_or_none(holdout_cell.get("win_rate"))
    holdout_worst_net = _metric_or_none(holdout_cell.get("worst_net_bps"))
    holdout_valid_events = int(holdout_cell.get("valid_event_count", 0))
    holdout_status = str(holdout_cell.get("status", ""))

    decay_bps, decay_ratio = _compute_decay_metrics(train_net_mean, holdout_net_mean)

    failure_reasons = _check_holdout_cell_thresholds(holdout_cell, config)
    comparison_passed = len(failure_reasons) == 0

    # Decay ratio threshold check — uses both train and holdout metrics
    if config.max_train_to_holdout_net_mean_decay_ratio is not None:
        if decay_ratio is None:
            failure_reasons.append("train_to_holdout_decay_ratio_unavailable")
            comparison_passed = False
        elif decay_ratio > config.max_train_to_holdout_net_mean_decay_ratio:
            failure_reasons.append(
                f"train_to_holdout_decay_ratio_above_threshold:{decay_ratio}>{config.max_train_to_holdout_net_mean_decay_ratio}"
            )
            comparison_passed = False

    return OfflineTrainHoldoutComparisonCell(
        cell_id=cell_id,
        family_id=family_id,
        family_name=str(train_cell.get("family_name", "")),
        signal_variant=train_cell.get("signal_variant"),
        lookback_ms=int(train_cell.get("lookback_ms", 0)),
        horizon_ms=int(train_cell.get("horizon_ms", 0)),
        train_valid_event_count=train_valid_events,
        holdout_valid_event_count=holdout_valid_events,
        train_net_mean_bps=train_net_mean,
        holdout_net_mean_bps=holdout_net_mean,
        train_net_median_bps=train_net_median,
        holdout_net_median_bps=holdout_net_median,
        train_win_rate=train_win_rate,
        holdout_win_rate=holdout_win_rate,
        train_worst_net_bps=train_worst_net,
        holdout_worst_net_bps=holdout_worst_net,
        net_mean_decay_bps=decay_bps,
        net_mean_decay_ratio=decay_ratio,
        holdout_survival_status=holdout_status,
        comparison_passed=comparison_passed,
        failure_reasons=failure_reasons,
        data_corpus_hash=str(train_cell.get("data_corpus_hash", "")),
        window_index_hash=str(train_cell.get("window_index_hash", "")),
        discovery_config_hash=str(survivor_cell.get("discovery_config_hash", "")),
        plan_hash=plan_hash,
        evaluation_hash=eval_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_eval_hash,
    )


def _validate_config_keys(config_dict: dict[str, Any]) -> None:
    """Strictly reject unknown config keys."""
    allowed = {
        "min_holdout_valid_events",
        "min_holdout_net_mean_bps",
        "min_holdout_net_median_bps",
        "min_holdout_win_rate",
        "min_holdout_worst_net_bps",
        "max_train_to_holdout_net_mean_decay_ratio",
    }
    unknown = set(config_dict.keys()) - allowed
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")


def _check_family4_in_survivors(
    train_survivor_cell_ids: list[str],
    train_cells: list[dict[str, Any]],
) -> list[str]:
    """Check if any Family 4 cells appear as survivors (should be impossible)."""
    problems: list[str] = []
    for cell in train_cells:
        if str(cell.get("cell_id", "")) in train_survivor_cell_ids:
            family_id = str(cell.get("family_id", ""))
            if "family_4" in family_id or "conditioning" in family_id:
                problems.append(str(cell.get("cell_id", "")))
    return problems


def build_offline_train_holdout_comparison_report(
    *,
    train_evaluation_manifest: dict[str, Any],
    train_evaluation_payload: dict[str, Any],
    train_survivor_freeze_manifest: dict[str, Any],
    train_survivor_freeze_payload: dict[str, Any],
    holdout_evaluation_manifest: dict[str, Any],
    holdout_evaluation_payload: dict[str, Any],
    comparison_config: OfflineTrainHoldoutComparisonConfig | None = None,
) -> OfflineTrainHoldoutComparisonReport:
    """
    Build the train-vs-holdout comparison report.

    All identity checks must pass before producing a comparison-ready report.
    """
    config = comparison_config or OfflineTrainHoldoutComparisonConfig()
    comparison_config_hash = compute_comparison_config_hash(config)

    # Extract lineage hashes
    data_corpus_hash = str(train_evaluation_payload.get("data_corpus_hash", ""))
    window_index_hash = str(train_evaluation_payload.get("window_index_hash", ""))
    discovery_config_hash = str(train_evaluation_payload.get("discovery_config_hash", ""))
    plan_hash = str(train_evaluation_payload.get("plan_hash", ""))
    evaluation_hash = str(train_evaluation_payload.get("evaluation_hash", ""))
    survivor_freeze_hash = str(train_survivor_freeze_payload.get("survivor_freeze_hash", ""))
    survivor_freeze_config_hash = str(train_survivor_freeze_payload.get("survivor_freeze_config_hash", ""))
    holdout_eval_hash = str(holdout_evaluation_payload.get("holdout_evaluation_hash", ""))

    metadata = {
        "comparison_config": _config_payload(config),
        "train_evaluation_status": str(train_evaluation_payload.get("status", "")),
        "survivor_freeze_status": str(train_survivor_freeze_payload.get("status", "")),
        "holdout_evaluation_status": str(holdout_evaluation_payload.get("status", "")),
    }

    # ---- Identity checks ----

    # 1. Train evaluation manifest hash matches train evaluation payload
    manifest_eval_hash = str(train_evaluation_manifest.get("evaluation_hash", ""))
    if manifest_eval_hash != evaluation_hash:
        return _empty_comparison_result(
            status=STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # 2. Survivor freeze manifest hash matches survivor freeze payload
    manifest_freeze_hash = str(train_survivor_freeze_manifest.get("survivor_freeze_hash", ""))
    if manifest_freeze_hash != survivor_freeze_hash:
        return _empty_comparison_result(
            status=STATUS_SURVIVOR_FREEZE_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # 3. Holdout evaluation manifest hash matches holdout evaluation payload
    manifest_holdout_hash = str(holdout_evaluation_manifest.get("holdout_evaluation_hash", ""))
    if manifest_holdout_hash != holdout_eval_hash:
        return _empty_comparison_result(
            status=STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # 4. Survivor freeze evaluation_hash matches train evaluation_hash
    freeze_eval_hash = str(train_survivor_freeze_payload.get("evaluation_hash", ""))
    if freeze_eval_hash != evaluation_hash:
        return _empty_comparison_result(
            status=STATUS_INPUT_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # 5. Holdout evaluation survivor_freeze_hash matches freeze hash
    holdout_freeze_hash_ref = str(holdout_evaluation_payload.get("survivor_freeze_hash", ""))
    if holdout_freeze_hash_ref != survivor_freeze_hash:
        return _empty_comparison_result(
            status=STATUS_INPUT_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # 6. Holdout evaluation plan_hash matches lineage
    holdout_plan_hash = str(holdout_evaluation_payload.get("plan_hash", ""))
    freeze_plan_hash = str(train_survivor_freeze_payload.get("plan_hash", ""))
    if holdout_plan_hash != plan_hash or freeze_plan_hash != plan_hash:
        return _empty_comparison_result(
            status=STATUS_INPUT_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # 7. Precommitment hash compatibility (all null is ok)
    precommit_sources = {
        train_evaluation_manifest.get("precommitment_hash"),
        train_survivor_freeze_manifest.get("precommitment_hash"),
        holdout_evaluation_manifest.get("precommitment_hash"),
    }
    non_null_precommit = {v for v in precommit_sources if v is not None}
    if len(non_null_precommit) > 1:
        return _empty_comparison_result(
            status=STATUS_INPUT_HASH_MISMATCH,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # ---- Extract artifacts ----

    train_survivor_cell_ids = _sorted_unique_strings(
        list(train_survivor_freeze_payload.get("train_survivor_cell_ids", []))
    )
    train_cell_results = list(train_evaluation_payload.get("cell_results", []))
    survivor_cells_list = list(train_survivor_freeze_payload.get("survivor_cells", []))
    holdout_cell_results = list(holdout_evaluation_payload.get("cell_results", []))

    # Check for Family 4 in survivors
    family4_problems = _check_family4_in_survivors(train_survivor_cell_ids, train_cell_results)
    if family4_problems:
        return _empty_comparison_result(
            status=STATUS_UNUSABLE_COMPARISON_INPUT,
            comparison_config_hash=comparison_config_hash,
            metadata={
                **metadata,
                "family_4_survivors_detected": family4_problems,
                "reason": "family_4_conditioning_not_allowed",
            },
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    if not train_survivor_cell_ids:
        return _empty_comparison_result(
            status=STATUS_NO_TRAIN_SURVIVORS,
            comparison_config_hash=comparison_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
            holdout_evaluation_hash=holdout_eval_hash,
        )

    # ---- Build comparison cells ----

    comparison_cells: list[OfflineTrainHoldoutComparisonCell] = []
    missing_holdout_cell_ids: list[str] = []
    holdout_failed_cell_ids: list[str] = []
    holdout_surviving_cell_ids: list[str] = []

    for cell_id in train_survivor_cell_ids:
        train_cell = _find_train_result(cell_id, train_cell_results)
        if train_cell is None:
            # Should not happen with valid upstream artifacts, but handle gracefully
            missing_holdout_cell_ids.append(cell_id)
            continue

        survivor_cell = _find_survivor_cell(cell_id, survivor_cells_list)
        if survivor_cell is None:
            missing_holdout_cell_ids.append(cell_id)
            continue

        holdout_cell = _find_holdout_result(cell_id, holdout_cell_results)

        comp_cell = _build_comparison_cell(
            train_cell,
            survivor_cell,
            holdout_cell,
            config,
            evaluation_hash,
            survivor_freeze_hash,
            holdout_eval_hash,
            plan_hash,
        )
        comparison_cells.append(comp_cell)

        if holdout_cell is None:
            missing_holdout_cell_ids.append(cell_id)
        elif comp_cell.comparison_passed:
            holdout_surviving_cell_ids.append(cell_id)
        else:
            holdout_failed_cell_ids.append(cell_id)

    # ---- Determine status ----

    holdout_evaluated_count = len(train_survivor_cell_ids) - len(missing_holdout_cell_ids)
    if holdout_evaluated_count == 0:
        status = STATUS_NO_HOLDOUT_EVALUATED_SURVIVORS
    elif not holdout_surviving_cell_ids:
        status = STATUS_NO_HOLDOUT_SURVIVORS
    else:
        status = STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY

    # ---- Build report ----

    run_id = f"offline_train_holdout_comparison_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    report = OfflineTrainHoldoutComparisonReport(
        status=status,
        comparison_cells=comparison_cells,
        train_survivor_cell_ids=_sorted_unique_strings(train_survivor_cell_ids),
        holdout_surviving_cell_ids=_sorted_unique_strings(holdout_surviving_cell_ids),
        holdout_failed_cell_ids=_sorted_unique_strings(holdout_failed_cell_ids),
        missing_holdout_cell_ids=_sorted_unique_strings(missing_holdout_cell_ids),
        comparison_config_hash=comparison_config_hash,
        comparison_hash="",
        metadata=metadata,
        run_id=run_id,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_config_hash=survivor_freeze_config_hash,
        survivor_freeze_hash=survivor_freeze_hash,
        holdout_evaluation_hash=holdout_eval_hash,
    )
    object.__setattr__(report, "comparison_hash", compute_comparison_hash(report))
    return report


def build_offline_train_holdout_comparison_manifest_payload(
    report: OfflineTrainHoldoutComparisonReport,
    *,
    train_evaluation_manifest_path: str,
    train_survivor_freeze_manifest_path: str,
    holdout_evaluation_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "phase": "offline_train_holdout_comparison",
        "generated_at_utc": _now_utc_iso(),
        "git_sha": _get_git_sha(),
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "train_evaluation_manifest_path": train_evaluation_manifest_path,
        "train_survivor_freeze_manifest_path": train_survivor_freeze_manifest_path,
        "holdout_evaluation_manifest_path": holdout_evaluation_manifest_path,
        "data_corpus_hash": report.data_corpus_hash,
        "precommitment_hash": None,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_config_hash": report.survivor_freeze_config_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "comparison_config_hash": report.comparison_config_hash,
        "comparison_hash": report.comparison_hash,
        "status": report.status,
        "train_survivor_cell_count": len(report.train_survivor_cell_ids),
        "holdout_evaluated_survivor_count": len(report.train_survivor_cell_ids) - len(report.missing_holdout_cell_ids),
        "holdout_surviving_cell_count": len(report.holdout_surviving_cell_ids),
        "holdout_failed_cell_count": len(report.holdout_failed_cell_ids),
        "missing_holdout_cell_count": len(report.missing_holdout_cell_ids),
        "safety": "public_data_observer_only",
    }


def write_offline_train_holdout_comparison_outputs(
    report: OfflineTrainHoldoutComparisonReport,
    out_dir: Path,
    *,
    train_evaluation_manifest_path: str,
    train_survivor_freeze_manifest_path: str,
    holdout_evaluation_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Write comparison JSON report and manifest to output directory."""
    run_dir = safe_output_dir(Path(out_dir), allow_existing=overwrite)

    result_payload = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "status": report.status,
        "comparison_cells": [asdict(cell) for cell in report.comparison_cells],
        "train_survivor_cell_ids": report.train_survivor_cell_ids,
        "holdout_surviving_cell_ids": report.holdout_surviving_cell_ids,
        "holdout_failed_cell_ids": report.holdout_failed_cell_ids,
        "missing_holdout_cell_ids": report.missing_holdout_cell_ids,
        "comparison_config_hash": report.comparison_config_hash,
        "comparison_hash": report.comparison_hash,
        "metadata": report.metadata,
        "data_corpus_hash": report.data_corpus_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_config_hash": report.survivor_freeze_config_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
    }
    result_path = run_dir / "offline_train_holdout_comparison.json"
    atomic_write_json(result_path, result_payload)

    manifest_path = run_dir / "offline_train_holdout_comparison_manifest.json"
    atomic_write_json(
        manifest_path,
        build_offline_train_holdout_comparison_manifest_payload(
            report,
            train_evaluation_manifest_path=train_evaluation_manifest_path,
            train_survivor_freeze_manifest_path=train_survivor_freeze_manifest_path,
            holdout_evaluation_manifest_path=holdout_evaluation_manifest_path,
        ),
    )
    return {"result_path": result_path, "manifest_path": manifest_path}
