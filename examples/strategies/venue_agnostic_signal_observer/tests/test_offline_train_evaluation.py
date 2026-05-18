from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_corpus_hash import compute_data_corpus_hash
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_BAR,
    RESOLUTION_TRADE,
    WINDOW_MODE_CAUSAL,
    OfflinePrepareManifest,
    OfflineSourceFile,
)
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import (
    DISCOVERY_SCHEMA_VERSION,
    CostConfig,
    OfflineDiscoveryPlan,
    OfflineDiscoveryPlanCell,
    compute_window_index_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
    STATUS_OFFLINE_STRESS_INDEX_READY,
    OfflineStressWindow,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_evaluation import (
    EVALUATION_SCHEMA_VERSION,
    STATUS_CONDITIONING_NOT_RUN_PHASE_2B2A,
    STATUS_DISCOVERY_PLAN_HASH_MISMATCH,
    STATUS_INPUT_HASH_MISMATCH,
    STATUS_INSUFFICIENT_TRAIN_EVENTS,
    STATUS_LATENCY_GATE_REQUIRED_NOT_RUN,
    STATUS_OFFLINE_TRAIN_EVALUATION_READY,
    STATUS_TRAIN_RAW_EDGE_SCREEN_READY,
    STATUS_UNSUPPORTED_PLAN_CELL,
    STATUS_WINDOW_INDEX_HASH_MISMATCH,
    build_offline_train_evaluation_manifest_payload,
    build_offline_train_evaluation_report,
    compute_evaluation_hash,
    write_offline_train_evaluation_outputs,
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
) -> OfflineSourceFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture", encoding="utf-8")
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
        file_size_bytes=7,
        mtime_ns=1,
        file_sha256=logical_source_id,
        row_count=6,
        data_start_ns=0,
        data_end_ns=600 * NS,
    )


def _prepare_manifest(tmp_path: Path, *, data_corpus_hash: str | None = None, precommitment_hash: str | None = None) -> OfflinePrepareManifest:
    sources = [
        _source_file(tmp_path / "kraken_btcusd.csv", logical_source_id="kraken-btcusd", venue="kraken", symbol="BTC/USD", base_asset="BTC", quote_asset="USD"),
        _source_file(tmp_path / "kraken_btcusdt.csv", logical_source_id="kraken-btcusdt", venue="kraken", symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT"),
        _source_file(tmp_path / "binance_btcusdt.csv", logical_source_id="binance-btcusdt", venue="binance", symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT"),
        _source_file(tmp_path / "binance_ethusdt.csv", logical_source_id="binance-ethusdt", venue="binance", symbol="ETH/USDT", base_asset="ETH", quote_asset="USDT"),
        _source_file(tmp_path / "binance_solusdt.csv", logical_source_id="binance-solusdt", venue="binance", symbol="SOL/USDT", base_asset="SOL", quote_asset="USDT"),
        _source_file(tmp_path / "coinbase_btcusd.csv", logical_source_id="coinbase-btcusd", venue="coinbase", symbol="BTC/USD", base_asset="BTC", quote_asset="USD"),
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


def _stress_manifest(windows: list[OfflineStressWindow], *, data_corpus_hash: str, window_index_hash: str = "window_hash", precommitment_hash: str | None = None) -> dict:
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


def _plan(cells: list[OfflineDiscoveryPlanCell], *, data_corpus_hash: str, window_index_hash: str, plan_hash: str = "plan_hash") -> OfflineDiscoveryPlan:
    edge_count = len([cell for cell in cells if cell.is_edge_family])
    conditioning_count = len([cell for cell in cells if cell.is_conditioning_family])
    return OfflineDiscoveryPlan(
        status="OFFLINE_DISCOVERY_PLAN_READY",
        plan_cells=cells,
        excluded_windows=[],
        family_summary={"train_window_count": 2, "holdout_window_count": 1, "split_timestamp_boundary_ns": 200 * NS},
        train_window_ids=["w1", "w2"],
        holdout_window_ids=["w3"],
        edge_family_cell_count=edge_count,
        conditioning_cell_count=conditioning_count,
        discovery_config_hash="discovery_hash",
        plan_hash=plan_hash,
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        split_timestamp_boundary_ns=200 * NS,
        train_survivor_cell_ids=[],
        holdout_evaluation_cell_ids=[],
        survivor_freeze_status="NOT_RUN_PHASE_2B1",
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
        "survivor_freeze_status": "NOT_RUN_PHASE_2B1",
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
    }


def _source_config(tmp_path: Path, price_map: dict[str, list[tuple[int, float]]]) -> Path:
    payload = {"sources": []}
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, rows in sorted(price_map.items()):
        file_path = tmp_path / f"{name.replace('/', '_').replace('|', '__')}.json"
        file_path.write_text(json.dumps([{"timestamp_ns": ts, "close": price} for ts, price in rows]), encoding="utf-8")
        venue, symbol = name.split("|", 1)
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


def _price_map(train_positive: bool = True, *, holdout_shift: float = 0.0) -> dict[str, list[tuple[int, float]]]:
    target_train_second = 105.0 if train_positive else 100.0
    return {
        "kraken|BTC/USD": [
            (0 * NS, 100.0),
            (100 * NS, 100.0),
            (160 * NS, 102.0),
            (200 * NS, 103.0),
            (220 * NS, target_train_second),
            (280 * NS, 106.0),
            (340 * NS, 108.0),
        ],
        "kraken|BTC/USDT": [
            (0 * NS, 102.0 if train_positive else 100.0),
            (100 * NS, 100.5),
            (160 * NS, 100.0),
            (200 * NS, 99.0 if train_positive else 103.0),
            (220 * NS, 103.0),
            (280 * NS, 103.5),
            (340 * NS, 104.0 + holdout_shift),
        ],
        "binance|BTC/USDT": [(100 * NS, 100.0), (160 * NS, 103.0), (200 * NS, 104.0), (220 * NS, 105.0), (280 * NS, 106.0), (340 * NS, 107.0 + holdout_shift)],
        "binance|ETH/USDT": [(100 * NS, 50.0), (160 * NS, 51.0), (200 * NS, 52.0), (220 * NS, 53.0), (280 * NS, 53.5), (340 * NS, 54.0 + holdout_shift)],
        "binance|SOL/USDT": [(100 * NS, 10.0), (160 * NS, 10.3), (200 * NS, target_train_second / 10.0), (220 * NS, 10.5), (280 * NS, 10.8), (340 * NS, 11.1 + holdout_shift)],
        "coinbase|BTC/USD": [(100 * NS, 100.0), (160 * NS, 102.0), (200 * NS, 103.0), (220 * NS, 104.0), (280 * NS, 105.0), (340 * NS, 106.0 + holdout_shift)],
    }


def _evaluate(
    tmp_path: Path,
    *,
    cells: list[OfflineDiscoveryPlanCell],
    price_map: dict[str, list[tuple[int, float]]],
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str | None = None,
    plan_hash: str | None = None,
    mismatch_data_corpus_hash: bool = False,
    mismatch_window_index_hash: bool = False,
    mismatch_plan_hash: bool = False,
):
    canonical_stress_windows = [
        _window("w1", 100 * NS, data_corpus_hash=data_corpus_hash),
        _window("w2", 200 * NS, data_corpus_hash=data_corpus_hash),
        _window("w3", 300 * NS, data_corpus_hash=data_corpus_hash),
    ]
    canonical_window_index_hash = compute_window_index_hash(_stress_payload(canonical_stress_windows))
    canonical_plan_hash = plan_hash or "plan_hash"
    plan = _plan(cells, data_corpus_hash=data_corpus_hash, window_index_hash=canonical_window_index_hash, plan_hash=canonical_plan_hash)
    report_data_hash = data_corpus_hash
    if mismatch_data_corpus_hash:
        report_data_hash = f"{data_corpus_hash}_report"

    stress_manifest_hash = canonical_window_index_hash
    discovery_manifest_hash = canonical_window_index_hash
    if mismatch_window_index_hash:
        stress_manifest_hash = window_index_hash or "wrong_hash"
        discovery_manifest_hash = stress_manifest_hash

    discovery_manifest_plan_hash = canonical_plan_hash
    if mismatch_plan_hash:
        discovery_manifest_plan_hash = f"{canonical_plan_hash}_mismatch"

    return build_offline_train_evaluation_report(
        prepare_manifest=_prepare_manifest(tmp_path / "prepare", data_corpus_hash=report_data_hash),
        stress_window_manifest=_stress_manifest(canonical_stress_windows, data_corpus_hash=data_corpus_hash, window_index_hash=stress_manifest_hash),
        stress_windows_payload=_stress_payload(canonical_stress_windows),
        discovery_plan_manifest=_discovery_manifest(plan, data_corpus_hash=data_corpus_hash, window_index_hash=discovery_manifest_hash, plan_hash=discovery_manifest_plan_hash),
        discovery_plan_payload=_discovery_payload(plan, data_corpus_hash=data_corpus_hash, window_index_hash=canonical_window_index_hash, plan_hash=canonical_plan_hash),
        discovery_plan=plan,
        source_config_path=_source_config(tmp_path / "sources", price_map),
    )


def test_rejects_data_corpus_hash_mismatch_across_inputs(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1",),
        data_corpus_hash="corpus_hash",
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map(), mismatch_data_corpus_hash=True)
    assert report.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_window_index_hash_mismatch(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1",),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map(), mismatch_window_index_hash=True, window_index_hash="wrong_hash")
    assert report.status == STATUS_WINDOW_INDEX_HASH_MISMATCH


def test_rejects_discovery_plan_hash_mismatch(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1",),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map(), mismatch_plan_hash=True, plan_hash="mismatch_plan_hash")
    assert report.status == STATUS_DISCOVERY_PLAN_HASH_MISMATCH


def test_does_not_evaluate_holdout_windows(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2", "w3"),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.status in {STATUS_OFFLINE_TRAIN_EVALUATION_READY, STATUS_TRAIN_RAW_EDGE_SCREEN_READY}
    assert report.cell_results[0].train_window_ids == ["w1", "w2"]
    assert "w3" not in report.cell_results[0].train_window_ids


def test_holdout_ids_preserved_only_as_metadata(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2", "w3"),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.holdout_window_ids_seen_but_not_evaluated == ["w3"]
    assert all("w3" not in result.train_window_ids for result in report.cell_results)


def test_changing_holdout_only_price_data_does_not_change_train_evaluation_hash(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2", "w3"),
        required_resolution=RESOLUTION_TRADE,
    )
    report_a = _evaluate(tmp_path / "a", cells=[cell], price_map=_price_map())
    report_b = _evaluate(tmp_path / "b", cells=[cell], price_map=_price_map(holdout_shift=1000.0))
    assert report_a.evaluation_hash == report_b.evaluation_hash


def test_changing_train_price_data_changes_train_evaluation_hash(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
        required_resolution=RESOLUTION_TRADE,
    )
    report_a = _evaluate(tmp_path / "a", cells=[cell], price_map=_price_map(train_positive=True))
    report_b = _evaluate(tmp_path / "b", cells=[cell], price_map=_price_map(train_positive=False))
    assert report_a.evaluation_hash != report_b.evaluation_hash


def test_family1_plan_cell_can_produce_deterministic_train_cell_result_from_synthetic_data(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
        cost_config=CostConfig(1.0, 2.0, 3.0),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    result = report.cell_results[0]
    assert result.family_id == "family_1_same_venue_quote_basis"
    assert result.evaluated_event_count == 2
    assert result.valid_event_count == 2
    assert result.raw_mean_bps is not None
    assert result.net_mean_bps == pytest.approx(result.raw_mean_bps - 6.0)


def test_family2_source_move_impulse_plan_cell_can_produce_deterministic_train_cell_result_from_synthetic_data(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_2_cross_asset_stress_beta_lag",
        family_name="Family 2",
        source_venues=("binance",),
        source_symbols=("BTC/USDT",),
        target_venues=("binance",),
        target_symbols=("SOL/USDT",),
        signal_variant="source_move_impulse",
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    result = report.cell_results[0]
    assert result.family_id == "family_2_cross_asset_stress_beta_lag"
    assert result.signal_variant == "source_move_impulse"
    assert result.valid_event_count == 2


def test_family2_unavailable_trade_flow_variant_is_excluded_with_clear_reason_when_required_data_missing(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_2_cross_asset_stress_beta_lag",
        family_name="Family 2",
        source_venues=("binance",),
        source_symbols=("BTC/USDT",),
        target_venues=("binance",),
        target_symbols=("SOL/USDT",),
        signal_variant="signed_imbalance",
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.cell_results[0].status == STATUS_UNSUPPORTED_PLAN_CELL
    assert "requires_trade_flow_data" in report.cell_results[0].exclusion_reasons


def test_family3_is_excluded_with_latency_gate_required_not_run(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_3_usd_reference_translation_lag",
        family_name="Family 3",
        source_venues=("coinbase",),
        source_symbols=("BTC/USD",),
        target_venues=("binance",),
        target_symbols=("BTC/USDT",),
        latency_gate_required=True,
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.cell_results[0].status == STATUS_LATENCY_GATE_REQUIRED_NOT_RUN


def test_family4_is_excluded_with_conditioning_not_run_phase_2b2a(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_4_stablecoin_quote_regime_conditioning",
        family_name="Family 4",
        source_venues=("kraken",),
        source_symbols=("BTC/USD",),
        target_venues=(),
        target_symbols=("family_1_same_venue_quote_basis",),
        window_ids=("w1", "w2"),
        is_edge_family=False,
        is_conditioning_family=True,
        promotion_allowed=False,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.cell_results[0].status == STATUS_CONDITIONING_NOT_RUN_PHASE_2B2A


def test_costs_are_applied_exactly_from_plan_cell(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
        cost_config=CostConfig(4.0, 5.0, 6.0),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    result = report.cell_results[0]
    assert result.fee_bps == 4.0
    assert result.slippage_bps == 5.0
    assert result.quote_mismatch_buffer_bps == 6.0
    assert result.net_mean_bps == pytest.approx(result.raw_mean_bps - 15.0)


def test_raw_and_net_bps_are_recorded_separately(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    result = report.cell_results[0]
    assert result.raw_mean_bps != result.net_mean_bps
    assert result.raw_median_bps != result.net_median_bps


def test_insufficient_train_events_excludes_a_cell(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1",),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.cell_results[0].status == STATUS_INSUFFICIENT_TRAIN_EVENTS


def test_unsupported_resolution_excludes_a_cell(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
        required_resolution="unsupported_foo",
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.cell_results[0].status == STATUS_UNSUPPORTED_PLAN_CELL
    assert "unsupported_resolution:unsupported_foo" in report.cell_results[0].exclusion_reasons


def test_raw_train_screen_can_populate_when_cell_has_positive_net_mean_and_enough_events(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
        required_resolution=RESOLUTION_TRADE,
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map(train_positive=True))
    assert report.raw_train_screen_cell_ids == [cell.cell_id]
    assert report.status == STATUS_TRAIN_RAW_EDGE_SCREEN_READY



def test_train_survivor_cell_ids_remain_empty(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.train_survivor_cell_ids == []


def test_survivor_freeze_status_is_not_run_phase_2b2a(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert report.survivor_freeze_status == "NOT_RUN_PHASE_2B2A"


def test_identical_runs_produce_identical_parsed_output_json_and_identical_evaluation_hash(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report_a = _evaluate(tmp_path / "a", cells=[cell], price_map=_price_map())
    report_b = _evaluate(tmp_path / "b", cells=[cell], price_map=_price_map())
    out_a = write_offline_train_evaluation_outputs(report_a, tmp_path / "out_a", prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", discovery_plan_manifest_path="discovery_manifest.json")
    out_b = write_offline_train_evaluation_outputs(report_b, tmp_path / "out_b", prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", discovery_plan_manifest_path="discovery_manifest.json")
    assert json.loads(out_a["report_path"].read_text(encoding="utf-8")) == json.loads(out_b["report_path"].read_text(encoding="utf-8"))
    assert report_a.evaluation_hash == report_b.evaluation_hash


def test_changing_plan_hash_changes_evaluation_hash(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report_a = _evaluate(tmp_path / "a", cells=[cell], price_map=_price_map(), plan_hash="plan_a")
    report_b = _evaluate(tmp_path / "b", cells=[cell], price_map=_price_map(), plan_hash="plan_b")
    assert report_a.evaluation_hash != report_b.evaluation_hash


def test_output_directory_does_not_overwrite_by_default(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    out_dir = tmp_path / "out"
    write_offline_train_evaluation_outputs(report, out_dir, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", discovery_plan_manifest_path="discovery_manifest.json")
    with pytest.raises(FileExistsError):
        write_offline_train_evaluation_outputs(report, out_dir, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", discovery_plan_manifest_path="discovery_manifest.json")


def test_manifest_includes_required_hashes(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    manifest = build_offline_train_evaluation_manifest_payload(report, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", discovery_plan_manifest_path="discovery_manifest.json")
    assert manifest["data_corpus_hash"]
    assert manifest["window_index_hash"]
    assert manifest["discovery_config_hash"]
    assert manifest["plan_hash"]
    assert manifest["evaluation_hash"]


def test_compute_evaluation_hash_is_stable_for_same_report(tmp_path: Path):
    cell = _plan_cell(
        family_id="family_1_same_venue_quote_basis",
        family_name="Family 1",
        source_venues=("kraken",),
        source_symbols=("BTC/USD", "BTC/USDT"),
        target_venues=("kraken",),
        target_symbols=("BTC/USD",),
        window_ids=("w1", "w2"),
    )
    report = _evaluate(tmp_path, cells=[cell], price_map=_price_map())
    assert compute_evaluation_hash(report) == report.evaluation_hash


def test_schema_version_constant_present():
    assert EVALUATION_SCHEMA_VERSION == "offline_train_evaluation_v1"
