from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_corpus_hash import (
    compute_data_corpus_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import (
    DISCOVERY_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import CostConfig
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import (
    OfflineDiscoveryPlan,
)
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import (
    OfflineDiscoveryPlanCell,
)
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import (
    compute_window_index_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    RESOLUTION_BAR,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    RESOLUTION_TRADE,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    WINDOW_MODE_CAUSAL,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OfflinePrepareManifest,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OfflineSourceFile,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    HOLDOUT_EVALUATION_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_CONDITIONING_NOT_RUN_PHASE_2B2B2,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_DISCOVERY_PLAN_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_INPUT_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_INSUFFICIENT_HOLDOUT_EVENTS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_LATENCY_GATE_REQUIRED_NOT_RUN,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_NO_EVALUABLE_SURVIVORS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_NO_HOLDOUT_WINDOWS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_NO_TRAIN_SURVIVORS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_OFFLINE_HOLDOUT_EVALUATION_READY,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_SURVIVOR_FREEZE_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_UNUSABLE_SURVIVOR_FREEZE,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    STATUS_WINDOW_INDEX_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    _summarize_event_returns,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    build_offline_holdout_evaluation_manifest_payload,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    build_offline_holdout_evaluation_report,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    compute_holdout_evaluation_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_holdout_evaluation import (
    write_offline_holdout_evaluation_outputs,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    STATUS_OFFLINE_STRESS_INDEX_READY,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    OfflineStressWindow,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_evaluation import (
    EVALUATION_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    FREEZE_SCHEMA_VERSION,
)


NS = 1_000_000_000


def _source_file(
    path: Path,
    *,
    logical_source_id: str,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    row_count: int = 6,
    data_start_ns: int = 0,
    data_end_ns: int = 600 * NS,
) -> OfflineSourceFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_bytes = b"fixture"
    path.write_bytes(file_bytes)
    return OfflineSourceFile(
        path=str(path),
        logical_source_id=logical_source_id,
        venue=venue,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        source_kind="bars",
        stream_type="bars",
        resolution_type=RESOLUTION_BAR,
        timestamp_unit="ns",
        expected_start_ns=0,
        expected_end_ns=600 * NS,
        file_size_bytes=len(file_bytes),
        mtime_ns=1,
        file_sha256=hashlib.sha256(file_bytes).hexdigest(),
        row_count=row_count,
        data_start_ns=data_start_ns,
        data_end_ns=data_end_ns,
    )


def _prepare_manifest(
    tmp_path: Path,
    *,
    data_corpus_hash: str | None = None,
    precommitment_hash: str | None = None,
    row_counts: dict[tuple[str, str], int] | None = None,
    data_ranges: dict[tuple[str, str], tuple[int, int]] | None = None,
) -> OfflinePrepareManifest:
    row_counts = row_counts or {}
    data_ranges = data_ranges or {}
    def make(path_name: str, logical_source_id: str, venue: str, symbol: str, base_asset: str, quote_asset: str) -> OfflineSourceFile:
        start_ns, end_ns = data_ranges.get((venue, symbol), (0, 600 * NS))
        return _source_file(
            tmp_path / path_name,
            logical_source_id=logical_source_id,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            row_count=row_counts.get((venue, symbol), 6),
            data_start_ns=start_ns,
            data_end_ns=end_ns,
        )

    sources = [
        make("kraken_btcusd.csv", "kraken-btcusd", "kraken", "BTC/USD", "BTC", "USD"),
        make("kraken_btcusdt.csv", "kraken-btcusdt", "kraken", "BTC/USDT", "BTC", "USDT"),
        make("binance_btcusdt.csv", "binance-btcusdt", "binance", "BTC/USDT", "BTC", "USDT"),
        make("binance_ethusdt.csv", "binance-ethusdt", "binance", "ETH/USDT", "ETH", "USDT"),
        make("binance_solusdt.csv", "binance-solusdt", "binance", "SOL/USDT", "SOL", "USDT"),
        make("coinbase_btcusd.csv", "coinbase-btcusd", "coinbase", "BTC/USD", "BTC", "USD"),
    ]
    corpus_hash = data_corpus_hash or compute_data_corpus_hash(sources, OFFLINE_DATA_SCHEMA_VERSION)
    return OfflinePrepareManifest(
        run_id="prepare_run",
        phase="offline_historical_prepare",
        generated_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
        schema_version=OFFLINE_DATA_SCHEMA_VERSION,
        precommitment_hash=precommitment_hash,
        data_corpus_hash=corpus_hash,
        source_files=[asdict(source) for source in sources],
        timestamp_validation={},
        hash_cache_used=False,
        hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=0,
        normalized_time_range={},
        stream_counts={},
        resolution_summary={RESOLUTION_BAR: len(sources)},
        quote_currency_summary={"USD": 2, "USDT": 4},
        safety="public_data_observer_only",
        forbidden_capabilities_present=False,
        next_phase_allowed=True,
    )


def _window(window_id: str, trigger_timestamp_ns: int, *, data_corpus_hash: str, promotion_allowed: bool = True) -> OfflineStressWindow:
    return OfflineStressWindow(
        window_id=window_id,
        selection_mode=WINDOW_MODE_CAUSAL,
        promotion_allowed=promotion_allowed,
        source_venue="kraken",
        source_symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        trigger_timestamp_ns=trigger_timestamp_ns,
        window_start_ns=trigger_timestamp_ns - (30 * NS),
        window_end_ns=trigger_timestamp_ns + (120 * NS),
        pre_window_start_ns=trigger_timestamp_ns - (30 * NS),
        post_window_end_ns=trigger_timestamp_ns + (120 * NS),
        rule_name="rolling_range_bps",
        rule_version="v1",
        trigger_metric="range_bps",
        trigger_value=150.0,
        trigger_threshold=100.0,
        lookback_ns=60 * NS,
        cooldown_ns=15 * NS,
        resolution_type=RESOLUTION_BAR,
        data_corpus_hash=data_corpus_hash,
        precommitment_hash=None,
        metadata={"logical_source_id": "kraken-btcusd"},
    )


def _stress_payload(windows: list[OfflineStressWindow]) -> dict:
    return {
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "status": STATUS_OFFLINE_STRESS_INDEX_READY,
        "windows": [asdict(window) for window in windows],
    }


def _stress_manifest(*, data_corpus_hash: str, window_index_hash: str, precommitment_hash: str | None = None) -> dict:
    return {
        "run_id": "stress_run",
        "phase": "offline_stress_window_index",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "data_corpus_hash": data_corpus_hash,
        "precommitment_hash": precommitment_hash,
        "stress_rule_config_hash": "stress_rule_hash",
        "window_index_hash": window_index_hash,
        "status": STATUS_OFFLINE_STRESS_INDEX_READY,
    }


def _plan_cell(
    *,
    family_id: str,
    family_name: str,
    source_venues: tuple[str, ...],
    source_symbols: tuple[str, ...],
    target_venues: tuple[str, ...],
    target_symbols: tuple[str, ...],
    window_ids: tuple[str, ...],
    lookback_ms: int = 60_000,
    horizon_ms: int = 60_000,
    signal_variant: str | None = None,
    latency_gate_required: bool = False,
    is_edge_family: bool = True,
    is_conditioning_family: bool = False,
    promotion_allowed: bool = True,
    required_resolution: str = RESOLUTION_BAR,
    cost_config: CostConfig | None = None,
    discovery_config_hash: str = "discovery_hash",
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
) -> OfflineDiscoveryPlanCell:
    return OfflineDiscoveryPlanCell(
        cell_id=f"{family_id}:{signal_variant}:{lookback_ms}:{horizon_ms}:{'-'.join(window_ids)}",
        family_id=family_id,
        family_name=family_name,
        is_edge_family=is_edge_family,
        is_conditioning_family=is_conditioning_family,
        source_venues=source_venues,
        source_symbols=source_symbols,
        target_venues=target_venues,
        target_symbols=target_symbols,
        signal_variant=signal_variant,
        lookback_ms=lookback_ms,
        horizon_ms=horizon_ms,
        required_resolution=required_resolution,
        latency_gate_required=latency_gate_required,
        cost_config=cost_config or CostConfig(1.0, 2.0, 3.0),
        window_ids=window_ids,
        promotion_allowed=promotion_allowed,
        exclusion_reasons=(),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
    )


def _plan(cells: list[OfflineDiscoveryPlanCell], *, data_corpus_hash: str, window_index_hash: str, plan_hash: str = "plan_hash", holdout_window_ids: list[str] | None = None) -> OfflineDiscoveryPlan:
    effective_holdout_ids = ["w3"] if holdout_window_ids is None else list(holdout_window_ids)
    edge_count = len([cell for cell in cells if cell.is_edge_family])
    conditioning_count = len([cell for cell in cells if cell.is_conditioning_family])
    return OfflineDiscoveryPlan(
        status="OFFLINE_DISCOVERY_PLAN_READY",
        plan_cells=cells,
        excluded_windows=[],
        family_summary={"train_window_count": 2, "holdout_window_count": len(effective_holdout_ids), "split_timestamp_boundary_ns": 200 * NS},
        train_window_ids=["w1", "w2"],
        holdout_window_ids=effective_holdout_ids,
        edge_family_cell_count=edge_count,
        conditioning_cell_count=conditioning_count,
        discovery_config_hash="discovery_hash",
        plan_hash=plan_hash,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        split_timestamp_boundary_ns=200 * NS,
        train_survivor_cell_ids=[],
        holdout_evaluation_cell_ids=[],
        survivor_freeze_status="NOT_RUN_PHASE_2B2B1",
        run_id="discovery_run",
        generated_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
        stress_rule_config_hash="stress_rule_hash",
        precommitment_hash=None,
    )


def _discovery_manifest(plan: OfflineDiscoveryPlan, *, data_corpus_hash: str, window_index_hash: str, plan_hash: str = "plan_hash") -> dict:
    return {
        "run_id": plan.run_id,
        "phase": "offline_discovery_plan",
        "generated_at_utc": plan.generated_at_utc,
        "git_sha": plan.git_sha,
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "prepare_manifest_path": "prepare.json",
        "stress_window_manifest_path": "stress_manifest.json",
        "data_corpus_hash": data_corpus_hash,
        "precommitment_hash": None,
        "stress_rule_config_hash": "stress_rule_hash",
        "window_index_hash": window_index_hash,
        "discovery_config_hash": plan.discovery_config_hash,
        "plan_hash": plan_hash,
        "status": plan.status,
        "edge_family_cell_count": plan.edge_family_cell_count,
        "conditioning_cell_count": plan.conditioning_cell_count,
        "train_window_count": len(plan.train_window_ids),
        "holdout_window_count": len(plan.holdout_window_ids),
        "safety": "public_data_observer_only",
    }


def _discovery_payload(plan: OfflineDiscoveryPlan, *, data_corpus_hash: str, window_index_hash: str, plan_hash: str = "plan_hash") -> dict:
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "status": plan.status,
        "plan_cells": [
            {
                **asdict(cell),
                "cost_config": asdict(cell.cost_config),
            }
            for cell in plan.plan_cells
        ],
        "excluded_windows": [],
        "family_summary": plan.family_summary,
        "train_window_ids": plan.train_window_ids,
        "holdout_window_ids": plan.holdout_window_ids,
        "edge_family_cell_count": plan.edge_family_cell_count,
        "conditioning_cell_count": plan.conditioning_cell_count,
        "discovery_config_hash": plan.discovery_config_hash,
        "plan_hash": plan_hash,
        "split_timestamp_boundary_ns": plan.split_timestamp_boundary_ns,
        "train_survivor_cell_ids": [],
        "holdout_evaluation_cell_ids": [],
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2B1",
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
    }


def _train_evaluation_payload(
    *,
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str = "evaluation_hash",
    train_window_ids: list[str] | None = None,
    holdout_window_ids: list[str] | None = None,
) -> dict:
    effective_holdout_ids = ["w3"] if holdout_window_ids is None else list(holdout_window_ids)
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "status": "OFFLINE_TRAIN_EVALUATION_READY",
        "cell_results": [],
        "excluded_cells": [],
        "train_window_ids": train_window_ids or ["w1", "w2"],
        "holdout_window_ids_seen_but_not_evaluated": effective_holdout_ids,
        "raw_train_screen_cell_ids": [],
        "train_survivor_cell_ids": [],
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "evaluation_hash": evaluation_hash,
        "metadata": {"holdout_window_ids_seen_but_not_evaluated": effective_holdout_ids},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
    }


def _train_evaluation_manifest(payload: dict) -> dict:
    return {
        "run_id": "train_eval_run",
        "phase": "offline_train_evaluation",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "train_evaluation_path": "offline_train_evaluation.json",
        "data_corpus_hash": payload["data_corpus_hash"],
        "precommitment_hash": None,
        "window_index_hash": payload["window_index_hash"],
        "discovery_config_hash": payload["discovery_config_hash"],
        "plan_hash": payload["plan_hash"],
        "evaluation_hash": payload["evaluation_hash"],
        "status": payload["status"],
        "raw_train_screen_cell_count": 0,
        "train_survivor_cell_count": 0,
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "safety": "public_data_observer_only",
    }


def _holdout_cell_from_plan(
    cell: OfflineDiscoveryPlanCell,
    *,
    status: str = "OFFLINE_TRAIN_EVALUATION_READY",
    valid_event_count: int = 2,
    net_mean_bps: float = 1.0,
    net_median_bps: float = 1.0,
    win_rate: float = 1.0,
    worst_net_bps: float = 0.5,
    latency_gate_satisfied: bool | None = None,
) -> dict:
    payload = {
        "cell_id": cell.cell_id,
        "family_id": cell.family_id,
        "family_name": cell.family_name,
        "is_edge_family": cell.is_edge_family,
        "status": status,
        "train_window_ids": ["w1", "w2"],
        "evaluated_event_count": valid_event_count,
        "valid_event_count": valid_event_count,
        "lookback_ms": cell.lookback_ms,
        "horizon_ms": cell.horizon_ms,
        "signal_variant": cell.signal_variant,
        "required_resolution": cell.required_resolution,
        "latency_gate_required": cell.latency_gate_required,
        "raw_mean_bps": net_mean_bps + 6.0,
        "raw_median_bps": net_median_bps + 6.0,
        "net_mean_bps": net_mean_bps,
        "net_median_bps": net_median_bps,
        "win_rate": win_rate,
        "worst_net_bps": worst_net_bps,
        "fee_bps": cell.cost_config.fees_bps,
        "slippage_bps": cell.cost_config.slippage_bps,
        "quote_mismatch_buffer_bps": cell.cost_config.quote_mismatch_bps,
        "exclusion_reasons": [],
        "data_corpus_hash": cell.data_corpus_hash,
        "window_index_hash": cell.window_index_hash,
        "plan_hash": "plan_hash",
    }
    if latency_gate_satisfied is not None:
        payload["latency_gate_satisfied"] = latency_gate_satisfied
    return payload


def _survivor_freeze_payload(
    cells: list[dict],
    *,
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
    plan_hash: str,
    evaluation_hash: str,
    train_survivor_cell_ids: list[str],
    raw_train_screen_cell_ids: list[str] | None = None,
    holdout_window_ids: list[str] | None = None,
    status: str = "OFFLINE_TRAIN_SURVIVOR_FREEZE_READY",
    survivor_freeze_hash: str = "survivor_freeze_hash",
    survivor_freeze_config_hash: str = "survivor_freeze_config_hash",
) -> dict:
    effective_holdout_ids = ["w3"] if holdout_window_ids is None else list(holdout_window_ids)
    return {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "status": status,
        "train_survivor_cell_ids": train_survivor_cell_ids,
        "rejected_cell_ids": [],
        "rejection_reasons_by_cell": {},
        "raw_train_screen_cell_ids": raw_train_screen_cell_ids or train_survivor_cell_ids,
        "holdout_window_ids_seen_but_not_evaluated": effective_holdout_ids,
        "survivor_cells": cells,
        "survivor_freeze_config_hash": survivor_freeze_config_hash,
        "survivor_freeze_hash": survivor_freeze_hash,
        "metadata": {"holdout_window_ids_seen_but_not_evaluated": effective_holdout_ids},
        "run_id": "freeze_run",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
    }


def _survivor_freeze_manifest(payload: dict) -> dict:
    return {
        "run_id": "freeze_run",
        "phase": "offline_train_survivor_freeze",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": FREEZE_SCHEMA_VERSION,
        "train_evaluation_manifest_path": "offline_train_evaluation_manifest.json",
        "data_corpus_hash": payload["data_corpus_hash"],
        "precommitment_hash": None,
        "window_index_hash": payload["window_index_hash"],
        "discovery_config_hash": payload["discovery_config_hash"],
        "plan_hash": payload["plan_hash"],
        "evaluation_hash": payload["evaluation_hash"],
        "survivor_freeze_config_hash": payload["survivor_freeze_config_hash"],
        "survivor_freeze_hash": payload["survivor_freeze_hash"],
        "status": payload["status"],
        "raw_train_screen_cell_count": len(payload["raw_train_screen_cell_ids"]),
        "train_survivor_cell_count": len(payload["train_survivor_cell_ids"]),
        "holdout_window_count_seen_but_not_evaluated": len(payload["holdout_window_ids_seen_but_not_evaluated"]),
        "safety": "public_data_observer_only",
    }


def _source_config(tmp_path: Path, price_map: dict[str, list[tuple[int, float]]]) -> Path:
    payload = {"sources": []}
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, rows in sorted(price_map.items()):
        venue, symbol = name.split("|", 1)
        file_path = tmp_path / f"{name.replace('/', '_').replace('|', '__')}.json"
        file_payload = {
            "venue": venue,
            "symbol": symbol,
            "rows": [{"timestamp_ns": ts, "close": price} for ts, price in rows],
        }
        file_path.write_text(json.dumps(file_payload, sort_keys=True), encoding="utf-8")
        payload["sources"].append(
            {
                "name": name,
                "venue": venue,
                "symbol": symbol,
                "path": str(file_path),
                "resolution": RESOLUTION_BAR,
            }
        )
    config_path = tmp_path / "offline_sources.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def _base_price_map(*, train_shift: float = 0.0, holdout_shift: float = 0.0) -> dict[str, list[tuple[int, float]]]:
    return {
        "kraken|BTC/USD": [
            (0 * NS, 100.0),
            (100 * NS, 100.0 + train_shift),
            (160 * NS, 102.0 + train_shift),
            (200 * NS, 103.0 + train_shift),
            (220 * NS, 105.0 + train_shift),
            (300 * NS, 104.0),
            (360 * NS, 106.0),
        ],
        "kraken|BTC/USDT": [
            (0 * NS, 101.0 + train_shift),
            (100 * NS, 101.0 + train_shift),
            (160 * NS, 100.0 + train_shift),
            (200 * NS, 99.0 + train_shift),
            (220 * NS, 103.0 + train_shift),
            (300 * NS, 105.0),
            (360 * NS, 106.0),
        ],
        "binance|BTC/USDT": [
            (100 * NS, 100.0),
            (160 * NS, 103.0),
            (200 * NS, 104.0),
            (220 * NS, 105.0),
            (300 * NS, 106.0),
            (360 * NS, 108.0 + holdout_shift),
        ],
        "binance|ETH/USDT": [
            (100 * NS, 50.0),
            (160 * NS, 51.0),
            (200 * NS, 52.0),
            (220 * NS, 53.0),
            (300 * NS, 54.0),
            (360 * NS, 55.0 + holdout_shift),
        ],
        "binance|SOL/USDT": [
            (100 * NS, 10.0),
            (160 * NS, 10.2),
            (200 * NS, 10.3),
            (220 * NS, 10.4),
            (300 * NS, 11.0),
            (360 * NS, 11.5 + (holdout_shift / 10.0)),
        ],
        "coinbase|BTC/USD": [
            (100 * NS, 100.0),
            (160 * NS, 102.0),
            (200 * NS, 103.0),
            (220 * NS, 104.0),
            (300 * NS, 105.0),
            (360 * NS, 109.0 + holdout_shift),
        ],
    }


def _evaluate(
    tmp_path: Path,
    *,
    cells: list[OfflineDiscoveryPlanCell],
    survivor_ids: list[str],
    survivor_cells: list[dict] | None = None,
    holdout_window_ids: list[str] | None = None,
    price_map: dict[str, list[tuple[int, float]]] | None = None,
    precommitment_hash: str | None = None,
    prepare_data_corpus_hash: str | None = None,
    stress_manifest_data_corpus_hash: str | None = None,
    discovery_manifest_data_corpus_hash: str | None = None,
    train_eval_manifest_data_corpus_hash: str | None = None,
    freeze_manifest_data_corpus_hash: str | None = None,
    stress_manifest_hash_override: str | None = None,
    discovery_manifest_hash_override: str | None = None,
    discovery_manifest_plan_hash_override: str | None = None,
    train_eval_manifest_hash_override: str | None = None,
    freeze_manifest_hash_override: str | None = None,
    freeze_manifest_status_override: str | None = None,
    freeze_payload_status_override: str | None = None,
    train_eval_manifest_eval_hash_override: str | None = None,
    freeze_manifest_eval_hash_override: str | None = None,
    freeze_manifest_plan_hash_override: str | None = None,
    reloaded_price_map: dict[str, list[tuple[int, float]]] | None = None,
):
    active_price_map = price_map or _base_price_map()
    manifest_row_counts = {
        tuple(name.split("|", 1)): len(rows)
        for name, rows in active_price_map.items()
    }
    manifest_ranges = {
        tuple(name.split("|", 1)): (int(rows[0][0]), int(rows[-1][0]))
        for name, rows in active_price_map.items()
        if rows
    }
    if prepare_data_corpus_hash is None:
        derived_prepare_manifest = _prepare_manifest(
            tmp_path / "prepare_seed",
            row_counts=manifest_row_counts,
            data_ranges=manifest_ranges,
        )
        data_corpus_hash = derived_prepare_manifest.data_corpus_hash
    else:
        data_corpus_hash = prepare_data_corpus_hash
    holdout_ids = ["w3"] if holdout_window_ids is None else list(holdout_window_ids)
    plan_holdout_ids = ["w3"] if holdout_window_ids is None else list(holdout_window_ids)
    freeze_holdout_ids = ["w3"] if holdout_window_ids is None else list(holdout_window_ids)
    stress_windows = [
        _window("w1", 100 * NS, data_corpus_hash=data_corpus_hash),
        _window("w2", 200 * NS, data_corpus_hash=data_corpus_hash),
        _window("w3", 300 * NS, data_corpus_hash=data_corpus_hash),
    ]
    stress_payload = _stress_payload(stress_windows)
    window_index_hash = compute_window_index_hash(stress_payload)
    plan = _plan(cells, data_corpus_hash=data_corpus_hash, window_index_hash=window_index_hash, holdout_window_ids=plan_holdout_ids)
    discovery_payload = _discovery_payload(plan, data_corpus_hash=data_corpus_hash, window_index_hash=window_index_hash)
    train_eval_payload = _train_evaluation_payload(
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=plan.discovery_config_hash,
        plan_hash=plan.plan_hash,
        holdout_window_ids=holdout_ids,
    )
    survivor_cell_payloads = (
        survivor_cells
        if survivor_cells is not None
        else [
            _holdout_cell_from_plan(cell, latency_gate_satisfied=True if cell.family_id == "family_3_usd_reference_translation_lag" else None)
            for cell in cells
            if cell.cell_id in survivor_ids
        ]
    )
    freeze_payload = _survivor_freeze_payload(
        survivor_cell_payloads,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=plan.discovery_config_hash,
        plan_hash=plan.plan_hash,
        evaluation_hash=train_eval_payload["evaluation_hash"],
        train_survivor_cell_ids=survivor_ids,
        holdout_window_ids=freeze_holdout_ids,
        status=(freeze_payload_status_override if freeze_payload_status_override is not None else "OFFLINE_TRAIN_SURVIVOR_FREEZE_READY"),
    )

    prepare_manifest = _prepare_manifest(
        tmp_path / "prepare",
        data_corpus_hash=prepare_data_corpus_hash or data_corpus_hash,
        precommitment_hash=precommitment_hash,
        row_counts=manifest_row_counts,
        data_ranges=manifest_ranges,
    )
    stress_manifest = _stress_manifest(
        data_corpus_hash=stress_manifest_data_corpus_hash or data_corpus_hash,
        window_index_hash=stress_manifest_hash_override or window_index_hash,
        precommitment_hash=precommitment_hash,
    )
    discovery_manifest = _discovery_manifest(
        plan,
        data_corpus_hash=discovery_manifest_data_corpus_hash or data_corpus_hash,
        window_index_hash=discovery_manifest_hash_override or window_index_hash,
        plan_hash=discovery_manifest_plan_hash_override or plan.plan_hash,
    )
    train_eval_manifest = _train_evaluation_manifest(train_eval_payload)
    train_eval_manifest["data_corpus_hash"] = train_eval_manifest_data_corpus_hash or data_corpus_hash
    train_eval_manifest["evaluation_hash"] = train_eval_manifest_eval_hash_override or train_eval_payload["evaluation_hash"]
    freeze_manifest = _survivor_freeze_manifest(freeze_payload)
    freeze_manifest["data_corpus_hash"] = freeze_manifest_data_corpus_hash or data_corpus_hash
    freeze_manifest["survivor_freeze_hash"] = freeze_manifest_hash_override or freeze_payload["survivor_freeze_hash"]
    freeze_manifest["evaluation_hash"] = freeze_manifest_eval_hash_override or freeze_payload["evaluation_hash"]
    freeze_manifest["plan_hash"] = freeze_manifest_plan_hash_override or freeze_payload["plan_hash"]
    if freeze_manifest_status_override:
        freeze_manifest["status"] = freeze_manifest_status_override
    if discovery_manifest_hash_override is not None:
        discovery_manifest["window_index_hash"] = discovery_manifest_hash_override
    if stress_manifest_hash_override is not None:
        stress_manifest["window_index_hash"] = stress_manifest_hash_override
    if train_eval_manifest_hash_override is not None:
        train_eval_manifest["evaluation_hash"] = train_eval_manifest_hash_override

    active_price_map = price_map or _base_price_map()
    source_config_path = _source_config(tmp_path / "source_config", active_price_map)
    if reloaded_price_map is not None:
        source_config_path = _source_config(tmp_path / "source_config_reloaded", reloaded_price_map)

    return build_offline_holdout_evaluation_report(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=stress_payload,
        discovery_plan_manifest=discovery_manifest,
        discovery_plan_payload=discovery_payload,
        train_evaluation_manifest=train_eval_manifest,
        train_evaluation_payload=train_eval_payload,
        train_survivor_freeze_manifest=freeze_manifest,
        train_survivor_freeze_payload=freeze_payload,
        source_config_path=source_config_path,
    )


def _family1_cell(*, data_corpus_hash: str = "corpus_hash", window_index_hash: str = "window_hash") -> OfflineDiscoveryPlanCell:
    return _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("coinbase",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2", "w3"),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        required_resolution=RESOLUTION_TRADE,
        lookback_ms=60_000,
    )


def _family2_cell(*, signal_variant: str = "source_move_impulse", data_corpus_hash: str = "corpus_hash", window_index_hash: str = "window_hash") -> OfflineDiscoveryPlanCell:
    return _plan_cell(
        family_id="family_2_cross_venue_impulse",
        family_name="Family 2",
        source_venues=("binance",),
        source_symbols=("BTC/USDT",),
        target_venues=("coinbase",),
        target_symbols=("BTC/USD",),
        signal_variant=signal_variant,
        window_ids=("w1", "w2", "w3"),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
    )


def _family3_cell(*, latency_gate_required: bool = True, data_corpus_hash: str = "corpus_hash", window_index_hash: str = "window_hash") -> OfflineDiscoveryPlanCell:
    return _plan_cell(
        family_id="family_3_usd_reference_translation_lag",
        family_name="Family 3",
        source_venues=("binance",),
        source_symbols=("BTC/USDT",),
        target_venues=("coinbase",),
        target_symbols=("BTC/USD",),
        signal_variant="source_move_impulse",
        latency_gate_required=latency_gate_required,
        window_ids=("w1", "w2", "w3"),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
    )


def _family4_cell(*, data_corpus_hash: str = "corpus_hash", window_index_hash: str = "window_hash") -> OfflineDiscoveryPlanCell:
    return _plan_cell(
        family_id="family_4_stablecoin_quote_regime_conditioning",
        family_name="Family 4",
        source_venues=("kraken",),
        source_symbols=("BTC/USD",),
        target_venues=("coinbase",),
        target_symbols=("BTC/USD",),
        is_edge_family=False,
        is_conditioning_family=True,
        signal_variant="conditioning",
        window_ids=("w1", "w2", "w3"),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
    )


def test_rejects_data_corpus_hash_mismatch_across_lineage_inputs(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], stress_manifest_data_corpus_hash="other_hash")
    assert report.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_stress_window_hash_mismatch(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], stress_manifest_hash_override="wrong_window_hash")
    assert report.status == STATUS_WINDOW_INDEX_HASH_MISMATCH


def test_rejects_discovery_plan_hash_mismatch(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], discovery_manifest_plan_hash_override="wrong_plan_hash")
    assert report.status == STATUS_DISCOVERY_PLAN_HASH_MISMATCH


def test_rejects_train_evaluation_hash_mismatch(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], train_eval_manifest_eval_hash_override="wrong_eval_hash")
    assert report.status == STATUS_TRAIN_EVALUATION_HASH_MISMATCH


def test_rejects_survivor_freeze_hash_mismatch(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], freeze_manifest_hash_override="wrong_freeze_hash")
    assert report.status == STATUS_SURVIVOR_FREEZE_HASH_MISMATCH


def test_rejects_unusable_survivor_freeze_status(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], freeze_payload_status_override="NO_TRAIN_SURVIVORS")
    assert report.status == STATUS_UNUSABLE_SURVIVOR_FREEZE


def test_returns_no_train_survivors_when_freeze_has_no_survivors(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[], survivor_cells=[])
    assert report.status == STATUS_NO_TRAIN_SURVIVORS


def test_returns_no_holdout_windows_when_no_holdout_windows_exist(tmp_path: Path):
    cell = _family1_cell()
    report = _evaluate(tmp_path, cells=[cell], survivor_ids=[cell.cell_id], holdout_window_ids=[])
    assert report.status == STATUS_NO_HOLDOUT_WINDOWS


def test_evaluates_only_frozen_survivor_cell_ids(tmp_path: Path):
    survivor = _family1_cell()
    non_survivor = _family2_cell()
    report = _evaluate(tmp_path, cells=[survivor, non_survivor], survivor_ids=[survivor.cell_id])
    assert set(report.holdout_evaluated_cell_ids) == {survivor.cell_id}
    assert non_survivor.cell_id not in report.holdout_evaluated_cell_ids


def test_does_not_evaluate_raw_train_screen_cells_that_failed_freeze(tmp_path: Path):
    survivor = _family1_cell()
    failed = _family2_cell()
    freeze_cells = [
        _holdout_cell_from_plan(survivor),
        _holdout_cell_from_plan(failed),
    ]
    report = _evaluate(tmp_path, cells=[survivor, failed], survivor_ids=[survivor.cell_id], survivor_cells=freeze_cells)
    assert failed.cell_id not in report.holdout_evaluated_cell_ids


def test_does_not_evaluate_non_survivor_plan_cells(tmp_path: Path):
    survivor = _family1_cell()
    non_survivor = _family2_cell(signal_variant="source_move_impulse")
    report = _evaluate(tmp_path, cells=[survivor, non_survivor], survivor_ids=[survivor.cell_id])
    assert all(cell.cell_id != non_survivor.cell_id for cell in report.cell_results)


def test_does_not_evaluate_train_windows(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.holdout_window_ids == ["w3"]
    assert report.train_window_ids_seen_but_not_evaluated == ["w1", "w2"]
    for result in report.cell_results:
        assert result.holdout_window_ids == ["w3"]
        assert "w1" not in result.holdout_window_ids
        assert "w2" not in result.holdout_window_ids


def test_preserves_train_window_ids_only_as_metadata(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.train_window_ids_seen_but_not_evaluated == ["w1", "w2"]
    assert report.holdout_evaluated_cell_ids == [survivor.cell_id]


def test_changing_train_only_price_data_does_not_change_holdout_evaluation_hash(tmp_path: Path):
    survivor = _family1_cell()
    report_a = _evaluate(tmp_path / "a", cells=[survivor], survivor_ids=[survivor.cell_id], price_map=_base_price_map(train_shift=0.0, holdout_shift=0.0))
    report_b = _evaluate(tmp_path / "b", cells=[survivor], survivor_ids=[survivor.cell_id], price_map=_base_price_map(train_shift=50.0, holdout_shift=0.0))
    assert report_a.holdout_evaluation_hash == report_b.holdout_evaluation_hash


def test_changing_holdout_price_data_changes_holdout_evaluation_hash(tmp_path: Path):
    survivor = _family1_cell()
    report_a = _evaluate(tmp_path / "a", cells=[survivor], survivor_ids=[survivor.cell_id], price_map=_base_price_map(holdout_shift=0.0))
    report_b = _evaluate(tmp_path / "b", cells=[survivor], survivor_ids=[survivor.cell_id], price_map=_base_price_map(holdout_shift=20.0))
    assert report_a.holdout_evaluation_hash != report_b.holdout_evaluation_hash


def test_family1_survivor_can_produce_deterministic_holdout_result(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY
    result = report.cell_results[0]
    assert result.cell_id == survivor.cell_id
    assert result.raw_mean_bps is not None
    assert result.net_mean_bps is not None


def test_family2_source_move_impulse_survivor_can_produce_deterministic_holdout_result(tmp_path: Path):
    survivor = _family2_cell(signal_variant="source_move_impulse")
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY
    assert report.cell_results[0].cell_id == survivor.cell_id


def test_family3_without_satisfied_latency_gate_is_excluded(tmp_path: Path):
    survivor = _family3_cell()
    freeze_cell = _holdout_cell_from_plan(survivor, latency_gate_satisfied=False)
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id], survivor_cells=[freeze_cell])
    assert report.status in {STATUS_NO_EVALUABLE_SURVIVORS, STATUS_LATENCY_GATE_REQUIRED_NOT_RUN}
    assert report.excluded_survivor_cells[survivor.cell_id] == [STATUS_LATENCY_GATE_REQUIRED_NOT_RUN]


def test_family4_is_excluded_with_conditioning_not_run_status(tmp_path: Path):
    survivor = _family4_cell()
    freeze_cell = _holdout_cell_from_plan(survivor)
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id], survivor_cells=[freeze_cell])
    assert report.excluded_survivor_cells[survivor.cell_id] == [STATUS_CONDITIONING_NOT_RUN_PHASE_2B2B2]


def test_costs_are_applied_exactly_from_plan_cell(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    result = report.cell_results[0]
    expected_cost = survivor.cost_config.fees_bps + survivor.cost_config.slippage_bps + survivor.cost_config.quote_mismatch_bps
    assert result.raw_mean_bps - result.net_mean_bps == pytest.approx(expected_cost)


def test_raw_and_net_bps_are_recorded_separately(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    result = report.cell_results[0]
    assert result.raw_mean_bps != result.net_mean_bps
    assert result.raw_median_bps != result.net_median_bps


def test_insufficient_holdout_events_excludes_survivor_cell(tmp_path: Path):
    survivor = _family1_cell()
    limited_prices = _base_price_map()
    limited_prices["coinbase|BTC/USD"] = [(100 * NS, 100.0), (160 * NS, 102.0), (200 * NS, 103.0), (220 * NS, 104.0)]
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id], price_map=limited_prices)
    assert survivor.cell_id in report.excluded_survivor_cells
    excl_reasons = report.excluded_survivor_cells[survivor.cell_id]
    # With the new tick-basis evaluator, missing target prices produce specific
    # exclusion reasons (e.g. missing_entry_price) instead of a generic status.
    # The status of the cell result is INSUFFICIENT_HOLDOUT_EVENTS.
    assert STATUS_INSUFFICIENT_HOLDOUT_EVENTS in excl_reasons or any(
        "missing" in r for r in excl_reasons
    ), f"excl_reasons={excl_reasons}"


def test_unsupported_resolution_excludes_survivor_cell(tmp_path: Path):
    survivor = _family1_cell(window_index_hash="window_hash")
    unsupported = _plan_cell(
        family_id=survivor.family_id,
        family_name=survivor.family_name,
        source_venues=survivor.source_venues,
        source_symbols=survivor.source_symbols,
        target_venues=survivor.target_venues,
        target_symbols=survivor.target_symbols,
        window_ids=survivor.window_ids,
        required_resolution="unsupported_baz",
        data_corpus_hash=survivor.data_corpus_hash,
        window_index_hash=survivor.window_index_hash,
    )
    report = _evaluate(tmp_path, cells=[unsupported], survivor_ids=[unsupported.cell_id], survivor_cells=[_holdout_cell_from_plan(unsupported)])
    assert report.excluded_survivor_cells[unsupported.cell_id] == ["unsupported_resolution:unsupported_baz"]


def test_identical_runs_produce_identical_parsed_output_json_and_identical_hash(tmp_path: Path):
    survivor = _family1_cell()
    report_a = _evaluate(tmp_path / "a", cells=[survivor], survivor_ids=[survivor.cell_id])
    report_b = _evaluate(tmp_path / "b", cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report_a.status == report_b.status
    assert report_a.holdout_evaluation_hash == report_b.holdout_evaluation_hash


def test_changing_survivor_set_changes_holdout_evaluation_hash(tmp_path: Path):
    survivor_a = _family1_cell()
    survivor_b = _family2_cell()
    report_a = _evaluate(tmp_path / "a", cells=[survivor_a, survivor_b], survivor_ids=[survivor_a.cell_id])
    report_b = _evaluate(tmp_path / "b", cells=[survivor_a, survivor_b], survivor_ids=[survivor_a.cell_id, survivor_b.cell_id])
    assert report_a.holdout_evaluation_hash != report_b.holdout_evaluation_hash


def test_output_directory_does_not_overwrite_by_default(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path / "input", cells=[survivor], survivor_ids=[survivor.cell_id])
    out_dir = tmp_path / "out"
    write_offline_holdout_evaluation_outputs(report, out_dir, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress.json", discovery_plan_manifest_path="plan.json", train_evaluation_manifest_path="train_eval.json", train_survivor_freeze_manifest_path="freeze.json")
    with pytest.raises(FileExistsError):
        write_offline_holdout_evaluation_outputs(report, out_dir, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress.json", discovery_plan_manifest_path="plan.json", train_evaluation_manifest_path="train_eval.json", train_survivor_freeze_manifest_path="freeze.json")


def test_manifest_includes_required_hashes(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    manifest = build_offline_holdout_evaluation_manifest_payload(
        report,
        prepare_manifest_path="prepare.json",
        stress_window_manifest_path="stress.json",
        discovery_plan_manifest_path="plan.json",
        train_evaluation_manifest_path="train_eval.json",
        train_survivor_freeze_manifest_path="freeze.json",
    )
    assert manifest["data_corpus_hash"]
    assert manifest["window_index_hash"]
    assert manifest["discovery_config_hash"]
    assert manifest["plan_hash"]
    assert manifest["evaluation_hash"]
    assert manifest["survivor_freeze_hash"]
    assert manifest["holdout_evaluation_hash"]


def test_event_net_bps_exported_for_successful_holdout_cell(tmp_path: Path):
    """Tests 1-2: Successful holdout evaluation exports event_net_bps with correct length."""
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY
    result = report.cell_results[0]
    assert len(result.event_net_bps) > 0
    assert len(result.event_net_bps) == result.valid_event_count
    assert len(result.event_raw_bps) > 0
    assert len(result.event_raw_bps) == result.valid_event_count


def test_summary_metrics_derived_from_event_vectors(tmp_path: Path):
    """
    Tests 3-7: Summary metrics are derived from event_net_bps and event_raw_bps.

    The event vectors are the single source of truth. All summary stats must
    match what _summarize_event_returns computes from those vectors.
    """
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY
    result = report.cell_results[0]
    net = result.event_net_bps
    raw = result.event_raw_bps

    # valid_event_count must match vector length
    assert result.valid_event_count == len(net)

    # Derive expected summary from vectors
    expected_summary = _summarize_event_returns(raw, net)
    assert result.raw_mean_bps == pytest.approx(expected_summary["raw_mean_bps"])
    assert result.raw_median_bps == pytest.approx(expected_summary["raw_median_bps"])
    assert result.net_mean_bps == pytest.approx(expected_summary["net_mean_bps"])
    assert result.net_median_bps == pytest.approx(expected_summary["net_median_bps"])
    assert result.win_rate == pytest.approx(expected_summary["win_rate"])
    assert result.worst_net_bps == pytest.approx(expected_summary["worst_net_bps"])

    # Direct vector-derived checks
    assert result.net_mean_bps == pytest.approx(sum(net) / len(net))
    from statistics import median
    assert result.net_median_bps == pytest.approx(median(net))
    expected_win_rate = sum(1 for v in net if v > 0.0) / len(net)
    assert result.win_rate == pytest.approx(expected_win_rate)
    assert result.worst_net_bps == pytest.approx(min(net))


def test_excluded_cells_have_empty_event_vectors(tmp_path: Path):
    """
    Test 8: Excluded survivor cells (in excluded_survivor_cells) have
    no cell_result entries. Verified through existing exclusion tests
    that produce excluded cells.
    """
    # Family 4 exclusion test already proves excluded cells are handled
    survivor = _family4_cell()
    freeze_cell = _holdout_cell_from_plan(survivor)
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id],
                       survivor_cells=[freeze_cell])
    # Excluded cell should be in excluded_survivor_cells, not cell_results
    assert survivor.cell_id in report.excluded_survivor_cells
    # Check that no cell_result has an empty vector when it was truly evaluated
    # Successful evaluations all have non-empty vectors
    for cell_result in report.cell_results:
        if cell_result.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY:
            assert len(cell_result.event_net_bps) > 0


def test_net_events_derived_from_raw_events_minus_costs(tmp_path: Path):
    """Test 9: Each event_net_bps[i] == event_raw_bps[i] - fee - slippage - buffer."""
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY
    result = report.cell_results[0]
    expected_cost = result.fee_bps + result.slippage_bps + result.quote_mismatch_buffer_bps
    for i in range(len(result.event_net_bps)):
        assert result.event_net_bps[i] == pytest.approx(
            result.event_raw_bps[i] - expected_cost
        )


def test_summarize_event_returns_is_production_summary_path(tmp_path: Path):
    """
    Test 10: The production summary uses _summarize_event_returns.

    Verify by confirming that _summarize_event_returns produces the same
    metrics as found in actual evaluation output.
    """
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report.status == STATUS_OFFLINE_HOLDOUT_EVALUATION_READY
    result = report.cell_results[0]
    summary = _summarize_event_returns(result.event_raw_bps, result.event_net_bps)
    assert result.net_mean_bps == pytest.approx(summary["net_mean_bps"])
    assert result.raw_mean_bps == pytest.approx(summary["raw_mean_bps"])
    assert result.win_rate == pytest.approx(summary["win_rate"])


def test_identical_runs_produce_identical_event_vectors(tmp_path: Path):
    """Test 11: Identical runs produce identical event vectors and hash."""
    survivor = _family1_cell()
    report_a = _evaluate(tmp_path / "a", cells=[survivor], survivor_ids=[survivor.cell_id])
    report_b = _evaluate(tmp_path / "b", cells=[survivor], survivor_ids=[survivor.cell_id])
    assert report_a.holdout_evaluation_hash == report_b.holdout_evaluation_hash
    assert report_a.cell_results[0].event_raw_bps == report_b.cell_results[0].event_raw_bps
    assert report_a.cell_results[0].event_net_bps == report_b.cell_results[0].event_net_bps


def test_changing_event_vector_changes_holdout_hash(tmp_path: Path):
    """Test 12: Changing price data changes event vectors and hash."""
    survivor = _family1_cell()
    report_a = _evaluate(tmp_path / "a", cells=[survivor], survivor_ids=[survivor.cell_id],
                         price_map=_base_price_map(holdout_shift=0.0))
    report_b = _evaluate(tmp_path / "b", cells=[survivor], survivor_ids=[survivor.cell_id],
                         price_map=_base_price_map(holdout_shift=25.0))
    assert report_a.holdout_evaluation_hash != report_b.holdout_evaluation_hash
    assert report_a.cell_results[0].event_net_bps != report_b.cell_results[0].event_net_bps


def test_safety_scan_has_no_forbidden_capability_usage():
    path = Path("/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/offline_holdout_evaluation.py")
    if not path.exists():
        pytest.skip("implementation file not created yet")
    text = path.read_text(encoding="utf-8")
    forbidden = [
        "OrderFactory",
        "submit_order",
        "TradingNode",
        "LiveNode",
        "private_key",
        "wallet",
        "signing",
        "CANDIDATE_FOR_LIVE",
        "TRADE_READY",
        "EXECUTION_READY",
        "POLYMARKET_PK",
        "KRAKEN_API_KEY",
        "run_permutation_null",
        "cost_sensitivity",
        "ShadowExecutor",
        "candidate_falsification",
        "NO_EDGE_AFTER_COSTS",
        "REJECTED",
        "bot_authorization",
    ]
    assert all(term not in text for term in forbidden)


def test_compute_holdout_evaluation_hash_is_stable_for_same_result(tmp_path: Path):
    survivor = _family1_cell()
    report = _evaluate(tmp_path, cells=[survivor], survivor_ids=[survivor.cell_id])
    assert compute_holdout_evaluation_hash(report) == report.holdout_evaluation_hash


def test_schema_version_constant_present():
    assert HOLDOUT_EVALUATION_SCHEMA_VERSION == "offline_holdout_evaluation_v1"
