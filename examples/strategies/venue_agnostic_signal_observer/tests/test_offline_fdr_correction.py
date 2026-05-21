"""
Tests for Phase 2B-2C2 offline FDR correction.

Uses synthetic JSON fixtures only. No source market data loading.
No null distribution generation, no MCPT, no cost sensitivity,
no candidate falsification, no Family 4 conditioning, no latency diagnostics,
no final offline verdicts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    EXCL_COMPARISON_FAILED,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    EXCL_FAMILY4_CONDITIONING,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    EXCL_FAMILY_NOT_ELIGIBLE,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    EXCL_MISSING_PVALUE,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    EXCL_NOT_EDGE_FAMILY,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    FDR_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    PVALUE_INPUT_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    STATUS_COMPARISON_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    STATUS_FDR_INPUT_PVALUES_MISSING,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    STATUS_INPUT_HASH_MISMATCH,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    STATUS_INVALID_PVALUE_INPUT,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    STATUS_NO_FDR_ELIGIBLE_CELLS,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    STATUS_OFFLINE_FDR_CORRECTION_READY,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    OfflineFdrConfig,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    OfflineFdrPValue,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import _bh_correction
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    _build_cell_exclusion_reasons,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import _by_correction
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    _canonical_json,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    _is_conditioning_family,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    _is_edge_family,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    _is_family_eligible,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    build_offline_fdr_correction_report,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    build_offline_fdr_manifest_payload,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    compute_fdr_config_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    compute_pvalue_input_hash,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    parse_pvalue_input,
)
from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
    write_offline_fdr_correction_outputs,
)


# ---------------------------------------------------------------------------
# Fixture helpers (pure JSON, no source data)
# ---------------------------------------------------------------------------


def _comparison_cell(
    cell_id: str,
    *,
    family_id: str = "family_1_same_venue_quote_basis",
    family_name: str = "Family 1",
    signal_variant: str | None = None,
    lookback_ms: int = 60_000,
    horizon_ms: int = 60_000,
    comparison_passed: bool = True,
    holdout_net_mean_bps: float | None = 1.0,
    holdout_net_median_bps: float | None = 1.0,
    holdout_win_rate: float | None = 0.6,
    holdout_worst_net_bps: float | None = 0.0,
    net_mean_decay_bps: float | None = 0.5,
    net_mean_decay_ratio: float | None = 0.5,
    data_corpus_hash: str = "corpus_hash",
    window_index_hash: str = "window_hash",
    plan_hash: str = "plan_hash",
    evaluation_hash: str = "evaluation_hash",
    survivor_freeze_hash: str = "survivor_freeze_hash",
    holdout_evaluation_hash: str = "holdout_eval_hash",
) -> dict:
    return {
        "cell_id": cell_id,
        "family_id": family_id,
        "family_name": family_name,
        "signal_variant": signal_variant,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "train_valid_event_count": 3,
        "holdout_valid_event_count": 3,
        "train_net_mean_bps": 2.0,
        "holdout_net_mean_bps": holdout_net_mean_bps,
        "train_net_median_bps": 2.0,
        "holdout_net_median_bps": holdout_net_median_bps,
        "train_win_rate": 0.75,
        "holdout_win_rate": holdout_win_rate,
        "train_worst_net_bps": 0.0,
        "holdout_worst_net_bps": holdout_worst_net_bps,
        "net_mean_decay_bps": net_mean_decay_bps,
        "net_mean_decay_ratio": net_mean_decay_ratio,
        "holdout_survival_status": "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY",
        "comparison_passed": comparison_passed,
        "failure_reasons": [] if comparison_passed else ["failed_threshold"],
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": "discovery_hash",
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_hash": survivor_freeze_hash,
        "holdout_evaluation_hash": holdout_evaluation_hash,
    }


def _comparison_payload(
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
    status: str = "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY",
    train_survivor_cell_ids: list[str] | None = None,
) -> dict:
    ids = train_survivor_cell_ids or [c["cell_id"] for c in cells]
    return {
        "schema_version": "offline_train_holdout_comparison_v1",
        "status": status,
        "comparison_cells": cells,
        "train_survivor_cell_ids": ids,
        "holdout_surviving_cell_ids": [c["cell_id"] for c in cells if c.get("comparison_passed", False)],
        "holdout_failed_cell_ids": [c["cell_id"] for c in cells if not c.get("comparison_passed", False)],
        "missing_holdout_cell_ids": [],
        "comparison_config_hash": comparison_config_hash,
        "comparison_hash": comparison_hash,
        "metadata": {"comparison_config": {"min_holdout_net_mean_bps": 0.0}},
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
        "plan_hash": plan_hash,
        "evaluation_hash": evaluation_hash,
        "survivor_freeze_config_hash": "sf_config_hash",
        "survivor_freeze_hash": survivor_freeze_hash,
        "holdout_evaluation_hash": holdout_evaluation_hash,
    }


def _comparison_manifest(
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
        "train_survivor_cell_count": len(payload["train_survivor_cell_ids"]),
        "holdout_surviving_cell_count": len(payload["holdout_surviving_cell_ids"]),
        "holdout_failed_cell_count": len(payload["holdout_failed_cell_ids"]),
        "safety": "public_data_observer_only",
    }


def _pvalue_entry(cell_id: str, p_value: float, **kw: str) -> dict:
    return {
        "cell_id": cell_id,
        "p_value": p_value,
        "test_name": kw.get("test_name", "placeholder_external_test"),
        "evidence_hash": kw.get("evidence_hash"),
        "metadata": {},
    }


def _pvalue_input(entries: list[dict]) -> dict:
    return {
        "schema_version": PVALUE_INPUT_SCHEMA_VERSION,
        "source": "external_or_future_null_layer",
        "pvalues": entries,
    }


def _default_comp_payload(cells: list[dict] | None = None) -> dict:
    if cells is None:
        cells = [
            _comparison_cell("cell_a"),
            _comparison_cell("cell_b"),
        ]
    return _comparison_payload(
        cells=cells,
        comparison_hash=_make_comparison_hash(cells),
    )


def _make_comparison_hash(cells: list[dict]) -> str:
    """Generate a deterministic comparison hash matching the FDR module format."""
    summaries = [
        {
            "cell_id": c["cell_id"],
            "holdout_survival_status": c.get("holdout_survival_status", ""),
            "comparison_passed": c.get("comparison_passed", False),
            "failure_reasons": c.get("failure_reasons", []),
            "holdout_net_mean_bps": c.get("holdout_net_mean_bps"),
            "holdout_net_median_bps": c.get("holdout_net_median_bps"),
            "holdout_win_rate": c.get("holdout_win_rate"),
            "holdout_worst_net_bps": c.get("holdout_worst_net_bps"),
            "net_mean_decay_bps": c.get("net_mean_decay_bps"),
            "net_mean_decay_ratio": c.get("net_mean_decay_ratio"),
        }
        for c in cells
    ]
    payload = {
        "schema_version": "offline_train_holdout_comparison_v1",
        "data_corpus_hash": "corpus_hash",
        "window_index_hash": "window_hash",
        "discovery_config_hash": "discovery_hash",
        "plan_hash": "plan_hash",
        "evaluation_hash": "evaluation_hash",
        "survivor_freeze_hash": "survivor_freeze_hash",
        "holdout_evaluation_hash": "holdout_eval_hash",
        "comparison_config_hash": "comp_config_hash",
        "train_survivor_cell_ids": [c["cell_id"] for c in cells],
        "holdout_surviving_cell_ids": [c["cell_id"] for c in cells if c.get("comparison_passed")],
        "holdout_failed_cell_ids": [c["cell_id"] for c in cells if not c.get("comparison_passed")],
        "missing_holdout_cell_ids": [],
        "comparison_cell_summaries": summaries,
        "status": "OFFLINE_TRAIN_HOLDOUT_COMPARISON_READY",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


# ============================= TESTS =============================


class TestComparisonHashMismatch:
    """Tests 1-2: Identity checks."""

    def test_rejects_comparison_hash_mismatch(self) -> None:
        cells = [_comparison_cell("cell_a")]
        payload = _comparison_payload(cells=cells, comparison_hash="hash_from_payload")
        manifest = _comparison_manifest(payload, comparison_hash="different_hash")

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
        )

        assert report.status == STATUS_COMPARISON_HASH_MISMATCH

    def test_rejects_lineage_hash_mismatch(self) -> None:
        cells = [_comparison_cell("cell_a")]
        payload = _comparison_payload(
            cells=cells,
            comparison_hash="comp_hash_123",
            data_corpus_hash="corpus_abc",
        )
        manifest = _comparison_manifest(
            payload,
            comparison_hash="comp_hash_123",
        )
        # Tamper manifest data_corpus_hash
        manifest["data_corpus_hash"] = "corpus_different"

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
        )

        assert report.status == STATUS_INPUT_HASH_MISMATCH


class TestFdrConfigValidation:
    """Tests 3-5: FDR config validation."""

    def test_rejects_invalid_method(self) -> None:
        with pytest.raises(ValueError, match="Unknown FDR method"):
            OfflineFdrConfig(method="invalid")

    def test_rejects_alpha_zero(self) -> None:
        with pytest.raises(ValueError, match="alpha must be in"):
            OfflineFdrConfig(alpha=0.0)

    def test_rejects_alpha_negative(self) -> None:
        with pytest.raises(ValueError, match="alpha must be in"):
            OfflineFdrConfig(alpha=-0.1)

    def test_rejects_alpha_one(self) -> None:
        with pytest.raises(ValueError, match="alpha must be in"):
            OfflineFdrConfig(alpha=1.0)

    def test_rejects_alpha_above_one(self) -> None:
        with pytest.raises(ValueError, match="alpha must be in"):
            OfflineFdrConfig(alpha=1.5)

    def test_rejects_unknown_config_key(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction import (
            _validate_config_keys,
        )
        with pytest.raises(ValueError, match="Unknown FDR config keys"):
            _validate_config_keys({"method": "bh", "nonexistent_key": True})


class TestMissingPValueInput:
    """Test 6: Missing p-value input."""

    def test_missing_pvalue_input(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=None,
        )

        assert report.status == STATUS_FDR_INPUT_PVALUES_MISSING
        assert report.pvalue_input_hash is None
        assert len(report.fdr_passed_cell_ids) == 0


class TestNoEligibleCells:
    """Test 7: No eligible comparison survivors."""

    def test_no_eligible_cells(self) -> None:
        cells = [
            _comparison_cell("cell_family4", family_id="family_4_conditioning_test",
                             comparison_passed=False),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_family4", 0.05)])
        parsed = parse_pvalue_input(pv)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_NO_FDR_ELIGIBLE_CELLS


class TestFamily4Exclusion:
    """Test 8: Family 4 cells excluded."""

    def test_family4_excluded(self) -> None:
        cells = [
            _comparison_cell("cell_family4", family_id="family_4_conditioning_test",
                             comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_family4", 0.05)])
        parsed = parse_pvalue_input(pv)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_NO_FDR_ELIGIBLE_CELLS

        reasons = report.exclusion_reasons_by_cell.get("cell_family4", [])
        assert EXCL_FAMILY4_CONDITIONING in reasons


class TestComparisonFailedExclusion:
    """Test 9: Comparison-failed cells excluded."""

    def test_comparison_failed_excluded(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=False),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.05)])
        parsed = parse_pvalue_input(pv)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_NO_FDR_ELIGIBLE_CELLS
        reasons = report.exclusion_reasons_by_cell.get("cell_a", [])
        assert EXCL_COMPARISON_FAILED in reasons

    def test_require_comparison_passed_false_allows_failed(self) -> None:
        """When require_comparison_passed=False, failed cells can be eligible."""
        cells = [
            _comparison_cell("cell_a", comparison_passed=False),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.05)])
        parsed = parse_pvalue_input(pv)
        config = OfflineFdrConfig(require_comparison_passed=False)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parsed,
        )

        # Should be ready since cell is now eligible with p-value
        assert report.status == STATUS_OFFLINE_FDR_CORRECTION_READY


class TestMissingPValue:
    """Test 10: Missing p-value for eligible cell."""

    def test_missing_pvalue_recorded(self) -> None:
        """
        P-value for eligible cell is missing. Only supply p-values for
        known comparison cells. This test verifies the missing_pvalue
        exclusion for the eligible cell.
        """
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        # Provide p-value for cell_b only; cell_a has no p-value
        pv = _pvalue_input([
            _pvalue_entry("cell_b", 0.01),
        ])
        parsed = parse_pvalue_input(pv)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        # cell_a should be missing p-value, cell_b should be eligible
        assert report.status == STATUS_OFFLINE_FDR_CORRECTION_READY
        assert "cell_b" in report.eligible_cell_ids
        assert "cell_a" not in report.eligible_cell_ids
        reasons_a = report.exclusion_reasons_by_cell.get("cell_a", [])
        assert EXCL_MISSING_PVALUE in reasons_a

    def test_missing_pvalue_for_eligible_cell(self) -> None:
        """Cell exists but no p-value supplied for it."""
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        # Only supply p-value for cell_b
        pv = _pvalue_input([_pvalue_entry("cell_b", 0.05)])
        parsed = parse_pvalue_input(pv)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        # cell_a has no p-value -> exclusion
        assert STATUS_OFFLINE_FDR_CORRECTION_READY in report.status
        reasons_a = report.exclusion_reasons_by_cell.get("cell_a", [])
        assert EXCL_MISSING_PVALUE in reasons_a


class TestDuplicatePValueRejected:
    """Test 11: Duplicate p-values rejected."""

    def test_duplicate_pvalue_rejected(self) -> None:
        pv = _pvalue_input([
            _pvalue_entry("cell_a", 0.05),
            _pvalue_entry("cell_a", 0.01),  # duplicate
        ])
        with pytest.raises(ValueError, match="Duplicate p-value for cell"):
            parse_pvalue_input(pv)


class TestUnknownCellPValueRejected:
    """Test 12: P-value for unknown cell is rejected loudly."""

    def test_unknown_cell_pvalue_rejected(self) -> None:
        """P-value for cell not in comparison payload returns INVALID_PVALUE_INPUT."""
        pv = _pvalue_input([_pvalue_entry("unknown_cell", 0.05)])
        parsed = parse_pvalue_input(pv)
        assert "unknown_cell" in parsed

        cells = [_comparison_cell("cell_a")]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_INVALID_PVALUE_INPUT

    def test_unknown_cell_reason_recorded(self) -> None:
        """Unknown p-value cell records reason unknown_pvalue_cell:<cell_id>."""
        pv = _pvalue_input([_pvalue_entry("ghost_cell", 0.05)])
        parsed = parse_pvalue_input(pv)

        cells = [_comparison_cell("cell_a")]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_INVALID_PVALUE_INPUT
        reasons = report.metadata.get("unknown_pvalue_reasons", [])
        assert "unknown_pvalue_cell:ghost_cell" in reasons

    def test_unknown_cell_prevents_fdr_application(self) -> None:
        """Unknown p-value cell prevents any FDR correction."""
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.001), _pvalue_entry("ghost", 0.05)])
        parsed = parse_pvalue_input(pv)

        cells = [_comparison_cell("cell_a")]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_INVALID_PVALUE_INPUT
        # No cells should be in FDR pass/fail lists
        assert len(report.fdr_passed_cell_ids) == 0
        assert len(report.fdr_failed_cell_ids) == 0
        assert len(report.eligible_cell_ids) == 0

    def test_unknown_cell_mixed_with_known_still_rejected(self) -> None:
        """Even with both valid and unknown p-values, the whole request is rejected."""
        pv = _pvalue_input([
            _pvalue_entry("cell_a", 0.001),
            _pvalue_entry("ghost", 0.05),
        ])
        parsed = parse_pvalue_input(pv)

        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_INVALID_PVALUE_INPUT
        assert "cell_a" not in report.eligible_cell_ids
        assert len(report.eligible_cell_ids) == 0


class TestInvalidPValueRange:
    """Test 13: P-value outside [0, 1]."""

    def test_pvalue_below_zero_rejected(self) -> None:
        pv = _pvalue_input([_pvalue_entry("cell_a", -0.01)])
        with pytest.raises(ValueError, match="p_value must be in"):
            parse_pvalue_input(pv)

    def test_pvalue_above_one_rejected(self) -> None:
        pv = _pvalue_input([_pvalue_entry("cell_a", 1.5)])
        with pytest.raises(ValueError, match="p_value must be in"):
            parse_pvalue_input(pv)

    def test_pvalue_at_zero_valid(self) -> None:
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.0)])
        parsed = parse_pvalue_input(pv)
        assert parsed["cell_a"].p_value == 0.0

    def test_pvalue_at_one_valid(self) -> None:
        pv = _pvalue_input([_pvalue_entry("cell_a", 1.0)])
        parsed = parse_pvalue_input(pv)
        assert parsed["cell_a"].p_value == 1.0


class TestBHCorrection:
    """Test 14: BH correction produces expected pass/fail."""

    def test_bh_correction_basic(self) -> None:
        """Known p-values: should pass the smallest, fail the largest."""
        pvalues = [
            ("cell_a", 0.001),
            ("cell_b", 0.01),
            ("cell_c", 0.05),
            ("cell_d", 0.10),
            ("cell_e", 0.50),
        ]
        results = _bh_correction(pvalues, alpha=0.05)

        # cell_a (p=0.001, rank=1, threshold=0.01) -> pass
        assert results["cell_a"]["fdr_passed"] is True
        # cell_e (p=0.5, rank=5, threshold=0.05) -> fail
        assert results["cell_e"]["fdr_passed"] is False

    def test_bh_all_pass(self) -> None:
        """All p-values very small."""
        pvalues = [
            ("cell_a", 0.001),
            ("cell_b", 0.002),
            ("cell_c", 0.003),
        ]
        results = _bh_correction(pvalues, alpha=0.05)
        for cell_id in ["cell_a", "cell_b", "cell_c"]:
            assert results[cell_id]["fdr_passed"] is True

    def test_bh_none_pass(self) -> None:
        """All p-values very large."""
        pvalues = [
            ("cell_a", 0.9),
            ("cell_b", 0.91),
            ("cell_c", 0.95),
        ]
        results = _bh_correction(pvalues, alpha=0.05)
        for cell_id in ["cell_a", "cell_b", "cell_c"]:
            assert results[cell_id]["fdr_passed"] is False

    def test_bh_with_pvalue_input_flow(self) -> None:
        """End-to-end: BH via builder."""
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
            _comparison_cell("cell_c", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        pv = _pvalue_input([
            _pvalue_entry("cell_a", 0.001),
            _pvalue_entry("cell_b", 0.01),
            _pvalue_entry("cell_c", 0.50),
        ])
        parsed = parse_pvalue_input(pv)
        config = OfflineFdrConfig(method="bh", alpha=0.05)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_OFFLINE_FDR_CORRECTION_READY
        assert "cell_a" in report.fdr_passed_cell_ids
        assert "cell_b" in report.fdr_passed_cell_ids
        assert "cell_c" not in report.fdr_passed_cell_ids


class TestBYConservative:
    """Test 15: BY is more conservative than or equal to BH."""

    def test_by_more_conservative(self) -> None:
        """For same p-values, BY should produce same or fewer passes."""
        pvalues = [
            ("cell_a", 0.005),
            ("cell_b", 0.01),
            ("cell_c", 0.03),
            ("cell_d", 0.06),
            ("cell_e", 0.10),
        ]
        bh_results = _bh_correction(pvalues, alpha=0.05)
        by_results = _by_correction(pvalues, alpha=0.05)

        bh_passed = {cid for cid, r in bh_results.items() if r["fdr_passed"]}
        by_passed = {cid for cid, r in by_results.items() if r["fdr_passed"]}

        # BY passes should be subset of BH passes
        assert by_passed.issubset(bh_passed), (
            f"BY passed {by_passed} should be subset of BH passed {bh_passed}"
        )

    def test_by_with_pvalue_input_flow(self) -> None:
        """End-to-end: BY via builder."""
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        pv = _pvalue_input([
            _pvalue_entry("cell_a", 0.001),
            _pvalue_entry("cell_b", 0.50),
        ])
        parsed = parse_pvalue_input(pv)
        config = OfflineFdrConfig(method="by", alpha=0.05)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parsed,
        )

        assert report.status == STATUS_OFFLINE_FDR_CORRECTION_READY
        # cell_a (rank=1, thresh=0.05/(2*H_2) approx 0.015) should pass with p=0.001
        assert "cell_a" in report.fdr_passed_cell_ids


class TestMonotonicQValues:
    """Test 16: Q-values are monotonic nondecreasing."""

    def test_bh_qvalues_monotonic(self) -> None:
        pvalues = [
            ("cell_a", 0.001),
            ("cell_b", 0.01),
            ("cell_c", 0.05),
            ("cell_d", 0.10),
            ("cell_e", 0.50),
        ]
        results = _bh_correction(pvalues, alpha=0.05)
        sorted_ids = sorted(results.keys(), key=lambda cid: (results[cid]["p_value"], cid))
        q_vals = [results[cid]["q_value"] for cid in sorted_ids]
        for i in range(1, len(q_vals)):
            assert q_vals[i] >= q_vals[i - 1] - 1e-10, (
                f"Q-values not monotonic at index {i}: {q_vals[i-1]} > {q_vals[i]}"
            )

    def test_by_qvalues_monotonic(self) -> None:
        pvalues = [
            ("cell_a", 0.001),
            ("cell_b", 0.01),
            ("cell_c", 0.05),
        ]
        results = _by_correction(pvalues, alpha=0.05)
        sorted_ids = sorted(results.keys(), key=lambda cid: (results[cid]["p_value"], cid))
        q_vals = [results[cid]["q_value"] for cid in sorted_ids]
        for i in range(1, len(q_vals)):
            assert q_vals[i] >= q_vals[i - 1] - 1e-10


class TestTiedPValues:
    """Test 17: Tied p-values handled deterministically by cell ID."""

    def test_tied_pvalues_deterministic(self) -> None:
        pvalues = [
            ("cell_b", 0.05),
            ("cell_a", 0.05),
            ("cell_c", 0.05),
        ]
        results1 = _bh_correction(pvalues, alpha=0.05)
        results2 = _bh_correction(pvalues, alpha=0.05)

        # Should be identical
        for key in results1:
            assert results1[key]["rank"] == results2[key]["rank"]
            assert results1[key]["q_value"] == results2[key]["q_value"]

        # cell_a should rank first (alphabetically among ties)
        assert results1["cell_a"]["rank"] == 1
        assert results1["cell_b"]["rank"] == 2
        assert results1["cell_c"]["rank"] == 3


class TestFdrConfigHash:
    """Tests 18-19: fdr_config_hash changes appropriately."""

    def test_config_hash_changes_with_alpha(self) -> None:
        c1 = OfflineFdrConfig(alpha=0.05)
        c2 = OfflineFdrConfig(alpha=0.10)
        assert compute_fdr_config_hash(c1) != compute_fdr_config_hash(c2)

    def test_config_hash_changes_with_method(self) -> None:
        c1 = OfflineFdrConfig(method="by")
        c2 = OfflineFdrConfig(method="bh")
        assert compute_fdr_config_hash(c1) != compute_fdr_config_hash(c2)

    def test_config_hash_changes_with_eligible_families(self) -> None:
        c1 = OfflineFdrConfig(eligible_families=("family_1", "family_2"))
        c2 = OfflineFdrConfig(eligible_families=("family_1", "family_2", "family_3"))
        assert compute_fdr_config_hash(c1) != compute_fdr_config_hash(c2)

    def test_config_hash_changes_with_require_edge(self) -> None:
        c1 = OfflineFdrConfig(require_edge_family=True)
        c2 = OfflineFdrConfig(require_edge_family=False)
        assert compute_fdr_config_hash(c1) != compute_fdr_config_hash(c2)

    def test_config_hash_deterministic(self) -> None:
        c1 = OfflineFdrConfig()
        c2 = OfflineFdrConfig()
        assert compute_fdr_config_hash(c1) == compute_fdr_config_hash(c2)


class TestPValueInputHash:
    """Test 20: pvalue_input_hash changes when p-values change."""

    def test_pvalue_input_hash_changes(self) -> None:
        pv1 = {"cell_a": OfflineFdrPValue(cell_id="cell_a", p_value=0.01)}
        pv2 = {"cell_a": OfflineFdrPValue(cell_id="cell_a", p_value=0.02)}
        assert compute_pvalue_input_hash(pv1) != compute_pvalue_input_hash(pv2)

    def test_pvalue_input_hash_deterministic(self) -> None:
        pv1 = {"cell_a": OfflineFdrPValue(cell_id="cell_a", p_value=0.01)}
        pv2 = {"cell_a": OfflineFdrPValue(cell_id="cell_a", p_value=0.01)}
        assert compute_pvalue_input_hash(pv1) == compute_pvalue_input_hash(pv2)


class TestFdrHashChanges:
    """Test 21: fdr_hash changes when p-values change."""

    def test_fdr_hash_changes_with_pvalues(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        pv1 = _pvalue_input([
            _pvalue_entry("cell_a", 0.001),
            _pvalue_entry("cell_b", 0.01),
        ])
        pv2 = _pvalue_input([
            _pvalue_entry("cell_a", 0.001),
            _pvalue_entry("cell_b", 0.50),  # different
        ])

        config = OfflineFdrConfig(method="bh")
        report1 = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv1),
        )
        report2 = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv2),
        )

        assert report1.fdr_hash != report2.fdr_hash

    def test_fdr_hash_deterministic_identical_pvalues(self) -> None:
        """Different origins but same p-values produce same hash."""
        cells_a = [
            _comparison_cell("cell_a", comparison_passed=True),
        ]
        cells_b = [
            _comparison_cell("cell_a", comparison_passed=True),
        ]
        payload_a = _default_comp_payload(cells_a)
        payload_b = _default_comp_payload(cells_b)
        manifest_a = _comparison_manifest(payload_a)
        manifest_b = _comparison_manifest(payload_b)

        pv = _pvalue_input([_pvalue_entry("cell_a", 0.001)])

        config = OfflineFdrConfig(method="bh")
        report1 = build_offline_fdr_correction_report(
            comparison_manifest=manifest_a,
            comparison_payload=payload_a,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv),
        )
        report2 = build_offline_fdr_correction_report(
            comparison_manifest=manifest_b,
            comparison_payload=payload_b,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv),
        )

        assert report1.fdr_hash == report2.fdr_hash


class TestIdenticalInputsIdenticalOutput:
    """Test 22: Identical runs produce identical parsed JSON and fdr_hash."""

    def test_identical_runs_identical_output(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
            _comparison_cell("cell_b", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([
            _pvalue_entry("cell_a", 0.001),
            _pvalue_entry("cell_b", 0.01),
        ])
        config = OfflineFdrConfig(method="by")

        report1 = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv),
        )
        report2 = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv),
        )

        # fdr_hash same
        assert report1.fdr_hash == report2.fdr_hash

        # JSON output same (canonical form)
        json1 = _canonical_json({
            "status": report1.status,
            "fdr_hash": report1.fdr_hash,
            "fdr_config_hash": report1.fdr_config_hash,
            "pvalue_input_hash": report1.pvalue_input_hash,
            "eligible_cell_ids": report1.eligible_cell_ids,
            "fdr_passed_cell_ids": report1.fdr_passed_cell_ids,
            "fdr_failed_cell_ids": report1.fdr_failed_cell_ids,
        })
        json2 = _canonical_json({
            "status": report2.status,
            "fdr_hash": report2.fdr_hash,
            "fdr_config_hash": report2.fdr_config_hash,
            "pvalue_input_hash": report2.pvalue_input_hash,
            "eligible_cell_ids": report2.eligible_cell_ids,
            "fdr_passed_cell_ids": report2.fdr_passed_cell_ids,
            "fdr_failed_cell_ids": report2.fdr_failed_cell_ids,
        })
        assert json1 == json2


class TestOutputDirectory:
    """Test 23: Output directory does not overwrite by default."""

    def test_output_no_overwrite_by_default(self, tmp_path: Path) -> None:
        cells = [_comparison_cell("cell_a")]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
        )

        run_dir = tmp_path / "fdr_test" / report.run_id
        run_dir.mkdir(parents=True)

        # Write a dummy file to make directory non-empty
        (run_dir / "existing.txt").write_text("existing")

        # Attempting to write should fail because directory is non-empty
        from examples.strategies.venue_agnostic_signal_observer.run_artifacts import safe_output_dir
        with pytest.raises(FileExistsError):
            safe_output_dir(run_dir)


class TestManifestIntegrity:
    """Test 24: Manifest includes all lineage hashes."""

    def test_manifest_includes_all_hashes(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.001)])
        config = OfflineFdrConfig(method="bh")

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv),
        )

        manifest_payload = build_offline_fdr_manifest_payload(
            report,
            comparison_manifest_path="/path/to/manifest.json",
        )

        assert manifest_payload["data_corpus_hash"] == "corpus_hash"
        assert manifest_payload["window_index_hash"] == "window_hash"
        assert manifest_payload["discovery_config_hash"] == "discovery_hash"
        assert manifest_payload["plan_hash"] == "plan_hash"
        assert manifest_payload["evaluation_hash"] == "evaluation_hash"
        assert manifest_payload["survivor_freeze_hash"] == "survivor_freeze_hash"
        assert manifest_payload["holdout_evaluation_hash"] == "holdout_eval_hash"
        assert manifest_payload["comparison_config_hash"] == "comp_config_hash"
        assert manifest_payload["comparison_hash"] is not None
        assert manifest_payload["fdr_config_hash"] is not None
        assert manifest_payload["pvalue_input_hash"] is not None
        assert manifest_payload["fdr_hash"] is not None
        assert manifest_payload["method"] == "bh"
        assert manifest_payload["alpha"] == 0.05
        assert manifest_payload["fdr_family_size"] == 1
        assert manifest_payload["fdr_passed_cell_count"] >= 0
        assert manifest_payload["status"] == STATUS_OFFLINE_FDR_CORRECTION_READY
        assert manifest_payload["safety"] == "public_data_observer_only"

    def test_manifest_no_pvalue_input(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=None,
        )

        manifest_payload = build_offline_fdr_manifest_payload(
            report,
            comparison_manifest_path="/path/to/manifest.json",
        )

        assert manifest_payload["pvalue_input_hash"] is None
        assert manifest_payload["status"] == STATUS_FDR_INPUT_PVALUES_MISSING


class TestSafety:
    """Test 25: Safety — no forbidden paths."""

    def test_no_forbidden_imports(self) -> None:
        """Verify that the FDR module does not import forbidden modules."""
        import ast

        fdr_path = Path(__file__).resolve().parent.parent / "offline_fdr_correction.py"
        with open(fdr_path) as f:
            tree = ast.parse(f.read())

        forbidden_imports = {
            "OrderFactory", "submit_order", "TradingNode", "LiveNode",
            "private_key", "wallet", "signing",
            "CANDIDATE_FOR_LIVE", "TRADE_READY", "EXECUTION_READY",
            "POLYMARKET_PK", "KRAKEN_API_KEY",
            "run_permutation_null", "permutation",
            "cost_sensitivity", "ShadowExecutor", "candidate_falsification",
            "NO_EDGE_AFTER_COSTS", "REJECTED",
        }

        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_imports:
                found.add(node.id)
            if isinstance(node, ast.Attribute):
                # Check for attribute access like obj.cost_sensitivity
                if node.attr in forbidden_imports:
                    found.add(node.attr)

        assert not found, (
            f"Forbidden identifiers found in FDR correction module: {sorted(found)}"
        )

    def test_no_forbidden_imports_runner(self) -> None:
        """Verify that the runner does not import forbidden modules."""
        import ast

        runner_path = Path(__file__).resolve().parent.parent / "run_offline_fdr_correction.py"
        if not runner_path.exists():
            pytest.skip("Runner file not found")

        with open(runner_path) as f:
            tree = ast.parse(f.read())

        forbidden_imports = {
            "OrderFactory", "submit_order", "TradingNode", "LiveNode",
            "private_key", "wallet", "signing",
            "POLYMARKET_PK", "KRAKEN_API_KEY",
            "run_permutation_null", "mcpt", "permutation",
            "cost_sensitivity", "ShadowExecutor", "candidate_falsification",
            "NO_EDGE_AFTER_COSTS", "REJECTED",
        }

        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_imports:
                found.add(node.id)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_imports:
                found.add(node.attr)

        assert not found, (
            f"Forbidden identifiers found in FDR runner: {sorted(found)}"
        )

    def test_no_forbidden_statuses_in_module_constants(self) -> None:
        """Verify the forbidden status constants are not defined."""
        module = (
            "examples.strategies.venue_agnostic_signal_observer.offline_fdr_correction"
        )
        import importlib
        mod = importlib.import_module(module)

        forbidden_statuses = {
            "CANDIDATE", "CANDIDATE_FOR_LIVE", "TRADE_READY", "EXECUTION_READY",
            "EDGE_FOUND", "BOT_ALLOWED", "REJECTED", "NO_EDGE_AFTER_COSTS",
        }

        for name in dir(mod):
            if name in forbidden_statuses:
                pytest.fail(f"Forbidden status constant defined: {name}")


# ---------------------------------------------------------------------------
# Additional utility / edge-case tests
# ---------------------------------------------------------------------------


class TestEmptyFamily:
    def test_empty_pvalues_no_crash_bh(self) -> None:
        results = _bh_correction([], alpha=0.05)
        assert results == {}

    def test_empty_pvalues_no_crash_by(self) -> None:
        results = _by_correction([], alpha=0.05)
        assert results == {}

    def test_empty_comparison_cells(self) -> None:
        payload = _comparison_payload(cells=[], comparison_hash="empty_hash")
        manifest = _comparison_manifest(payload, comparison_hash="empty_hash")
        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
        )
        assert report.status in (STATUS_NO_FDR_ELIGIBLE_CELLS, STATUS_FDR_INPUT_PVALUES_MISSING)


class TestEdgeFamilyHelpers:
    def test_is_edge_family(self) -> None:
        assert _is_edge_family("family_1_same_venue_quote_basis") is True
        assert _is_edge_family("family_2_source_move_impulse") is True
        assert _is_edge_family("family_3_other") is True
        assert _is_edge_family("family_4_conditioning") is False
        assert _is_edge_family("unknown_family") is False

    def test_is_conditioning_family(self) -> None:
        assert _is_conditioning_family("family_4_conditioning_test") is True
        assert _is_conditioning_family("conditioning_family") is True
        assert _is_conditioning_family("family_1_edge") is False
        assert _is_conditioning_family("family_2_source") is False

    def test_is_family_eligible(self) -> None:
        eligible = ("family_1", "family_2", "family_3")
        assert _is_family_eligible("family_1_same_venue", eligible) is True
        assert _is_family_eligible("family_4_conditioning", eligible) is False


class TestBuildCellExclusion:
    def test_comparison_failed_exclusion(self) -> None:
        cell = _comparison_cell("cell_a", comparison_passed=False)
        config = OfflineFdrConfig()
        reasons = _build_cell_exclusion_reasons(cell, config)
        assert EXCL_COMPARISON_FAILED in reasons

    def test_family_not_eligible(self) -> None:
        cell = _comparison_cell("cell_a", family_id="family_4_conditioning",
                                comparison_passed=True)
        config = OfflineFdrConfig()
        reasons = _build_cell_exclusion_reasons(cell, config)
        assert EXCL_FAMILY_NOT_ELIGIBLE in reasons

    def test_not_edge_family(self) -> None:
        cell = _comparison_cell("cell_a", family_id="some_other_family",
                                comparison_passed=True)
        config = OfflineFdrConfig(
            eligible_families=("some_other_family",),
            require_edge_family=True,
        )
        reasons = _build_cell_exclusion_reasons(cell, config)
        assert EXCL_NOT_EDGE_FAMILY in reasons

    def test_family4_excluded(self) -> None:
        cell = _comparison_cell("cell_a", family_id="family_4_conditioning",
                                comparison_passed=True)
        config = OfflineFdrConfig(
            eligible_families=("family_4",),
            require_edge_family=False,
        )
        reasons = _build_cell_exclusion_reasons(cell, config)
        assert EXCL_FAMILY4_CONDITIONING in reasons

    def test_eligible_cell_no_reasons(self) -> None:
        cell = _comparison_cell("cell_a", comparison_passed=True)
        config = OfflineFdrConfig()
        reasons = _build_cell_exclusion_reasons(cell, config)
        assert reasons == []


class TestPValueInputParsing:
    def test_invalid_schema_version(self) -> None:
        pv = {"schema_version": "wrong_version", "pvalues": []}
        with pytest.raises(ValueError, match="Unknown p-value input schema version"):
            parse_pvalue_input(pv)

    def test_empty_pvalues_array(self) -> None:
        pv = _pvalue_input([])
        with pytest.raises(ValueError, match="no pvalues array"):
            parse_pvalue_input(pv)

    def test_non_dict_entry(self) -> None:
        pv = {"schema_version": PVALUE_INPUT_SCHEMA_VERSION, "source": "test", "pvalues": [42]}
        with pytest.raises(ValueError, match="is not a dict"):
            parse_pvalue_input(pv)

    def test_missing_cell_id(self) -> None:
        pv = {"schema_version": PVALUE_INPUT_SCHEMA_VERSION, "source": "test", "pvalues": [{"p_value": 0.05}]}
        with pytest.raises(ValueError, match="missing cell_id"):
            parse_pvalue_input(pv)

    def test_missing_p_value(self) -> None:
        pv = {"schema_version": PVALUE_INPUT_SCHEMA_VERSION, "source": "test", "pvalues": [{"cell_id": "cell_a"}]}
        with pytest.raises(ValueError, match="missing p_value"):
            parse_pvalue_input(pv)


class TestWriteOutputs:
    def test_write_outputs_creates_files(self, tmp_path: Path) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.001)])
        config = OfflineFdrConfig(method="bh")

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            fdr_config=config,
            pvalue_input=parse_pvalue_input(pv),
        )

        out_dir = tmp_path / "fdr_out" / report.run_id
        outputs = write_offline_fdr_correction_outputs(
            report,
            out_dir,
            comparison_manifest_path="/path/to/manifest.json",
            overwrite=False,
        )

        assert outputs["result_path"].exists()
        assert outputs["manifest_path"].exists()

        # Verify result JSON content
        result = json.loads(outputs["result_path"].read_text())
        assert result["schema_version"] == FDR_SCHEMA_VERSION
        assert result["status"] == STATUS_OFFLINE_FDR_CORRECTION_READY
        assert result["fdr_hash"] == report.fdr_hash
        assert len(result["cell_results"]) == 1

        # Verify manifest JSON content
        mf = json.loads(outputs["manifest_path"].read_text())
        assert mf["phase"] == "offline_fdr_correction"
        assert mf["fdr_hash"] == report.fdr_hash
        assert mf["safety"] == "public_data_observer_only"
        assert mf["comparison_manifest_path"] == "/path/to/manifest.json"


class TestForbiddenStatusesNotEmitted:
    """Ensure no forbidden statuses appear in output."""

    def test_no_forbidden_status_in_ready(self) -> None:
        cells = [
            _comparison_cell("cell_a", comparison_passed=True),
        ]
        payload = _default_comp_payload(cells)
        manifest = _comparison_manifest(payload)
        pv = _pvalue_input([_pvalue_entry("cell_a", 0.001)])

        report = build_offline_fdr_correction_report(
            comparison_manifest=manifest,
            comparison_payload=payload,
            pvalue_input=parse_pvalue_input(pv),
        )

        forbidden = {
            "CANDIDATE", "CANDIDATE_FOR_LIVE", "TRADE_READY", "EXECUTION_READY",
            "EDGE_FOUND", "BOT_ALLOWED", "REJECTED", "NO_EDGE_AFTER_COSTS",
        }
        assert report.status not in forbidden, f"Status {report.status} is forbidden"
