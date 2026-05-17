"""Tests for diagnostic candidate falsification summary."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from venue_agnostic_signal_observer.candidate_falsification import (
    COST_WALL_BLOCKED,
    FALSIFICATION_SUMMARY_READY,
    MISSING_REQUIRED_REPORTS,
    NO_EVALUATED_GROUPS,
    FalsificationSummary,
    compute_candidate_falsification_summary,
    validate_verdict,
    write_candidate_falsification_reports,
)


def _group(
    *,
    source_venue="binance_perp",
    target_venue="coinbase",
    symbol="ETH/USD",
    signal_type="signed_imbalance",
    lookback_ms=500,
    horizon_ms=1000,
    mean_raw_bps=2.0,
    mean_net_bps=-48.0,
    valid_count=100,
    win_rate=0.55,
) -> dict:
    return {
        "source_venue": source_venue,
        "target_venue": target_venue,
        "symbol": symbol,
        "signal_type": signal_type,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "mean_raw_bps": mean_raw_bps,
        "mean_net_bps": mean_net_bps,
        "valid_count": valid_count,
        "win_rate": win_rate,
    }


def _write_eval(path: Path, groups: list[dict], *, mode="FAST_DIAGNOSTIC") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(json.dumps({
        "capture_mode": mode,
        "all_in_cost_bps": 50.0,
        "results_by_group": groups,
        "_metadata": {"capture_mode": mode, "safety_mode": "public_data_observer_only"},
    }))
    return path


def _write_cost(path: Path, rows: list[dict], *, mode="FAST_DIAGNOSTIC") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for row in rows:
        row.setdefault("current_all_in_cost_bps", 50.0)
        row.setdefault("breakeven_cost_bps", row.get("mean_raw_bps"))
    (path / "cost_sensitivity_summary.json").write_text(json.dumps({
        "capture_mode": mode,
        "rows": rows,
        "verdict": "COST_SENSITIVITY_READY",
    }))
    return path


def _write_null(path: Path, results: list[dict], *, mode="FAST_DIAGNOSTIC") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "null_test_summary.json").write_text(json.dumps({
        "overall_verdict": "SURVIVED_NULL_TEST" if any(r.get("candidate_survives_null") for r in results) else "NULL_REJECTED_DIAGNOSTIC",
        "results": results,
        "_metadata": {"capture_mode": mode},
    }))
    return path


def _write_heatmap(path: Path, rows: list[dict], *, mode="FAST_DIAGNOSTIC") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "lead_lag_heatmap_summary.json").write_text(json.dumps({
        "capture_mode": mode,
        "rows": rows,
    }))
    return path


def _write_consistency(path: Path, rows: list[dict], *, mode="FAST_DIAGNOSTIC") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for row in rows:
        row.setdefault("capture_mode", mode)
    (path / "cross_capture_consistency_summary.json").write_text(json.dumps({
        "verdict": "CROSS_CAPTURE_CONSISTENCY_READY",
        "capture_mode": mode,
        "rows": rows,
    }))
    return path


def _null_result(group: dict, survived: bool = True) -> dict:
    return {"group": group, "candidate_survives_null": survived, "verdict": "SURVIVED_NULL_TEST" if survived else "NULL_REJECTED_DIAGNOSTIC"}


def test_missing_all_reports_returns_missing_required_reports():
    summary = compute_candidate_falsification_summary()
    assert summary.verdict == MISSING_REQUIRED_REPORTS
    assert summary.rows == []


def test_evaluated_report_with_no_groups_returns_no_evaluated_groups(tmp_path):
    eval_dir = _write_eval(tmp_path / "eval", [])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir)
    assert summary.verdict == NO_EVALUATED_GROUPS


def test_cost_wall_blocked_group_is_labelled_correctly(tmp_path):
    g = _group(mean_raw_bps=4.0, mean_net_bps=-46.0)
    eval_dir = _write_eval(tmp_path / "eval", [g])
    cost_dir = _write_cost(tmp_path / "cost", [{**g, "breakeven_cost_bps": 4.0}])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir, cost_sensitivity_dir=cost_dir)
    assert summary.verdict == COST_WALL_BLOCKED
    assert summary.rows[0]["diagnostic_status"] == "blocked by costs"
    assert "blocked by configured cost threshold" in summary.rows[0]["evidence_notes"]


def test_missing_null_report_does_not_crash_and_adds_missing_evidence(tmp_path):
    g = _group()
    eval_dir = _write_eval(tmp_path / "eval", [g])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir)
    assert "permutation_null" in summary.rows[0]["missing_evidence"]
    assert summary.rows[0]["null_survived"] is None


def test_missing_heatmap_report_does_not_crash(tmp_path):
    eval_dir = _write_eval(tmp_path / "eval", [_group()])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir)
    assert "lead_lag_heatmap" in summary.rows[0]["missing_evidence"]


def test_missing_consistency_report_does_not_crash(tmp_path):
    eval_dir = _write_eval(tmp_path / "eval", [_group()])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir)
    assert "cross_capture_consistency" in summary.rows[0]["missing_evidence"]
    assert summary.rows[0]["captures_seen"] is None


def test_matching_group_keys_merge_across_input_sources(tmp_path):
    g = _group(mean_raw_bps=60.0, mean_net_bps=10.0)
    eval_dir = _write_eval(tmp_path / "eval", [g])
    cost_dir = _write_cost(tmp_path / "cost", [{**g, "breakeven_cost_bps": 60.0}])
    null_dir = _write_null(tmp_path / "null", [_null_result(g, True)])
    heat_dir = _write_heatmap(tmp_path / "heat", [{
        "source_venue": g["source_venue"], "target_venue": g["target_venue"], "symbol": g["symbol"],
        "signal_type": "lead_lag", "lag_ms": g["horizon_ms"], "verdict": "LEAD_LAG_DIAGNOSTIC_READY",
    }])
    cons_dir = _write_consistency(tmp_path / "cons", [{**g, "captures_seen": 2, "positive_mean_raw_captures": 2, "positive_mean_net_captures": 2}])
    summary = compute_candidate_falsification_summary(
        evaluated_report_dir=eval_dir,
        cost_sensitivity_dir=cost_dir,
        permutation_null_dir=null_dir,
        heatmap_dir=heat_dir,
        consistency_dir=cons_dir,
    )
    row = summary.rows[0]
    assert summary.verdict == FALSIFICATION_SUMMARY_READY
    assert row["evidence_sources_available"] == 5
    assert row["null_survived"] is True
    assert row["heatmap_supported"] is True
    assert row["captures_seen"] == 2
    assert row["diagnostic_status"] == "survives diagnostic filter"


def test_mismatched_horizons_do_not_merge(tmp_path):
    g1 = _group(horizon_ms=1000)
    g2 = _group(horizon_ms=2000)
    eval_dir = _write_eval(tmp_path / "eval", [g1])
    cost_dir = _write_cost(tmp_path / "cost", [g2])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir, cost_sensitivity_dir=cost_dir)
    assert summary.total_groups == 2
    assert sorted(r["horizon_ms"] for r in summary.rows) == [1000, 2000]


def test_capture_mode_is_respected(tmp_path):
    g = _group()
    eval_dir = _write_eval(tmp_path / "eval", [g], mode="FAST_DIAGNOSTIC")
    cost_dir = _write_cost(tmp_path / "cost", [{**g, "capture_mode": "FULL_ACTIVE"}], mode="FULL_ACTIVE")
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir, cost_sensitivity_dir=cost_dir)
    assert summary.total_groups == 2
    assert sorted(r["capture_mode"] for r in summary.rows) == ["FAST_DIAGNOSTIC", "FULL_ACTIVE"]


def test_non_finite_values_are_ignored(tmp_path):
    g = _group(mean_raw_bps=float("nan"), mean_net_bps=float("inf"), win_rate=float("nan"))
    eval_dir = _write_eval(tmp_path / "eval", [g])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir)
    row = summary.rows[0]
    assert row["mean_raw_bps"] is None
    assert row["mean_net_bps"] is None
    assert row["raw_edge_positive"] is False


def test_forbidden_verdicts_cannot_be_emitted():
    for verdict in ["REJECTED", "CANDIDATE", "CANDIDATE_FOR_LIVE", "EXECUTION_READY", "TRADE_READY"]:
        with pytest.raises(ValueError):
            validate_verdict(verdict)
        with pytest.raises(ValueError):
            FalsificationSummary(verdict=verdict, reason="bad")


def test_json_csv_and_markdown_files_are_written(tmp_path):
    eval_dir = _write_eval(tmp_path / "eval", [_group()])
    summary = compute_candidate_falsification_summary(evaluated_report_dir=eval_dir)
    out = tmp_path / "out"
    write_candidate_falsification_reports(summary, out)
    assert (out / "candidate_falsification_summary.json").exists()
    assert (out / "candidate_falsification_matrix.csv").exists()
    assert (out / "candidate_falsification.md").exists()
    data = json.loads((out / "candidate_falsification_summary.json").read_text())
    assert "rows" in data
    with open(out / "candidate_falsification_matrix.csv", newline="") as f:
        assert len(list(csv.DictReader(f))) == 1
    assert "Diagnostic-only" in (out / "candidate_falsification.md").read_text()


def test_diagnostic_score_is_stable_and_transparent(tmp_path):
    g = _group(mean_raw_bps=60.0, mean_net_bps=10.0, valid_count=100)
    eval_dir = _write_eval(tmp_path / "eval", [g])
    cost_dir = _write_cost(tmp_path / "cost", [{**g, "breakeven_cost_bps": 60.0}])
    null_dir = _write_null(tmp_path / "null", [_null_result(g, True)])
    heat_dir = _write_heatmap(tmp_path / "heat", [{
        "source_venue": g["source_venue"], "target_venue": g["target_venue"], "symbol": g["symbol"],
        "signal_type": "lead_lag", "lag_ms": g["horizon_ms"], "verdict": "LEAD_LAG_DIAGNOSTIC_READY",
    }])
    cons_dir = _write_consistency(tmp_path / "cons", [{**g, "captures_seen": 2, "positive_mean_raw_captures": 2, "positive_mean_net_captures": 2}])
    summary = compute_candidate_falsification_summary(
        evaluated_report_dir=eval_dir,
        cost_sensitivity_dir=cost_dir,
        permutation_null_dir=null_dir,
        heatmap_dir=heat_dir,
        consistency_dir=cons_dir,
    )
    # +1 raw, +1 net, +1 cost, +1 null, +1 heatmap, +1 recurrence, no penalties.
    assert summary.rows[0]["falsification_score"] == 6
