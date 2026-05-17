"""Phase 2B-2B1 deterministic train survivor freeze.

Consumes Phase 2B-2A train-evaluation artifacts and freezes a deterministic
train-survivor set from the existing raw train screen only. No holdout
inspection, no forward-return recomputation, no FDR, no null tests, no cost
sensitivity, no candidate falsification, no Family 4 conditioning, and no live
execution or network access.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .run_artifacts import atomic_write_json, safe_output_dir

FREEZE_SCHEMA_VERSION = "offline_train_survivor_freeze_v1"

STATUS_OFFLINE_TRAIN_SURVIVOR_FREEZE_READY = "OFFLINE_TRAIN_SURVIVOR_FREEZE_READY"
STATUS_NO_TRAIN_SURVIVORS = "NO_TRAIN_SURVIVORS"
STATUS_NO_RAW_TRAIN_SCREEN_CELLS = "NO_RAW_TRAIN_SCREEN_CELLS"
STATUS_TRAIN_EVALUATION_HASH_MISMATCH = "TRAIN_EVALUATION_HASH_MISMATCH"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_UNUSABLE_TRAIN_EVALUATION = "UNUSABLE_TRAIN_EVALUATION"

ALLOWED_TRAIN_EVALUATION_STATUSES = {
    "OFFLINE_TRAIN_EVALUATION_READY",
    "TRAIN_RAW_EDGE_SCREEN_READY",
    "TRAIN_RAW_EDGE_SCREEN_EMPTY",
}
USABLE_EMPTY_TRAIN_EVALUATION_STATUSES = {"TRAIN_RAW_EDGE_SCREEN_EMPTY"}


@dataclass(frozen=True)
class OfflineTrainSurvivorFreezeConfig:
    min_valid_events: int = 2
    min_net_mean_bps: float = 0.0
    min_net_median_bps: float = 0.0
    min_win_rate: float = 0.5
    min_worst_net_bps: float | None = None
    max_survivors: int | None = None


@dataclass(frozen=True)
class OfflineTrainSurvivorCell:
    cell_id: str
    family_id: str
    family_name: str
    signal_variant: str | None
    lookback_ms: int
    horizon_ms: int
    valid_event_count: int
    net_mean_bps: float
    net_median_bps: float
    win_rate: float
    worst_net_bps: float
    freeze_rank: int
    freeze_reasons: list[str]
    data_corpus_hash: str
    window_index_hash: str
    plan_hash: str
    evaluation_hash: str


@dataclass(frozen=True)
class OfflineTrainSurvivorFreezeResult:
    status: str
    train_survivor_cell_ids: list[str]
    rejected_cell_ids: list[str]
    rejection_reasons_by_cell: dict[str, list[str]]
    raw_train_screen_cell_ids: list[str]
    holdout_window_ids_seen_but_not_evaluated: list[str]
    survivor_cells: list[OfflineTrainSurvivorCell]
    survivor_freeze_config_hash: str
    survivor_freeze_hash: str
    metadata: dict[str, Any]
    run_id: str
    generated_at_utc: str
    git_sha: str
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str
    evaluation_hash: str


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_git_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _sorted_unique_strings(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values})


def _config_payload(config: OfflineTrainSurvivorFreezeConfig) -> dict[str, Any]:
    return {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "min_valid_events": config.min_valid_events,
        "min_net_mean_bps": config.min_net_mean_bps,
        "min_net_median_bps": config.min_net_median_bps,
        "min_win_rate": config.min_win_rate,
        "min_worst_net_bps": config.min_worst_net_bps,
        "max_survivors": config.max_survivors,
    }


def compute_survivor_freeze_config_hash(config: OfflineTrainSurvivorFreezeConfig) -> str:
    return _sha256_json(_config_payload(config))


def _empty_result(
    *,
    status: str,
    raw_train_screen_cell_ids: list[str],
    holdout_window_ids_seen_but_not_evaluated: list[str],
    survivor_freeze_config_hash: str,
    metadata: dict[str, Any],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
) -> OfflineTrainSurvivorFreezeResult:
    result = OfflineTrainSurvivorFreezeResult(
        status=status,
        train_survivor_cell_ids=[],
        rejected_cell_ids=[],
        rejection_reasons_by_cell={},
        raw_train_screen_cell_ids=list(raw_train_screen_cell_ids),
        holdout_window_ids_seen_but_not_evaluated=list(holdout_window_ids_seen_but_not_evaluated),
        survivor_cells=[],
        survivor_freeze_config_hash=survivor_freeze_config_hash,
        survivor_freeze_hash="",
        metadata=metadata,
        run_id=f"offline_train_survivor_freeze_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        generated_at_utc=_now_utc_iso(),
        git_sha=_get_git_sha(),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
    )
    object.__setattr__(result, "survivor_freeze_hash", compute_survivor_freeze_hash(result))
    return result


def _lineage_matches(train_evaluation_manifest: dict[str, Any], train_evaluation_payload: dict[str, Any]) -> bool:
    fields = (
        "data_corpus_hash",
        "window_index_hash",
        "discovery_config_hash",
        "plan_hash",
        "evaluation_hash",
    )
    return all(train_evaluation_manifest.get(field) == train_evaluation_payload.get(field) for field in fields)


def _metric_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _cell_sort_key(cell: dict[str, Any]) -> tuple[float, float, int, str]:
    return (
        -float(cell["net_mean_bps"]),
        -float(cell["win_rate"]),
        -int(cell["valid_event_count"]),
        str(cell["cell_id"]),
    )


def _build_survivor_cell(cell: dict[str, Any], *, freeze_rank: int, evaluation_hash: str) -> OfflineTrainSurvivorCell:
    return OfflineTrainSurvivorCell(
        cell_id=str(cell["cell_id"]),
        family_id=str(cell["family_id"]),
        family_name=str(cell["family_name"]),
        signal_variant=cell.get("signal_variant"),
        lookback_ms=int(cell["lookback_ms"]),
        horizon_ms=int(cell["horizon_ms"]),
        valid_event_count=int(cell["valid_event_count"]),
        net_mean_bps=float(cell["net_mean_bps"]),
        net_median_bps=float(cell["net_median_bps"]),
        win_rate=float(cell["win_rate"]),
        worst_net_bps=float(cell["worst_net_bps"]),
        freeze_rank=freeze_rank,
        freeze_reasons=["passed_train_survivor_freeze"],
        data_corpus_hash=str(cell["data_corpus_hash"]),
        window_index_hash=str(cell["window_index_hash"]),
        plan_hash=str(cell["plan_hash"]),
        evaluation_hash=evaluation_hash,
    )


def _evaluate_cell(cell: dict[str, Any], *, raw_train_screen_cell_ids: set[str], config: OfflineTrainSurvivorFreezeConfig) -> list[str]:
    reasons: list[str] = []
    if str(cell.get("status")) != "OFFLINE_TRAIN_EVALUATION_READY":
        reasons.append("cell_status_not_ready")
        return reasons
    if str(cell.get("cell_id")) not in raw_train_screen_cell_ids:
        reasons.append("not_in_raw_train_screen")
        return reasons
    if int(cell.get("valid_event_count", 0)) < config.min_valid_events:
        reasons.append("below_min_valid_events")
        return reasons
    net_mean_bps = _metric_or_none(cell.get("net_mean_bps"))
    if net_mean_bps is None or net_mean_bps <= config.min_net_mean_bps:
        reasons.append("below_min_net_mean_bps")
        return reasons
    net_median_bps = _metric_or_none(cell.get("net_median_bps"))
    if net_median_bps is None or net_median_bps < config.min_net_median_bps:
        reasons.append("below_min_net_median_bps")
        return reasons
    win_rate = _metric_or_none(cell.get("win_rate"))
    if win_rate is None or win_rate < config.min_win_rate:
        reasons.append("below_min_win_rate")
        return reasons
    worst_net_bps = _metric_or_none(cell.get("worst_net_bps"))
    if worst_net_bps is None:
        reasons.append("missing_worst_net_bps")
        return reasons
    if config.min_worst_net_bps is not None and worst_net_bps < config.min_worst_net_bps:
        reasons.append("below_min_worst_net_bps")
        return reasons
    if str(cell.get("family_id")) == "family_4_stablecoin_quote_regime_conditioning":
        reasons.append("family_4_conditioning_excluded")
        return reasons
    if not bool(cell.get("is_edge_family", False)):
        reasons.append("non_edge_family_excluded")
        return reasons
    if str(cell.get("family_id")) == "family_3_usd_reference_translation_lag":
        if not bool(cell.get("latency_gate_satisfied", False)):
            reasons.append("LATENCY_GATE_REQUIRED_NOT_RUN")
            return reasons
    return reasons


def _survivor_cell_payload(cell: OfflineTrainSurvivorCell) -> dict[str, Any]:
    return asdict(cell)


def compute_survivor_freeze_hash(result: OfflineTrainSurvivorFreezeResult) -> str:
    return _sha256_json(
        {
            "schema_version": FREEZE_SCHEMA_VERSION,
            "data_corpus_hash": result.data_corpus_hash,
            "window_index_hash": result.window_index_hash,
            "discovery_config_hash": result.discovery_config_hash,
            "plan_hash": result.plan_hash,
            "evaluation_hash": result.evaluation_hash,
            "survivor_freeze_config_hash": result.survivor_freeze_config_hash,
            "raw_train_screen_cell_ids": result.raw_train_screen_cell_ids,
            "train_survivor_cell_ids": result.train_survivor_cell_ids,
            "rejected_cell_ids": result.rejected_cell_ids,
            "rejection_reasons_by_cell": result.rejection_reasons_by_cell,
            "survivor_cells": [_survivor_cell_payload(cell) for cell in result.survivor_cells],
            "holdout_window_ids_seen_but_not_evaluated": result.holdout_window_ids_seen_but_not_evaluated,
            "status": result.status,
        }
    )


def build_offline_train_survivor_freeze_result(
    *,
    train_evaluation_manifest: dict[str, Any],
    train_evaluation_payload: dict[str, Any],
    freeze_config: OfflineTrainSurvivorFreezeConfig,
) -> OfflineTrainSurvivorFreezeResult:
    survivor_freeze_config_hash = compute_survivor_freeze_config_hash(freeze_config)
    raw_train_screen_cell_ids = _sorted_unique_strings(list(train_evaluation_payload.get("raw_train_screen_cell_ids", [])))
    holdout_window_ids = _sorted_unique_strings(list(train_evaluation_payload.get("holdout_window_ids_seen_but_not_evaluated", [])))
    metadata = {
        "holdout_window_ids_seen_but_not_evaluated": list(holdout_window_ids),
        "freeze_config": _config_payload(freeze_config),
    }
    data_corpus_hash = str(train_evaluation_payload.get("data_corpus_hash", ""))
    window_index_hash = str(train_evaluation_payload.get("window_index_hash", ""))
    discovery_config_hash = str(train_evaluation_payload.get("discovery_config_hash", ""))
    plan_hash = str(train_evaluation_payload.get("plan_hash", ""))
    evaluation_hash = str(train_evaluation_payload.get("evaluation_hash", ""))

    if str(train_evaluation_manifest.get("evaluation_hash", "")) != evaluation_hash:
        return _empty_result(
            status=STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
            raw_train_screen_cell_ids=raw_train_screen_cell_ids,
            holdout_window_ids_seen_but_not_evaluated=holdout_window_ids,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
        )
    if not _lineage_matches(train_evaluation_manifest, train_evaluation_payload):
        return _empty_result(
            status=STATUS_INPUT_HASH_MISMATCH,
            raw_train_screen_cell_ids=raw_train_screen_cell_ids,
            holdout_window_ids_seen_but_not_evaluated=holdout_window_ids,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
        )
    evaluation_status = str(train_evaluation_payload.get("status", ""))
    if evaluation_status not in ALLOWED_TRAIN_EVALUATION_STATUSES:
        return _empty_result(
            status=STATUS_UNUSABLE_TRAIN_EVALUATION,
            raw_train_screen_cell_ids=raw_train_screen_cell_ids,
            holdout_window_ids_seen_but_not_evaluated=holdout_window_ids,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
        )
    if evaluation_status in USABLE_EMPTY_TRAIN_EVALUATION_STATUSES or not raw_train_screen_cell_ids:
        return _empty_result(
            status=STATUS_NO_RAW_TRAIN_SCREEN_CELLS,
            raw_train_screen_cell_ids=raw_train_screen_cell_ids,
            holdout_window_ids_seen_but_not_evaluated=holdout_window_ids,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
        )

    cell_results = list(train_evaluation_payload.get("cell_results", []))
    raw_train_screen_set = set(raw_train_screen_cell_ids)
    accepted_cells: list[dict[str, Any]] = []
    rejection_reasons_by_cell: dict[str, list[str]] = {}
    for cell in cell_results:
        reasons = _evaluate_cell(cell, raw_train_screen_cell_ids=raw_train_screen_set, config=freeze_config)
        if reasons:
            rejection_reasons_by_cell[str(cell.get("cell_id"))] = reasons
        else:
            accepted_cells.append(cell)

    accepted_cells.sort(key=_cell_sort_key)
    if freeze_config.max_survivors is not None and len(accepted_cells) > freeze_config.max_survivors:
        survivors = accepted_cells[: freeze_config.max_survivors]
        capped = accepted_cells[freeze_config.max_survivors :]
        for cell in capped:
            rejection_reasons_by_cell[str(cell.get("cell_id"))] = ["excluded_by_max_survivors"]
    else:
        survivors = accepted_cells

    survivor_cells = [
        _build_survivor_cell(cell, freeze_rank=index + 1, evaluation_hash=evaluation_hash)
        for index, cell in enumerate(survivors)
    ]
    train_survivor_cell_ids = [cell.cell_id for cell in survivor_cells]
    rejected_cell_ids = sorted(rejection_reasons_by_cell)
    status = STATUS_OFFLINE_TRAIN_SURVIVOR_FREEZE_READY if survivor_cells else STATUS_NO_TRAIN_SURVIVORS

    result = OfflineTrainSurvivorFreezeResult(
        status=status,
        train_survivor_cell_ids=train_survivor_cell_ids,
        rejected_cell_ids=rejected_cell_ids,
        rejection_reasons_by_cell={key: rejection_reasons_by_cell[key] for key in rejected_cell_ids},
        raw_train_screen_cell_ids=raw_train_screen_cell_ids,
        holdout_window_ids_seen_but_not_evaluated=holdout_window_ids,
        survivor_cells=survivor_cells,
        survivor_freeze_config_hash=survivor_freeze_config_hash,
        survivor_freeze_hash="",
        metadata=metadata,
        run_id=f"offline_train_survivor_freeze_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        generated_at_utc=_now_utc_iso(),
        git_sha=_get_git_sha(),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
    )
    object.__setattr__(result, "survivor_freeze_hash", compute_survivor_freeze_hash(result))
    return result


def build_offline_train_survivor_freeze_manifest_payload(
    result: OfflineTrainSurvivorFreezeResult,
    *,
    train_evaluation_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "phase": "offline_train_survivor_freeze",
        "generated_at_utc": result.generated_at_utc,
        "git_sha": result.git_sha,
        "schema_version": FREEZE_SCHEMA_VERSION,
        "train_evaluation_manifest_path": train_evaluation_manifest_path,
        "data_corpus_hash": result.data_corpus_hash,
        "precommitment_hash": None,
        "window_index_hash": result.window_index_hash,
        "discovery_config_hash": result.discovery_config_hash,
        "plan_hash": result.plan_hash,
        "evaluation_hash": result.evaluation_hash,
        "survivor_freeze_config_hash": result.survivor_freeze_config_hash,
        "survivor_freeze_hash": result.survivor_freeze_hash,
        "status": result.status,
        "raw_train_screen_cell_count": len(result.raw_train_screen_cell_ids),
        "train_survivor_cell_count": len(result.train_survivor_cell_ids),
        "holdout_window_count_seen_but_not_evaluated": len(result.holdout_window_ids_seen_but_not_evaluated),
        "safety": "public_data_observer_only",
    }


def write_offline_train_survivor_freeze_outputs(
    result: OfflineTrainSurvivorFreezeResult,
    out_dir: Path,
    *,
    train_evaluation_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    run_dir = safe_output_dir(Path(out_dir), allow_existing=overwrite)
    result_payload = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "status": result.status,
        "train_survivor_cell_ids": result.train_survivor_cell_ids,
        "rejected_cell_ids": result.rejected_cell_ids,
        "rejection_reasons_by_cell": result.rejection_reasons_by_cell,
        "raw_train_screen_cell_ids": result.raw_train_screen_cell_ids,
        "holdout_window_ids_seen_but_not_evaluated": result.holdout_window_ids_seen_but_not_evaluated,
        "survivor_cells": [_survivor_cell_payload(cell) for cell in result.survivor_cells],
        "survivor_freeze_config_hash": result.survivor_freeze_config_hash,
        "survivor_freeze_hash": result.survivor_freeze_hash,
        "metadata": result.metadata,
        "data_corpus_hash": result.data_corpus_hash,
        "window_index_hash": result.window_index_hash,
        "discovery_config_hash": result.discovery_config_hash,
        "plan_hash": result.plan_hash,
        "evaluation_hash": result.evaluation_hash,
    }
    result_path = run_dir / "offline_train_survivor_freeze.json"
    atomic_write_json(result_path, result_payload)

    manifest_path = run_dir / "offline_train_survivor_freeze_manifest.json"
    atomic_write_json(
        manifest_path,
        build_offline_train_survivor_freeze_manifest_payload(
            result,
            train_evaluation_manifest_path=train_evaluation_manifest_path,
        ),
    )
    return {"result_path": result_path, "manifest_path": manifest_path}
