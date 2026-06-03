from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.core.direction import (
    UNKNOWN_DIRECTION,
    normalize_direction,
)


def test_direction_helper_normalizes_known_values() -> None:
    assert normalize_direction("buy") == "long"
    assert normalize_direction("SELL") == "short"
    assert normalize_direction(1) == "long"
    assert normalize_direction(-2.5) == "short"


def test_direction_helper_returns_unknown_for_missing_or_ambiguous() -> None:
    assert normalize_direction(None) == UNKNOWN_DIRECTION
    assert normalize_direction(0) == UNKNOWN_DIRECTION
    assert normalize_direction(True) == UNKNOWN_DIRECTION
    assert normalize_direction("") == UNKNOWN_DIRECTION
    assert normalize_direction("flat") == UNKNOWN_DIRECTION
