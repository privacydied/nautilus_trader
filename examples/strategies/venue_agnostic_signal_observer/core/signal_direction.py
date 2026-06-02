"""Shared signal-direction normalization utilities.

This module provides a future-facing, import-safe normalization API for
signal direction vocabulary. It is intentionally isolated from the existing
``core.direction`` helper so existing caller behavior remains unchanged.
"""

from __future__ import annotations

from enum import Enum


class SignalDirection(str, Enum):
    """Typed signal-direction vocabulary."""

    LONG = "long"
    SHORT = "short"


DIRECTION_ALIASES: dict[str, SignalDirection] = {
    "long": SignalDirection.LONG,
    "buy": SignalDirection.LONG,
    "bull": SignalDirection.LONG,
    "bullish": SignalDirection.LONG,
    "short": SignalDirection.SHORT,
    "sell": SignalDirection.SHORT,
    "bear": SignalDirection.SHORT,
    "bearish": SignalDirection.SHORT,
}


def normalize_signal_direction(value: object) -> SignalDirection | None:
    """Normalize *value* to :class:`SignalDirection` if recognized.

    Returns ``None`` for missing, empty, or unknown input.
    Numeric inputs are not supported and return ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, (str, bytes)):
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    normalized = value.strip().lower()
    if not normalized:
        return None
    return DIRECTION_ALIASES.get(normalized)


def require_signal_direction(value: object) -> SignalDirection:
    """Normalize *value* or raise :class:`ValueError` if unknown/missing."""
    result = normalize_signal_direction(value)
    if result is None:
        raise ValueError(f"Unknown/missing direction: {value!r}")
    return result


def signal_direction_sign(direction: SignalDirection) -> int:
    """Return ``+1`` for long and ``-1`` for short."""
    if direction is SignalDirection.LONG:
        return 1
    if direction is SignalDirection.SHORT:
        return -1
    raise ValueError(f"Unsupported direction: {direction!r}")
