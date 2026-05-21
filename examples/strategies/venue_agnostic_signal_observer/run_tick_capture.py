#!/usr/bin/env python3
"""
Public WebSocket tick-data capture — read-only, no auth, no orders.

This script connects to **public, unauthenticated** WebSocket trade feeds from
supported cryptocurrency venues (Kraken, Coinbase, Binance, OKX, Bybit, Bitfinex) and writes normalised
trade ticks to incremental JSONL files for downstream analysis.

**This is a public data-collection utility only.**
- No API keys are required or accepted.
- No authentication credentials are sent.
- No orders are ever submitted.
- No account or wallet information is accessed.
- There is zero trading, execution, or live-trading code in this module.

Ticks are written incrementally — each incoming trade is appended to the
corresponding venue/symbol JSONL file as soon as it arrives so partial data
is preserved even if the process is interrupted.

Usage examples
--------------
Capture BTC ticks from Kraken and Coinbase for 5 minutes::

    python -m examples.strategies.venue_agnostic_signal_observer.run_tick_capture \
        --venues kraken,coinbase --symbols BTC/USD,BTC-USD --duration-seconds 300

Capture ETH from Binance only::

    python run_tick_capture.py --venues binance --symbols ETH/USD --duration-seconds 60

All captured files land under the output directory::

    data/signal_observer_ticks/
        trades_kraken_BTC-USD_1715577600.jsonl
        trades_coinbase_BTC-USD_1715577600.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import websockets

from . import symbol_aliases
from .tick_models import TradeTickLite


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Venue configuration helpers
# ---------------------------------------------------------------------------


def binance_stream_path(symbol: str) -> str:
    """
    Return the Binance WebSocket stream path for a symbol.

    Normalises common symbol notations into the lowercase ``basequote@trade``
    form that Binance expects, e.g. ``BTC/USD`` → ``btcusd@trade``.
    """
    clean = symbol.replace("/", "").replace("-", "").replace("_", "").lower()
    return f"{clean}@trade"


def coinbase_symbol(symbol: str) -> str:
    """
    Normalise a symbol for Coinbase's product_id format.

    Accepts ``BTC/USD`` → ``BTC-USD`` (identity if already hyphenated).
    """
    return symbol.replace("/", "-").replace("_", "-")


def _normalize_symbol(
    raw_symbol: str,
    venue: str,
    errors: list[str],
    warnings: list[str],
) -> str:
    """
    Resolve a raw venue symbol to canonical ``ASSET/QUOTE`` format.

    Uses ``symbol_aliases.resolve_symbol`` to look up the canonical form.
    If resolution fails, the raw symbol is prefixed with ``UNRESOLVED:`` and
    a warning is logged / recorded.

    Returns the normalised symbol string.
    """
    try:
        canonical = symbol_aliases.resolve_symbol(raw_symbol)
        return f"{canonical.asset}/{canonical.quote}"
    except ValueError as exc:
        msg = f"[{venue}] Unresolved symbol '{raw_symbol}': {exc}"
        logger.warning(msg)
        warnings.append(msg)
        return f"UNRESOLVED:{raw_symbol}"


# ---------------------------------------------------------------------------
# Incremental JSONL writer
# ---------------------------------------------------------------------------


class IncrementalJSONLWriter:
    """
    Append-only JSONL writer that opens and closes per-write for safety.

    Designed for incremental tick capture where data loss on crash must be
    minimised.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def write(self, tick: TradeTickLite) -> None:
        with open(self.path, "a") as fh:
            fh.write(tick.to_json() + "\n")
        self._count += 1

    @property
    def count(self) -> int:
        return self._count


# ---------------------------------------------------------------------------
# Per-venue feed runners
# ---------------------------------------------------------------------------


async def run_binance_feed(
    symbols: list[str],
    writers: dict[str, IncrementalJSONLWriter],
    counts: dict[str, int],
    stats: _CaptureStats | None = None,
    deadline: asyncio.Event | None = None,
    deadline_seconds: float = 300,
    max_retries: int = 3,
) -> None:
    """
    Capture trades from Binance public WebSocket feed.

    Binance does not require a subscription message — the stream path encodes
    the subscription.  Each symbol gets its own stream concatenated into a
    single connection URL.
    """
    retry = 0
    while retry <= max_retries:
        try:
            streams = "/".join(binance_stream_path(s) for s in symbols)
            url = f"wss://stream.binance.com:9443/ws/{streams}"
            logger.info("[binance] connecting to %s", url)

            async with websockets.connect(url) as ws:
                logger.info("[binance] connected")
                retry = 0  # reset on successful connect

                async for msg in ws:
                    _check_deadline(deadline, deadline_seconds)

                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.error(
                            "[binance] JSON decode error: %s", msg[:200]
                        )
                        continue

                    # Binance trade message
                    # e.g. {"e":"trade","s":"BTCUSDT","p":"...","q":"...","T":...,"m":false}
                    if data.get("e") != "trade":
                        continue

                    raw_symbol = data.get("s", "")
                    # Map back to user-supplied symbol
                    user_symbol = _map_binance_symbol(raw_symbol, symbols)
                    if user_symbol is None:
                        continue

                    ts_ms = data.get("T", 0)
                    ts_ns = ts_ms * 1_000_000

                    price = float(data["p"])
                    size = float(data["q"])
                    side = "sell" if data.get("m", False) else "buy"
                    trade_id = str(data.get("t", ""))

                    # Normalize symbol
                    norm_sym = _normalize_symbol(
                        user_symbol, "binance", stats.errors if stats else [], stats.warnings if stats else []
                    )

                    tick = TradeTickLite(
                        ts_event=ts_ns,
                        venue="binance",
                        symbol=norm_sym,
                        price=price,
                        size=size,
                        side=side,
                        trade_id=trade_id,
                        raw=data,
                    )

                    if norm_sym not in writers:
                        writers[norm_sym] = _make_writer("binance", norm_sym)
                        counts[norm_sym] = 0
                    writers[norm_sym].write(tick)
                    counts[norm_sym] += 1

                    # Record stats
                    if stats is not None:
                        stats.record(f"binance|{norm_sym}", ts_ns, price)

        except websockets.ConnectionClosed as exc:
            retry += 1
            if retry > max_retries:
                logger.error(
                    "[binance] connection closed after %d retries: %s",
                    max_retries,
                    exc,
                )
                return
            logger.warning(
                "[binance] connection lost (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

        except TimeoutError as exc:
            retry += 1
            if retry > max_retries:
                logger.error(
                    "[binance] timeout after %d retries: %s", max_retries, exc
                )
                return
            logger.warning(
                "[binance] timeout (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

        except Exception as exc:
            retry += 1
            logger.error(
                "[binance] unexpected error (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if retry > max_retries:
                return
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

    logger.info("[binance] feed finished (max retries exhausted)")


async def run_kraken_feed(
    symbols: list[str],
    writers: dict[str, IncrementalJSONLWriter],
    counts: dict[str, int],
    stats: _CaptureStats | None = None,
    deadline: asyncio.Event | None = None,
    deadline_seconds: float = 300,
    max_retries: int = 3,
) -> None:
    """Capture trades from Kraken public WebSocket feed."""
    retry = 0
    while retry <= max_retries:
        try:
            url = "wss://ws.kraken.com"
            logger.info("[kraken] connecting to %s", url)

            async with websockets.connect(url) as ws:
                logger.info("[kraken] connected")
                retry = 0

                # Build subscription message
                sub_msg = {
                    "event": "subscribe",
                    "pair": symbols,
                    "subscription": {"name": "trade"},
                }
                await ws.send(json.dumps(sub_msg))
                logger.info("[kraken] subscribed to %s", symbols)

                async for msg in ws:
                    _check_deadline(deadline, deadline_seconds)

                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.error(
                            "[kraken] JSON decode error: %s", msg[:200]
                        )
                        continue

                    # Kraken sends heartbeat/subscription-status messages
                    if isinstance(data, dict):
                        event = data.get("event", "")
                        if event in ("heartbeat", "subscriptionStatus"):
                            if event == "subscriptionStatus":
                                status = data.get("status", "")
                                if status == "subscribed":
                                    logger.info(
                                        "[kraken] subscribed to %s",
                                        data.get("pair", ""),
                                    )
                                elif status == "error":
                                    logger.error(
                                        "[kraken] subscription error: %s",
                                        data.get("errorMessage", data),
                                    )
                            continue
                        # Unexpected dict — log and skip
                        logger.debug(
                            "[kraken] unexpected dict msg: %s",
                            json.dumps(data)[:200],
                        )
                        continue

                    # Trade messages come as a list:
                    # [channelID, [trade...], "channelName", "pair"]
                    if not isinstance(data, list):
                        continue

                    if len(data) < 4:
                        continue

                    pair = data[3]
                    trades_data: list[Any] = data[1]

                    for td in trades_data:
                        # td: [price, volume, time, side, orderType, misc]
                        if not isinstance(td, list) or len(td) < 4:
                            continue

                        price_str = td[0]
                        size_str = td[1]
                        ts_str = td[2]
                        side_str = td[3]

                        try:
                            # Kraken timestamps are fractional seconds (string)
                            ts_float = float(ts_str)
                            ts_ns = int(ts_float * 1_000_000_000)
                        except (ValueError, TypeError) as exc:
                            logger.error(
                                "[kraken] timestamp parse error: %s", exc
                            )
                            continue

                        try:
                            price = float(price_str)
                            size = float(size_str)
                        except (ValueError, TypeError) as exc:
                            logger.error(
                                "[kraken] price/size parse error: %s", exc
                            )
                            continue

                        side = "buy" if side_str.startswith("b") else "sell"
                        user_symbol = _map_kraken_symbol(pair, symbols)
                        raw_for_norm = user_symbol if user_symbol else pair

                        # Normalize symbol
                        norm_sym = _normalize_symbol(
                            raw_for_norm, "kraken",
                            stats.errors if stats else [],
                            stats.warnings if stats else [],
                        )

                        tick = TradeTickLite(
                            ts_event=ts_ns,
                            venue="kraken",
                            symbol=norm_sym,
                            price=price,
                            size=size,
                            side=side,
                            trade_id=None,
                            raw={
                                "price": price_str,
                                "volume": size_str,
                                "time": ts_str,
                                "side": side_str,
                            },
                        )

                        if norm_sym not in writers:
                            writers[norm_sym] = _make_writer("kraken", norm_sym)
                            counts[norm_sym] = 0
                        writers[norm_sym].write(tick)
                        counts[norm_sym] += 1

                        # Record stats
                        if stats is not None:
                            stats.record(f"kraken|{norm_sym}", ts_ns, price)

        except websockets.ConnectionClosed as exc:
            retry += 1
            if retry > max_retries:
                logger.error(
                    "[kraken] connection closed after %d retries: %s",
                    max_retries,
                    exc,
                )
                return
            logger.warning(
                "[kraken] connection lost (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

        except TimeoutError as exc:
            retry += 1
            if retry > max_retries:
                logger.error(
                    "[kraken] timeout after %d retries: %s", max_retries, exc
                )
                return
            logger.warning(
                "[kraken] timeout (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

        except Exception as exc:
            retry += 1
            logger.error(
                "[kraken] unexpected error (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if retry > max_retries:
                return
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

    logger.info("[kraken] feed finished (max retries exhausted)")


async def run_coinbase_feed(
    symbols: list[str],
    writers: dict[str, IncrementalJSONLWriter],
    counts: dict[str, int],
    stats: _CaptureStats | None = None,
    deadline: asyncio.Event | None = None,
    deadline_seconds: float = 300,
    max_retries: int = 3,
) -> None:
    """Capture trades from Coinbase public WebSocket feed."""
    retry = 0
    product_ids = [coinbase_symbol(s) for s in symbols]

    while retry <= max_retries:
        try:
            url = "wss://ws-feed.exchange.coinbase.com"
            logger.info("[coinbase] connecting to %s", url)

            async with websockets.connect(url) as ws:
                logger.info("[coinbase] connected")
                retry = 0

                sub_msg = {
                    "type": "subscribe",
                    "product_ids": product_ids,
                    "channels": ["matches"],
                }
                await ws.send(json.dumps(sub_msg))
                logger.info(
                    "[coinbase] subscribed to %s", product_ids
                )

                async for msg in ws:
                    _check_deadline(deadline, deadline_seconds)

                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.error(
                            "[coinbase] JSON decode error: %s", msg[:200]
                        )
                        continue

                    msg_type = data.get("type", "")

                    # Subscription confirmation
                    if msg_type == "subscriptions":
                        logger.info("[coinbase] subscription confirmed: %s", data)
                        continue

                    if msg_type != "match":
                        # heartbeat, error, last_match, etc.
                        if msg_type == "error":
                            logger.error("[coinbase] error: %s", data)
                        continue

                    # match message
                    product_id = data.get("product_id", "")
                    user_symbol = _map_coinbase_symbol(product_id, symbols)

                    # price is string on Coinbase
                    try:
                        price = float(data["price"])
                        size = float(data.get("size", 0))
                    except (KeyError, ValueError, TypeError) as exc:
                        logger.error(
                            "[coinbase] price/size parse error: %s — %s",
                            exc,
                            json.dumps(data)[:200],
                        )
                        continue

                    # Coinbase time is ISO 8601, e.g. "2024-05-12T20:00:00.123456Z"
                    ts_str = data.get("time", "")
                    ts_ns = _parse_iso8601_to_ns(ts_str)
                    if ts_ns == 0:
                        logger.warning(
                            "[coinbase] could not parse timestamp: %s", ts_str
                        )

                    side = data.get("side", "unknown")
                    trade_id = str(data.get("trade_id", ""))

                    # Normalize symbol
                    raw_for_norm = user_symbol if user_symbol else product_id
                    norm_sym = _normalize_symbol(
                        raw_for_norm, "coinbase",
                        stats.errors if stats else [],
                        stats.warnings if stats else [],
                    )

                    tick = TradeTickLite(
                        ts_event=ts_ns,
                        venue="coinbase",
                        symbol=norm_sym,
                        price=price,
                        size=size,
                        side=side,
                        trade_id=trade_id,
                        raw=data,
                    )

                    if norm_sym not in writers:
                        writers[norm_sym] = _make_writer(
                            "coinbase", norm_sym
                        )
                        counts[norm_sym] = 0

                    writers[norm_sym].write(tick)
                    counts[norm_sym] += 1

                    # Record stats
                    if stats is not None:
                        stats.record(f"coinbase|{norm_sym}", ts_ns, price)

        except websockets.ConnectionClosed as exc:
            retry += 1
            if retry > max_retries:
                logger.error(
                    "[coinbase] connection closed after %d retries: %s",
                    max_retries,
                    exc,
                )
                return
            logger.warning(
                "[coinbase] connection lost (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

        except TimeoutError as exc:
            retry += 1
            if retry > max_retries:
                logger.error(
                    "[coinbase] timeout after %d retries: %s", max_retries, exc
                )
                return
            logger.warning(
                "[coinbase] timeout (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

        except Exception as exc:
            retry += 1
            logger.error(
                "[coinbase] unexpected error (retry %d/%d): %s",
                retry,
                max_retries,
                exc,
            )
            if retry > max_retries:
                return
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)

    logger.info("[coinbase] feed finished (max retries exhausted)")


# ---------------------------------------------------------------------------
# OKX feed (public spot trades, no auth)
# ---------------------------------------------------------------------------


def okx_symbol(symbol: str) -> str:
    """Normalise to OKX instId form, e.g. ``BTC/USDT`` -> ``BTC-USDT``."""
    s = symbol.replace("/", "-").replace("_", "-").upper()
    return s


def parse_okx_trade(td: dict, norm_sym: str) -> TradeTickLite:
    """
    Parse a single OKX trade payload dict into a TradeTickLite.

    Expected fields: ``instId``, ``tradeId``, ``px``, ``sz``, ``side``, ``ts`` (ms epoch as str).
    Raises ``KeyError`` / ``ValueError`` on malformed inputs.
    """
    price = float(td["px"])
    size = float(td["sz"])
    ts_ns = int(td["ts"]) * 1_000_000
    return TradeTickLite(
        ts_event=ts_ns,
        venue="okx",
        symbol=norm_sym,
        price=price,
        size=size,
        side=td.get("side", "unknown"),
        trade_id=str(td.get("tradeId", "")),
        raw=td,
    )


async def run_okx_feed(
    symbols: list[str],
    writers: dict[str, IncrementalJSONLWriter],
    counts: dict[str, int],
    stats: _CaptureStats | None = None,
    deadline: asyncio.Event | None = None,
    deadline_seconds: float = 300,
    max_retries: int = 3,
) -> None:
    """Capture trades from OKX public WebSocket feed (no auth)."""
    retry = 0
    inst_ids = [okx_symbol(s) for s in symbols]
    while retry <= max_retries:
        try:
            url = "wss://ws.okx.com:8443/ws/v5/public"
            logger.info("[okx] connecting to %s", url)
            async with websockets.connect(url) as ws:
                logger.info("[okx] connected")
                retry = 0
                sub_msg = {
                    "op": "subscribe",
                    "args": [
                        {"channel": "trades", "instId": iid} for iid in inst_ids
                    ],
                }
                await ws.send(json.dumps(sub_msg))
                logger.info("[okx] subscribed to %s", inst_ids)

                async for msg in ws:
                    _check_deadline(deadline, deadline_seconds)
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.error("[okx] JSON decode error: %s", msg[:200])
                        continue
                    # Subscription confirm / errors
                    if "event" in data:
                        if data.get("event") == "error":
                            logger.error("[okx] sub error: %s", data)
                        continue
                    if data.get("arg", {}).get("channel") != "trades":
                        continue
                    for td in data.get("data", []):
                        inst = td.get("instId", "")
                        user_symbol = _map_okx_symbol(inst, symbols)
                        raw_for_norm = user_symbol if user_symbol else inst
                        norm_sym = _normalize_symbol(
                            raw_for_norm, "okx",
                            stats.errors if stats else [],
                            stats.warnings if stats else [],
                        )
                        try:
                            price = float(td["px"])
                            size = float(td["sz"])
                            ts_ns = int(td["ts"]) * 1_000_000
                        except (KeyError, ValueError, TypeError) as exc:
                            logger.error("[okx] parse error: %s — %s", exc, td)
                            continue
                        side = td.get("side", "unknown")
                        trade_id = str(td.get("tradeId", ""))
                        tick = TradeTickLite(
                            ts_event=ts_ns,
                            venue="okx",
                            symbol=norm_sym,
                            price=price,
                            size=size,
                            side=side,
                            trade_id=trade_id,
                            raw=td,
                        )
                        if norm_sym not in writers:
                            writers[norm_sym] = _make_writer("okx", norm_sym)
                            counts[norm_sym] = 0
                        writers[norm_sym].write(tick)
                        counts[norm_sym] += 1
                        if stats is not None:
                            stats.record(f"okx|{norm_sym}", ts_ns, price)
        except (TimeoutError, websockets.ConnectionClosed) as exc:
            retry += 1
            if stats is not None:
                stats.record_reconnect("okx")
                stats.record_error("okx", exc)
            if retry > max_retries:
                logger.error("[okx] giving up after %d retries: %s", max_retries, exc)
                return
            logger.warning("[okx] reconnect %d/%d: %s", retry, max_retries, exc)
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)
        except Exception as exc:
            retry += 1
            if stats is not None:
                stats.record_reconnect("okx")
                stats.record_error("okx", exc)
            logger.error("[okx] unexpected (retry %d/%d): %s", retry, max_retries, exc)
            if retry > max_retries:
                return
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)
    logger.info("[okx] feed finished (max retries exhausted)")


# ---------------------------------------------------------------------------
# Bybit feed (public spot trades, no auth)
# ---------------------------------------------------------------------------


def bybit_symbol(symbol: str) -> str:
    """Normalise to Bybit symbol form, e.g. ``BTC/USDT`` -> ``BTCUSDT``."""
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper()


def parse_bybit_trade(td: dict, norm_sym: str) -> TradeTickLite:
    """
    Parse a single Bybit publicTrade entry into a TradeTickLite.

    Expected fields: ``T`` (ms epoch), ``s``, ``S`` (Buy/Sell), ``v``, ``p``, ``i``.
    Raises ``KeyError`` / ``ValueError`` on malformed inputs.
    """
    price = float(td["p"])
    size = float(td["v"])
    ts_ns = int(td["T"]) * 1_000_000
    raw_side = td.get("S", "")
    if raw_side.lower().startswith("b"):
        side = "buy"
    elif raw_side.lower().startswith("s"):
        side = "sell"
    else:
        side = "unknown"
    return TradeTickLite(
        ts_event=ts_ns,
        venue="bybit",
        symbol=norm_sym,
        price=price,
        size=size,
        side=side,
        trade_id=str(td.get("i", "")),
        raw=td,
    )


async def run_bybit_feed(
    symbols: list[str],
    writers: dict[str, IncrementalJSONLWriter],
    counts: dict[str, int],
    stats: _CaptureStats | None = None,
    deadline: asyncio.Event | None = None,
    deadline_seconds: float = 300,
    max_retries: int = 3,
) -> None:
    """Capture trades from Bybit public WebSocket feed (no auth)."""
    retry = 0
    bsyms = [bybit_symbol(s) for s in symbols]
    topics = [f"publicTrade.{s}" for s in bsyms]
    while retry <= max_retries:
        try:
            url = "wss://stream.bybit.com/v5/public/spot"
            logger.info("[bybit] connecting to %s", url)
            async with websockets.connect(url) as ws:
                logger.info("[bybit] connected")
                retry = 0
                sub_msg = {"op": "subscribe", "args": topics}
                await ws.send(json.dumps(sub_msg))
                logger.info("[bybit] subscribed to %s", topics)

                async for msg in ws:
                    _check_deadline(deadline, deadline_seconds)
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.error("[bybit] JSON decode error: %s", msg[:200])
                        continue
                    if data.get("op") == "subscribe":
                        if not data.get("success", True):
                            logger.error("[bybit] sub failure: %s", data)
                        continue
                    topic = data.get("topic", "")
                    if not topic.startswith("publicTrade."):
                        continue
                    raw_sym = topic.split(".", 1)[1]
                    user_symbol = _map_bybit_symbol(raw_sym, symbols)
                    raw_for_norm = user_symbol if user_symbol else raw_sym
                    norm_sym = _normalize_symbol(
                        raw_for_norm, "bybit",
                        stats.errors if stats else [],
                        stats.warnings if stats else [],
                    )
                    for td in data.get("data", []):
                        try:
                            price = float(td["p"])
                            size = float(td["v"])
                            ts_ns = int(td["T"]) * 1_000_000
                        except (KeyError, ValueError, TypeError) as exc:
                            logger.error("[bybit] parse error: %s — %s", exc, td)
                            continue
                        raw_side = td.get("S", "")
                        side = "buy" if raw_side.lower().startswith("b") else "sell" if raw_side.lower().startswith("s") else "unknown"
                        trade_id = str(td.get("i", ""))
                        tick = TradeTickLite(
                            ts_event=ts_ns,
                            venue="bybit",
                            symbol=norm_sym,
                            price=price,
                            size=size,
                            side=side,
                            trade_id=trade_id,
                            raw=td,
                        )
                        if norm_sym not in writers:
                            writers[norm_sym] = _make_writer("bybit", norm_sym)
                            counts[norm_sym] = 0
                        writers[norm_sym].write(tick)
                        counts[norm_sym] += 1
                        if stats is not None:
                            stats.record(f"bybit|{norm_sym}", ts_ns, price)
        except (TimeoutError, websockets.ConnectionClosed) as exc:
            retry += 1
            if stats is not None:
                stats.record_reconnect("bybit")
                stats.record_error("bybit", exc)
            if retry > max_retries:
                logger.error("[bybit] giving up after %d retries: %s", max_retries, exc)
                return
            logger.warning("[bybit] reconnect %d/%d: %s", retry, max_retries, exc)
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)
        except Exception as exc:
            retry += 1
            if stats is not None:
                stats.record_reconnect("bybit")
                stats.record_error("bybit", exc)
            logger.error("[bybit] unexpected (retry %d/%d): %s", retry, max_retries, exc)
            if retry > max_retries:
                return
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)
    logger.info("[bybit] feed finished (max retries exhausted)")


def _map_okx_symbol(inst: str, symbols: list[str]) -> str | None:
    if inst in symbols:
        return inst
    for s in symbols:
        if okx_symbol(s) == inst:
            return s
    return None


def _map_bybit_symbol(raw: str, symbols: list[str]) -> str | None:
    if raw in symbols:
        return raw
    for s in symbols:
        if bybit_symbol(s) == raw:
            return s
    return None


# ---------------------------------------------------------------------------
# Bitfinex feed (public spot trades, no auth)
# ---------------------------------------------------------------------------


def bitfinex_symbol(symbol: str) -> str:
    """
    Map a user symbol to a Bitfinex public-trades ws symbol.

    Examples:
        ``BTC/USD`` -> ``tBTCUSD``
        ``BTC/UST`` -> ``tBTCUST``
        ``LINK/USD`` -> ``tLINK:USD``
        ``LINK:UST`` -> ``tLINK:UST``
        ``tBTCUSD`` (already prefixed) -> identity.
    """
    s = symbol.strip()
    if s.startswith("t") and (len(s) > 1) and s[1].isupper():
        # Already a Bitfinex ws symbol
        return s
    # Normalise separators: prefer to preserve colon if present, else strip "/"
    if "/" in s:
        base, quote = s.split("/", 1)
    elif ":" in s:
        base, quote = s.split(":", 1)
    else:
        # Heuristic: trailing 3-char quote (USD, UST, EUR, BTC, ETH)
        for q in ("USDT", "USDC", "USD", "UST", "EUR", "BTC", "ETH"):
            if s.upper().endswith(q):
                base = s[: -len(q)]
                quote = q
                break
        else:
            base, quote = s, ""
    base = base.upper()
    quote = quote.upper()
    if len(base) > 3:
        return f"t{base}:{quote}"
    return f"t{base}{quote}"


def _map_bitfinex_symbol(raw: str, symbols: list[str]) -> str | None:
    """Map a Bitfinex ws symbol (e.g. ``tBTCUSD``) back to a user-supplied symbol."""
    if raw in symbols:
        return raw
    for s in symbols:
        if bitfinex_symbol(s) == raw:
            return s
    # Strip leading 't' and try matching plain forms
    bare = raw.removeprefix("t")
    for s in symbols:
        if s.replace("/", "").replace(":", "").upper() == bare.replace(":", "").upper():
            return s
    return None


def parse_bitfinex_trade(td: list, norm_sym: str) -> TradeTickLite:
    """
    Parse a Bitfinex trade payload (``[ID, MTS, AMOUNT, PRICE]``) into a TradeTickLite.

    Side is inferred from the sign of AMOUNT (positive=buy, negative=sell).
    Raises ``ValueError``/``IndexError``/``TypeError`` on malformed inputs.
    """
    if not isinstance(td, list) or len(td) < 4:
        raise ValueError(f"bitfinex trade payload too short: {td!r}")
    trade_id = td[0]
    mts = int(td[1])
    amount = float(td[2])
    price = float(td[3])
    side = "buy" if amount > 0 else "sell" if amount < 0 else "unknown"
    return TradeTickLite(
        ts_event=mts * 1_000_000,
        venue="bitfinex",
        symbol=norm_sym,
        price=price,
        size=abs(amount),
        side=side,
        trade_id=str(trade_id),
        raw={"id": trade_id, "mts": mts, "amount": amount, "price": price},
    )


async def run_bitfinex_feed(
    symbols: list[str],
    writers: dict[str, IncrementalJSONLWriter],
    counts: dict[str, int],
    stats: _CaptureStats | None = None,
    deadline: asyncio.Event | None = None,
    deadline_seconds: float = 300,
    max_retries: int = 3,
) -> None:
    """
    Capture trades from Bitfinex public WebSocket feed (no auth).

    Bitfinex requires one subscribe message per symbol on a shared connection,
    and assigns a per-subscription ``chanId`` that must be tracked to demux trade
    messages back to symbols.
    """
    retry = 0
    bfx_syms = [bitfinex_symbol(s) for s in symbols]
    while retry <= max_retries:
        chan_to_sym: dict[int, str] = {}
        try:
            url = "wss://api-pub.bitfinex.com/ws/2"
            logger.info("[bitfinex] connecting to %s", url)
            async with websockets.connect(url) as ws:
                logger.info("[bitfinex] connected")
                retry = 0
                for bfx in bfx_syms:
                    sub_msg = {
                        "event": "subscribe",
                        "channel": "trades",
                        "symbol": bfx,
                    }
                    await ws.send(json.dumps(sub_msg))
                logger.info("[bitfinex] subscribed to %s", bfx_syms)

                async for msg in ws:
                    _check_deadline(deadline, deadline_seconds)
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.error("[bitfinex] JSON decode error: %s", msg[:200])
                        continue
                    if isinstance(data, dict):
                        evt = data.get("event", "")
                        if evt == "subscribed":
                            chan_id = data.get("chanId")
                            sym = data.get("symbol", "")
                            if isinstance(chan_id, int) and sym:
                                chan_to_sym[chan_id] = sym
                                logger.info(
                                    "[bitfinex] chan %d -> %s", chan_id, sym
                                )
                        elif evt == "error":
                            logger.error("[bitfinex] sub error: %s", data)
                        continue
                    if not isinstance(data, list) or len(data) < 2:
                        continue
                    chan_id = data[0]
                    payload = data[1]
                    # Heartbeat
                    if payload == "hb":
                        continue
                    raw_sym = chan_to_sym.get(chan_id, "")
                    if not raw_sym:
                        continue
                    user_symbol = _map_bitfinex_symbol(raw_sym, symbols)
                    raw_for_norm = user_symbol if user_symbol else raw_sym
                    # Normalise: strip leading 't' for alias resolution
                    if raw_for_norm.startswith("t") and len(raw_for_norm) > 1 and raw_for_norm[1].isupper():
                        raw_for_norm = raw_for_norm[1:]
                    norm_sym = _normalize_symbol(
                        raw_for_norm, "bitfinex",
                        stats.errors if stats else [],
                        stats.warnings if stats else [],
                    )
                    # Snapshot: [chan, [[id,mts,amt,price], ...]]
                    # Update: [chan, "te"|"tu", [id,mts,amt,price]]
                    trades: list = []
                    if isinstance(payload, list) and payload and isinstance(payload[0], list):
                        trades = payload  # snapshot
                    elif payload in ("te", "tu") and len(data) >= 3 and isinstance(data[2], list):
                        if payload == "tu":
                            # Skip duplicate "trade update" — "te" is the executed event
                            continue
                        trades = [data[2]]
                    else:
                        continue
                    for td in trades:
                        try:
                            tick = parse_bitfinex_trade(td, norm_sym)
                        except (ValueError, IndexError, TypeError) as exc:
                            logger.error("[bitfinex] parse error: %s — %s", exc, td)
                            continue
                        if norm_sym not in writers:
                            writers[norm_sym] = _make_writer("bitfinex", norm_sym)
                            counts[norm_sym] = 0
                        writers[norm_sym].write(tick)
                        counts[norm_sym] += 1
                        if stats is not None:
                            stats.record(f"bitfinex|{norm_sym}", tick.ts_event, tick.price)
        except (TimeoutError, websockets.ConnectionClosed) as exc:
            retry += 1
            if stats is not None:
                stats.record_reconnect("bitfinex")
                stats.record_error("bitfinex", exc)
            if retry > max_retries:
                logger.error("[bitfinex] giving up after %d retries: %s", max_retries, exc)
                return
            logger.warning("[bitfinex] reconnect %d/%d: %s", retry, max_retries, exc)
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)
        except Exception as exc:
            retry += 1
            if stats is not None:
                stats.record_reconnect("bitfinex")
                stats.record_error("bitfinex", exc)
            logger.error("[bitfinex] unexpected (retry %d/%d): %s", retry, max_retries, exc)
            if retry > max_retries:
                return
            if not deadline or not deadline.is_set():
                await asyncio.sleep(5)
    logger.info("[bitfinex] feed finished (max retries exhausted)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FEED_TASKS: dict[str, Any] = {
    "binance": run_binance_feed,
    "kraken": run_kraken_feed,
    "coinbase": run_coinbase_feed,
    "okx": run_okx_feed,
    "bybit": run_bybit_feed,
    "bitfinex": run_bitfinex_feed,
}


def _make_writer(venue: str, symbol: str) -> IncrementalJSONLWriter:
    ts = int(time.time())
    # Sanitise symbol for filename
    safe_sym = symbol.replace("/", "-").replace(" ", "_")
    fname = f"trades_{venue}_{safe_sym}_{ts}.jsonl"
    return IncrementalJSONLWriter(Path(out_dir) / fname)


def _check_deadline(deadline: asyncio.Event | None, duration: float) -> None:
    """Raise SystemExit if the capture duration has elapsed."""
    if deadline and deadline.is_set():
        raise SystemExit("deadline reached")


def _binance_raw_to_user(raw_symbol: str) -> str:
    """Attempt to match a Binance raw symbol (e.g. BTCUSDT) to a user symbol."""
    clean = raw_symbol.upper()
    # Try common quote currencies
    for quote in ("USDT", "USD", "BUSD", "USDC", "EUR", "GBP"):
        if clean.endswith(quote):
            base = clean[: -len(quote)]
            return f"{base}/{quote}"
    return f"{raw_symbol}/USD"


def _map_binance_symbol(raw: str, symbols: list[str]) -> str | None:
    """Map a Binance raw symbol back to the closest user-supplied symbol."""
    # Direct match
    if raw in symbols:
        return raw

    # Normalised matching
    user_raw_map: dict[str, str] = {}
    for s in symbols:
        nr = s.replace("/", "").replace("-", "").replace("_", "").lower()
        user_raw_map[nr] = s

    clean_raw = raw.replace("/", "").replace("-", "").replace("_", "").lower()
    if clean_raw in user_raw_map:
        return user_raw_map[clean_raw]

    # Fallback: derive from raw
    return _binance_raw_to_user(raw)


def _map_kraken_symbol(pair: str, symbols: list[str]) -> str | None:
    """Map a Kraken pair string to the user-supplied symbol."""
    if pair in symbols:
        return pair
    for s in symbols:
        if s.replace("/", "") == pair.replace("/", ""):
            return s
    return None


def _map_coinbase_symbol(product_id: str, symbols: list[str]) -> str | None:
    """Map a Coinbase product_id to a user-supplied symbol."""
    if product_id in symbols:
        return product_id
    for s in symbols:
        if coinbase_symbol(s) == product_id:
            return s
    return None


def _parse_iso8601_to_ns(ts_str: str) -> int:
    """Parse an ISO 8601 timestamp to nanosecond Unix epoch."""
    if not ts_str:
        return 0
    try:
        # Handle formats like "2024-05-12T20:00:00.123456Z"
        ts_str_clean = ts_str.replace("Z", "+00:00")
        from datetime import datetime

        dt = datetime.fromisoformat(ts_str_clean)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception as exc:
        logger.warning("failed to parse ISO 8601 '%s': %s", ts_str, exc)
        return 0


@dataclass
class _CaptureStats:
    """Mutable capture statistics used to build the end-of-run manifest."""

    tick_counts: dict[str, int]  # key: "venue|symbol"
    first_tick_ts: dict[str, int]  # first ns epoch per key
    last_tick_ts: dict[str, int]  # last ns epoch per key
    price_min: dict[str, float]
    price_max: dict[str, float]
    errors: list[str]
    warnings: list[str]
    reconnect_count: dict[str, int] = field(default_factory=dict)  # key: venue
    error_summary: dict[str, int] = field(default_factory=dict)  # key: "venue:exc_type"

    def record_reconnect(self, venue: str) -> None:
        self.reconnect_count[venue] = self.reconnect_count.get(venue, 0) + 1

    def record_error(self, venue: str, exc: BaseException) -> None:
        key = f"{venue}:{type(exc).__name__}"
        self.error_summary[key] = self.error_summary.get(key, 0) + 1

    def record(self, key: str, ts_ns: int, price: float) -> None:
        """Update stats for a single tick."""
        self.tick_counts[key] = self.tick_counts.get(key, 0) + 1
        if key not in self.first_tick_ts or ts_ns < self.first_tick_ts[key]:
            self.first_tick_ts[key] = ts_ns
        if key not in self.last_tick_ts or ts_ns > self.last_tick_ts[key]:
            self.last_tick_ts[key] = ts_ns
        if key not in self.price_min or price < self.price_min[key]:
            self.price_min[key] = price
        if key not in self.price_max or price > self.price_max[key]:
            self.price_max[key] = price


def _write_manifest(
    stats: _CaptureStats,
    run_start_utc: str,
    run_end_utc: str,
    duration_seconds: float,
    venues: list[str],
    requested_symbols: list[str],
    files_written: list[str],
    out_dir: str,
) -> None:
    """Write capture_manifest.json to the output directory."""
    move_bps: dict[str, float] = {}
    for key, pmin in stats.price_min.items():
        pmax = stats.price_max.get(key, pmin)
        if pmin > 0:
            move_bps[key] = (pmax - pmin) / pmin * 10000
        else:
            move_bps[key] = 0.0

    # ------------------------------------------------------------------
    # Per-stream status
    # ------------------------------------------------------------------
    # NOTE on overlap semantics (2026-05-16):
    # With 5-6 venues active, a global "all streams co-alive" window collapses
    # to the slowest-to-start / earliest-to-die stream and is diagnostic only.
    # The analytically-relevant overlap for cross-asset / cross-venue signals
    # is *pairwise*: source-stream <-> target-stream. We emit per-stream
    # uptime, pairwise overlap, and per-canonical-asset cross-venue overlap.
    # The global overlap window is retained as a coarse diagnostic.

    run_duration_ns = int(duration_seconds * 1_000_000_000)
    streams: dict[str, dict[str, Any]] = {}
    for key, count in stats.tick_counts.items():
        first = stats.first_tick_ts.get(key, 0)
        last = stats.last_tick_ts.get(key, 0)
        status = "ok" if count > 0 else "zero_ticks"
        if first > 0 and last >= first and run_duration_ns > 0:
            uptime_fraction = min(1.0, (last - first) / run_duration_ns)
        else:
            uptime_fraction = 0.0
        streams[key] = {
            "status": status,
            "tick_count": count,
            "first_ts_ns": first,
            "last_ts_ns": last,
            "uptime_ns": max(0, last - first) if first > 0 and last >= first else 0,
            "uptime_fraction": round(uptime_fraction, 4),
        }

    # ------------------------------------------------------------------
    # Pairwise overlap (the analytically-relevant metric)
    # ------------------------------------------------------------------
    live_keys = [k for k, v in streams.items() if v["status"] == "ok"]
    pairwise: dict[str, dict[str, int]] = {}
    for i, a in enumerate(live_keys):
        for b in live_keys[i + 1:]:
            fa, la = streams[a]["first_ts_ns"], streams[a]["last_ts_ns"]
            fb, lb = streams[b]["first_ts_ns"], streams[b]["last_ts_ns"]
            ov_start = max(fa, fb)
            ov_end = min(la, lb)
            dur = max(0, ov_end - ov_start)
            pair_key = f"{a}||{b}"
            pairwise[pair_key] = {
                "start_ns": ov_start if dur > 0 else 0,
                "end_ns": ov_end if dur > 0 else 0,
                "duration_ns": dur,
            }

    # ------------------------------------------------------------------
    # Per-canonical-asset cross-venue overlap
    # For each canonical asset, intersect the windows of all live streams
    # carrying that asset. This is "how long were N venues co-alive for X".
    # ------------------------------------------------------------------
    by_asset: dict[str, list[str]] = {}
    for k in live_keys:
        # key form: "venue|ASSET/QUOTE" or "venue|UNRESOLVED:..."
        sym_part = k.split("|", 1)[1] if "|" in k else ""
        asset = sym_part.split("/", 1)[0] if "/" in sym_part else sym_part
        if not asset or asset.startswith("UNRESOLVED:"):
            continue
        by_asset.setdefault(asset, []).append(k)

    per_asset_overlap: dict[str, dict[str, Any]] = {}
    for asset, keys in by_asset.items():
        firsts = [streams[k]["first_ts_ns"] for k in keys]
        lasts = [streams[k]["last_ts_ns"] for k in keys]
        ov_start = max(firsts)
        ov_end = min(lasts)
        dur = max(0, ov_end - ov_start)
        per_asset_overlap[asset] = {
            "venues_alive": len(keys),
            "stream_keys": sorted(keys),
            "start_ns": ov_start if dur > 0 else 0,
            "end_ns": ov_end if dur > 0 else 0,
            "duration_ns": dur,
        }

    # ------------------------------------------------------------------
    # Global overlap — retained as a coarse diagnostic only
    # ------------------------------------------------------------------
    all_firsts = [v for v in stats.first_tick_ts.values() if v > 0]
    all_lasts = [v for v in stats.last_tick_ts.values() if v > 0]
    g_start = max(all_firsts) if all_firsts else 0
    g_end = min(all_lasts) if all_lasts else 0
    g_dur = max(0, g_end - g_start) if all_firsts and all_lasts else 0

    zero_tick_streams = [k for k, v in streams.items() if v["status"] == "zero_ticks"]

    manifest = {
        "run_start_utc": run_start_utc,
        "run_end_utc": run_end_utc,
        "duration_seconds": round(duration_seconds, 3),
        "venues": venues,
        "requested_symbols": requested_symbols,
        "files_written": files_written,
        "tick_counts": stats.tick_counts,
        "first_tick_ts": stats.first_tick_ts,
        "last_tick_ts": stats.last_tick_ts,
        "price_min": stats.price_min,
        "price_max": stats.price_max,
        "price_move_bps": move_bps,
        "errors": stats.errors,
        "warnings": stats.warnings,
        "streams": streams,
        "reconnect_count": stats.reconnect_count,
        "error_summary": stats.error_summary,
        "zero_tick_streams": zero_tick_streams,
        # Analytically-relevant overlap metrics for cross-asset/cross-venue signals.
        "pairwise_overlap_ns": pairwise,
        "per_asset_overlap_ns": per_asset_overlap,
        # Diagnostic only — collapses under many streams; do not gate on this.
        "overlap_window_ns": {
            "start": g_start,
            "end": g_end,
            "duration_ns": g_dur,
            "note": "diagnostic only; use pairwise_overlap_ns / per_asset_overlap_ns for signal evaluation",
        },
    }

    manifest_path = Path(out_dir) / "capture_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    logger.info("Manifest written to %s", manifest_path)


def _print_diagnostics(
    stats: _CaptureStats,
    venues: list[str],
    elapsed: float,
) -> None:
    """Print a summary table of capture results."""
    print()
    print("=" * 92)
    print(f"  Tick capture complete — elapsed={elapsed:.1f}s")
    print("=" * 92)
    print(
        f"  {'Venue':>12}  {'Symbol':>16}  {'Count':>8}  "
        f"{'First tick (ns)':>20}  {'Last tick (ns)':>20}  "
        f"{'Price min':>12}  {'Price max':>12}  {'Move (bps)':>10}"
    )
    print("-" * 92)

    total = 0
    for venue in venues:
        for key, count in sorted(stats.tick_counts.items()):
            if not key.startswith(f"{venue}|"):
                continue
            sym = key.split("|", 1)[1]
            first = stats.first_tick_ts.get(key, 0)
            last = stats.last_tick_ts.get(key, 0)
            pmin = stats.price_min.get(key, 0.0)
            pmax = stats.price_max.get(key, 0.0)
            move = 0.0
            if pmin > 0:
                move = (pmax - pmin) / pmin * 10000
            total += count
            print(
                f"  {venue:>12}  {sym:>16}  {count:>8,}  "
                f"{first:>20,}  {last:>20,}  "
                f"{pmin:12.4f}  {pmax:12.4f}  {move:10.2f}"
            )

    print("-" * 92)
    print(f"  {'TOTAL':>12}  {'':>16}  {total:>8,} ticks")
    print("=" * 92)

    if stats.errors:
        print(f"\n  Errors ({len(stats.errors)}):")
        for e in stats.errors:
            print(f"    - {e}")
    if stats.warnings:
        print(f"\n  Warnings ({len(stats.warnings)}):")
        seen: set[str] = set()
        for w in stats.warnings:
            if w not in seen:
                print(f"    - {w}")
                seen.add(w)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _main(args: argparse.Namespace) -> None:
    global out_dir
    out_dir = args.out

    venues = [v.strip().lower() for v in args.venues.split(",")]
    symbols = [s.strip() for s in args.symbols.split(",")]
    duration = args.duration_seconds

    # Validate venues
    for v in venues:
        if v not in _FEED_TASKS:
            logger.error(
                "unknown venue '%s' — supported: %s",
                v,
                ", ".join(sorted(_FEED_TASKS.keys())),
            )
            raise SystemExit(1)

    # Global capture stats for manifest + diagnostics
    stats = _CaptureStats(
        tick_counts={},
        first_tick_ts={},
        last_tick_ts={},
        price_min={},
        price_max={},
        errors=[],
        warnings=[],
    )

    # Prepare writers and counters per venue
    # Each venue gets its own set of writers keyed by (normalised) symbol
    venue_writers: dict[str, dict[str, IncrementalJSONLWriter]] = defaultdict(dict)
    venue_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # Pre-create writers for all venue/symbol combos using normalised keys
    for venue in venues:
        for sym in symbols:
            norm = _normalize_symbol(sym, venue, stats.errors, stats.warnings)
            list(stats.errors)
            list(stats.warnings)
            # Only create writer once per normalised symbol
            if norm not in venue_writers[venue]:
                venue_writers[venue][norm] = _make_writer(venue, norm)
                venue_counts[venue][norm] = 0
                # Seed stats keys so they appear in diagnostics even with 0 ticks
                stats_key = f"{venue}|{norm}"
                if stats_key not in stats.tick_counts:
                    stats.tick_counts[stats_key] = 0

    deadline = asyncio.Event()
    start_time = time.monotonic()
    run_start_utc = datetime.now(tz=UTC).isoformat()

    # Deadline watchdog
    async def _deadline_watch() -> None:
        await asyncio.sleep(duration)
        logger.info("duration reached (%ds) — signalling stop", duration)
        deadline.set()

    tasks: list[asyncio.Task] = [asyncio.create_task(_deadline_watch())]

    for venue in venues:
        runner = _FEED_TASKS[venue]
        task = asyncio.create_task(
            runner(
                symbols=symbols,
                writers=venue_writers[venue],
                counts=venue_counts[venue],
                stats=stats,
                deadline=deadline,
                deadline_seconds=duration,
                max_retries=3,
            )
        )
        tasks.append(task)

    logger.info(
        "started %d venue feed(s), duration=%ds — waiting...",
        len(venues),
        duration,
    )

    # Wait for deadline, then cancel remaining feed tasks
    done, pending = await asyncio.wait(
        tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # The deadline task should be the one that completed
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t

    run_end_utc = datetime.now(tz=UTC).isoformat()
    elapsed = time.monotonic() - start_time

    # Explicit warnings for zero-tick streams and subscription failures
    for key, count in stats.tick_counts.items():
        if count == 0:
            venue_part = key.split("|")[0]
            sym_part = key.split("|")[1]
            warn_msg = f"ZERO TICKS: {venue_part} {sym_part} — stream produced no ticks during capture window (possible subscription failure or dead product)"
            if warn_msg not in stats.warnings:
                stats.warnings.append(warn_msg)

    # Collect relative paths of written files
    files_written: list[str] = []
    for venue in venues:
        for sym, writer in venue_writers[venue].items():
            if writer.count > 0:
                try:
                    files_written.append(str(writer.path.relative_to(Path("."))))
                except ValueError:
                    files_written.append(str(writer.path))

    # Write manifest
    _write_manifest(
        stats=stats,
        run_start_utc=run_start_utc,
        run_end_utc=run_end_utc,
        duration_seconds=elapsed,
        venues=venues,
        requested_symbols=symbols,
        files_written=files_written,
        out_dir=out_dir,
    )

    # Print diagnostics
    _print_diagnostics(stats, venues, elapsed)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the tick capture script."""
    parser = argparse.ArgumentParser(
        description="Capture public trade ticks from WebSocket feeds."
    )
    parser.add_argument(
        "--venues",
        type=str,
        default="kraken,coinbase",
        help="Comma-separated venue names (default: kraken,coinbase)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="BTC/USD,ETH/USD",
        help="Comma-separated symbols (default: BTC/USD,ETH/USD)",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=300,
        help="Capture duration in seconds (default: 300)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="data/signal_observer_ticks",
        help="Output directory for JSONL files and manifest (default: data/signal_observer_ticks)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        logger.info("interrupted by user")
    except Exception as exc:
        logger.error("fatal error: %s", exc, exc_info=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
