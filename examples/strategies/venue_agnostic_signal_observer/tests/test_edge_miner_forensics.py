"""Tests for offline Edge Miner failure forensics."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.edge_miner_forensics import (
    FIELD_UNAVAILABLE,
)
from examples.strategies.venue_agnostic_signal_observer.edge_miner_forensics import (
    build_failure_forensics,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _base_counts(**updates):
    counts = {
        "raw_cells": 10,
        "cells_tested": 10,
        "candidate_cards": 2,
        "rejected_on_cost_floor": 1,
        "rejected_by_null_mcpt": 1,
        "rejected_by_fdr": 0,
        "rejected_by_dsr": 0,
        "rejected_by_cpcv_nonstationarity": 0,
        "validator_survivors": 0,
        "sent_to_shadow": 0,
        "passing_shadow": 0,
        "final_candidates": 0,
    }
    counts.update(updates)
    return counts


def _write_report(report_dir: Path, counts: dict | None = None, evidence_rows: list[dict] | None = None) -> None:
    _write_json(report_dir / "run_summary.json", {
        "grid_hash": "grid123",
        "stress_corpus_hash": "corpus123",
        "candidate_counts": counts or _base_counts(),
    })
    _write_json(report_dir / "corpus_summary.json", {"status": "NO_VALIDATOR_SURVIVORS", "n_captures": 1, "results": []})
    _write_json(report_dir / "shadow_summary.json", {"status": "NO_VALIDATOR_SURVIVORS", "n_sent_to_shadow": 0, "n_passing_shadow": 0, "results": []})
    rows = evidence_rows if evidence_rows is not None else [
        {
            "candidate_card": {
                "candidate_hash": "cost1",
                "cell_id": "BTC_to_SOL_price_impulse_lb30s_h60s_d0s",
                "source_asset": "BTC",
                "target_asset": "SOL",
                "feature_family": "price_impulse",
                "horizon_seconds": 60,
                "lookback_seconds": 30,
                "entry_delay_seconds": 0,
                "mean_return": 0.0001,
                "hit_rate": 0.6,
                "sharpe_like": 0.4,
                "n_observations": 5,
                "rejection_reason": "BELOW_COST_FLOOR",
            },
            "validator_summary": {"cost_floor": 0.005, "mcpt_result": {"p_value": 0.01, "alpha": 0.05, "status": "PASS"}},
        },
        {
            "candidate_card": {
                "candidate_hash": "null1",
                "cell_id": "ETH_to_LINK_large_trade_lb60s_h180s_d15s",
                "source_asset": "ETH",
                "target_asset": "LINK",
                "feature_family": "large_trade",
                "horizon_seconds": 180,
                "lookback_seconds": 60,
                "entry_delay_seconds": 15,
                "mean_return": 0.0002,
                "hit_rate": 0.5,
                "sharpe_like": 0.2,
                "n_observations": 6,
                "rejection_reason": "NULL_MCPT_FAIL",
            },
            "validator_summary": {"mcpt_result": {"p_value": 0.2, "alpha": 0.05, "status": "FAIL"}},
        },
    ]
    _write_jsonl(report_dir / "validator_evidence.jsonl", rows)


def _write_corpus(tmp_path: Path, stress_count: int, coverage: dict[str, int]):
    corpus = tmp_path / "corpus_manifest.json"
    target = tmp_path / "target_coverage_summary.json"
    _write_json(corpus, {
        "corpus_hash": "corpus123",
        "label_version": "stress_label.v1",
        "source_assets": ["BTC", "ETH"],
        "target_assets": ["SOL", "LINK", "DOGE", "AVAX"],
        "stress_window_count": stress_count,
        "usable_window_count": stress_count,
    })
    _write_json(target, {
        "target_assets": ["SOL", "LINK", "DOGE", "AVAX"],
        "coverage_by_target": coverage,
    })
    return corpus, target


class TestEdgeMinerFailureForensics:
    def test_builds_clean_rejection_when_corpus_adequate_and_no_survivors(self, tmp_path: Path):
        report = tmp_path / "report"
        _write_report(report, counts=_base_counts(rejected_on_cost_floor=1, rejected_by_null_mcpt=1))
        corpus, target = _write_corpus(tmp_path, 25, {"SOL": 25, "LINK": 25, "DOGE": 25, "AVAX": 25})

        result = build_failure_forensics(report, corpus, target)

        assert result.executive_verdict == "CLEAN_REJECTION"
        payload = json.loads((result.forensics_dir / "failure_forensics.json").read_text())
        assert payload["shadow_diagnostics"]["interpretation"] == "SHADOW_NOT_REACHED"

    def test_builds_data_insufficient_rejection_when_stress_windows_low(self, tmp_path: Path):
        report = tmp_path / "report"
        _write_report(report)
        corpus, target = _write_corpus(tmp_path, 9, {"SOL": 9, "LINK": 9, "DOGE": 9, "AVAX": 9})

        result = build_failure_forensics(report, corpus, target)

        assert result.executive_verdict == "DATA_INSUFFICIENT_REJECTION"
        assert "DATA_INSUFFICIENT_REJECTION" in result.contributing_reasons

    def test_flags_target_coverage_limited_when_target_has_zero_coverage(self, tmp_path: Path):
        report = tmp_path / "report"
        _write_report(report)
        corpus, target = _write_corpus(tmp_path, 25, {"SOL": 25, "LINK": 3, "DOGE": 3, "AVAX": 0})

        result = build_failure_forensics(report, corpus, target)

        assert result.executive_verdict == "TARGET_COVERAGE_LIMITED_REJECTION"
        assert "TARGET_COVERAGE_LIMITED_REJECTION" in result.contributing_reasons
        coverage = json.loads((result.forensics_dir / "target_coverage_breakdown.json").read_text())
        assert coverage["missing_target_assets"] == ["AVAX"]

    def test_reports_shadow_not_reached(self, tmp_path: Path):
        report = tmp_path / "report"
        _write_report(report, counts=_base_counts(sent_to_shadow=0))
        corpus, target = _write_corpus(tmp_path, 9, {"SOL": 9, "LINK": 3, "DOGE": 3, "AVAX": 0})

        result = build_failure_forensics(report, corpus, target)
        payload = json.loads((result.forensics_dir / "failure_forensics.json").read_text())

        assert payload["shadow_diagnostics"]["interpretation"] == "SHADOW_NOT_REACHED"

    def test_does_not_invent_unavailable_fields(self, tmp_path: Path):
        report = tmp_path / "report"
        _write_report(report, evidence_rows=[
            {
                "candidate_card": {
                    "candidate_hash": "fdr1",
                    "cell_id": "BTC_to_SOL_price_impulse_lb30s_h60s_d0s",
                    "source_asset": "BTC",
                    "target_asset": "SOL",
                    "feature_family": "price_impulse",
                    "horizon_seconds": 60,
                    "lookback_seconds": 30,
                    "entry_delay_seconds": 0,
                    "rejection_reason": "FDR_FAIL",
                },
                "validator_summary": {"fdr_result": {"p_value": 0.02, "primary_fdr_q": 0.1, "status": "FAIL"}},
            }
        ])
        corpus, target = _write_corpus(tmp_path, 25, {"SOL": 25, "LINK": 25, "DOGE": 25, "AVAX": 25})

        result = build_failure_forensics(report, corpus, target)
        payload = json.loads((result.forensics_dir / "failure_forensics.json").read_text())

        assert payload["fdr_diagnostics"]["family_size"] == FIELD_UNAVAILABLE
        assert payload["top_rejected_candidates"][0]["mean_return_bps"] == FIELD_UNAVAILABLE

    def test_writes_expected_forensics_files(self, tmp_path: Path):
        report = tmp_path / "report"
        _write_report(report)
        corpus, target = _write_corpus(tmp_path, 9, {"SOL": 9, "LINK": 3, "DOGE": 3, "AVAX": 0})

        result = build_failure_forensics(report, corpus, target)

        expected = {
            "failure_forensics.json",
            "failure_forensics.md",
            "candidate_rejection_breakdown.json",
            "target_coverage_breakdown.json",
            "top_rejected_candidates.jsonl",
            "data_adequacy_summary.json",
        }
        assert expected.issubset({path.name for path in result.forensics_dir.iterdir()})
        assert "observer-only offline analysis" in (result.forensics_dir / "failure_forensics.md").read_text()
