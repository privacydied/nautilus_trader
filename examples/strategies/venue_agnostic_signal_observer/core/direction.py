from __future__ import annotations

UNKNOWN_DIRECTION = "unknown"

_LONG_VALUES = {
    "long",
    "buy",
    "bid",
    "up",
    "positive",
    "+",
    "+1",
    "bull",
}
_SHORT_VALUES = {
    "short",
    "sell",
    "ask",
    "down",
    "negative",
    "-",
    "-1",
    "bear",
}


def normalize_direction(value: object) -> str:
    if value is None:
        return UNKNOWN_DIRECTION
    if isinstance(value, bool):
        return UNKNOWN_DIRECTION
    if isinstance(value, (int, float)):
        if value > 0:
            return "long"
        if value < 0:
            return "short"
        return UNKNOWN_DIRECTION

    normalized = str(value).strip().lower()
    if not normalized:
        return UNKNOWN_DIRECTION
    if normalized in _LONG_VALUES:
        return "long"
    if normalized in _SHORT_VALUES:
        return "short"
    return UNKNOWN_DIRECTION
