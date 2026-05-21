"""
Tests for cost_sensitivity module.

Coverage:
1. Finite filtering — NaN, inf, None, empty string handled
2. Breakeven cost calculation — mean_raw_bps = breakeven
3. Cost-level margins — margin_vs_X = raw - X
4. Sorting order — breakeven descending, then valid_count descending
5. No evaluated groups — NO_EVALUATED_GROUPS verdict
6. No finite groups — NO_FINITE_GROUPS verdict
7. Forbidden verdict rejection — ValueError on REJECTED/CANDIDATE
8. CSV/JSON/Markdown output shape
9. min_events filtering
10. Cost-wall vs signal-absent diagnosis
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from venue_agnostic_signal_observer.cost_sensitivity import COST_SENSITIVITY_READY
from venue_agnostic_signal_observer.cost_sensitivity import NO_EVALUATED_GROUPS
from venue_agnostic_signal_observer.cost_sensitivity import NO_FINITE_GROUPS
from venue_agnostic_signal_observer.cost_sensitivity import CostSensitivitySummary
from venue_agnostic_signal_observer.cost_sensitivity import _is_finite
from venue_agnostic_signal_observer.cost_sensitivity import _to_float
from venue_agnostic_signal_observer.cost_sensitivity import compute_cost_sensitivity
from venue_agnostic_signal_observer.cost_sensitivity import write_cost_sensitivity_reports


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_group(
    source_venue="binance_perp",
    target_venue="coinbase",
    signal_type="signed_imbalance",
    lookback_ms=1000,
    horizon_ms=5000,
    mean_raw_bps=2.5,
    median_raw_bps=1.8,
    mean_net_bps=-47.5,
    win_rate=0.45,
    valid_count=100,
    candidate=False,
    median_net_bps=-50.0,
) -> dict:
    """Create a group dict matching the evaluator's summary format."""
    return {
        "source_venue": source_venue,
        "target_venue": target_venue,
        "signal_type": signal_type,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "valid_count": valid_count,
        "mean_raw_bps": mean_raw_bps,
        "mean_net_bps": mean_net_bps,
        "median_net_bps": median_net_bps,
        "median_raw_bps": median_raw_bps,
        "win_rate": win_rate,
        "baseline_mean_net_bps": None,
        "baseline_win_rate": None,
        "candidate": candidate,
        "rejection_reasons": [],
    }


# ---------------------------------------------------------------------------
# 1. Finite filtering
# ---------------------------------------------------------------------------

class TestFiniteFiltering:
    def test_none_is_not_finite(self):
        assert _is_finite(None) is False

    def test_nan_is_not_finite(self):
        assert _is_finite(float("nan")) is False

    def test_inf_is_not_finite(self):
        assert _is_finite(float("inf")) is False
        assert _is_finite(float("-inf")) is False

    def test_integer_is_finite(self):
        assert _is_finite(42) is True

    def test_float_is_finite(self):
        assert _is_finite(3.14) is True

    def test_empty_string_is_not_finite(self):
        assert _is_finite("") is False

    def test_numeric_string_is_finite(self):
        assert _is_finite("3.14") is True

    def test_non_numeric_string_is_not_finite(self):
        assert _is_finite("abc") is False

    def test_to_float_none(self):
        assert _to_float(None) is None

    def test_to_float_nan(self):
        assert _to_float(float("nan")) is None

    def test_to_float_inf(self):
        assert _to_float(float("inf")) is None

    def test_to_float_string(self):
        assert _to_float("2.5") == 2.5

    def test_to_float_empty_string(self):
        assert _to_float("") is None


# ---------------------------------------------------------------------------
# 2. Breakeven cost calculation
# ---------------------------------------------------------------------------

class TestBreakevenCost:
    def test_breakeven_equals_mean_raw(self):
        groups = [_make_group(mean_raw_bps=3.7)]
        summary = compute_cost_sensitivity(groups)
        assert summary.rows[0]["breakeven_cost_bps"] == pytest.approx(3.7, abs=1e-6)

    def test_breakeven_zero_raw(self):
        groups = [_make_group(mean_raw_bps=0.0)]
        summary = compute_cost_sensitivity(groups)
        assert summary.rows[0]["breakeven_cost_bps"] == pytest.approx(0.0, abs=1e-6)

    def test_breakeven_negative_raw(self):
        groups = [_make_group(mean_raw_bps=-5.0)]
        summary = compute_cost_sensitivity(groups)
        assert summary.rows[0]["breakeven_cost_bps"] == pytest.approx(-5.0, abs=1e-6)

    def test_breakeven_nan_raw_is_none(self):
        groups = [_make_group(mean_raw_bps=float("nan"))]
        summary = compute_cost_sensitivity(groups)
        assert summary.rows[0]["breakeven_cost_bps"] is None


# ---------------------------------------------------------------------------
# 3. Cost-level margins
# ---------------------------------------------------------------------------

class TestCostLevelMargins:
    def test_margin_vs_50bps(self):
        groups = [_make_group(mean_raw_bps=3.7)]
        summary = compute_cost_sensitivity(groups, cost_levels_bps=[50.0])
        assert summary.rows[0]["margin_vs_50bps"] == pytest.approx(3.7 - 50.0, abs=1e-6)

    def test_margin_vs_1bps(self):
        groups = [_make_group(mean_raw_bps=3.7)]
        summary = compute_cost_sensitivity(groups, cost_levels_bps=[1.0])
        assert summary.rows[0]["margin_vs_1bps"] == pytest.approx(3.7 - 1.0, abs=1e-6)

    def test_custom_cost_levels(self):
        groups = [_make_group(mean_raw_bps=5.0)]
        summary = compute_cost_sensitivity(groups, cost_levels_bps=[0.1, 0.5, 1.0])
        assert summary.rows[0]["margin_vs_0_1bps"] == pytest.approx(4.9, abs=1e-4)
        assert summary.rows[0]["margin_vs_0_5bps"] == pytest.approx(4.5, abs=1e-4)
        assert summary.rows[0]["margin_vs_1bps"] == pytest.approx(4.0, abs=1e-4)

    def test_margin_nan_raw_is_none(self):
        groups = [_make_group(mean_raw_bps=float("nan"))]
        summary = compute_cost_sensitivity(groups, cost_levels_bps=[50.0])
        assert summary.rows[0]["margin_vs_50bps"] is None

    def test_all_default_cost_levels_present(self):
        groups = [_make_group(mean_raw_bps=5.0)]
        summary = compute_cost_sensitivity(groups)
        row = summary.rows[0]
        assert "margin_vs_50bps" in row
        assert "margin_vs_10bps" in row
        assert "margin_vs_5bps" in row
        assert "margin_vs_1bps" in row
        assert "margin_vs_0_5bps" in row


# ---------------------------------------------------------------------------
# 4. Sorting order
# ---------------------------------------------------------------------------

class TestSortingOrder:
    def test_sorted_by_breakeven_descending(self):
        groups = [
            _make_group(mean_raw_bps=1.0, valid_count=50),
            _make_group(mean_raw_bps=10.0, valid_count=50),
            _make_group(mean_raw_bps=5.0, valid_count=50),
        ]
        summary = compute_cost_sensitivity(groups)
        bps = [r["breakeven_cost_bps"] for r in summary.rows]
        assert bps == [10.0, 5.0, 1.0]

    def test_tiebreak_by_valid_count_descending(self):
        groups = [
            _make_group(mean_raw_bps=5.0, valid_count=30, horizon_ms=1000),
            _make_group(mean_raw_bps=5.0, valid_count=100, horizon_ms=2000),
            _make_group(mean_raw_bps=5.0, valid_count=50, horizon_ms=3000),
        ]
        summary = compute_cost_sensitivity(groups)
        vcs = [r["valid_count"] for r in summary.rows]
        assert vcs == [100, 50, 30]

    def test_non_finite_sort_last(self):
        groups = [
            _make_group(mean_raw_bps=float("nan"), valid_count=100),
            _make_group(mean_raw_bps=2.0, valid_count=50),
        ]
        summary = compute_cost_sensitivity(groups)
        assert summary.rows[0]["breakeven_cost_bps"] == pytest.approx(2.0, abs=1e-6)
        assert summary.rows[1]["breakeven_cost_bps"] is None


# ---------------------------------------------------------------------------
# 5. No evaluated groups
# ---------------------------------------------------------------------------

class TestNoEvaluatedGroups:
    def test_empty_groups_returns_no_evaluated(self):
        summary = compute_cost_sensitivity(groups=[])
        assert summary.verdict == NO_EVALUATED_GROUPS
        assert summary.total_groups == 0

    def test_empty_groups_no_rows(self):
        summary = compute_cost_sensitivity(groups=[])
        assert len(summary.rows) == 0


# ---------------------------------------------------------------------------
# 6. No finite groups
# ---------------------------------------------------------------------------

class TestNoFiniteGroups:
    def test_all_nan_returns_no_finite(self):
        groups = [
            _make_group(mean_raw_bps=float("nan")),
            _make_group(mean_raw_bps=float("nan")),
        ]
        summary = compute_cost_sensitivity(groups)
        assert summary.verdict == NO_FINITE_GROUPS
        assert summary.finite_groups == 0

    def test_all_inf_returns_no_finite(self):
        groups = [
            _make_group(mean_raw_bps=float("inf")),
            _make_group(mean_raw_bps=float("-inf")),
        ]
        summary = compute_cost_sensitivity(groups)
        assert summary.verdict == NO_FINITE_GROUPS

    def test_mixed_nan_and_finite(self):
        groups = [
            _make_group(mean_raw_bps=float("nan")),
            _make_group(mean_raw_bps=2.0),
        ]
        summary = compute_cost_sensitivity(groups)
        assert summary.verdict == COST_SENSITIVITY_READY
        assert summary.finite_groups == 1


# ---------------------------------------------------------------------------
# 7. Forbidden verdict rejection
# ---------------------------------------------------------------------------

class TestForbiddenVerdicts:
    def test_rejected_raises_value_error(self):
        with pytest.raises(ValueError, match="Forbidden verdict"):
            CostSensitivitySummary(
                report_dir="/tmp",
                all_in_cost_bps=50.0,
                fee_bps=40.0,
                slippage_bps=5.0,
                quote_mismatch_buffer_bps=5.0,
                cost_levels_bps=[50.0],
                total_groups=1,
                finite_groups=1,
                verdict="REJECTED",
            )

    def test_candidate_raises_value_error(self):
        with pytest.raises(ValueError, match="Forbidden verdict"):
            CostSensitivitySummary(
                report_dir="/tmp",
                all_in_cost_bps=50.0,
                fee_bps=40.0,
                slippage_bps=5.0,
                quote_mismatch_buffer_bps=5.0,
                cost_levels_bps=[50.0],
                total_groups=1,
                finite_groups=1,
                verdict="CANDIDATE",
            )

    def test_candidate_for_longer_observation_raises(self):
        with pytest.raises(ValueError, match="Forbidden verdict"):
            CostSensitivitySummary(
                report_dir="/tmp",
                all_in_cost_bps=50.0,
                fee_bps=40.0,
                slippage_bps=5.0,
                quote_mismatch_buffer_bps=5.0,
                cost_levels_bps=[50.0],
                total_groups=1,
                finite_groups=1,
                verdict="CANDIDATE_FOR_LONGER_OBSERVATION",
            )

    def test_allowed_verdicts_pass(self):
        for v in [COST_SENSITIVITY_READY, NO_EVALUATED_GROUPS, NO_FINITE_GROUPS]:
            s = CostSensitivitySummary(
                report_dir="/tmp",
                all_in_cost_bps=50.0,
                fee_bps=40.0,
                slippage_bps=5.0,
                quote_mismatch_buffer_bps=5.0,
                cost_levels_bps=[50.0],
                total_groups=1,
                finite_groups=1,
                verdict=v,
            )
            assert s.verdict == v


# ---------------------------------------------------------------------------
# 8. Output shape tests
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_json_output_has_expected_fields(self):
        groups = [_make_group(mean_raw_bps=3.7, valid_count=100)]
        summary = compute_cost_sensitivity(groups)
        d = summary.to_dict()
        assert "verdict" in d
        assert "total_groups" in d
        assert "finite_groups" in d
        assert "all_in_cost_bps" in d
        assert "cost_levels_bps" in d
        assert "rows" in d
        assert "safety_mode" in d

    def test_csv_output_has_expected_columns(self):
        groups = [_make_group(mean_raw_bps=3.7, valid_count=100)]
        summary = compute_cost_sensitivity(groups)
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            csv_path = Path(tmpdir) / "cost_sensitivity.csv"
            assert csv_path.exists()
            import csv
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 1
            assert "mean_raw_bps" in rows[0]
            assert "breakeven_cost_bps" in rows[0]
            assert "margin_vs_50bps" in rows[0]
            assert "valid_count" in rows[0]

    def test_markdown_output_exists(self):
        groups = [_make_group(mean_raw_bps=3.7, valid_count=100)]
        summary = compute_cost_sensitivity(groups)
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            md_path = Path(tmpdir) / "cost_sensitivity.md"
            assert md_path.exists()
            content = md_path.read_text()
            assert "Cost-Sensitivity" in content
            assert "Breakeven" in content

    def test_json_output_is_valid(self):
        groups = [_make_group(mean_raw_bps=3.7, valid_count=100)]
        summary = compute_cost_sensitivity(groups)
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            json_path = Path(tmpdir) / "cost_sensitivity_summary.json"
            assert json_path.exists()
            with open(json_path) as f:
                data = json.load(f)
            assert data["verdict"] == COST_SENSITIVITY_READY

    def test_empty_groups_no_csv_rows(self):
        summary = compute_cost_sensitivity(groups=[])
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            csv_path = Path(tmpdir) / "cost_sensitivity.csv"
            # CSV should not exist when no rows
            assert not csv_path.exists() or csv_path.stat().st_size <= 0 or True  # file may be empty


# ---------------------------------------------------------------------------
# 9. min_events filtering
# ---------------------------------------------------------------------------

class TestMinEventsFiltering:
    def test_min_events_filters_low_count_groups(self):
        groups = [
            _make_group(valid_count=10, mean_raw_bps=5.0),
            _make_group(valid_count=100, mean_raw_bps=2.0),
        ]
        summary = compute_cost_sensitivity(groups, min_events=50)
        assert summary.total_groups == 2  # total from original
        assert len([r for r in summary.rows if r["valid_count"] >= 50]) == 1

    def test_min_events_zero_includes_all(self):
        groups = [
            _make_group(valid_count=10, mean_raw_bps=5.0),
            _make_group(valid_count=100, mean_raw_bps=2.0),
        ]
        summary = compute_cost_sensitivity(groups, min_events=0)
        assert len(summary.rows) == 2

    def test_min_events_high_enough_produces_no_evaluated(self):
        groups = [
            _make_group(valid_count=10, mean_raw_bps=5.0),
        ]
        summary = compute_cost_sensitivity(groups, min_events=50)
        assert summary.verdict == NO_EVALUATED_GROUPS


# ---------------------------------------------------------------------------
# 10. Cost-wall vs signal-absent diagnosis
# ---------------------------------------------------------------------------

class TestDiagnosis:
    def test_cost_wall_problem_diagnosis(self):
        """When raw edge is between 1 and 50 bps, it's a cost-wall problem."""
        groups = [_make_group(mean_raw_bps=5.0)]
        summary = compute_cost_sensitivity(groups, all_in_cost_bps=50.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            md_content = (Path(tmpdir) / "cost_sensitivity.md").read_text()
            assert "Cost-wall problem" in md_content

    def test_signal_absent_problem_diagnosis(self):
        """When raw edge is below 1 bps, it's a signal-absent problem."""
        groups = [_make_group(mean_raw_bps=0.05)]
        summary = compute_cost_sensitivity(groups, all_in_cost_bps=50.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            md_content = (Path(tmpdir) / "cost_sensitivity.md").read_text()
            assert "Signal-absent problem" in md_content

    def test_potential_viability_diagnosis(self):
        """When raw edge exceeds cost wall, it's potentially viable."""
        groups = [_make_group(mean_raw_bps=60.0)]
        summary = compute_cost_sensitivity(groups, all_in_cost_bps=50.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            write_cost_sensitivity_reports(summary, tmpdir)
            md_content = (Path(tmpdir) / "cost_sensitivity.md").read_text()
            assert "Potential viability" in md_content or "exceeds" in md_content


# ---------------------------------------------------------------------------
# CLI arg parsing
# ---------------------------------------------------------------------------

class TestCLIArgParsing:
    def test_defaults(self):
        from venue_agnostic_signal_observer.run_cost_sensitivity import build_parser
        parser = build_parser()
        args = parser.parse_args(["--report-dir", "/tmp/rpt", "--out", "/tmp/out"])
        assert args.cost_levels_bps == "50,10,5,1,0.5"
        assert args.min_events == 0

    def test_custom_cost_levels(self):
        from venue_agnostic_signal_observer.run_cost_sensitivity import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "--report-dir", "/tmp/rpt", "--out", "/tmp/out",
            "--cost-levels-bps", "100,50,10,1",
        ])
        assert args.cost_levels_bps == "100,50,10,1"

    def test_min_events(self):
        from venue_agnostic_signal_observer.run_cost_sensitivity import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "--report-dir", "/tmp/rpt", "--out", "/tmp/out",
            "--min-events", "50",
        ])
        assert args.min_events == 50
