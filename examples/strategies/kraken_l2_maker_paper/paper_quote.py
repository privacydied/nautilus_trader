"""V7: Paper quote model for maker-style microstructure simulation.

Simulates post-only maker quotes without placing real orders.
Tracks quote lifecycle, cancellation reasons, and fill eligibility.
"""
from dataclasses import dataclass, field
from typing import Optional, List
import time


@dataclass(frozen=True)
class PaperQuote:
    """A single paper quote at a specific price level."""
    quote_id: int
    timestamp: float
    symbol: str
    side: str  # "bid" or "ask"
    price: float
    size: float  # assumed 1 unit for simplicity
    midprice_at_placement: Optional[float]
    spread_bps_at_placement: Optional[float]
    imbalance_at_placement: float

    @property
    def lifetime_seconds(self) -> float:
        return time.time() - self.timestamp


@dataclass
class PaperQuoteState:
    """Mutable state for an active quote."""
    quote: PaperQuote
    cancelled: bool = False
    filled: bool = False
    fill_timestamp: Optional[float] = None
    cancel_reason: Optional[str] = None
    cancel_timestamp: Optional[float] = None

    @property
    def is_active(self) -> bool:
        return not self.cancelled and not self.filled

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.quote.timestamp

    def cancel(self, reason: str):
        if not self.cancelled:
            self.cancelled = True
            self.cancel_reason = reason
            self.cancel_timestamp = time.time()

    def fill(self):
        if not self.filled and not self.cancelled:
            self.filled = True
            self.fill_timestamp = time.time()


class QuoteEngine:
    """Manages paper quote placement, cancellation, and lifecycle."""

    def __init__(self, config):
        self.config = config
        self._quote_counter = 0
        self.active_quotes: List[PaperQuoteState] = []
        self.history: List[PaperQuoteState] = []

    def place_quote(self, symbol: str, side: str, price: float,
                    midprice: Optional[float], spread_bps: Optional[float],
                    imbalance: float, size: float = 1.0) -> Optional[PaperQuoteState]:
        """Place a paper quote. Returns state or None if already at limit."""
        # Don't place too many concurrent quotes
        if len(self.active_quotes) >= 4:
            return None

        self._quote_counter += 1
        quote = PaperQuote(
            quote_id=self._quote_counter,
            timestamp=time.time(),
            symbol=symbol,
            side=side,
            price=price,
            size=size,
            midprice_at_placement=midprice,
            spread_bps_at_placement=spread_bps,
            imbalance_at_placement=imbalance,
        )
        state = PaperQuoteState(quote=quote)
        self.active_quotes.append(state)
        return state

    def check_quotes(self, current_midprice: Optional[float],
                     current_spread_bps: Optional[float],
                     current_imbalance: float,
                     stale_book: bool) -> List[PaperQuoteState]:
        """Check all active quotes against cancellation conditions.
        Returns list of quotes that were cancelled this tick."""
        cancelled = []
        cfg = self.config

        for state in self.active_quotes:
            if not state.is_active:
                continue

            # Lifetime exceeded
            if state.elapsed_seconds >= cfg.quote_lifetime_seconds:
                state.cancel("max_lifetime")
                cancelled.append(state)
                continue

            # Stale book
            if stale_book:
                state.cancel("stale_book")
                cancelled.append(state)
                continue

            # Midprice moved away from quote
            if current_midprice is not None and state.quote.midprice_at_placement is not None:
                old_mid = state.quote.midprice_at_placement
                if old_mid > 0:
                    move_bps = abs(current_midprice - old_mid) / old_mid * 10000.0
                    if move_bps > cfg.cancel_on_mid_move_bps:
                        state.cancel("mid_move")
                        cancelled.append(state)
                        continue

            # Spread collapsed below threshold
            if current_spread_bps is not None:
                if current_spread_bps < cfg.cancel_on_spread_collapse_bps:
                    state.cancel("spread_collapse")
                    cancelled.append(state)
                    continue

            # Imbalance flip (for quotes placed when imbalance favored the opposite side)
            if cfg.cancel_on_imbalance_flip:
                old_imp = state.quote.imbalance_at_placement
                if state.quote.side == "bid" and old_imp > 0.2 and current_imbalance < -0.2:
                    state.cancel("imbalance_flip")
                    cancelled.append(state)
                    continue
                if state.quote.side == "ask" and old_imp < -0.2 and current_imbalance > 0.2:
                    state.cancel("imbalance_flip")
                    cancelled.append(state)
                    continue

        # Move cancelled/filled to history
        self.active_quotes = [s for s in self.active_quotes if s.is_active]
        self.history.extend(cancelled)
        return cancelled

    def close_all(self, reason: str = "shutdown"):
        for state in self.active_quotes:
            if state.is_active:
                state.cancel(reason)
        self.history.extend(self.active_quotes)
        self.active_quotes = []
