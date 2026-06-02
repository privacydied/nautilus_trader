from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.core.time import (
    milliseconds_to_ns,
    nanoseconds_to_seconds,
    seconds_to_ns,
)


def test_time_helpers_round_trip_obvious_values() -> None:
    assert seconds_to_ns(1.5) == 1_500_000_000
    assert milliseconds_to_ns(1.5) == 1_500_000
    assert nanoseconds_to_seconds(1_500_000_000) == 1.5
