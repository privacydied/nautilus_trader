from __future__ import annotations

"""Public Hyperliquid market-data observer writing hourly Parquet shards.

Safety contract: public market-data only; no account transport; no order-capable
client imports. The implementation uses public Hyperliquid REST/WebSocket data
and keeps Nautilus Hyperliquid data-side symbols discoverable for adapter parity.

Book snapshots are flattened into fixed Parquet columns:
``bid_px_0..19``, ``bid_sz_0..19``, ``bid_n_0..19``, ``ask_px_0..19``,
``ask_sz_0..19``, ``ask_n_0..19`` plus ``ts_event``, ``coin`` and ``seq``.
The fixed schema is deliberately boring: it avoids nested-list compatibility
surprises and makes downstream quantile scans cheap.
"""

import asyncio
import dataclasses
import json
import math
import os
import signal
import sys
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import websockets

try:  # data-side Nautilus adapter symbols only; observer does not instantiate a node.
    from nautilus_trader.adapters.hyperliquid.data import HyperliquidDataClient  # noqa: F401
    from nautilus_trader.adapters.hyperliquid.providers import HyperliquidInstrumentProvider  # noqa: F401
    from nautilus_trader.core.nautilus_pyo3 import HyperliquidWebSocketClient  # noqa: F401
except Exception:  # pragma: no cover - local builds may not have extensions ready.
    HyperliquidDataClient = None  # type: ignore[assignment]
    HyperliquidInstrumentProvider = None  # type: ignore[assignment]
    HyperliquidWebSocketClient = None  # type: ignore[assignment]

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_COINS = ("BTC", "ETH", "SOL", "LINK", "DOGE", "AVAX")
DEFAULT_CHANNELS = ("l2book", "trades", "funding", "mark")


def now_ns() -> int:
    return time.time_ns()


def ms_to_ns(ms: int | float | str | None) -> int:
    if ms is None:
        return now_ns()
    return int(float(ms) * 1_000_000)


@dataclass(frozen=True)
class HyperliquidBookSnapshot:
    ts_event: int
    coin: str
    bids: list[tuple[float, float, int]]
    asks: list[tuple[float, float, int]]
    seq: int

    CHANNEL: ClassVar[str] = "l2book"

    def to_row(self, depth: int = 20) -> dict[str, Any]:
        row: dict[str, Any] = {"ts_event": int(self.ts_event), "coin": self.coin, "seq": int(self.seq)}
        for side_name, levels in (("bid", self.bids), ("ask", self.asks)):
            for i in range(depth):
                if i < len(levels):
                    px, sz, n_orders = levels[i]
                else:
                    px, sz, n_orders = math.nan, math.nan, 0
                row[f"{side_name}_px_{i}"] = float(px)
                row[f"{side_name}_sz_{i}"] = float(sz)
                row[f"{side_name}_n_{i}"] = int(n_orders)
        return row

    @classmethod
    def from_l2_payload(cls, coin: str, payload: dict[str, Any], *, depth: int = 20) -> "HyperliquidBookSnapshot":
        levels = payload.get("levels") or [[], []]
        bids = [(float(x["px"]), float(x["sz"]), int(x.get("n", 0))) for x in levels[0][:depth]]
        asks = [(float(x["px"]), float(x["sz"]), int(x.get("n", 0))) for x in levels[1][:depth]]
        ts_ns = ms_to_ns(payload.get("time"))
        seq = int(payload.get("time") or ts_ns)
        return cls(ts_event=ts_ns, coin=coin, bids=bids, asks=asks, seq=seq)


@dataclass(frozen=True)
class HyperliquidTrade:
    ts_event: int
    coin: str
    px: float
    sz: float
    side: str
    hash: str

    CHANNEL: ClassVar[str] = "trades"

    def to_row(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_payload(cls, coin: str, payload: dict[str, Any]) -> "HyperliquidTrade":
        side = str(payload.get("side", ""))[:1].upper()
        return cls(
            ts_event=ms_to_ns(payload.get("time")),
            coin=coin,
            px=float(payload.get("px")),
            sz=float(payload.get("sz")),
            side=side,
            hash=str(payload.get("hash") or payload.get("tid") or f"{coin}-{payload.get('time')}-{payload.get('px')}-{payload.get('sz')}-{side}"),
        )


@dataclass(frozen=True)
class HyperliquidFundingUpdate:
    ts_event: int
    coin: str
    funding_rate: float
    premium: float
    oracle_px: float

    CHANNEL: ClassVar[str] = "funding"

    def to_row(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class HyperliquidMarkUpdate:
    ts_event: int
    coin: str
    mark_px: float
    mid_px: float

    CHANNEL: ClassVar[str] = "mark"

    def to_row(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class HourlyParquetSink:
    def __init__(self, out_dir: Path, *, depth: int = 20, flush_rows: int = 100) -> None:
        self.out_dir = Path(out_dir)
        self.depth = depth
        self.flush_rows = flush_rows
        self.buffers: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.seen_seq: dict[str, set[int]] = defaultdict(set)
        self.seen_hash: dict[str, set[str]] = defaultdict(set)
        self.rows_written: dict[tuple[str, str], int] = defaultdict(int)
        self.files_written: set[Path] = set()

    @staticmethod
    def hour_key(ts_event: int) -> str:
        dt = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=UTC)
        return f"{dt:%Y-%m-%d}/{dt:%H}"

    def file_path(self, coin: str, channel: str, hour_key: str) -> Path:
        date_part, hour_part = hour_key.split("/")
        return self.out_dir / coin / channel / date_part / f"{hour_part}.parquet"

    def load_recovery_keys(self, coins: Iterable[str], channels: Iterable[str]) -> None:
        for coin in coins:
            for channel in channels:
                paths = sorted((self.out_dir / coin / channel).glob("*/*.parquet"))
                if not paths:
                    continue
                latest = paths[-1]
                try:
                    table = pq.read_table(latest, columns=["seq"] if channel == "l2book" else ["hash"] if channel == "trades" else None)
                    df = table.to_pandas()
                    if channel == "l2book" and "seq" in df:
                        self.seen_seq[coin].update(int(x) for x in df["seq"].dropna().tolist())
                    elif channel == "trades" and "hash" in df:
                        self.seen_hash[coin].update(str(x) for x in df["hash"].dropna().tolist())
                except Exception as exc:
                    print(f"RECOVERY_WARNING coin={coin} channel={channel} path={latest} error={exc}", flush=True)

    def add(self, coin: str, channel: str, row: dict[str, Any]) -> bool:
        if channel == "l2book":
            seq = int(row["seq"])
            if seq in self.seen_seq[coin]:
                return False
            self.seen_seq[coin].add(seq)
        elif channel == "trades":
            h = str(row["hash"])
            if h in self.seen_hash[coin]:
                return False
            self.seen_hash[coin].add(h)
        hour = self.hour_key(int(row["ts_event"]))
        key = (coin, channel, hour)
        self.buffers[key].append(row)
        if len(self.buffers[key]) >= self.flush_rows:
            self.flush_key(key)
        return True

    def flush_key(self, key: tuple[str, str, str]) -> None:
        rows = self.buffers.get(key) or []
        if not rows:
            return
        coin, channel, hour = key
        path = self.file_path(coin, channel, hour)
        path.parent.mkdir(parents=True, exist_ok=True)
        new_df = pd.DataFrame(rows)
        if path.exists():
            old_df = pq.read_table(path).to_pandas()
            df = pd.concat([old_df, new_df], ignore_index=True)
        else:
            df = new_df
        if channel == "l2book" and "seq" in df:
            df = df.drop_duplicates(subset=["seq"], keep="last").sort_values("ts_event")
        elif channel == "trades" and "hash" in df:
            df = df.drop_duplicates(subset=["hash"], keep="last").sort_values("ts_event")
        else:
            df = df.drop_duplicates().sort_values("ts_event")
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp)
        os.replace(tmp, path)
        self.rows_written[(coin, channel)] += len(rows)
        self.files_written.add(path)
        self.buffers[key].clear()

    def flush_all(self) -> None:
        for key in list(self.buffers):
            self.flush_key(key)


@dataclass(frozen=True)
class ObserverConfig:
    coins: tuple[str, ...] = DEFAULT_COINS
    channels: tuple[str, ...] = DEFAULT_CHANNELS
    book_depth: int = 20
    book_snapshot_interval_seconds: float = 1.0
    out: Path = Path("data/hyperliquid_live/v0")
    duration_seconds: int = 0
    max_rss_gb: float = 4.0
    hard_rss_gb: float = 6.0
    heartbeat_seconds: int = 60


class HyperliquidLiveObserver:
    def __init__(self, config: ObserverConfig) -> None:
        self.config = config
        self.sink = HourlyParquetSink(config.out, depth=config.book_depth)
        self.counts: dict[str, dict[str, int]] = {coin: {ch: 0 for ch in config.channels} for coin in config.coins}
        self.last_seq: dict[str, int] = {coin: 0 for coin in config.coins}
        self._stop = asyncio.Event()
        self._process = psutil.Process(os.getpid())

    def stop(self) -> None:
        self._stop.set()

    def rss_gb(self) -> float:
        return self._process.memory_info().rss / (1024 ** 3)

    def assert_memory_guard(self) -> None:
        rss = self.rss_gb()
        if rss >= self.config.hard_rss_gb:
            raise MemoryError(f"RSS_HARD_LIMIT_EXCEEDED rss_gb={rss:.3f} hard_gb={self.config.hard_rss_gb}")

    async def run(self) -> None:
        self.sink.load_recovery_keys(self.config.coins, self.config.channels)
        print("HYPERLIQUID_OBSERVER_START", json.dumps(self.config_snapshot(), sort_keys=True, default=str), flush=True)
        tasks = [asyncio.create_task(self._heartbeat_loop())]
        if "l2book" in self.config.channels:
            tasks.append(asyncio.create_task(self._book_poll_loop()))
        if "trades" in self.config.channels:
            tasks.append(asyncio.create_task(self._trades_ws_loop()))
        if "funding" in self.config.channels or "mark" in self.config.channels:
            tasks.append(asyncio.create_task(self._asset_context_loop()))
        if self.config.duration_seconds > 0:
            tasks.append(asyncio.create_task(self._duration_stop()))
        try:
            await self._stop.wait()
        except Exception:
            print("CRASH_BANNER", json.dumps({"last_seq": self.last_seq, "counts": self.counts}, sort_keys=True), flush=True)
            raise
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.sink.flush_all()
            print("HYPERLIQUID_OBSERVER_STOP", json.dumps({"last_seq": self.last_seq, "counts": self.counts}, sort_keys=True), flush=True)

    def config_snapshot(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self.config), "pid": os.getpid(), "out": str(self.config.out)}

    async def _duration_stop(self) -> None:
        await asyncio.sleep(self.config.duration_seconds)
        self.stop()

    async def _book_poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            while not self._stop.is_set():
                started = time.monotonic()
                for coin in self.config.coins:
                    self.assert_memory_guard()
                    resp = await client.post(INFO_URL, json={"type": "l2Book", "coin": coin})
                    resp.raise_for_status()
                    snap = HyperliquidBookSnapshot.from_l2_payload(coin, resp.json(), depth=self.config.book_depth)
                    if self.sink.add(coin, "l2book", snap.to_row(self.config.book_depth)):
                        self.counts[coin]["l2book"] += 1
                        self.last_seq[coin] = snap.seq
                await asyncio.sleep(max(0.0, self.config.book_snapshot_interval_seconds - (time.monotonic() - started)))

    async def _trades_ws_loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    print("WebSocket connected channel=trades", flush=True)
                    for coin in self.config.coins:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
                    async for raw in ws:
                        self.assert_memory_guard()
                        msg = json.loads(raw)
                        if msg.get("channel") != "trades":
                            continue
                        data = msg.get("data") or []
                        if isinstance(data, dict):
                            data = [data]
                        for item in data:
                            coin = str(item.get("coin") or item.get("coinName") or "")
                            if coin not in self.config.coins:
                                continue
                            trade = HyperliquidTrade.from_payload(coin, item)
                            if self.sink.add(coin, "trades", trade.to_row()):
                                self.counts[coin]["trades"] += 1
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"WS_RECONNECT channel=trades error={exc}", flush=True)
                await asyncio.sleep(5)

    async def _asset_context_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            while not self._stop.is_set():
                self.assert_memory_guard()
                mids_resp = await client.post(INFO_URL, json={"type": "allMids"})
                mids_resp.raise_for_status()
                mids = mids_resp.json()
                funding_resp = await client.post(INFO_URL, json={"type": "predictedFundings"})
                funding_resp.raise_for_status()
                fundings = _extract_hl_fundings(funding_resp.json())
                ts = now_ns()
                if "mark" in self.config.channels:
                    for coin in self.config.coins:
                        mid = float(mids[coin]) if coin in mids else math.nan
                        row = HyperliquidMarkUpdate(ts_event=ts, coin=coin, mark_px=mid, mid_px=mid).to_row()
                        if self.sink.add(coin, "mark", row):
                            self.counts[coin]["mark"] += 1
                if "funding" in self.config.channels:
                    for coin in self.config.coins:
                        fr = fundings.get(coin, math.nan)
                        oracle = float(mids[coin]) if coin in mids else math.nan
                        row = HyperliquidFundingUpdate(ts_event=ts, coin=coin, funding_rate=float(fr), premium=math.nan, oracle_px=oracle).to_row()
                        if self.sink.add(coin, "funding", row):
                            self.counts[coin]["funding"] += 1
                await asyncio.sleep(10)

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.config.heartbeat_seconds)
            self.sink.flush_all()
            rss = self.rss_gb()
            free = psutil.disk_usage(str(self.config.out.parent if self.config.out.exists() else self.config.out.parent)).free
            print("HEARTBEAT", json.dumps({"rss_gb": round(rss, 3), "disk_free_gb": round(free/(1024**3), 2), "counts": self.counts, "last_seq": self.last_seq}, sort_keys=True), flush=True)
            if rss > self.config.max_rss_gb:
                print(f"RSS_SOFT_TARGET_EXCEEDED rss_gb={rss:.3f} target_gb={self.config.max_rss_gb}", flush=True)
            self.assert_memory_guard()


def _extract_hl_fundings(payload: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for coin, entries in payload:
        for venue, detail in entries:
            if venue == "HlPerp":
                out[str(coin)] = float(detail.get("fundingRate"))
    return out


def parse_csv(value: str) -> tuple[str, ...]:
    return tuple(x.strip().upper() for x in value.split(",") if x.strip())


async def run_observer(config: ObserverConfig) -> None:
    observer = HyperliquidLiveObserver(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, observer.stop)
        except NotImplementedError:  # pragma: no cover
            pass
    await observer.run()
