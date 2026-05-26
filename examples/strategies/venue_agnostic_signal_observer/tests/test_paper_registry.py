"""Tests for paper registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..paper.models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState
from ..paper.registry import (
    delete_strategy,
    load_all_strategies,
    load_strategy,
    save_strategy,
    update_strategy_state,
)


def _make_spec(strategy_id: str = "paper_test") -> PaperStrategySpec:
    return PaperStrategySpec(
        strategy_id=strategy_id,
        signal_family="test_family",
        study_id="test_study",
        precommitment_hash="abc123",
        precommitment_path=Path("/tmp/p.json"),
        promotion_rule_id="rule_v0",
        execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
        group_id="g1",
        mean_net_bps=10.0,
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
        promoted_at_utc="2026-01-01T00:00:00",
        last_refalsified_utc=None,
        refalsification_status=None,
        state=PaperStrategyState.ENABLED,
        metadata={},
    )


class TestRegistry:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        spec = _make_spec()
        save_strategy(spec, tmp_path)
        loaded = load_strategy("paper_test", tmp_path)
        assert loaded is not None
        assert loaded.strategy_id == spec.strategy_id
        assert loaded.signal_family == spec.signal_family
        assert loaded.precommitment_hash == spec.precommitment_hash
        assert loaded.execution_mode == spec.execution_mode
        assert loaded.mean_net_bps == spec.mean_net_bps

    def test_load_all_strategies(self, tmp_path: Path) -> None:
        spec1 = _make_spec("paper_1")
        spec2 = _make_spec("paper_2")
        save_strategy(spec1, tmp_path)
        save_strategy(spec2, tmp_path)
        all_specs = load_all_strategies(tmp_path)
        assert len(all_specs) == 2
        ids = {s.strategy_id for s in all_specs}
        assert ids == {"paper_1", "paper_2"}

    def test_delete_strategy(self, tmp_path: Path) -> None:
        spec = _make_spec()
        save_strategy(spec, tmp_path)
        assert load_strategy("paper_test", tmp_path) is not None
        deleted = delete_strategy("paper_test", tmp_path)
        assert deleted
        assert load_strategy("paper_test", tmp_path) is None

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        assert not delete_strategy("does_not_exist", tmp_path)

    def test_update_state(self, tmp_path: Path) -> None:
        spec = _make_spec()
        save_strategy(spec, tmp_path)
        updated = update_strategy_state(
            "paper_test", PaperStrategyState.DISABLED, tmp_path
        )
        assert updated is not None
        assert updated.state == PaperStrategyState.DISABLED
        loaded = load_strategy("paper_test", tmp_path)
        assert loaded is not None
        assert loaded.state == PaperStrategyState.DISABLED

    def test_corrupt_file_skipped(self, tmp_path: Path) -> None:
        # Save a valid spec
        spec = _make_spec("paper_valid")
        save_strategy(spec, tmp_path)
        # Write a corrupt file
        (tmp_path / "paper_corrupt.json").write_text("not json")
        all_specs = load_all_strategies(tmp_path)
        assert len(all_specs) == 1
        assert all_specs[0].strategy_id == "paper_valid"

    def test_non_json_files_skipped(self, tmp_path: Path) -> None:
        spec = _make_spec()
        save_strategy(spec, tmp_path)
        (tmp_path / "readme.txt").write_text("hello")
        all_specs = load_all_strategies(tmp_path)
        assert len(all_specs) == 1

    def test_empty_registry(self, tmp_path: Path) -> None:
        all_specs = load_all_strategies(tmp_path)
        assert all_specs == []

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        assert load_strategy("does_not_exist", tmp_path) is None