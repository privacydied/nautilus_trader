"""
Ledger replay — derives current candidate state from the event log.

Replay rules (from the plan):
1. Events are ordered by ledger_index, not wall-clock timestamp.
2. Later ledger_index wins unless event precedence says otherwise.
3. REVOCATION, KILL_SWITCH, LEDGER_INTEGRITY have hard precedence over APPROVAL.
4. SUPERSESSION replaces prior approval only when it references the prior candidate_hash.
5. Unknown event types cause fail-closed replay.
6. Changed estimator versions produce new evidence events; do not replace old ones.

Corrupt/missing/truncated ledger → fail closed.
The bot must derive permission by replaying; a manifest file is not sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from .events import EventType
from .ledger import EvidenceLedger
from .ledger import LedgerIntegrityError


@dataclass
class CandidateState:
    candidate_hash: str
    grid_hash: str | None
    is_approved: bool = False
    is_revoked: bool = False
    is_demoted: bool = False
    is_killed: bool = False
    is_superseded: bool = False
    superseded_by: str | None = None
    evidence_events: list[dict[str, Any]] = field(default_factory=list)
    approval_conditions: dict[str, Any] = field(default_factory=dict)
    revocation_reason: str | None = None
    demotion_reason: str | None = None

    @property
    def is_tradeable(self) -> bool:
        """True only if approved and no negative event has occurred."""
        return (
            self.is_approved
            and not self.is_revoked
            and not self.is_killed
            and not self.is_superseded
            and not self.is_demoted
        )


@dataclass
class ReplayResult:
    success: bool
    candidate_states: dict[str, CandidateState]
    grid_locks: list[str]
    events_processed: int
    error_message: str | None = None

    def get_state(self, candidate_hash: str) -> CandidateState | None:
        return self.candidate_states.get(candidate_hash)

    def is_approved(self, candidate_hash: str) -> bool:
        """Safe check: returns False if replay failed or candidate not found."""
        if not self.success:
            return False
        state = self.candidate_states.get(candidate_hash)
        if state is None:
            return False
        return state.is_tradeable


def replay_ledger(ledger: EvidenceLedger) -> ReplayResult:
    """
    Replay the evidence ledger to derive current candidate states.

    Fails closed on any integrity error, unknown event type, or missing ledger.
    """
    if not ledger.path.exists():
        return ReplayResult(
            success=False,
            candidate_states={},
            grid_locks=[],
            events_processed=0,
            error_message=f"Ledger file not found: {ledger.path}",
        )

    try:
        events = ledger.read_all()
    except LedgerIntegrityError as exc:
        return ReplayResult(
            success=False,
            candidate_states={},
            grid_locks=[],
            events_processed=0,
            error_message=f"Ledger integrity failure: {exc}",
        )
    except Exception as exc:
        return ReplayResult(
            success=False,
            candidate_states={},
            grid_locks=[],
            events_processed=0,
            error_message=f"Unexpected ledger read error: {exc}",
        )

    known_event_types = set(EventType)
    candidate_states: dict[str, CandidateState] = {}
    grid_locks: list[str] = []
    events_processed = 0

    for event in events:
        events_processed += 1

        # Unknown event type → fail closed
        if event.event_type not in known_event_types:
            return ReplayResult(
                success=False,
                candidate_states={},
                grid_locks=[],
                events_processed=events_processed,
                error_message=f"Unknown event type '{event.event_type}' at index {event.ledger_index}",
            )

        if event.event_type == EventType.GRID_LOCK:
            if event.grid_hash and event.grid_hash not in grid_locks:
                grid_locks.append(event.grid_hash)

        elif event.event_type == EventType.CANDIDATE_LOCK:
            chash = event.candidate_hash
            if chash and chash not in candidate_states:
                candidate_states[chash] = CandidateState(
                    candidate_hash=chash,
                    grid_hash=event.grid_hash,
                )

        elif event.event_type == EventType.ESTIMATOR_EVIDENCE:
            chash = event.candidate_hash
            if chash:
                if chash not in candidate_states:
                    candidate_states[chash] = CandidateState(
                        candidate_hash=chash,
                        grid_hash=event.grid_hash,
                    )
                candidate_states[chash].evidence_events.append({
                    "ledger_index": event.ledger_index,
                    "estimator_name": event.payload.get("estimator_name"),
                    "estimator_version": event.payload.get("estimator_version"),
                    "estimator_config_hash": event.payload.get("estimator_config_hash"),
                })

        elif event.event_type == EventType.APPROVAL:
            chash = event.candidate_hash
            if chash:
                if chash not in candidate_states:
                    candidate_states[chash] = CandidateState(
                        candidate_hash=chash,
                        grid_hash=event.grid_hash,
                    )
                state = candidate_states[chash]
                # Hard precedence: revoked/killed candidates cannot be re-approved
                if not state.is_revoked and not state.is_killed:
                    state.is_approved = True
                    state.approval_conditions = event.payload.get("conditions", {})

        elif event.event_type in (EventType.REVOCATION, EventType.KILL_SWITCH):
            # Hard precedence — overrides approval regardless of timestamp
            chash = event.candidate_hash
            if chash:
                if chash not in candidate_states:
                    candidate_states[chash] = CandidateState(
                        candidate_hash=chash,
                        grid_hash=event.grid_hash,
                    )
                state = candidate_states[chash]
                state.is_approved = False
                if event.event_type == EventType.REVOCATION:
                    state.is_revoked = True
                    state.revocation_reason = event.payload.get("reason")
                else:
                    state.is_killed = True
            elif event.payload.get("scope") == "grid" and event.grid_hash:
                # Kill all candidates associated with the grid
                for state in candidate_states.values():
                    if state.grid_hash == event.grid_hash:
                        state.is_killed = True
                        state.is_approved = False

        elif event.event_type == EventType.DEMOTION:
            chash = event.candidate_hash
            if chash and chash in candidate_states:
                candidate_states[chash].is_demoted = True
                candidate_states[chash].demotion_reason = event.payload.get("reason")

        elif event.event_type == EventType.SUPERSESSION:
            chash = event.candidate_hash
            prior = event.payload.get("prior_candidate_hash")
            if chash and prior and prior in candidate_states:
                candidate_states[prior].is_superseded = True
                candidate_states[prior].superseded_by = chash
                candidate_states[prior].is_approved = False

        elif event.event_type == EventType.LEDGER_INTEGRITY:
            # Fail closed on any ledger integrity signal
            return ReplayResult(
                success=False,
                candidate_states={},
                grid_locks=[],
                events_processed=events_processed,
                error_message=f"Ledger integrity event at index {event.ledger_index}: "
                              f"{event.payload.get('reason', 'unknown')}",
            )

    return ReplayResult(
        success=True,
        candidate_states=candidate_states,
        grid_locks=grid_locks,
        events_processed=events_processed,
    )
