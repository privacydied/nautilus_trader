"""Phase 2B-2A fixed-window train-only forward-return evaluation scaffold.

Consumes frozen Phase 1/2A/2B-1 inputs and evaluates only train-window plan
cells. No holdout evaluation, no survivor freeze, no FDR, no null tests, no
cost sensitivity sweeps, no falsification, no shadow execution, no auth, no
live trading, and no network access.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Optional

from .family1_tick_basis import compute_family1_tick_signal
from .offline_discovery_plan import (
    CostConfig,
    DISCOVERY_SCHEMA_VERSION,
    OfflineDiscoveryPlan,
    OfflineDiscoveryPlanCell,
    compute_window_index_hash,
)
from .offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_AGG_TRADE,
    RESOLUTION_BAR,
    RESOLUTION_TRADE,
)

EVALUATION_SCHEMA_VERSION = "offline_train_evaluation_v1"

STATUS_OFFLINE_TRAIN_EVALUATION_READY = "OFFLINE_TRAIN_EVALUATION_READY"
STATUS_NO_TRAIN_WINDOWS = "NO_TRAIN_WINDOWS"
STATUS_NO_EVALUABLE_PLAN_CELLS = "NO_EVALUABLE_PLAN_CELLS"
STATUS_INSUFFICIENT_TRAIN_EVENTS = "INSUFFICIENT_TRAIN_EVENTS"
STATUS_UNSUPPORTED_PLAN_CELL = "UNSUPPORTED_PLAN_CELL"
STATUS_LATENCY_GATE_REQUIRED_NOT_RUN = "LATENCY_GATE_REQUIRED_NOT_RUN"
STATUS_TRAIN_RAW_EDGE_SCREEN_READY = "TRAIN_RAW_EDGE_SCREEN_READY"
STATUS_TRAIN_RAW_EDGE_SCREEN_EMPTY = "TRAIN_RAW_EDGE_SCREEN_EMPTY"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_WINDOW_INDEX_HASH_MISMATCH = "WINDOW_INDEX_HASH_MISMATCH"
STATUS_DISCOVERY_PLAN_HASH_MISMATCH = "DISCOVERY_PLAN_HASH_MISMATCH"
STATUS_UNUSABLE_DISCOVERY_PLAN = "UNUSABLE_DISCOVERY_PLAN"
STATUS_CONDITIONING_NOT_RUN_PHASE_2B2A = "CONDITIONING_NOT_RUN_PHASE_2B2A"


@dataclass(frozen=True)
class OfflineTrainEvaluationCellResult:
    cell_id: str
    family_id: str
    family_name: str
    is_edge_family: bool
    status: str
    train_window_ids: list[str]
    evaluated_event_count: int
    valid_event_count: int
    lookback_ms: int
    horizon_ms: int
    signal_variant: Optional[str]
    required_resolution: str
    latency_gate_required: bool
    raw_mean_bps: Optional[float]
    raw_median_bps: Optional[float]
    net_mean_bps: Optional[float]
    net_median_bps: Optional[float]
    win_rate: Optional[float]
    worst_net_bps: Optional[float]
    fee_bps: float
    slippage_bps: float
    quote_mismatch_buffer_bps: float
    exclusion_reasons: list[str]
    data_corpus_hash: str
    window_index_hash: str
    plan_hash: str


@dataclass(frozen=True)
class OfflineTrainEvaluationReport:
    status: str
    cell_results: list[OfflineTrainEvaluationCellResult]
    excluded_cells: list[dict[str, Any]]
    train_window_ids: list[str]
    holdout_window_ids_seen_but_not_evaluated: list[str]
    raw_train_screen_cell_ids: list[str]
    train_survivor_cell_ids: list[str]
    survivor_freeze_status: str
    evaluation_hash: str
    metadata: dict[str, Any]
    run_id: str
    generated_at_utc: str
    git_sha: str
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str


@dataclass(frozen=True)
class _PricePoint:
    timestamp_ns: int
    price: float


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_git_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _report_empty(
    *,
    status: str,
    train_window_ids: list[str],
    holdout_window_ids: list[str],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    metadata: dict[str, Any],
) -> OfflineTrainEvaluationReport:
    run_id = f"offline_train_evaluation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    generated_at_utc = _now_utc_iso()
    git_sha = _get_git_sha()
    report = OfflineTrainEvaluationReport(
        status=status,
        cell_results=[],
        excluded_cells=[],
        train_window_ids=list(train_window_ids),
        holdout_window_ids_seen_but_not_evaluated=list(holdout_window_ids),
        raw_train_screen_cell_ids=[],
        train_survivor_cell_ids=[],
        survivor_freeze_status="NOT_RUN_PHASE_2B2A",
        evaluation_hash="",
        metadata=metadata,
        run_id=run_id,
        generated_at_utc=generated_at_utc,
        git_sha=git_sha,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
    )
    object.__setattr__(report, "evaluation_hash", compute_evaluation_hash(report))
    return report


def _load_price_series(source_config_path: Path) -> dict[tuple[str, str], list[_PricePoint]]:
    payload = json.loads(source_config_path.read_text(encoding="utf-8"))
    series: dict[tuple[str, str], list[_PricePoint]] = {}
    for source in payload.get("sources", []):
        venue = str(source["venue"])
        symbol = str(source["symbol"])
        rows = json.loads(Path(source["path"]).read_text(encoding="utf-8"))
        points = sorted(
            [_PricePoint(timestamp_ns=int(row["timestamp_ns"]), price=float(row["close"])) for row in rows],
            key=lambda item: item.timestamp_ns,
        )
        series[(venue, symbol)] = points
    return series


def _find_price_at_or_after(points: list[_PricePoint], timestamp_ns: int) -> Optional[float]:
    for point in points:
        if point.timestamp_ns >= timestamp_ns:
            return point.price
    return None


def _window_lookup(stress_windows_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["window_id"]: item for item in stress_windows_payload.get("windows", [])}


def _cost_total_bps(cost_config: CostConfig) -> float:
    return float(cost_config.fees_bps + cost_config.slippage_bps + cost_config.quote_mismatch_bps)


def _returns_summary(raw_returns_bps: list[float], net_returns_bps: list[float]) -> dict[str, Optional[float]]:
    if not raw_returns_bps or not net_returns_bps:
        return {
            "raw_mean_bps": None,
            "raw_median_bps": None,
            "net_mean_bps": None,
            "net_median_bps": None,
            "win_rate": None,
            "worst_net_bps": None,
        }
    raw_mean = sum(raw_returns_bps) / len(raw_returns_bps)
    net_mean = sum(net_returns_bps) / len(net_returns_bps)
    return {
        "raw_mean_bps": raw_mean,
        "raw_median_bps": median(raw_returns_bps),
        "net_mean_bps": net_mean,
        "net_median_bps": median(net_returns_bps),
        "win_rate": sum(1 for value in net_returns_bps if value > 0.0) / len(net_returns_bps),
        "worst_net_bps": min(net_returns_bps),
    }


def _evaluate_family1(cell: OfflineDiscoveryPlanCell, train_window_ids: list[str], windows: dict[str, dict[str, Any]], series: dict[tuple[str, str], list[_PricePoint]], plan_hash: str) -> OfflineTrainEvaluationCellResult:
    # --- Resolution gate ---
    # BAR resolution is explicitly unsupported. The old tick-resolvable
    # evaluator was broken (ignored cell.lookback_ms) and has been
    # decommissioned. Only TRADE / AGG_TRADE may use the new tick-basis
    # signal. A separate precommitted bar evaluator would be needed for BAR.
    if cell.required_resolution == RESOLUTION_BAR:
        return _excluded_cell_result(
            cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL,
            ["bar_resolution_not_supported_for_family1_tick_basis"],
        )
    if cell.required_resolution not in (RESOLUTION_TRADE, RESOLUTION_AGG_TRADE):
        return _excluded_cell_result(
            cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL,
            [f"unsupported_resolution:{cell.required_resolution}"],
        )
    if cell.lookback_ms < 30_000:
        return _excluded_cell_result(
            cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL,
            [f"lookback_below_minimum_30s:{cell.lookback_ms}ms"],
        )
    if len(cell.source_symbols) < 2 or not cell.target_symbols:
        return _excluded_cell_result(
            cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL,
            ["unsupported_plan_shape"],
        )

    source_a = series.get((cell.source_venues[0], cell.source_symbols[0]))
    source_b = series.get((cell.source_venues[0], cell.source_symbols[1]))
    target = series.get((cell.target_venues[0], cell.target_symbols[0]))
    if not source_a or not source_b or not target:
        return _excluded_cell_result(
            cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL,
            ["missing_required_stream_coverage"],
        )

    # Use the precommitted shared tick-basis helper
    raw_returns, net_returns, evaluated, valid_count, exclusion_reasons = (
        compute_family1_tick_signal(
            source_a_prices=source_a,
            source_b_prices=source_b,
            target_prices=target,
            window_ids=train_window_ids,
            windows=windows,
            lookback_ms=cell.lookback_ms,
            horizon_ms=cell.horizon_ms,
            cost_total=_cost_total_bps(cell.cost_config),
        )
    )

    if valid_count < max(2, len(train_window_ids)):
        return _excluded_cell_result(
            cell, train_window_ids, plan_hash, STATUS_INSUFFICIENT_TRAIN_EVENTS,
            exclusion_reasons or ["insufficient_train_events"],
            evaluated_count=evaluated, valid_count=valid_count,
        )

    summary = _returns_summary(raw_returns, net_returns)
    return OfflineTrainEvaluationCellResult(
        cell_id=cell.cell_id,
        family_id=cell.family_id,
        family_name=cell.family_name,
        is_edge_family=cell.is_edge_family,
        status=STATUS_OFFLINE_TRAIN_EVALUATION_READY,
        train_window_ids=list(train_window_ids),
        evaluated_event_count=evaluated,
        valid_event_count=valid_count,
        lookback_ms=cell.lookback_ms,
        horizon_ms=cell.horizon_ms,
        signal_variant=cell.signal_variant,
        required_resolution=cell.required_resolution,
        latency_gate_required=cell.latency_gate_required,
        raw_mean_bps=summary["raw_mean_bps"],
        raw_median_bps=summary["raw_median_bps"],
        net_mean_bps=summary["net_mean_bps"],
        net_median_bps=summary["net_median_bps"],
        win_rate=summary["win_rate"],
        worst_net_bps=summary["worst_net_bps"],
        fee_bps=cell.cost_config.fees_bps,
        slippage_bps=cell.cost_config.slippage_bps,
        quote_mismatch_buffer_bps=cell.cost_config.quote_mismatch_bps,
        exclusion_reasons=exclusion_reasons,
        data_corpus_hash=cell.data_corpus_hash,
        window_index_hash=cell.window_index_hash,
        plan_hash=plan_hash,
    )


def _evaluate_family2(cell: OfflineDiscoveryPlanCell, train_window_ids: list[str], windows: dict[str, dict[str, Any]], series: dict[tuple[str, str], list[_PricePoint]], plan_hash: str) -> OfflineTrainEvaluationCellResult:
    if cell.required_resolution != RESOLUTION_BAR:
        return _excluded_cell_result(cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, [f"unsupported_resolution:{cell.required_resolution}"])
    if cell.signal_variant in {"signed_imbalance", "notional_burst"}:
        return _excluded_cell_result(cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, ["requires_trade_flow_data"])
    if cell.signal_variant != "source_move_impulse":
        return _excluded_cell_result(cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, ["unsupported_signal_variant"])
    if not cell.source_symbols or not cell.target_symbols:
        return _excluded_cell_result(cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, ["unsupported_plan_shape"])

    source = series.get((cell.source_venues[0], cell.source_symbols[0]))
    target = series.get((cell.target_venues[0], cell.target_symbols[0]))
    if not source or not target:
        return _excluded_cell_result(cell, train_window_ids, plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, ["missing_required_stream_coverage"])

    raw_returns: list[float] = []
    net_returns: list[float] = []
    evaluated = 0
    cost_total = _cost_total_bps(cell.cost_config)
    for window_id in train_window_ids:
        window = windows.get(window_id)
        if not window:
            continue
        trigger_ts = int(window["trigger_timestamp_ns"])
        source_entry = _find_price_at_or_after(source, trigger_ts)
        source_exit = _find_price_at_or_after(source, trigger_ts + (cell.lookback_ms * 1_000_000))
        target_entry = _find_price_at_or_after(target, trigger_ts)
        target_exit = _find_price_at_or_after(target, trigger_ts + (cell.horizon_ms * 1_000_000))
        evaluated += 1
        if None in (source_entry, source_exit, target_entry, target_exit):
            continue
        direction = 1.0 if source_exit >= source_entry else -1.0
        raw_bps = direction * ((target_exit - target_entry) / target_entry) * 10_000.0
        net_bps = raw_bps - cost_total
        raw_returns.append(raw_bps)
        net_returns.append(net_bps)

    if len(net_returns) < max(2, len(train_window_ids)):
        return _excluded_cell_result(cell, train_window_ids, plan_hash, STATUS_INSUFFICIENT_TRAIN_EVENTS, ["insufficient_train_events"], evaluated_count=evaluated, valid_count=len(net_returns))

    summary = _returns_summary(raw_returns, net_returns)
    return OfflineTrainEvaluationCellResult(
        cell_id=cell.cell_id,
        family_id=cell.family_id,
        family_name=cell.family_name,
        is_edge_family=cell.is_edge_family,
        status=STATUS_OFFLINE_TRAIN_EVALUATION_READY,
        train_window_ids=list(train_window_ids),
        evaluated_event_count=evaluated,
        valid_event_count=len(net_returns),
        lookback_ms=cell.lookback_ms,
        horizon_ms=cell.horizon_ms,
        signal_variant=cell.signal_variant,
        required_resolution=cell.required_resolution,
        latency_gate_required=cell.latency_gate_required,
        raw_mean_bps=summary["raw_mean_bps"],
        raw_median_bps=summary["raw_median_bps"],
        net_mean_bps=summary["net_mean_bps"],
        net_median_bps=summary["net_median_bps"],
        win_rate=summary["win_rate"],
        worst_net_bps=summary["worst_net_bps"],
        fee_bps=cell.cost_config.fees_bps,
        slippage_bps=cell.cost_config.slippage_bps,
        quote_mismatch_buffer_bps=cell.cost_config.quote_mismatch_bps,
        exclusion_reasons=[],
        data_corpus_hash=cell.data_corpus_hash,
        window_index_hash=cell.window_index_hash,
        plan_hash=plan_hash,
    )


def _excluded_cell_result(
    cell: OfflineDiscoveryPlanCell,
    train_window_ids: list[str],
    plan_hash: str,
    status: str,
    reasons: list[str],
    *,
    evaluated_count: int = 0,
    valid_count: int = 0,
) -> OfflineTrainEvaluationCellResult:
    return OfflineTrainEvaluationCellResult(
        cell_id=cell.cell_id,
        family_id=cell.family_id,
        family_name=cell.family_name,
        is_edge_family=cell.is_edge_family,
        status=status,
        train_window_ids=list(train_window_ids),
        evaluated_event_count=evaluated_count,
        valid_event_count=valid_count,
        lookback_ms=cell.lookback_ms,
        horizon_ms=cell.horizon_ms,
        signal_variant=cell.signal_variant,
        required_resolution=cell.required_resolution,
        latency_gate_required=cell.latency_gate_required,
        raw_mean_bps=None,
        raw_median_bps=None,
        net_mean_bps=None,
        net_median_bps=None,
        win_rate=None,
        worst_net_bps=None,
        fee_bps=cell.cost_config.fees_bps,
        slippage_bps=cell.cost_config.slippage_bps,
        quote_mismatch_buffer_bps=cell.cost_config.quote_mismatch_bps,
        exclusion_reasons=list(reasons),
        data_corpus_hash=cell.data_corpus_hash,
        window_index_hash=cell.window_index_hash,
        plan_hash=plan_hash,
    )


def _plan_payload_hashable_subset(discovery_plan_payload: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_version": discovery_plan_payload.get("schema_version"),
        "status": discovery_plan_payload.get("status"),
        "plan_cells": discovery_plan_payload.get("plan_cells", []),
        "excluded_windows": discovery_plan_payload.get("excluded_windows", []),
        "family_summary": discovery_plan_payload.get("family_summary", {}),
        "train_window_ids": discovery_plan_payload.get("train_window_ids", []),
        "holdout_window_ids": discovery_plan_payload.get("holdout_window_ids", []),
        "edge_family_cell_count": discovery_plan_payload.get("edge_family_cell_count", 0),
        "conditioning_cell_count": discovery_plan_payload.get("conditioning_cell_count", 0),
        "discovery_config_hash": discovery_plan_payload.get("discovery_config_hash"),
        "plan_hash": discovery_plan_payload.get("plan_hash"),
        "split_timestamp_boundary_ns": discovery_plan_payload.get("split_timestamp_boundary_ns"),
        "train_survivor_cell_ids": discovery_plan_payload.get("train_survivor_cell_ids", []),
        "holdout_evaluation_cell_ids": discovery_plan_payload.get("holdout_evaluation_cell_ids", []),
        "survivor_freeze_status": discovery_plan_payload.get("survivor_freeze_status"),
        "data_corpus_hash": discovery_plan_payload.get("data_corpus_hash"),
        "window_index_hash": discovery_plan_payload.get("window_index_hash"),
    }
    plan_hash = payload.get("plan_hash")
    if plan_hash:
        return plan_hash
    return payload


def build_offline_train_evaluation_report(
    *,
    prepare_manifest: Any,
    stress_window_manifest: dict[str, Any],
    stress_windows_payload: dict[str, Any],
    discovery_plan_manifest: dict[str, Any],
    discovery_plan_payload: dict[str, Any],
    discovery_plan: OfflineDiscoveryPlan,
    source_config_path: Path,
) -> OfflineTrainEvaluationReport:
    metadata = {
        "holdout_window_ids_seen_but_not_evaluated": list(discovery_plan.holdout_window_ids),
    }
    data_corpus_hash = getattr(prepare_manifest, "data_corpus_hash", None)
    stress_data_hash = stress_window_manifest.get("data_corpus_hash")
    plan_manifest_data_hash = discovery_plan_manifest.get("data_corpus_hash")
    plan_json_data_hash = discovery_plan_payload.get("data_corpus_hash")
    if len({data_corpus_hash, stress_data_hash, plan_manifest_data_hash, plan_json_data_hash}) != 1:
        return _report_empty(
            status=STATUS_INPUT_HASH_MISMATCH,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=str(data_corpus_hash or ""),
            window_index_hash=str(stress_window_manifest.get("window_index_hash") or ""),
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )

    prepare_precommitment_hash = getattr(prepare_manifest, "precommitment_hash", None)
    stress_precommitment_hash = stress_window_manifest.get("precommitment_hash")
    plan_precommitment_hash = discovery_plan_manifest.get("precommitment_hash")
    non_null_precommitments = {value for value in [prepare_precommitment_hash, stress_precommitment_hash, plan_precommitment_hash] if value is not None}
    if len(non_null_precommitments) > 1:
        return _report_empty(
            status=STATUS_INPUT_HASH_MISMATCH,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=data_corpus_hash,
            window_index_hash=str(stress_window_manifest.get("window_index_hash") or ""),
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )

    computed_window_index_hash = compute_window_index_hash(stress_windows_payload)
    if computed_window_index_hash != stress_window_manifest.get("window_index_hash"):
        return _report_empty(
            status=STATUS_WINDOW_INDEX_HASH_MISMATCH,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=data_corpus_hash,
            window_index_hash=computed_window_index_hash,
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )
    if discovery_plan_manifest.get("window_index_hash") != stress_window_manifest.get("window_index_hash"):
        return _report_empty(
            status=STATUS_WINDOW_INDEX_HASH_MISMATCH,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=data_corpus_hash,
            window_index_hash=computed_window_index_hash,
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )

    computed_plan_hash = _plan_payload_hashable_subset(discovery_plan_payload)
    if discovery_plan_manifest.get("plan_hash") != computed_plan_hash:
        return _report_empty(
            status=STATUS_DISCOVERY_PLAN_HASH_MISMATCH,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=data_corpus_hash,
            window_index_hash=computed_window_index_hash,
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )
    if discovery_plan_manifest.get("discovery_config_hash") != discovery_plan_payload.get("discovery_config_hash"):
        return _report_empty(
            status=STATUS_UNUSABLE_DISCOVERY_PLAN,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=data_corpus_hash,
            window_index_hash=computed_window_index_hash,
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )

    if discovery_plan_manifest.get("status") not in {
        "OFFLINE_DISCOVERY_PLAN_READY",
        "NO_PROMOTABLE_WINDOWS",
    }:
        return _report_empty(
            status=STATUS_UNUSABLE_DISCOVERY_PLAN,
            train_window_ids=list(discovery_plan.train_window_ids),
            holdout_window_ids=list(discovery_plan.holdout_window_ids),
            data_corpus_hash=data_corpus_hash,
            window_index_hash=computed_window_index_hash,
            discovery_config_hash=str(discovery_plan.discovery_config_hash),
            plan_hash=str(discovery_plan.plan_hash),
            metadata=metadata,
        )

    train_window_ids = list(discovery_plan.train_window_ids)
    holdout_window_ids = list(discovery_plan.holdout_window_ids)
    if not train_window_ids:
        return _report_empty(
            status=STATUS_NO_TRAIN_WINDOWS,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            data_corpus_hash=data_corpus_hash,
            window_index_hash=computed_window_index_hash,
            discovery_config_hash=discovery_plan.discovery_config_hash,
            plan_hash=discovery_plan.plan_hash,
            metadata=metadata,
        )

    price_series = _load_price_series(source_config_path)
    window_by_id = _window_lookup(stress_windows_payload)
    cell_results: list[OfflineTrainEvaluationCellResult] = []
    excluded_cells: list[dict[str, Any]] = []

    for cell in discovery_plan.plan_cells:
        cell_train_window_ids = [window_id for window_id in cell.window_ids if window_id in train_window_ids]
        if not cell_train_window_ids:
            result = _excluded_cell_result(cell, [], discovery_plan.plan_hash, STATUS_NO_TRAIN_WINDOWS, ["no_train_windows"])
        elif cell.is_conditioning_family or cell.family_id == "family_4_stablecoin_quote_regime_conditioning":
            result = _excluded_cell_result(cell, cell_train_window_ids, discovery_plan.plan_hash, STATUS_CONDITIONING_NOT_RUN_PHASE_2B2A, ["conditioning_not_run_phase_2b2a"])
        elif not cell.promotion_allowed:
            result = _excluded_cell_result(cell, cell_train_window_ids, discovery_plan.plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, ["cell_marked_non_promotable"])
        elif cell.latency_gate_required:
            result = _excluded_cell_result(cell, cell_train_window_ids, discovery_plan.plan_hash, STATUS_LATENCY_GATE_REQUIRED_NOT_RUN, ["latency_gate_required_not_run"])
        elif cell.family_id == "family_1_same_venue_quote_basis":
            result = _evaluate_family1(cell, cell_train_window_ids, window_by_id, price_series, discovery_plan.plan_hash)
        elif cell.family_id == "family_2_cross_asset_stress_beta_lag":
            result = _evaluate_family2(cell, cell_train_window_ids, window_by_id, price_series, discovery_plan.plan_hash)
        else:
            result = _excluded_cell_result(cell, cell_train_window_ids, discovery_plan.plan_hash, STATUS_UNSUPPORTED_PLAN_CELL, ["unsupported_plan_family"])
        cell_results.append(result)
        if result.status != STATUS_OFFLINE_TRAIN_EVALUATION_READY:
            excluded_cells.append({"cell_id": result.cell_id, "reasons": list(result.exclusion_reasons), "status": result.status})

    raw_train_screen_cell_ids = sorted(
        [
            result.cell_id
            for result in cell_results
            if result.status == STATUS_OFFLINE_TRAIN_EVALUATION_READY
            and result.net_mean_bps is not None
            and result.net_mean_bps > 0.0
            and result.valid_event_count >= 2
        ]
    )

    evaluated_ready = [result for result in cell_results if result.status == STATUS_OFFLINE_TRAIN_EVALUATION_READY]
    if not cell_results:
        status = STATUS_NO_EVALUABLE_PLAN_CELLS
    elif raw_train_screen_cell_ids:
        status = STATUS_TRAIN_RAW_EDGE_SCREEN_READY
    elif evaluated_ready:
        status = STATUS_OFFLINE_TRAIN_EVALUATION_READY
    else:
        status = STATUS_TRAIN_RAW_EDGE_SCREEN_EMPTY

    report = OfflineTrainEvaluationReport(
        status=status,
        cell_results=cell_results,
        excluded_cells=excluded_cells,
        train_window_ids=train_window_ids,
        holdout_window_ids_seen_but_not_evaluated=holdout_window_ids,
        raw_train_screen_cell_ids=raw_train_screen_cell_ids,
        train_survivor_cell_ids=[],
        survivor_freeze_status="NOT_RUN_PHASE_2B2A",
        evaluation_hash="",
        metadata=metadata,
        run_id=f"offline_train_evaluation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        generated_at_utc=_now_utc_iso(),
        git_sha=_get_git_sha(),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=computed_window_index_hash,
        discovery_config_hash=discovery_plan.discovery_config_hash,
        plan_hash=discovery_plan.plan_hash,
    )
    object.__setattr__(report, "evaluation_hash", compute_evaluation_hash(report))
    return report


def _cell_result_payload(result: OfflineTrainEvaluationCellResult) -> dict[str, Any]:
    return asdict(result)


def compute_evaluation_hash(report: OfflineTrainEvaluationReport) -> str:
    return _sha256_json(
        {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "data_corpus_hash": report.data_corpus_hash,
            "window_index_hash": report.window_index_hash,
            "discovery_config_hash": report.discovery_config_hash,
            "plan_hash": report.plan_hash,
            "train_window_ids": report.train_window_ids,
            "evaluated_cell_ids": [result.cell_id for result in report.cell_results],
            "cell_result_summaries": [_cell_result_payload(result) for result in report.cell_results],
            "excluded_cells": report.excluded_cells,
            "raw_train_screen_cell_ids": report.raw_train_screen_cell_ids,
            "survivor_freeze_status": report.survivor_freeze_status,
        }
    )


def build_offline_train_evaluation_manifest_payload(
    report: OfflineTrainEvaluationReport,
    *,
    prepare_manifest_path: str,
    stress_window_manifest_path: str,
    discovery_plan_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "phase": "offline_train_evaluation",
        "generated_at_utc": report.generated_at_utc,
        "git_sha": report.git_sha,
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "prepare_manifest_path": prepare_manifest_path,
        "stress_window_manifest_path": stress_window_manifest_path,
        "discovery_plan_manifest_path": discovery_plan_manifest_path,
        "data_corpus_hash": report.data_corpus_hash,
        "precommitment_hash": None,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "status": report.status,
        "evaluated_cell_count": len(report.cell_results),
        "excluded_cell_count": len(report.excluded_cells),
        "raw_train_screen_cell_count": len(report.raw_train_screen_cell_ids),
        "train_survivor_cell_count": 0,
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "safety": "public_data_observer_only",
    }


def write_offline_train_evaluation_outputs(
    report: OfflineTrainEvaluationReport,
    out_dir: Path,
    *,
    prepare_manifest_path: str,
    stress_window_manifest_path: str,
    discovery_plan_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    run_dir = Path(out_dir)
    if run_dir.exists() and not overwrite:
        raise FileExistsError(
            f"Output directory {run_dir} already exists. Pass overwrite=True or --overwrite to force."
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    report_payload = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "status": report.status,
        "cell_results": [_cell_result_payload(result) for result in report.cell_results],
        "excluded_cells": report.excluded_cells,
        "train_window_ids": report.train_window_ids,
        "holdout_window_ids_seen_but_not_evaluated": report.holdout_window_ids_seen_but_not_evaluated,
        "raw_train_screen_cell_ids": report.raw_train_screen_cell_ids,
        "train_survivor_cell_ids": report.train_survivor_cell_ids,
        "survivor_freeze_status": report.survivor_freeze_status,
        "evaluation_hash": report.evaluation_hash,
        "metadata": report.metadata,
        "data_corpus_hash": report.data_corpus_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
    }
    report_path = run_dir / "offline_train_evaluation.json"
    report_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True), encoding="utf-8")

    manifest_path = run_dir / "offline_train_evaluation_manifest.json"
    manifest_path.write_text(
        json.dumps(
            build_offline_train_evaluation_manifest_payload(
                report,
                prepare_manifest_path=prepare_manifest_path,
                stress_window_manifest_path=stress_window_manifest_path,
                discovery_plan_manifest_path=discovery_plan_manifest_path,
            ),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {"report_path": report_path, "manifest_path": manifest_path}
