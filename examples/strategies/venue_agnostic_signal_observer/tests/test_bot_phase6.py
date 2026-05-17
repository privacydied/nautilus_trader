"""
Tests for Phase 6 Trading Bot gate.

Covers:
- No discovery, tuning, or promotion in bot code
- No live execution clients imported
- Manifest alone is not sufficient (ledger replay required)
- Revoked candidate fails gate
- Killed candidate fails gate
- Demoted candidate fails gate
- Missing manifest fails gate
- Expired manifest fails gate
- Missing ledger fails closed
- Corrupt ledger fails closed
- Full authorized path: approved candidate + valid manifest
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest

from ..bot import BotGate, load_manifest
from ..governance import (
    EvidenceLedger,
    make_grid_lock_event,
    make_candidate_lock_event,
    make_approval_event,
    make_revocation_event,
    make_demotion_event,
    make_kill_switch_event,
)


def _make_manifest(tmp_path: Path, candidate_hash: str, expired: bool = False) -> Path:
    from ..bot.manifest import _compute_manifest_hash
    expires = None
    if expired:
        expires = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    record = {
        "candidate_hash": candidate_hash,
        "grid_hash": "g1",
        "rule_description": "test rule",
        "conditions": {},
        "limits": {"max_position_usd": 1000},
        "manifest_hash": "placeholder",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "expires_at_utc": expires,
    }
    # recompute hash
    record["manifest_hash"] = _compute_manifest_hash([record])
    path = tmp_path / "manifest.json"
    with path.open("w") as f:
        json.dump([record], f)
    return path


def _approved_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.jsonl"
    ledger = EvidenceLedger(path)
    ledger.append(make_grid_lock_event(0, "g1"))
    ledger.append(make_candidate_lock_event(1, "c1", "g1"))
    ledger.append(make_approval_event(2, "c1", "g1"))
    return path


class TestBotGateSafety:
    def test_no_discovery_in_gate_source(self):
        import inspect
        from ..bot import gate as mod
        src = inspect.getsource(mod)
        # Gate must not call discovery/miner functions or use live-trading imports
        functional_forbidden = ["scan_grid", "run_sweep", "compute_dsr",
                                "private_key", "api_key", "nautilus_trader"]
        for f in functional_forbidden:
            assert f not in src, f"gate.py must not contain '{f}'"

    def test_no_live_execution_in_manifest_source(self):
        import inspect
        from ..bot import manifest as mod
        src = inspect.getsource(mod)
        assert "nautilus_trader" not in src
        assert "submit_order" not in src

    def test_manifest_alone_insufficient(self, tmp_path):
        manifest_path = _make_manifest(tmp_path, "c1")
        # No ledger at all
        ledger_path = tmp_path / "ledger.jsonl"
        gate = BotGate(ledger_path, manifest_path)
        result = gate.authorize("c1")
        # Missing ledger → empty ledger (success=True but c1 not approved)
        assert not result.authorized

    def test_full_authorized_path(self, tmp_path):
        ledger_path = _approved_ledger(tmp_path)
        manifest_path = _make_manifest(tmp_path, "c1")
        gate = BotGate(ledger_path, manifest_path)
        result = gate.authorize("c1")
        assert result.authorized
        assert result.ledger_replay_success
        assert result.candidate_state_tradeable

    def test_revoked_candidate_fails(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        ledger = EvidenceLedger(path)
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))
        ledger.append(make_approval_event(2, "c1", "g1"))
        ledger.append(make_revocation_event(3, "c1", "g1", reason="corpus contradiction"))
        manifest_path = _make_manifest(tmp_path, "c1")
        gate = BotGate(path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized
        assert "revoked" in result.fail_reason.lower()

    def test_killed_candidate_fails(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        ledger = EvidenceLedger(path)
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))
        ledger.append(make_approval_event(2, "c1", "g1"))
        ledger.append(make_kill_switch_event(3, "c1", "g1", reason="emergency"))
        manifest_path = _make_manifest(tmp_path, "c1")
        gate = BotGate(path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized
        assert "kill" in result.fail_reason.lower()

    def test_demoted_candidate_fails(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        ledger = EvidenceLedger(path)
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))
        ledger.append(make_approval_event(2, "c1", "g1"))
        ledger.append(make_demotion_event(3, "c1", "g1", reason="single-window artifact"))
        manifest_path = _make_manifest(tmp_path, "c1")
        gate = BotGate(path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized

    def test_corrupt_ledger_fails_closed(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        with path.open("w") as f:
            f.write("garbage\n")
        manifest_path = _make_manifest(tmp_path, "c1")
        gate = BotGate(path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized
        assert not result.ledger_replay_success

    def test_missing_manifest_fails(self, tmp_path):
        ledger_path = _approved_ledger(tmp_path)
        manifest_path = tmp_path / "nonexistent_manifest.json"
        gate = BotGate(ledger_path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized
        assert "Manifest file not found" in result.fail_reason

    def test_expired_manifest_fails(self, tmp_path):
        ledger_path = _approved_ledger(tmp_path)
        manifest_path = _make_manifest(tmp_path, "c1", expired=True)
        gate = BotGate(ledger_path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized
        assert "expired" in result.fail_reason.lower()

    def test_unapproved_candidate_fails_even_with_manifest(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        ledger = EvidenceLedger(path)
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))
        # No approval event
        manifest_path = _make_manifest(tmp_path, "c1")
        gate = BotGate(path, manifest_path)
        result = gate.authorize("c1")
        assert not result.authorized
        assert not result.candidate_state_tradeable
