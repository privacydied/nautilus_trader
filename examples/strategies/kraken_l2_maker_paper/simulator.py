"""V7: L2 maker paper simulator — main loop.

Observer-only. No orders. No private keys. No live execution.
"""
import asyncio
import time
import logging
from typing import Optional
from pathlib import Path

from .config import V7Config
from .book_models import OrderBook, BookSnapshot
from .paper_quote import QuoteEngine
from .paper_fill_model import PaperFillModel
from .reports import write_event, write_summary
from .kraken_ws import KrakenBookWS, HAS_AIOHTTP, WSEvent

logger = logging.getLogger("v7.simulator")


class MakerPaperSimulator:
    """Observer-only L2 maker paper simulator."""

    def __init__(self, cfg: V7Config):
        self.cfg = cfg
        # Book dicts: keyed by Kraken symbol (e.g. "BTC/USD")
        self.books: dict[str, OrderBook] = {}
        self.quote_engines: dict[str, QuoteEngine] = {}
        self.fill_models: dict[str, PaperFillModel] = {}
        self.stats = {
            "book_updates": 0,
            "quotes_placed": 0,
            "quotes_cancelled": 0,
            "paper_fills": 0,
            "adverse_checks": 0,
            "stale_books": 0,
            "crossed_books": 0,
        }
        self._book_initialized: set[str] = set()

        for symbol in cfg.symbols:
            self.books[symbol] = OrderBook(symbol=symbol)
            self.quote_engines[symbol] = QuoteEngine(cfg)
            self.fill_models[symbol] = PaperFillModel(cfg)

    # ---------------------------------------------------------------
    # Book update handler
    # ---------------------------------------------------------------

    def process_book_update(self, symbol: str):
        """Process a book update for one symbol."""
        if symbol not in self.books:
            return
        book = self.books[symbol]
        self.stats["book_updates"] += 1

        snap = BookSnapshot.from_book(book, self.cfg.stale_book_max_age_seconds)

        if snap.is_stale:
            self.stats["stale_books"] += 1
            self.quote_engines[symbol].close_all("stale_book")
            return

        if snap.is_crossed:
            self.stats["crossed_books"] += 1
            self.quote_engines[symbol].close_all("crossed_book")
            return

        # Check for fills on active quotes
        engine = self.quote_engines[symbol]
        fm = self.fill_models[symbol]
        states = list(engine.active_quotes)
        fills = fm.check_fills(states, book)
        self.stats["paper_fills"] += len(fills)

        # Initialize quotes if book wasn't ready yet
        if symbol not in self._book_initialized:
            if snap.best_bid and snap.best_ask and snap.spread_bps is not None:
                self._place_initial_quotes(symbol, snap)
                self._book_initialized.add(symbol)
        else:
            # Check existing quotes for cancellation
            cancelled = engine.check_quotes(
                snap.midprice, snap.spread_bps, snap.imbalance, False,
            )
            self.stats["quotes_cancelled"] += len(cancelled)
            # Replace cancelled quotes if spread OK
            if cancelled and snap.spread_bps is not None:
                self._place_initial_quotes(symbol, snap)

    def _place_initial_quotes(self, symbol: str, snap: BookSnapshot):
        """Place fresh quotes at the touch."""
        engine = self.quote_engines[symbol]
        mid = snap.midprice
        if mid is None:
            return

        active = len(engine.active_quotes)
        if self.cfg.quote_side in ("both", "bid"):
            if active < 4 and snap.best_bid:
                state = engine.place_quote(
                    symbol=symbol, side="bid", price=snap.best_bid,
                    midprice=mid, spread_bps=snap.spread_bps,
                    imbalance=snap.imbalance,
                )
                if state:
                    self.stats["quotes_placed"] += 1
                    active += 1

        if self.cfg.quote_side in ("both", "ask"):
            if active < 4 and snap.best_ask:
                state = engine.place_quote(
                    symbol=symbol, side="ask", price=snap.best_ask,
                    midprice=mid, spread_bps=snap.spread_bps,
                    imbalance=snap.imbalance,
                )
                if state:
                    self.stats["quotes_placed"] += 1

    # ---------------------------------------------------------------
    # Trade handler — adverse selection checks
    # ---------------------------------------------------------------

    def process_trade(self, symbol: str, price: float, size: float):
        fm = self.fill_models.get(symbol)
        if not fm or not fm.fills:
            return
        for fill in fm.fills[-20:]:
            chk = fm.check_adverse_selection(fill, price)
            if chk:
                self.stats["adverse_checks"] += 1

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------

    def get_summary_stats(self, start: float, end: float) -> dict:
        total_fills = 0
        gross_pnl_bps = 0.0
        total_fees_bps = 0.0

        for fm in self.fill_models.values():
            total_fills += len(fm.fills)
            for f in fm.fills:
                spread_cap = f.quote_spread_bps / 2.0 if f.quote_spread_bps else 0.0
                fee = self.cfg.maker_fee_bps + self.cfg.fill_penalty_bps
                gross_pnl_bps += spread_cap
                total_fees_bps += fee

        all_quotes = []
        for engine in self.quote_engines.values():
            all_quotes.extend(engine.history)

        fill_count = sum(1 for s in all_quotes if s.filled)
        total_quoted = len(all_quotes)
        fill_rate = fill_count / total_quoted if total_quoted > 0 else 0.0

        spreads = [
            s.quote.spread_bps_at_placement
            for s in all_quotes if s.quote.spread_bps_at_placement
        ]
        avg_spread = sum(spreads) / len(spreads) if spreads else 0.0

        lifetimes = [
            s.elapsed_seconds for s in all_quotes if s.cancelled or s.filled
        ]
        avg_lifetime = sum(lifetimes) / len(lifetimes) if lifetimes else 0.0

        net_pnl_bps = gross_pnl_bps - total_fees_bps

        return {
            "runtime_seconds": round(end - start, 1),
            "symbols": self.cfg.symbols,
            "book_updates": self.stats["book_updates"],
            "quotes_placed": self.stats["quotes_placed"],
            "quotes_cancelled": self.stats["quotes_cancelled"],
            "paper_fills": self.stats["paper_fills"],
            "fill_rate": round(fill_rate, 6),
            "average_spread_bps": round(avg_spread, 4),
            "average_quote_lifetime_seconds": round(avg_lifetime, 4),
            "gross_paper_pnl_bps": round(gross_pnl_bps, 4),
            "fees_bps": round(total_fees_bps, 4),
            "net_paper_pnl_bps": round(net_pnl_bps, 4),
            "stale_book_count": self.stats["stale_books"],
            "crossed_book_count": self.stats["crossed_books"],
            "adverse_checks_performed": self.stats["adverse_checks"],
        }

    # ---------------------------------------------------------------
    # Main runner
    # ---------------------------------------------------------------

    async def run(self, ws_client=None, out_dir: Optional[Path] = None,
                  duration: Optional[float] = None):
        """Main simulator loop."""
        if out_dir is None:
            out_dir = Path(self.cfg.output_dir)
        if duration is None:
            duration = self.cfg.duration_seconds
        out_dir = Path(out_dir)

        start = time.time()

        if ws_client is None:
            ws_client = KrakenBookWS(self.cfg.ws_url)

        event_log = out_dir / "events.jsonl"

        with open(event_log, "w") as fh:
            def on_event(event: WSEvent):
                if event.event_type in ("snapshot", "update"):
                    sym = event.symbol
                    if sym and sym in self.books:
                        self.process_book_update(sym)
                        # Log summary every ~50 updates
                        if self.stats["book_updates"] % 50 == 0:
                            book = self.books[sym]
                            snap = BookSnapshot.from_book(book, self.cfg.stale_book_max_age_seconds)
                            write_event({
                                "type": "book_update_summary",
                                "timestamp": time.time(),
                                "symbol": sym,
                                "best_bid": round(float(snap.best_bid or 0), 2),
                                "best_ask": round(float(snap.best_ask or 0), 2),
                                "midprice": round(float(snap.midprice or 0), 2),
                                "spread_bps": round(snap.spread_bps or 0, 4),
                                "imbalance": round(snap.imbalance, 4),
                                "is_stale": snap.is_stale,
                                "book_updates": self.stats["book_updates"],
                            }, fh)

                elif event.event_type == "trade":
                    if event.trades:
                        for t in event.trades:
                            self.process_trade(event.symbol, t["price"], t["qty"])
                            write_event({
                                "type": "trade",
                                "timestamp": event.timestamp,
                                "symbol": event.symbol,
                                "price": t["price"],
                                "qty": t["qty"],
                            }, fh)

            symbols = list(self.books.keys())
            try:
                await ws_client.connect_and_subscribe(
                    symbols, on_book_update=on_event, duration=duration,
                )
            except Exception as e:
                logger.error("WS connection failed: %s", e)
                if not HAS_AIOHTTP:
                    logger.error("aiohttp not available")
                return

            # Close remaining quotes
            for engine in self.quote_engines.values():
                for state in list(engine.active_quotes):
                    state.cancel("run_end")
                    engine.history.append(state)
                engine.active_quotes = []

            end = time.time()
            summary = self.get_summary_stats(start, end)
            write_summary(summary, out_dir)

            net = summary["net_paper_pnl_bps"]
            print(f"\nV7 Summary:")
            print(f"  Runtime: {summary['runtime_seconds']:.0f}s")
            print(f"  Book updates: {summary['book_updates']}")
            print(f"  Quotes placed/cancelled: {summary['quotes_placed']}/{summary['quotes_cancelled']}")
            print(f"  Paper fills: {summary['paper_fills']}")
            print(f"  Fill rate: {summary['fill_rate']*100:.2f}%")
            print(f"  Avg spread: {summary['average_spread_bps']:.2f} bps")
            print(f"  Gross paper PnL: {summary['gross_paper_pnl_bps']:.2f} bps")
            print(f"  Fees: {summary['fees_bps']:.2f} bps")
            print(f"  Net paper PnL: {net:.2f} bps")
            if net > 0:
                print("  → POSITIVE net paper PnL")
            else:
                print("  → NEGATIVE net paper PnL")
            print(f"\nSummary: {out_dir / 'summary.json'}")
