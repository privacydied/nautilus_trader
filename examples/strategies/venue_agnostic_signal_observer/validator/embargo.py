"""
Purging and time-domain embargo.

Embargo is wall-clock-time based, never event-index based. Event-index
embargo is forbidden because event density varies: a fixed count
under-embargoes dense stress windows and over-embargoes sparse quiet ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Sequence


@dataclass(frozen=True)
class TimeInterval:
    """A closed label interval [start, end] in UTC."""

    start: datetime
    end: datetime

    def overlaps(self, other: TimeInterval) -> bool:
        return self.start <= other.end and self.end >= other.start

    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True)
class TimestampedObservation:
    """An observation with an explicit label interval."""

    idx: int
    event_time: datetime
    label_interval: TimeInterval
    value: float


def purge_train_obs(
    train_obs: Sequence[TimestampedObservation],
    test_interval: TimeInterval,
) -> tuple[list[TimestampedObservation], int]:
    """
    Remove train observations whose label interval overlaps the test interval.

    Returns (surviving_obs, purged_count).
    """
    surviving = []
    purged = 0
    for obs in train_obs:
        if obs.label_interval.overlaps(test_interval):
            purged += 1
        else:
            surviving.append(obs)
    return surviving, purged


def embargo_train_obs(
    train_obs: Sequence[TimestampedObservation],
    test_interval: TimeInterval,
    embargo_seconds: float,
) -> tuple[list[TimestampedObservation], int]:
    """
    Remove train observations within embargo_seconds after test_interval.end.

    Embargo is wall-clock based. Observations whose event_time falls in
    (test_interval.end, test_interval.end + embargo_seconds] are removed.

    Returns (surviving_obs, embargoed_count).
    """
    if embargo_seconds <= 0:
        return list(train_obs), 0

    embargo_end = test_interval.end + timedelta(seconds=embargo_seconds)
    surviving = []
    embargoed = 0
    for obs in train_obs:
        if test_interval.end < obs.event_time <= embargo_end:
            embargoed += 1
        else:
            surviving.append(obs)
    return surviving, embargoed


def compute_embargo_seconds(
    label_horizon_seconds: float,
    entry_delay_seconds: float = 0.0,
    staleness_buffer_seconds: float = 0.0,
) -> float:
    """Derive wall-clock embargo duration from label horizon and delays."""
    return label_horizon_seconds + entry_delay_seconds + staleness_buffer_seconds
