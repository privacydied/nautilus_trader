from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    FREEZE_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    STATUS_INPUT_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    STATUS_NO_RAW_TRAIN_SCREEN_CELLS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    STATUS_NO_TRAIN_SURVIVORS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    STATUS_OFFLINE_TRAIN_SURVIVOR_FREEZE_READY,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    STATUS_UNUSABLE_TRAIN_EVALUATION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    OfflineTrainSurvivorFreezeConfig,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    build_offline_train_survivor_freeze_manifest_payload,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    build_offline_train_survivor_freeze_result,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    compute_survivor_freeze_config_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    compute_survivor_freeze_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_survivor_freeze import (
    write_offline_train_survivor_freeze_outputs,
)


def _cell_result(
    cell_id: str,
    *,
    status: str = "OFFLINE_TRAIN_EVALUATION_READY",
    family_id: str = "family_1_same_venue_quote_basis",
    family_name: str = "Family 1",
    signal_variant: str | None = None,
    lookback_ms: int = 60_000,
    horizon_ms: int = 60_000,
    valid_event_count: int = 3,
    net_mean_bps: float | None = 1.0,
    net_median_bps: float | None = 1.0,
    win_rate: float | None = 2 / 3,
    worst_net_bps: float | None = 0.25,
    is_edge_family: bool = True,
    latency_gate_required: bool = False,
    latency_gate_satisfied: bool | None = None,
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    plan_hash: str = "plan_hash",
) -> dict:
    payload = {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "is_edge_family": is_edge_family,
        "status": status,
        "train_window_ids": ["w1", "w2"],
        "evaluated_event_count": valid_event_count,
        "valid_event_count": valid_event_count,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "signal_variant": signal_variant,
        "required_resolution": "bar",
        "latency_gate_required": latency_gate_required,
        "raw_mean_bps": None if net_mean_bps is None else net_mean_bps + 6.0,
        "raw_median_bps": None if net_median_bps is None else net_median_bps + 6.0,
        "net_mean_bps": net_mean_bps,
        "net_median_bps": net_median_bps,
        "win_rate": win_rate,
        "worst_net_bps": worst_net_bps,
        "fee_bps": 1.0,
        "slippage_bps": 2.0,
        "quote_mismatch_buffer_bps": 3.0,
        "exclusion_reasons": [],
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "plan_hash": plan_hash,
    }
    if latency_gate_satisfied is not None:
        payload["latency_gate_satisfied"] = latency_gate_satisfied
    return payload


def _train_evaluation_payload(
    cells: list[dict],
    *,
    status: str = "TRAIN_RAW_EDGE_SCREEN_READY",
    raw_train_screen_cell_ids: list[str] | None = None,
    holdout_window_ids: list[str] | None = None,
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
) -> dict:
    return {
        "schema_version": "offline_train_evaluation_v1",
        "status": status,
        "cell_results": cells,
        "excluded_cells": [],
        "train_window_ids": ["w1", "w2"],
        "holdout_window_ids_seen_but_not_evaluated": holdout_window_ids or ["h2", "h1"],
        "raw_train_screen_cell_ids": raw_train_screen_cell_ids if raw_train_screen_cell_ids is not None else [cell["cell_id"] for cell in cells],
        "train_survivor_cell_ids": [],
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "evaluation_hash": evaluation_hash,
        "metadata": {"holdout_window_ids_seen_but_not_evaluated": holdout_window_ids or ["h2", "h1"]},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
    }


def _train_evaluation_manifest(
    payload: dict,
    *,
    status: str | None = None,
    evaluation_hash: str | None = None,
    data_corpus_hash: str | None = None,
    window_index_hash: str | None = None,
    discovery_config_hash: str | None = None,
    plan_hash: str | None = None,
) -> dict:
    return {
        "run_id": "train_eval_run",
        "phase": "offline_train_evaluation",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": payload["schema_version"],
        "train_evaluation_path": "offline_train_evaluation.json",
        "data_corpus_hash": data_corpus_hash or payload["data_corpus_hash"],
        "precommitment_hash": None,
        "window_index_hash": window_index_hash or payload["window_index_hash"],
        "discovery_config_hash": discovery_config_hash or payload["discovery_config_hash"],
        "plan_hash": plan_hash or payload["plan_hash"],
        "evaluation_hash": evaluation_hash or payload["evaluation_hash"],
        "status": status or payload["status"],
        "raw_train_screen_cell_count": len(payload["raw_train_screen_cell_ids"]),
        "train_survivor_cell_count": 0,
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "safety": "public_data_observer_only",
    }


def _freeze(
    *,
    cells: list[dict],
    config: OfflineTrainSurvivorFreezeConfig | None = None,
    status: str = "TRAIN_RAW_EDGE_SCREEN_READY",
    raw_train_screen_cell_ids: list[str] | None = None,
    holdout_window_ids: list[str] | None = None,
    manifest_overrides: dict | None = None,
    payload_overrides: dict | None = None,
):
    payload = _train_evaluation_payload(
        cells,
        status=status,
        raw_train_screen_cell_ids=raw_train_screen_cell_ids,
        holdout_window_ids=holdout_window_ids,
    )
    if payload_overrides:
        payload.update(payload_overrides)
    manifest = _train_evaluation_manifest(payload)
    if manifest_overrides:
        manifest.update(manifest_overrides)
    return build_offline_train_survivor_freeze_result(
        train_evaluation_manifest=manifest,
        train_evaluation_payload=payload,
        freeze_config=config or OfflineTrainSurvivorFreezeConfig(),
    )


def test_rejects_train_evaluation_manifest_json_evaluation_hash_mismatch():
    result = _freeze(cells=[_cell_result("cell_a")], manifest_overrides={"evaluation_hash": "other_hash"})
    assert result.status == STATUS_TRAIN_EVALUATION_HASH_MISMATCH


def test_rejects_mismatched_lineage_hashes_between_manifest_and_json():
    result = _freeze(cells=[_cell_result("cell_a")], manifest_overrides={"plan_hash": "other_plan"})
    assert result.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_unusable_train_evaluation_status():
    result = _freeze(cells=[_cell_result("cell_a")], status="OFFLINE_DISCOVERY_PLAN_READY")
    assert result.status == STATUS_UNUSABLE_TRAIN_EVALUATION


def test_produces_no_raw_train_screen_cells_when_raw_screen_is_empty():
    result = _freeze(cells=[_cell_result("cell_a")], status="TRAIN_RAW_EDGE_SCREEN_EMPTY", raw_train_screen_cell_ids=[])
    assert result.status == STATUS_NO_RAW_TRAIN_SCREEN_CELLS
    assert result.train_survivor_cell_ids == []


def test_produces_no_train_survivors_when_raw_screen_exists_but_thresholds_fail():
    result = _freeze(cells=[_cell_result("cell_a", net_mean_bps=-0.1)], raw_train_screen_cell_ids=["cell_a"])
    assert result.status == STATUS_NO_TRAIN_SURVIVORS
    assert result.train_survivor_cell_ids == []


def test_freezes_a_survivor_when_all_train_thresholds_pass():
    result = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    assert result.status == STATUS_OFFLINE_TRAIN_SURVIVOR_FREEZE_READY
    assert result.train_survivor_cell_ids == ["cell_a"]
    assert result.survivor_cells[0].freeze_rank == 1


def test_rejects_cell_when_valid_event_count_below_threshold():
    result = _freeze(
        cells=[_cell_result("cell_a", valid_event_count=1)],
        raw_train_screen_cell_ids=["cell_a"],
        config=OfflineTrainSurvivorFreezeConfig(min_valid_events=2),
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["below_min_valid_events"]


def test_rejects_cell_when_net_mean_bps_below_threshold():
    result = _freeze(
        cells=[_cell_result("cell_a", net_mean_bps=-0.01)],
        raw_train_screen_cell_ids=["cell_a"],
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["below_min_net_mean_bps"]


def test_rejects_cell_when_net_median_bps_below_threshold():
    result = _freeze(
        cells=[_cell_result("cell_a", net_median_bps=-0.01)],
        raw_train_screen_cell_ids=["cell_a"],
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["below_min_net_median_bps"]


def test_rejects_cell_when_win_rate_below_threshold():
    result = _freeze(
        cells=[_cell_result("cell_a", win_rate=0.49)],
        raw_train_screen_cell_ids=["cell_a"],
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["below_min_win_rate"]


def test_rejects_cell_when_worst_net_bps_below_threshold_if_configured():
    result = _freeze(
        cells=[_cell_result("cell_a", worst_net_bps=-2.0)],
        raw_train_screen_cell_ids=["cell_a"],
        config=OfflineTrainSurvivorFreezeConfig(min_worst_net_bps=-1.0),
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["below_min_worst_net_bps"]


def test_rejects_non_edge_family4_conditioning_cells():
    result = _freeze(
        cells=[_cell_result(
            "cell_a",
            family_id="family_4_stablecoin_quote_regime_conditioning",
            is_edge_family=False,
        )],
        raw_train_screen_cell_ids=["cell_a"],
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["family_4_conditioning_excluded"]


def test_rejects_family3_when_latency_gate_required_but_not_satisfied():
    result = _freeze(
        cells=[_cell_result(
            "cell_a",
            family_id="family_3_usd_reference_translation_lag",
            latency_gate_required=True,
        )],
        raw_train_screen_cell_ids=["cell_a"],
    )
    assert result.rejection_reasons_by_cell["cell_a"] == ["LATENCY_GATE_REQUIRED_NOT_RUN"]


def test_deterministic_ranking_works_when_max_survivors_is_set():
    result = _freeze(
        cells=[
            _cell_result("cell_b", net_mean_bps=2.0, win_rate=0.7, valid_event_count=3),
            _cell_result("cell_a", net_mean_bps=2.0, win_rate=0.7, valid_event_count=3),
            _cell_result("cell_c", net_mean_bps=1.0, win_rate=0.9, valid_event_count=5),
        ],
        raw_train_screen_cell_ids=["cell_b", "cell_a", "cell_c"],
        config=OfflineTrainSurvivorFreezeConfig(max_survivors=2),
    )
    assert result.train_survivor_cell_ids == ["cell_a", "cell_b"]
    assert [cell.cell_id for cell in result.survivor_cells] == ["cell_a", "cell_b"]


def test_cells_excluded_by_max_survivors_are_recorded_with_reason():
    result = _freeze(
        cells=[
            _cell_result("cell_a", net_mean_bps=3.0),
            _cell_result("cell_b", net_mean_bps=2.0),
        ],
        raw_train_screen_cell_ids=["cell_a", "cell_b"],
        config=OfflineTrainSurvivorFreezeConfig(max_survivors=1),
    )
    assert result.rejection_reasons_by_cell["cell_b"] == ["excluded_by_max_survivors"]


def test_survivor_freeze_config_hash_changes_when_thresholds_change():
    default_hash = compute_survivor_freeze_config_hash(OfflineTrainSurvivorFreezeConfig())
    changed_hash = compute_survivor_freeze_config_hash(OfflineTrainSurvivorFreezeConfig(min_valid_events=4))
    assert default_hash != changed_hash


def test_survivor_freeze_hash_changes_when_survivor_set_changes():
    result_a = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    result_b = _freeze(cells=[_cell_result("cell_a", net_mean_bps=-1.0)], raw_train_screen_cell_ids=["cell_a"])
    assert result_a.survivor_freeze_hash != result_b.survivor_freeze_hash


def test_identical_runs_produce_identical_parsed_output_json_and_identical_survivor_freeze_hash(tmp_path: Path):
    result_a = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    result_b = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    out_a = write_offline_train_survivor_freeze_outputs(result_a, tmp_path / "a", train_evaluation_manifest_path="train_manifest.json")
    out_b = write_offline_train_survivor_freeze_outputs(result_b, tmp_path / "b", train_evaluation_manifest_path="train_manifest.json")
    assert json.loads(out_a["result_path"].read_text(encoding="utf-8")) == json.loads(out_b["result_path"].read_text(encoding="utf-8"))
    assert result_a.survivor_freeze_hash == result_b.survivor_freeze_hash


def test_holdout_window_ids_are_preserved_only_as_metadata():
    result = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"], holdout_window_ids=["h2", "h1"])
    assert result.holdout_window_ids_seen_but_not_evaluated == ["h1", "h2"]
    assert result.train_survivor_cell_ids == ["cell_a"]


def test_holdout_ids_do_not_influence_survivor_selection_beyond_canonical_metadata():
    result_a = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"], holdout_window_ids=["h2", "h1"])
    result_b = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"], holdout_window_ids=["h1", "h2"])
    assert result_a.train_survivor_cell_ids == result_b.train_survivor_cell_ids
    assert result_a.survivor_freeze_hash == result_b.survivor_freeze_hash


def test_output_directory_does_not_overwrite_by_default(tmp_path: Path):
    result = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    out_dir = tmp_path / "out"
    write_offline_train_survivor_freeze_outputs(result, out_dir, train_evaluation_manifest_path="train_manifest.json")
    with pytest.raises(FileExistsError):
        write_offline_train_survivor_freeze_outputs(result, out_dir, train_evaluation_manifest_path="train_manifest.json")


def test_manifest_includes_required_hashes():
    result = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    manifest = build_offline_train_survivor_freeze_manifest_payload(result, train_evaluation_manifest_path="train_manifest.json")
    assert manifest["data_corpus_hash"]
    assert manifest["window_index_hash"]
    assert manifest["discovery_config_hash"]
    assert manifest["plan_hash"]
    assert manifest["evaluation_hash"]
    assert manifest["survivor_freeze_config_hash"]
    assert manifest["survivor_freeze_hash"]


def test_safety_scan_has_no_forbidden_capability_usage():
    path = Path("/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/offline_train_survivor_freeze.py")
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
        "holdout_evaluation",
        "fdr",
        "null_test",
        "bot_authorization",
    ]
    assert all(term not in text for term in forbidden)


def test_compute_survivor_freeze_hash_is_stable_for_same_result():
    result = _freeze(cells=[_cell_result("cell_a")], raw_train_screen_cell_ids=["cell_a"])
    assert compute_survivor_freeze_hash(result) == result.survivor_freeze_hash


def test_schema_version_constant_present():
    assert FREEZE_SCHEMA_VERSION == "offline_train_survivor_freeze_v1"
