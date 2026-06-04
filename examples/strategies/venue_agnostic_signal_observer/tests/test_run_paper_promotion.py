"""Tests for run_paper_promotion CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ..conductor.atomic_io import write_json_atomic, append_jsonl_durable


def _setup_precommitment(precommitment_dir: Path) -> None:
    write_json_atomic(
        precommitment_dir / "abc123.json",
        {
            "ledger_write_authorized_before_locked_run": True,
            "registry_verdict_authorized": True,
            "group_id": "group_a",
            "group_payload": {"mean_net_bps": 15.0, "valid_count": 50},
            "source_job_spec": {
                "signal_family": "test",
                "study_id": "test",
                "command": ["python", "-c", "pass"],
            },
            "cost_floor_bps": 50.0,
            "min_events": 50,
        },
    )


def _setup_evidence_ledger(ledger_path: Path) -> None:
    append_jsonl_durable(
        ledger_path,
        {
            "event_type": "CONDUCTOR_LOCKED_RUN_COMPLETED",
            "precommitment_hash": "abc123",
        },
    )


class TestRunPaperPromotionCLI:
    def test_help_works(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.strategies.venue_agnostic_signal_observer.run_paper_promotion",
                "--help",
            ],
            capture_output=True,
            text=True,
            cwd="/mnt/nasirjones/py/nautilus_trader",
        )
        assert result.returncode == 0
        assert "help" in result.stdout.lower() or "usage:" in result.stdout.lower()

    def test_dry_run_exits_cleanly(self, tmp_path: Path) -> None:
        precommitment_dir = tmp_path / "precommitments"
        precommitment_dir.mkdir()
        _setup_precommitment(precommitment_dir)
        _setup_evidence_ledger(tmp_path / "evidence_ledger.jsonl")

        artifacts_base = tmp_path
        write_json_atomic(
            artifacts_base / "summary.json",
            {"groups": [{"group_id": "group_a", "mean_net_bps": 15.0, "valid_count": 50}]},
        )
        write_json_atomic(
            artifacts_base / "conductor_result.json",
            {"job_id": "test", "status": "COMPLETED"},
        )

        cmd = [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.run_paper_promotion",
            "--precommitment-hash",
            "abc123",
            "--precommitment-dir",
            str(precommitment_dir),
            "--registry-dir",
            str(tmp_path / "registry"),
            "--paper-events-ledger",
            str(tmp_path / "paper_events.jsonl"),
            "--evidence-ledger",
            str(tmp_path / "evidence_ledger.jsonl"),
            "--artifacts-base-dir",
            str(artifacts_base),
            "--dry-run",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Verify no writes
        assert not (tmp_path / "registry" / "paper_abc123.json").is_file()
        assert not (tmp_path / "paper_events.jsonl").is_file()

    def test_dry_run_no_network(self) -> None:
        # Static assertion that run_paper_promotion has no network imports
        import ast

        path = Path(
            __file__
        ).parent.parent / "run_paper_promotion.py"
        assert path.is_file()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "socket" not in alias.name
                    assert "requests" not in alias.name
                    assert "urllib" not in alias.name

    def test_dry_run_no_nautilus_import(self) -> None:
        import ast

        path = Path(
            __file__
        ).parent.parent / "run_paper_promotion.py"
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