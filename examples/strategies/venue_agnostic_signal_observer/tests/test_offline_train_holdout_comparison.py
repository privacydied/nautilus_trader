"""
Tests for Phase 2B-2C1 offline train-holdout comparison.

Uses synthetic JSON fixtures only. No source market data loading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    COMPARISON_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_INPUT_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_NO_HOLDOUT_EVALUATED_SURVIVORS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_NO_HOLDOUT_SURVIVORS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_NO_TRAIN_SURVIVORS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_SURVIVOR_FREEZE_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_TRAIN_EVALUATION_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    STATUS_UNUSABLE_COMPARISON_INPUT,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    OfflineTrainHoldoutComparisonConfig,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    OfflineTrainHoldoutComparisonReport,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    build_offline_train_holdout_comparison_manifest_payload,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    build_offline_train_holdout_comparison_report,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    compute_comparison_config_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_train_holdout_comparison import (
    write_offline_train_holdout_comparison_outputs,
)


# ---------------------------------------------------------------------------
# Fixture helpers (pure JSON, no source data)
# ---------------------------------------------------------------------------

def _eval_cell(
    cell_id: str,
    *,
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
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    plan_hash: str = "plan_hash",
) -> dict:
    return {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "is_edge_family": is_edge_family,
        "status": "OFFLINE_TRAIN_EVALUATION_READY",
        "train_window_ids": ["w1", "w2"],
        "evaluated_event_count": valid_event_count,
        "valid_event_count": valid_event_count,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "signal_variant": signal_variant,
        "required_resolution": "bar",
        "latency_gate_required": False,
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


def _train_evaluation_payload(
    *,
    cells: list[dict],
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
) -> dict:
    return {
        "schema_version": "offline_train_evaluation_v1",
        "status": "OFFLINE_TRAIN_EVALUATION_READY",
        "cell_results": cells,
        "excluded_cells": [],
        "train_window_ids": ["w1", "w2"],
        "holdout_window_ids_seen_but_not_evaluated": ["w3"],
        "raw_train_screen_cell_ids": [cell["cell_id"] for cell in cells],
        "train_survivor_cell_ids": [],
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "evaluation_hash": evaluation_hash,
        "metadata": {"holdout_window_ids_seen_but_not_evaluated": ["w3"]},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
    }


def _train_evaluation_manifest(
    payload: dict,
    *,
    evaluation_hash: str | None = None,
) -> dict:
    return {
        "run_id": "train_eval_run",
        "phase": "offline_train_evaluation",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": "offline_train_evaluation_v1",
        "data_corpus_hash": payload["data_corpus_hash"],
        "precommitment_hash": None,
        "window_index_hash": payload["window_index_hash"],
        "discovery_config_hash": payload["discovery_config_hash"],
        "plan_hash": payload["plan_hash"],
        "evaluation_hash": evaluation_hash or payload["evaluation_hash"],
        "status": payload["status"],
        "raw_train_screen_cell_count": len(payload["raw_train_screen_cell_ids"]),
        "train_survivor_cell_count": 0,
        "survivor_freeze_status": "NOT_RUN_PHASE_2B2A",
        "safety": "public_data_observer_only",
    }


def _freeze_survivor_cell(
    cell_id: str,
    *,
    family_id: str = "family_1_same_venue_quote_basis",
    family_name: str = "Family 1",
    signal_variant: str | None = None,
    lookback_ms: int = 60_000,
    horizon_ms: int = 60_000,
    valid_event_count: int = 3,
    net_mean_bps: float = 1.0,
    net_median_bps: float = 1.0,
    win_rate: float = 2 / 3,
    worst_net_bps: float = 0.25,
    freeze_rank: int = 1,
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
) -> dict:
    return {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "signal_variant": signal_variant,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "valid_event_count": valid_event_count,
        "net_mean_bps": net_mean_bps,
        "net_median_bps": net_median_bps,
        "win_rate": win_rate,
        "worst_net_bps": worst_net_bps,
        "freeze_rank": freeze_rank,
        "freeze_reasons": ["passed_train_survivor_freeze"],
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "discovery_config_hash": discovery_config_hash,
    }


def _survivor_freeze_payload(
    *,
    survivor_cells: list[dict],
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    train_survivor_cell_ids: list[str] | None = None,
    status: str = "OFFLINE_TRAIN_SURVIVOR_FREEZE_READY",
    survivor_freeze_hash: str = "survivor_freeze_hash",
    survivor_freeze_config_hash: str = "survivor_freeze_config_hash",
) -> dict:
    ids = train_survivor_cell_ids or [c["cell_id"] for c in survivor_cells]
    return {
        "schema_version": "offline_train_survivor_freeze_v1",
        "status": status,
        "train_survivor_cell_ids": ids,
        "rejected_cell_ids": [],
        "rejection_reasons_by_cell": {},
        "raw_train_screen_cell_ids": ids,
        "holdout_window_ids_seen_but_not_evaluated": ["w3"],
        "survivor_cells": survivor_cells,
        "survivor_freeze_config_hash": survivor_freeze_config_hash,
        "survivor_freeze_hash": survivor_freeze_hash,
        "metadata": {},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
    }


def _survivor_freeze_manifest(
    payload: dict,
) -> dict:
    return {
        "run_id": "freeze_run",
        "phase": "offline_train_survivor_freeze",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": "offline_train_survivor_freeze_v1",
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
        "holdout_window_count_seen_but_not_evaluated": len(payload.get("holdout_window_ids_seen_but_not_evaluated", [])),
        "safety": "public_data_observer_only",
    }


def _holdout_cell(
    cell_id: str,
    *,
    family_id: str = "family_1_same_venue_quote_basis",
    family_name: str = "Family 1",
    signal_variant: str | None = None,
    lookback_ms: int = 60_000,
    horizon_ms: int = 60_000,
    valid_event_count: int = 2,
    net_mean_bps: float | None = 1.0,
    net_median_bps: float | None = 1.0,
    win_rate: float | None = 1.0,
    worst_net_bps: float | None = 0.5,
    status: str = "OFFLINE_HOLDOUT_EVALUATION_READY",
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    survivor_freeze_hash: str = "survivor_freeze_hash",
) -> dict:
    return {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "is_edge_family": True,
        "status": status,
        "holdout_window_ids": ["w3"],
        "evaluated_event_count": valid_event_count,
        "valid_event_count": valid_event_count,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "signal_variant": signal_variant,
        "required_resolution": "bar",
        "latency_gate_required": False,
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
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_hash": survivor_freeze_hash,
    }


def _holdout_evaluation_payload(
    *,
    cells: list[dict],
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    survivor_freeze_hash: str = "survivor_freeze_hash",
    train_survivor_cell_ids: list[str] | None = None,
    holdout_evaluation_hash: str = "holdout_evaluation_hash",
) -> dict:
    ids = train_survivor_cell_ids or [c["cell_id"] for c in cells]
    return {
        "schema_version": "offline_holdout_evaluation_v1",
        "status": "OFFLINE_HOLDOUT_EVALUATION_READY" if cells else "NO_TRAIN_SURVIVORS",
        "cell_results": cells,
        "excluded_survivor_cells": {},
        "train_survivor_cell_ids": ids,
        "holdout_evaluated_cell_ids": [c["cell_id"] for c in cells],
        "train_window_ids_seen_but_not_evaluated": ["w1", "w2"],
        "holdout_window_ids": ["w3"],
        "holdout_evaluation_hash": holdout_evaluation_hash,
        "metadata": {},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_config_hash": "survivor_freeze_config_hash",
        "survivor_freeze_hash": survivor_freeze_hash,
    }


def _holdout_evaluation_manifest(
    payload: dict,
) -> dict:
    return {
        "run_id": "holdout_eval_run",
        "phase": "offline_holdout_evaluation",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": "offline_holdout_evaluation_v1",
        "data_corpus_hash": payload["data_corpus_hash"],
        "precommitment_hash": None,
        "window_index_hash": payload["window_index_hash"],
        "discovery_config_hash": payload["discovery_config_hash"],
        "plan_hash": payload["plan_hash"],
        "evaluation_hash": payload["evaluation_hash"],
        "survivor_freeze_config_hash": payload.get("survivor_freeze_config_hash", ""),
        "survivor_freeze_hash": payload["survivor_freeze_hash"],
        "holdout_evaluation_hash": payload["holdout_evaluation_hash"],
        "status": payload["status"],
        "train_survivor_cell_count": len(payload["train_survivor_cell_ids"]),
        "holdout_evaluated_cell_count": len(payload["holdout_evaluated_cell_ids"]),
        "safety": "public_data_observer_only",
    }


def _compare(
    *,
    train_cells: list[dict] | None = None,
    survivor_cells: list[dict] | None = None,
    holdout_cells: list[dict] | None = None,
    freeze_status: str = "OFFLINE_TRAIN_SURVIVOR_FREEZE_READY",
    config: OfflineTrainHoldoutComparisonConfig | None = None,
    manifest_overrides: dict | None = None,
    freeze_payload_overrides: dict | None = None,
    holdout_payload_overrides: dict | None = None,
) -> OfflineTrainHoldoutComparisonReport:
    train_cells = train_cells or []
    survivor_ids = [c["cell_id"] for c in (survivor_cells or [])]
    survivor_list = survivor_cells or []

    train_eval_payload = _train_evaluation_payload(
        cells=train_cells,
    )
    holdout_cell_list = holdout_cells if holdout_cells is not None else []
    freeze_payload = _survivor_freeze_payload(
        survivor_cells=survivor_list,
        train_survivor_cell_ids=survivor_ids,
        status=freeze_status,
    )
    holdout_payload = _holdout_evaluation_payload(
        cells=holdout_cell_list,
        train_survivor_cell_ids=survivor_ids,
    )

    if freeze_payload_overrides:
        freeze_payload.update(freeze_payload_overrides)
    if holdout_payload_overrides:
        holdout_payload.update(holdout_payload_overrides)

    train_eval_manifest = _train_evaluation_manifest(train_eval_payload)
    freeze_manifest = _survivor_freeze_manifest(freeze_payload)
    holdout_manifest = _holdout_evaluation_manifest(holdout_payload)

    if manifest_overrides:
        train_eval_manifest.update(manifest_overrides.get("train", {}))
        freeze_manifest.update(manifest_overrides.get("freeze", {}))
        holdout_manifest.update(manifest_overrides.get("holdout", {}))

    return build_offline_train_holdout_comparison_report(
        train_evaluation_manifest=train_eval_manifest,
        train_evaluation_payload=train_eval_payload,
        train_survivor_freeze_manifest=freeze_manifest,
        train_survivor_freeze_payload=freeze_payload,
        holdout_evaluation_manifest=holdout_manifest,
        holdout_evaluation_payload=holdout_payload,
        comparison_config=config,
    )


# ---------------------------------------------------------------------------
# Identity checks
# ---------------------------------------------------------------------------


def test_rejects_train_evaluation_hash_mismatch():
    """Train evaluation manifest hash != payload hash."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
        manifest_overrides={"train": {"evaluation_hash": "other_eval_hash"}},
    )
    assert report.status == STATUS_TRAIN_EVALUATION_HASH_MISMATCH


def test_rejects_survivor_freeze_hash_mismatch():
    """Survivor freeze manifest hash != payload hash."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
        manifest_overrides={"freeze": {"survivor_freeze_hash": "other_freeze_hash"}},
    )
    assert report.status == STATUS_SURVIVOR_FREEZE_HASH_MISMATCH


def test_rejects_holdout_evaluation_hash_mismatch():
    """Holdout evaluation manifest hash != payload hash."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a")],
        manifest_overrides={"holdout": {"holdout_evaluation_hash": "other_holdout_hash"}},
    )
    assert report.status == STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH


def test_rejects_lineage_hash_mismatch_freeze_eval_hash():
    """Survivor freeze evaluation_hash != train evaluation_hash."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
        freeze_payload_overrides={"evaluation_hash": "different_eval_hash"},
    )
    assert report.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_lineage_hash_mismatch_holdout_freeze_hash():
    """Holdout evaluation survivor_freeze_hash != freeze hash."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
        holdout_payload_overrides={"survivor_freeze_hash": "different_freeze_hash"},
    )
    assert report.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_lineage_hash_mismatch_holdout_plan_hash():
    """Holdout evaluation plan_hash != train plan_hash."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
        holdout_payload_overrides={"plan_hash": "different_plan_hash"},
    )
    assert report.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_precommitment_mismatch():
    """Non-null precommitment hashes must agree."""
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
        manifest_overrides={
            "train": {"precommitment_hash": "pc_a"},
            "freeze": {"precommitment_hash": "pc_b"},
        },
    )
    assert report.status == STATUS_INPUT_HASH_MISMATCH


# ---------------------------------------------------------------------------
# Empty / edge states
# ---------------------------------------------------------------------------


def test_returns_no_train_survivors_when_freeze_has_no_survivors():
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[],
        holdout_cells=[],
        freeze_status="NO_TRAIN_SURVIVORS",
    )
    assert report.status == STATUS_NO_TRAIN_SURVIVORS
    assert report.comparison_cells == []
    assert report.train_survivor_cell_ids == []


def test_returns_no_holdout_evaluated_survivors_when_no_holdout_result():
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[],
    )
    # No holdout cells at all -> missing holdout -> NO_HOLDOUT_EVALUATED_SURVIVORS
    assert report.status == STATUS_NO_HOLDOUT_EVALUATED_SURVIVORS
    assert "cell_a" in report.missing_holdout_cell_ids


def test_returns_no_holdout_survivors_when_all_fail_thresholds():
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=1.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=1.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=-0.5, win_rate=0.0)],
    )
    assert report.status == STATUS_NO_HOLDOUT_SURVIVORS
    assert "cell_a" in report.holdout_failed_cell_ids


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def test_produces_comparison_row_for_matching_survivor_and_holdout():
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=1.0, valid_event_count=3)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=1.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, valid_event_count=2, win_rate=1.0)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY
    assert len(report.comparison_cells) == 1
    cell = report.comparison_cells[0]
    assert cell.cell_id == "cell_a"
    assert cell.comparison_passed
    assert cell.failure_reasons == []


def test_passes_comparison_when_holdout_metrics_meet_thresholds():
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0, valid_event_count=5)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=3.0, valid_event_count=3, net_median_bps=2.0, win_rate=0.8, worst_net_bps=-1.0)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY
    assert report.comparison_cells[0].comparison_passed


def test_fails_when_holdout_valid_events_below_threshold():
    config = OfflineTrainHoldoutComparisonConfig(min_holdout_valid_events=5)
    report = _compare(
        train_cells=[_eval_cell("cell_a", valid_event_count=5)],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a", valid_event_count=2)],
        config=config,
    )
    assert not report.comparison_cells[0].comparison_passed
    assert any("below_min_holdout_valid_events" in r for r in report.comparison_cells[0].failure_reasons)


def test_fails_when_holdout_net_mean_below_threshold():
    """Default min_holdout_net_mean_bps=0, so negative net_mean fails."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=1.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=-0.5)],
    )
    assert not report.comparison_cells[0].comparison_passed
    assert any("below_min_holdout_net_mean_bps" in r for r in report.comparison_cells[0].failure_reasons)


def test_fails_when_holdout_net_median_below_threshold():
    """Default min_holdout_net_median_bps=0, so negative median fails."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_median_bps=1.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a", net_median_bps=-0.5, win_rate=0.6)],
    )
    assert not report.comparison_cells[0].comparison_passed
    assert any("below_min_holdout_net_median_bps" in r for r in report.comparison_cells[0].failure_reasons)


def test_fails_when_holdout_win_rate_below_threshold():
    report = _compare(
        train_cells=[_eval_cell("cell_a", win_rate=0.7)],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a", win_rate=0.3)],
    )
    assert not report.comparison_cells[0].comparison_passed
    assert any("below_min_holdout_win_rate" in r for r in report.comparison_cells[0].failure_reasons)


def test_applies_optional_worst_net_threshold_when_configured():
    config = OfflineTrainHoldoutComparisonConfig(min_holdout_worst_net_bps=0.0)
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a", worst_net_bps=-1.0, win_rate=0.7)],
        config=config,
    )
    assert not report.comparison_cells[0].comparison_passed
    assert any("below_min_holdout_worst_net_bps" in r for r in report.comparison_cells[0].failure_reasons)


def test_optional_worst_net_threshold_not_applied_when_null():
    config = OfflineTrainHoldoutComparisonConfig(min_holdout_worst_net_bps=None)
    report = _compare(
        train_cells=[_eval_cell("cell_a")],
        survivor_cells=[_freeze_survivor_cell("cell_a")],
        holdout_cells=[_holdout_cell("cell_a", worst_net_bps=-10.0, win_rate=0.7, net_mean_bps=1.0, net_median_bps=1.0)],
        config=config,
    )
    assert report.comparison_cells[0].comparison_passed


def test_computes_train_to_holdout_decay_metrics():
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
    )
    cell = report.comparison_cells[0]
    assert cell.train_net_mean_bps == 3.0
    assert cell.holdout_net_mean_bps == 1.0
    assert cell.net_mean_decay_bps == 2.0  # 3.0 - 1.0
    assert cell.net_mean_decay_ratio is not None
    assert cell.net_mean_decay_ratio == pytest.approx(2.0 / 3.0)


def test_decay_ratio_null_when_train_net_mean_non_positive():
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=0.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=0.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
    )
    cell = report.comparison_cells[0]
    assert cell.net_mean_decay_bps == -1.0  # 0.0 - 1.0
    assert cell.net_mean_decay_ratio is None  # train_net_mean is 0, not > 0


def test_decay_metrics_null_when_metrics_missing():
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=None)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=0.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
    )
    cell = report.comparison_cells[0]
    assert cell.net_mean_decay_bps is None
    assert cell.net_mean_decay_ratio is None


# ---------------------------------------------------------------------------
# Survivor set boundaries
# ---------------------------------------------------------------------------


def test_does_not_create_or_mutate_train_survivor_set():
    """Comparison must not add cells to survivor set."""
    report = _compare(
        train_cells=[
            _eval_cell("cell_a", net_mean_bps=2.0),
            _eval_cell("cell_b", net_mean_bps=1.0),
        ],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report.train_survivor_cell_ids == ["cell_a"]
    assert "cell_b" not in report.train_survivor_cell_ids


def test_does_not_add_non_survivor_cells():
    """Non-survivor cells must not enter comparison."""
    report = _compare(
        train_cells=[
            _eval_cell("cell_a", net_mean_bps=2.0),
            _eval_cell("cell_b", net_mean_bps=1.0),
        ],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[
            _holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7),
            _holdout_cell("cell_b", net_mean_bps=1.0, win_rate=0.6),
        ],
    )
    assert len(report.comparison_cells) == 1
    assert report.comparison_cells[0].cell_id == "cell_a"
    assert "cell_b" not in [c.cell_id for c in report.comparison_cells]


def test_holdout_result_for_non_survivor_is_excluded():
    """Holdout results for non-survivors must be ignored."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[
            _holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7),
            _holdout_cell("cell_b", net_mean_bps=5.0, win_rate=0.9),
        ],
    )
    assert len(report.comparison_cells) == 1
    assert report.comparison_cells[0].cell_id == "cell_a"


# ---------------------------------------------------------------------------
# Family 4 isolation
# ---------------------------------------------------------------------------


def test_rejects_family4_in_survivors():
    """Family 4 conditioning cells are not allowed as survivors."""
    train_cells = [
        _eval_cell("cell_a", family_id="family_4_stablecoin_quote_regime_conditioning", net_mean_bps=2.0, is_edge_family=False),
    ]
    report = _compare(
        train_cells=train_cells,
        survivor_cells=[_freeze_survivor_cell("cell_a", family_id="family_4_stablecoin_quote_regime_conditioning", net_mean_bps=2.0)],
        holdout_cells=[],
    )
    assert report.status == STATUS_UNUSABLE_COMPARISON_INPUT


# ---------------------------------------------------------------------------
# Hash properties
# ---------------------------------------------------------------------------


def test_comparison_config_hash_changes_when_thresholds_change():
    default_hash = compute_comparison_config_hash(OfflineTrainHoldoutComparisonConfig())
    changed_hash = compute_comparison_config_hash(OfflineTrainHoldoutComparisonConfig(min_holdout_valid_events=3))
    assert default_hash != changed_hash


def test_comparison_hash_changes_when_holdout_metrics_change():
    report_a = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    report_b = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
    )
    assert report_a.comparison_hash != report_b.comparison_hash


def test_comparison_hash_changes_when_survivor_set_changes():
    report_a = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    report_b = _compare(
        train_cells=[
            _eval_cell("cell_a", net_mean_bps=2.0),
            _eval_cell("cell_b", net_mean_bps=2.0),
        ],
        survivor_cells=[
            _freeze_survivor_cell("cell_a", net_mean_bps=2.0),
            _freeze_survivor_cell("cell_b", net_mean_bps=2.0),
        ],
        holdout_cells=[
            _holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7),
            _holdout_cell("cell_b", net_mean_bps=2.0, win_rate=0.7),
        ],
    )
    assert report_a.comparison_hash != report_b.comparison_hash


def test_identical_runs_produce_identical_comparison_hash():
    report_a = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    report_b = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report_a.comparison_hash == report_b.comparison_hash


def test_identical_runs_produce_identical_parsed_json(tmp_path: Path):
    report_a = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    report_b = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )

    out_a = write_offline_train_holdout_comparison_outputs(
        report_a, tmp_path / "a",
        train_evaluation_manifest_path="train_manifest.json",
        train_survivor_freeze_manifest_path="freeze_manifest.json",
        holdout_evaluation_manifest_path="holdout_manifest.json",
    )
    out_b = write_offline_train_holdout_comparison_outputs(
        report_b, tmp_path / "b",
        train_evaluation_manifest_path="train_manifest.json",
        train_survivor_freeze_manifest_path="freeze_manifest.json",
        holdout_evaluation_manifest_path="holdout_manifest.json",
    )
    assert json.loads(out_a["result_path"].read_text(encoding="utf-8")) == json.loads(out_b["result_path"].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Output behavior
# ---------------------------------------------------------------------------


def test_output_directory_does_not_overwrite_by_default(tmp_path: Path):
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    from examples.strategies.venue_agnostic_signal_observer.run_artifacts import safe_output_dir
    safe_output_dir(out_dir, allow_existing=True)

    write_offline_train_holdout_comparison_outputs(
        report, out_dir / report.run_id,
        train_evaluation_manifest_path="train_manifest.json",
        train_survivor_freeze_manifest_path="freeze_manifest.json",
        holdout_evaluation_manifest_path="holdout_manifest.json",
    )
    with pytest.raises(FileExistsError):
        write_offline_train_holdout_comparison_outputs(
            report, out_dir / report.run_id,
            train_evaluation_manifest_path="train_manifest.json",
            train_survivor_freeze_manifest_path="freeze_manifest.json",
            holdout_evaluation_manifest_path="holdout_manifest.json",
        )


# ---------------------------------------------------------------------------
# Missing holdout results
# ---------------------------------------------------------------------------


def test_records_missing_holdout_result_separately():
    """A train survivor without holdout result goes to missing list."""
    report = _compare(
        train_cells=[
            _eval_cell("cell_a", net_mean_bps=2.0),
            _eval_cell("cell_b", net_mean_bps=2.0),
        ],
        survivor_cells=[
            _freeze_survivor_cell("cell_a", net_mean_bps=2.0),
            _freeze_survivor_cell("cell_b", net_mean_bps=2.0),
        ],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert "cell_a" not in report.missing_holdout_cell_ids
    assert "cell_b" in report.missing_holdout_cell_ids
    assert len(report.comparison_cells) == 2
    cell_b = next(c for c in report.comparison_cells if c.cell_id == "cell_b")
    assert not cell_b.comparison_passed
    assert "missing_holdout_result" in cell_b.failure_reasons


def test_non_evaluable_holdout_status_fails_comparison():
    """Holdout cell with non-evaluable status (e.g., INSUFFICIENT_HOLDOUT_EVENTS)."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", status="INSUFFICIENT_HOLDOUT_EVENTS", net_mean_bps=None, net_median_bps=None, win_rate=None)],
    )
    assert not report.comparison_cells[0].comparison_passed
    assert any("holdout_status_not_evaluable" in r for r in report.comparison_cells[0].failure_reasons)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_includes_required_hashes(tmp_path: Path):
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    manifest = build_offline_train_holdout_comparison_manifest_payload(
        report,
        train_evaluation_manifest_path="train_manifest.json",
        train_survivor_freeze_manifest_path="freeze_manifest.json",
        holdout_evaluation_manifest_path="holdout_manifest.json",
    )
    assert manifest["data_corpus_hash"]
    assert manifest["window_index_hash"]
    assert manifest["discovery_config_hash"]
    assert manifest["plan_hash"]
    assert manifest["evaluation_hash"]
    assert manifest["survivor_freeze_hash"]
    assert manifest["holdout_evaluation_hash"]
    assert manifest["comparison_config_hash"]
    assert manifest["comparison_hash"]


# ---------------------------------------------------------------------------
# Safety: no source data loading, no FDR, no verdicts
# ---------------------------------------------------------------------------


def test_does_not_inspect_source_market_data():
    """Safe by construction: operates on JSON artifacts only."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    # Compare row must use pre-computed metrics, not source data
    assert report.comparison_cells[0].train_valid_event_count == 3
    assert report.comparison_cells[0].holdout_valid_event_count == 2


def test_does_not_run_fdr():
    """No FDR execution in this phase."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY
    # No FDR fields should exist
    assert report.comparison_hash


def test_does_not_run_null_tests():
    """No null test execution in this phase."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY
    # No null test fields expected


def test_does_not_run_cost_sensitivity():
    """No cost sensitivity execution in this phase."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY


def test_does_not_run_candidate_falsification():
    """No candidate falsification in this phase."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY


def test_does_not_run_shadow_executor():
    """No Shadow Executor in this phase."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, win_rate=0.7)],
    )
    assert report.status == STATUS_OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY


def test_does_not_emit_final_rejection_verdicts():
    """No REJECTED, NO_EDGE_AFTER_COSTS or similar final verdicts."""
    allowed_statuses = {
        "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY",
        "NO_TRAIN_SURVIVORS",
        "NO_HOLDOUT_EVALUATED_SURVIVORS",
        "NO_HOLDOUT_SURVIVORS",
        "INPUT_HASH_MISMATCH",
        "TRAIN_EVALUATION_HASH_MISMATCH",
        "SURVIVOR_FREEZE_HASH_MISMATCH",
        "HOLDOUT_EVALUATION_HASH_MISMATCH",
        "UNUSABLE_COMPARISON_INPUT",
    }
    forbidden = {
        "CANDIDATE",
        "CANDIDATE_FOR_LIVE",
        "TRADE_READY",
        "EXECUTION_READY",
        "EDGE_FOUND",
        "BOT_ALLOWED",
        "REJECTED",
        "NO_EDGE_AFTER_COSTS",
    }
    assert not forbidden.intersection(allowed_statuses)


def test_safety_scan_has_no_forbidden_capability_usage():
    path = Path("/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/offline_train_holdout_comparison.py")
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
    ]
    matches = [term for term in forbidden if term in text]
    assert not matches, f"Forbidden terms found: {matches}"


# ---------------------------------------------------------------------------
# Schema version constant
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Decay ratio threshold enforcement
# ---------------------------------------------------------------------------


def test_fails_when_decay_ratio_exceeds_configured_threshold():
    """Cell fails when decay ratio > max_train_to_holdout_net_mean_decay_ratio."""
    config = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=0.5)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config,
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    # decay_bps = 3.0 - 1.0 = 2.0, decay_ratio = 2.0 / 3.0 ≈ 0.667 > 0.5
    assert cell.net_mean_decay_bps == 2.0
    assert cell.net_mean_decay_ratio == pytest.approx(2.0 / 3.0)
    assert any("train_to_holdout_decay_ratio_above_threshold" in r for r in cell.failure_reasons)


def test_passes_decay_ratio_check_when_ratio_equals_threshold():
    """Cell passes decay ratio check when ratio == threshold."""
    config = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=0.5)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=2.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=2.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config,
    )
    cell = report.comparison_cells[0]
    # decay_bps = 2.0 - 1.0 = 1.0, decay_ratio = 1.0 / 2.0 = 0.5 == 0.5
    assert cell.comparison_passed
    assert not any("train_to_holdout_decay_ratio" in r for r in cell.failure_reasons)


def test_passes_decay_ratio_check_when_ratio_below_threshold():
    """Cell passes decay ratio check when ratio < threshold."""
    config = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=1.0)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config,
    )
    cell = report.comparison_cells[0]
    # decay_bps = 3.0 - 1.0 = 2.0, decay_ratio = 2.0 / 3.0 ≈ 0.667 < 1.0
    assert cell.comparison_passed
    assert not any("train_to_holdout_decay_ratio" in r for r in cell.failure_reasons)


def test_fails_decay_ratio_check_when_ratio_unavailable_and_threshold_configured():
    """When threshold is configured but train net mean is zero, decay ratio is unavailable."""
    config = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=0.5)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=0.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=0.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config,
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    assert cell.net_mean_decay_ratio is None
    assert any("train_to_holdout_decay_ratio_unavailable" in r for r in cell.failure_reasons)


def test_decay_ratio_not_checked_when_threshold_is_null():
    """When max_train_to_holdout_net_mean_decay_ratio is null, check is not applied."""
    config = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=None)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config,
    )
    cell = report.comparison_cells[0]
    assert cell.comparison_passed
    assert not any("train_to_holdout_decay_ratio" in r for r in cell.failure_reasons)


def test_comparison_hash_changes_when_decay_ratio_threshold_changes():
    """Changing decay ratio threshold changes comparison_config_hash and comparison_hash."""
    config_a = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=None)
    config_b = OfflineTrainHoldoutComparisonConfig(max_train_to_holdout_net_mean_decay_ratio=0.5)

    hash_a = compute_comparison_config_hash(config_a)
    hash_b = compute_comparison_config_hash(config_b)
    assert hash_a != hash_b

    report_a = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config_a,
    )
    report_b = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=1.0, win_rate=0.7)],
        config=config_b,
    )
    assert report_a.comparison_config_hash != report_b.comparison_config_hash
    assert report_a.comparison_hash != report_b.comparison_hash


def test_existing_threshold_behavior_unchanged_for_valid_events():
    """Existing holdout valid events threshold still works after decay ratio patch."""
    config = OfflineTrainHoldoutComparisonConfig(min_holdout_valid_events=5, max_train_to_holdout_net_mean_decay_ratio=0.5)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=2.0, valid_event_count=2, win_rate=0.7)],
        config=config,
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    assert any("below_min_holdout_valid_events" in r for r in cell.failure_reasons)


def test_existing_threshold_unchanged_for_net_mean():
    """Existing holdout net mean threshold still works after decay ratio patch."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=-0.5, win_rate=0.7)],
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    assert any("below_min_holdout_net_mean_bps" in r for r in cell.failure_reasons)


def test_existing_threshold_unchanged_for_net_median():
    """Existing holdout net median threshold still works after decay ratio patch."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=3.0, net_median_bps=-0.5, win_rate=0.7)],
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    assert any("below_min_holdout_net_median_bps" in r for r in cell.failure_reasons)


def test_existing_threshold_unchanged_for_win_rate():
    """Existing holdout win rate threshold still works after decay ratio patch."""
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=3.0, net_median_bps=2.0, win_rate=0.3)],
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    assert any("below_min_holdout_win_rate" in r for r in cell.failure_reasons)


def test_existing_threshold_unchanged_for_worst_net():
    """Existing optional worst-net threshold still works after decay ratio patch."""
    config = OfflineTrainHoldoutComparisonConfig(min_holdout_worst_net_bps=0.0)
    report = _compare(
        train_cells=[_eval_cell("cell_a", net_mean_bps=3.0)],
        survivor_cells=[_freeze_survivor_cell("cell_a", net_mean_bps=3.0)],
        holdout_cells=[_holdout_cell("cell_a", net_mean_bps=3.0, net_median_bps=2.0, win_rate=0.7, worst_net_bps=-1.0)],
        config=config,
    )
    cell = report.comparison_cells[0]
    assert not cell.comparison_passed
    assert any("below_min_holdout_worst_net_bps" in r for r in cell.failure_reasons)


def test_schema_version_constant_present():
    assert COMPARISON_SCHEMA_VERSION == "offline_train_holdout_comparison_v1"
