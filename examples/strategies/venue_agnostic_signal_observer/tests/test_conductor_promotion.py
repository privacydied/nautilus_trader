"""Tests for conductor promotion logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..conductor.models import (
    ConductorJobSpec,
    ConductorRunMode,
    ConductorSourceKind,
)
from ..conductor.promotion import scan_summary_for_promotions


def _make_exploration_job(
    signal_family: str = "test_family",
    study_id: str = "test_study",
    min_events: int = 10,
    capture_dir: str | None = None,
    output_dir: str = "/tmp/conductor/exploration/test",
) -> ConductorJobSpec:
    return ConductorJobSpec(
        job_id="explore_001",
        source_kind=ConductorSourceKind.EXISTING_CAPTURE,
        signal_family=signal_family,
        study_id=study_id,
        command=("python", "-c", "pass"),
        capture_dir=capture_dir,
        report_dir=None,
        output_dir=output_dir,
        run_mode=ConductorRunMode.EXPLORATION,
        min_events=min_events,
        cost_floor_bps=50.0,
        structural_change_rationale=None,
        requested_devices=(),
        metadata={},
    )


def _write_summary(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


GROUP_POSITIVE = {
    "group_id": "group_a",
    "mean_net_bps": 15.3,
    "valid_count": 25,
    "win_rate": 0.55,
}

GROUP_NEGATIVE = {
    "group_id": "group_b",
    "mean_net_bps": -5.2,
    "valid_count": 30,
    "win_rate": 0.40,
}

GROUP_INSUFFICIENT_EVENTS = {
    "group_id": "group_c",
    "mean_net_bps": 20.0,
    "valid_count": 3,
    "win_rate": 0.60,
}


class TestScanSummaryForPromotions:
    def test_top_level_groups_supported(self, tmp_path: Path) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_POSITIVE]})
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1
        assert candidates[0].mean_net_bps == 15.3

    def test_top_level_evaluated_groups_supported(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"evaluated_groups": [GROUP_POSITIVE]})
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1

    def test_top_level_results_not_supported(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"results": [GROUP_POSITIVE]})
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 0

    def test_positive_mean_sufficient_events_creates_precommitment(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_POSITIVE]})
        job = _make_exploration_job(min_events=10)
        precommit_out = tmp_path / "precommitments"
        candidates = scan_summary_for_promotions(
            job, summary_path, precommit_out
        )
        assert len(candidates) == 1
        assert candidates[0].precommitment_hash
        precommit_file = precommit_out / f"{candidates[0].precommitment_hash}.json"
        assert precommit_file.is_file()

    def test_insufficient_events_does_not_promote(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_INSUFFICIENT_EVENTS]})
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 0

    def test_negative_mean_does_not_promote(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_NEGATIVE]})
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 0

    def test_events_list_not_converted_with_len(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(
            summary_path,
            {
                "groups": [
                    {
                        "group_id": "group_d",
                        "mean_net_bps": 12.0,
                        "events": ["a", "b", "c"],
                        "win_rate": 0.55,
                    }
                ]
            },
        )
        job = _make_exploration_job(min_events=2)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 0

    def test_events_int_fallback_works(self, tmp_path: Path) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(
            summary_path,
            {
                "groups": [
                    {
                        "group_id": "group_e",
                        "mean_net_bps": 12.0,
                        "events": 20,
                        "win_rate": 0.55,
                    }
                ]
            },
        )
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1

    def test_n_fallback_works(self, tmp_path: Path) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(
            summary_path,
            {
                "groups": [
                    {
                        "group_id": "group_f",
                        "mean_net_bps": 12.0,
                        "n": 20,
                        "win_rate": 0.55,
                    }
                ]
            },
        )
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1

    def test_locked_output_path_swaps_first_exploration(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_POSITIVE]})
        job = _make_exploration_job(
            min_events=10,
            capture_dir="/data/capture",
        )
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1
        locked_job = candidates[0].locked_job_spec
        assert "locked" in Path(locked_job.output_dir).parts
        assert "exploration" not in Path(locked_job.output_dir).parts

    def test_locked_path_preserves_later_exploration_in_capture_dir_name(
        self, tmp_path: Path
    ) -> None:
        job = ConductorJobSpec(
            job_id="explore_002",
            source_kind=ConductorSourceKind.EXISTING_CAPTURE,
            signal_family="test_family",
            study_id="test_study",
            command=("python", "-c", "pass"),
            capture_dir=None,
            report_dir=None,
            output_dir="/tmp/conductor/exploration/exploration_run_2026",
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=10,
            cost_floor_bps=50.0,
            structural_change_rationale=None,
            requested_devices=(),
            metadata={},
        )
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_POSITIVE]})
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1
        locked_job = candidates[0].locked_job_spec
        output_path = Path(locked_job.output_dir)
        parts = output_path.parts
        assert "locked" in parts
        assert parts[-1] == "exploration_run_2026"

    def test_precommitment_hash_stable(
        self, tmp_path: Path
    ) -> None:
        summary_data = {"groups": [GROUP_POSITIVE]}
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, summary_data)
        job = _make_exploration_job(min_events=10)

        candidates1 = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        tmp_path2 = tmp_path / "run2"
        summary_path2 = tmp_path2 / "summary.json"
        _write_summary(summary_path2, summary_data)
        candidates2 = scan_summary_for_promotions(
            job, summary_path2, tmp_path2 / "precommitments"
        )

        assert len(candidates1) == 1
        assert len(candidates2) == 1
        assert candidates1[0].precommitment_hash == candidates2[0].precommitment_hash

    def test_precommitment_hash_excludes_timestamp(
        self, tmp_path: Path
    ) -> None:
        summary_path = tmp_path / "summary.json"
        _write_summary(summary_path, {"groups": [GROUP_POSITIVE]})
        job = _make_exploration_job(min_events=10)
        candidates = scan_summary_for_promotions(
            job, summary_path, tmp_path / "precommitments"
        )
        assert len(candidates) == 1
        precommit_file = (
            tmp_path / "precommitments" / f"{candidates[0].precommitment_hash}.json"
        )
        assert precommit_file.is_file()
        data = json.loads(precommit_file.read_text())
        assert "created_at_utc" in data
        assert "git_sha" in data
        assert data["promotion_rule_id"] is not None
        assert data["registry_verdict_authorized"] is False
        assert data["ledger_write_authorized_before_locked_run"] is False