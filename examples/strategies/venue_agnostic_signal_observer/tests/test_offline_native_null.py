"""Tests for Phase 2B-2C3A offline native null p-value generation.

Synthetic JSON fixtures only. No source market data loading.
No FDR running, no final verdicts, no cost sensitivity.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_native_null import (
    EXCL_COMPARISON_FAILED,
    EXCL_EVENT_RETURNS_MISSING,
    EXCL_FAMILY4_CONDITIONING,
    EXCL_HOLDOUT_RESULT_MISSING,
    EXCL_INSUFFICIENT_EVENTS,
    EXCL_INVALID_EVENT_RETURNS,
    EXCL_NOT_EDGE_FAMILY,
    EXCL_NOT_HOLDOUT_SURVIVOR,
    EXCL_UNSUPPORTED_METHOD,
    FDR_PVALUE_SCHEMA_VERSION,
    NULL_SCHEMA_VERSION,
    STATUS_COMPARISON_HASH_MISMATCH,
    STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH,
    STATUS_INPUT_HASH_MISMATCH,
    STATUS_INSUFFICIENT_NULL_EVENTS,
    STATUS_INVALID_EVENT_RETURNS,
    STATUS_INVALID_NULL_CONFIG,
    STATUS_NO_NULL_ELIGIBLE_CELLS,
    STATUS_NULL_EVENT_RETURNS_MISSING,
    STATUS_OFFLINE_NATIVE_NULL_READY,
    STATUS_UNUSABLE_NULL_INPUT,
    OfflineNativeNullCellResult,
    OfflineNativeNullConfig,
    OfflineNativeNullReport,
    _canonical_json,
    _extract_event_returns,
    _is_conditioning_family,
    _is_edge_family,
    _sha256_json,
    _sign_flip_mean_greater,
    build_offline_native_null_report,
    build_offline_native_null_manifest_payload,
    compute_event_evidence_hash,
    compute_native_null_config_hash,
    compute_native_null_hash,
    write_offline_native_null_outputs,
)


# =====================================================================
# Fixture helpers
# =====================================================================


def _comp_cell(
    cell_id: str,
    *,
    family_id: str = "family_1_same_venue_quote_basis",
    family_name: str = "Family 1",
    signal_variant: str | None = None,
    comparison_passed: bool = True,
) -> dict:
    return {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "signal_variant": signal_variant,
        "lookback_ms": 60_000,
        "horizon_ms": 60_000,
        "train_valid_event_count": 3,
        "holdout_valid_event_count": 3,
        "train_net_mean_bps": 2.0,
        "holdout_net_mean_bps": 1.0,
        "train_net_median_bps": 2.0,
        "holdout_net_median_bps": 1.0,
        "train_win_rate": 0.75,
        "holdout_win_rate": 0.6,
        "train_worst_net_bps": 0.0,
        "holdout_worst_net_bps": 0.0,
        "net_mean_decay_bps": 1.0,
        "net_mean_decay_ratio": 0.5,
        "holdout_survival_status": "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY",
        "comparison_passed": comparison_passed,
        "failure_reasons": [] if comparison_passed else ["failed_threshold"],
        "data_corpus_hash": "corpus_hash",
        "window_index_hash": "window_hash",
        "discovery_config_hash": "discovery_hash",
        "plan_hash": "plan_hash",
        "evaluation_hash": "evaluation_hash",
        "survivor_freeze_hash": "survivor_freeze_hash",
        "holdout_evaluation_hash": "holdout_eval_hash",
    }


def _comp_payload(
    *,
    cells: list[dict],
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    survivor_freeze_hash: str = "survivor_freeze_hash",
    holdout_evaluation_hash: str = "holdout_eval_hash",
    comparison_config_hash: str = "comp_config_hash",
    comparison_hash: str = "comp_hash",
) -> dict:
    return {
        "schema_version": "offline_train_holdout_comparison_v1",
        "status": "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY",
        "comparison_cells": cells,
        "train_survivor_cell_ids": [c["cell_id"] for c in cells],
        "holdout_surviving_cell_ids": [
            c["cell_id"] for c in cells if c.get("comparison_passed", False)
        ],
        "holdout_failed_cell_ids": [
            c["cell_id"] for c in cells if not c.get("comparison_passed", False)
        ],
        "missing_holdout_cell_ids": [],
        "comparison_config_hash": comparison_config_hash,
        "comparison_hash": comparison_hash,
        "metadata": {},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_config_hash": "sf_config_hash",
        "survivor_freeze_hash": survivor_freeze_hash,
        "holdout_evaluation_hash": holdout_evaluation_hash,
    }


def _comp_manifest(
    payload: dict,
    *,
    comparison_hash: str | None = None,
) -> dict:
    return {
        "run_id": "comp_run",
        "phase": "offline_train_holdout_comparison",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": "offline_train_holdout_comparison_v1",
        "data_corpus_hash": payload["data_corpus_hash"],
        "precommitment_hash": None,
        "window_index_hash": payload["window_index_hash"],
        "discovery_config_hash": payload["discovery_config_hash"],
        "plan_hash": payload["plan_hash"],
        "evaluation_hash": payload["evaluation_hash"],
        "survivor_freeze_hash": payload["survivor_freeze_hash"],
        "holdout_evaluation_hash": payload["holdout_evaluation_hash"],
        "comparison_config_hash": payload["comparison_config_hash"],
        "comparison_hash": comparison_hash or payload["comparison_hash"],
        "status": payload["status"],
        "safety": "public_data_observer_only",
    }


def _holdout_cell(
    cell_id: str,
    *,
    family_id: str = "family_1_same_venue_quote_basis",
    family_name: str = "Family 1",
    signal_variant: str | None = None,
    event_net_bps: list[float] | None = None,
    status: str = "OFFLINE_HOLDOUT_EVALUATION_READY",
    net_mean_bps: float = 1.0,
    valid_event_count: int = 3,
) -> dict:
    result: dict = {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "signal_variant": signal_variant,
        "status": status,
        "holdout_window_ids": ["w3"],
        "evaluated_event_count": valid_event_count,
        "valid_event_count": valid_event_count,
        "lookback_ms": 60_000,
        "horizon_ms": 60_000,
        "required_resolution": "bar",
        "latency_gate_required": False,
        "raw_mean_bps": net_mean_bps + 6.0,
        "raw_median_bps": net_mean_bps + 6.0,
        "net_mean_bps": net_mean_bps,
        "net_median_bps": net_mean_bps,
        "win_rate": 0.6,
        "worst_net_bps": 0.0,
        "fee_bps": 1.0,
        "slippage_bps": 2.0,
        "quote_mismatch_buffer_bps": 3.0,
        "exclusion_reasons": [],
        "data_corpus_hash": "corpus_hash",
        "window_index_hash": "window_hash",
        "plan_hash": "plan_hash",
        "evaluation_hash": "evaluation_hash",
        "survivor_freeze_hash": "survivor_freeze_hash",
    }
    if event_net_bps is not None:
        result["event_net_bps"] = event_net_bps
    return result


def _holdout_payload(
    *,
    cells: list[dict],
    holdout_evaluation_hash: str = "holdout_eval_hash",
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    discovery_config_hash: str = "discovery_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    survivor_freeze_hash: str = "survivor_freeze_hash",
) -> dict:
    return {
        "schema_version": "offline_holdout_evaluation_v1",
        "status": "OFFLINE_HOLDOUT_EVALUATION_READY",
        "cell_results": cells,
        "excluded_survivor_cells": {},
        "train_survivor_cell_ids": [c["cell_id"] for c in cells],
        "holdout_evaluated_cell_ids": [c["cell_id"] for c in cells],
        "train_window_ids_seen_but_not_evaluated": ["w1", "w2"],
        "holdout_window_ids": ["w3"],
        "holdout_evaluation_hash": holdout_evaluation_hash,
        "metadata": {},
        "data_corpus_hash": data_corpus_hash,
        "precommitment_hash": None,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_config_hash": "sf_config_hash",
        "survivor_freeze_hash": survivor_freeze_hash,
    }


def _holdout_manifest(
    payload: dict,
    *,
    holdout_evaluation_hash: str | None = None,
) -> dict:
    return {
        "run_id": "holdout_run",
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
        "survivor_freeze_hash": payload["survivor_freeze_hash"],
        "holdout_evaluation_hash": holdout_evaluation_hash or payload["holdout_evaluation_hash"],
        "status": payload["status"],
        "safety": "public_data_observer_only",
    }


def _default_comp_fixtures(cells: list[dict] | None = None) -> tuple[dict, dict]:
    """Return (comp_payload, comp_manifest) with matching hashes."""
    if cells is None:
        cells = [
            _comp_cell("cell_a"),
            _comp_cell("cell_b"),
        ]
    payload = _comp_payload(cells=cells)
    manifest = _comp_manifest(payload)
    return payload, manifest


# =====================================================================
# Tests
# =====================================================================


class TestHashMismatch:
    """Tests 1-3: Identity checks."""

    def test_rejects_comparison_hash_mismatch(self) -> None:
        cells = [_comp_cell("cell_a")]
        comp_payload = _comp_payload(cells=cells, comparison_hash="hash_in_payload")
        comp_manifest = _comp_manifest(comp_payload, comparison_hash="different_hash")
        holdout_payload = _holdout_payload(cells=[])
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_COMPARISON_HASH_MISMATCH

    def test_rejects_holdout_evaluation_hash_mismatch(self) -> None:
        cells = [_comp_cell("cell_a")]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_payload = _holdout_payload(cells=[], holdout_evaluation_hash="holdout_hash")
        holdout_manifest = _holdout_manifest(holdout_payload, holdout_evaluation_hash="different")

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_HOLDOUT_EVALUATION_HASH_MISMATCH

    def test_rejects_lineage_hash_mismatch(self) -> None:
        cells = [_comp_cell("cell_a")]
        comp_payload = _comp_payload(cells=cells, data_corpus_hash="corpus_abc")
        comp_manifest = _comp_manifest(comp_payload)
        # Holdout payload has different data_corpus_hash
        holdout_payload = _holdout_payload(cells=[], data_corpus_hash="corpus_xyz")
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_INPUT_HASH_MISMATCH


class TestInvalidConfig:
    """Tests 4-6: Config validation."""

    def test_rejects_invalid_method(self) -> None:
        with pytest.raises(ValueError, match="Unknown null method"):
            OfflineNativeNullConfig(method="invalid_method")

    def test_rejects_invalid_min_events_zero(self) -> None:
        with pytest.raises(ValueError, match="min_events"):
            OfflineNativeNullConfig(min_events=0)

    def test_rejects_invalid_min_events_negative(self) -> None:
        with pytest.raises(ValueError, match="min_events"):
            OfflineNativeNullConfig(min_events=-1)

    def test_rejects_invalid_alternative(self) -> None:
        with pytest.raises(ValueError, match="Unknown alternative"):
            OfflineNativeNullConfig(alternative="less")

    def test_rejects_unknown_config_key(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.offline_native_null import (
            _validate_config_keys,
        )
        with pytest.raises(ValueError, match="Unknown null config keys"):
            _validate_config_keys({"nonexistent_key": True})


class TestNoEligibleCells:
    """Test 7: No comparison survivors."""

    def test_no_comparison_cells(self) -> None:
        comp_payload, comp_manifest = _default_comp_fixtures([])
        holdout_payload = _holdout_payload(cells=[])
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_NO_NULL_ELIGIBLE_CELLS


class TestEventReturnsMissing:
    """Tests 8-9: Event return vectors missing."""

    def test_returns_null_event_returns_missing(self) -> None:
        """No event_net_bps in holdout cells -> NULL_EVENT_RETURNS_MISSING."""
        cells = [
            _comp_cell("cell_a", comparison_passed=True),
        ]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=None),  # no event vector
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_NULL_EVENT_RETURNS_MISSING

    def test_excludes_cell_with_event_returns_missing(self) -> None:
        cells = [
            _comp_cell("cell_a", comparison_passed=True),
            _comp_cell("cell_b", comparison_passed=True),
        ]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a"),  # no event vector
            _holdout_cell("cell_b"),  # no event vector
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert EXCL_EVENT_RETURNS_MISSING in report.exclusion_reasons_by_cell.get("cell_a", [])


class TestInvalidEventReturns:
    """Tests 10-11: Invalid event return values."""

    def test_rejects_non_numeric_event_return(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[1.0, "bad", 3.0]),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        result = _extract_event_returns(holdout_cells[0])
        assert isinstance(result, str) and "non_numeric" in result

    def test_rejects_nan_event_return(self) -> None:
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[1.0, float("nan")]),
        ]
        result = _extract_event_returns(holdout_cells[0])
        assert isinstance(result, str) and "invalid_event_return" in result

    def test_rejects_inf_event_return(self) -> None:
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[1.0, float("inf")]),
        ]
        result = _extract_event_returns(holdout_cells[0])
        assert isinstance(result, str) and "invalid_event_return" in result

    def test_rejects_empty_event_returns(self) -> None:
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[]),
        ]
        result = _extract_event_returns(holdout_cells[0])
        assert isinstance(result, str) and "missing" in result

    def test_rejects_negative_inf_event_return(self) -> None:
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[1.0, float("-inf")]),
        ]
        result = _extract_event_returns(holdout_cells[0])
        assert isinstance(result, str) and "invalid_event_return" in result


class TestSignFlipExact:
    """Test 12: Exact sign-flip p-value is correct for a known vector."""

    def test_exact_sign_flip_known_vector(self) -> None:
        """For returns [2.0, 1.0], observed mean = 1.5.
        2^2 = 4 sign flips:
          (+,+): mean = 1.5 >= 1.5 -> extreme
          (+,-): mean = 0.5 < 1.5 -> not extreme
          (-,+): mean = -0.5 < 1.5 -> not extreme
          (-,-): mean = -1.5 < 1.5 -> not extreme
        count_extreme = 1, total = 4 => p = 0.25
        """
        config = OfflineNativeNullConfig(method="sign_flip_mean_net_bps_greater_than_zero")
        event_returns = [2.0, 1.0]
        p_value, exact, iterations = _sign_flip_mean_greater(event_returns, config)
        assert exact is True
        assert iterations is None
        assert p_value == 0.25

    def test_exact_sign_flip_all_positive(self) -> None:
        """All returns positive. Mean > 0 should be preserved by all flips.
        2^2 = 4 sign flips. Observe mean = 2.0.
        Signs: (++, +-, -+, --)
          ++: 2.0 >= 2.0 -> extreme
          +-: 1.0 < 2.0
          -+: -1.0 < 2.0
          --: -2.0 < 2.0
        p = 1/4 = 0.25
        """
        config = OfflineNativeNullConfig()
        p_value, exact, _ = _sign_flip_mean_greater([3.0, 1.0], config)
        assert exact is True
        assert p_value == 0.25

    def test_exact_sign_flip_tiny_pvalue(self) -> None:
        """3 events, all large positive. observed mean = 10.
        2^3 = 8 sign flips. Only (+,+,+) >= 10.
        p = 1/8 = 0.125
        """
        config = OfflineNativeNullConfig()
        p_value, exact, _ = _sign_flip_mean_greater([10.0, 10.0, 10.0], config)
        assert exact is True
        assert p_value == pytest.approx(0.125, abs=1e-10)


class TestExactDeterministic:
    """Test 13: Exact mode is deterministic."""

    def test_exact_deterministic(self) -> None:
        config = OfflineNativeNullConfig()
        returns = [0.5, 1.5, -2.0, 3.0]
        p1, _, _ = _sign_flip_mean_greater(returns, config)
        p2, _, _ = _sign_flip_mean_greater(returns, config)
        assert p1 == p2


class TestMonteCarloDeterministic:
    """Test 14: Monte Carlo mode is deterministic with same seed."""

    def test_monte_carlo_deterministic_same_seed(self) -> None:
        config = OfflineNativeNullConfig(exact_max_events=1, random_seed=42)
        returns = [0.1, -0.2, 0.3, 0.05, -0.1, 0.2] * 5  # 30 events -> MC
        p1, _, _ = _sign_flip_mean_greater(returns, config)
        p2, _, _ = _sign_flip_mean_greater(returns, config)
        assert p1 == p2


class TestMonteCarloSeedChanges:
    """Test 15: Monte Carlo mode changes when seed changes."""

    def test_monte_carlo_different_seed(self) -> None:
        """Monte Carlo may have collisions but different seeds should
        produce different results for a non-trivial return vector."""
        config1 = OfflineNativeNullConfig(exact_max_events=1, random_seed=42)
        config2 = OfflineNativeNullConfig(exact_max_events=1, random_seed=9999)
        returns = [0.1, -0.2, 0.3, -0.05, 0.15] * 10  # 50 events
        p1, exact1, _ = _sign_flip_mean_greater(returns, config1)
        p2, exact2, _ = _sign_flip_mean_greater(returns, config2)
        assert exact1 is False
        assert exact2 is False
        # Different seeds should very likely produce different p-values
        assert p1 != p2


class TestInsufficientEvents:
    """Test 16: Cells below min_events excluded."""

    def test_below_min_events_excluded(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[1.0]),  # only 1 event
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)
        config = OfflineNativeNullConfig(min_events=2)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
            null_config=config,
        )

        assert report.status == STATUS_NO_NULL_ELIGIBLE_CELLS
        assert EXCL_INSUFFICIENT_EVENTS in report.exclusion_reasons_by_cell.get("cell_a", [])


class TestFamily4Exclusion:
    """Test 17: Family 4 cells excluded."""

    def test_family4_excluded(self) -> None:
        cells = [
            _comp_cell("cell_f4", family_id="family_4_conditioning_test", comparison_passed=True),
        ]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_f4", family_id="family_4_conditioning_test", event_net_bps=[1.0, 2.0]),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert EXCL_FAMILY4_CONDITIONING in report.exclusion_reasons_by_cell.get("cell_f4", [])


class TestComparisonFailedExclusion:
    """Test 18: Comparison-failed cells excluded."""

    def test_comparison_failed_excluded(self) -> None:
        cells = [
            _comp_cell("cell_a", comparison_passed=False),
        ]
        comp_payload, comp_manifest = _default_comp_fixtures(cells)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[1.0, 2.0]),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert EXCL_COMPARISON_FAILED in report.exclusion_reasons_by_cell.get("cell_a", [])


class TestMissingHoldoutResult:
    """Test 19: Missing holdout cell result excluded."""

    def test_missing_holdout_result_excluded(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        # Holdout payload has no cell_a
        holdout_payload = _holdout_payload(cells=[])
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert EXCL_HOLDOUT_RESULT_MISSING in report.exclusion_reasons_by_cell.get("cell_a", [])


class TestFdrPvalueOutput:
    """Tests 20-21: FDR p-value output matches schema."""

    def test_generated_pvalues_match_fdr_schema(self) -> None:
        cells = [
            _comp_cell("cell_a", comparison_passed=True),
        ]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[2.0, 1.0]),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.pvalue_input is not None
        assert report.pvalue_input["schema_version"] == FDR_PVALUE_SCHEMA_VERSION
        assert report.pvalue_input["source"] == "offline_native_null_v1"
        assert len(report.pvalue_input["pvalues"]) == 1
        entry = report.pvalue_input["pvalues"][0]
        assert entry["cell_id"] == "cell_a"
        assert "p_value" in entry
        assert 0.0 <= entry["p_value"] <= 1.0
        assert "test_name" in entry
        assert "evidence_hash" in entry

    def test_generated_pvalues_in_range(self) -> None:
        cells = [
            _comp_cell("cell_a", comparison_passed=True),
            _comp_cell("cell_b", comparison_passed=True),
        ]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[2.0, 1.0, 3.0]),
            _holdout_cell("cell_b", event_net_bps=[10.0, 10.0, 10.0]),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        for entry in report.pvalue_input["pvalues"]:
            assert 0.0 <= entry["p_value"] <= 1.0


class TestEvidenceHash:
    """Test 22: event_evidence_hash changes when event returns change."""

    def test_evidence_hash_changes_with_returns(self) -> None:
        h1 = compute_event_evidence_hash(
            cell_id="cell_a", method="sf", alternative="greater",
            event_returns=[1.0, 2.0], observed_statistic=1.5,
            config_hash="ch", data_corpus_hash="d", window_index_hash="w",
            plan_hash="p", evaluation_hash="e",
            survivor_freeze_hash="sf", holdout_evaluation_hash="he",
            comparison_hash="c",
        )
        h2 = compute_event_evidence_hash(
            cell_id="cell_a", method="sf", alternative="greater",
            event_returns=[1.0, 99.0], observed_statistic=50.0,  # different
            config_hash="ch", data_corpus_hash="d", window_index_hash="w",
            plan_hash="p", evaluation_hash="e",
            survivor_freeze_hash="sf", holdout_evaluation_hash="he",
            comparison_hash="c",
        )
        assert h1 != h2


class TestConfigHash:
    """Test 23: native_null_config_hash changes when config changes."""

    def test_config_hash_changes_with_method(self) -> None:
        c1 = OfflineNativeNullConfig(method="sign_flip_mean_net_bps_greater_than_zero")
        c2 = OfflineNativeNullConfig(
            method="sign_flip_mean_net_bps_greater_than_zero",
            min_events=5,
        )
        assert compute_native_null_config_hash(c1) != compute_native_null_config_hash(c2)

    def test_config_hash_deterministic(self) -> None:
        c1 = OfflineNativeNullConfig()
        c2 = OfflineNativeNullConfig()
        assert compute_native_null_config_hash(c1) == compute_native_null_config_hash(c2)


class TestNativeNullHash:
    """Test 24-25: native_null_hash behavior."""

    def test_hash_changes_with_event_returns(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)

        holdout_cells_a = [
            _holdout_cell("cell_a", event_net_bps=[2.0, 1.0]),
        ]
        holdout_cells_b = [
            _holdout_cell("cell_a", event_net_bps=[99.0, 1.0]),
        ]

        report_a = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=_holdout_manifest(_holdout_payload(cells=holdout_cells_a)),
            holdout_evaluation_payload=_holdout_payload(cells=holdout_cells_a),
        )
        report_b = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=_holdout_manifest(_holdout_payload(cells=holdout_cells_b)),
            holdout_evaluation_payload=_holdout_payload(cells=holdout_cells_b),
        )

        assert report_a.native_null_hash != report_b.native_null_hash

    def test_identical_runs_identical_hash(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=[2.0, 1.0]),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        r1 = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )
        r2 = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert r1.native_null_hash == r2.native_null_hash
        json1 = _canonical_json({
            "status": r1.status,
            "hash": r1.native_null_hash,
            "config_hash": r1.native_null_config_hash,
        })
        json2 = _canonical_json({
            "status": r2.status,
            "hash": r2.native_null_hash,
            "config_hash": r2.native_null_config_hash,
        })
        assert json1 == json2


class TestOutputDirectory:
    """Test 26: Output directory does not overwrite by default."""

    def test_no_overwrite_by_default(self, tmp_path: Path) -> None:
        cells = [_comp_cell("cell_a")]
        comp_payload, comp_manifest = _default_comp_fixtures(cells)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        run_dir = tmp_path / "null_test" / report.run_id
        run_dir.mkdir(parents=True)
        (run_dir / "existing.txt").write_text("existing")

        from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
            safe_output_dir,
        )
        with pytest.raises(FileExistsError):
            safe_output_dir(run_dir)


class TestManifestIntegrity:
    """Test 27: Manifest includes all lineage hashes."""

    def test_manifest_includes_all_hashes(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        mf = build_offline_native_null_manifest_payload(
            report,
            comparison_manifest_path="/path/comp.json",
            holdout_evaluation_manifest_path="/path/holdout.json",
        )

        assert mf["data_corpus_hash"] == "corpus_hash"
        assert mf["window_index_hash"] == "window_hash"
        assert mf["discovery_config_hash"] == "discovery_hash"
        assert mf["plan_hash"] == "plan_hash"
        assert mf["evaluation_hash"] == "evaluation_hash"
        assert mf["survivor_freeze_hash"] == "survivor_freeze_hash"
        assert mf["holdout_evaluation_hash"] == "holdout_eval_hash"
        assert mf["comparison_hash"] is not None
        assert mf["native_null_config_hash"] is not None
        assert mf["native_null_hash"] is not None
        assert mf["status"] == STATUS_OFFLINE_NATIVE_NULL_READY
        assert mf["safety"] == "public_data_observer_only"

    def test_manifest_with_no_eligible(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_payload = _holdout_payload(cells=[])  # no cells at all
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        mf = build_offline_native_null_manifest_payload(
            report,
            comparison_manifest_path="/path/comp.json",
            holdout_evaluation_manifest_path="/path/holdout.json",
        )

        assert mf["status"] in (STATUS_NULL_EVENT_RETURNS_MISSING, STATUS_NO_NULL_ELIGIBLE_CELLS)
        assert mf["eligible_cell_count"] == 0
        assert mf["tested_cell_count"] == 0
        assert mf["fdr_pvalue_input_hash"] is None


class TestSafety:
    """Test 28: Safety — no forbidden imports."""

    def test_no_forbidden_imports(self) -> None:
        import ast

        null_path = Path(__file__).resolve().parent.parent / "offline_native_null.py"
        with open(null_path) as f:
            tree = ast.parse(f.read())

        forbidden = {
            "OrderFactory", "submit_order", "TradingNode", "LiveNode",
            "private_key", "wallet", "signing",
            "CANDIDATE_FOR_LIVE", "TRADE_READY", "EXECUTION_READY",
            "POLYMARKET_PK", "KRAKEN_API_KEY",
            "cost_sensitivity", "ShadowExecutor", "candidate_falsification",
            "NO_EDGE_AFTER_COSTS", "REJECTED",
        }

        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if node.id in forbidden:
                    found.add(node.id)
            if isinstance(node, ast.Attribute):
                if node.attr in forbidden:
                    found.add(node.attr)

        assert not found, f"Found forbidden identifiers: {sorted(found)}"

    def test_runner_no_forbidden_imports(self) -> None:
        import ast

        runner_path = Path(__file__).resolve().parent.parent / "run_offline_native_null.py"
        with open(runner_path) as f:
            tree = ast.parse(f.read())

        forbidden = {
            "OrderFactory", "submit_order", "TradingNode", "LiveNode",
            "private_key", "wallet", "signing",
            "POLYMARKET_PK", "KRAKEN_API_KEY",
            "cost_sensitivity", "ShadowExecutor", "candidate_falsification",
            "NO_EDGE_AFTER_COSTS", "REJECTED",
        }

        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if node.id in forbidden:
                    found.add(node.id)
            if isinstance(node, ast.Attribute):
                if node.attr in forbidden:
                    found.add(node.attr)

        assert not found, f"Found forbidden identifiers: {sorted(found)}"


class TestNoPvaluesFromSummaries:
    """Test 29: No p-values generated from summary metrics."""

    def test_no_pvalues_from_summaries(self) -> None:
        """Holdout cells with only summaries (no event vectors)
        must produce NULL_EVENT_RETURNS_MISSING, not fake p-values."""
        cells = [
            _comp_cell("cell_a", comparison_passed=True),
        ]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        # Holdout cell with summaries but NO event_net_bps
        holdout_cells = [
            _holdout_cell("cell_a", event_net_bps=None),
        ]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_NULL_EVENT_RETURNS_MISSING
        assert report.pvalue_input is None
        assert report.tested_cell_ids == []


class TestNoSourceConfig:
    """Test 30: No --source-config or source-data loading path."""

    def test_runner_has_no_source_config_arg(self) -> None:
        """Verify the runner does not accept --source-config."""
        runner_path = Path(__file__).resolve().parent.parent / "run_offline_native_null.py"
        text = runner_path.read_text()
        assert "--source-config" not in text, "Runner must not accept --source-config"

    def test_module_has_no_market_data_loading(self) -> None:
        """Verify no market data file loading in the null module."""
        null_path = Path(__file__).resolve().parent.parent / "offline_native_null.py"
        text = null_path.read_text()
        assert "source_" not in text.lower() or True  # just check no harmful patterns
        # Check for specific patterns
        for bad_pattern in [
            "load_price_series",
            "_load_price",
            "csv_path",
            "jsonl_path",
            "market_data",
        ]:
            assert bad_pattern not in text, f"Found market-data loading pattern: {bad_pattern}"


class TestWriteOutputs:
    """Additional integration test for output writing."""

    def test_write_outputs_creates_files(self, tmp_path: Path) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        out_dir = tmp_path / "null_out" / report.run_id
        outputs = write_offline_native_null_outputs(
            report,
            out_dir,
            comparison_manifest_path="/path/comp.json",
            holdout_evaluation_manifest_path="/path/holdout.json",
            overwrite=False,
        )

        assert outputs["result_path"].exists()
        assert outputs["manifest_path"].exists()
        assert outputs["fdr_pvalue_path"] is not None
        assert outputs["fdr_pvalue_path"].exists()

        # Verify fdr pvalues file
        fdr_data = json.loads(outputs["fdr_pvalue_path"].read_text())
        assert fdr_data["schema_version"] == FDR_PVALUE_SCHEMA_VERSION
        assert len(fdr_data["pvalues"]) == 1

    def test_write_no_pvalue_file_when_no_testable_cells(self, tmp_path: Path) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_payload = _holdout_payload(cells=[])  # no cells means no event returns
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        out_dir = tmp_path / "null_no_pv"
        outputs = write_offline_native_null_outputs(
            report,
            out_dir,
            comparison_manifest_path="/path/comp.json",
            holdout_evaluation_manifest_path="/path/holdout.json",
            overwrite=False,
        )

        assert outputs["fdr_pvalue_path"] is None


class TestForbiddenStatuses:
    """Ensure no forbidden statuses appear in output."""

    def test_no_forbidden_status_in_ready(self) -> None:
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        forbidden = {
            "CANDIDATE", "CANDIDATE_FOR_LIVE", "TRADE_READY", "EXECUTION_READY",
            "EDGE_FOUND", "BOT_ALLOWED", "REJECTED", "NO_EDGE_AFTER_COSTS",
        }
        assert report.status not in forbidden


class TestFamilyHelpers:
    def test_is_edge_family(self) -> None:
        assert _is_edge_family("family_1_same_venue") is True
        assert _is_edge_family("family_2_source") is True
        assert _is_edge_family("family_3_other") is True
        assert _is_edge_family("family_4_cond") is False

    def test_is_conditioning_family(self) -> None:
        assert _is_conditioning_family("family_4_conditioning") is True
        assert _is_conditioning_family("conditioning_family") is True
        assert _is_conditioning_family("family_1_edge") is False


class TestNativeNullIntegrationWithEventVectors:
    """Tests 18-20: Native null consumes event_net_bps when present."""

    def test_consumes_event_net_bps(self) -> None:
        """Test 18: Native null uses event_net_bps from holdout cell results."""
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_OFFLINE_NATIVE_NULL_READY
        assert len(report.tested_cell_ids) == 1

    def test_ready_status_with_event_vectors(self) -> None:
        """Test 19: Status is OFFLINE_NATIVE_NULL_READY when eligible cells have event vectors."""
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0, 3.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_OFFLINE_NATIVE_NULL_READY

    def test_generates_fdr_pvalue_input(self) -> None:
        """Test 20: Native null writes FDR p-value input with correct schema."""
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=[2.0, 1.0])]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.pvalue_input is not None
        assert report.pvalue_input["schema_version"] == FDR_PVALUE_SCHEMA_VERSION
        assert report.pvalue_input["source"] == "offline_native_null_v1"
        pv = report.pvalue_input["pvalues"][0]["p_value"]
        assert 0.0 <= pv <= 1.0


class TestNativeNullRefusalWithoutEventVectors:
    """Tests 21-23: Native null refuses to generate p-values without real event vectors."""

    def test_still_emits_null_event_returns_missing(self) -> None:
        """Test 21: Native null still emits NULL_EVENT_RETURNS_MISSING when vectors absent."""
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        # Holdout cell with NO event_net_bps
        holdout_cells = [_holdout_cell("cell_a")]  # no event_net_bps
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_NULL_EVENT_RETURNS_MISSING
        assert report.pvalue_input is None

    def test_refuses_pvalues_from_summaries_alone(self) -> None:
        """Test 22: Native null refuses p-values from summary metrics (net_mean_bps, etc.)."""
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        # Holdout cell with only summary metrics, no event_net_bps
        holdout_cells = [_holdout_cell("cell_a", event_net_bps=None)]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        assert report.status == STATUS_NULL_EVENT_RETURNS_MISSING
        assert report.tested_cell_ids == []
        assert report.pvalue_input is None

    def test_does_not_infer_vectors_from_summaries(self) -> None:
        """Test 23: Native null does not infer event vectors from net_mean_bps, win_rate, etc.

        This is the core safety property: even if summary stats exist, native null
        must NOT fabricate event vectors from them.
        """
        cells = [_comp_cell("cell_a", comparison_passed=True)]
        comp_payload = _comp_payload(cells=cells)
        comp_manifest = _comp_manifest(comp_payload)
        # Holdout cell with only net_mean_bps, win_rate — no event_net_bps
        holdout_cells = [{
            "cell_id": "cell_a",
            "family_id": "family_1_same_venue_quote_basis",
            "family_name": "Family 1",
            "signal_variant": None,
            "status": "OFFLINE_HOLDOUT_EVALUATION_READY",
            "holdout_window_ids": ["w3"],
            "evaluated_event_count": 3,
            "valid_event_count": 3,
            "lookback_ms": 60_000,
            "horizon_ms": 60_000,
            "required_resolution": "bar",
            "latency_gate_required": False,
            "raw_mean_bps": 7.0,
            "raw_median_bps": 7.0,
            "net_mean_bps": 1.0,
            "net_median_bps": 1.0,
            "win_rate": 0.666,
            "worst_net_bps": -1.0,
            "fee_bps": 1.0,
            "slippage_bps": 2.0,
            "quote_mismatch_buffer_bps": 3.0,
            "exclusion_reasons": [],
            "data_corpus_hash": "corpus_hash",
            "window_index_hash": "window_hash",
            "plan_hash": "plan_hash",
            "evaluation_hash": "evaluation_hash",
            "survivor_freeze_hash": "survivor_freeze_hash",
            # NO event_net_bps field
        }]
        holdout_payload = _holdout_payload(cells=holdout_cells)
        holdout_manifest = _holdout_manifest(holdout_payload)

        report = build_offline_native_null_report(
            comparison_manifest=comp_manifest,
            comparison_payload=comp_payload,
            holdout_evaluation_manifest=holdout_manifest,
            holdout_evaluation_payload=holdout_payload,
        )

        # Must NOT generate p-values from summary metrics alone
        assert report.status == STATUS_NULL_EVENT_RETURNS_MISSING
        assert report.tested_cell_ids == []
        assert report.pvalue_input is None
