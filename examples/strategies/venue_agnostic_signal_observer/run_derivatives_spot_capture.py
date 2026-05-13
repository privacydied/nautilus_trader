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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .tick_models import TradeTickLite
from .symbol_aliases import resolve_symbol

_MS_TO_NS = 1_000_000


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
        ws_timeout = aiohttp.ClientWSTimeout(ws_receive=10, ws_close=10)
        ws = await asyncio.wait_for(session.ws_connect(url, timeout=ws_timeout), timeout=10)
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
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Binance USD-M perp capture
# ---------------------------------------------------------------------------

async def capture_binance_perp(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict[str, StreamStats],
    duration_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Capture Binance USD-M perp aggTrade trades via combined WebSocket.

    WebSocket URL: wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/...
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

    url = f"wss://fstream.binance.com/stream?streams={'/'.join(stream_names)}"
    print(f"  [BINANCE_PERP] Connecting: {url}")

    try:
        ws, backend = await _ws_connect(
            url, max_reconnects=5, name="BINANCE_PERP"
        )
    except RuntimeError as e:
        print(f"  [BINANCE_PERP] Failed: {e}")
        for raw, canon in raw_to_canonical.items():
            s = stats[f"binance_perp_{canon}"] = StreamStats(f"binance_perp_{canon}")
            s.status = "failed"
            s.error_summary = str(e)[:200]
        return

    # Open file handles
    files: dict[str, object] = {}
    for raw, canon in raw_to_canonical.items():
        sym_file = canon.replace("/", "-")
        fname = f"trades_binance_perp_{sym_file}_{run_id}.jsonl"
        name = f"binance_perp_{canon}"
        s = stats[name] = StreamStats(name)
        s.start_time = _ts_now_iso()
        s.status = "ok"
        files[raw] = open(out_dir / fname, "w")

    deadline = time.time() + duration_seconds

    try:
        while time.time() < deadline and not stop_event.is_set():
            remaining = min(2.0, deadline - time.time())
            try:
                msg = await _ws_recv(ws, backend, timeout=remaining)
            except Exception:
                break

            if msg is None:
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            # Combined stream: {"stream":"btcusdt@aggTrade","data":{...}}
            if "data" in data:
                payload = data["data"]
                stream_label = data.get("stream", "")
                raw = stream_label.split("@")[0]
            elif data.get("e") == "aggTrade":
                payload = data
                raw = payload.get("s", "").lower()
            else:
                continue

            if raw not in raw_to_canonical:
                continue

            try:
                p = float(payload["p"])
                q = float(payload["q"])
                m = payload.get("m", False)
                t_ms = int(payload["T"])
            except (KeyError, ValueError):
                continue

            # m=true -> buyer was maker -> seller aggressor -> sell
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
    finally:
        for f in files.values():
            f.close()
        for raw, canon in raw_to_canonical.items():
            stats[f"binance_perp_{canon}"].end_time = _ts_now_iso()
        await _ws_close(ws, backend)

    print(f"  [BINANCE_PERP] Capture complete")


# ---------------------------------------------------------------------------
# Kraken spot capture
# ---------------------------------------------------------------------------

async def capture_kraken_spot(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict[str, StreamStats],
    duration_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Capture Kraken spot trades via WebSocket."""
    url = "wss://ws.kraken.com"
    print(f"  [KRAKEN] Connecting: {url}")

    try:
        ws, backend = await _ws_connect(
            url, max_reconnects=5, name="KRAKEN"
        )
    except RuntimeError as e:
        print(f"  [KRAKEN] Failed: {e}")
        for sym in symbols:
            canon = resolve_symbol(sym)
            sym_key = f"{canon.asset}/{canon.quote}"
            s = stats[f"kraken_{sym_key}"] = StreamStats(f"kraken_{sym_key}")
            s.status = "failed"
            s.error_summary = str(e)[:200]
        return

    # Subscribe
    sub = {
        "method": "subscribe",
        "params": {"channel": "trade", "symbol": symbols},
    }
    try:
        await _ws_send(ws, backend, json.dumps(sub))
    except Exception as e:
        print(f"  [KRAKEN] Subscribe error: {e}")
        await _ws_close(ws, backend)
        return

    # Open files
    files: dict[str, object] = {}
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

    deadline = time.time() + duration_seconds

    try:
        while time.time() < deadline and not stop_event.is_set():
            remaining = min(2.0, deadline - time.time())
            try:
                msg = await _ws_recv(ws, backend, timeout=remaining)
            except Exception:
                break

            if msg is None:
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            # Kraken trade messages are arrays: [channel_id, [trades...], channel_name, symbol]
            if not isinstance(data, list) or len(data) < 4:
                continue

            if data[2] != "trade":
                continue

            symbol = data[3]
            trades_list = data[1]

            # Normalize symbol
            canon_sym = _normalize_kraken_symbol(symbol)
            if canon_sym not in files:
                continue

            for t in trades_list:
                try:
                    price = float(t["price"])
                    size = float(t["qty"])
                    side = t.get("side", "unknown")
                    ts_seconds = float(t["timestamp"])
                    ts_ns = int(ts_seconds * 1_000_000_000)
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
                    trade_id=str(t.get("id", "")),
                )
                files[canon_sym].write(tick.to_json() + "\n")
    finally:
        for f in files.values():
            f.close()
        for canon_sym in files:
            stats[f"kraken_{canon_sym}"].end_time = _ts_now_iso()
        await _ws_close(ws, backend)

    print(f"  [KRAKEN] Capture complete")


# ---------------------------------------------------------------------------
# Coinbase spot capture
# ---------------------------------------------------------------------------

async def capture_coinbase_spot(
    out_dir: Path,
    run_id: str,
    symbols: list[str],
    stats: dict[str, StreamStats],
    duration_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Capture Coinbase spot trades via WebSocket."""
    url = "wss://ws-feed.exchange.coinbase.com"
    print(f"  [COINBASE] Connecting: {url}")

    try:
        ws, backend = await _ws_connect(
            url, max_reconnects=5, name="COINBASE"
        )
    except RuntimeError as e:
        print(f"  [COINBASE] Failed: {e}")
        for sym in symbols:
            try:
                canon = resolve_symbol(sym)
                sym_key = f"{canon.asset}/{canon.quote}"
            except ValueError:
                sym_key = sym
            s = stats[f"coinbase_{sym_key}"] = StreamStats(f"coinbase_{sym_key}")
            s.status = "failed"
            s.error_summary = str(e)[:200]
        return

    # Build Coinbase product IDs
    product_ids: list[str] = []
    for sym in symbols:
        try:
            canon = resolve_symbol(sym)
            product_ids.append(f"{canon.asset}-{canon.quote}")
        except ValueError:
            product_ids.append(sym)

    sub = {
        "type": "subscribe",
        "product_ids": product_ids,
        "channels": ["matches"],
    }
    try:
        await _ws_send(ws, backend, json.dumps(sub))
    except Exception as e:
        print(f"  [COINBASE] Subscribe error: {e}")
        await _ws_close(ws, backend)
        return

    # Open files
    files: dict[str, object] = {}
    for pid in product_ids:
        # Map product_id back to canonical
        parts = pid.split("-")
        canon_sym = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else pid
        sym_file = canon_sym.replace("/", "-")
        fname = f"trades_coinbase_{sym_file}_{run_id}.jsonl"
        name = f"coinbase_{canon_sym}"
        s = stats[name] = StreamStats(name)
        s.start_time = _ts_now_iso()
        s.status = "ok"
        files[canon_sym] = open(out_dir / fname, "w")

    deadline = time.time() + duration_seconds

    try:
        while time.time() < deadline and not stop_event.is_set():
            remaining = min(2.0, deadline - time.time())
            try:
                msg = await _ws_recv(ws, backend, timeout=remaining)
            except Exception:
                break

            if msg is None:
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            if data.get("type") != "match":
                continue

            product_id = data.get("product_id", "")
            # Map product_id to canonical symbol
            parts = product_id.split("-")
            if len(parts) >= 2:
                canon_sym = f"{parts[0]}/{parts[1]}"
            else:
                canon_sym = product_id

            if canon_sym not in files:
                continue

            try:
                price = float(data["price"])
                size = float(data["size"])
                side = data.get("side", "unknown")
                time_str = data.get("time", "")
                dt_match = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                ts_ns = int(dt_match.timestamp() * 1e9)
            except (KeyError, ValueError):
                continue

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
    finally:
        for f in files.values():
            f.close()
        for canon_sym in files:
            stats[f"coinbase_{canon_sym}"].end_time = _ts_now_iso()
        await _ws_close(ws, backend)

    print(f"  [COINBASE] Capture complete")


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

    deadline = time.time() + (interval_seconds * len(files) * 2)  # Run at least 2 rounds

    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while time.time() < deadline and not stop_event.is_set():
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
                    if stop_event.is_set() or time.time() >= deadline:
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
    p.add_argument("--source-venue", type=str, default="binance_perp")
    p.add_argument("--source-symbols", type=str, default="BTC/USDT,ETH/USDT,SOL/USDT")
    p.add_argument("--target-venues", type=str, default="kraken,coinbase")
    p.add_argument("--target-symbols", type=str, default="BTC/USD,ETH/USD,SOL/USD")
    p.add_argument("--duration-seconds", type=int, default=600)
    p.add_argument("--capture-open-interest", action="store_true", default=False)
    p.add_argument("--open-interest-interval-seconds", type=int, default=5)
    p.add_argument("--out", type=str, default="data/derivatives_spot_capture_v2")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run_id = str(int(time.time()))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    source_symbols = [x.strip() for x in args.source_symbols.split(",") if x.strip()]
    target_symbols = [x.strip() for x in args.target_symbols.split(",") if x.strip()]

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

    async def run_capture():
        # Start all captures concurrently
        tasks = []
        tasks.append(asyncio.create_task(
            capture_binance_perp(
                out_dir, run_id, source_symbols, stats,
                args.duration_seconds, stop_event
            ),
            name="binance_perp"
        ))
        tasks.append(asyncio.create_task(
            capture_kraken_spot(
                out_dir, run_id, target_symbols, stats,
                args.duration_seconds, stop_event
            ),
            name="kraken"
        ))
        tasks.append(asyncio.create_task(
            capture_coinbase_spot(
                out_dir, run_id, target_symbols, stats,
                args.duration_seconds, stop_event
            ),
            name="coinbase"
        ))

        if args.capture_open_interest:
            tasks.append(asyncio.create_task(
                poll_binance_oi(
                    out_dir, run_id, source_symbols, stats,
                    args.open_interest_interval_seconds, stop_event
                ),
                name="oi_poll"
            ))

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
        return results

    start_time = _ts_now_iso()
    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(run_capture())
    finally:
        loop.close()

    end_time = _ts_now_iso()

    # Handle OI counts from poll task and log any task exceptions
    for r in results:
        if isinstance(r, dict):
            oi_counts.update(r)
        elif isinstance(r, BaseException):
            task_name = getattr(r, "__cause__", None)
            print(f"  [WARN] Capture task failed: {type(r).__name__}: {r}")

    # Compute overlaps
    # Extract base assets from source symbols
    base_assets = []
    for sym in source_symbols:
        try:
            canon = resolve_symbol(sym)
            if canon.asset not in base_assets:
                base_assets.append(canon.asset)
        except ValueError:
            continue

    overlap_info = compute_overlap_windows(stats, base_assets)

    # Build manifest
    manifest = {
        "run_id": run_id,
        "requested_duration_seconds": args.duration_seconds,
        "actual_start_time": start_time,
        "actual_end_time": end_time,
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
    }

    # Write manifest
    manifest_path = out_dir / "capture_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

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
    print(f"  Manifest written to: {manifest_path}")


if __name__ == "__main__":
    main()
