"""
Append-only evidence ledger backed by JSONL.

Invariants:
- Events are only appended, never modified or deleted.
- Each event has a monotonically increasing ledger_index.
- Each event carries an event_hash for integrity checking.
- A corrupt, missing, or ambiguous ledger causes fail-closed behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .events import LedgerEvent, EventType


class LedgerIntegrityError(Exception):
    """Raised when the ledger cannot be safely read or replayed."""


class EvidenceLedger:
    """Append-only JSONL evidence ledger."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: LedgerEvent) -> None:
        """Append one event to the ledger. Verifies monotone ledger_index."""
        existing = list(self._raw_iter())
        if existing:
            last_index = existing[-1].ledger_index
            if event.ledger_index <= last_index:
                raise LedgerIntegrityError(
                    f"New event ledger_index {event.ledger_index} is not "
                    f"greater than last index {last_index}"
                )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), default=str) + "\n")

    def read_all(self) -> list[LedgerEvent]:
        """Read and validate all events. Raises LedgerIntegrityError on any anomaly."""
        return list(self._validated_iter())

    def _raw_iter(self) -> Iterator[LedgerEvent]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerIntegrityError(
                        f"Corrupt JSONL at line {lineno}: {exc}"
                    ) from exc
                try:
                    yield LedgerEvent.from_dict(d)
                except (KeyError, ValueError) as exc:
                    raise LedgerIntegrityError(
                        f"Invalid event at line {lineno}: {exc}"
                    ) from exc

    def _validated_iter(self) -> Iterator[LedgerEvent]:
        prev_index: int | None = None
        for event in self._raw_iter():
            # Verify monotone sequence
            if prev_index is not None and event.ledger_index <= prev_index:
                raise LedgerIntegrityError(
                    f"Non-monotone ledger_index: {event.ledger_index} after {prev_index}"
                )
            # Verify event_hash
            from .events import _event_hash
            expected = _event_hash(
                event.ledger_index,
                event.event_type.value,
                event.event_time_utc,
                event.candidate_hash,
                event.grid_hash,
                event.payload,
            )
            if event.event_hash != expected:
                raise LedgerIntegrityError(
                    f"Hash mismatch at ledger_index {event.ledger_index}: "
                    f"stored={event.event_hash} expected={expected}"
                )
            prev_index = event.ledger_index
            yield event

    def next_index(self) -> int:
        """Return the next available ledger_index (0 if ledger is empty)."""
        events = list(self._raw_iter())
        if not events:
            return 0
        return events[-1].ledger_index + 1
