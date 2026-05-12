"""V7: L2 order book model with spread, midprice, imbalance, staleness."""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
import time


@dataclass(frozen=True)
class BookLevel:
    """Single price level in the order book."""
    price: float
    size: float


@dataclass
class OrderBook:
    """L2 order book with convenience methods."""
    symbol: str
    bids: List[BookLevel] = field(default_factory=list)  # sorted high to low
    asks: List[BookLevel] = field(default_factory=list)  # sorted low to high
    last_update_timestamp: float = 0.0
    sequence: int = 0

    @property
    def best_bid(self) -> Optional[float]:
        if self.bids and self.bids[0].price > 0:
            return self.bids[0].price
        return None

    @property
    def best_ask(self) -> Optional[float]:
        if self.asks and self.asks[0].price > 0:
            return self.asks[0].price
        return None

    @property
    def midprice(self) -> Optional[float]:
        bb = self.best_bid
        ba = self.best_ask
        if bb and ba:
            return (bb + ba) / 2.0
        return None

    @property
    def spread_bps(self) -> Optional[float]:
        bb = self.best_bid
        ba = self.best_ask
        if bb and ba and bb > 0:
            return ((ba - bb) / bb) * 10000.0
        return None

    @property
    def best_bid_size(self) -> Optional[float]:
        if self.bids:
            return self.bids[0].size
        return None

    @property
    def best_ask_size(self) -> Optional[float]:
        if self.asks:
            return self.asks[0].size
        return None

    @property
    def imbalance(self) -> float:
        """Volume imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol).
        Range: -1 (all ask) to +1 (all bid)."""
        bid_vol = sum(l.size for l in self.bids[:10])
        ask_vol = sum(l.size for l in self.asks[:10])
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    @property
    def is_crossed(self) -> bool:
        bb = self.best_bid
        ba = self.best_ask
        if bb and ba:
            return bb >= ba
        return False

    def is_stale(self, max_age_seconds: float = 10.0) -> bool:
        if self.last_update_timestamp <= 0:
            return True
        return (time.time() - self.last_update_timestamp) > max_age_seconds

    def apply_snapshot(self, bids: List[Dict], asks: List[Dict]) -> None:
        """Replace entire book with snapshot (initial book download)."""
        self.bids = sorted(
            [BookLevel(float(b[0]), float(b[1])) for b in bids if float(b[0]) > 0 and float(b[1]) > 0],
            key=lambda x: x.price,
            reverse=True,
        )
        self.asks = sorted(
            [BookLevel(float(a[0]), float(a[1])) for a in asks if float(a[0]) > 0 and float(a[1]) > 0],
            key=lambda x: x.price,
        )
        self.last_update_timestamp = time.time()

    def apply_update(self, bids: List[Dict], asks: List[Dict]) -> None:
        """Incrementally update book from WS delta."""
        # Bids: update or remove (size=0 means remove)
        bid_prices = {b.price for b in self.bids}
        for b in bids:
            price = float(b[0])
            size = float(b[1])
            if size == 0:
                self.bids = [x for x in self.bids if x.price != price]
            elif price in bid_prices:
                self.bids = [x if x.price != price else BookLevel(price, size) for x in self.bids]
                bid_prices.discard(price)
            else:
                self.bids.append(BookLevel(price, size))

        # Asks
        ask_prices = {a.price for a in self.asks}
        for a in asks:
            price = float(a[0])
            size = float(a[1])
            if size == 0:
                self.asks = [x for x in self.asks if x.price != price]
            elif price in ask_prices:
                self.asks = [x if x.price != price else BookLevel(price, size) for x in self.asks]
                ask_prices.discard(price)
            else:
                self.asks.append(BookLevel(price, size))

        # Re-sort
        self.bids.sort(key=lambda x: x.price, reverse=True)
        self.asks.sort(key=lambda x: x.price)

        # Remove crossed levels (sanity)
        if self.best_bid and self.best_ask and self.best_bid >= self.best_ask:
            self.bids = [b for b in self.bids if b.price < self.best_ask]
            self.asks = [a for a in self.asks if a.price > self.best_bid] if self.best_bid else self.asks

        self.last_update_timestamp = time.time()


@dataclass(frozen=True)
class BookSnapshot:
    """Immutable snapshot of book state for analysis."""
    timestamp: float
    symbol: str
    best_bid: Optional[float]
    best_ask: Optional[float]
    midprice: Optional[float]
    spread_bps: Optional[float]
    best_bid_size: Optional[float]
    best_ask_size: Optional[float]
    imbalance: float
    is_stale: bool
    is_crossed: bool
    bid_levels: int
    ask_levels: int

    @classmethod
    def from_book(cls, book: OrderBook, stale_age: float = 10.0) -> "BookSnapshot":
        return cls(
            timestamp=time.time(),
            symbol=book.symbol,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            midprice=book.midprice,
            spread_bps=book.spread_bps,
            best_bid_size=book.best_bid_size,
            best_ask_size=book.best_ask_size,
            imbalance=book.imbalance,
            is_stale=book.is_stale(stale_age),
            is_crossed=book.is_crossed,
            bid_levels=len(book.bids),
            ask_levels=len(book.asks),
        )
