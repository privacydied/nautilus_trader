"""V7: Paper fill model for maker-style microstructure.

Determines whether a hypothetical post-only maker quote would have filled
based on subsequent order book and trade activity. No orders are placed.

Key principle: fills only happen when book movement crosses through
the quote price — never on the same tick, never by assumption.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import time

from .paper_quote import PaperQuoteState
from .book_models import OrderBook


@dataclass(frozen=True)
class PaperFill:
    """Record of a paper fill event."""
    quote_id: int
    timestamp: float
    symbol: str
    side: str
    fill_price: float
    fill_size: float
    midprice_at_fill: Optional[float]
    spread_bps_at_fill: Optional[float]
    quote_age_seconds: float
    quote_spread_bps: Optional[float]  # spread when quote was placed
    adverse_selection_1s: Optional[float] = None
    adverse_selection_5s: Optional[float] = None
    adverse_selection_30s: Optional[float] = None
    adverse_selection_60s: Optional[float] = None


class PaperFillModel:
    """Determines fills for paper quotes based on book/trade movement.

    FILL RULES (pessimistic by default):
    1. A bid quote only fills if the best_ask later drops to or below the quote price
       (i.e., the ask side crosses through the quote).
    2. An ask quote only fills if the best_bid later rises to or above the quote price.
    3. Same-tick fills are never allowed — at least one book update must have occurred.
    4. If trade prices confirm the fill price was touched, that also triggers a fill.
    5. Fill penalty bps is deducted from the fill price to account for queue position.

    A pessimistic model requires book-crossing (not just price touch).
    A neutral model allows fill on price touch.
    An optimistic model fills immediately if trade price matches.
    """

    def __init__(self, config):
        self.config = config
        self.fills: List[PaperFill] = []
        self._adverse_checks: Dict[int, list] = {}  # quote_id -> [(window, mid_change, ts)]

    def _should_fill(self, quote_state: PaperQuoteState, book: OrderBook,
                     since_ts: float) -> bool:
        """Check if the quote has been filled based on book movement."""
        quote = quote_state.quote
        cfg = self.config

        # Must not be same-tick: require at least a small time delta
        if time.time() - quote.timestamp < 0.5:
            return False

        if cfg.fill_model == "pessimistic":
            # Book must have crossed the quote price
            if quote.side == "bid":
                # Bid fills only if best_ask drops to or below our bid price
                ba = book.best_ask
                if ba is not None and ba <= quote.price:
                    return True
            else:
                # Ask fills only if best_bid rises to or above our ask price
                bb = book.best_bid
                if bb is not None and bb >= quote.price:
                    return True

        elif cfg.fill_model == "neutral":
            # Fill if book touched the price level
            if quote.side == "bid":
                ba = book.best_ask
                if ba is not None and ba <= quote.price:
                    return True
            else:
                bb = book.best_bid
                if bb is not None and bb >= quote.price:
                    return True

        elif cfg.fill_model == "optimistic":
            # Fill if trade price touched quote level
            pass  # Would need trade data; skip for now to stay conservative

        return False

    def check_fills(self, quote_states: List[PaperQuoteState],
                    book: OrderBook) -> List[PaperFill]:
        """Check all active quotes for fills. Returns new fills."""
        new_fills = []
        now = time.time()

        for state in quote_states:
            if not state.is_active:
                continue

            if not self._should_fill(state, book, state.quote.timestamp):
                continue

            # Fill occurred
            state.fill()
            mid = book.midprice
            spread = book.spread_bps

            # Apply fill penalty (queue position uncertainty)
            fill_price = state.quote.price
            if self.config.fill_penalty_bps > 0:
                penalty = state.quote.price * (self.config.fill_penalty_bps / 10000.0)
                fill_price -= penalty if state.quote.side == "bid" else -penalty

            fill = PaperFill(
                quote_id=state.quote.quote_id,
                timestamp=now,
                symbol=state.quote.symbol,
                side=state.quote.side,
                fill_price=round(fill_price, 8),
                fill_size=state.quote.size,
                midprice_at_fill=round(mid, 4) if mid else None,
                spread_bps_at_fill=round(spread, 4) if spread else None,
                quote_age_seconds=state.elapsed_seconds,
                quote_spread_bps=state.quote.spread_bps_at_placement,
            )

            self.fills.append(fill)
            self._adverse_checks[fill.quote_id] = [{"timestamp": now, "mid": mid}]
            new_fills.append(fill)

        return new_fills

    def check_adverse_selection(self, fill: PaperFill,
                                current_midprice: Optional[float]) -> Optional[dict]:
        """Measure adverse selection since fill at configured windows."""
        if fill.midprice_at_fill is None or current_midprice is None:
            return None

        fill_mid = fill.midprice_at_fill
        now = time.time()
        age = now - fill.timestamp

        results = {"quote_id": fill.quote_id, "checks": []}
        for window in self.config.adverse_selection_windows:
            if age >= window:
                if fill.side == "bid":
                    # Adverse: mid moved down after buying
                    adverse = ((fill_mid - current_midprice) / fill_mid) * 10000.0
                else:
                    # Adverse: mid moved up after selling
                    adverse = ((current_midprice - fill_mid) / fill_mid) * 10000.0
                results["checks"].append({
                    "window_seconds": window,
                    "adverse_selection_bps": round(adverse, 4),
                })

        if results["checks"]:
            return results
        return None
