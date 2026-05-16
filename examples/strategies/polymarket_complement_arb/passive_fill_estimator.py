"""
Passive fill estimator for Polymarket complement arb strategy.

Records would-be maker quotes and tracks whether later trades or book movement
touch/cross those quotes. Estimates passive fill plausibility without claiming
queue priority.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from .models import BookSnapshot, PassiveFillEstimate


@dataclass
class QuoteRecord:
    """Internal record of a would-be maker quote."""
    condition_id: str
    market_slug: str
    side: Literal["YES", "NO"]
    quote_price: float
    quote_size: float
    quote_timestamp_ns: int
    edge_remaining_ns: int | None  # How long the edge was expected to last
    touched: bool = False
    crossed: bool = False
    time_to_touch_ms: float | None = None
    touch_timestamp_ns: int | None = None
    stale: bool = False
    resolved_by_danger: bool = False


class PassiveFillEstimator:
    """
    Estimates passive fill plausibility by tracking would-be quotes against
    subsequent book movement and trades.

    This does NOT claim queue priority or actual fill probability.
    All results are labelled as passive_fill_estimate, not actual_fill.
    """

    def __init__(self, max_quote_age_ms: float = 30_000.0, resolution_danger_window_ns: int = 3_600_000_000_000):
        self._max_quote_age_ms = max_quote_age_ms
        self._resolution_danger_window_ns = resolution_danger_window_ns
        self._active_quotes: dict[str, QuoteRecord] = {}
        self._estimates: list[PassiveFillEstimate] = []
        self._touch_count: int = 0
        self._cross_count: int = 0
        self._expired_count: int = 0

    @property
    def estimates(self) -> list[PassiveFillEstimate]:
        return list(self._estimates)

    @property
    def touch_count(self) -> int:
        return self._touch_count

    @property
    def cross_count(self) -> int:
        return self._cross_count

    @property
    def expired_count(self) -> int:
        return self._expired_count

    def record_quote(
        self,
        condition_id: str,
        market_slug: str,
        side: Literal["YES", "NO"],
        quote_price: float,
        quote_size: float,
        edge_remaining_ns: int | None = None,
    ) -> str:
        """
        Record a would-be maker quote.

        Returns a quote key for later lookups.
        """
        ts_ns = time.time_ns()
        key = f"{condition_id}_{side}_{ts_ns}"
        self._active_quotes[key] = QuoteRecord(
            condition_id=condition_id,
            market_slug=market_slug,
            side=side,
            quote_price=quote_price,
            quote_size=quote_size,
            quote_timestamp_ns=ts_ns,
            edge_remaining_ns=edge_remaining_ns,
        )
        return key

    def on_trade_ticks(self, trades: list) -> None:
        """
        Process trade ticks to detect touches/crosses of active quotes.
        """
        now_ns = time.time_ns()
        to_remove: list[str] = []

        for key, record in self._active_quotes.items():
            if record.stale or record.touched:
                if record.touched:
                    to_remove.append(key)
                continue

            # Check for staleness
            age_ms = (now_ns - record.quote_timestamp_ns) / 1_000_000
            if age_ms > self._max_quote_age_ms:
                record.stale = True
                self._expired_count += 1
                to_remove.append(key)
                record.touched = True  # Mark as resolved
                self._estimates.append(
                    PassiveFillEstimate(
                        condition_id=record.condition_id,
                        market_slug=record.market_slug,
                        side=record.side,
                        quote_price=record.quote_price,
                        quote_size=record.quote_size,
                        quote_timestamp_ns=record.quote_timestamp_ns,
                        touched=False,
                        crossed=False,
                        time_to_touch_ms=None,
                        edge_lifetime_ms=age_ms,
                        edge_remained_profitable=False,
                        second_leg_available=False,
                        stale_before_touch=True,
                        resolution_danger=record.resolved_by_danger,
                    ),
                )
                continue

            # Check trades for touches
            for trade in trades:
                trade_price = trade.get("price", 0) if isinstance(trade, dict) else getattr(trade, "price", 0)

                if record.side == "YES":
                    # For a BUY quote, a trade at or above our price is a touch
                    if trade_price >= record.quote_price:
                        record.touched = True
                        record.touch_timestamp_ns = now_ns
                        record.time_to_touch_ms = (now_ns - record.quote_timestamp_ns) / 1_000_000
                        self._touch_count += 1
                        if trade_price > record.quote_price:
                            record.crossed = True
                            self._cross_count += 1
                        self._estimates.append(
                            PassiveFillEstimate(
                                condition_id=record.condition_id,
                                market_slug=record.market_slug,
                                side=record.side,
                                quote_price=record.quote_price,
                                quote_size=record.quote_size,
                                quote_timestamp_ns=record.quote_timestamp_ns,
                                touched=True,
                                crossed=record.crossed,
                                time_to_touch_ms=record.time_to_touch_ms,
                                edge_lifetime_ms=record.time_to_touch_ms or 0.0,
                                edge_remained_profitable=True,
                                second_leg_available=False,
                                stale_before_touch=False,
                                resolution_danger=False,
                            ),
                        )
                        to_remove.append(key)
                        break
                else:
                    # For NO, same logic
                    if trade_price >= record.quote_price:
                        record.touched = True
                        record.touch_timestamp_ns = now_ns
                        record.time_to_touch_ms = (now_ns - record.quote_timestamp_ns) / 1_000_000
                        self._touch_count += 1
                        if trade_price > record.quote_price:
                            record.crossed = True
                            self._cross_count += 1
                        self._estimates.append(
                            PassiveFillEstimate(
                                condition_id=record.condition_id,
                                market_slug=record.market_slug,
                                side=record.side,
                                quote_price=record.quote_price,
                                quote_size=record.quote_size,
                                quote_timestamp_ns=record.quote_timestamp_ns,
                                touched=True,
                                crossed=record.crossed,
                                time_to_touch_ms=record.time_to_touch_ms,
                                edge_lifetime_ms=record.time_to_touch_ms or 0.0,
                                edge_remained_profitable=True,
                                second_leg_available=False,
                                stale_before_touch=False,
                                resolution_danger=False,
                            ),
                        )
                        to_remove.append(key)
                        break

        # Remove resolved records
        for key in to_remove:
            self._active_quotes.pop(key, None)

    def on_book_snapshot(self, snapshot: BookSnapshot) -> None:
        """
        Process book snapshots to detect touches/crosses from book movement.
        """
        now_ns = time.time_ns()

        for record in list(self._active_quotes.values()):
            if record.stale or record.touched:
                continue

            # Check if the best bid has moved to/past our quote
            if snapshot.bids and snapshot.bids[0][0] >= record.quote_price:
                record.touched = True
                record.touch_timestamp_ns = now_ns
                record.time_to_touch_ms = (now_ns - record.quote_timestamp_ns) / 1_000_000
                self._touch_count += 1

    def finalize(self, total_quotes: int) -> dict:
        """
        Finalize all pending estimates and return a summary.
        """
        # Expire remaining active quotes
        now_ns = time.time_ns()
        for record in list(self._active_quotes.values()):
            if not record.touched and not record.stale:
                age_ms = (now_ns - record.quote_timestamp_ns) / 1_000_000
                self._estimates.append(
                    PassiveFillEstimate(
                        condition_id=record.condition_id,
                        market_slug=record.market_slug,
                        side=record.side,
                        quote_price=record.quote_price,
                        quote_size=record.quote_size,
                        quote_timestamp_ns=record.quote_timestamp_ns,
                        touched=False,
                        crossed=False,
                        time_to_touch_ms=None,
                        edge_lifetime_ms=age_ms,
                        edge_remained_profitable=False,
                        second_leg_available=False,
                        stale_before_touch=True,
                        resolution_danger=False,
                    ),
                )
            self._expired_count += 1

        self._active_quotes.clear()

        times_to_touch = [
            e.time_to_touch_ms for e in self._estimates
            if e.touched and e.time_to_touch_ms is not None
        ]

        return {
            "total_quotes_recorded": total_quotes,
            "total_estimates": len(self._estimates),
            "touches": self._touch_count,
            "crosses": self._cross_count,
            "expired": self._expired_count,
            "touch_rate_pct": (self._touch_count / max(total_quotes, 1)) * 100,
            "median_time_to_touch_ms": sorted(times_to_touch)[len(times_to_touch) // 2] if times_to_touch else None,
            "p95_time_to_touch_ms": sorted(times_to_touch)[int(len(times_to_touch) * 0.95)] if len(times_to_touch) > 0 else None,
            "source": "book_movement_only",
        }
