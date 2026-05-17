#!/usr/bin/env python3
"""Combined async capture runner for derivatives-source -> spot-target research.

Starts Binance USD-M perp, Kraken spot, Coinbase spot, and optional OI polling
in one event loop. Writes per-stream JSONL files and a capture_manifest.json
proving true timestamp overlap between source and target streams.

Public-data observer only. No auth, no orders, no private keys, no execution.

OI rate-limit budget:
  openInterest endpoint costs low request weight.
  At 3 symbols x 5-second interval = 36 requests/minute.
  Binance rate limit is 2400 weight/minute. This is intentionally conservative.
  Future symbol expansion must keep polling bounded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .tick_models import TradeTickLite
from .symbol_aliases import resolve_symbol
from .artifact_metadata import inject_metadata_into_manifest
from .run_artifacts import (
    atomic_write_json,
    create_run_id,
    safe_output_dir,
)
from .run_index import append_run_index_row, build_run_index_row

_MS_TO_NS = 1_000_000

# WebSocket endpoint constants — testable and visible.
# Binance USD-M futures uses the /market routed path for combined streams.
# The unrouted /stream endpoint may connect but will not push data.
BINANCE_PERP_WS_BASE = "wss://fstream.binance.com/market/stream"
# Kraken WebSocket v2 API (v2 subscription format required).
KRAKEN_WS_URL = "wss://ws.kraken.com/v2"


def _ts_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ts_ns_to_iso(ts_ns: int) -> str:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _normalize_kraken_symbol(raw: str) -> str:
    """Map Kraken venue symbols to our canonical form."""
    raw_upper = raw.upper()
    if raw_upper in ("XBT/USD", "XBTUSD"):
        return "BTC/USD"
    if raw_upper in ("XETH/USD", "ETH/USD"):
        return "ETH/USD"
    if raw_upper in ("SOLUSD", "SOL/USD"):
        return "SOL/USD"
    # Try via alias registry
    try:
        canon = resolve_symbol(raw)
        return f"{canon.asset}/{canon.quote}"
    except ValueError:
        return raw


class StreamStats:
    """Track per-stream metadata."""

    def __init__(self, name: str):
        self.name = name
        self.start_time: str | None = None
        self.end_time: str | None = None
        self.tick_count: int = 0
        self.first_tick_ts: int | None = None
        self.last_tick_ts: int | None = None
        self.status: str = "missing"
        self.reconnect_count: int = 0
        self.error_summary: str | None = None
        self.diagnostics: list[str] = []  # Preflight diagnostic messages

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "tick_count": self.tick_count,
            "first_tick_ts": _ts_ns_to_iso(self.first_tick_ts) if self.first_tick_ts else None,
            "first_tick_ts_ns": self.first_tick_ts,
            "last_tick_ts": _ts_ns_to_iso(self.last_tick_ts) if self.last_tick_ts else None,
            "last_tick_ts_ns": self.last_tick_ts,
            "status": self.status,
            "reconnect_count": self.reconnect_count,
            "error_summary": self.error_summary,
            "diagnostics": self.diagnostics,
        }


# ---------------------------------------------------------------------------
# WebSocket connect abstraction
# ---------------------------------------------------------------------------

async def _ws_connect(url: str, max_reconnects: int = 5, name: str = "ws"):
    """Connect to a WebSocket URL. Tries websockets first, then aiohttp."""
    for attempt in range(max_reconnects + 1):
        try:
            import websockets
            ws = await asyncio.wait_for(websockets.connect(url), timeout=10)
            print(f"  [{name}] Connected via websockets (attempt {attempt + 1})")
            return ws, "websockets"
        except ImportError:
            break
        except asyncio.TimeoutError:
            print(f"  [{name}] Timeout attempt {attempt + 1}")
            await asyncio.sleep(min(2 ** attempt, 30))
        except Exception as e:
            print(f"  [{name}] Error attempt {attempt + 1}: {e}")
            await asyncio.sleep(min(2 ** attempt, 30))

    try:
        import aiohttp
        session = aiohttp.ClientSession()
        ws = await asyncio.wait_for(
            session.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_receive=10, ws_close=10)),
            timeout=10,
        )
        # Stash session on ws so _ws_close can clean it up — closing the
        # WebSocket alone does NOT free the underlying ClientSession.
        ws._aiohttp_session = session  # track for cleanup in _ws_close
        print(f"  [{name}] Connected via aiohttp")
        return ws, "aiohttp"
    except ImportError:
        pass

    raise RuntimeError(f"Cannot connect to {url}: neither 'websockets' nor 'aiohttp' available")


async def _ws_recv(ws, backend: str, timeout: float = 2.0) -> str | None:
    """Receive a string message from a WebSocket, with timeout."""
    try:
        if backend == "websockets":
            return await asyncio.wait_for(ws.recv(), timeout=timeout)
        else:
            msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
            return msg.data
    except asyncio.TimeoutError:
        return None
    except Exception:
        raise


async def _ws_send(ws, backend: str, data: str) -> None:
    """Send a string message to a WebSocket."""
    if backend == "websockets":
        await ws.send(data)
    else:
        await ws.send_str(data)


async def _ws_close(ws, backend: str) -> None:
    """Close a WebSocket connection."""
    try:
        if backend == "websockets":
            await ws.close()
        else:
            await ws.close()
            # Clean up the aiohttp session we stashed during connect
            session = getattr(ws, "_aiohttp_session", None)
            if session is not None and not session.closed:
                await session.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bounded reconnect wrapper for capture loops
# ---------------------------------------------------------------------------

_RECONNECT_MAX_ATTEMPTS = 5
_RECONNECT_BUDGET_S = 30


async def _with_reconnect_loop(
    url: str,
    name: str,
    on_connect,
    on_message,
    duration_seconds: int,
    stop_event: asyncio.Event,
    stats: dict[str, StreamStats] | None = None,
    stat_keys: list[str] | None = None,
) -> str | None:
    """Run a capture loop with bounded reconnect on socket failures.

    Parameters
    ----------
    url : str
        WebSocket endpoint.
    name : str
        Label for log output (e.g. \"BINANCE_PERP\").
    on_connect : async callable(ws, backend)
        Called after each successful connect (or reconnect) to perform
        venue-specific setup — usually subscription messages.  Called as
        ``await on_connect(ws, backend)``.  May be ``None``.
    on_message : async callable(msg_dict, ws, backend) -> bool
        Called for each parsed message.  Returns ``True`` to continue,
        ``False`` to stop the loop immediately (e.g. parse error that
        indicates an unrecoverable stream state).
    duration_seconds : int
        How long to capture total.
    stop_event : asyncio.Event
        External stop signal.
    stats : dict, optional
        StreamStats dict — updated with reconnect_count on each recovery.
    stat_keys : list, optional
        Which stat entries to increment reconnect_count on.

    Returns
    -------
    str | None
        ``None`` on normal exit, or a short reason string on unrecoverable
        failure (e.g. \"reconnect_budget_exceeded\").
    """
    deadline = time.time() + duration_seconds
    ws: object | None = None
    backend: str = ""
    reason: str | None = None

    # --- initial connect -------------------------------------------------
    connect_start = time.monotonic()
    for attempt in range(_RECONNECT_MAX_ATTEMPTS + 1):
        try:
            ws, backend = await _ws_connect(
                url, max_reconnects=0, name=name
            )
            connect_start = time.monotonic()
            break
        except Exception as exc:
            if time.monotonic() - connect_start > _RECONNECT_BUDGET_S:
                reason = f"connect_budget_exceeded ({exc})"
                ws = None
                break
            print(f"  [{name}] Connect attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(min(2 ** attempt, 10))
    else:
        reason = "initial_connect_failed"
        ws = None

    if reason:
        print(f"  [{name}] {reason}")
        return reason

    # --- subscribe if callable provided ----------------------------------
    try:
        if on_connect is not None:
            await on_connect(ws, backend)
    except Exception as exc:
        reason = f"subscribe_error: {exc}"
        print(f"  [{name}] {reason}")
        try:
            await _ws_close(ws, backend)
        except Exception:
            pass
        return reason

    # --- main receive loop with reconnect --------------------------------
    while time.time() < deadline and not stop_event.is_set():
        remaining = min(2.0, max(0.0, deadline - time.time()))
        try:
            msg = await _ws_recv(ws, backend, timeout=remaining)
        except Exception as exc:
            # Socket broke — attempt bounded reconnect
            print(f"  [{name}] Socket error, reconnecting: {exc}")
            try:
                await _ws_close(ws, backend)
            except Exception:
                pass

            reconnect_start = time.monotonic()
            ws, backend = None, ""
            for attempt in range(_RECONNECT_MAX_ATTEMPTS + 1):
                if time.monotonic() - reconnect_start > _RECONNECT_BUDGET_S:
                    reason = f"reconnect_budget_exceeded ({exc})"
                    break
                try:
                    ws, backend = await _ws_connect(
                        url, max_reconnects=0, name=name
                    )
                    break
                except Exception as exc2:
                    print(
                        f"  [{name}] Reconnect {attempt + 1} failed: {exc2}"
                    )
                    await asyncio.sleep(min(2 ** attempt, 10))
            else:
                reason = f"reconnect_exhausted ({exc})"

            if ws is None:
                print(f"  [{name}] {reason}")
                return reason

            # Re-subscribe
            try:
                if on_connect is not None:
                    await on_connect(ws, backend)
            except Exception as exc3:
                reason = f"resubscribe_error: {exc3}"
                print(f"  [{name}] {reason}")
                try:
                    await _ws_close(ws, backend)
                except Exception:
                    pass
                return reason

            # Track recovery
            if stats and stat_keys:
                for k in stat_keys:
                    if k in stats:
                        stats[k].reconnect_count += 1
                print(f"  [{name}] Reconnected, total reconnects: {stats[stat_keys[0]].reconnect_count if stat_keys else 0}")
            continue

        if msg is None:
            continue

        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            continue

        # Let the per-venue handler process; False = stop
        try:
            keep_going = await on_message(data, ws, backend)
        except Exception as exc:
            print(f"  [{name}] Handler error: {exc}")
            # Treat handler errors like socket errors — reconnect
            continue
        if not keep_going:
            break

    # --- clean exit ------------------------------------------------------
    try:
        await _ws_close(ws, backend)
    except Exception:
        pass

    if reason:
        print(f"  [{name}] Capture ended early: {reason}")
    return reason


# ---------------------------------------------------------------------------
# Binance USD-M perp capture
# ---------------------------------------------------------------------------

async def _binance_perp_handler(raw_to_canonical, files, stats, run_id, out_dir):
    """Binance perp message handler for _with_reconnect_loop."""
    first_message_seen = False

    async def _on_connect(ws, backend):
        # Binance combined streams auto-send after connect — no subscribe needed.
        pass

    async def _on_message(data, ws, backend):
        nonlocal first_message_seen
        # Combined stream: {"stream":"btcusdt@aggTrade","data":{...}}
        if "data" in data:
            payload = data["data"]
            stream_label = data.get("stream", "")
            raw = stream_label.split("@")[0]
            if not first_message_seen:
                first_message_seen = True
                for sk in stats:
                    if sk.startswith("binance_perp_"):
                        stats[sk].diagnostics.append("ws_received_first_message")
                print(f"  [BINANCE_PERP] First message received: stream={stream_label}")
        elif data.get("e") == "aggTrade":
            payload = data
            raw = payload.get("s", "").lower()
            if not first_message_seen:
                first_message_seen = True
                for sk in stats:
                    if sk.startswith("binance_perp_"):
                        stats[sk].diagnostics.append("ws_received_first_message")
        else:
            # Log non-trade messages for diagnostics
            evt = data.get("event", data.get("e", ""))
            if evt and not first_message_seen:
                print(f"  [BINANCE_PERP] Non-trade event before first data: {evt}")
            return True  # not a trade message, keep going

        if raw not in raw_to_canonical:
            return True

        try:
            p = float(payload["p"])
            q = float(payload["q"])
            m = payload.get("m", False)
            t_ms = int(payload["T"])
        except (KeyError, ValueError):
            return True

        side = "sell" if m else "buy"
        ts_ns = t_ms * _MS_TO_NS
        canon_sym = raw_to_canonical[raw]

        s = stats[f"binance_perp_{canon_sym}"]
        s.tick_count += 1
        if s.first_tick_ts is None:
            s.first_tick_ts = ts_ns
        s.last_tick_ts = ts_ns

        tick = TradeTickLite(
            ts_event=ts_ns,
            venue="binance_perp",
            symbol=canon_sym,
            price=p,
            size=q,
            side=side,
            trade_id=str(payload.get("a", "")),
        )
        files[raw].write(tick.to_json() + "\n")
        return True

    return _on_connect, _on_message


def build_binance_perp_ws_url(symbols: list[str]) -> str:
    """Build Binance USD-M perp combined aggTrade WebSocket URL.

    Uses the /market routed endpoint as required by Binance USD-M futures
    combined stream API. The unrouted /stream endpoint may connect but
    will not push data for aggTrade streams.

    Args:
        symbols: List of canonical symbol strings like ['BTC/USDT', 'ETH/USDT'].

    Returns:
        WebSocket URL string, e.g.
        'wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade'
    """
    stream_names: list[str] = []
    for sym in symbols:
        canon = resolve_symbol(sym)
        raw = f"{canon.asset}{canon.quote}".lower()
        stream_names.append(f"{raw}@aggTrade")
    return f"{BINANCE_PERP_WS_BASE}?streams={'/'.join(stream_names)}"


async def capture_binance_perp(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict[str, StreamStats],
    duration_seconds: int,
    stop_event: asyncio.Event,
) -> str | None:
    """Capture Binance USD-M perp aggTrade trades via combined WebSocket.

    WebSocket URL: wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/...
    Side from 'm' field: m=True -> seller aggressor -> side='sell'
                          m=False -> buyer aggressor -> side='buy'
    """
    raw_to_canonical: dict[str, str] = {}
    stream_names: list[str] = []

    for sym in symbols:
        try:
            canon = resolve_symbol(sym)
            raw = f"{canon.asset}{canon.quote}".lower()
            raw_to_canonical[raw] = f"{canon.asset}/{canon.quote}"
            stream_names.append(f"{raw}@aggTrade")
        except ValueError:
            print(f"  [WARN] Cannot resolve symbol {sym}, skipping")

    if not stream_names:
        print("  [WARN] No valid Binance perp symbols")
        return

    url = build_binance_perp_ws_url(symbols)
    print(f"  [BINANCE_PERP] Connecting: {url}")

    # Open file handles
    files: dict[str, object] = {}
    stat_keys: list[str] = []
    for raw, canon in raw_to_canonical.items():
        sym_file = canon.replace("/", "-")
        fname = f"trades_binance_perp_{sym_file}_{run_id}.jsonl"
        name = f"binance_perp_{canon}"
        s = stats[name] = StreamStats(name)
        s.start_time = _ts_now_iso()
        s.status = "ok"
        files[raw] = open(out_dir / fname, "w")
        stat_keys.append(name)

    on_connect, on_message = await _binance_perp_handler(
        raw_to_canonical, files, stats, run_id, out_dir
    )

    reason = await _with_reconnect_loop(
        url=url,
        name="BINANCE_PERP",
        on_connect=on_connect,
        on_message=on_message,
        duration_seconds=duration_seconds,
        stop_event=stop_event,
        stats=stats,
        stat_keys=stat_keys,
    )

    for f in files.values():
        f.close()
    for raw, canon in raw_to_canonical.items():
        stats[f"binance_perp_{canon}"].end_time = _ts_now_iso()

    if reason:
        for k in stat_keys:
            if k in stats:
                stats[k].error_summary = reason[:200]
                if stats[k].status == "ok":
                    stats[k].status = "failed"

    print(f"  [BINANCE_PERP] Capture complete")
    return reason


# ---------------------------------------------------------------------------
# Kraken spot capture
# ---------------------------------------------------------------------------

async def _kraken_handler(files, stats, symbols):
    """Kraken spot message handler for _with_reconnect_loop.

    Uses Kraken WebSocket v2 protocol (wss://ws.kraken.com/v2).
    v2 trade messages are dicts with ``channel`` and ``data`` keys.
    """
    subscribed_symbols: set[str] = set()

    async def _on_connect(ws, backend):
        sub = {
            "method": "subscribe",
            "params": {"channel": "trade", "symbol": symbols},
        }
        await _ws_send(ws, backend, json.dumps(sub))

    async def _on_message(data, ws, backend):
        # Kraken v2 trade messages are dicts:
        #   {"channel": "trade", "type": "update", "data": [{"symbol": "BTC/USD", ...}]}
        # Non-trade messages (heartbeat, subscription ack, status) are dicts too.
        if not isinstance(data, dict):
            return True

        # Log subscription acks
        if data.get("method") == "subscribe":
            result = data.get("result", {})
            success = data.get("success", False)
            sym = result.get("symbol", "")
            if success:
                subscribed_symbols.add(sym)
                print(f"  [KRAKEN] Subscribe ack: {sym} success=True")
                canon = _normalize_kraken_symbol(sym) if sym else sym
                sk = f"kraken_{canon}"
                if sk in stats:
                    stats[sk].diagnostics.append(f"subscribe_ack: {sym} success=True")
            else:
                error_msg = data.get("error", "unknown")
                print(f"  [KRAKEN] Subscribe FAIL: {sym} success=False error={error_msg}")
                canon = _normalize_kraken_symbol(sym) if sym else sym
                sk = f"kraken_{canon}"
                if sk in stats:
                    stats[sk].diagnostics.append(f"subscribe_rejected: {sym} error={error_msg}")
                    stats[sk].status = "failed"
            return True

        if data.get("channel") != "trade":
            return True

        trades_list = data.get("data", [])
        if not isinstance(trades_list, list):
            return True

        for t in trades_list:
            if not isinstance(t, dict):
                continue
            symbol = t.get("symbol", "")
            canon_sym = _normalize_kraken_symbol(symbol)
            if canon_sym not in files:
                continue
            try:
                price = float(t["price"])
                size = float(t["qty"])
                side = t.get("side", "unknown")
                ts_str = t.get("timestamp", "")
                if ts_str:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts_ns = int(dt.timestamp() * 1_000_000_000)
                else:
                    continue
            except (KeyError, ValueError, TypeError):
                continue

            s = stats[f"kraken_{canon_sym}"]
            s.tick_count += 1
            if s.first_tick_ts is None:
                s.first_tick_ts = ts_ns
            s.last_tick_ts = ts_ns

            tick = TradeTickLite(
                ts_event=ts_ns,
                venue="kraken",
                symbol=canon_sym,
                price=price,
                size=size,
                side=side,
                trade_id=str(t.get("trade_id", "")),
            )
            files[canon_sym].write(tick.to_json() + "\n")
        return True

    return _on_connect, _on_message


async def capture_kraken_spot(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict[str, StreamStats],
    duration_seconds: int,
    stop_event: asyncio.Event,
) -> str | None:
    """Capture Kraken spot trades via WebSocket."""
    url = KRAKEN_WS_URL
    print(f"  [KRAKEN] Connecting: {url}")

    # Open files
    files: dict[str, object] = {}
    stat_keys: list[str] = []
    for sym in symbols:
        try:
            canon = resolve_symbol(sym)
            canon_sym = f"{canon.asset}/{canon.quote}"
        except ValueError:
            canon_sym = sym
        sym_file = canon_sym.replace("/", "-")
        fname = f"trades_kraken_{sym_file}_{run_id}.jsonl"
        name = f"kraken_{canon_sym}"
        s = stats[name] = StreamStats(name)
        s.start_time = _ts_now_iso()
        s.status = "ok"
        files[canon_sym] = open(out_dir / fname, "w")
        stat_keys.append(name)

    on_connect, on_message = await _kraken_handler(files, stats, symbols)

    reason = await _with_reconnect_loop(
        url=url,
        name="KRAKEN",
        on_connect=on_connect,
        on_message=on_message,
        duration_seconds=duration_seconds,
        stop_event=stop_event,
        stats=stats,
        stat_keys=stat_keys,
    )

    for f in files.values():
        f.close()
    for canon_sym in files:
        stats[f"kraken_{canon_sym}"].end_time = _ts_now_iso()

    if reason:
        for k in stat_keys:
            if k in stats:
                stats[k].error_summary = reason[:200]
                if stats[k].status == "ok":
                    stats[k].status = "failed"

    print(f"  [KRAKEN] Capture complete")
    return reason


# ---------------------------------------------------------------------------
# Coinbase spot capture
# ---------------------------------------------------------------------------

async def _coinbase_handler(files, stats, product_ids):
    """Coinbase spot message handler for _with_reconnect_loop."""
    async def _on_connect(ws, backend):
        sub = {
            "type": "subscribe",
            "product_ids": product_ids,
            "channels": ["matches"],
        }
        await _ws_send(ws, backend, json.dumps(sub))

    async def _on_message(data, ws, backend):
        if data.get("type") != "match":
            return True

        product_id = data.get("product_id", "")
        parts = product_id.split("-")
        canon_sym = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else product_id

        if canon_sym not in files:
            return True

        try:
            price = float(data["price"])
            size = float(data["size"])
            side = data.get("side", "unknown")
            time_str = data.get("time", "")
            dt_match = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            ts_ns = int(dt_match.timestamp() * 1e9)
        except (KeyError, ValueError):
            return True

        s = stats[f"coinbase_{canon_sym}"]
        s.tick_count += 1
        if s.first_tick_ts is None:
            s.first_tick_ts = ts_ns
        s.last_tick_ts = ts_ns

        tick = TradeTickLite(
            ts_event=ts_ns,
            venue="coinbase",
            symbol=canon_sym,
            price=price,
            size=size,
            side=side,
            trade_id=str(data.get("trade_id", "")),
        )
        files[canon_sym].write(tick.to_json() + "\n")
        return True

    return _on_connect, _on_message


async def capture_coinbase_spot(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict[str, StreamStats],
    duration_seconds: int,
    stop_event: asyncio.Event,
) -> str | None:
    """Capture Coinbase spot trades via WebSocket."""
    url = "wss://ws-feed.exchange.coinbase.com"
    print(f"  [COINBASE] Connecting: {url}")

    # Build Coinbase product IDs
    product_ids: list[str] = []
    for sym in symbols:
        try:
            canon = resolve_symbol(sym)
            product_ids.append(f"{canon.asset}-{canon.quote}")
        except ValueError:
            product_ids.append(sym)

    # Open files
    files: dict[str, object] = {}
    stat_keys: list[str] = []
    for pid in product_ids:
        parts = pid.split("-")
        canon_sym = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else pid
        sym_file = canon_sym.replace("/", "-")
        fname = f"trades_coinbase_{sym_file}_{run_id}.jsonl"
        name = f"coinbase_{canon_sym}"
        s = stats[name] = StreamStats(name)
        s.start_time = _ts_now_iso()
        s.status = "ok"
        files[canon_sym] = open(out_dir / fname, "w")
        stat_keys.append(name)

    on_connect, on_message = await _coinbase_handler(files, stats, product_ids)

    reason = await _with_reconnect_loop(
        url=url,
        name="COINBASE",
        on_connect=on_connect,
        on_message=on_message,
        duration_seconds=duration_seconds,
        stop_event=stop_event,
        stats=stats,
        stat_keys=stat_keys,
    )

    for f in files.values():
        f.close()
    for canon_sym in files:
        stats[f"coinbase_{canon_sym}"].end_time = _ts_now_iso()

    if reason:
        for k in stat_keys:
            if k in stats:
                stats[k].error_summary = reason[:200]
                if stats[k].status == "ok":
                    stats[k].status = "failed"

    print(f"  [COINBASE] Capture complete")
    return reason


# ---------------------------------------------------------------------------
# Binance OI polling
# ---------------------------------------------------------------------------

async def poll_binance_oi(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Poll Binance open interest via public REST."""
    import aiohttp

    files: dict[str, object] = {}
    oi_counts: dict[str, int] = {}

    for sym in symbols:
        try:
            canon = resolve_symbol(sym)
            api_symbol = f"{canon.asset}{canon.quote}"
            canon_sym = f"{canon.asset}/{canon.quote}"
            sym_file = canon_sym.replace("/", "-")
            fname = f"open_interest_binance_perp_{sym_file}_{run_id}.jsonl"
            files[api_symbol] = open(out_dir / fname, "w")
            oi_counts[api_symbol] = 0
        except ValueError:
            continue

    if not files:
        return

    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while not stop_event.is_set():
                for api_symbol in list(files.keys()):
                    if stop_event.is_set():
                        break
                    try:
                        url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={api_symbol}"
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                body = await resp.json()
                                received_ns = int(time.time() * 1e9)
                                row = {
                                    "receive_timestamp_ns": received_ns,
                                    "receive_time": _ts_ns_to_iso(received_ns),
                                    "venue": "binance_perp",
                                    "api_symbol": api_symbol,
                                    "open_interest": float(body.get("openInterest", 0)),
                                    "open_interest_value": float(body.get("openInterestValue", 0)) if "openInterestValue" in body else None,
                                }
                                files[api_symbol].write(json.dumps(row) + "\n")
                                files[api_symbol].flush()
                                oi_counts[api_symbol] = oi_counts.get(api_symbol, 0) + 1
                    except Exception as e:
                        print(f"  [OI] Error polling {api_symbol}: {e}")

                # Wait for interval, but stop sooner if requested
                for _ in range(interval_seconds * 10):
                    if stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)
    except Exception as e:
        print(f"  [OI] Session error: {e}")
    finally:
        for f in files.values():
            f.close()

    print(f"  [OI] Capture complete, snapshot counts: {oi_counts}")
    return oi_counts


# ---------------------------------------------------------------------------
# Overlap computation
# ---------------------------------------------------------------------------

def compute_overlap_windows(stats: dict[str, StreamStats], target_assets: list[str]) -> dict:
    """Compute per-pair and global overlap windows from stream statistics."""
    # Group stats by asset
    per_pair: dict[str, dict] = {}

    # Map stream names to categories
    for asset in target_assets:
        # Find binance_perp ticks for this asset
        source_key = f"binance_perp_{asset}/USDT"
        source_stats = stats.get(source_key)

        # Find target ticks for this asset (kraken + coinbase)
        kraken_key = f"kraken_{asset}/USD"
        coinbase_key = f"coinbase_{asset}/USD"
        kraken = stats.get(kraken_key)
        coinbase = stats.get(coinbase_key)

        if source_stats and source_stats.first_tick_ts:
            source_min = source_stats.first_tick_ts
            source_max = source_stats.last_tick_ts
        else:
            per_pair[asset] = {"error": "no_source_data"}
            continue

        # Collect all target timestamps
        target_mins = []
        target_maxs = []
        if kraken and kraken.first_tick_ts:
            target_mins.append(kraken.first_tick_ts)
            target_maxs.append(kraken.last_tick_ts)
        if coinbase and coinbase.first_tick_ts:
            target_mins.append(coinbase.first_tick_ts)
            target_maxs.append(coinbase.last_tick_ts)

        if not target_mins:
            per_pair[asset] = {"error": "no_target_data"}
            continue

        target_min = min(target_mins)
        target_max = max(target_maxs)

        overlap_start = max(source_min, target_min)
        overlap_end = min(source_max, target_max)
        overlap_seconds = max(0, (overlap_end - overlap_start) / 1e9) if overlap_end > overlap_start else 0

        per_pair[asset] = {
            "source_start": _ts_ns_to_iso(source_min),
            "source_end": _ts_ns_to_iso(source_max),
            "source_start_ns": source_min,
            "source_end_ns": source_max,
            "source_tick_count": source_stats.tick_count if source_stats else 0,
            "target_start": _ts_ns_to_iso(target_min),
            "target_end": _ts_ns_to_iso(target_max),
            "target_start_ns": target_min,
            "target_end_ns": target_max,
            "target_tick_count": (kraken.tick_count if kraken else 0) + (coinbase.tick_count if coinbase else 0),
            "overlap_start": _ts_ns_to_iso(overlap_start) if overlap_start <= overlap_end else None,
            "overlap_end": _ts_ns_to_iso(overlap_end) if overlap_start <= overlap_end else None,
            "overlap_start_ns": overlap_start if overlap_start <= overlap_end else None,
            "overlap_end_ns": overlap_end if overlap_start <= overlap_end else None,
            "overlap_duration_seconds": round(overlap_seconds, 2),
        }

    # Global overlap
    overlap_starts = []
    overlap_ends = []
    for v in per_pair.values():
        if isinstance(v, dict) and v.get("overlap_start_ns"):
            overlap_starts.append(v["overlap_start_ns"])
            overlap_ends.append(v["overlap_end_ns"])

    if overlap_starts:
        global_start = max(overlap_starts)
        global_end = min(overlap_ends)
        global_duration = max(0, (global_end - global_start) / 1e9)
    else:
        global_start = global_end = None
        global_duration = 0

    return {
        "global_overlap_start": _ts_ns_to_iso(global_start) if global_start else None,
        "global_overlap_start_ns": global_start,
        "global_overlap_end": _ts_ns_to_iso(global_end) if global_end else None,
        "global_overlap_end_ns": global_end,
        "global_overlap_duration_seconds": round(global_duration, 2),
        "per_pair": per_pair,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Combined derivatives-source + spot-target capture runner. "
                    "Public data only, no auth, no orders."
    )
    p.add_argument("--run-id", type=str, default=None,
                    help="Optional run ID. Auto-generated if not provided.")
    p.add_argument("--source-venue", type=str, default="binance_perp")
    p.add_argument("--source-symbols", type=str, default="BTC/USDT,ETH/USDT")
    p.add_argument("--target-venues", type=str, default="kraken,coinbase")
    p.add_argument("--target-symbols", type=str, default="BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOGE/USD,AVAX/USD")
    p.add_argument("--duration-seconds", type=int, default=600)
    p.add_argument("--capture-open-interest", action="store_true", default=False)
    p.add_argument("--open-interest-interval-seconds", type=int, default=5)
    p.add_argument("--capture-mode", type=str, default="",
                   choices=["FULL_ACTIVE", "FAST_DIAGNOSTIC", ""],
                   help="Capture mode passed from volatility gate. Stored in manifest metadata.")
    p.add_argument("--out-base", type=str, default="data",
                    help="Base output directory (relative to cwd). Default: data")
    p.add_argument("--out", type=str, default=None,
                    help="Explicit output path. Overrides --out-base + run directory naming.")
    p.add_argument("--allow-existing-output", action="store_true", default=False,
                    help="Allow reuse of existing non-empty output directory.")
    p.add_argument("--disk-soft-cap-mb", type=int, default=1024,
                    help="Soft disk usage cap in MB. Warn if output exceeds this.")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Determine run ID
    run_id = args.run_id if args.run_id else create_run_id("cap")

    # Resolve output directory
    out_base = Path(args.out_base).resolve()
    if args.out:
        raw_out = Path(args.out)
        if not raw_out.is_absolute():
            out_dir_candidate = Path.cwd() / raw_out
        else:
            out_dir_candidate = raw_out
    else:
        out_dir_candidate = out_base / f"derivatives_spot_capture_v2_{run_id}"

    out_dir = safe_output_dir(out_dir_candidate, allow_existing=args.allow_existing_output)

    source_symbols = [x.strip() for x in args.source_symbols.split(",") if x.strip()]
    target_symbols = [x.strip() for x in args.target_symbols.split(",") if x.strip()]

    # Write run-index row: started
    run_index_path = Path.cwd() / "reports" / "research_run_index.jsonl"
    run_index_row = build_run_index_row(
        run_id=run_id,
        run_type="capture",
        status="started",
        command_args=" ".join(sys.argv),
        output_dir=str(out_dir),
        notes=f"derivatives_spot_capture v2, {args.duration_seconds}s, mode={args.capture_mode}",
    )
    append_run_index_row(run_index_row, path=run_index_path)

    print("=" * 70)
    print("DERIVATIVES-SOURCE + SPOT-TARGET CAPTURE")
    print("=" * 70)
    print(f"  run_id:            {run_id}")
    print(f"  source_venue:      {args.source_venue}")
    print(f"  source_symbols:    {source_symbols}")
    print(f"  target_venues:     {args.target_venues}")
    print(f"  target_symbols:    {target_symbols}")
    print(f"  duration_seconds:  {args.duration_seconds}")
    print(f"  capture_oi:        {args.capture_open_interest}")
    if args.capture_open_interest:
        print(f"  oi_interval_s:     {args.open_interest_interval_seconds}")
    print(f"  output:            {out_dir}")
    print()

    stats: dict[str, StreamStats] = {}
    stop_event = asyncio.Event()
    oi_counts: dict[str, int] = {}
    interrupted = False

    # Signal handling: set stop_event on SIGINT/SIGTERM
    shutdown_signals = set()

    def _handle_signal(signum, frame):
        nonlocal interrupted
        if interrupted:
            print(f"\n  [SIGNAL] Forced shutdown (signal {signum})")
            sys.exit(1)
        interrupted = True
        print(f"\n  [SIGNAL] Received signal {signum}, stopping capture...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    # SIGTERM: Unix-like only; Windows may not support it the same way
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    async def run_capture() -> tuple[list, list]:
        # Start all captures concurrently
        tasks = []
        tasks_and_names: list[tuple[str, asyncio.Task]] = []
        tasks.append(asyncio.create_task(
            capture_binance_perp(
                out_dir, run_id, source_symbols, stats,
                args.duration_seconds, stop_event
            ),
            name="binance_perp"
        ))
        tasks_and_names.append(("binance_perp", tasks[-1]))
        tasks.append(asyncio.create_task(
            capture_kraken_spot(
                out_dir, run_id, target_symbols, stats,
                args.duration_seconds, stop_event
            ),
            name="kraken"
        ))
        tasks_and_names.append(("kraken", tasks[-1]))
        tasks.append(asyncio.create_task(
            capture_coinbase_spot(
                out_dir, run_id, target_symbols, stats,
                args.duration_seconds, stop_event
            ),
            name="coinbase"
        ))
        tasks_and_names.append(("coinbase", tasks[-1]))

        if args.capture_open_interest:
            tasks.append(asyncio.create_task(
                poll_binance_oi(
                    out_dir, run_id, source_symbols, stats,
                    args.open_interest_interval_seconds, stop_event
                ),
                name="oi_poll"
            ))
            tasks_and_names.append(("oi_poll", tasks[-1]))

        # Wait for duration, then stop
        print(f"\n  Capturing for {args.duration_seconds} seconds...")
        try:
            await asyncio.sleep(args.duration_seconds)
        except asyncio.CancelledError:
            pass
        print("  Duration elapsed, stopping streams...")
        stop_event.set()

        # Give streams a moment to flush
        await asyncio.sleep(2)

        # Cancel remaining tasks
        for t in tasks:
            if not t.done():
                t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results, tasks_and_names

    start_time = _ts_now_iso()
    loop = asyncio.new_event_loop()
    try:
        results, tasks_and_names = loop.run_until_complete(run_capture())
    except (KeyboardInterrupt, SystemExit):
        interrupted = True
        results = []
        tasks_and_names = []
    finally:
        loop.close()

    end_time = _ts_now_iso()

    # Determine final capture status
    capture_status = "interrupted" if interrupted else "completed"

    # Handle OI counts from poll task and log any task exceptions
    task_exceptions: list[dict] = []
    for t, r in zip([t for _, t in tasks_and_names], results if results else []):
        task_name = t.get_name()
        if isinstance(r, dict):
            oi_counts.update(r)
        elif isinstance(r, BaseException):
            task_exceptions.append({
                "task": task_name,
                "exception_type": type(r).__name__,
                "message": str(r)[:500],
            })
            print(f"  [WARN] Capture task '{task_name}' failed: {type(r).__name__}: {r}")
            # Partial failure still recorded
            if capture_status == "completed":
                capture_status = "completed_with_errors"

    # Compute overlaps
    base_assets = []
    for sym in source_symbols:
        try:
            canon = resolve_symbol(sym)
            if canon.asset not in base_assets:
                base_assets.append(canon.asset)
        except ValueError:
            continue

    overlap_info = compute_overlap_windows(stats, base_assets)

    # Post-capture zero-tick diagnostics
    zero_tick_streams = [
        name for name, s in stats.items()
        if s.tick_count == 0 and s.status != "missing"
    ]
    for name in zero_tick_streams:
        s = stats[name]
        if "ws_received_first_message" not in s.diagnostics:
            s.diagnostics.append("zero_ticks_no_ws_data")
            print(f"  [DIAG] {name}: 0 ticks, no WebSocket data frames received")
        elif s.tick_count == 0:
            s.diagnostics.append("zero_ticks_parser_may_have_dropped")
            print(f"  [DIAG] {name}: 0 ticks, WS data frames received but parser may have dropped all")
    for name, s in stats.items():
        if s.tick_count > 0 and "ws_received_first_message" not in s.diagnostics:
            s.diagnostics.append("ticks_received_no_ws_diag_marker")

    # Build manifest
    manifest = {
        "run_id": run_id,
        "requested_duration_seconds": args.duration_seconds,
        "actual_start_time": start_time,
        "actual_end_time": end_time,
        "capture_status": capture_status,
        "streams": {name: s.to_dict() for name, s in stats.items()},
        "oi_snapshots": {k: {"count": v} for k, v in oi_counts.items()},
        "overlap": overlap_info,
        "missing_streams": [
            name for name, s in stats.items() if s.status == "missing"
        ],
        "failed_streams": [
            name for name, s in stats.items() if s.status == "failed"
        ],
        "total_reconnect_count": sum(s.reconnect_count for s in stats.values()),
        "error_summaries": [
            {"stream": s.name, "error": s.error_summary}
            for s in stats.values() if s.error_summary
        ],
        "task_exceptions": task_exceptions,
    }

    # Inject provenance metadata (schema version, git info, capture mode, etc.)
    inject_metadata_into_manifest(
        manifest,
        capture_mode=getattr(args, "capture_mode", ""),
        run_args=args,
    )

    # Partial manifest: write during active capture only
    partial_path = out_dir / "capture_manifest.partial.json"
    if interrupted:
        with open(partial_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

    # Final canonical manifest: atomic write
    manifest_path = out_dir / "capture_manifest.json"
    atomic_write_json(manifest_path, manifest)
    manifest_path_resolved = str(manifest_path.resolve())

    # Disk soft cap check
    total_size = sum(
        f.stat().st_size for f in out_dir.rglob("*") if f.is_file()
    )
    total_mb = total_size / (1024 * 1024)
    if total_mb > args.disk_soft_cap_mb:
        print(f"\n  [WARN] Output directory exceeds soft cap: "
              f"{total_mb:.1f} MB > {args.disk_soft_cap_mb} MB")

    # Write run-index row: final status
    final_row = build_run_index_row(
        run_id=run_id,
        run_type="capture",
        status=capture_status,
        command_args=" ".join(sys.argv),
        output_dir=str(out_dir.resolve()),
        manifest_path=manifest_path_resolved,
        notes=f"global_overlap={overlap_info.get('global_overlap_duration_seconds', '?')}s, total_ticks={sum(s.tick_count for s in stats.values())}",
        errors=None if capture_status == "completed" else f"capture_status={capture_status}",
    )
    append_run_index_row(final_row, path=run_index_path)

    # Print summary
    print()
    print("=" * 70)
    print("CAPTURE SUMMARY")
    print("=" * 70)
    for name, s in sorted(stats.items()):
        status_icon = "OK" if s.status == "ok" else ("FAIL" if s.status == "failed" else "MISS")
        print(f"  [{status_icon}] {name}: {s.tick_count:>8} ticks  "
              f"({s.start_time or '?'} -> {s.end_time or '?'})")
    print()
    print(f"  Global overlap: {overlap_info['global_overlap_start']} -> "
          f"{overlap_info['global_overlap_end']} "
          f"({overlap_info['global_overlap_duration_seconds']}s)")
    for asset, pair in overlap_info.get("per_pair", {}).items():
        if isinstance(pair, dict) and pair.get("overlap_duration_seconds"):
            print(f"    {asset}: {pair['overlap_duration_seconds']}s overlap")
    print()
    print(f"  Capture status: {capture_status}")
    print(f"  Manifest written to: {manifest_path}")
    print(f"  Total size: {total_mb:.1f} MB" if total_mb > 0 else "")


if __name__ == "__main__":
    main()
