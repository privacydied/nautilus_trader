"""
Emit corpus-evidence, demotion, and revocation events into the Phase 2 ledger.

Phase 4 does not own a state store. It writes events into the Phase 2
evidence ledger. Current candidate state is still derived by Phase 2 replay.
"""

from __future__ import annotations

from venue_agnostic_signal_observer.governance.events import LedgerEvent
from venue_agnostic_signal_observer.governance.events import make_demotion_event
from venue_agnostic_signal_observer.governance.events import make_estimator_evidence_event

from .aggregator import CorpusAggregationResult


def emit_corpus_events(
    result: CorpusAggregationResult,
    ledger_index_start: int,
) -> list[LedgerEvent]:
    """
    Produce ledger events for a corpus aggregation result.

    Returns a list of events to be appended to the Phase 2 evidence ledger.
    The caller is responsible for appending them via EvidenceLedger.append().
    Phase 4 does not own state — it only emits events.
    """
    events: list[LedgerEvent] = []
    idx = ledger_index_start

    # Always emit corpus-evidence event
    events.append(make_estimator_evidence_event(
        ledger_index=idx,
        candidate_hash=result.candidate_hash,
        grid_hash=result.grid_hash,
        estimator_name="corpus_recurrence",
        estimator_version="1.0.0",
        estimator_config_hash="",
        result_summary={
            "corpus_hash": result.corpus_hash,
            "n_captures": result.n_captures,
            "n_positive_captures": result.n_positive_captures,
            "recurrence_rate": result.recurrence_rate,
            "consistency_score": result.consistency_score,
            "aggregate_mean_return": result.aggregate_mean_return,
            "effective_trial_count_per_corpus": result.effective_trial_count_per_corpus,
            "verdict": result.verdict,
        },
    ))
    idx += 1

    # Emit demotion if warranted
    if result.verdict in ("SINGLE_WINDOW_ARTIFACT", "SINGLE_WINDOW_UNVERIFIED", "INCONSISTENT"):
        events.append(make_demotion_event(
            ledger_index=idx,
            candidate_hash=result.candidate_hash,
            grid_hash=result.grid_hash,
            reason=result.demotion_reason or result.verdict,
        ))
        idx += 1

    return events
