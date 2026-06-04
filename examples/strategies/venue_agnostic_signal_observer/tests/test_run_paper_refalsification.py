"""Tests for run_paper_refalsification CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


class TestRunPaperRefalsificationCLI:
    def test_help_works(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_paper_refalsification",
                "--help",
            ],
            capture_output=True,
            text=True,
            cwd="/mnt/nasirjones/py/nautilus_trader",
        )
        assert result.returncode == 0
        assert "help" in result.stdout.lower() or "usage:" in result.stdout.lower()

    def test_dry_run_exits_cleanly(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        pnl_path = tmp_path / "pnl.jsonl"
        pnl_path.touch()

        # No strategies in registry, so should be clean
        cmd = [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_paper_refalsification",
            "--registry-dir",
            str(registry_dir),
            "--ledger-path",
            str(tmp_path / "paper_events.jsonl"),
            "--pnl-ledger",
            str(pnl_path),
            "--artifacts-root",
            str(tmp_path / "artifacts_root"),
            "--once",
            "--dry-run",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_no_nautilus_import(self) -> None:
        import ast

        path = Path(
            __file__
        ).parent.parent / "runners" / "legacy_cli" / "run_paper_refalsification.py"
        assert path.is_file()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "nautilus_trader" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module and "nautilus_trader" in node.module:
                    raise AssertionError(
                        f"Imports nautilus_trader: {node.module}"
                    )

    def test_dry_run_no_ledger_write(self, tmp_path: Path) -> None:
        """Dry-run with a strategy that would be disabled should not write ledger."""
        from ..paper.models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState
        from ..paper.registry import save_strategy

        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        strategy = PaperStrategySpec(
            strategy_id="paper_test",
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
            last_refalsified_utc=None,
            refalsification_status=None,
            state=PaperStrategyState.ENABLED,
            metadata={},
        )
        save_strategy(strategy, registry_dir)

        pnl_path = tmp_path / "pnl.jsonl"
        pnl_path.touch()

        cmd = [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_paper_refalsification",
            "--registry-dir",
            str(registry_dir),
            "--ledger-path",
            str(tmp_path / "paper_events.jsonl"),
            "--pnl-ledger",
            str(pnl_path),
            "--artifacts-root",
            str(tmp_path / "artifacts_root"),
            "--once",
            "--dry-run",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0

        # Registry should not be mutated and no ledger written in dry-run
        from ..paper.registry import load_strategy

        loaded = load_strategy("paper_test", registry_dir)
        assert loaded is not None
        assert loaded.state == PaperStrategyState.ENABLED
        assert not (tmp_path / "paper_events.jsonl").is_file()