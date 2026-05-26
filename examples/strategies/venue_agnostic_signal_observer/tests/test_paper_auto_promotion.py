"""Tests for auto-promotion orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..conductor.atomic_io import append_jsonl_durable, write_json_atomic
from ..paper.auto_promotion import evaluate_promotion
from ..paper.gate_verifier import FrozenGateDecision
from ..paper.models import PaperExecutionMode, PaperStrategyState
from ..paper.registry import load_strategy, save_strategy


def _setup_frozen_precommitment(
    precommitment_dir: Path,
    hash_val: str = "abc123",
) -> None:
    """Create a fully frozen precommitment file."""
    write_json_atomic(
        precommitment_dir / f"{hash_val}.json",
        {
            "schema_version": "1",
            "promotion_rule_id": "v0",
            "ledger_write_authorized_before_locked_run": True,
            "registry_verdict_authorized": True,
            "group_id": "group_a",
            "group_payload": {
                "mean_net_bps": 15.0,
                "valid_count": 50,
                "win_rate": 0.55,
            },
            "source_job_spec": {
                "signal_family": "test_family",
                "study_id": "test_study",
                "command": ["python", "-c", "pass"],
                "capture_dir": "/data/cap",
            },
            "cost_floor_bps": 50.0,
            "min_events": 50,
        },
    )


def _setup_evidence_ledger(
    ledger_path: Path,
    hash_val: str = "abc123",
) -> None:
    append_jsonl_durable(
        ledger_path,
        {
            "event_type": "CONDUCTOR_LOCKED_RUN_COMPLETED",
            "precommitment_hash": hash_val,
            "job_id": "test",
            "result_status": "COMPLETED",
        },
    )


def _setup_artifacts(artifacts_dir: Path) -> None:
    write_json_atomic(
        artifacts_dir / "summary.json",
        {"groups": [{"group_id": "group_a", "mean_net_bps": 15.0, "valid_count": 50}]},
    )
    write_json_atomic(
        artifacts_dir / "conductor_result.json",
        {"job_id": "test", "status": "COMPLETED"},
    )


class TestEvaluatePromotion:
    def test_full_promotion_flow(self, tmp_path: Path) -> None:
        precommitment_dir = tmp_path / "precommitments"
        precommitment_dir.mkdir()
        _setup_frozen_precommitment(precommitment_dir)

        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        paper_ledger = tmp_path / "paper_events.jsonl"
        evidence_ledger = tmp_path / "evidence_ledger.jsonl"
        _setup_evidence_ledger(evidence_ledger)
        _setup_artifacts(tmp_path)

        decision = evaluate_promotion(
            precommitment_hash="abc123",
            precommitment_dir=precommitment_dir,
            registry_dir=registry_dir,
            paper_events_ledger_path=paper_ledger,
            artifacts_base_dir=tmp_path,
            evidence_ledger_path=evidence_ledger,
        )
        assert decision.allowed
        assert decision.reason == "ALL_GATES_PASSED"
        assert decision.strategy_spec is not None
        assert decision.strategy_spec.state == PaperStrategyState.ENABLED
        assert decision.event_hash is not None

        # Verify strategy was saved
        loaded = load_strategy("paper_abc123", registry_dir)
        assert loaded is not None
        assert loaded.execution_mode == PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED

        # Verify ledger event was written
        assert paper_ledger.is_file()
        events = paper_ledger.read_text().strip().split("\n")
        assert any("PAPER_STRATEGY_PROMOTED" in line for line in events)

    def test_frozen_fails_blocked(self, tmp_path: Path) -> None:
        # Precommitment without frozen gates
        precommitment_dir = tmp_path / "precommitments"
        precommitment_dir.mkdir()
        write_json_atomic(
            precommitment_dir / "abc123.json",
            {
                "ledger_write_authorized_before_locked_run": False,
                "registry_verdict_authorized": False,
            },
        )

        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        paper_ledger = tmp_path / "paper_events.jsonl"
        evidence_ledger = tmp_path / "evidence_ledger.jsonl"

        decision = evaluate_promotion(
            precommitment_hash="abc123",
            precommitment_dir=precommitment_dir,
            registry_dir=registry_dir,
            paper_events_ledger_path=paper_ledger,
            artifacts_base_dir=tmp_path,
            evidence_ledger_path=evidence_ledger,
        )
        assert not decision.allowed
        assert "FROZEN_GATE_BLOCKED" in decision.reason
        assert paper_ledger.is_file()
        events = paper_ledger.read_text()
        assert "AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH" in events

    def test_already_enabled_skip(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        precommitment_dir = tmp_path / "precommitments"
        precommitment_dir.mkdir()
        _setup_frozen_precommitment(precommitment_dir)

        # Save already ENABLED strategy
        from ..paper.models import PaperStrategySpec

        existing = PaperStrategySpec(
            strategy_id="paper_abc123",
            signal_family="test",
            study_id="test",
            precommitment_hash="abc123",
            precommitment_path=precommitment_dir / "abc123.json",
            promotion_rule_id="v0",
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
            artifacts_dir=tmp_path,
            output_dir=tmp_path,
            promoter_verdict="PROMOTED",
            promoted_at_utc="2026-01-01T00:00:00",
            last_refalsified_utc=None,
            refalsification_status=None,
            state=PaperStrategyState.ENABLED,
            metadata={},
        )
        save_strategy(existing, registry_dir)

        decision = evaluate_promotion(
            precommitment_hash="abc123",
            precommitment_dir=precommitment_dir,
            registry_dir=registry_dir,
            paper_events_ledger_path=tmp_path / "paper_events.jsonl",
            artifacts_base_dir=tmp_path,
            evidence_ledger_path=tmp_path / "evidence_ledger.jsonl",
        )
        assert not decision.allowed
        assert "STRATEGY_ALREADY_ENABLED" in decision.reason

    def test_no_registry_writes_in_dry_run(self, tmp_path: Path) -> None:
        # Dry-run isn't directly supported inside evaluate_promotion
        # This tests that no registry writes happen if gate 1 fails
        precommitment_dir = tmp_path / "precommitments"
        precommitment_dir.mkdir()
        _setup_frozen_precommitment(precommitment_dir)

        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        paper_ledger = tmp_path / "paper_events.jsonl"

        # No evidence ledger means gate 1 will fail
        decision = evaluate_promotion(
            precommitment_hash="abc123",
            precommitment_dir=precommitment_dir,
            registry_dir=registry_dir,
            paper_events_ledger_path=paper_ledger,
            artifacts_base_dir=tmp_path,
            evidence_ledger_path=tmp_path / "evidence_ledger.jsonl",
        )
        assert not decision.allowed
        assert not (registry_dir / "paper_abc123.json").is_file()

    def test_event_hash_verifies(self, tmp_path: Path) -> None:
        from ..conductor.atomic_io import sha256_canonical_json

        precommitment_dir = tmp_path / "precommitments"
        precommitment_dir.mkdir()
        _setup_frozen_precommitment(precommitment_dir)

        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        paper_ledger = tmp_path / "paper_events.jsonl"
        evidence_ledger = tmp_path / "evidence_ledger.jsonl"
        _setup_evidence_ledger(evidence_ledger)
        _setup_artifacts(tmp_path)

        decision = evaluate_promotion(
            precommitment_hash="abc123",
            precommitment_dir=precommitment_dir,
            registry_dir=registry_dir,
            paper_events_ledger_path=paper_ledger,
            artifacts_base_dir=tmp_path,
            evidence_ledger_path=evidence_ledger,
        )
        assert decision.event_hash is not None
        # Read back the event
        line = paper_ledger.read_text().strip().split("\n")[-1]
        event = json.loads(line)
        payload_without_hash = {
            k: v for k, v in event.items() if k != "event_hash"
        }
        expected = sha256_canonical_json(payload_without_hash)
        assert event["event_hash"] == expected