"""Tests for cross_capture_consistency diagnostics."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from venue_agnostic_signal_observer.cross_capture_consistency import (
    CROSS_CAPTURE_CONSISTENCY_READY,
    INSUFFICIENT_CAPTURE_COUNT,
    NO_FINITE_GROUPS,
    NO_REPORTS_FOUND,
    CrossCaptureConsistencySummary,
    compute_cross_capture_consistency,
    load_report,
    validate_verdict,
    write_cross_capture_consistency_reports,
)


def _group(
    *,
    source_venue="binance_perp",
    target_venue="coinbase",
    symbol="ETH/USD",
    signal_type="signed_imbalance",
    lookback_ms=500,
    horizon_ms=1000,
    oi_bucket="",
    mean_raw_bps=1.0,
    mean_net_bps=-49.0,
    win_rate=0.55,
    valid_count=100,
    candidate=False,
) -> dict:
    return {
        "source_venue": source_venue,
        "target_venue": target_venue,
        "symbol": symbol,
        "signal_type": signal_type,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "oi_bucket": oi_bucket,
        "mean_raw_bps": mean_raw_bps,
        "mean_net_bps": mean_net_bps,
        "win_rate": win_rate,
        "valid_count": valid_count,
        "candidate": candidate,
    }


def _write_report(path: Path, *, groups: list[dict], capture_mode="FAST_DIAGNOSTIC", verdict="MARKET_MODERATE_DIAGNOSTIC") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    summary = {
        "verdict": verdict,
        "capture_mode": capture_mode,
        "total_signals": 123,
        "valid_evaluations": sum(int(g.get("valid_count", 0) or 0) for g in groups),
        "results_by_group": groups,
        "_metadata": {
            "capture_mode": capture_mode,
            "safety_mode": "public_data_observer_only",
            "run_id": path.name,
        },
    }
    (path / "summary.json").write_text(json.dumps(summary))
    return path


def test_empty_report_list_returns_no_reports_found():
    summary = compute_cross_capture_consistency([])
    assert summary.verdict == NO_REPORTS_FOUND
    assert summary.loaded_reports == 0
    assert summary.rows == []


def test_one_report_only_returns_insufficient_capture_count(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=2.0)])
    summary = compute_cross_capture_consistency([r1])
    assert summary.verdict == INSUFFICIENT_CAPTURE_COUNT
    assert summary.loaded_reports == 1
    assert summary.unique_groups == 1
    assert summary.rows[0]["captures_seen"] == 1


def test_two_reports_with_matching_group_keys_aggregate_correctly(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=2.0, mean_net_bps=-48.0, valid_count=100, win_rate=0.6)])
    r2 = _write_report(tmp_path / "r2", groups=[_group(mean_raw_bps=4.0, mean_net_bps=-46.0, valid_count=150, win_rate=0.4)])
    summary = compute_cross_capture_consistency([r1, r2])
    assert summary.verdict == CROSS_CAPTURE_CONSISTENCY_READY
    assert summary.loaded_reports == 2
    assert summary.unique_groups == 1
    row = summary.rows[0]
    assert row["captures_seen"] == 2
    assert row["finite_mean_raw_captures"] == 2
    assert row["positive_mean_raw_captures"] == 2
    assert row["positive_mean_net_captures"] == 0
    assert row["mean_of_mean_raw_bps"] == pytest.approx(3.0)
    assert row["median_of_mean_raw_bps"] == pytest.approx(3.0)
    assert row["mean_of_mean_net_bps"] == pytest.approx(-47.0)
    assert row["median_of_mean_net_bps"] == pytest.approx(-47.0)
    assert row["best_raw_bps"] == pytest.approx(4.0)
    assert row["worst_raw_bps"] == pytest.approx(2.0)
    assert row["total_valid_count"] == 250
    assert row["min_valid_count"] == 100
    assert row["max_valid_count"] == 150
    assert row["mean_win_rate"] == pytest.approx(0.5)
    assert row["median_win_rate"] == pytest.approx(0.5)


def test_non_finite_values_are_ignored(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=float("nan"), mean_net_bps=float("inf"), win_rate=float("nan"))])
    r2 = _write_report(tmp_path / "r2", groups=[_group(mean_raw_bps=3.0, mean_net_bps=-1.0, win_rate=0.25)])
    summary = compute_cross_capture_consistency([r1, r2])
    row = summary.rows[0]
    assert row["captures_seen"] == 2
    assert row["finite_mean_raw_captures"] == 1
    assert row["mean_of_mean_raw_bps"] == pytest.approx(3.0)
    assert row["mean_of_mean_net_bps"] == pytest.approx(-1.0)
    assert row["mean_win_rate"] == pytest.approx(0.25)


def test_no_finite_groups_returns_no_finite_groups(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=float("nan"))])
    r2 = _write_report(tmp_path / "r2", groups=[_group(mean_raw_bps=float("inf"))])
    summary = compute_cross_capture_consistency([r1, r2])
    assert summary.verdict == NO_FINITE_GROUPS
    assert summary.finite_group_observations == 0


def test_groups_with_different_horizons_do_not_merge(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(horizon_ms=1000)])
    r2 = _write_report(tmp_path / "r2", groups=[_group(horizon_ms=2000)])
    summary = compute_cross_capture_consistency([r1, r2])
    assert summary.unique_groups == 2
    assert sorted(r["horizon_ms"] for r in summary.rows) == [1000, 2000]
    assert all(r["captures_seen"] == 1 for r in summary.rows)


def test_groups_with_different_capture_modes_do_not_merge_by_default(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=1.0)], capture_mode="FAST_DIAGNOSTIC")
    r2 = _write_report(tmp_path / "r2", groups=[_group(mean_raw_bps=2.0)], capture_mode="FULL_ACTIVE")
    summary = compute_cross_capture_consistency([r1, r2])
    assert summary.unique_groups == 2
    assert sorted(r["capture_mode"] for r in summary.rows) == ["FAST_DIAGNOSTIC", "FULL_ACTIVE"]


def test_groups_with_different_capture_modes_can_merge_explicitly(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=1.0)], capture_mode="FAST_DIAGNOSTIC")
    r2 = _write_report(tmp_path / "r2", groups=[_group(mean_raw_bps=3.0)], capture_mode="FULL_ACTIVE")
    summary = compute_cross_capture_consistency([r1, r2], include_capture_mode=False)
    assert summary.unique_groups == 1
    assert summary.rows[0]["capture_mode"] == ""
    assert summary.rows[0]["captures_seen"] == 2
    assert summary.rows[0]["median_of_mean_raw_bps"] == pytest.approx(2.0)


def test_sorting_prefers_consistency_over_one_lucky_best_return(tmp_path):
    recurring = _group(signal_type="signed_imbalance", mean_raw_bps=1.0, valid_count=100)
    lucky = _group(signal_type="large_trade", mean_raw_bps=100.0, valid_count=10)
    r1 = _write_report(tmp_path / "r1", groups=[recurring, lucky])
    r2 = _write_report(tmp_path / "r2", groups=[_group(signal_type="signed_imbalance", mean_raw_bps=1.2, valid_count=100)])
    summary = compute_cross_capture_consistency([r1, r2])
    assert summary.rows[0]["signal_type"] == "signed_imbalance"
    assert summary.rows[0]["captures_seen"] == 2
    assert summary.rows[1]["signal_type"] == "large_trade"
    assert summary.rows[1]["best_raw_bps"] == pytest.approx(100.0)


def test_forbidden_verdicts_raise_or_are_impossible():
    for verdict in ["REJECTED", "CANDIDATE", "CANDIDATE_FOR_LIVE", "READY_FOR_LIVE"]:
        with pytest.raises(ValueError):
            validate_verdict(verdict)
        with pytest.raises(ValueError):
            CrossCaptureConsistencySummary(
                report_dirs=[],
                total_reports=0,
                loaded_reports=0,
                total_group_observations=0,
                finite_group_observations=0,
                unique_groups=0,
                recurring_groups=0,
                verdict=verdict,
                reason="bad",
            )


def test_markdown_csv_json_are_written(tmp_path):
    r1 = _write_report(tmp_path / "r1", groups=[_group(mean_raw_bps=1.0)])
    r2 = _write_report(tmp_path / "r2", groups=[_group(mean_raw_bps=2.0)])
    summary = compute_cross_capture_consistency([r1, r2])
    out = tmp_path / "out"
    write_cross_capture_consistency_reports(summary, out)
    assert (out / "cross_capture_consistency_summary.json").exists()
    assert (out / "cross_capture_consistency.csv").exists()
    assert (out / "cross_capture_consistency.md").exists()
    data = json.loads((out / "cross_capture_consistency_summary.json").read_text())
    assert data["verdict"] == CROSS_CAPTURE_CONSISTENCY_READY
    with open(out / "cross_capture_consistency.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    md = (out / "cross_capture_consistency.md").read_text()
    assert "Diagnostic-only" in md


def test_metadata_is_preserved_where_available(tmp_path):
    r1 = _write_report(tmp_path / "capture_a", groups=[_group(oi_bucket="high")], capture_mode="FAST_DIAGNOSTIC")
    loaded = load_report(r1)
    assert loaded is not None
    groups, meta = loaded
    assert meta["capture_mode"] == "FAST_DIAGNOSTIC"
    assert meta["metadata"]["safety_mode"] == "public_data_observer_only"
    summary = compute_cross_capture_consistency([r1, r1])
    assert summary.report_metadata[0]["report_name"] == "capture_a"
    assert summary.rows[0]["oi_bucket"] == "high"
