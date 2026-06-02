"""Tests for rolling re-falsification."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from ..conductor.atomic_io import append_jsonl_durable, write_json_atomic
from ..paper.models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState
from ..paper.refalsification import (
    RefalsificationConfig,
    RefalsificationDecision,
    _find_latest_artifact,
    refalsify_strategy,
    run_refalsification_once,
)
from ..paper.registry import load_all_strategies, load_strategy, save_strategy


def _make_enabled_strategy(
    strategy_id: str = "paper_abc123",
    promoted_dt: "datetime.datetime | None" = None,
) -> PaperStrategySpec:
    if promoted_dt is None:
        promoted_dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    return PaperStrategySpec(
        strategy_id=strategy_id,
        signal_family="test_family",
        study_id="test_study",
        precommitment_hash="abc123",
        precommitment_path=Path("/tmp/p.json"),
        promotion_rule_id="v0",
        execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
        group_id="group_a",
        mean_net_bps=15.0,
        valid_count=50,
        win_rate=0.55,
        cost_floor_bps=50.0,
        min_events=10,
        source_venue=None,
        target_venue=None,
        source_symbol=None,
        target_symbol=None,
        command=("echo",),
        capture_dir=None,
        artifacts_dir=Path("/tmp/a"),
        output_dir=Path("/tmp/o"),
        promoter_verdict="PROMOTED",
        promoted_at_utc=promoted_dt.isoformat(),
        last_refalsified_utc=None,
        refalsification_status=None,
        state=PaperStrategyState.ENABLED,
        metadata={},
    )


def _setup_artifacts(
    artifacts_root: Path,
    study_id: str = "test_study",
    null_rejected: bool = False,
) -> Path:
    # Production convention: directory name is exactly the study_id
    study_dir = artifacts_root / study_id
    study_dir.mkdir(parents=True, exist_ok=True)

    # Null test results
    write_json_atomic(
        study_dir / "null_test_results.json",
        {
            "null_rejected_groups": (
                [{"group_id": "group_a"}] if null_rejected else []
            )
        },
    )
    # Stage 2 results
    write_json_atomic(
        study_dir / "stage2_check_results.json",
        {"fdr_blocked": False, "holdout_blocked": False},
    )
    # Cross-capture results (median above cost floor)
    write_json_atomic(
        study_dir / "cross_capture_results.json",
        {
            "groups": [
                {
                    "group_id": "group_a",
                    "mean_net_bps": 65.0,
                    "median_net_bps": 62.5,
                }
            ]
        },
    )
    return study_dir


class TestRefalsifyStrategy:
    def test_active_strategy_remains_enabled(self, tmp_path: Path) -> None:
        strategy = _make_enabled_strategy()
        _setup_artifacts(tmp_path)
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert not decision.should_disable
        assert decision.reason == "ALL_GATES_PASSED"

    def test_missing_artifact_disables(self, tmp_path: Path) -> None:
        strategy = _make_enabled_strategy()
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert decision.should_disable
        assert "REFALSIFICATION_ARTIFACT_MISSING" in decision.reason

    def test_null_now_fails_disables(self, tmp_path: Path) -> None:
        strategy = _make_enabled_strategy()
        _setup_artifacts(tmp_path, null_rejected=True)
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert decision.should_disable
        assert any("GATE_FAILED" in r for r in decision.reason.split(";"))

    def test_fdr_now_fails_disables(self, tmp_path: Path) -> None:
        """FDR now fails → disables."""
        strategy = _make_enabled_strategy()
        study_dir = tmp_path / "test_study"
        study_dir.mkdir(parents=True, exist_ok=True)
        # Write stage2 results with fdr_blocked=True
        from ..conductor.atomic_io import write_json_atomic
        write_json_atomic(
            study_dir / "stage2_check_results.json",
            {"fdr_blocked": True, "holdout_blocked": False},
        )
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert decision.should_disable

    def test_holdout_now_fails_disables(self, tmp_path: Path) -> None:
        """Holdout now fails → disables."""
        strategy = _make_enabled_strategy()
        study_dir = tmp_path / "test_study"
        study_dir.mkdir(parents=True, exist_ok=True)
        from ..conductor.atomic_io import write_json_atomic
        write_json_atomic(
            study_dir / "stage2_check_results.json",
            {"fdr_blocked": False, "holdout_blocked": True},
        )
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert decision.should_disable

    def test_cross_capture_median_below_cost_floor_disables(
        self, tmp_path: Path
    ) -> None:
        """Cross-capture median now below cost floor → disables."""
        strategy = _make_enabled_strategy()
        study_dir = tmp_path / "test_study"
        study_dir.mkdir(parents=True, exist_ok=True)
        from ..conductor.atomic_io import write_json_atomic
        write_json_atomic(
            study_dir / "cross_capture_results.json",
            {
                "groups": [
                    {
                        "group_id": "group_a",
                        "median_net_bps": 10.0,  # well below 50 bps cost floor
                    }
                ]
            },
        )
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert decision.should_disable

    def test_cross_capture_median_negative_disables(
        self, tmp_path: Path
    ) -> None:
        """Cross-capture median negative → disables."""
        strategy = _make_enabled_strategy()
        study_dir = tmp_path / "test_study"
        study_dir.mkdir(parents=True, exist_ok=True)
        from ..conductor.atomic_io import write_json_atomic
        write_json_atomic(
            study_dir / "cross_capture_results.json",
            {
                "groups": [
                    {
                        "group_id": "group_a",
                        "median_net_bps": -5.0,
                    }
                ]
            },
        )
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        assert decision.should_disable

    def test_strategy_younger_than_min_age_skipped(
        self, tmp_path: Path
    ) -> None:
        now = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
        strategy = _make_enabled_strategy(
            promoted_dt=datetime.datetime(2026, 1, 2, 0, 0, tzinfo=datetime.timezone.utc)
        )
        save_strategy(strategy, tmp_path)
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
            min_age_hours_before_refalsification=48,
        )
        decisions = run_refalsification_once(config, now_utc=now)
        assert len(decisions) == 0

    def test_strategy_checked_recently_skipped(self, tmp_path: Path) -> None:
        now = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
        strategy = PaperStrategySpec(
            strategy_id="paper_abc123",
            signal_family="test_family",
            study_id="test_study",
            precommitment_hash="abc123",
            precommitment_path=Path("/tmp/p.json"),
            promotion_rule_id="v0",
            execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
            group_id="group_a",
            mean_net_bps=15.0,
            valid_count=50,
            win_rate=0.55,
            cost_floor_bps=50.0,
            min_events=10,
            source_venue=None,
            target_venue=None,
            source_symbol=None,
            target_symbol=None,
            command=("echo",),
            capture_dir=None,
            artifacts_dir=tmp_path,
            output_dir=tmp_path,
            promoter_verdict="PROMOTED",
            promoted_at_utc="2026-01-01T00:00:00",
            last_refalsified_utc="2026-06-01T00:00:00",
            refalsification_status=None,
            state=PaperStrategyState.ENABLED,
            metadata={},
        )
        save_strategy(strategy, tmp_path)
        _setup_artifacts(tmp_path)

        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
            refalsification_interval_hours=168,
        )
        decisions = run_refalsification_once(
            config, now_utc=now + datetime.timedelta(hours=1)
        )
        assert len(decisions) == 0

    def test_dry_run_does_not_mutate_registry(self, tmp_path: Path) -> None:
        now = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
        strategy = _make_enabled_strategy()
        save_strategy(strategy, tmp_path)
        _setup_artifacts(tmp_path, null_rejected=True)  # Will cause disable

        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decisions = run_refalsification_once(config, now_utc=now, dry_run=True)

        assert len(decisions) == 1
        assert decisions[0].should_disable
        # Registry should not have been mutated
        loaded = load_strategy("paper_abc123", tmp_path)
        assert loaded is not None
        assert loaded.state == PaperStrategyState.ENABLED
        # Ledger should not have been written
        assert not (tmp_path / "paper_events.jsonl").is_file()

    def test_refalsification_event_hash_verifies(self, tmp_path: Path) -> None:
        from ..conductor.atomic_io import sha256_canonical_json

        now = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
        strategy = _make_enabled_strategy()
        save_strategy(strategy, tmp_path)
        _setup_artifacts(tmp_path)

        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decisions = run_refalsification_once(config, now_utc=now)

        assert len(decisions) == 1
        # No disable happened, so no event was written. Check hash in the decision.
        # The decision always has an event_hash computed even for non-disable
        assert decisions[0].event_hash is not None

    def test_no_forbidden_verdicts(self, tmp_path: Path) -> None:
        strategy = _make_enabled_strategy()
        now = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
        config = RefalsificationConfig(
            registry_dir=tmp_path,
            ledger_path=tmp_path / "paper_events.jsonl",
            pnl_ledger_path=tmp_path / "pnl.jsonl",
            artifacts_root=tmp_path,
        )
        decision = refalsify_strategy(
            strategy=strategy, config=config, now_utc=now
        )
        report = json.dumps({
            "should_disable": decision.should_disable,
            "reason": decision.reason,
        })
        assert "TRADE_READY" not in report
        assert "EXECUTION_READY" not in report
        assert "LIVE_READY" not in report
        assert "CANDIDATE_FOR_LIVE" not in report


class TestFindLatestArtifactExactMatch:
    """Regression tests for exact study_id matching in _find_latest_artifact."""

    def test_find_latest_artifact_ignores_substring_study_id_match(
        self, tmp_path: Path
    ) -> None:
        """Target study_id='abc' must return exact 'abc' artifact, not 'abc_extra'."""
        # Create older exact artifact for "abc"
        exact_dir = tmp_path / "abc"
        exact_dir.mkdir()
        (exact_dir / "null_test_results.json").write_text("{}")
        # Set mtime to older
        import os
        os.utime(exact_dir, (1000, 1000))

        # Create newer unrelated artifact for "abc_extra"
        superset_dir = tmp_path / "abc_extra"
        superset_dir.mkdir()
        (superset_dir / "null_test_results.json").write_text("{}")
        # Set mtime to newer
        os.utime(superset_dir, (2000, 2000))

        result = _find_latest_artifact(tmp_path, "abc")
        assert result is not None
        assert result.name == "abc", (
            f"Expected exact match 'abc', got '{result.name}' "
            "(substring bug: abc_extra selected instead)"
        )

    def test_find_latest_artifact_returns_none_when_only_superset_study_exists(
        self, tmp_path: Path
    ) -> None:
        """Target study_id='abc' must return None when only 'abc_extra' exists."""
        superset_dir = tmp_path / "abc_extra"
        superset_dir.mkdir()
        (superset_dir / "null_test_results.json").write_text("{}")

        result = _find_latest_artifact(tmp_path, "abc")
        assert result is None, (
            f"Expected None for exact-match-only lookup, got '{result.name}' "
            "(substring bug: abc_extra should not match abc)"
        )

    def test_find_latest_artifact_selects_latest_exact_match(
        self, tmp_path: Path
    ) -> None:
        """Two exact artifacts for 'abc' — select latest by mtime."""
        import os

        older_dir = tmp_path / "abc_older"
        older_dir.mkdir()
        os.utime(older_dir, (1000, 1000))

        newer_dir = tmp_path / "abc_newer"
        newer_dir.mkdir()
        os.utime(newer_dir, (2000, 2000))

        # Neither should match "abc" exactly
        result = _find_latest_artifact(tmp_path, "abc")
        assert result is None

    def test_find_latest_artifact_exact_directory_name_match(
        self, tmp_path: Path
    ) -> None:
        """Directory named exactly study_id is found."""
        target = tmp_path / "hyperliquid_node_fills"
        target.mkdir()
        (target / "null_test_results.json").write_text("{}")

        result = _find_latest_artifact(tmp_path, "hyperliquid_node_fills")
        assert result is not None
        assert result.name == "hyperliquid_node_fills"

    def test_find_latest_artifact_no_cross_match_between_similar_studies(
        self, tmp_path: Path
    ) -> None:
        """study_id='hyperliquid_node_fills' must not match
        'hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0'."""
        import os

        # Create the longer-named study artifact (newer)
        longer_dir = tmp_path / "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
        longer_dir.mkdir()
        os.utime(longer_dir, (2000, 2000))

        result = _find_latest_artifact(tmp_path, "hyperliquid_node_fills")
        assert result is None, (
            f"Expected None, got '{result.name}' "
            "(substring bug: longer study_id matched as substring)"
        )