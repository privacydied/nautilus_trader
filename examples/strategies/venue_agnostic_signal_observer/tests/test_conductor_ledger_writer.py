"""Tests for the ledger writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..conductor.atomic_io import sha256_canonical_json
from ..conductor.ledger_writer import write_locked_run_event
from ..conductor.models import (
    ConductorJobResult,
    ConductorJobSpec,
    ConductorJobStatus,
    ConductorRunMode,
    ConductorSourceKind,
)


def _make_job(
    run_mode: ConductorRunMode = ConductorRunMode.LOCKED,
    precommitment_hash: str | None = "abc123",
    job_id: str = "locked_job_001",
    signal_family: str = "test_family",
    study_id: str = "test_study",
    output_dir: str = "/tmp/conductor/locked/test",
) -> ConductorJobSpec:
    meta = {}
    if precommitment_hash is not None:
        meta["precommitment_hash"] = precommitment_hash
    return ConductorJobSpec(
        job_id=job_id,
        source_kind=ConductorSourceKind.EXISTING_CAPTURE,
        signal_family=signal_family,
        study_id=study_id,
        command=("python", "-c", "pass"),
        capture_dir=None,
        report_dir=None,
        output_dir=output_dir,
        run_mode=run_mode,
        min_events=50,
        cost_floor_bps=50.0,
        structural_change_rationale=None,
        requested_devices=(),
        metadata=meta,
    )


def _make_result(
    status: ConductorJobStatus = ConductorJobStatus.COMPLETED,
) -> ConductorJobResult:
    return ConductorJobResult(
        job_id="locked_job_001",
        status=status,
        returncode=0,
        started_at_utc="2026-01-01T00:00:00",
        finished_at_utc="2026-01-01T00:01:00",
        output_dir="/tmp/conductor/locked/test",
        summary_path="/tmp/conductor/locked/test/summary.json",
        error=None,
        metadata={},
    )


class TestWriteLockedRunEvent:
    def test_exploration_job_refused(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(
            run_mode=ConductorRunMode.EXPLORATION,
            output_dir="/tmp/conductor/exploration/test_exp_refused",
        )
        result = _make_result()
        written = write_locked_run_event(ledger, job, result)
        assert not written
        assert not ledger.is_file()

    def test_locked_job_without_hash_refused(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(precommitment_hash=None)
        result = _make_result()
        written = write_locked_run_event(ledger, job, result)
        assert not written
        assert not ledger.is_file()

    def test_locked_job_with_hash_writes_one_event(
        self, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(precommitment_hash="def456")
        result = _make_result()
        written = write_locked_run_event(ledger, job, result)
        assert written
        lines = ledger.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_event_contains_required_fields(
        self, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(precommitment_hash="ghi789")
        result = _make_result()
        write_locked_run_event(ledger, job, result)
        line = json.loads(ledger.read_text().strip())
        assert line["event_type"] == "CONDUCTOR_LOCKED_RUN_COMPLETED"
        assert line["job_id"] == "locked_job_001"
        assert line["signal_family"] == "test_family"
        assert line["precommitment_hash"] == "ghi789"
        assert line["result_status"] == "COMPLETED"
        assert line["returncode"] == 0
        assert "event_hash" in line
        assert "created_at_utc" in line

    def test_event_hash_verifies(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(precommitment_hash="jkl012")
        result = _make_result()
        write_locked_run_event(ledger, job, result)
        event = json.loads(ledger.read_text().strip())
        payload_without_hash = {k: v for k, v in event.items() if k != "event_hash"}
        expected_hash = sha256_canonical_json(payload_without_hash)
        assert event["event_hash"] == expected_hash

    def test_second_different_event_appends(
        self, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job1 = _make_job(job_id="job_1")
        result1 = _make_result()
        write_locked_run_event(ledger, job1, result1)
        job2 = ConductorJobSpec(
            job_id="job_2",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="other_family",
            study_id="other_study",
            command=("python", "-c", "pass"),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/conductor/locked/test2",
            run_mode=ConductorRunMode.LOCKED,
            min_events=50,
            cost_floor_bps=50.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={"precommitment_hash": "mno345"},
        )
        result2 = _make_result()
        write_locked_run_event(ledger, job2, result2)
        lines = ledger.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_duplicate_event_returns_false(
        self, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(precommitment_hash="pqr678")
        result = _make_result()
        first = write_locked_run_event(ledger, job, result)
        assert first
        second = write_locked_run_event(ledger, job, result)
        assert not second
        lines = ledger.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_ledger_writer_uses_append_jsonl_durable(
        self, tmp_path: Path
    ) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        job = _make_job(precommitment_hash="stu901")
        result = _make_result()
        write_locked_run_event(ledger, job, result)
        lines = ledger.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) is not None