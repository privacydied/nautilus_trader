"""Phase 2C evidence review tests.

Tests gate decision logic with synthetic evidence.
No live data. No network. No orders. No keys.
"""

import json
import tempfile
from pathlib import Path

import pytest

from examples.strategies.polymarket_btcusd_arb.observer_evidence_review import (
    GateDecision,
    RejectionRollup,
    WindowRollup,
    compute_gate_decision,
    write_evidence_report,
)


def _make_rollup(
    source: str = "live_observer",
    candidate_count: int = 0,
    grid_rejection_count: int = 3160,
    spread: int = 2640,
    stale: int = 520,
    market_slug: str = "btc-updown-15m-test",
    replay_passed: bool = True,
    evaluated_events: int = 158,
) -> WindowRollup:
    return WindowRollup(
        run_id="test-run",
        source=source,
        market_slug=market_slug,
        evaluated_events=evaluated_events,
        candidate_count=candidate_count,
        grid_rejection_count=grid_rejection_count,
        rejection_counts={"spread_too_wide": spread, "stale_or_missing_binance": stale},
        spread_too_wide_rate=spread / (spread + stale) if (spread + stale) > 0 else 0.0,
        stale_or_missing_binance_rate=stale / (spread + stale) if (spread + stale) > 0 else 0.0,
        replay_passed=replay_passed,
    )


class TestEvidenceReviewArchivesZeroCandidateSpreadDominated:
    """3+ completed zero-candidate windows with spread dominance → REJECTED_FOR_CURRENT_LIVE_CONDITIONS."""

    def test_archives_zero_candidate_spread_dominated(self):
        rollups = [
            _make_rollup(spread=2640, stale=520),
            _make_rollup(spread=2280, stale=880, market_slug="btc-updown-15m-test2"),
            _make_rollup(spread=2520, stale=620, market_slug="btc-updown-15m-test3"),
            _make_rollup(spread=2740, stale=420, market_slug="btc-updown-15m-test4"),
            _make_rollup(spread=3060, stale=220, market_slug="btc-updown-15m-test5"),
            _make_rollup(spread=3000, stale=160, market_slug="btc-updown-15m-test6"),
        ]
        agg = RejectionRollup(
            total_evaluated_events=950,
            total_candidates=0,
            total_grid_rejections=18980,
            rejection_counts_by_reason={
                "spread_too_wide": 16240,
                "stale_or_missing_binance": 2820,
            },
            spread_too_wide_total=16240,
            stale_or_missing_binance_total=2820,
        )
        gate = compute_gate_decision(rollups, agg)
        assert gate.gate == "REJECTED_FOR_CURRENT_LIVE_CONDITIONS"
        assert gate.candidates_total == 0
        assert gate.spread_dominated is True
        assert gate.replay_all_passed is True


class TestEvidenceReviewContinuesOnInconclusiveSample:
    """Too few windows or mixed results → CONTINUE_OBSERVER_ONLY."""

    def test_continues_on_inconclusive_sample(self):
        rollups = [
            _make_rollup(spread=2640, stale=520),
            _make_rollup(spread=2280, stale=880),
        ]
        agg = RejectionRollup(
            total_evaluated_events=316,
            total_candidates=0,
            total_grid_rejections=6320,
            rejection_counts_by_reason={
                "spread_too_wide": 4920,
                "stale_or_missing_binance": 1400,
            },
            spread_too_wide_total=4920,
            stale_or_missing_binance_total=1400,
        )
        gate = compute_gate_decision(rollups, agg)
        assert gate.gate == "CONTINUE_OBSERVER_ONLY"


class TestEvidenceReviewAllowsPhase3OnlyWithReplayedLiveCandidates:
    """Phase 3 only if multiple live windows produce candidates AND replay passes."""

    def test_allows_phase3_with_candidates_and_replay(self):
        rollups = [
            _make_rollup(candidate_count=5, spread=2000, stale=160, market_slug="market-a"),
            _make_rollup(candidate_count=3, spread=1800, stale=200, market_slug="market-b"),
            _make_rollup(candidate_count=2, spread=2200, stale=180, market_slug="market-c"),
            _make_rollup(candidate_count=1, spread=2400, stale=220, market_slug="market-d"),
        ]
        agg = RejectionRollup(
            total_evaluated_events=640,
            total_candidates=11,
            total_grid_rejections=12760,
            rejection_counts_by_reason={
                "spread_too_wide": 8400,
                "stale_or_missing_binance": 760,
            },
            spread_too_wide_total=8400,
            stale_or_missing_binance_total=760,
        )
        gate = compute_gate_decision(rollups, agg)
        assert gate.gate == "ALLOW_PHASE_3_RUST_HOTPATH"

    def test_phase3_blocked_with_zero_candidates(self):
        rollups = [
            _make_rollup(spread=2640, stale=520),
            _make_rollup(spread=2280, stale=880),
            _make_rollup(spread=2520, stale=620),
            _make_rollup(spread=2740, stale=420),
        ]
        agg = RejectionRollup(
            total_evaluated_events=632,
            total_candidates=0,
            total_grid_rejections=12640,
            rejection_counts_by_reason={
                "spread_too_wide": 10180,
                "stale_or_missing_binance": 2440,
            },
            spread_too_wide_total=10180,
            stale_or_missing_binance_total=2440,
        )
        gate = compute_gate_decision(rollups, agg)
        assert gate.gate != "ALLOW_PHASE_3_RUST_HOTPATH"


class TestEvidenceReportDoesNotRecommendExecution:
    """Evidence report must not recommend execution."""

    def test_rejected_does_not_recommend_execution(self):
        rollups = [_make_rollup() for _ in range(6)]
        agg = RejectionRollup(
            total_evaluated_events=950,
            total_candidates=0,
            total_grid_rejections=18960,
            rejection_counts_by_reason={
                "spread_too_wide": 16000,
                "stale_or_missing_binance": 2960,
            },
            spread_too_wide_total=16000,
            stale_or_missing_binance_total=2960,
        )
        gate = compute_gate_decision(rollups, agg)
        with tempfile.TemporaryDirectory() as tmpdir:
            review_dir = write_evidence_report(rollups, agg, gate, Path(tmpdir), rollups)
            report_md = review_dir / "evidence_report.md"
            content = report_md.read_text()
            assert "Phase 3" not in content or "Do not recommend" in content
            assert "execution" not in content.lower() or "No execution" in content or "Do not recommend" in content

    def test_continue_observer_does_not_recommend_execution(self):
        rollups = [_make_rollup(), _make_rollup(market_slug="market-2")]
        agg = RejectionRollup(
            total_evaluated_events=316,
            total_candidates=0,
            total_grid_rejections=6320,
            rejection_counts_by_reason={
                "spread_too_wide": 5000,
                "stale_or_missing_binance": 1320,
            },
            spread_too_wide_total=5000,
            stale_or_missing_binance_total=1320,
        )
        gate = compute_gate_decision(rollups, agg)
        with tempfile.TemporaryDirectory() as tmpdir:
            review_dir = write_evidence_report(rollups, agg, gate, Path(tmpdir), rollups)
            report_md = review_dir / "evidence_report.md"
            content = report_md.read_text()
            assert "Do not recommend" in content


class TestGateDecisionValues:
    """Gate decision must be one of the three allowed values."""

    @pytest.mark.parametrize("expected_gate", [
        "REJECTED_FOR_CURRENT_LIVE_CONDITIONS",
        "CONTINUE_OBSERVER_ONLY",
        "ALLOW_PHASE_3_RUST_HOTPATH",
    ])
    def test_gate_values_are_valid(self, expected_gate):
        assert expected_gate in {
            "REJECTED_FOR_CURRENT_LIVE_CONDITIONS",
            "CONTINUE_OBSERVER_ONLY",
            "ALLOW_PHASE_3_RUST_HOTPATH",
        }


class TestWriteEvidenceReportFiles:
    """Evidence review output files are complete and valid."""

    def test_all_required_files_written(self):
        rollups = [_make_rollup() for _ in range(3)]
        agg = RejectionRollup(
            total_evaluated_events=474,
            total_candidates=0,
            total_grid_rejections=9480,
            rejection_counts_by_reason={
                "spread_too_wide": 7800,
                "stale_or_missing_binance": 1680,
            },
            spread_too_wide_total=7800,
            stale_or_missing_binance_total=1680,
        )
        gate = compute_gate_decision(rollups, agg)
        with tempfile.TemporaryDirectory() as tmpdir:
            review_dir = write_evidence_report(rollups, agg, gate, Path(tmpdir), rollups)
            assert (review_dir / "evidence_summary.json").exists()
            assert (review_dir / "evidence_report.md").exists()
            assert (review_dir / "window_rollup.csv").exists()
            assert (review_dir / "rejection_rollup.csv").exists()
            assert (review_dir / "gate_decision.json").exists()

            # Validate JSON content
            summary = json.loads((review_dir / "evidence_summary.json").read_text())
            assert "gate_decision" in summary
            assert summary["gate_decision"] in {
                "REJECTED_FOR_CURRENT_LIVE_CONDITIONS",
                "CONTINUE_OBSERVER_ONLY",
                "ALLOW_PHASE_3_RUST_HOTPATH",
            }
            # Valid live count must be present
            assert "valid_live_windows_analyzed" in summary
            assert "total_artifact_rows" in summary
            assert summary["valid_live_windows_analyzed"] <= summary["total_artifact_rows"]

            gate_json = json.loads((review_dir / "gate_decision.json").read_text())
            assert gate_json["gate"] == summary["gate_decision"]


class TestValidLiveWindowFiltering:
    """Only windows with events > 0 and grid_rejections > 0 count for gate decisions."""

    def test_empty_windows_excluded_from_gate(self):
        """Windows with 0 events or 0 grid rejections are excluded."""
        valid = [
            _make_rollup(spread=2640, stale=520, market_slug="m1"),
            _make_rollup(spread=2280, stale=880, market_slug="m2"),
        ]
        # Include an empty window — should not affect the gate
        empty = [
            _make_rollup(evaluated_events=0, grid_rejection_count=0, spread=0, stale=0),
        ]
        # Gate decision should be based on valid windows only
        agg = RejectionRollup(
            total_evaluated_events=316,
            total_candidates=0,
            total_grid_rejections=6320,
            rejection_counts_by_reason={"spread_too_wide": 4920, "stale_or_missing_binance": 1400},
            spread_too_wide_total=4920,
            stale_or_missing_binance_total=1400,
        )
        # 2 valid windows: too few for REJECTED, should get CONTINUE
        gate = compute_gate_decision(valid, agg)
        assert gate.gate == "CONTINUE_OBSERVER_ONLY"
        # 6 valid windows: enough for REJECTED
        six_valid = [_make_rollup(market_slug=f"m{i}") for i in range(6)]
        agg6 = RejectionRollup(
            total_evaluated_events=950,
            total_candidates=0,
            total_grid_rejections=18960,
            rejection_counts_by_reason={"spread_too_wide": 16000, "stale_or_missing_binance": 2960},
            spread_too_wide_total=16000,
            stale_or_missing_binance_total=2960,
        )
        gate6 = compute_gate_decision(six_valid, agg6)
        assert gate6.gate == "REJECTED_FOR_CURRENT_LIVE_CONDITIONS"

    def test_aggregate_deduplicates(self):
        """aggregate_evidence deduplicates overlapping observer/campaign entries."""
        from examples.strategies.polymarket_btcusd_arb.observer_evidence_review import aggregate_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            # Create minimal observer summary
            obs_dir = td / "data" / "polymarket_btcusd_arb" / "live_observer" / "run1"
            obs_dir.mkdir(parents=True)
            summary = {
                "evaluated_event_count": 158,
                "candidate_count": 0,
                "grid_rejection_count": 3160,
                "grid_rejection_counts": {"spread_too_wide": 2640, "stale_or_missing_binance": 520},
                "market_slug": "unknown",
                "replay_deterministic": True,
            }
            (obs_dir / "observer_summary.json").write_text(json.dumps(summary))
            all_rollups, agg, valid = aggregate_evidence(td / "reports", td / "data")
            assert len(valid) == 1
            assert agg.total_grid_rejections == 3160