"""
Tests for Phase 4 Corpus Recurrence.

Covers:
- Recurrence rate computation
- Single-window artifact demotion
- Recurring candidate verdict
- effective_trial_count recomputed per-corpus
- Ledger event emission (corpus-evidence, demotion)
- Phase 4 emits into Phase 2 ledger (no parallel state store)
"""

from __future__ import annotations

from venue_agnostic_signal_observer.corpus import CaptureRecord
from venue_agnostic_signal_observer.corpus import aggregate_corpus
from venue_agnostic_signal_observer.corpus import emit_corpus_events
from venue_agnostic_signal_observer.governance import EvidenceLedger
from venue_agnostic_signal_observer.governance import replay_ledger


def _cap(capture_id, mean_return, n=50, returns=None):
    if returns is None:
        returns = [mean_return] * n
    return CaptureRecord(
        capture_id=capture_id,
        candidate_hash="c1",
        grid_hash="g1",
        corpus_hash="corpus1",
        mean_return=mean_return,
        hit_rate=0.6 if mean_return > 0 else 0.4,
        sharpe_like=mean_return / 0.001 if mean_return else 0.0,
        n_observations=n,
        return_series=returns,
    )


class TestCorpusAggregation:
    def test_recurring_candidate(self):
        captures = [_cap(f"cap{i}", 0.002) for i in range(5)]
        result = aggregate_corpus(captures)
        assert result.verdict == "RECURRING_CANDIDATE"
        assert result.recurrence_rate == 1.0
        assert result.n_captures == 5

    def test_single_window_artifact_low_recurrence(self):
        captures = [
            _cap("cap1", 0.003),
            _cap("cap2", -0.001),
            _cap("cap3", -0.002),
            _cap("cap4", -0.001),
        ]
        result = aggregate_corpus(captures, demotion_threshold_recurrence=0.4)
        assert result.verdict == "SINGLE_WINDOW_ARTIFACT"
        assert result.demotion_reason is not None

    def test_single_window_unverified(self):
        captures = [_cap("cap1", 0.002)]
        result = aggregate_corpus(captures)
        assert result.verdict == "SINGLE_WINDOW_UNVERIFIED"
        assert result.demotion_reason is not None

    def test_no_captures(self):
        result = aggregate_corpus([])
        assert result.verdict == "NO_DATA"

    def test_effective_trial_count_computed_per_corpus(self):
        # Identical return series → 1 cluster
        series = [0.001, -0.001, 0.002, -0.002, 0.001]
        captures = [
            CaptureRecord("cap1", "c1", "g1", "corpus1", 0.001, 0.6, 1.0, 5, return_series=series),
            CaptureRecord("cap2", "c1", "g1", "corpus1", 0.001, 0.6, 1.0, 5, return_series=series),
        ]
        result = aggregate_corpus(captures)
        # Identical series → effective_trial_count should be 1
        assert result.effective_trial_count_per_corpus == 1

    def test_consistency_score_present(self):
        captures = [_cap(f"cap{i}", 0.002 + i * 0.0001) for i in range(4)]
        result = aggregate_corpus(captures)
        assert result.consistency_score is not None
        assert 0.0 <= result.consistency_score <= 1.0


class TestLedgerEmitter:
    def test_emits_corpus_evidence_event(self):
        captures = [_cap(f"cap{i}", 0.002) for i in range(3)]
        result = aggregate_corpus(captures)
        events = emit_corpus_events(result, ledger_index_start=10)
        types = [e.event_type.value for e in events]
        assert "estimator_evidence" in types

    def test_single_window_also_emits_demotion(self):
        captures = [_cap("cap1", 0.002)]
        result = aggregate_corpus(captures)
        events = emit_corpus_events(result, ledger_index_start=0)
        types = [e.event_type.value for e in events]
        assert "demotion" in types

    def test_recurring_candidate_no_demotion_event(self):
        captures = [_cap(f"cap{i}", 0.003) for i in range(5)]
        result = aggregate_corpus(captures)
        events = emit_corpus_events(result, ledger_index_start=0)
        types = [e.event_type.value for e in events]
        assert "demotion" not in types

    def test_events_can_be_appended_to_phase2_ledger(self, tmp_path):
        """Phase 4 events feed into Phase 2 ledger — no parallel state store."""
        from venue_agnostic_signal_observer.governance.events import make_candidate_lock_event
        from venue_agnostic_signal_observer.governance.events import make_grid_lock_event
        ledger = EvidenceLedger(tmp_path / "ledger.jsonl")
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))

        captures = [_cap(f"cap{i}", 0.002) for i in range(3)]
        result = aggregate_corpus(captures)
        events = emit_corpus_events(result, ledger_index_start=2)
        for evt in events:
            ledger.append(evt)

        replay = replay_ledger(ledger)
        assert replay.success
        state = replay.get_state("c1")
        assert state is not None
        # Evidence from corpus recurrence should be recorded
        assert any(
            e.get("estimator_name") == "corpus_recurrence"
            for e in state.evidence_events
        )

    def test_demotion_event_visible_in_replay(self, tmp_path):
        """Demotion from Phase 4 is reflected in Phase 2 replay state."""
        from venue_agnostic_signal_observer.governance.events import make_approval_event
        from venue_agnostic_signal_observer.governance.events import make_candidate_lock_event
        from venue_agnostic_signal_observer.governance.events import make_grid_lock_event
        ledger = EvidenceLedger(tmp_path / "ledger.jsonl")
        ledger.append(make_grid_lock_event(0, "g1"))
        ledger.append(make_candidate_lock_event(1, "c1", "g1"))
        ledger.append(make_approval_event(2, "c1", "g1"))

        captures = [_cap("cap1", 0.002)]  # single window → demotion
        result = aggregate_corpus(captures)
        events = emit_corpus_events(result, ledger_index_start=3)
        for evt in events:
            ledger.append(evt)

        replay = replay_ledger(ledger)
        assert replay.success
        state = replay.get_state("c1")
        assert state.is_demoted
        assert not state.is_tradeable
