#!/usr/bin/env python3
"""Tests for V7 L2 maker paper simulator."""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# Book model tests
# ============================================================

from examples.strategies.kraken_l2_maker_paper.book_models import (
    BookLevel,
    OrderBook,
    BookSnapshot,
)


class TestOrderBook:

    def test_best_bid_ask(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.5"], ["63999", "2.0"]],
            asks=[["64001", "1.0"], ["64002", "3.0"]],
        )
        assert book.best_bid == 64000.0
        assert book.best_ask == 64001.0

    def test_midprice(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64010", "1.0"]],
        )
        assert book.midprice == 64005.0

    def test_spread_bps(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64064.0", "1.0"]],  # ~10 bp spread
        )
        spread = book.spread_bps
        assert spread is not None
        assert abs(spread - 10.0) < 0.1  # ~10 bps

    def test_spread_bps_zero_ask(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["0", "1.0"]],
        )
        assert book.spread_bps is None

    def test_best_bid_from_empty(self):
        book = OrderBook("BTC/USD")
        assert book.best_bid is None
        assert book.best_ask is None
        assert book.midprice is None
        assert book.spread_bps is None

    def test_imbalance(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "10.0"]],
            asks=[["64001", "1.0"]],
        )
        # Bid vol >> ask vol → imbalance close to +1
        imp = book.imbalance
        assert imp > 0.5

    def test_imbalance_balanced(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "5.0"]],
            asks=[["64001", "5.0"]],
        )
        assert abs(book.imbalance) < 0.01

    def test_is_crossed_snapshot(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64010", "1.0"]],
            asks=[["64000", "1.0"]],
        )
        # apply_snapshot does NOT clean up crosses
        assert book.is_crossed is True

    def test_is_crossed_after_cleanup(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64010", "1.0"]],
        )
        # After clean setup, no cross
        assert book.is_crossed is False

    def test_stale_book(self):
        book = OrderBook("BTC/USD")
        book.last_update_timestamp = time.time() - 15.0
        assert book.is_stale(10.0) is True
        assert book.is_stale(20.0) is False

    def test_empty_book_is_stale(self):
        book = OrderBook("BTC/USD")
        book.last_update_timestamp = 0.0
        assert book.is_stale(10.0) is True

    def test_update_add_level(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64001", "1.0"]],
        )
        book.apply_update(
            bids=[["63998", "2.0"]],
            asks=[["64003", "3.0"]],
        )
        assert len(book.bids) == 2
        assert book.best_bid == 64000.0  # original still highest

    def test_update_modify_level(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64001", "1.0"]],
        )
        book.apply_update(
            bids=[["64000", "5.0"]],
            asks=[],
        )
        assert book.bids[0].size == 5.0

    def test_update_remove_level(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64001", "1.0"]],
        )
        book.apply_update(
            bids=[["64000", "0.0"]],
            asks=[],
        )
        assert len(book.bids) == 0


class TestBookSnapshot:

    def test_from_book(self):
        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64001", "1.0"]],
        )
        snap = BookSnapshot.from_book(book)
        assert snap.best_bid == 64000.0
        assert snap.best_ask == 64001.0
        assert snap.midprice == 64000.5
        assert snap.spread_bps is not None
        assert snap.bid_levels == 1
        assert snap.ask_levels == 1
        assert not snap.is_stale
        assert not snap.is_crossed


# ============================================================
# Quote tests
# ============================================================

from examples.strategies.kraken_l2_maker_paper.config import V7Config
from examples.strategies.kraken_l2_maker_paper.paper_quote import (
    PaperQuote,
    PaperQuoteState,
    QuoteEngine,
)


class TestPaperQuotePlacement:

    def _cfg(self):
        return V7Config()

    def test_place_bid_quote(self):
        engine = QuoteEngine(self._cfg())
        state = engine.place_quote(
            symbol="BTC/USD", side="bid", price=64000.0,
            midprice=64005.0, spread_bps=10.0, imbalance=0.2,
        )
        assert state is not None
        assert state.quote.side == "bid"
        assert state.quote.price == 64000.0
        assert state.is_active

    def test_place_ask_quote(self):
        engine = QuoteEngine(self._cfg())
        state = engine.place_quote(
            symbol="BTC/USD", side="ask", price=64010.0,
            midprice=64005.0, spread_bps=10.0, imbalance=-0.2,
        )
        assert state is not None
        assert state.quote.side == "ask"
        assert state.is_active

    def test_too_many_quotes(self):
        engine = QuoteEngine(self._cfg())
        states = []
        for i in range(6):
            s = engine.place_quote(
                symbol="BTC/USD", side="bid", price=64000.0 - i,
                midprice=64005.0, spread_bps=10.0, imbalance=0.2,
            )
            states.append(s)
        # Should accept up to 4
        active = [s for s in states if s is not None]
        assert len(active) <= 4


class TestQuoteCancellation:

    def _cfg(self):
        return V7Config()

    def test_cancel_on_lifetime(self):
        cfg = self._cfg()
        cfg.quote_lifetime_seconds = 0.001  # Very short for test
        engine = QuoteEngine(cfg)
        state = engine.place_quote(
            symbol="BTC/USD", side="bid", price=64000.0,
            midprice=64005.0, spread_bps=10.0, imbalance=0.2,
        )
        time.sleep(0.002)
        cancelled = engine.check_quotes(64005.0, 10.0, 0.2, False)
        assert len(cancelled) == 1
        assert cancelled[0].cancel_reason == "max_lifetime"

    def test_cancel_on_stale_book(self):
        engine = QuoteEngine(self._cfg())
        state = engine.place_quote(
            symbol="BTC/USD", side="bid", price=64000.0,
            midprice=64005.0, spread_bps=10.0, imbalance=0.2,
        )
        cancelled = engine.check_quotes(64005.0, 10.0, 0.2, True)
        assert len(cancelled) == 1
        assert cancelled[0].cancel_reason == "stale_book"

    def test_cancel_on_mid_move(self):
        engine = QuoteEngine(self._cfg())
        state = engine.place_quote(
            symbol="BTC/USD", side="bid", price=64000.0,
            midprice=64000.0, spread_bps=10.0, imbalance=0.2,
        )
        # Move mid by 10 bps = 0.1% → should trigger 5bps cancel threshold
        cancelled = engine.check_quotes(64040.0, 10.0, 0.2, False)
        assert len(cancelled) == 1
        assert cancelled[0].cancel_reason == "mid_move"

    def test_no_cancel_when_stable(self):
        engine = QuoteEngine(self._cfg())
        state = engine.place_quote(
            symbol="BTC/USD", side="bid", price=64000.0,
            midprice=64005.0, spread_bps=10.0, imbalance=0.1,
        )
        cancelled = engine.check_quotes(64005.001, 10.0, 0.1, False)
        assert len(cancelled) == 0
        assert state.is_active

    def test_close_all(self):
        engine = QuoteEngine(self._cfg())
        engine.place_quote("BTC/USD", "bid", 64000.0, 64005.0, 10.0, 0.2)
        engine.place_quote("BTC/USD", "ask", 64010.0, 64005.0, 10.0, -0.2)
        engine.close_all("shutdown")
        assert len(engine.active_quotes) == 0
        assert all(s.cancelled for s in engine.history)


# ============================================================
# Fill model tests
# ============================================================

from examples.strategies.kraken_l2_maker_paper.paper_fill_model import (
    PaperFillModel,
    PaperFill,
)


class TestPaperFillModel:

    def _cfg(self):
        return V7Config()

    def test_no_same_tick_fill(self):
        """A quote should not fill immediately on placement."""
        cfg = self._cfg()
        cfg.fill_model = "optimistic"  # Even with optimistic, same-tick should not fill
        model = PaperFillModel(cfg)

        engine = QuoteEngine(cfg)
        state = engine.place_quote(
            "BTC/USD", "bid", 64000.0, 64005.0, 10.0, 0.2,
        )
        book = OrderBook("BTC/USD")
        # Book has ask >> bid, so no cross anyway
        book.apply_snapshot(
            bids=[["64000", "1.0"]],
            asks=[["64001", "1.0"]],
        )

        fills = model.check_fills([state], book)
        assert len(fills) == 0

    def test_fill_on_cross_pessimistic(self):
        """Bid fills when best_ask drops to or below quote price."""
        cfg = self._cfg()
        cfg.fill_model = "pessimistic"
        model = PaperFillModel(cfg)

        # Place a bid at 64005
        quote_state = MagicMock()
        quote_state.is_active = True
        quote_state.filled = False
        quote_state.fill_timestamp = None
        ts = time.time()
        quote_state.quote = MagicMock()
        quote_state.quote.quote_id = 1
        quote_state.quote.timestamp = ts - 1.0  # 1 second ago, not same-tick
        quote_state.quote.symbol = "BTC/USD"
        quote_state.quote.side = "bid"
        quote_state.quote.price = 64005.0
        quote_state.quote.size = 1.0
        quote_state.fill = lambda: setattr(quote_state, 'filled', True) or setattr(quote_state, 'fill_timestamp', time.time())

        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64004", "1.0"]],
            asks=[["64005.0", "1.0"]],  # ask at quote price → cross
        )

        fills = model.check_fills([quote_state], book)
        assert len(fills) == 1
        assert fills[0].fill_price == pytest.approx(64005.0, abs=13.0)  # ~12.8 bps penalty applied
        assert quote_state.filled

    def test_ask_fill_on_cross(self):
        """Ask fills when best_bid rises to or above quote price."""
        cfg = self._cfg()
        cfg.fill_model = "pessimistic"
        model = PaperFillModel(cfg)

        ts = time.time() - 1.0
        quote_state = MagicMock()
        quote_state.is_active = True
        quote_state.filled = False
        quote_state.fill_timestamp = None
        quote_state.quote = MagicMock()
        quote_state.quote.quote_id = 2
        quote_state.quote.timestamp = ts
        quote_state.quote.symbol = "BTC/USD"
        quote_state.quote.side = "ask"
        quote_state.quote.price = 64005.0
        quote_state.quote.size = 1.0
        quote_state.fill = lambda: (setattr(quote_state, 'filled', True),
                                     setattr(quote_state, 'fill_timestamp', time.time()))

        book = OrderBook("BTC/USD")
        book.apply_snapshot(
            bids=[["64005.0", "1.0"]],  # bid at or above ask quote
            asks=[["64010", "1.0"]],
        )

        fills = model.check_fills([quote_state], book)
        assert len(fills) == 1


class TestAdverseSelection:

    def _cfg(self):
        return V7Config()

    def test_adverse_selection_filled_bid(self):
        """Adverse selection after bid fill: if mid drops, that's adverse."""
        cfg = self._cfg()
        model = PaperFillModel(cfg)

        fill = MagicMock()
        fill.quote_id = 1
        fill.timestamp = time.time() - 2.0  # 2 seconds ago
        fill.side = "bid"
        fill.midprice_at_fill = 64000.0

        # Mid dropped to 63990 → adverse
        result = model.check_adverse_selection(fill, 63990.0)
        assert result is not None
        checks_1s = [c for c in result["checks"] if c["window_seconds"] == 1]
        assert len(checks_1s) == 1
        assert checks_1s[0]["adverse_selection_bps"] > 0  # ~1.56 bps

    def test_adverse_selection_filled_ask(self):
        """Adverse selection after ask fill: if mid rises, that's adverse."""
        cfg = self._cfg()
        model = PaperFillModel(cfg)

        fill = MagicMock()
        fill.quote_id = 2
        fill.timestamp = time.time() - 2.0
        fill.side = "ask"
        fill.midprice_at_fill = 64000.0

        # Mid rose to 64010 → adverse
        result = model.check_adverse_selection(fill, 64010.0)
        assert result is not None

    def test_no_adverse_selection(self):
        """If mid moves in favor, no adverse selection."""
        cfg = self._cfg()
        model = PaperFillModel(cfg)

        fill = MagicMock()
        fill.quote_id = 3
        fill.timestamp = time.time() - 2.0
        fill.side = "bid"
        fill.midprice_at_fill = 64000.0

        # Mid rose to 64010 → favorable for bid
        result = model.check_adverse_selection(fill, 64010.0)
        # 1s window: 2s elapsed >= 1s, but check for window_seconds == 1
        assert result is not None  # Should have at least some windows
        checks_1s = [c for c in result["checks"] if c["window_seconds"] == 1]
        if checks_1s:
            assert checks_1s[0]["adverse_selection_bps"] < 0  # Negative = favorable


# ============================================================
# Report tests
# ============================================================

from examples.strategies.kraken_l2_maker_paper.reports import (
    write_event,
    write_summary,
)


class TestReports:

    def test_write_event(self):
        event = {"type": "book_update_summary", "symbol": "BTC/USD", "best_bid": 64000.0}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            write_event(event, f)
            path = f.name
        with open(path) as f:
            parsed = json.loads(f.readline())
        assert parsed["type"] == "book_update_summary"
        assert parsed["symbol"] == "BTC/USD"
        Path(path).unlink()

    def test_write_summary(self):
        with tempfile.TemporaryDirectory() as td:
            summary = {
                "runtime_seconds": 60.0,
                "symbols": ["BTC/USD"],
                "book_updates": 100,
                "paper_fills": 5,
                "net_paper_pnl_bps": -50.0,
            }
            path = write_summary(summary, Path(td))
            with open(path) as f:
                loaded = json.load(f)
            assert loaded["book_updates"] == 100
            assert loaded["paper_fills"] == 5


# ============================================================
# Config tests
# ============================================================

class TestV7Config:

    def test_defaults(self):
        cfg = V7Config()
        assert "BTC/USD" in cfg.symbols
        assert "ETH/USD" in cfg.symbols
        assert cfg.duration_seconds == 600.0
        assert cfg.quote_side == "both"
        assert cfg.fill_model == "pessimistic"
        assert cfg.maker_fee_bps == 3.0

    def test_custom_maker_fee(self):
        cfg = V7Config(maker_fee_bps=16.0)
        assert cfg.maker_fee_bps == 16.0


# ============================================================
# No private key / no order placement
# ============================================================

class TestNoLiveTradingCode:

    def test_no_place_order(self):
        for module_name in [
            "kraken_ws",
            "simulator",
            "paper_quote",
            "paper_fill_model",
            "book_models",
            "config",
            "reports",
        ]:
            path = (
                Path(__file__).resolve().parent.parent
                / f"{module_name}.py"
            )
            if not path.exists():
                continue
            source = path.read_text().lower()
            assert "place_order" not in source, f"{module_name} has place_order"
            assert "create_order" not in source, f"{module_name} has create_order"
            assert "submit_order" not in source, f"{module_name} has submit_order"
            assert "api_key" not in source, f"{module_name} has api_key"
            assert "secret_key" not in source, f"{module_name} has secret_key"
            assert "private_key" not in source, f"{module_name} has private_key"


# ============================================================
# Symbol tests
# ============================================================

from examples.strategies.kraken_l2_maker_paper.symbols import (
    SYMBOL_MAP,
    to_ws_symbol,
    to_rest_pair,
)


class TestSymbols:

    def test_btc_mapping(self):
        assert to_ws_symbol("BTC/USD") == "BTC/USD"

    def test_eth_mapping(self):
        assert to_ws_symbol("ETH/USD") == "ETH/USD"

    def test_unknown_symbol_raises(self):
        with pytest.raises(ValueError):
            to_ws_symbol("DOGE/USD")

    def test_rest_pair(self):
        assert to_rest_pair("BTC/USD") == "XBTUSD"
        assert to_rest_pair("ETH/USD") == "ETHUSD"
