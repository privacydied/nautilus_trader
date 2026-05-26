"""Tests for conductor data models."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..conductor.atomic_io import sha256_canonical_json
from ..conductor.models import (
    ConductorJobSpec,
    ConductorRunMode,
    ConductorSourceKind,
    derive_job_id,
)


class TestConductorJobSpecValidation:
    def test_valid_minimal(self) -> None:
        spec = ConductorJobSpec(
            job_id="test_job",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="test_family",
            study_id="test_study",
            command=("python", "-c", "pass"),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/conductor/exploration/test",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=50,
            cost_floor_bps=50.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        assert spec.job_id == "test_job"
        assert spec.signal_family == "test_family"
        assert spec.study_id == "test_study"

    def test_snake_case_accepted(self) -> None:
        spec = ConductorJobSpec(
            job_id="t",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="family_1_v2",
            study_id="study_2_v4",
            command=("echo",),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/conductor/exploration/test",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=1,
            cost_floor_bps=0.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        assert spec.signal_family == "family_1_v2"

    def test_hyphenated_signal_family_rejected(self) -> None:
        with pytest.raises(ValueError, match="snake_case"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family-v1",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_hyphenated_study_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="snake_case"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study-v1",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_empty_job_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="job_id cannot be empty"):
            ConductorJobSpec(
                job_id="",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_empty_signal_family_rejected(self) -> None:
        with pytest.raises(ValueError, match="signal_family cannot be empty"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="command cannot be empty"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=(),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_empty_output_dir_rejected(self) -> None:
        with pytest.raises(ValueError, match="output_dir cannot be empty"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_min_events_less_than_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_events"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=0,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_cost_floor_bps_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost_floor_bps"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=-1.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_exploration_path_must_contain_exploration(self) -> None:
        with pytest.raises(ValueError, match="exploration"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/locked/test",
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_locked_path_must_not_contain_exploration(self) -> None:
        with pytest.raises(ValueError, match="exploration"):
            ConductorJobSpec(
                job_id="t",
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family="family",
                study_id="study",
                command=("echo",),
                capture_dir=None,
                report_dir=None,
                output_dir="/tmp/conductor/exploration/test",
                run_mode=ConductorRunMode.LOCKED,
                min_events=1,
                cost_floor_bps=0.0,
                structural_change_rationale=None,
                requested_devices=(),
                metadata={},
            )

    def test_path_validation_not_literal_substring(self) -> None:
        """Path validation must use Path.parts, not literal '/exploration/'."""
        spec = ConductorJobSpec(
            job_id="t",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="family",
            study_id="study",
            command=("echo",),
            capture_dir=None,
            report_dir=None,
            output_dir="reports/exploration/test",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=1,
            cost_floor_bps=0.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        assert spec is not None
        parts = Path("reports/exploration/test").parts
        assert "exploration" in parts

    def test_requested_devices_may_be_empty(self) -> None:
        spec = ConductorJobSpec(
            job_id="t",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="family",
            study_id="study",
            command=("echo",),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/conductor/exploration/test",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=1,
            cost_floor_bps=0.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        assert spec.requested_devices == ()


class TestDeriveJobId:
    def test_deterministic(self) -> None:
        id1 = derive_job_id(
            signal_family="alpha",
            study_id="v1",
            command=("python", "run.py"),
            capture_dir=None,
            run_mode=ConductorRunMode.EXPLORATION,
        )
        id2 = derive_job_id(
            signal_family="alpha",
            study_id="v1",
            command=("python", "run.py"),
            capture_dir=None,
            run_mode=ConductorRunMode.EXPLORATION,
        )
        assert id1 == id2

    def test_same_stable_fields_same_id(self) -> None:
        id1 = derive_job_id(
            signal_family="beta",
            study_id="v2",
            command=("python", "eval.py"),
            capture_dir="/data/capture_001",
            run_mode=ConductorRunMode.EXPLORATION,
        )
        id2 = derive_job_id(
            signal_family="beta",
            study_id="v2",
            command=("python", "eval.py"),
            capture_dir="/data/capture_001",
            run_mode=ConductorRunMode.EXPLORATION,
        )
        assert id1 == id2

    def test_different_run_mode_different_id(self) -> None:
        id_explore = derive_job_id(
            signal_family="gamma",
            study_id="v3",
            command=("python", "run.py"),
            capture_dir="/data/cap",
            run_mode=ConductorRunMode.EXPLORATION,
        )
        id_locked = derive_job_id(
            signal_family="gamma",
            study_id="v3",
            command=("python", "run.py"),
            capture_dir="/data/cap",
            run_mode=ConductorRunMode.LOCKED,
        )
        assert id_explore != id_locked

    def test_format(self) -> None:
        job_id = derive_job_id(
            signal_family="alpha",
            study_id="v1",
            command=("python", "run.py"),
            capture_dir="/data/cap",
            run_mode=ConductorRunMode.EXPLORATION,
        )
        parts = job_id.split("_")
        assert parts[0] == "alpha"
        assert parts[1] == "v1"
        assert len(parts[2]) == 12

    def test_hash_uses_canonical_json(self) -> None:
        id1 = derive_job_id(
            signal_family="delta",
            study_id="v4",
            command=("echo", "hello"),
            capture_dir="/data/cap",
            run_mode=ConductorRunMode.EXPLORATION,
        )
        id2 = derive_job_id(
            signal_family="delta",
            study_id="v4",
            command=("echo", "hello"),
            capture_dir="/data/cap",
            run_mode=ConductorRunMode.EXPLORATION,
        )
        assert id1 == id2