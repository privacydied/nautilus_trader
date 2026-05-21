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

from .events import EventType
from .events import LedgerEvent
from .events import make_approval_event
from .events import make_candidate_lock_event
from .events import make_demotion_event
from .events import make_estimator_evidence_event
from .events import make_grid_lock_event
from .events import make_kill_switch_event
from .events import make_revocation_event
from .events import make_supersession_event
from .ledger import EvidenceLedger
from .ledger import LedgerIntegrityError
from .replay import CandidateState
from .replay import ReplayResult
from .replay import replay_ledger


__all__ = [
    "CandidateState",
    "EventType",
    "EvidenceLedger",
    "LedgerEvent",
    "LedgerIntegrityError",
    "ReplayResult",
    "make_approval_event",
    "make_candidate_lock_event",
    "make_demotion_event",
    "make_estimator_evidence_event",
    "make_grid_lock_event",
    "make_kill_switch_event",
    "make_revocation_event",
    "make_supersession_event",
    "replay_ledger",
]
