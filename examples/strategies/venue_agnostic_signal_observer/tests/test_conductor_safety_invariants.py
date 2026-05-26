"""Cross-cutting safety invariant tests for the conductor."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..conductor.atomic_io import sha256_canonical_json
from ..conductor.gpu_scheduler import acquire_gpu_devices
from ..conductor.ledger_writer import write_locked_run_event
from ..conductor.locked_gate_filter import LockedGateDecision
from ..conductor.models import (
    ConductorJobResult,
    ConductorJobSpec,
    ConductorJobStatus,
    ConductorRunMode,
    ConductorSourceKind,
)
from ..conductor.policy import FORBIDDEN_VERDICTS
from ..conductor.promotion import _build_locked_output_path
from ..conductor.runner import build_runner_env


class TestSafetyInvariants:
    def test_invariant_1_exploration_env_disables_ledger(
        self,
    ) -> None:
        job = ConductorJobSpec(
            job_id="inv_1",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="test",
            study_id="test",
            command=("echo",),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/cond/inv/exploration/test",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=1,
            cost_floor_bps=0.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        env = build_runner_env(job)
        assert env["VA_SIGNAL_OBSERVER_CONDUCTOR_MODE"] == "exploration"
        assert env["VA_SIGNAL_OBSERVER_LEDGER_WRITE_ALLOWED"] == "0"

    def test_invariant_2_locked_job_without_hash_raises(
        self, tmp_path: Path
    ) -> None:
        job = ConductorJobSpec(
            job_id="inv_2",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="test",
            study_id="test",
            command=("echo",),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/cond/inv/locked/test",
            run_mode=ConductorRunMode.LOCKED,
            min_events=1,
            cost_floor_bps=0.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        result = ConductorJobResult(
            job_id="inv_2",
            status=ConductorJobStatus.COMPLETED,
            returncode=0,
            started_at_utc="2026-01-01T00:00:00",
            finished_at_utc="2026-01-01T00:01:00",
            output_dir="/tmp/cond/inv/locked/test",
            summary_path=None,
            error=None,
            metadata={},
        )
        ledger_path = tmp_path / "evidence_ledger.jsonl"
        written = write_locked_run_event(ledger_path, job, result)
        assert not written, "locked job without hash must not write ledger"

    def test_invariant_3_promotion_path_swaps_first_exploration(
        self,
    ) -> None:
        job = ConductorJobSpec(
            job_id="inv_3",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="test",
            study_id="test",
            command=("echo",),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/conductor/exploration/exploration_run_2026",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=1,
            cost_floor_bps=0.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        result = _build_locked_output_path(job.output_dir, job)
        locked_path = Path(result)
        parts = locked_path.parts
        assert "locked" in parts
        assert parts[-1] == "exploration_run_2026"
        assert list(parts).count("locked") == 1

    def test_invariant_4_forbidden_verdicts_nonempty(
        self,
    ) -> None:
        assert len(FORBIDDEN_VERDICTS) > 0
        assert "TRADE_READY" in FORBIDDEN_VERDICTS

    def test_invariant_5_locked_gate_empty_rationale_blocks(
        self,
    ) -> None:
        decision = LockedGateDecision.blocked_with_gates(
            gate_numbers=(1,),
            reason="LOCKED_GATE_OVERLAP_NO_STRUCTURAL_RATIONALE",
        )
        assert not decision.allowed
        assert decision.reason == "LOCKED_GATE_OVERLAP_NO_STRUCTURAL_RATIONALE"
        assert len(decision.matched_gate_numbers) == 1

    def test_invariant_6_canonical_json_order_independent(
        self,
    ) -> None:
        a = sha256_canonical_json({"a": 1, "b": 2})
        b = sha256_canonical_json({"b": 2, "a": 1})
        assert a == b

    def test_invariant_7_gpu_fcntl_fallback(
        self, tmp_path: Path
    ) -> None:
        from ..conductor import gpu_scheduler as gpu_mod

        result = gpu_mod.validate_device_syntax([])
        assert result == ()

        saved = gpu_mod.fcntl
        gpu_mod.fcntl = None
        try:
            with pytest.raises(RuntimeError, match="fcntl module is unavailable"):
                with gpu_mod.acquire_gpu_devices(
                    ("cuda:0",), lock_dir=tmp_path
                ):
                    pass
        finally:
            gpu_mod.fcntl = saved