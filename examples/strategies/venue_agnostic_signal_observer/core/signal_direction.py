from __future__ import annotations

from enum import Enum


class SignalDirection(str, Enum):
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
    if isinstance(value, SignalDirection):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return DIRECTION_ALIASES.get(normalized)


def require_signal_direction(value: object) -> SignalDirection:
    direction = normalize_signal_direction(value)
    if direction is None:
        raise ValueError(f"Invalid signal direction: {value!r}")
    return direction


def signal_direction_sign(direction: SignalDirection) -> int:
    required = require_signal_direction(direction)
    if required is SignalDirection.LONG:
        return 1
    if required is SignalDirection.SHORT:
        return -1
    raise ValueError(f"Invalid signal direction: {direction!r}")


__all__ = (
    "DIRECTION_ALIASES",
    "SignalDirection",
    "normalize_signal_direction",
    "require_signal_direction",
    "signal_direction_sign",
)
