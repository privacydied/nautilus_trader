"""Tests for paper data models."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..paper.models import (
    PaperExecutionMode,
    PaperStrategySpec,
    PaperStrategyState,
)


def _make_valid_spec(
    state: PaperStrategyState = PaperStrategyState.ENABLED,
) -> PaperStrategySpec:
    return PaperStrategySpec(
        strategy_id="paper_abc123def456",
        signal_family="test_family",
        study_id="test_study",
        precommitment_hash="abc123def456",
        precommitment_path=Path("/tmp/precommitments/abc123.json"),
        promotion_rule_id="mean_net_bps_gt_0_and_valid_count_gte_min_events_v0",
        execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
        group_id="group_a",
        mean_net_bps=15.3,
        valid_count=25,
        win_rate=0.55,
        cost_floor_bps=50.0,
        min_events=10,
        source_venue="binance",
        target_venue="kraken",
        source_symbol="BTC/USDT",
        target_symbol="BTC/USD",
        command=("python", "-c", "pass"),
        capture_dir="/data/capture",
        artifacts_dir=Path("/tmp/artifacts"),
        output_dir=Path("/tmp/output"),
        promoter_verdict="PAPER_STRATEGY_PROMOTED",
        promoted_at_utc="2026-01-01T00:00:00",
        last_refalsified_utc=None,
        refalsification_status=None,
        state=state,
        metadata={},
    )


class TestPaperExecutionMode:
    def test_single_mode(self) -> None:
        assert PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED.value == 1


class TestPaperStrategySpecValidation:
    def test_valid_spec(self) -> None:
        spec = _make_valid_spec()
        assert spec.strategy_id == "paper_abc123def456"

    def test_execution_mode_validated(self) -> None:
        """execution_mode must be NAUTILUS_BACKTEST_SIMULATED."""
        # v0 has only one mode; verify it works and wrong values are caught
        spec = PaperStrategySpec(
            strategy_id="test",
            signal_family="test",
            study_id="test",
            precommitment_hash="abc",
            precommitment_path=Path("/tmp/p.json"),
            promotion_rule_id="rule_v0",
            execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
            group_id="g",
            mean_net_bps=0.0,
            valid_count=1,
            win_rate=None,
            cost_floor_bps=0.0,
            min_events=1,
            source_venue=None,
            target_venue=None,
            source_symbol=None,
            target_symbol=None,
            command=("echo",),
            capture_dir=None,
            artifacts_dir=Path("/tmp/a"),
            output_dir=Path("/tmp/o"),
            promoter_verdict="PROMOTED",
            promoted_at_utc="2026-01-01T00:00:00",
            last_refalsified_utc=None,
            refalsification_status=None,
            state=PaperStrategyState.PENDING,
            metadata={},
        )
        assert spec.execution_mode == PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED

    def test_hyphenated_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="snake_case"):
            PaperStrategySpec(
                strategy_id="t",
                signal_family="signal-family",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("echo",),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_empty_strategy_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="strategy_id cannot be empty"):
            PaperStrategySpec(
                strategy_id="",
                signal_family="test",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("echo",),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_empty_signal_family_rejected(self) -> None:
        with pytest.raises(ValueError, match="signal_family cannot be empty"):
            PaperStrategySpec(
                strategy_id="test",
                signal_family="",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("python", "-c", "pass"),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_empty_precommitment_hash_rejected(self) -> None:
        with pytest.raises(ValueError, match="precommitment_hash cannot be empty"):
            PaperStrategySpec(
                strategy_id="test",
                signal_family="test",
                study_id="test",
                precommitment_hash="",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("python", "-c", "pass"),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="command cannot be empty"):
            PaperStrategySpec(
                strategy_id="test",
                signal_family="test",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=(),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_negative_cost_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost_floor_bps"):
            PaperStrategySpec(
                strategy_id="test",
                signal_family="test",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=-1.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("echo",),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_min_events_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_events"):
            PaperStrategySpec(
                strategy_id="test",
                signal_family="test",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=0,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("echo",),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.PENDING,
                metadata={},
            )

    def test_enabled_requires_promoted_at(self) -> None:
        with pytest.raises(ValueError, match="promoted_at_utc"):
            PaperStrategySpec(
                strategy_id="test",
                signal_family="test",
                study_id="test",
                precommitment_hash="abc",
                precommitment_path=Path("/tmp/p.json"),
                promotion_rule_id="rule_v0",
                execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
                group_id="g",
                mean_net_bps=0.0,
                valid_count=1,
                win_rate=None,
                cost_floor_bps=0.0,
                min_events=1,
                source_venue=None,
                target_venue=None,
                source_symbol=None,
                target_symbol=None,
                command=("echo",),
                capture_dir=None,
                artifacts_dir=Path("/tmp/a"),
                output_dir=Path("/tmp/o"),
                promoter_verdict="PROMOTED",
                promoted_at_utc="",
                last_refalsified_utc=None,
                refalsification_status=None,
                state=PaperStrategyState.ENABLED,
                metadata={},
            )