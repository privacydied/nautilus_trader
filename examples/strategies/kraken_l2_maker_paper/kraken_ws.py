"""V7: Kraken public WebSocket order book client using aiohttp.

Subscribes to L2 book via Kraken WS v2 public endpoint.
No private channels. No authentication.

Kraken WS v2 format:
- Subscribe: {"method": "subscribe", "params": {"channel": "book", "symbol": ["BTC/USD"], ...}}
- Book data: {"channel": "book", "type": "update/snapshot", "data": [{"symbol": "BTC/USD", "bids": [...], "asks": [...]}]}
- Trade data: {"channel": "trade", "type": "snapshot", "data": [{"symbol": "BTC/USD", "trades": [...]}]}
"""
import asyncio
import json
import time
import logging
from typing import Callable, Dict, Optional
from dataclasses import dataclass, field

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from .book_models import OrderBook
from .symbols import SYMBOL_MAP

logger = logging.getLogger("v7.kraken_ws")


@dataclass
class WSEvent:
    """Parsed WS event."""
    event_type: str  # "snapshot", "update", "heartbeat", "trade"
    symbol: str
    timestamp: float
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    trades: list = field(default_factory=list)  # [{"price": N, "qty": N}, ...]


class KrakenBookWS:
    """Kraken public WebSocket L2 book subscriber via aiohttp."""

    def __init__(self, ws_url: str = "wss://ws.kraken.com/v2"):
        self.ws_url = ws_url
        self.books: Dict[str, OrderBook] = {}
        self.running = False

    def register_callback(self, callback: Callable[[WSEvent], None]):
        pass  # Deprecated — use on_book_update param on connect_and_subscribe

    @staticmethod
    def _convert_price_qty(items: list) -> list:
        """Convert [{'price': N, 'qty': N}, ...] → [[price_str, qty_str], ...]"""
        return [[str(i["price"]), str(i["qty"])] for i in items if "price" in i and "qty" in i]

    @staticmethod
    def _parse_book_data(raw) -> Dict[str, list]:
        """Parse book data into {symbol: {bids, asks}}."""
        results = {}
        if isinstance(raw, list):
            for item in raw:
                symbol = item.get("symbol", "")
                bids = item.get("bids", [])
                asks = item.get("asks", [])
                if symbol:
                    results[symbol] = {
                        "bids": KrakenBookWS._convert_price_qty(bids) if bids and isinstance(bids[0], dict) else bids,
                        "asks": KrakenBookWS._convert_price_qty(asks) if asks and isinstance(asks[0], dict) else asks,
                    }
        elif isinstance(raw, dict):
            bids = raw.get("bids", [])
            asks = raw.get("asks", [])
            if bids and isinstance(bids[0], dict):
                bids = KrakenBookWS._convert_price_qty(bids)
            if asks and isinstance(asks[0], dict):
                asks = KrakenBookWS._convert_price_qty(asks)
            results[""] = {"bids": bids, "asks": asks}
        return results

    def _handle_message(self, msg: dict) -> list:
        """Parse WS message and update book. Returns list of WSEvents for callbacks."""
        events = []
        ch = msg.get("channel", "")
        msg_type = msg.get("type", msg.get("method", ""))

        if msg_type == "?" or msg_type == "heartbeat" or ch == "heartbeat":
            return events

        if msg.get("error"):
            logger.error(f"WS error: {msg['error']}")
            return events

        ts = time.time()

        if ch == "book":
            raw = msg.get("data", {})
            parsed = self._parse_book_data(raw)
            for symbol, ba in parsed.items():
                # Create book entry on-the-fly if needed
                if symbol not in self.books:
                    self.books[symbol] = OrderBook(symbol=symbol)

                book = self.books[symbol]
                if len(ba["bids"]) >= 10:
                    book.apply_snapshot(ba["bids"], ba["asks"])
                    evt_type = "snapshot"
                else:
                    book.apply_update(ba["bids"], ba["asks"])
                    evt_type = "update"

                events.append(WSEvent(
                    event_type=evt_type, symbol=symbol, timestamp=ts,
                ))

        elif ch == "trade":
            raw = msg.get("data", {})
            if isinstance(raw, list):
                for item in raw:
                    symbol = item.get("symbol", "")
                    trades = item.get("trades", [])
                    if symbol and trades:
                        events.append(WSEvent(
                            event_type="trade", symbol=symbol, timestamp=ts,
                            trades=[{"price": float(t.get("price", 0)), "qty": float(t.get("qty", 0))}
                                    for t in trades],
                        ))
            elif isinstance(raw, dict):
                symbol = raw.get("symbol", "")
                trades = raw.get("trades", [])
                if symbol and trades:
                    events.append(WSEvent(
                        event_type="trade", symbol=symbol, timestamp=ts,
                        trades=[{"price": float(t.get("price", 0)), "qty": float(t.get("qty", 0))}
                                for t in trades],
                    ))

        return events

    async def connect_and_subscribe(self, symbols: list, on_book_update=None,
                                     max_retries: int = 5, duration: float = 600.0):
        """Connect to Kraken WS v2, subscribe to L2 books, run until duration."""
        if not HAS_AIOHTTP:
            logger.error("aiohttp not available")
            return False

        self.running = True
        start = time.time()
        retry_count = 0

        while self.running and retry_count < max_retries:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        self.ws_url,
                        heartbeat=20,
                        timeout=aiohttp.ClientTimeout(total=None),
                    ) as ws:
                        self._ws = ws
                        retry_count = 0
                        logger.info(f"Connected to {self.ws_url}")

                        # Subscribe to each symbol
                        for symbol in symbols:
                            ws_symbol = SYMBOL_MAP.get(symbol, symbol)
                            await ws.send_json({
                                "method": "subscribe",
                                "params": {
                                    "channel": "book",
                                    "symbol": [ws_symbol],
                                    "snapshot": True,
                                    "depth": 10,
                                }
                            })
                            logger.info(f"Subscribed to {ws_symbol} book")

                            await ws.send_json({
                                "method": "subscribe",
                                "params": {
                                    "channel": "trade",
                                    "symbol": [ws_symbol],
                                }
                            })

                        # Main loop
                        while self.running and (time.time() - start) < duration:
                            try:
                                ws_msg = await asyncio.wait_for(ws.receive(), timeout=5.0)

                                if ws_msg.type == aiohttp.WSMsgType.TEXT:
                                    data = json.loads(ws_msg.data)
                                    if isinstance(data, dict):
                                        events = self._handle_message(data)
                                        if on_book_update:
                                            for evt in events:
                                                on_book_update(evt)
                                elif ws_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break

                            except asyncio.TimeoutError:
                                continue

                        return True

            except (aiohttp.ClientError, OSError) as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"Connection failed: {e}")
                    return False
                delay = 2.0 * retry_count
                logger.warning(f"Reconnecting ({retry_count}/{max_retries}) in {delay}s: {e}")
                await asyncio.sleep(delay)
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"Unexpected error: {e}")
                    return False
                await asyncio.sleep(2.0)

        return False

    def stop(self):
        self.running = False
