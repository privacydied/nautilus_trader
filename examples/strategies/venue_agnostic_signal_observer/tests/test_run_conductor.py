"""Tests for the run_conductor.py CLI entrypoint and service layer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ..conductor.ledger_writer import write_locked_run_event
from ..conductor.models import (
    ConductorJobResult,
    ConductorJobSpec,
    ConductorJobStatus,
    ConductorRunMode,
    ConductorSourceKind,
)
from ..conductor.service import (
    ConductorConfig,
    ConductorRuntimeState,
    run_conductor_once,
)


def _make_minimal_config(tmp_path: Path) -> ConductorConfig:
    return ConductorConfig(
        data_root=tmp_path / "data",
        reports_root=tmp_path / "reports",
        rejected_research_path=tmp_path / "REJECTED_RESEARCH.md",
        ledger_path=tmp_path / "evidence_ledger.jsonl",
        precommitment_dir=tmp_path / "precommitments",
        gpu_lock_dir=tmp_path / "gpu_locks",
        available_devices=(),
        default_min_events=50,
        default_cost_floor_bps=50.0,
        poll_existing_captures=False,
        poll_gate_watcher=False,
        poll_archive_windows=False,
        gate_watcher_status_path=None,
        archive_windows_path=None,
        command_templates={},
        dry_run=True,
    )


class TestRunConductorCLI:
    def test_help_works(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_conductor",
                "--help",
            ],
            capture_output=True,
            text=True,
            cwd="/mnt/nasirjones/py/nautilus_trader",
        )
        assert result.returncode == 0
        assert "help" in result.stdout.lower() or "usage:" in result.stdout.lower()

    def test_dry_run_with_empty_config_exits_cleanly(
        self, tmp_path: Path
    ) -> None:
        config = {
            "data_root": str(tmp_path / "data"),
            "reports_root": str(tmp_path / "reports"),
            "rejected_research_path": str(tmp_path / "REJECTED_RESEARCH.md"),
            "ledger_path": str(tmp_path / "evidence_ledger.jsonl"),
            "precommitment_dir": str(tmp_path / "precommitments"),
            "gpu_lock_dir": str(tmp_path / "gpu_locks"),
            "available_devices": [],
            "default_min_events": 50,
            "default_cost_floor_bps": 50.0,
            "poll_existing_captures": False,
            "poll_gate_watcher": False,
            "poll_archive_windows": False,
            "gate_watcher_status_path": None,
            "archive_windows_path": None,
            "command_templates": {},
        }
        config_path = tmp_path / "conductor_config.json"
        config_path.write_text(json.dumps(config))

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_conductor",
                "--config",
                str(config_path),
                "--once",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd="/mnt/nasirjones/py/nautilus_trader",
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestRunConductorOnce:
    def test_no_sources_returns_empty(
        self, tmp_path: Path
    ) -> None:
        config = _make_minimal_config(tmp_path)
        results = run_conductor_once(config)
        assert isinstance(results, list)

    def test_dry_run_does_not_execute_subprocess(
        self, tmp_path: Path
    ) -> None:
        config = ConductorConfig(
            data_root=tmp_path / "data",
            reports_root=tmp_path / "reports",
            rejected_research_path=tmp_path / "REJECTED_RESEARCH.md",
            ledger_path=tmp_path / "evidence_ledger.jsonl",
            precommitment_dir=tmp_path / "precommitments",
            gpu_lock_dir=tmp_path / "gpu_locks",
            available_devices=(),
            default_min_events=50,
            default_cost_floor_bps=50.0,
            poll_existing_captures=True,
            poll_gate_watcher=False,
            poll_archive_windows=False,
            gate_watcher_status_path=None,
            archive_windows_path=None,
            command_templates={
                "existing_capture_eval": ("python", "-c", "print('should not run')")
            },
            dry_run=True,
        )
        results = run_conductor_once(config)
        for r in results:
            if r.status == ConductorJobStatus.COMPLETED:
                assert r.metadata.get("dry_run") is True

    def test_dry_run_does_not_write_ledger(
        self, tmp_path: Path
    ) -> None:
        config = _make_minimal_config(tmp_path)
        run_conductor_once(config)
        ledger = tmp_path / "evidence_ledger.jsonl"
        assert not ledger.is_file()

    def test_no_duplicate_job_ids(
        self, tmp_path: Path
    ) -> None:
        config = _make_minimal_config(tmp_path)
        state = ConductorRuntimeState()

        results1 = run_conductor_once(config, state)
        results2 = run_conductor_once(config, state)

        all_ids = [r.job_id for r in results1] + [r.job_id for r in results2]
        assert len(all_ids) == len(set(all_ids)), f"Duplicate job IDs: {all_ids}"

    def test_no_duplicate_ledger_events(
        self, tmp_path: Path
    ) -> None:
        config = ConductorConfig(
            data_root=tmp_path / "data",
            reports_root=tmp_path / "reports",
            rejected_research_path=tmp_path / "REJECTED_RESEARCH.md",
            ledger_path=tmp_path / "evidence_ledger.jsonl",
            precommitment_dir=tmp_path / "precommitments",
            gpu_lock_dir=tmp_path / "gpu_locks",
            available_devices=(),
            default_min_events=50,
            default_cost_floor_bps=50.0,
            poll_existing_captures=False,
            poll_gate_watcher=False,
            poll_archive_windows=False,
            gate_watcher_status_path=None,
            archive_windows_path=None,
            command_templates={},
            dry_run=False,
        )
        state = ConductorRuntimeState()

        ledger_path = config.ledger_path
        job = ConductorJobSpec(
            job_id="test_locked",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="test",
            study_id="test",
            command=("echo", "done"),
            capture_dir=None,
            report_dir=None,
            output_dir=str(
                tmp_path / "reports" / "conductor" / "locked" / "test"
            ),
            run_mode=ConductorRunMode.LOCKED,
            min_events=50,
            cost_floor_bps=50.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={"precommitment_hash": "test_hash_001"},
        )
        result = ConductorJobResult(
            job_id="test_locked",
            status=ConductorJobStatus.COMPLETED,
            returncode=0,
            started_at_utc="2026-01-01T00:00:00",
            finished_at_utc="2026-01-01T00:01:00",
            output_dir=str(
                tmp_path / "reports" / "conductor" / "locked" / "test"
            ),
            summary_path=None,
            error=None,
            metadata={},
        )

        written1 = write_locked_run_event(ledger_path, job, result)
        assert written1

        ledger_lines_before = ledger_path.read_text().strip().split("\n")

        state.seen_ledger_event_keys.add(
            "test_locked:test_hash_001:COMPLETED"
        )
        results = run_conductor_once(config, state)
        _ = results

        ledger_lines_after = ledger_path.read_text().strip().split("\n")
        assert len(ledger_lines_after) == len(ledger_lines_before)
        assert len(ledger_lines_after) == 1