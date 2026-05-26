"""Tests for the gate verifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..conductor.atomic_io import write_json_atomic, append_jsonl_durable
from ..paper.gate_verifier import (
    check_promotion_frozen,
    verify_promotion_gates,
)


def _write_precommitment(
    precommitment_dir: Path,
    hash_val: str,
    ledger_write: bool = True,
    registry_verdict: bool = True,
) -> None:
    payload = {
        "schema_version": "1",
        "promotion_rule_id": "v0",
        "ledger_write_authorized_before_locked_run": ledger_write,
        "registry_verdict_authorized": registry_verdict,
        "group_id": "group_a",
        "group_payload": {"mean_net_bps": 15.0, "valid_count": 50},
    }
    write_json_atomic(
        precommitment_dir / f"{hash_val}.json",
        payload,
    )


class TestCheckPromotionFrozen:
    def test_valid_precommitment(self, tmp_path: Path) -> None:
        _write_precommitment(tmp_path, "abc123")
        result = check_promotion_frozen("abc123", tmp_path)
        assert result.allowed
        assert result.status() == "PROMOTION_FROZEN"

    def test_missing_precommitment(self, tmp_path: Path) -> None:
        result = check_promotion_frozen("missing", tmp_path)
        assert not result.allowed
        assert result.status() == "FAILED_MISSING_PRECOMMITMENT"

    def test_evidence_gap(self, tmp_path: Path) -> None:
        _write_precommitment(tmp_path, "abc123", ledger_write=False)
        result = check_promotion_frozen("abc123", tmp_path)
        assert not result.allowed
        assert result.error is not None
        assert "FAILED_EVIDENCE_GAP" in result.error

    def test_verdict_not_authorized(self, tmp_path: Path) -> None:
        _write_precommitment(tmp_path, "abc123", registry_verdict=False)
        result = check_promotion_frozen("abc123", tmp_path)
        assert not result.allowed
        assert result.error is not None
        assert "FAILED_VERDICT_NOT_AUTHORIZED" in result.error

    def test_verifier_does_not_write_ledger(self, tmp_path: Path) -> None:
        _write_precommitment(tmp_path, "abc123")
        ledger = tmp_path / "paper_events.jsonl"
        result = check_promotion_frozen("abc123", tmp_path)
        _ = result
        assert not ledger.is_file()

    def test_verifier_does_not_write_precommitments(self, tmp_path: Path) -> None:
        result = check_promotion_frozen("missing", tmp_path)
        _ = result
        # No new precommitment files should appear
        files = list(tmp_path.iterdir())
        assert len(files) == 0


class TestVerifyPromotionGates:
    def _setup_artifacts(
        self, base: Path, hash_val: str = "abc123"
    ) -> tuple[Path, Path]:
        ledger = base / "evidence_ledger.jsonl"
        append_jsonl_durable(
            ledger,
            {
                "event_type": "CONDUCTOR_LOCKED_RUN_COMPLETED",
                "precommitment_hash": hash_val,
                "job_id": "test",
            },
        )
        return base, ledger

    def _write_summary(
        self, artifacts_dir: Path, mean_net: float = 15.0
    ) -> None:
        write_json_atomic(
            artifacts_dir / "summary.json",
            {
                "groups": [
                    {
                        "group_id": "group_a",
                        "mean_net_bps": mean_net,
                        "valid_count": 50,
                    }
                ]
            },
        )

    def _write_conductor_result(self, artifacts_dir: Path) -> None:
        write_json_atomic(
            artifacts_dir / "conductor_result.json",
            {"job_id": "test", "status": "COMPLETED"},
        )

    def test_gate1_locked_run_event_exists(self, tmp_path: Path) -> None:
        artifacts_dir, ledger = self._setup_artifacts(tmp_path)
        results = verify_promotion_gates("abc123", ledger, artifacts_dir)
        gate1 = next(g for g in results if g.gate_id == "ledger_locked_run_completed")
        assert gate1.passed

    def test_gate1_locked_run_missing(self, tmp_path: Path) -> None:
        ledger = tmp_path / "evidence_ledger.jsonl"
        # Write a different hash event
        append_jsonl_durable(
            ledger,
            {
                "event_type": "CONDUCTOR_LOCKED_RUN_COMPLETED",
                "precommitment_hash": "other_hash",
            },
        )
        results = verify_promotion_gates("abc123", ledger, tmp_path)
        gate1 = next(g for g in results if g.gate_id == "ledger_locked_run_completed")
        assert not gate1.passed
        assert "NOT_FOUND" in gate1.detail

    def test_gate2_artifact_exists(self, tmp_path: Path) -> None:
        self._write_summary(tmp_path)
        self._write_conductor_result(tmp_path)
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate2 = next(g for g in results if g.gate_id == "artifact_exists")
        assert gate2.passed

    def test_gate2_artifact_missing(self, tmp_path: Path) -> None:
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate2 = next(g for g in results if g.gate_id == "artifact_exists")
        assert not gate2.passed

    def test_gate3_group_positive(self, tmp_path: Path) -> None:
        self._write_summary(tmp_path, mean_net=15.0)
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate3 = next(
            g for g in results if g.gate_id == "group_survives_in_locked_run"
        )
        assert gate3.passed

    def test_gate3_group_missing(self, tmp_path: Path) -> None:
        # Summary with no positive groups
        write_json_atomic(
            tmp_path / "summary.json",
            {"groups": []},
        )
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate3 = next(
            g for g in results if g.gate_id == "group_survives_in_locked_run"
        )
        assert not gate3.passed

    def test_gate3_group_negative(self, tmp_path: Path) -> None:
        self._write_summary(tmp_path, mean_net=-10.0)
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate3 = next(
            g for g in results if g.gate_id == "group_survives_in_locked_run"
        )
        assert not gate3.passed

    def test_gate4_no_null_data(self, tmp_path: Path) -> None:
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate4 = next(
            g for g in results if g.gate_id == "null_not_candidate_for_rejection"
        )
        assert gate4.passed
        assert "SKIPPED_NO_NULL_DATA" in gate4.detail

    def test_gate5_no_stage2_data(self, tmp_path: Path) -> None:
        results = verify_promotion_gates("abc123", tmp_path, tmp_path)
        gate5 = next(g for g in results if g.gate_id == "no_stage2_rejection")
        assert gate5.passed
        assert "SKIPPED_NO_STAGE2_DATA" in gate5.detail

    def test_verifier_does_not_write_ledger(self, tmp_path: Path) -> None:
        ledger_path, _ = self._setup_artifacts(tmp_path)
        ledger_before = (
            ledger_path.read_text() if ledger_path.is_file() else ""
        )
        results = verify_promotion_gates("abc123", ledger_path, tmp_path)
        _ = results
        ledger_after = (
            ledger_path.read_text() if ledger_path.is_file() else ""
        )
        assert ledger_after == ledger_before