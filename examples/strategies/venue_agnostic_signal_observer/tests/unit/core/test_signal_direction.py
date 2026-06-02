"""Focused tests for the isolated signal-direction normalization spine."""

from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.core.direction import (
    normalize_direction as base_normalize_direction,
)
from examples.strategies.venue_agnostic_signal_observer.core.signal_direction import (
    DIRECTION_ALIASES,
    SignalDirection,
    normalize_signal_direction,
    require_signal_direction,
    signal_direction_sign,
)


_REPO_ROOT = Path(__file__).resolve().parents[6]


def _run_import_probe(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT.parent)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class TestSignalDirectionEnum:
    def test_long_value(self) -> None:
        assert SignalDirection.LONG.value == "long"

    def test_short_value(self) -> None:
        assert SignalDirection.SHORT.value == "short"


class TestNormalizeSignalDirection:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("long", SignalDirection.LONG),
            ("short", SignalDirection.SHORT),
            ("Long", SignalDirection.LONG),
            ("SHORT", SignalDirection.SHORT),
            ("  long  ", SignalDirection.LONG),
            ("  SHORT  ", SignalDirection.SHORT),
            ("buy", SignalDirection.LONG),
            ("bull", SignalDirection.LONG),
            ("bullish", SignalDirection.LONG),
            ("sell", SignalDirection.SHORT),
            ("bear", SignalDirection.SHORT),
            ("bearish", SignalDirection.SHORT),
        ],
    )
    def test_accepts_valid_values(self, value: str, expected: SignalDirection) -> None:
        assert normalize_signal_direction(value) is expected

    def test_accepts_signal_direction_instance(self) -> None:
        assert normalize_signal_direction(SignalDirection.LONG) is SignalDirection.LONG
        assert normalize_signal_direction(SignalDirection.SHORT) is SignalDirection.SHORT

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "  ",
            "unknown",
            "random",
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
            True,
            False,
            object(),
            {},
            [],
        ],
    )
    def test_rejects_invalid_values(self, value: object) -> None:
        assert normalize_signal_direction(value) is None

    def test_does_not_default_unknown_to_long(self) -> None:
        assert normalize_signal_direction("unknown") is None
        assert normalize_signal_direction("random") is None

    def test_does_not_default_unknown_to_short(self) -> None:
        assert normalize_signal_direction("unknown") is None
        assert normalize_signal_direction("random") is None

    def test_does_not_accept_numeric_inputs(self) -> None:
        assert normalize_signal_direction(1) is None
        assert normalize_signal_direction(-1) is None
        assert normalize_signal_direction(0) is None
        assert normalize_signal_direction("1") is None
        assert normalize_signal_direction("-1") is None
        assert normalize_signal_direction("0") is None

    def test_empty_string_returns_none(self) -> None:
        assert normalize_signal_direction("") is None

    def test_none_returns_none(self) -> None:
        assert normalize_signal_direction(None) is None

    def test_no_fuzzy_matching(self) -> None:
        assert normalize_signal_direction("longish") is None
        assert normalize_signal_direction("shorter") is None

    def test_no_default_direction(self) -> None:
        assert normalize_signal_direction("foo") is None
        assert normalize_signal_direction("up") is None
        assert normalize_signal_direction("down") is None

    def test_preserves_base_direction_behavior(self) -> None:
        assert base_normalize_direction("buy") == "long"
        assert base_normalize_direction(-2) == "short"


class TestRequireSignalDirection:
    def test_returns_enum_for_valid_value(self) -> None:
        assert require_signal_direction("long") is SignalDirection.LONG
        assert require_signal_direction("buy") is SignalDirection.LONG
        assert require_signal_direction("sell") is SignalDirection.SHORT
        assert require_signal_direction(SignalDirection.LONG) is SignalDirection.LONG
        assert require_signal_direction(SignalDirection.SHORT) is SignalDirection.SHORT

    @pytest.mark.parametrize(
        "value",
        [
            "unknown",
            None,
            "",
            1,
            "buyer",
        ],
    )
    def test_raises_value_error_for_invalid_value(self, value: object) -> None:
        with pytest.raises(ValueError):
            require_signal_direction(value)


class TestSignalDirectionSign:
    def test_long_is_positive(self) -> None:
        assert signal_direction_sign(SignalDirection.LONG) == 1

    def test_short_is_negative(self) -> None:
        assert signal_direction_sign(SignalDirection.SHORT) == -1

    def test_invalid_value_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            signal_direction_sign("not_a_direction")

    def test_sign_matches_normalize(self) -> None:
        assert signal_direction_sign(require_signal_direction("long")) == 1
        assert signal_direction_sign(require_signal_direction("buy")) == 1
        assert signal_direction_sign(require_signal_direction("short")) == -1
        assert signal_direction_sign(require_signal_direction("sell")) == -1


class TestSignalDirectionImportContract:
    def test_module_import_is_dependency_light(self) -> None:
        result = _run_import_probe(
            """
import sys
before = set(sys.modules)
import examples.strategies.venue_agnostic_signal_observer.core.signal_direction as module
after = set(sys.modules)
forbidden = (
    'examples.strategies.venue_agnostic_signal_observer.paper',
    'examples.strategies.venue_agnostic_signal_observer.bot',
    'examples.strategies.venue_agnostic_signal_observer.conductor',
    'examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.node_fills_liq_reconstruction.runner',
    'examples.strategies.venue_agnostic_signal_observer.hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0',
)
hits = [name for name in forbidden if name in after - before]
print(module.__name__)
print('hits', hits)
raise SystemExit(1 if hits else 0)
"""
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'hits []' in result.stdout

    def test_existing_core_direction_remains_importable(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.core import direction

        assert hasattr(direction, "normalize_direction")
