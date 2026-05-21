"""
JSONL loading/saving, tick sorting, deduplication, and stale tick rejection.

This module provides **storage and loading utilities only** for managing
JSONL files containing tick-level data used by the signal observer.

It handles:
- Loading and saving TradeTickLite and QuoteTickLite from/to JSONL files
- Sorting ticks by timestamp
- Deduplicating trade ticks
- Rejecting stale ticks that indicate data gaps
- Merging trade and quote tick streams
- Discovering tick data files by venue, symbol, and tick type

**No execution logic, no order submission, no position tracking, and no
live-trading code** is present in or invoked by this module.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .tick_models import QuoteTickLite
from .tick_models import TradeTickLite


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_trades_jsonl(path: str) -> list[TradeTickLite]:
    """
    Load TradeTickLite objects from a JSONL file.

    - Reads one JSON object per line using TradeTickLite.from_dict()
    - Sorts results by ts_event ascending
    - Deduplicates by (ts_event, venue, symbol, price, size), keeping first
      occurrence
    - Skips malformed lines with a warning to stderr

    Args:
        path: Absolute or relative path to the JSONL file.

    Returns:
        Deduplicated, sorted list of TradeTickLite instances.
    """
    trades: list[TradeTickLite] = []
    seen: set[tuple] = set()

    with open(path) as f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                tick = TradeTickLite.from_dict(json.loads(stripped))
            except Exception as exc:
                print(
                    f"WARNING: skipping malformed trade line {lineno} in "
                    f"{path}: {exc}",
                    file=sys.stderr,
                )
                continue

            # Deduplicate by composite key
            key = (tick.ts_event, tick.venue, tick.symbol, tick.price, tick.size)
            if key in seen:
                continue
            seen.add(key)
            trades.append(tick)

    return _sort_ticks(trades)


def load_quotes_jsonl(path: str) -> list[QuoteTickLite]:
    """
    Load QuoteTickLite objects from a JSONL file.

    - Reads one JSON object per line using QuoteTickLite.from_dict()
    - Sorts results by ts_event ascending
    - Skips quotes where ask <= bid (invalid cross or crossed market)
    - Skips malformed lines with a warning to stderr

    Args:
        path: Absolute or relative path to the JSONL file.

    Returns:
        Sorted list of valid QuoteTickLite instances.
    """
    quotes: list[QuoteTickLite] = []

    with open(path) as f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(
                    f"WARNING: skipping malformed quote line {lineno} in "
                    f"{path}: {exc}",
                    file=sys.stderr,
                )
                continue

            # Pre-validate ask > bid before delegating to from_dict
            try:
                ask_val = float(data["ask"]) if "ask" in data and data["ask"] is not None else 0.0
                bid_val = float(data["bid"]) if "bid" in data and data["bid"] is not None else 0.0
            except (TypeError, ValueError) as exc:
                print(
                    f"WARNING: skipping quote line {lineno} in {path}: "
                    f"bid/ask parse error — {exc}",
                    file=sys.stderr,
                )
                continue

            if ask_val <= bid_val:
                print(
                    f"WARNING: skipping invalid quote line {lineno} in "
                    f"{path}: ask ({ask_val}) <= bid ({bid_val})",
                    file=sys.stderr,
                )
                continue

            try:
                tick = QuoteTickLite.from_dict(data)
            except Exception as exc:
                print(
                    f"WARNING: skipping quote line {lineno} in {path}: {exc}",
                    file=sys.stderr,
                )
                continue

            quotes.append(tick)

    return _sort_ticks(quotes)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def save_trades_jsonl(path: str, trades: list[TradeTickLite]) -> None:
    """
    Serialize TradeTickLite objects to a JSONL file.

    Creates intermediate parent directories if they do not exist.

    Args:
        path: Output file path (will be overwritten).
        trades: TradeTickLite instances to write (must support .to_json()).
    """
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.writelines(tick.to_json() + "\n" for tick in trades)


def save_quotes_jsonl(path: str, quotes: list[QuoteTickLite]) -> None:
    """
    Serialize QuoteTickLite objects to a JSONL file.

    Creates intermediate parent directories if they do not exist.

    Args:
        path: Output file path (will be overwritten).
        quotes: QuoteTickLite instances to write (must support .to_json()).
    """
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.writelines(tick.to_json() + "\n" for tick in quotes)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def _sort_ticks[T: TradeTickLite | QuoteTickLite](ticks: list[T]) -> list[T]:
    """Internal helper — sort a list of ticks by ts_event (non-mutating)."""
    return sorted(ticks, key=lambda t: t.ts_event)


def sort_by_ts(
    ticks: list[TradeTickLite | QuoteTickLite],
) -> list[TradeTickLite | QuoteTickLite]:
    """
    Return a new list sorted by ts_event ascending.

    Works with mixed lists containing TradeTickLite and/or QuoteTickLite.

    Args:
        ticks: Input tick list (unsorted).

    Returns:
        A new sorted list.
    """
    return _sort_ticks(ticks)


# ---------------------------------------------------------------------------
# Stale tick rejection
# ---------------------------------------------------------------------------


def reject_stale_ticks(
    ticks: list,
    max_age_ns: int = 60_000_000_000,
) -> list:
    """
    Remove ticks that are stale relative to the next tick in the series.

    A tick is considered stale when the gap between it and the *next* tick
    exceeds *max_age_ns* nanoseconds.  This detects data gaps where a tick
    sits far behind the following data point.

    The input list is assumed to already be sorted by ts_event ascending.

    Args:
        ticks: Sorted list of tick objects exposing ``ts_event`` (int).
        max_age_ns: Maximum allowed gap in nanoseconds (default: 60 seconds).

    Returns:
        Filtered list with stale ticks removed.
    """
    if len(ticks) <= 1:
        return list(ticks)

    result: list = []
    for i, tick in enumerate(ticks[:-1]):
        next_tick = ticks[i + 1]
        gap = next_tick.ts_event - tick.ts_event
        if gap <= max_age_ns:
            result.append(tick)

    # Last tick: always keep if the preceding tick was kept and recent,
    # or if it is the only tick.  We check the gap from the last kept tick.
    # Simpler rule: the final tick has no successor, so we keep it if
    # the gap from the previous tick is within bounds (or if it's alone
    # after filtering).
    if result:
        last_kept = result[-1]
        if ticks[-1].ts_event - last_kept.ts_event <= max_age_ns:
            result.append(ticks[-1])
    else:
        # Nothing kept yet — keep the last tick only if it isn't standalone
        # stale (no next tick means we have nothing to compare to, so keep it).
        result.append(ticks[-1])

    return result


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

_TT_TAG = "trade"
_QT_TAG = "quote"


def merge_and_sort_ticks(
    trades: list[TradeTickLite],
    quotes: list[QuoteTickLite],
) -> list:
    """
    Merge trade and quote tick lists into one timeline sorted by ts_event.

    Each item is annotated with a ``tick_type`` attribute at runtime
    (``"trade"`` or ``"quote"``) so downstream consumers can distinguish
    them without isinstance checks.  If the attribute already exists it
    is left unchanged.

    Args:
        trades: List of TradeTickLite objects.
        quotes: List of QuoteTickLite objects.

    Returns:
        Mixed list sorted by ts_event; each element has a ``tick_type`` attr.
    """
    merged: list[TradeTickLite | QuoteTickLite] = []

    for t in trades:
        # Tag if not already tagged
        if not hasattr(t, "tick_type") or getattr(t, "tick_type", None) is None:
            object.__setattr__(t, "tick_type", _TT_TAG)
        else:
            # Ensure it says "trade"
            if t.tick_type != _TT_TAG:
                object.__setattr__(t, "tick_type", _TT_TAG)
        merged.append(t)

    for q in quotes:
        if not hasattr(q, "tick_type") or getattr(q, "tick_type", None) is None:
            object.__setattr__(q, "tick_type", _QT_TAG)
        else:
            if q.tick_type != _QT_TAG:
                object.__setattr__(q, "tick_type", _QT_TAG)
        merged.append(q)

    return _sort_ticks(merged)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

# Pattern: tick_type_<venue>_<symbol>_<timestamp>.jsonl
_TICK_FILE_RE = re.compile(
    r"^(?P<tick_type>[a-zA-Z0-9_-]+)_"
    r"(?P<venue>[a-zA-Z0-9_-]+)_"
    r"(?P<symbol>[a-zA-Z0-9_-]+)_"
    r"(?P<ts>\d{10,}(?:T\d{6})?)\.jsonl?$",
)


def tick_file_discovery(
    data_dir: str,
    venue: str,
    symbol: str,
    tick_type: str = "trades",
) -> list[str]:
    """
    Discover JSONL tick files matching the expected naming convention.

    Expected filename pattern::

        <tick_type>_<venue>_<symbol>_<timestamp>.jsonl

    Example::

        trades_binance_BTC-USDT_20240101T000000.jsonl

    Matching on venue and symbol is case-insensitive.

    Args:
        data_dir: Directory to scan (must exist).
        venue: Venue identifier to match (case-insensitive).
        symbol: Symbol identifier to match (case-insensitive).
        tick_type: Tick type prefix to match, e.g. ``"trades"`` or ``"quotes"``.

    Returns:
        Sorted list of absolute file path strings.
    """
    p = Path(data_dir)
    if not p.is_dir():
        return []

    venue_lower = venue.lower()
    symbol_lower = symbol.lower()
    tick_type_lower = tick_type.lower()

    results: list[str] = []

    for entry in p.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.lower().endswith(".jsonl"):
            continue

        match = _TICK_FILE_RE.match(entry.name)
        if match is None:
            # Try with .jsonl.gz — not part of spec, skip
            continue

        parts = match.groupdict()
        # Normalize slashes/hyphens for cross-venue symbol matching
        file_sym = parts["symbol"].lower().replace("-", "/")
        arg_sym = symbol_lower.replace("-", "/")
        if (
            parts["tick_type"].lower() == tick_type_lower
            and parts["venue"].lower() == venue_lower
            and file_sym == arg_sym
        ):
            results.append(str(entry.resolve()))

    return sorted(results)
