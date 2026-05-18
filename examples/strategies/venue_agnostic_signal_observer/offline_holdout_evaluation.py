"""Phase 2B-2B2 holdout-only evaluation for frozen train survivors.

Consumes Phase 1/2A/2B-1/2B-2A/2B-2B1 artifacts and evaluates only frozen
train survivors on holdout windows. No survivor mutation, no train evaluation,
no FDR, no null tests, no cost sensitivity, no candidate falsification, no
conditioning execution, no latency diagnostics, and no final verdicting.
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
from .offline_corpus_hash import compute_data_corpus_hash
from .offline_discovery_plan import CostConfig, OfflineDiscoveryPlan, OfflineDiscoveryPlanCell, compute_window_index_hash
from .offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_AGG_TRADE,
    RESOLUTION_BAR,
    RESOLUTION_TRADE,
    OfflinePrepareManifest,
    OfflineSourceFile,
)
from .run_artifacts import atomic_write_json, safe_output_dir

HOLDOUT_EVALUATION_SCHEMA_VERSION = "offline_holdout_evaluation_v1"

STATUS_OFFLINE_HOLDOUT_EVALUATION_READY = "OFFLINE_HOLDOUT_EVALUATION_READY"
STATUS_NO_TRAIN_SURVIVORS = "NO_TRAIN_SURVIVORS"
STATUS_NO_HOLDOUT_WINDOWS = "NO_HOLDOUT_WINDOWS"
STATUS_NO_EVALUABLE_SURVIVORS = "NO_EVALUABLE_SURVIVORS"
STATUS_INSUFFICIENT_HOLDOUT_EVENTS = "INSUFFICIENT_HOLDOUT_EVENTS"
STATUS_LATENCY_GATE_REQUIRED_NOT_RUN = "LATENCY_GATE_REQUIRED_NOT_RUN"
STATUS_UNSUPPORTED_SURVIVOR_CELL = "UNSUPPORTED_SURVIVOR_CELL"
STATUS_DATA_CORPUS_HASH_MISMATCH = "DATA_CORPUS_HASH_MISMATCH"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_WINDOW_INDEX_HASH_MISMATCH = "WINDOW_INDEX_HASH_MISMATCH"
STATUS_DISCOVERY_PLAN_HASH_MISMATCH = "DISCOVERY_PLAN_HASH_MISMATCH"
STATUS_TRAIN_EVALUATION_HASH_MISMATCH = "TRAIN_EVALUATION_HASH_MISMATCH"
STATUS_SURVIVOR_FREEZE_HASH_MISMATCH = "SURVIVOR_FREEZE_HASH_MISMATCH"
STATUS_UNUSABLE_SURVIVOR_FREEZE = "UNUSABLE_SURVIVOR_FREEZE"
STATUS_CONDITIONING_NOT_RUN_PHASE_2B2B2 = "CONDITIONING_NOT_RUN_PHASE_2B2B2"

USABLE_SURVIVOR_FREEZE_STATUSES = {"OFFLINE_TRAIN_SURVIVOR_FREEZE_READY"}


@dataclass(frozen=True)
class OfflineHoldoutEvaluationCellResult:
    cell_id: str
    family_id: str
    family_name: str
    status: str
    holdout_window_ids: list[str]
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
    event_raw_bps: list[float]
    event_net_bps: list[float]
    data_corpus_hash: str
    window_index_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_hash: str


@dataclass(frozen=True)
class OfflineHoldoutEvaluationReport:
    status: str
    cell_results: list[OfflineHoldoutEvaluationCellResult]
    excluded_survivor_cells: dict[str, list[str]]
    train_survivor_cell_ids: list[str]
    holdout_evaluated_cell_ids: list[str]
    train_window_ids_seen_but_not_evaluated: list[str]
    holdout_window_ids: list[str]
    holdout_evaluation_hash: str
    metadata: dict[str, Any]
    run_id: str
    generated_at_utc: str
    git_sha: str
    data_corpus_hash: str
    precommitment_hash: str | None
    window_index_hash: str
    discovery_config_hash: str
    plan_hash: str
    evaluation_hash: str
    survivor_freeze_config_hash: str
    survivor_freeze_hash: str


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
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _sorted_unique_strings(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values})


def _manifest_sources(prepare_manifest: OfflinePrepareManifest) -> list[OfflineSourceFile]:
    return [OfflineSourceFile(**payload) for payload in prepare_manifest.source_files]


def _source_file_by_key(prepare_manifest: OfflinePrepareManifest) -> dict[tuple[str, str], OfflineSourceFile]:
    return {
        (source.venue, source.symbol): source
        for source in _manifest_sources(prepare_manifest)
    }


def _compute_source_config_corpus_hash(source_config_path: Path, *, prepare_manifest: OfflinePrepareManifest) -> str:
    payload = json.loads(source_config_path.read_text(encoding="utf-8"))
    manifest_sources = _source_file_by_key(prepare_manifest)
    sources: list[OfflineSourceFile] = []
    for source in payload.get("sources", []):
        key = (str(source["venue"]), str(source["symbol"]))
        manifest_source = manifest_sources.get(key)
        if manifest_source is None:
            raise KeyError(f"Unknown prepared source for {key!r}")
        path = Path(source["path"])
        rows_payload = json.loads(path.read_text(encoding="utf-8"))
        rows = rows_payload if isinstance(rows_payload, list) else rows_payload.get("rows", rows_payload)
        timestamps = [int(row["timestamp_ns"]) for row in rows]
        source_file = OfflineSourceFile(
            path=manifest_source.path,
            logical_source_id=manifest_source.logical_source_id,
            venue=manifest_source.venue,
            symbol=manifest_source.symbol,
            base_asset=manifest_source.base_asset,
            quote_asset=manifest_source.quote_asset,
            source_kind=manifest_source.source_kind,
            stream_type=manifest_source.stream_type,
            resolution_type=manifest_source.resolution_type,
            timestamp_unit=manifest_source.timestamp_unit,
            expected_start_ns=manifest_source.expected_start_ns,
            expected_end_ns=manifest_source.expected_end_ns,
            file_size_bytes=manifest_source.file_size_bytes,
            mtime_ns=manifest_source.mtime_ns,
            file_sha256=manifest_source.file_sha256,
            row_count=len(rows),
            data_start_ns=min(timestamps) if timestamps else 0,
            data_end_ns=max(timestamps) if timestamps else 0,
        )
        sources.append(source_file)
    return compute_data_corpus_hash(sources, OFFLINE_DATA_SCHEMA_VERSION)


def _load_price_series(source_config_path: Path) -> dict[tuple[str, str], list[_PricePoint]]:
    payload = json.loads(source_config_path.read_text(encoding="utf-8"))
    series: dict[tuple[str, str], list[_PricePoint]] = {}
    for source in payload.get("sources", []):
        venue = str(source["venue"])
        symbol = str(source["symbol"])
        rows_payload = json.loads(Path(source["path"]).read_text(encoding="utf-8"))
        rows = rows_payload if isinstance(rows_payload, list) else rows_payload.get("rows", rows_payload)
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
    return {str(item["window_id"]): item for item in stress_windows_payload.get("windows", [])}


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
    return {
        "raw_mean_bps": sum(raw_returns_bps) / len(raw_returns_bps),
        "raw_median_bps": median(raw_returns_bps),
        "net_mean_bps": sum(net_returns_bps) / len(net_returns_bps),
        "net_median_bps": median(net_returns_bps),
        "win_rate": sum(1 for value in net_returns_bps if value > 0.0) / len(net_returns_bps),
        "worst_net_bps": min(net_returns_bps),
    }


def _summarize_event_returns(raw_bps: list[float], net_bps: list[float]) -> dict[str, Optional[float]]:
    """Single shared helper for deriving summary metrics from event return vectors.

    This is the only path to summary metrics for a holdout evaluation cell.
    The event vectors are the single source of truth. A fake event vector
    is structurally impossible because all summaries are derived from it.
    """
    return _returns_summary(raw_bps, net_bps)


def _empty_report(
    *,
    status: str,
    train_survivor_cell_ids: list[str],
    train_window_ids: list[str],
    holdout_window_ids: list[str],
    metadata: dict[str, Any],
    data_corpus_hash: str,
    precommitment_hash: str | None,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    survivor_freeze_config_hash: str,
    survivor_freeze_hash: str,
    excluded_survivor_cells: dict[str, list[str]] | None = None,
) -> OfflineHoldoutEvaluationReport:
    report = OfflineHoldoutEvaluationReport(
        status=status,
        cell_results=[],
        excluded_survivor_cells=excluded_survivor_cells or {},
        train_survivor_cell_ids=list(train_survivor_cell_ids),
        holdout_evaluated_cell_ids=[],
        train_window_ids_seen_but_not_evaluated=list(train_window_ids),
        holdout_window_ids=list(holdout_window_ids),
        holdout_evaluation_hash="",
        metadata=metadata,
        run_id=f"offline_holdout_evaluation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        generated_at_utc=_now_utc_iso(),
        git_sha=_get_git_sha(),
        data_corpus_hash=data_corpus_hash,
        precommitment_hash=precommitment_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_config_hash=survivor_freeze_config_hash,
        survivor_freeze_hash=survivor_freeze_hash,
    )
    object.__setattr__(report, "holdout_evaluation_hash", compute_holdout_evaluation_hash(report))
    return report


def _discovery_plan_from_payload(discovery_plan_payload: dict[str, Any]) -> OfflineDiscoveryPlan:
    cells = [
        OfflineDiscoveryPlanCell(
            **{
                **cell,
                "cost_config": CostConfig(**cell["cost_config"]),
            }
        )
        for cell in discovery_plan_payload.get("plan_cells", [])
    ]
    return OfflineDiscoveryPlan(
        status=str(discovery_plan_payload["status"]),
        plan_cells=cells,
        excluded_windows=list(discovery_plan_payload.get("excluded_windows", [])),
        family_summary=dict(discovery_plan_payload.get("family_summary", {})),
        train_window_ids=list(discovery_plan_payload.get("train_window_ids", [])),
        holdout_window_ids=list(discovery_plan_payload.get("holdout_window_ids", [])),
        edge_family_cell_count=int(discovery_plan_payload.get("edge_family_cell_count", 0)),
        conditioning_cell_count=int(discovery_plan_payload.get("conditioning_cell_count", 0)),
        discovery_config_hash=str(discovery_plan_payload.get("discovery_config_hash", "")),
        plan_hash=str(discovery_plan_payload.get("plan_hash", "")),
        data_corpus_hash=str(discovery_plan_payload.get("data_corpus_hash", "")),
        window_index_hash=str(discovery_plan_payload.get("window_index_hash", "")),
        split_timestamp_boundary_ns=int(discovery_plan_payload.get("split_timestamp_boundary_ns", 0)),
        train_survivor_cell_ids=list(discovery_plan_payload.get("train_survivor_cell_ids", [])),
        holdout_evaluation_cell_ids=list(discovery_plan_payload.get("holdout_evaluation_cell_ids", [])),
        survivor_freeze_status=str(discovery_plan_payload.get("survivor_freeze_status", "")),
        run_id="discovery_run",
        generated_at_utc="",
        git_sha="",
        stress_rule_config_hash="",
        precommitment_hash=discovery_plan_payload.get("precommitment_hash"),
    )


def _excluded_cell_result(
    cell: OfflineDiscoveryPlanCell,
    holdout_window_ids: list[str],
    evaluation_hash: str,
    survivor_freeze_hash: str,
    status: str,
    reasons: list[str],
    *,
    evaluated_count: int = 0,
    valid_count: int = 0,
) -> OfflineHoldoutEvaluationCellResult:
    return OfflineHoldoutEvaluationCellResult(
        cell_id=cell.cell_id,
        family_id=cell.family_id,
        family_name=cell.family_name,
        status=status,
        holdout_window_ids=list(holdout_window_ids),
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
        event_raw_bps=[],
        event_net_bps=[],
        data_corpus_hash=cell.data_corpus_hash,
        window_index_hash=cell.window_index_hash,
        plan_hash=cell.discovery_config_hash if False else cell.window_index_hash and cell.discovery_config_hash and cell.window_index_hash and cell.discovery_config_hash and cell.window_index_hash and cell.discovery_config_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
    )


def _make_excluded_cell_result(
    cell: OfflineDiscoveryPlanCell,
    holdout_window_ids: list[str],
    evaluation_hash: str,
    survivor_freeze_hash: str,
    plan_hash: str,
    status: str,
    reasons: list[str],
    *,
    evaluated_count: int = 0,
    valid_count: int = 0,
) -> OfflineHoldoutEvaluationCellResult:
    result = _excluded_cell_result(
        cell,
        holdout_window_ids,
        evaluation_hash,
        survivor_freeze_hash,
        status,
        reasons,
        evaluated_count=evaluated_count,
        valid_count=valid_count,
    )
    object.__setattr__(result, "plan_hash", plan_hash)
    return result


def _evaluate_family1(
    cell: OfflineDiscoveryPlanCell,
    holdout_window_ids: list[str],
    windows: dict[str, dict[str, Any]],
    series: dict[tuple[str, str], list[_PricePoint]],
    evaluation_hash: str,
    survivor_freeze_hash: str,
    plan_hash: str,
) -> OfflineHoldoutEvaluationCellResult:
    # --- Resolution gate ---
    # BAR resolution is explicitly unsupported. The old tick-resolvable
    # evaluator was broken (ignored cell.lookback_ms) and has been
    # decommissioned. Only TRADE / AGG_TRADE may use the new tick-basis
    # signal.
    if cell.required_resolution == RESOLUTION_BAR:
        return _make_excluded_cell_result(
            cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash,
            plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL,
            ["bar_resolution_not_supported_for_family1_tick_basis"],
        )
    if cell.required_resolution not in (RESOLUTION_TRADE, RESOLUTION_AGG_TRADE):
        return _make_excluded_cell_result(
            cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash,
            plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL,
            [f"unsupported_resolution:{cell.required_resolution}"],
        )
    if cell.lookback_ms < 30_000:
        return _make_excluded_cell_result(
            cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash,
            plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL,
            [f"lookback_below_minimum_30s:{cell.lookback_ms}ms"],
        )
    if len(cell.source_symbols) < 2 or not cell.target_symbols:
        return _make_excluded_cell_result(
            cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash,
            plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL,
            ["unsupported_plan_shape"],
        )

    source_a = series.get((cell.source_venues[0], cell.source_symbols[0]))
    source_b = series.get((cell.source_venues[0], cell.source_symbols[1]))
    source_b = series.get((cell.source_venues[0], cell.source_symbols[1]))
    target = series.get((cell.target_venues[0], cell.target_symbols[0]))
    if not source_a or not source_b or not target:
        return _make_excluded_cell_result(
            cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash,
            plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL,
            ["missing_required_stream_coverage"],
        )

    # Use the precommitted shared tick-basis helper
    raw_returns, net_returns, evaluated, valid_count, exclusion_reasons = (
        compute_family1_tick_signal(
            source_a_prices=source_a,
            source_b_prices=source_b,
            target_prices=target,
            window_ids=holdout_window_ids,
            windows=windows,
            lookback_ms=cell.lookback_ms,
            horizon_ms=cell.horizon_ms,
            cost_total=_cost_total_bps(cell.cost_config),
        )
    )

    if valid_count < max(1, len(holdout_window_ids)):
        return _make_excluded_cell_result(
            cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash,
            plan_hash, STATUS_INSUFFICIENT_HOLDOUT_EVENTS,
            exclusion_reasons or [STATUS_INSUFFICIENT_HOLDOUT_EVENTS],
            evaluated_count=evaluated, valid_count=valid_count,
        )

    summary = _summarize_event_returns(raw_returns, net_returns)
    return OfflineHoldoutEvaluationCellResult(
        cell_id=cell.cell_id,
        family_id=cell.family_id,
        family_name=cell.family_name,
        status=STATUS_OFFLINE_HOLDOUT_EVALUATION_READY,
        holdout_window_ids=list(holdout_window_ids),
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
        event_raw_bps=raw_returns,
        event_net_bps=net_returns,
        data_corpus_hash=cell.data_corpus_hash,
        window_index_hash=cell.window_index_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
    )


def _evaluate_family2(
    cell: OfflineDiscoveryPlanCell,
    holdout_window_ids: list[str],
    windows: dict[str, dict[str, Any]],
    series: dict[tuple[str, str], list[_PricePoint]],
    evaluation_hash: str,
    survivor_freeze_hash: str,
    plan_hash: str,
) -> OfflineHoldoutEvaluationCellResult:
    if cell.required_resolution != RESOLUTION_BAR:
        return _make_excluded_cell_result(cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash, plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL, [f"unsupported_resolution:{cell.required_resolution}"])
    if cell.signal_variant in {"signed_imbalance", "notional_burst"}:
        return _make_excluded_cell_result(cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash, plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL, ["requires_trade_flow_data"])
    if cell.signal_variant != "source_move_impulse":
        return _make_excluded_cell_result(cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash, plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL, ["unsupported_signal_variant"])
    if not cell.source_symbols or not cell.target_symbols:
        return _make_excluded_cell_result(cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash, plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL, ["unsupported_plan_shape"])

    source = series.get((cell.source_venues[0], cell.source_symbols[0]))
    target = series.get((cell.target_venues[0], cell.target_symbols[0]))
    if not source or not target:
        return _make_excluded_cell_result(cell, holdout_window_ids, evaluation_hash, survivor_freeze_hash, plan_hash, STATUS_UNSUPPORTED_SURVIVOR_CELL, ["missing_required_stream_coverage"])

    raw_returns: list[float] = []
    net_returns: list[float] = []
    evaluated = 0
    cost_total = _cost_total_bps(cell.cost_config)
    for window_id in holdout_window_ids:
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

    if len(net_returns) < max(1, len(holdout_window_ids)):
        return _make_excluded_cell_result(
            cell,
            holdout_window_ids,
            evaluation_hash,
            survivor_freeze_hash,
            plan_hash,
            STATUS_INSUFFICIENT_HOLDOUT_EVENTS,
            [STATUS_INSUFFICIENT_HOLDOUT_EVENTS],
            evaluated_count=evaluated,
            valid_count=len(net_returns),
        )

    summary = _summarize_event_returns(raw_returns, net_returns)
    return OfflineHoldoutEvaluationCellResult(
        cell_id=cell.cell_id,
        family_id=cell.family_id,
        family_name=cell.family_name,
        status=STATUS_OFFLINE_HOLDOUT_EVALUATION_READY,
        holdout_window_ids=list(holdout_window_ids),
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
        event_raw_bps=raw_returns,
        event_net_bps=net_returns,
        data_corpus_hash=cell.data_corpus_hash,
        window_index_hash=cell.window_index_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_hash=survivor_freeze_hash,
    )


def compute_holdout_evaluation_hash(report: OfflineHoldoutEvaluationReport) -> str:
    return _sha256_json(
        {
            "schema_version": HOLDOUT_EVALUATION_SCHEMA_VERSION,
            "data_corpus_hash": report.data_corpus_hash,
            "window_index_hash": report.window_index_hash,
            "discovery_config_hash": report.discovery_config_hash,
            "plan_hash": report.plan_hash,
            "evaluation_hash": report.evaluation_hash,
            "survivor_freeze_config_hash": report.survivor_freeze_config_hash,
            "survivor_freeze_hash": report.survivor_freeze_hash,
            "train_survivor_cell_ids": report.train_survivor_cell_ids,
            "holdout_window_ids": report.holdout_window_ids,
            "cell_results": [asdict(cell) for cell in report.cell_results],
            "excluded_survivor_cells": report.excluded_survivor_cells,
            "train_window_ids_seen_but_not_evaluated": report.train_window_ids_seen_but_not_evaluated,
            "status": report.status,
        }
    )


def build_offline_holdout_evaluation_report(
    *,
    prepare_manifest: OfflinePrepareManifest,
    stress_window_manifest: dict[str, Any],
    stress_windows_payload: dict[str, Any],
    discovery_plan_manifest: dict[str, Any],
    discovery_plan_payload: dict[str, Any],
    train_evaluation_manifest: dict[str, Any],
    train_evaluation_payload: dict[str, Any],
    train_survivor_freeze_manifest: dict[str, Any],
    train_survivor_freeze_payload: dict[str, Any],
    source_config_path: Path,
) -> OfflineHoldoutEvaluationReport:
    data_corpus_hash = str(prepare_manifest.data_corpus_hash)
    precommitment_hash = prepare_manifest.precommitment_hash
    window_index_hash = str(stress_window_manifest.get("window_index_hash", ""))
    discovery_config_hash = str(discovery_plan_manifest.get("discovery_config_hash", discovery_plan_payload.get("discovery_config_hash", "")))
    plan_hash = str(discovery_plan_manifest.get("plan_hash", discovery_plan_payload.get("plan_hash", "")))
    evaluation_hash = str(train_evaluation_payload.get("evaluation_hash", ""))
    survivor_freeze_config_hash = str(train_survivor_freeze_payload.get("survivor_freeze_config_hash", ""))
    survivor_freeze_hash = str(train_survivor_freeze_payload.get("survivor_freeze_hash", ""))
    train_window_ids = _sorted_unique_strings(list(train_evaluation_payload.get("train_window_ids", [])))
    holdout_window_ids = _sorted_unique_strings(list(train_survivor_freeze_payload.get("holdout_window_ids_seen_but_not_evaluated", [])))
    train_survivor_cell_ids = _sorted_unique_strings(list(train_survivor_freeze_payload.get("train_survivor_cell_ids", [])))
    metadata = {
        "train_window_ids_seen_but_not_evaluated": train_window_ids,
        "holdout_window_ids": holdout_window_ids,
    }

    lineage_hashes = {
        str(prepare_manifest.data_corpus_hash),
        str(stress_window_manifest.get("data_corpus_hash", "")),
        str(discovery_plan_manifest.get("data_corpus_hash", "")),
        str(train_evaluation_manifest.get("data_corpus_hash", "")),
        str(train_survivor_freeze_manifest.get("data_corpus_hash", "")),
    }
    if len(lineage_hashes) != 1:
        return _empty_report(
            status=STATUS_INPUT_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    computed_window_index_hash = compute_window_index_hash(stress_windows_payload)
    if str(stress_window_manifest.get("window_index_hash", "")) != computed_window_index_hash:
        return _empty_report(
            status=STATUS_WINDOW_INDEX_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    plan_hash_payload = str(discovery_plan_payload.get("plan_hash", ""))
    if str(discovery_plan_manifest.get("plan_hash", "")) != plan_hash_payload:
        return _empty_report(
            status=STATUS_DISCOVERY_PLAN_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    if str(train_evaluation_manifest.get("evaluation_hash", "")) != evaluation_hash:
        return _empty_report(
            status=STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    if str(train_survivor_freeze_manifest.get("survivor_freeze_hash", "")) != survivor_freeze_hash:
        return _empty_report(
            status=STATUS_SURVIVOR_FREEZE_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    if str(train_survivor_freeze_manifest.get("evaluation_hash", "")) != str(train_evaluation_manifest.get("evaluation_hash", "")):
        return _empty_report(
            status=STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    if str(train_survivor_freeze_manifest.get("plan_hash", "")) != str(discovery_plan_manifest.get("plan_hash", "")):
        return _empty_report(
            status=STATUS_DISCOVERY_PLAN_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    precommitment_candidates = {
        prepare_manifest.precommitment_hash,
        stress_window_manifest.get("precommitment_hash"),
        discovery_plan_payload.get("precommitment_hash"),
        train_evaluation_manifest.get("precommitment_hash"),
        train_survivor_freeze_manifest.get("precommitment_hash"),
    }
    non_null_precommitment = {value for value in precommitment_candidates if value is not None}
    if len(non_null_precommitment) > 1:
        return _empty_report(
            status=STATUS_INPUT_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    freeze_status = str(train_survivor_freeze_payload.get("status", ""))
    if freeze_status not in USABLE_SURVIVOR_FREEZE_STATUSES:
        return _empty_report(
            status=STATUS_UNUSABLE_SURVIVOR_FREEZE,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    if not train_survivor_cell_ids:
        return _empty_report(
            status=STATUS_NO_TRAIN_SURVIVORS,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    if not holdout_window_ids:
        return _empty_report(
            status=STATUS_NO_HOLDOUT_WINDOWS,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    reloaded_data_corpus_hash = _compute_source_config_corpus_hash(source_config_path, prepare_manifest=prepare_manifest)
    if reloaded_data_corpus_hash != data_corpus_hash:
        return _empty_report(
            status=STATUS_DATA_CORPUS_HASH_MISMATCH,
            train_survivor_cell_ids=train_survivor_cell_ids,
            train_window_ids=train_window_ids,
            holdout_window_ids=holdout_window_ids,
            metadata=metadata,
            data_corpus_hash=data_corpus_hash,
            precommitment_hash=precommitment_hash,
            window_index_hash=window_index_hash,
            discovery_config_hash=discovery_config_hash,
            plan_hash=plan_hash,
            evaluation_hash=evaluation_hash,
            survivor_freeze_config_hash=survivor_freeze_config_hash,
            survivor_freeze_hash=survivor_freeze_hash,
        )

    discovery_plan = _discovery_plan_from_payload(discovery_plan_payload)
    price_series = _load_price_series(source_config_path)
    window_by_id = _window_lookup(stress_windows_payload)
    survivor_cell_map = {str(cell["cell_id"]): cell for cell in train_survivor_freeze_payload.get("survivor_cells", [])}

    cell_results: list[OfflineHoldoutEvaluationCellResult] = []
    excluded_survivor_cells: dict[str, list[str]] = {}
    for cell in discovery_plan.plan_cells:
        if cell.cell_id not in train_survivor_cell_ids:
            continue
        freeze_cell = survivor_cell_map.get(cell.cell_id)
        if freeze_cell is None:
            excluded_survivor_cells[cell.cell_id] = [STATUS_UNSUPPORTED_SURVIVOR_CELL]
            continue
        if cell.family_id == "family_4_stablecoin_quote_regime_conditioning":
            excluded_survivor_cells[cell.cell_id] = [STATUS_CONDITIONING_NOT_RUN_PHASE_2B2B2]
            continue
        if cell.family_id == "family_3_usd_reference_translation_lag" and not bool(freeze_cell.get("latency_gate_satisfied", False)):
            excluded_survivor_cells[cell.cell_id] = [STATUS_LATENCY_GATE_REQUIRED_NOT_RUN]
            continue
        if cell.family_id == "family_1_same_venue_quote_basis":
            result = _evaluate_family1(cell, holdout_window_ids, window_by_id, price_series, evaluation_hash, survivor_freeze_hash, plan_hash)
        elif cell.family_id == "family_2_cross_venue_impulse":
            result = _evaluate_family2(cell, holdout_window_ids, window_by_id, price_series, evaluation_hash, survivor_freeze_hash, plan_hash)
        else:
            excluded_survivor_cells[cell.cell_id] = [STATUS_UNSUPPORTED_SURVIVOR_CELL]
            continue
        if result.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY:
            cell_results.append(result)
        else:
            excluded_survivor_cells[cell.cell_id] = list(result.exclusion_reasons or [result.status])

    if not cell_results and excluded_survivor_cells:
        statuses = {reason for reasons in excluded_survivor_cells.values() for reason in reasons}
        if STATUS_INSUFFICIENT_HOLDOUT_EVENTS in statuses:
            status = STATUS_INSUFFICIENT_HOLDOUT_EVENTS
        else:
            status = STATUS_NO_EVALUABLE_SURVIVORS
    elif not cell_results:
        status = STATUS_NO_EVALUABLE_SURVIVORS
    else:
        status = STATUS_OFFLINE_HOLDOUT_EVALUATION_READY

    report = OfflineHoldoutEvaluationReport(
        status=status,
        cell_results=cell_results,
        excluded_survivor_cells=excluded_survivor_cells,
        train_survivor_cell_ids=train_survivor_cell_ids,
        holdout_evaluated_cell_ids=[cell.cell_id for cell in cell_results],
        train_window_ids_seen_but_not_evaluated=train_window_ids,
        holdout_window_ids=holdout_window_ids,
        holdout_evaluation_hash="",
        metadata=metadata,
        run_id=f"offline_holdout_evaluation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        generated_at_utc=_now_utc_iso(),
        git_sha=_get_git_sha(),
        data_corpus_hash=data_corpus_hash,
        precommitment_hash=precommitment_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        evaluation_hash=evaluation_hash,
        survivor_freeze_config_hash=survivor_freeze_config_hash,
        survivor_freeze_hash=survivor_freeze_hash,
    )
    object.__setattr__(report, "holdout_evaluation_hash", compute_holdout_evaluation_hash(report))
    return report


def build_offline_holdout_evaluation_manifest_payload(
    report: OfflineHoldoutEvaluationReport,
    *,
    prepare_manifest_path: str,
    stress_window_manifest_path: str,
    discovery_plan_manifest_path: str,
    train_evaluation_manifest_path: str,
    train_survivor_freeze_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "phase": "offline_holdout_evaluation",
        "generated_at_utc": report.generated_at_utc,
        "git_sha": report.git_sha,
        "schema_version": HOLDOUT_EVALUATION_SCHEMA_VERSION,
        "prepare_manifest_path": prepare_manifest_path,
        "stress_window_manifest_path": stress_window_manifest_path,
        "discovery_plan_manifest_path": discovery_plan_manifest_path,
        "train_evaluation_manifest_path": train_evaluation_manifest_path,
        "train_survivor_freeze_manifest_path": train_survivor_freeze_manifest_path,
        "data_corpus_hash": report.data_corpus_hash,
        "precommitment_hash": report.precommitment_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_config_hash": report.survivor_freeze_config_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "status": report.status,
        "train_survivor_cell_count": len(report.train_survivor_cell_ids),
        "holdout_evaluated_cell_count": len(report.holdout_evaluated_cell_ids),
        "excluded_survivor_cell_count": len(report.excluded_survivor_cells),
        "train_window_count_seen_but_not_evaluated": len(report.train_window_ids_seen_but_not_evaluated),
        "holdout_window_count": len(report.holdout_window_ids),
        "safety": "public_data_observer_only",
    }


def _report_payload(report: OfflineHoldoutEvaluationReport) -> dict[str, Any]:
    return {
        "schema_version": HOLDOUT_EVALUATION_SCHEMA_VERSION,
        "status": report.status,
        "cell_results": [asdict(cell) for cell in report.cell_results],
        "excluded_survivor_cells": report.excluded_survivor_cells,
        "train_survivor_cell_ids": report.train_survivor_cell_ids,
        "holdout_evaluated_cell_ids": report.holdout_evaluated_cell_ids,
        "train_window_ids_seen_but_not_evaluated": report.train_window_ids_seen_but_not_evaluated,
        "holdout_window_ids": report.holdout_window_ids,
        "holdout_evaluation_hash": report.holdout_evaluation_hash,
        "metadata": report.metadata,
        "run_id": report.run_id,
        "generated_at_utc": report.generated_at_utc,
        "git_sha": report.git_sha,
        "data_corpus_hash": report.data_corpus_hash,
        "precommitment_hash": report.precommitment_hash,
        "window_index_hash": report.window_index_hash,
        "discovery_config_hash": report.discovery_config_hash,
        "plan_hash": report.plan_hash,
        "evaluation_hash": report.evaluation_hash,
        "survivor_freeze_config_hash": report.survivor_freeze_config_hash,
        "survivor_freeze_hash": report.survivor_freeze_hash,
    }


def write_offline_holdout_evaluation_outputs(
    report: OfflineHoldoutEvaluationReport,
    out_dir: Path,
    *,
    prepare_manifest_path: str,
    stress_window_manifest_path: str,
    discovery_plan_manifest_path: str,
    train_evaluation_manifest_path: str,
    train_survivor_freeze_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    safe_output_dir(out_dir, allow_existing=overwrite)
    result_path = out_dir / "offline_holdout_evaluation.json"
    manifest_path = out_dir / "offline_holdout_evaluation_manifest.json"
    atomic_write_json(result_path, _report_payload(report))
    atomic_write_json(
        manifest_path,
        build_offline_holdout_evaluation_manifest_payload(
            report,
            prepare_manifest_path=prepare_manifest_path,
            stress_window_manifest_path=stress_window_manifest_path,
            discovery_plan_manifest_path=discovery_plan_manifest_path,
            train_evaluation_manifest_path=train_evaluation_manifest_path,
            train_survivor_freeze_manifest_path=train_survivor_freeze_manifest_path,
        ),
    )
    return {"result_path": result_path, "manifest_path": manifest_path}
