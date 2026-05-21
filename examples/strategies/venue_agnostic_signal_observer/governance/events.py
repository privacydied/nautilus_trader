"""
Ledger event types and constructors.

All events are immutable once created. The ledger is append-only.
event_time_utc is metadata — it must not be the sole ordering authority.
Ordering is by ledger_index.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    GRID_LOCK = "grid_lock"
    CANDIDATE_LOCK = "candidate_lock"
    ESTIMATOR_EVIDENCE = "estimator_evidence"
    APPROVAL = "approval"
    REVOCATION = "revocation"
    DEMOTION = "demotion"
    SUPERSESSION = "supersession"
    KILL_SWITCH = "kill_switch"
    LEDGER_INTEGRITY = "ledger_integrity"


HIGH_PRECEDENCE_EVENTS = {
    EventType.REVOCATION,
    EventType.KILL_SWITCH,
    EventType.LEDGER_INTEGRITY,
}


@dataclass(frozen=True)
class LedgerEvent:
    ledger_index: int
    event_type: EventType
    event_time_utc: str
    written_at_utc: str
    candidate_hash: str | None
    grid_hash: str | None
    payload: dict[str, Any]
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LedgerEvent:
        return cls(
            ledger_index=d["ledger_index"],
            event_type=EventType(d["event_type"]),
            event_time_utc=d["event_time_utc"],
            written_at_utc=d["written_at_utc"],
            candidate_hash=d.get("candidate_hash"),
            grid_hash=d.get("grid_hash"),
            payload=d.get("payload", {}),
            event_hash=d["event_hash"],
        )


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _event_hash(
    ledger_index: int,
    event_type: str,
    event_time_utc: str,
    candidate_hash: str | None,
    grid_hash: str | None,
    payload: dict[str, Any],
) -> str:
    raw = json.dumps({
        "ledger_index": ledger_index,
        "event_type": event_type,
        "event_time_utc": event_time_utc,
        "candidate_hash": candidate_hash,
        "grid_hash": grid_hash,
        "payload": payload,
    }, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _make_event(
    ledger_index: int,
    event_type: EventType,
    candidate_hash: str | None = None,
    grid_hash: str | None = None,
    payload: dict[str, Any] | None = None,
    event_time_utc: str | None = None,
) -> LedgerEvent:
    now = _now_utc()
    evt_time = event_time_utc or now
    pl = payload or {}
    h = _event_hash(ledger_index, event_type.value, evt_time, candidate_hash, grid_hash, pl)
    return LedgerEvent(
        ledger_index=ledger_index,
        event_type=event_type,
        event_time_utc=evt_time,
        written_at_utc=now,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload=pl,
        event_hash=h,
    )


def make_grid_lock_event(
    ledger_index: int,
    grid_hash: str,
    grid_spec_summary: dict[str, Any] | None = None,
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.GRID_LOCK,
        grid_hash=grid_hash,
        payload={"grid_spec_summary": grid_spec_summary or {}},
    )


def make_candidate_lock_event(
    ledger_index: int,
    candidate_hash: str,
    grid_hash: str,
    candidate_spec: dict[str, Any] | None = None,
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.CANDIDATE_LOCK,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={"candidate_spec": candidate_spec or {}},
    )


def make_estimator_evidence_event(
    ledger_index: int,
    candidate_hash: str,
    grid_hash: str,
    estimator_name: str,
    estimator_version: str,
    estimator_config_hash: str,
    result_summary: dict[str, Any],
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.ESTIMATOR_EVIDENCE,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={
            "estimator_name": estimator_name,
            "estimator_version": estimator_version,
            "estimator_config_hash": estimator_config_hash,
            "result_summary": result_summary,
        },
    )


def make_approval_event(
    ledger_index: int,
    candidate_hash: str,
    grid_hash: str,
    approved_by: str = "governance",
    conditions: dict[str, Any] | None = None,
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.APPROVAL,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={
            "approved_by": approved_by,
            "conditions": conditions or {},
        },
    )


def make_revocation_event(
    ledger_index: int,
    candidate_hash: str,
    grid_hash: str,
    reason: str,
    revoked_by: str = "governance",
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.REVOCATION,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={"reason": reason, "revoked_by": revoked_by},
    )


def make_demotion_event(
    ledger_index: int,
    candidate_hash: str,
    grid_hash: str,
    reason: str,
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.DEMOTION,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={"reason": reason},
    )


def make_supersession_event(
    ledger_index: int,
    candidate_hash: str,
    prior_candidate_hash: str,
    grid_hash: str,
    reason: str,
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.SUPERSESSION,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={
            "prior_candidate_hash": prior_candidate_hash,
            "reason": reason,
        },
    )


def make_kill_switch_event(
    ledger_index: int,
    candidate_hash: str | None,
    grid_hash: str | None,
    reason: str,
    scope: str = "candidate",
) -> LedgerEvent:
    return _make_event(
        ledger_index=ledger_index,
        event_type=EventType.KILL_SWITCH,
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        payload={"reason": reason, "scope": scope},
    )
