"""
Governance Spine — Phase 2.

Provides grid lock, candidate lock, append-only evidence ledger,
approval/revocation/demotion events, and current-state replay.

Key invariants:
- Evidence ledger is append-only. Estimator outputs are never replaced.
- Approval is derived by replaying the ledger, not from trusting a manifest.
- Revocation and kill-switch events have hard precedence over approval.
- Unknown event types cause fail-closed replay.
- A corrupt, missing, or ambiguous ledger is treated as no approval.
"""

from .events import (
    EventType,
    LedgerEvent,
    make_grid_lock_event,
    make_candidate_lock_event,
    make_estimator_evidence_event,
    make_approval_event,
    make_revocation_event,
    make_demotion_event,
    make_supersession_event,
    make_kill_switch_event,
)
from .ledger import EvidenceLedger, LedgerIntegrityError
from .replay import replay_ledger, CandidateState, ReplayResult

__all__ = [
    "EventType",
    "LedgerEvent",
    "make_grid_lock_event",
    "make_candidate_lock_event",
    "make_estimator_evidence_event",
    "make_approval_event",
    "make_revocation_event",
    "make_demotion_event",
    "make_supersession_event",
    "make_kill_switch_event",
    "EvidenceLedger",
    "LedgerIntegrityError",
    "replay_ledger",
    "CandidateState",
    "ReplayResult",
]
