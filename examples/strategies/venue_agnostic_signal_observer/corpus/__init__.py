"""
Corpus Recurrence — Phase 4.

Determines whether frozen candidates recur across independent captures
and stress windows. Emits corpus-evidence, demotion, revocation, and
supersession events into the Phase 2 evidence ledger.

Phase 4 does NOT own a separate state store. Current candidate state
is always derived by Phase 2 ledger replay.

Invariants:
- effective_trial_count is per-grid-per-corpus; recomputed when corpus changes.
- Repeated evidence matters more than best single capture.
- Single-window artifacts should be demoted or kept diagnostic-only.
"""

from .aggregator import CaptureRecord
from .aggregator import CorpusAggregationResult
from .aggregator import aggregate_corpus
from .ledger_emitter import emit_corpus_events


__all__ = [
    "CaptureRecord",
    "CorpusAggregationResult",
    "aggregate_corpus",
    "emit_corpus_events",
]
