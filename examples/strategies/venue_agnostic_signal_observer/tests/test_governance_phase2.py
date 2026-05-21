"""
Tests for Phase 2 Governance Spine.

Covers:
- Append-only ledger: events write, sequence enforced
- Hash integrity: corrupt line detected
- Fail-closed replay on corrupt/missing/unknown-event ledger
- Approval → revocation precedence (hard precedence)
- Kill-switch overrides approval
- Supersession
- Demotion
- Estimator evidence accumulation (append, not replace)
- Unknown event type causes fail-closed
- is_tradeable semantics
- Missing ledger returns fail-closed (not exception to caller)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ..governance import (
    EvidenceLedger,
    LedgerIntegrityError,
    make_grid_lock_event,
    make_candidate_lock_event,
    make_estimator_evidence_event,
    make_approval_event,
    make_revocation_event,
    make_demotion_event,
    make_supersession_event,
    make_kill_switch_event,
    replay_ledger,
)


def _fresh_ledger(tmp_path: Path) -> EvidenceLedger:
    return EvidenceLedger(tmp_path / "test_ledger.jsonl")


# ---------------------------------------------------------------------------
# Append-only ledger basics
# ---------------------------------------------------------------------------

class TestLedgerAppend:
    def test_append_and_read(self, tmp_path):
        ledger = _fresh_ledger(tmp_path)
        evt = make_grid_lock_event(0, "grid_abc123")
        ledger.append(evt)
        events = ledger.read_all()
        assert len(events) == 1
        assert events[0].grid_hash == "grid_abc123"

    def test_monotone_index_enforced(self, tmp_path):
        ledger = _fresh_ledger(tmp_path)
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))
        with pytest.raises(LedgerIntegrityError):
            ledger.append(make_grid_lock_event(1, "g2"))  # duplicate index

    def test_missing_ledger_replay_fails_closed(self, tmp_path):
        ledger = EvidenceLedger(tmp_path / "nonexistent.jsonl")
        result = replay_ledger(ledger)
        # Missing ledger → fail-closed with an error message
        assert not result.success
        assert result.events_processed == 0
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_corrupt_jsonl_fails_closed(self, tmp_path):
        ledger = _fresh_ledger(tmp_path)
        ledger.append(make_grid_lock_event(0, "g1"))
        # Corrupt the file
        with ledger.path.open("a") as f:
            f.write("not-valid-json\n")
        result = replay_ledger(ledger)
        assert not result.success
        assert "Corrupt" in (result.error_message or "")

    def test_hash_mismatch_fails_closed(self, tmp_path):
        ledger = _fresh_ledger(tmp_path)
        ledger.append(make_grid_lock_event(0, "g1"))
        # Tamper with the hash
        with ledger.path.open("r") as f:
            lines = f.readlines()
        tampered = json.loads(lines[0])
        tampered["event_hash"] = "deadbeefdeadbeef000000"
        with ledger.path.open("w") as f:
            f.write(json.dumps(tampered) + "\n")
        result = replay_ledger(ledger)
        assert not result.success
        assert "Hash mismatch" in (result.error_message or "")

    def test_unknown_event_type_fails_closed(self, tmp_path):
        ledger = _fresh_ledger(tmp_path)
        ledger.append(make_grid_lock_event(0, "g1"))
        # Inject unknown event type (bypass hash so it parses)
        with ledger.path.open("r") as f:
            lines = f.readlines()
        d = json.loads(lines[0])
        d["event_type"] = "alien_event_type_xyz"
        # Recompute hash to pass integrity check
        from ..governance.events import _event_hash
        d["event_hash"] = _event_hash(
            d["ledger_index"], d["event_type"], d["event_time_utc"],
            d.get("candidate_hash"), d.get("grid_hash"), d.get("payload", {}),
        )
        with ledger.path.open("w") as f:
            f.write(json.dumps(d) + "\n")
        result = replay_ledger(ledger)
        assert not result.success
        assert result.error_message is not None  # fail-closed; message varies by parsing path


# ---------------------------------------------------------------------------
# Approval / revocation / hard precedence
# ---------------------------------------------------------------------------

class TestReplaySemantics:
    def _build_ledger(self, tmp_path, events):
        ledger = _fresh_ledger(tmp_path)
        for e in events:
            ledger.append(e)
        return ledger

    def test_approval_grants_tradeable(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_approval_event(2, "c1", "g1"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert result.is_approved("c1")

    def test_revocation_overrides_approval(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_approval_event(2, "c1", "g1"),
            make_revocation_event(3, "c1", "g1", reason="corpus contradiction"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert not result.is_approved("c1")
        state = result.get_state("c1")
        assert state.is_revoked
        assert "corpus" in state.revocation_reason

    def test_revoked_cannot_be_re_approved(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_approval_event(2, "c1", "g1"),
            make_revocation_event(3, "c1", "g1", reason="bad"),
            make_approval_event(4, "c1", "g1"),  # attempted re-approval
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert not result.is_approved("c1")

    def test_kill_switch_overrides_approval(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_approval_event(2, "c1", "g1"),
            make_kill_switch_event(3, "c1", "g1", reason="emergency halt"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert not result.is_approved("c1")
        assert result.get_state("c1").is_killed

    def test_demotion_prevents_tradeable(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_approval_event(2, "c1", "g1"),
            make_demotion_event(3, "c1", "g1", reason="single-window artifact"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert not result.is_approved("c1")
        assert result.get_state("c1").is_demoted

    def test_supersession_marks_prior_not_tradeable(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_approval_event(2, "c1", "g1"),
            make_candidate_lock_event(3, "c2", "g1"),
            make_supersession_event(4, "c2", prior_candidate_hash="c1", grid_hash="g1",
                                    reason="c2 supersedes c1"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert not result.is_approved("c1")
        assert result.get_state("c1").is_superseded
        assert result.get_state("c1").superseded_by == "c2"

    def test_estimator_evidence_accumulates(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
            make_estimator_evidence_event(2, "c1", "g1", "dsr", "1.0.0", "abc", {"status": "PASS"}),
            make_estimator_evidence_event(3, "c1", "g1", "dsr", "1.1.0", "def", {"status": "PASS"}),
        ])
        result = replay_ledger(ledger)
        assert result.success
        state = result.get_state("c1")
        assert state is not None
        # Both evidence events must be present (append-only, not replaced)
        assert len(state.evidence_events) == 2
        versions = [e["estimator_version"] for e in state.evidence_events]
        assert "1.0.0" in versions
        assert "1.1.0" in versions

    def test_unapproved_candidate_not_tradeable(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_candidate_lock_event(1, "c1", "g1"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert not result.is_approved("c1")

    def test_replay_is_failed_closed_not_exception(self, tmp_path):
        ledger = _fresh_ledger(tmp_path)
        with ledger.path.open("w") as f:
            f.write("garbage\n")
        result = replay_ledger(ledger)
        assert not result.success
        assert result.error_message is not None

    def test_grid_lock_tracked(self, tmp_path):
        ledger = self._build_ledger(tmp_path, [
            make_grid_lock_event(0, "g1"),
            make_grid_lock_event(1, "g2"),
        ])
        result = replay_ledger(ledger)
        assert result.success
        assert "g1" in result.grid_locks
        assert "g2" in result.grid_locks
