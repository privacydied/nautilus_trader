"""
Timestamp normalisation for offline historical data.

Supports seconds, milliseconds, microseconds, and nanoseconds.

Rules:
- Prefer explicit unit declarations; ambiguous unit with no declaration fails loudly.
- After normalisation, timestamps must land within expected_start_ns..expected_end_ns
  plus tolerance_ns, or the validator raises.
- Impossible dates (< year 2000 or > year 2100 after normalisation) always fail.
- Sorting is allowed only if recorded in metadata as was_sorted=True.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .offline_historical_models import TIMESTAMP_UNIT_MS
from .offline_historical_models import TIMESTAMP_UNIT_NS
from .offline_historical_models import TIMESTAMP_UNIT_S
from .offline_historical_models import TIMESTAMP_UNIT_US
from .offline_historical_models import VALID_TIMESTAMP_UNITS


# ---------------------------------------------------------------------------
# Plausible range guard — epoch ns for 2000-01-01 and 2100-01-01
# ---------------------------------------------------------------------------

_NS_YEAR_2000 = 946_684_800_000_000_000   # 2000-01-01T00:00:00Z in ns
_NS_YEAR_2100 = 4_102_444_800_000_000_000  # 2100-01-01T00:00:00Z in ns

# Multipliers from raw unit -> nanoseconds
_UNIT_TO_NS: dict[str, int] = {
    TIMESTAMP_UNIT_S:  1_000_000_000,
    TIMESTAMP_UNIT_MS: 1_000_000,
    TIMESTAMP_UNIT_US: 1_000,
    TIMESTAMP_UNIT_NS: 1,
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def to_nanoseconds(raw: float, unit: str) -> int:
    """
    Convert a raw timestamp value to integer nanoseconds.

    ``unit`` must be one of the VALID_TIMESTAMP_UNITS.  Fails loudly for
    unknown units or values that would fall outside the year-2000..2100 guard.
    """
    if unit not in VALID_TIMESTAMP_UNITS:
        raise ValueError(
            f"Unknown timestamp unit {unit!r}. Must be one of {sorted(VALID_TIMESTAMP_UNITS)}"
        )
    multiplier = _UNIT_TO_NS[unit]
    ns = int(raw * multiplier)
    if not (_NS_YEAR_2000 <= ns <= _NS_YEAR_2100):
        raise ValueError(
            f"Normalised timestamp {ns} ns (raw={raw}, unit={unit!r}) is outside "
            f"the plausible range 2000-01-01..2100-01-01. "
            f"Possible wrong timestamp unit or corrupt data."
        )
    return ns


def validate_timestamp_range(
    timestamp_ns: int,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,  # 1 day default tolerance
) -> None:
    """
    Raise if ``timestamp_ns`` lies implausibly outside the declared window.

    A one-day tolerance is applied by default to handle off-by-one day edge
    effects at file boundaries.
    """
    low = expected_start_ns - tolerance_ns
    high = expected_end_ns + tolerance_ns
    if not (low <= timestamp_ns <= high):
        raise ValueError(
            f"Timestamp {timestamp_ns} ns is outside expected range "
            f"[{expected_start_ns} - {tolerance_ns}, {expected_end_ns} + {tolerance_ns}] "
            f"(={low}..{high}). "
            f"Check timestamp unit declaration or expected date range."
        )


# ---------------------------------------------------------------------------
# Batch normalisation helper
# ---------------------------------------------------------------------------


@dataclass
class NormalisedTimestampBatch:
    """Result of normalising a batch of raw timestamps."""

    timestamps_ns: List[int]
    unit_used: str
    was_sorted: bool
    inferred_unit: bool           # True if unit was guessed rather than declared


def normalise_timestamp_batch(
    raw_values: List[int | float],
    unit: str,
    expected_start_ns: int | None = None,
    expected_end_ns: int | None = None,
    tolerance_ns: int = 86_400_000_000_000,
    allow_sort: bool = True,
) -> NormalisedTimestampBatch:
    """
    Normalise a list of raw timestamps to nanoseconds.

    If ``expected_start_ns`` and ``expected_end_ns`` are provided, every
    normalised timestamp is range-validated.

    If the batch is not monotonically non-decreasing, it is sorted and
    ``was_sorted`` is recorded as True.
    """
    ns_values = [to_nanoseconds(r, unit) for r in raw_values]

    # Range validation
    if expected_start_ns is not None and expected_end_ns is not None:
        for ns in ns_values:
            validate_timestamp_range(ns, expected_start_ns, expected_end_ns, tolerance_ns)

    # Monotonicity / sort
    was_sorted = False
    for i in range(1, len(ns_values)):
        if ns_values[i] < ns_values[i - 1]:
            if not allow_sort:
                raise ValueError(
                    "Timestamps are not monotonically non-decreasing and sorting is disabled."
                )
            ns_values = sorted(ns_values)
            was_sorted = True
            break

    return NormalisedTimestampBatch(
        timestamps_ns=ns_values,
        unit_used=unit,
        was_sorted=was_sorted,
        inferred_unit=False,
    )
