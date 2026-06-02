"""Tests for the shared signal-direction normalization utility spine."""

from __future__ import annotations

import sys

import pytest

from examples.strategies.venue_agnostic_signal_observer.core.direction import (
    normalize_direction as base_normalize_direction,
)
from examples.strategies.venue_agnostic_signal_observer.core.signal_direction import (
    SignalDirection,
    normalize_signal_direction,
    require_signal_direction,
    signal_direction_sign,
)


class TestSignalDirection:
    def test_long_value(self) -> None:
        assert SignalDirection.LONG.value == "long"

    def test_short_value(self) -> None:
        assert SignalDirection.SHORT.value == "short"


class TestNormalizeSignalDirection:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("long", SignalDirection.LONG),
            ("Long", SignalDirection.LONG),
            ("  LONG  ", SignalDirection.LONG),
            ("buy", SignalDirection.LONG),
            ("bull", SignalDirection.LONG),
            ("bullish", SignalDirection.LONG),
            ("short", SignalDirection.SHORT),
            ("Short", SignalDirection.SHORT),
            ("  SHORT  ", SignalDirection.SHORT),
            ("sell", SignalDirection.SHORT),
            ("bear", SignalDirection.SHORT),
            ("bearish", SignalDirection.SHORT),
        ],
    )
    def test_accepts_normal_and_alias(self, value: str, expected: SignalDirection) -> None:
        assert normalize_signal_direction(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "up",
            "down",
            "long short",
            "longs",
            "shorts",
            "1",
            "-1",
            "0",
            1,
            -1,
            0,
            1.0,
            -1.0,
            object(),
        ],
    )
    def test_rejects_non_direction_values(self, value: object) -> None:
        assert normalize_signal_direction(value) is None

    def test_does_not_default_unknown_to_long_or_short(self) -> None:
        assert normalize_signal_direction("unknown") is None
        assert normalize_signal_direction("random") is None

    def test_does_not_conflict_with_existing_core_direction(self) -> None:
        assert base_normalize_direction("buy") == "long"
        assert base_normalize_direction(-2) == "short"


class TestRequireSignalDirection:
    def test_returns_enum_for_valid_value(self) -> None:
        assert require_signal_direction("long") is SignalDirection.LONG
        assert require_signal_direction("buy") is SignalDirection.LONG
        assert require_signal_direction("sell") is SignalDirection.SHORT

    def test_raises_value_error_for_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            require_signal_direction("unknown")
        with pytest.raises(ValueError):
            require_signal_direction(None)
        with pytest.raises(ValueError):
            require_signal_direction("")


class TestSignalDirectionSign:
    def test_long_is_positive(self) -> None:
        assert signal_direction_sign(SignalDirection.LONG) == 1

    def test_short_is_negative(self) -> None:
        assert signal_direction_sign(SignalDirection.SHORT) == -1

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ValueError):
            signal_direction_sign("long")  # type: ignore[arg-type]


class TestSignalDirectionImportContract:
    def test_module_import_is_dependency_light(self) -> None:
        import examples.strategies.venue_agnostic_signal_observer.core.signal_direction as module  # noqa: F401

        assert module is not None
        for forbidden in (
            "examples.strategies.venue_agnostic_signal_observer.paper",
            "examples.strategies.venue_agnostic_signal_observer.bot",
            "examples.strategies.venue_agnostic_signal_observer.conductor",
            "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.node_fills_liq_reconstruction.runner",
            "examples.strategies.venue_agnostic_signal_observer.hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        ):
            assert forbidden not in sys.modules, f"forbidden module imported: {forbidden}"
