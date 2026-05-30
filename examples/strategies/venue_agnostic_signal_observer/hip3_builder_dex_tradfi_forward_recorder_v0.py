"""HIP-3 Builder-DEX TradFi Forward Recorder v0.

Public-data-only forward recorder for TSLA/AAPL/MSFT/NVDA builder DEX symbols.

Captures:
- Current L2 books (public l2Book)
- Asset contexts / marks / oracles (public metaAndAssetCtxs)
- Candle continuity (public candleSnapshot)
- External anchors (public, no-auth)
- Calendar/off-hours labels
- Spread/depth/staleness metrics
- Optional diagnostic residuals only

NOT a strategy. NOT paper trading. NOT live trading. NOT Phase 0.
No orders, no private keys, no exchange auth, no signing.
"""

from __future__ import annotations

import json
import os
import sys
import time
import hashlib
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional
from collections import defaultdict
import urllib.request
import urllib.error

try:
    import orjson
    _USE_ORJSON = True
except ImportError:
    _USE_ORJSON = False

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

STUDY_ID = "hip3_builder_dex_tradfi_forward_recorder_v0"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
ALLOWED_INFO_TYPES = frozenset({"perpDexs", "meta", "metaAndAssetCtxs", "l2Book", "candleSnapshot"})
FORBIDDEN_INFO_TYPES = frozenset({
    "clearinghouseState", "userState", "openOrders", "historicalOrders",
    "orderStatus", "userFills", "userFees", "portfolio", "subAccounts",
})

PRIMARY_SYMBOLS = ["TSLA", "AAPL", "MSFT", "NVDA"]
SECONDARY_SYMBOLS = ["AMZN", "GOOG", "GOOGL", "META", "GOLD", "WTI", "OIL", "SPX", "NDX", "NAS100", "QQQ"]

DEFAULT_POLL_SECONDS = 60
CANDLE_1M_INTERVAL_MINUTES = 120
CANDLE_15M_INTERVAL_HOURS = 24

ET_ZONE = "America/New_York"

# ──────────────────────────────────────────────────────────────────────────────
# Status taxonomy
# ──────────────────────────────────────────────────────────────────────────────

class RecorderStatus(str):
    READY = "HIP3_FORWARD_RECORDER_READY"
    DRY_RUN_READY = "HIP3_FORWARD_RECORDER_DRY_RUN_READY"
    SYMBOLS_RESOLVED = "HIP3_FORWARD_RECORDER_SYMBOLS_RESOLVED"
    CAPTURE_STARTED = "HIP3_FORWARD_RECORDER_CAPTURE_STARTED"
    CAPTURE_RUNNING = "HIP3_FORWARD_RECORDER_CAPTURE_RUNNING"
    CAPTURE_COMPLETE = "HIP3_FORWARD_RECORDER_CAPTURE_COMPLETE"
    CAPTURE_ERROR = "HIP3_FORWARD_RECORDER_CAPTURE_ERROR"
    PUBLIC_API_BLOCKED = "HIP3_FORWARD_RECORDER_PUBLIC_API_BLOCKED"
    SYMBOL_RESOLUTION_FAILED = "HIP3_FORWARD_RECORDER_SYMBOL_RESOLUTION_FAILED"
    ANCHOR_UNAVAILABLE = "HIP3_FORWARD_RECORDER_ANCHOR_UNAVAILABLE"
    PARTIAL_CAPTURE = "HIP3_FORWARD_RECORDER_PARTIAL_CAPTURE"
    NO_L2_BOOK = "HIP3_FORWARD_RECORDER_NO_L2_BOOK"
    NO_CANDLES = "HIP3_FORWARD_RECORDER_NO_CANDLES"

FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
    "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
    "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
    "EDGE_CONFIRMED",
})

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(STUDY_ID)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _epoch_ms(dt: Optional[datetime] = None) -> int:
    if dt is None:
        dt = datetime.now(timezone.utc)
    return int(dt.timestamp() * 1000)

def _write_json_atomic(path: Path, data: dict) -> None:
    """Atomic write: write to .tmp then rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, indent=2, default=str)
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)

def _append_jsonl(path: Path, record: dict) -> None:
    """Append a single JSON line."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")

def _safe_json_loads(text: str) -> dict:
    """JSONL line parser with orjson fallback."""
    if _USE_ORJSON and orjson is not None:
        try:
            if isinstance(text, str):
                text = text.encode("utf-8")
            return orjson.loads(text)
        except Exception:
            pass  # Fall through to stdlib json
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return json.loads(text)

def _git_info() -> tuple:
    """Get git SHA and dirty flag."""
    try:
        sha = os.popen("git rev-parse HEAD 2>/dev/null").read().strip()
        dirty = bool(os.popen("git status --porcelain 2>/dev/null").read().strip())
        return sha, dirty
    except Exception:
        return "unknown", False

# ──────────────────────────────────────────────────────────────────────────────
# Public HTTP Chokepoint
# ──────────────────────────────────────────────────────────────────────────────

class PublicInfoChokepoint:
    """Public Hyperliquid info endpoint chokepoint.

    Only allows approved request types. Rejects account/user endpoints.
    """

    def __init__(self, allow_network: bool = False):
        self.allow_network = allow_network
        self.request_count = 0
        self.errors = []

    def post_info(self, payload: dict, timeout: int = 30) -> dict:
        """Post a payload to the Hyperliquid info endpoint.

        Validates request type against allow/forgotten lists.
        Returns parsed JSON response.
        """
        if not self.allow_network:
            raise RuntimeError("Network not allowed (allow_network=False)")

        req_type = payload.get("type", "")
        if req_type not in ALLOWED_INFO_TYPES:
            if req_type in FORBIDDEN_INFO_TYPES:
                raise ValueError(f"Forbidden info type: {req_type}")
            raise ValueError(f"Unknown/unsupported info type: {req_type}")

        self.request_count += 1
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            HYPERLIQUID_INFO_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            start = time.monotonic()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.monotonic() - start) * 1000
                data = json.loads(resp.read().decode("utf-8"))
                return {"data": data, "latency_ms": round(elapsed_ms, 1), "status": "ok"}
        except urllib.error.HTTPError as e:
            self.errors.append(f"HTTP {e.code}: {e.reason}")
            raise
        except urllib.error.URLError as e:
            self.errors.append(f"URLError: {e.reason}")
            raise
        except Exception as e:
            self.errors.append(f"Error: {type(e).__name__}: {e}")
            raise

# ──────────────────────────────────────────────────────────────────────────────
# Symbol Resolution
# ──────────────────────────────────────────────────────────────────────────────

class ResolutionPolicy(str):
    CAPTURE_ALL = "capture_all"
    CANONICAL_BY_LIQUIDITY = "canonical_by_liquidity"

class AmbiguityStatus(str):
    SINGLE_RESOLUTION = "single_resolution"
    MULTI_RESOLUTION_CAPTURE_ALL = "multi_resolution_capture_all"
    MULTI_RESOLUTION_CANONICAL_SELECTED = "multi_resolution_canonical_selected"
    UNRESOLVED = "unresolved"

@dataclass
class ResolvedSymbol:
    display_symbol: str
    api_symbol: str
    dex_name: str

@dataclass
class SymbolResolutionGroup:
    """All resolved API symbols for a single display ticker."""
    display_symbol: str
    resolved_symbols: list[ResolvedSymbol] = field(default_factory=list)
    ambiguity_status: str = AmbiguityStatus.UNRESOLVED
    selected_for_capture: list[str] = field(default_factory=list)  # api_symbols selected
    selection_reason: str = ""
    liquidity_snapshot_used: bool = False

@dataclass
class ResolutionPolicyConfig:
    policy: ResolutionPolicy = ResolutionPolicy.CAPTURE_ALL
    selected_symbols: list[ResolvedSymbol] = field(default_factory=list)
    groups: list[SymbolResolutionGroup] = field(default_factory=list)

def resolve_symbols(info_cp: PublicInfoChokepoint, requested_symbols: list[str],
                    policy: str | ResolutionPolicy = ResolutionPolicy.CAPTURE_ALL) -> ResolutionPolicyConfig:
    """Resolve display symbols to actual builder DEX API symbols.

    Groups results by display ticker with explicit ambiguity status.
    Default policy: capture_all (capture every resolved API symbol separately).

    Returns ResolutionPolicyConfig with groups and selected symbols.
    """
    # Accept both str and ResolutionPolicy enum
    if isinstance(policy, str):
        policy = ResolutionPolicy(policy)
    dex_resp = info_cp.post_info({"type": "perpDexs"})
    dex_data = dex_resp["data"]
    if not isinstance(dex_data, list):
        raise ValueError(f"perpDexs returned non-list: {type(dex_data)}")

    dex_names = [d.get("name", "") for d in dex_data if isinstance(d, dict) and d.get("name")]

    symbol_map: dict[str, list[ResolvedSymbol]] = defaultdict(list)
    for dex_name in dex_names:
        try:
            ctx_resp = info_cp.post_info({"type": "metaAndAssetCtxs", "dex": dex_name})
            ctx_data = ctx_resp["data"]
            if not isinstance(ctx_data, list) or len(ctx_data) < 2:
                logger.warning(f"metaAndAssetCtxs for {dex_name} returned unexpected structure: {type(ctx_data)}")
                continue
            universe_info = ctx_data[0]
            asset_ctxs = ctx_data[1]
            if not isinstance(universe_info, dict) or not isinstance(asset_ctxs, list):
                continue
            universe = universe_info.get("universe", [])
            if not isinstance(universe, list):
                continue
            for i, item in enumerate(universe):
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                if not name:
                    continue
                parts = name.split(":")
                if len(parts) < 2:
                    continue
                coin = parts[1]
                coin_upper = coin.upper()
                for req_sym in requested_symbols:
                    if coin_upper == req_sym.upper():
                        api_symbol = f"{dex_name}:{coin}" if dex_name else coin
                        symbol_map[req_sym.upper()].append(ResolvedSymbol(
                            display_symbol=req_sym,
                            api_symbol=api_symbol,
                            dex_name=dex_name,
                        ))
        except Exception as e:
            logger.warning(f"Failed to query metaAndAssetCtxs for {dex_name}: {e}")
            continue

    groups: list[SymbolResolutionGroup] = []
    all_selected: list[ResolvedSymbol] = []
    seen = set()

    for req_sym in requested_symbols:
        sym_upper = req_sym.upper()
        entries = symbol_map.get(sym_upper, [])
        if not entries:
            groups.append(SymbolResolutionGroup(
                display_symbol=req_sym,
                ambiguity_status=AmbiguityStatus.UNRESOLVED,
            ))
            continue
        unique_entries = [e for e in entries if e.api_symbol not in seen]
        for e in unique_entries:
            seen.add(e.api_symbol)
        ambiguity = AmbiguityStatus.SINGLE_RESOLUTION if len(unique_entries) == 1 else AmbiguityStatus.MULTI_RESOLUTION_CAPTURE_ALL
        group = SymbolResolutionGroup(
            display_symbol=req_sym,
            resolved_symbols=unique_entries,
            ambiguity_status=ambiguity,
            selected_for_capture=[e.api_symbol for e in unique_entries],
            selection_reason=f"Policy: {policy} — all {len(unique_entries)} DEX markets captured",
        )
        groups.append(group)
        all_selected.extend(unique_entries)

    if not all_selected:
        raise ValueError(f"No symbols resolved for {requested_symbols}")

    return ResolutionPolicyConfig(
        policy=policy,
        selected_symbols=all_selected,
        groups=groups,
    )


# ──────────────────────────────────────────────────────────────────────────────
# L2 Book Parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_l2_book(l2_data: dict, api_symbol: str, display_symbol: str, dex_name: str,
                  ts_utc: str, latency_ms: float) -> dict:
    """Parse an l2Book response into structured metrics.

    l2Book returns: {"coin": "...", "time": ..., "levels": [[{"px": "...", "sz": "...", "n": ...}, ...], [...]]}
    Each level is a list of dicts with "px" (price), "sz" (size), "n" (order count).
    """
    levels = l2_data.get("levels", [])
    if not isinstance(levels, list) or len(levels) < 2:
        return {
            "timestamp_utc": ts_utc, "latency_ms": latency_ms,
            "api_symbol": api_symbol, "display_symbol": display_symbol, "dex_name": dex_name,
            "raw_book": l2_data,
            "bids": [], "asks": [],
            "best_bid": None, "best_ask": None, "mid": None,
            "spread_bps": None,
            "depth_usd_100_bid": 0, "depth_usd_100_ask": 0,
            "depth_usd_500_bid": 0, "depth_usd_500_ask": 0,
            "depth_usd_1000_bid": 0, "depth_usd_1000_ask": 0,
            "depth_usd_5000_bid": 0, "depth_usd_5000_ask": 0,
            "empty_bid_side": True, "empty_ask_side": True,
        }

    bids = levels[0]  # [{"px": "...", "sz": "...", "n": ...}, ...]
    asks = levels[1]

    def extract_price(level):
        if isinstance(level, dict):
            try:
                return float(level.get("px", 0))
            except (ValueError, TypeError):
                return None
        elif isinstance(level, list) and len(level) >= 2:
            try:
                return float(level[0])
            except (ValueError, TypeError):
                return None
        return None

    def extract_size(level):
        if isinstance(level, dict):
            try:
                return float(level.get("sz", 0))
            except (ValueError, TypeError):
                return None
        elif isinstance(level, list) and len(level) >= 2:
            try:
                return float(level[1])
            except (ValueError, TypeError):
                return None
        return None

    best_bid = extract_price(bids[0]) if bids else None
    best_ask = extract_price(asks[0]) if asks else None

    mid = None
    spread_bps = None
    if best_bid and best_ask and best_bid > 0:
        mid = (best_bid + best_ask) / 2
        spread_bps = ((best_ask - best_bid) / mid) * 10000

    def compute_depth(price_levels, mid_price):
        if not price_levels or mid_price is None or mid_price <= 0:
            return 0
        cumulative = 0.0
        for level in price_levels:
            price = extract_price(level)
            size = extract_size(level)
            if price is None or size is None or price <= 0:
                continue
            cumulative += size * price
            if cumulative >= 5000:
                break
        return round(cumulative, 2)

    return {
        "timestamp_utc": ts_utc, "latency_ms": latency_ms,
        "api_symbol": api_symbol, "display_symbol": display_symbol, "dex_name": dex_name,
        "raw_book": l2_data,
        "bids": bids[:20], "asks": asks[:20],
        "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
        "spread_bps": round(spread_bps, 4) if spread_bps is not None else None,
        "depth_usd_100_bid": compute_depth(bids, mid),
        "depth_usd_100_ask": compute_depth(asks, mid),
        "depth_usd_500_bid": compute_depth(bids, mid),
        "depth_usd_500_ask": compute_depth(asks, mid),
        "depth_usd_1000_bid": compute_depth(bids, mid),
        "depth_usd_1000_ask": compute_depth(asks, mid),
        "depth_usd_5000_bid": compute_depth(bids, mid),
        "depth_usd_5000_ask": compute_depth(asks, mid),
        "empty_bid_side": len(bids) == 0,
        "empty_ask_side": len(asks) == 0,
    }


def parse_asset_context(ctx_data, api_symbol: str, display_symbol: str, dex_name: str,
                        ts_utc: str, latency_ms: float) -> dict:
    """Parse metaAndAssetCtxs response for a specific symbol.

    API returns [universe_info_dict, assetCtxs_list].
    universe[i].name (e.g. "cash:TSLA") corresponds to assetCtxs[i].
    """
    if isinstance(ctx_data, list) and len(ctx_data) >= 2:
        universe_info = ctx_data[0]
        asset_ctxs = ctx_data[1]
    elif isinstance(ctx_data, dict):
        universe_info = ctx_data
        asset_ctxs = ctx_data.get("assetCtxs", [])
    else:
        asset_ctxs = []
        universe_info = {}

    if not isinstance(asset_ctxs, list):
        asset_ctxs = []

    result = {
        "timestamp_utc": ts_utc, "latency_ms": latency_ms,
        "api_symbol": api_symbol, "display_symbol": display_symbol, "dex_name": dex_name,
        "raw_context": ctx_data,
        "mark_price": None, "oracle_price": None, "mid_price": None,
        "funding": None, "open_interest": None, "day_volume": None,
    }

    # Try to match by universe name
    universe = []
    if isinstance(universe_info, dict):
        universe = universe_info.get("universe", [])
        if not isinstance(universe, list):
            universe = []

    for i, ctx in enumerate(asset_ctxs):
        if not isinstance(ctx, dict):
            continue

        # Try to get coin from universe list
        coin = ""
        if i < len(universe) and isinstance(universe[i], dict):
            name = universe[i].get("name", "")
            if name and ":" in name:
                coin = name.split(":", 1)[1]

        # Also check if context has a coin field (backward compat)
        if not coin:
            coin = ctx.get("coin", "")

        if coin.upper() == display_symbol.upper():
            result["mark_price"] = ctx.get("markPx") or ctx.get("markPrice")
            result["oracle_price"] = ctx.get("oraclePx") or ctx.get("oraclePrice")
            result["mid_price"] = ctx.get("midPx") or ctx.get("midPrice")
            result["funding"] = ctx.get("funding")
            result["open_interest"] = ctx.get("openInterest")
            result["day_volume"] = ctx.get("dayNtlVlm")
            break

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Candle Snapshot Parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_candle_snapshot(resp_data: list, api_symbol: str, display_symbol: str, dex_name: str,
                          ts_utc: str, latency_ms: float) -> dict:
    """Parse candleSnapshot response.

    API returns list of dicts: {t, T, s, i, o, c, h, l, v, n}
    """
    if not isinstance(resp_data, list):
        return {
            "timestamp_utc": ts_utc, "latency_ms": latency_ms,
            "api_symbol": api_symbol, "display_symbol": display_symbol, "dex_name": dex_name,
            "bars_returned": 0, "latest_candle_timestamp": None,
            "latest_close": None, "latest_volume": None, "gap_from_current_seconds": None,
        }

    bars = []
    for c in resp_data:
        if not isinstance(c, dict):
            continue
        try:
            bars.append({
                "timestamp_ms": int(c["t"]),
                "open": float(c["o"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
                "count": int(c.get("n", 0)),
            })
        except (ValueError, KeyError):
            continue

    latest_close = None
    latest_volume = None
    latest_ts = None
    gap_seconds = None

    if bars:
        latest = bars[-1]
        latest_close = latest["close"]
        latest_volume = latest["volume"]
        latest_ts = latest["timestamp_ms"]
        gap_seconds = round((datetime.now(timezone.utc).timestamp() * 1000 - latest_ts) / 1000, 1)

    return {
        "timestamp_utc": ts_utc, "latency_ms": latency_ms,
        "api_symbol": api_symbol, "display_symbol": display_symbol, "dex_name": dex_name,
        "bars_returned": len(bars),
        "latest_candle_timestamp": latest_ts,
        "latest_close": latest_close,
        "latest_volume": latest_volume,
        "gap_from_current_seconds": gap_seconds,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Anchor Capture
# ──────────────────────────────────────────────────────────────────────────────

def fetch_anchor_yahoo(symbol: str, timeout: int = 15) -> Optional[dict]:
    """Fetch a public price anchor from Yahoo Finance (no auth).

    Returns None if unavailable.
    """
    import urllib.parse
    ticker_map = {
        "TSLA": "TSLA", "AAPL": "AAPL", "MSFT": "MSFT", "NVDA": "NVDA",
        "AMZN": "AMZN", "GOOG": "GOOGL", "GOOGL": "GOOGL", "META": "META",
        "GOLD": "GC=F", "WTI": "CL=F", "OIL": "CL=F",
        "SPX": "^GSPC", "NDX": "^NDX", "NAS100": "^NDX", "QQQ": "QQQ",
    }
    ticker = ticker_map.get(symbol.upper())
    if not ticker:
        return None

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.monotonic() - start) * 1000
            data = json.loads(resp.read().decode("utf-8"))
            # Extract latest close price
            chart = data.get("chart", {})
            result = chart.get("result", [])
            if not result:
                return None
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if price is None:
                return None

            raw_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]

            now = datetime.now(timezone.utc)
            reg_time = meta.get("regularMarketTime")
            if reg_time:
                from datetime import timezone as _tz
                reg_dt = datetime.fromtimestamp(reg_time, tz=_tz.utc)
                staleness = (now - reg_dt).total_seconds()
            else:
                staleness = 0

            return {
                "anchor_symbol": symbol,
                "price": price,
                "source": "yahoo_finance",
                "source_latency_ms": round(elapsed_ms, 1),
                "staleness_seconds": round(staleness, 1),
                "raw_hash": raw_hash,
                "timestamp_utc": _now_utc(),
                "raw_response": data,
            }
    except Exception as e:
        logger.warning(f"Anchor fetch failed for {symbol}: {e}")
        return None

# ──────────────────────────────────────────────────────────────────────────────
# Calendar Classification
# ──────────────────────────────────────────────────────────────────────────────

def classify_market_session(dt: datetime) -> str:
    """Classify a UTC datetime into ET market session buckets.

    Uses zoneinfo for correct EDT/EST switching.
    Regular session: 09:30-16:00 America/New_York weekdays.
    Holiday calendar not available in v0; weekends still classified.

    Returns one of:
    - regular_hours: Mon-Fri 09:30-16:00 ET
    - premarket: Mon-Fri 00:00-09:30 ET
    - after_hours: Mon-Fri 16:00-23:59 ET
    - overnight: Mon-Fri 00:00-06:00 ET (deep overnight, before premarket)
    - weekend_or_holiday: Sat-Sun
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    ny_tz = ZoneInfo("America/New_York")
    et_dt = dt.astimezone(ny_tz)
    day_of_week = et_dt.weekday()  # 0=Mon, 6=Sun

    if day_of_week >= 5:  # Saturday or Sunday
        return "weekend_or_holiday"

    et_hour = et_dt.hour
    et_minute = et_dt.minute

    # Regular session: 09:30-16:00 ET
    if et_hour == 9 and et_minute >= 30:
        return "regular_hours"
    if 10 <= et_hour < 16:
        return "regular_hours"
    if et_hour == 16 and et_minute == 0:
        # 16:00:00 is the instant market closes; classify as after_hours
        return "after_hours"

    if et_hour >= 16:
        return "after_hours"

    if et_hour < 6:
        return "overnight"

    # 06:00-09:29 ET
    return "premarket"

# ──────────────────────────────────────────────────────────────────────────────
# Derived Metrics
# ──────────────────────────────────────────────────────────────────────────────

def compute_derived_metrics(l2_snapshot: dict, ctx_snapshot: dict, anchor: Optional[dict]) -> dict:
    """Compute per-poll derived metrics. No PnL, no trade signals."""
    ts = l2_snapshot.get("timestamp_utc", _now_utc())
    sym = l2_snapshot.get("display_symbol", "")
    api_sym = l2_snapshot.get("api_symbol", "")
    dex = l2_snapshot.get("dex_name", "")

    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    session = classify_market_session(dt)

    bid = l2_snapshot.get("best_bid")
    ask = l2_snapshot.get("best_ask")
    mid = l2_snapshot.get("mid")
    spread_bps = l2_snapshot.get("spread_bps")

    mark = ctx_snapshot.get("mark_price")
    oracle = ctx_snapshot.get("oracle_price")

    anchor_price = None
    anchor_staleness = None
    residual_bps = None
    if anchor:
        anchor_price = anchor.get("price")
        anchor_staleness = anchor.get("staleness_seconds")
        if anchor_price and mid and mid > 0:
            residual_bps = round(((mid - anchor_price) / anchor_price) * 10000, 4)

    # Capture quality
    quality = "good"
    if l2_snapshot.get("empty_bid_side") or l2_snapshot.get("empty_ask_side"):
        quality = "degraded_one_side"
    if l2_snapshot.get("spread_bps") is None:
        quality = "no_spread"

    return {
        "timestamp_utc": ts,
        "symbol": sym,
        "api_symbol": api_sym,
        "dex_name": dex,
        "market_session_bucket": session,
        "best_bid": bid,
        "best_ask": ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "depth_usd_100_bid": l2_snapshot.get("depth_usd_100_bid"),
        "depth_usd_100_ask": l2_snapshot.get("depth_usd_100_ask"),
        "depth_usd_1000_bid": l2_snapshot.get("depth_usd_1000_bid"),
        "depth_usd_1000_ask": l2_snapshot.get("depth_usd_1000_ask"),
        "depth_usd_5000_bid": l2_snapshot.get("depth_usd_5000_bid"),
        "depth_usd_5000_ask": l2_snapshot.get("depth_usd_5000_ask"),
        "mark": mark,
        "oracle": oracle,
        "anchor_price": anchor_price,
        "anchor_staleness_seconds": anchor_staleness,
        "diagnostic_residual_bps": residual_bps,
        "empty_bid_side": l2_snapshot.get("empty_bid_side", True),
        "empty_ask_side": l2_snapshot.get("empty_ask_side", True),
        "capture_quality_status": quality,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Forward Recorder
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ForwardRecorderConfig:
    out_root: str = "reports/hip3_builder_dex_tradfi_forward_recorder_v0"
    symbols: list[str] = field(default_factory=lambda: list(PRIMARY_SYMBOLS))
    include_secondary: bool = False
    poll_seconds: int = DEFAULT_POLL_SECONDS
    duration_minutes: int = 1440
    max_runtime_seconds: Optional[int] = None
    max_records: Optional[int] = None
    allow_network_public: bool = False
    enable_anchors: bool = False
    anchor_source: str = "yahoo"
    dry_run: bool = False
    once: bool = False
    stop_after_init: bool = False
    run_id: str = ""
    study_id: str = STUDY_ID
    resolution_policy: str = ResolutionPolicy.CAPTURE_ALL

@dataclass
class CaptureState:
    """Mutable state for the polling loop."""
    symbol_resolutions: list[ResolvedSymbol] = field(default_factory=list)
    previous_l2: dict[str, dict] = field(default_factory=dict)  # api_symbol -> last snapshot
    total_polls: int = 0
    total_errors: int = 0
    status: str = RecorderStatus.READY
    start_time_utc: Optional[str] = None
    last_heartbeat_utc: Optional[str] = None
    anchor_available_count: int = 0
    anchor_unavailable_count: int = 0
    l2_captured_count: int = 0
    candle_captured_count: int = 0
    ctx_captured_count: int = 0

def create_report_dir(out_root: str) -> tuple:
    """Create the run directory with all subdirectories.

    Returns (run_id, run_dir) where run_dir = out_root/run_id with all subdirs.
    """
    run_id = hashlib.md5(_now_utc().encode()).hexdigest()[:8]
    run_dir = Path(out_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ["l2_snapshots", "asset_context_snapshots", "candle_snapshots",
                   "anchor_snapshots", "derived_metrics"]:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)
    return run_id, run_dir

def write_run_manifest(run_dir: Path, config: ForwardRecorderConfig,
                       symbol_resolutions: list[ResolvedSymbol],
                       git_sha: str, git_dirty: bool, branch: str,
                       command_args: list[str]) -> None:
    """Write run_manifest.json."""
    api_symbols = [s.api_symbol for s in symbol_resolutions]
    dex_names = list(set(s.dex_name for s in symbol_resolutions))
    manifest = {
        "study_id": config.study_id,
        "run_id": config.run_id,
        "created_at_utc": _now_utc(),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "repo_root": str(Path(__file__).resolve().parent.parent.parent.parent),
        "branch": branch,
        "command_args": command_args,
        "safety_mode": "public_data_observer_only",
        "symbols_requested": config.symbols,
        "symbols_resolved": [s.display_symbol for s in symbol_resolutions],
        "api_symbols": api_symbols,
        "dex_names": dex_names,
        "poll_seconds": config.poll_seconds,
        "anchor_sources_enabled": config.enable_anchors,
        "no_orders_no_auth_no_live_confirmation": True,
        "official_s3_l2_blocker_acknowledged": True,
        "official_candle_history_available_acknowledged": True,
        "recorder_purpose": "forward_execution_feasibility_data_collection",
    }
    _write_json_atomic(run_dir / "run_manifest.json", manifest)

def write_capture_status(run_dir: Path, state: CaptureState, run_id: str = "") -> None:
    """Write capture_status.json."""
    status = {
        "run_id": run_id or getattr(state, "_run_id", ""),
        "status": state.status,
        "total_polls": state.total_polls,
        "total_errors": state.total_errors,
        "start_time_utc": state.start_time_utc,
        "last_heartbeat_utc": state.last_heartbeat_utc,
        "anchor_available_count": state.anchor_available_count,
        "anchor_unavailable_count": state.anchor_unavailable_count,
        "l2_captured_count": state.l2_captured_count,
        "candle_captured_count": state.candle_captured_count,
        "ctx_captured_count": state.ctx_captured_count,
    }
    _write_json_atomic(run_dir / "capture_status.json", status)

def run_forward_recorder(config: ForwardRecorderConfig) -> dict:
    """Main forward recorder loop.

    Returns final status dict.
    """
    git_sha, git_dirty = _git_info()
    branch = "unknown"
    try:
        branch = os.popen("git branch --show-current 2>/dev/null").read().strip()
    except Exception:
        pass

    run_id, run_dir = create_report_dir(config.out_root)

    # Write manifest
    write_run_manifest(run_dir, config, [], git_sha, git_dirty, branch, sys.argv[1:])

    # Write capture config
    config_dict = {k: v for k, v in vars(config).items() if not k.startswith("_")}
    _write_json_atomic(run_dir / "capture_config.json", config_dict)

    state = CaptureState()
    state._run_id = config.run_id

    # Initialize
    state.status = RecorderStatus.CAPTURE_STARTED
    state.start_time_utc = _now_utc()
    write_capture_status(run_dir, state, config.run_id)

    # Write heartbeat
    _append_jsonl(run_dir / "heartbeat.jsonl", {
        "event": "started", "timestamp_utc": state.start_time_utc,
        "run_id": config.run_id,
    })

    # Write errors file
    errors_path = run_dir / "errors.jsonl"
    errors_path.touch(exist_ok=True)

    # Write capture index
    index_path = run_dir / "capture_index.jsonl"
    index_path.touch(exist_ok=True)

    # Resolve symbols
    info_cp = PublicInfoChokepoint(allow_network=not config.dry_run and config.allow_network_public)

    try:
        state.status = RecorderStatus.SYMBOLS_RESOLVED
        resolution_config = resolve_symbols(info_cp, config.symbols, policy=config.resolution_policy)
        state.symbol_resolutions = resolution_config.selected_symbols

        # Update manifest with resolved symbols
        write_run_manifest(run_dir, config, resolution_config.selected_symbols, git_sha, git_dirty, branch, sys.argv[1:])

        # Write symbol resolution with full group info
        sym_res_data = {
            "run_id": config.run_id,
            "resolved_at_utc": _now_utc(),
            "policy": str(config.resolution_policy),
            "groups": [
                {
                    "display_symbol": g.display_symbol,
                    "ambiguity_status": g.ambiguity_status,
                    "resolved_api_symbols": [s.api_symbol for s in g.resolved_symbols],
                    "resolved_dex_names": list(set(s.dex_name for s in g.resolved_symbols)),
                    "selected_for_capture": g.selected_for_capture,
                    "selection_reason": g.selection_reason,
                }
                for g in resolution_config.groups
            ],
        }
        _write_json_atomic(run_dir / "symbol_resolution.json", sym_res_data)

        # Write capture index entry
        _append_jsonl(index_path, {
            "event": "symbols_resolved",
            "timestamp_utc": _now_utc(),
            "symbols": [s.display_symbol for s in resolution_config.selected_symbols],
        })

    except Exception as e:
        state.status = RecorderStatus.SYMBOL_RESOLUTION_FAILED
        write_capture_status(run_dir, state, config.run_id)
        _append_jsonl(errors_path, {
            "timestamp_utc": _now_utc(),
            "error": str(e),
            "phase": "symbol_resolution",
            "traceback": traceback.format_exc(),
        })
        return {"status": state.status, "error": str(e)}

    if config.dry_run:
        state.status = RecorderStatus.DRY_RUN_READY
        write_capture_status(run_dir, state, config.run_id)
        _append_jsonl(run_dir / "heartbeat.jsonl", {
            "event": "dry_run_complete",
            "timestamp_utc": _now_utc(),
            "symbols_resolved": [s.display_symbol for s in resolution_config.selected_symbols],
        })
        return {"status": state.status, "dry_run": True, "run_dir": str(run_dir)}

    if config.stop_after_init:
        state.status = RecorderStatus.SYMBOLS_RESOLVED
        write_capture_status(run_dir, state, config.run_id)
        _append_jsonl(run_dir / "heartbeat.jsonl", {
            "event": "stop_after_init",
            "timestamp_utc": _now_utc(),
            "symbols_resolved": [s.display_symbol for s in resolution_config.selected_symbols],
        })
        return {"status": state.status, "run_dir": str(run_dir)}

    # Polling loop
    state.status = RecorderStatus.CAPTURE_RUNNING
    start_time = time.time()
    poll_count = 0

    last_candle_poll_time = 0
    candle_poll_interval = 300  # 5 minutes

    while True:
        now = time.time()

        # Duration limit
        if config.duration_minutes > 0:
            elapsed_min = (now - start_time) / 60
            if elapsed_min >= config.duration_minutes:
                break

        # Max runtime limit
        if config.max_runtime_seconds is not None:
            elapsed = now - start_time
            if elapsed >= config.max_runtime_seconds:
                break

        # Max records limit
        if config.max_records is not None and state.total_polls >= config.max_records:
            break

        # One-shot mode
        if config.once and state.total_polls > 0:
            break

        poll_count += 1
        state.total_polls = poll_count
        poll_ts = _now_utc()

        poll_errors = 0

        for sym_res in resolution_config.selected_symbols:
            api_sym = sym_res.api_symbol
            display_sym = sym_res.display_symbol
            dex_name = sym_res.dex_name

            try:
                # 1. Fetch L2 book
                l2_resp = info_cp.post_info({"type": "l2Book", "coin": api_sym})
                l2_data = l2_resp["data"]
                l2_ts = _now_utc()
                l2_latency = l2_resp.get("latency_ms", 0)

                l2_snapshot = parse_l2_book(l2_data, api_sym, display_sym, dex_name, l2_ts, l2_latency)

                # Stale detection
                prev = state.previous_l2.get(api_sym)
                if prev:
                    l2_snapshot["stale_relative_to_previous_snapshot"] = (
                        l2_ts != prev.get("timestamp_utc")
                    )
                state.previous_l2[api_sym] = l2_snapshot

                # Write L2 snapshot
                date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
                l2_file = run_dir / "l2_snapshots" / f"{date_str}.jsonl"
                _append_jsonl(l2_file, l2_snapshot)
                state.l2_captured_count += 1

                # 2. Fetch asset context
                ctx_resp = info_cp.post_info({"type": "metaAndAssetCtxs", "dex": dex_name})
                ctx_data = ctx_resp["data"]
                ctx_ts = _now_utc()
                ctx_latency = ctx_resp.get("latency_ms", 0)

                ctx_snapshot = parse_asset_context(ctx_data, api_sym, display_sym, dex_name, ctx_ts, ctx_latency)

                # Write asset context
                ctx_file = run_dir / "asset_context_snapshots" / f"{date_str}.jsonl"
                _append_jsonl(ctx_file, ctx_snapshot)
                state.ctx_captured_count += 1

                # 3. Fetch anchor if enabled
                anchor = None
                if config.enable_anchors:
                    anchor = fetch_anchor_yahoo(display_sym)
                    if anchor:
                        anchor["api_symbol"] = api_sym
                        anchor["display_symbol"] = display_sym
                        anchor["dex_name"] = dex_name
                        anchor_file = run_dir / "anchor_snapshots" / f"{date_str}.jsonl"
                        _append_jsonl(anchor_file, anchor)
                        state.anchor_available_count += 1
                    else:
                        state.anchor_unavailable_count += 1

                # 4. Compute derived metrics
                metrics = compute_derived_metrics(l2_snapshot, ctx_snapshot, anchor)
                metrics_file = run_dir / "derived_metrics" / f"{date_str}.jsonl"
                _append_jsonl(metrics_file, metrics)

                # 5. Write capture index entry
                _append_jsonl(index_path, {
                    "event": "poll",
                    "poll_number": poll_count,
                    "timestamp_utc": poll_ts,
                    "symbol": display_sym,
                    "api_symbol": api_sym,
                    "l2_captured": True,
                    "ctx_captured": True,
                    "anchor_available": anchor is not None,
                })

            except Exception as e:
                poll_errors += 1
                state.total_errors += 1
                _append_jsonl(errors_path, {
                    "timestamp_utc": poll_ts,
                    "poll": poll_count,
                    "symbol": display_sym,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                })

        # Candle continuity poll (every 5 minutes)
        if now - last_candle_poll_time >= candle_poll_interval:
            last_candle_poll_time = now
            candle_ts = _now_utc()

            for sym_res in resolution_config.selected_symbols:
                api_sym = sym_res.api_symbol
                display_sym = sym_res.display_symbol
                dex_name = sym_res.dex_name

                try:
                    # 1m candles (last 2 hours)
                    now_dt = datetime.now(timezone.utc)
                    start_1m = now_dt - timedelta(minutes=CANDLE_1M_INTERVAL_MINUTES)
                    end_1m = now_dt

                    candle_resp_1m = info_cp.post_info({
                        "type": "candleSnapshot",
                        "req": {
                            "coin": api_sym,
                            "interval": "1m",
                            "startTime": _epoch_ms(start_1m),
                            "endTime": _epoch_ms(end_1m),
                        }
                    })
                    candle_1m = parse_candle_snapshot(
                        candle_resp_1m["data"], api_sym, display_sym, dex_name, candle_ts,
                        candle_resp_1m.get("latency_ms", 0)
                    )

                    # 15m candles (last 24 hours)
                    start_15m = now_dt - timedelta(hours=CANDLE_15M_INTERVAL_HOURS)
                    candle_resp_15m = info_cp.post_info({
                        "type": "candleSnapshot",
                        "req": {
                            "coin": api_sym,
                            "interval": "15m",
                            "startTime": _epoch_ms(start_15m),
                            "endTime": _epoch_ms(now_dt),
                        }
                    })
                    candle_15m = parse_candle_snapshot(
                        candle_resp_15m["data"], api_sym, display_sym, dex_name, candle_ts,
                        candle_resp_15m.get("latency_ms", 0)
                    )

                    # Write candle snapshots
                    candle_file = run_dir / "candle_snapshots" / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
                    _append_jsonl(candle_file, candle_1m)
                    _append_jsonl(candle_file, candle_15m)
                    state.candle_captured_count += 2

                except Exception as e:
                    _append_jsonl(errors_path, {
                        "timestamp_utc": candle_ts,
                        "phase": "candle",
                        "symbol": display_sym,
                        "error": str(e),
                    })

        # Write heartbeat
        state.last_heartbeat_utc = _now_utc()
        _append_jsonl(run_dir / "heartbeat.jsonl", {
            "event": "heartbeat",
            "timestamp_utc": state.last_heartbeat_utc,
            "poll": poll_count,
            "errors_this_poll": poll_errors,
            "total_errors": state.total_errors,
            "l2_captured": state.l2_captured_count,
            "candle_captured": state.candle_captured_count,
            "ctx_captured": state.ctx_captured_count,
        })

        # Write status
        write_capture_status(run_dir, state, config.run_id)

        # Sleep
        if not config.once:
            time.sleep(config.poll_seconds)

    # Final status
    if state.total_errors > 0 and state.total_polls > 0:
        state.status = RecorderStatus.PARTIAL_CAPTURE
    else:
        state.status = RecorderStatus.CAPTURE_COMPLETE

    write_capture_status(run_dir, state, config.run_id)

    _append_jsonl(run_dir / "heartbeat.jsonl", {
        "event": "stopped",
        "timestamp_utc": _now_utc(),
        "final_status": state.status,
        "total_polls": state.total_polls,
        "total_errors": state.total_errors,
    })

    return {"status": state.status, "run_dir": str(run_dir)}

# ──────────────────────────────────────────────────────────────────────────────
# Summarizer
# ──────────────────────────────────────────────────────────────────────────────

def summarize_forward_capture(report_dir: str) -> dict:
    """Summarize captured forward data with per-API-symbol breakdowns.

    Produces forward_capture_summary.json and forward_capture_summary.md.
    Metrics are keyed by api_symbol, not display_symbol, to avoid multi-DEX collapse.
    """
    run_path = Path(report_dir)

    manifest = json.loads((run_path / "run_manifest.json").read_text()) if (run_path / "run_manifest.json").exists() else {}
    status_data = json.loads((run_path / "capture_status.json").read_text()) if (run_path / "capture_status.json").exists() else {}
    sym_res = json.loads((run_path / "symbol_resolution.json").read_text()) if (run_path / "symbol_resolution.json").exists() else {}

    metrics_files = sorted(run_path.glob("derived_metrics/*.jsonl"))
    all_metrics = []
    for mf in metrics_files:
        with open(mf) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_metrics.append(_safe_json_loads(line))
                    except Exception:
                        continue

    l2_files = sorted(run_path.glob("l2_snapshots/*.jsonl"))
    all_l2 = []
    for lf in l2_files:
        with open(lf) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_l2.append(_safe_json_loads(line))
                    except Exception:
                        continue

    anchor_files = sorted(run_path.glob("anchor_snapshots/*.jsonl"))
    all_anchors = []
    for af in anchor_files:
        with open(af) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_anchors.append(_safe_json_loads(line))
                    except Exception:
                        continue

    total_polls = status_data.get("total_polls", 0)
    total_l2_samples = len(all_l2)
    total_metrics_samples = len(all_metrics)
    total_anchor_samples = len(all_anchors)
    total_errors = status_data.get("total_errors", 0)

    # Per-API-symbol metrics (primary breakdown)
    api_symbol_metrics = defaultdict(list)
    for m in all_metrics:
        api_sym = m.get("api_symbol", "unknown")
        api_symbol_metrics[api_sym].append(m)

    # Per-display-symbol metrics (secondary, clearly labeled)
    display_symbol_metrics = defaultdict(list)
    for m in all_metrics:
        sym = m.get("symbol", "unknown")
        display_symbol_metrics[sym].append(m)

    # Global spread stats
    all_spread_bps = [m["spread_bps"] for m in all_metrics if m.get("spread_bps") is not None]
    global_spread_stats = {}
    if all_spread_bps:
        sorted_spreads = sorted(all_spread_bps)
        n = len(sorted_spreads)
        global_spread_stats = {
            "count": n,
            "median_bps": round(sorted_spreads[n // 2], 4),
            "p75_bps": round(sorted_spreads[int(n * 0.75)], 4),
            "p90_bps": round(sorted_spreads[int(n * 0.9)], 4),
            "min_bps": round(sorted_spreads[0], 4),
            "max_bps": round(sorted_spreads[-1], 4),
        }

    # Per-API-symbol spread stats
    api_spread_stats = {}
    for api_sym, sym_metrics in api_symbol_metrics.items():
        vals = [m["spread_bps"] for m in sym_metrics if m.get("spread_bps") is not None]
        if vals:
            sv = sorted(vals)
            sn = len(sv)
            api_spread_stats[api_sym] = {
                "count": sn,
                "median_bps": round(sv[sn // 2], 4),
                "p75_bps": round(sv[int(sn * 0.75)], 4),
                "min_bps": round(sv[0], 4),
                "max_bps": round(sv[-1], 4),
            }

    # Global depth stats
    depth_keys = ["depth_usd_100_bid", "depth_usd_100_ask",
                  "depth_usd_1000_bid", "depth_usd_1000_ask",
                  "depth_usd_5000_bid", "depth_usd_5000_ask"]
    global_depth_stats = {}
    api_depth_stats = {}
    for dk in depth_keys:
        values = [m.get(dk, 0) for m in all_metrics if m.get(dk) is not None]
        if values:
            non_zero = sum(1 for v in values if v > 0)
            global_depth_stats[dk] = {
                "non_zero_rate": round(non_zero / len(values), 4),
                "mean": round(sum(values) / len(values), 2),
            }
            for api_sym, sym_metrics in api_symbol_metrics.items():
                sv = [m.get(dk, 0) for m in sym_metrics if m.get(dk) is not None]
                if sv:
                    nz = sum(1 for v in sv if v > 0)
                    if api_sym not in api_depth_stats:
                        api_depth_stats[api_sym] = {}
                    api_depth_stats[api_sym][dk] = {
                        "non_zero_rate": round(nz / len(sv), 4),
                        "mean": round(sum(sv) / len(sv), 2),
                    }

    empty_bid_count = sum(1 for m in all_metrics if m.get("empty_bid_side"))
    empty_ask_count = sum(1 for m in all_metrics if m.get("empty_ask_side"))
    total_l2 = len(all_l2)

    # Session counts
    session_counts = defaultdict(int)
    api_session_counts = defaultdict(lambda: defaultdict(int))
    for m in all_metrics:
        session = m.get("market_session_bucket", "unknown")
        api_sym = m.get("api_symbol", "unknown")
        session_counts[session] += 1
        api_session_counts[api_sym][session] += 1

    # Anchor stats
    anchor_available = len(all_anchors)
    anchor_staleness_values = [a.get("staleness_seconds", 0) for a in all_anchors if a.get("staleness_seconds") is not None]
    anchor_stats = {
        "available_count": anchor_available,
        "unavailable_count": total_polls - anchor_available if total_polls > 0 else 0,
        "availability_rate": round(anchor_available / max(total_anchor_samples, 1), 4),
    }
    if anchor_staleness_values:
        sorted_staleness = sorted(anchor_staleness_values)
        n = len(sorted_staleness)
        anchor_stats["median_staleness_seconds"] = round(sorted_staleness[n // 2], 1)
        anchor_stats["mean_staleness_seconds"] = round(sum(sorted_staleness) / n, 1)

    api_anchor_counts = defaultdict(int)
    for a in all_anchors:
        api_sym = a.get("api_symbol", "unknown")
        api_anchor_counts[api_sym] += 1

    # Diagnostic residuals
    residuals = [m.get("diagnostic_residual_bps") for m in all_metrics if m.get("diagnostic_residual_bps") is not None]
    residual_stats = {}
    if residuals:
        abs_residuals = [abs(r) for r in residuals]
        sorted_abs = sorted(abs_residuals)
        n = len(sorted_abs)
        residual_stats = {
            "count": n,
            "median_bps": round(sorted_abs[n // 2], 4),
            "p75_bps": round(sorted_abs[int(n * 0.75)], 4),
            "abs_ge_25_bps": sum(1 for r in sorted_abs if r >= 25),
            "abs_ge_50_bps": sum(1 for r in sorted_abs if r >= 50),
            "abs_ge_100_bps": sum(1 for r in sorted_abs if r >= 100),
        }

    two_sided = sum(1 for m in all_metrics if not m.get("empty_bid_side") and not m.get("empty_ask_side"))
    two_sided_rate = round(two_sided / max(len(all_metrics), 1), 4)

    # Concentration
    day_counts = defaultdict(int)
    symbol_counts = defaultdict(int)
    api_symbol_counts = defaultdict(int)
    for m in all_metrics:
        ts = m.get("timestamp_utc", "")
        day = ts[:10] if ts else "unknown"
        day_counts[day] += 1
        symbol_counts[m.get("symbol", "unknown")] += 1
        api_symbol_counts[m.get("api_symbol", "unknown")] += 1

    # Data sufficiency
    days_covered = len(day_counts)
    regular_sessions = session_counts.get("regular_hours", 0)
    off_hours_samples = session_counts.get("after_hours", 0) + session_counts.get("premarket", 0) + session_counts.get("overnight", 0)

    per_api_symbol_offhours = {}
    for api_sym in api_symbol_metrics:
        sym_metrics = api_symbol_metrics[api_sym]
        oh = sum(1 for m in sym_metrics if m.get("market_session_bucket") in ("after_hours", "premarket", "overnight"))
        per_api_symbol_offhours[api_sym] = oh

    per_display_symbol_offhours = {}
    for sym in display_symbol_metrics:
        sym_metrics = display_symbol_metrics[sym]
        oh = sum(1 for m in sym_metrics if m.get("market_session_bucket") in ("after_hours", "premarket", "overnight"))
        per_display_symbol_offhours[sym] = oh

    sufficiency = {
        "days_covered": days_covered,
        "regular_session_samples": regular_sessions,
        "off_hours_samples": off_hours_samples,
        "per_api_symbol_offhours": dict(per_api_symbol_offhours),
        "per_display_symbol_offhours": dict(per_display_symbol_offhours),
        "two_sided_book_rate": two_sided_rate,
        "median_spread_bps": global_spread_stats.get("median_bps"),
        "p75_spread_bps": global_spread_stats.get("p75_bps"),
    }

    required_offhours_per_symbol = 500
    required_days = 7
    required_regular_sessions = 3
    max_median_spread = 50
    max_p75_spread = 100
    min_two_sided_rate = 0.80

    checks = {
        "days_ge_7": days_covered >= required_days,
        "regular_sessions_ge_3": regular_sessions >= required_regular_sessions,
        "offhours_ge_500_per_symbol": all(
            per_api_symbol_offhours.get(s, 0) >= required_offhours_per_symbol for s in api_symbol_metrics
        ),
        "median_spread_le_50": global_spread_stats.get("median_bps", float("inf")) <= max_median_spread,
        "p75_spread_le_100": global_spread_stats.get("p75_bps", float("inf")) <= max_p75_spread,
        "two_sided_rate_ge_0_8": two_sided_rate >= min_two_sided_rate,
    }

    all_pass = all(checks.values())
    partial_pass = sum(1 for v in checks.values() if v) >= 4

    if not any(checks.values()):
        final_status = "FORWARD_DATA_UNDERPOWERED"
    elif partial_pass:
        final_status = "FORWARD_DATA_EXECUTION_FEASIBILITY_MEASURABLE"
    else:
        final_status = "FORWARD_DATA_UNDERPOWERED"

    if anchor_available == 0 and total_polls > 0:
        final_status = "FORWARD_DATA_ANCHOR_BLOCKED"

    summary = {
        "study_id": STUDY_ID,
        "run_id": manifest.get("run_id", ""),
        "generated_at_utc": _now_utc(),
        "resolution_policy": sym_res.get("policy", "capture_all"),
        "total_capture_duration_polls": total_polls,
        "total_errors": total_errors,
        "symbols": list(dict.fromkeys(manifest.get("symbols_resolved", []))),
        "api_symbols": manifest.get("api_symbols", []),
        "snapshots_per_api_symbol": dict(api_symbol_counts),
        "snapshots_per_display_symbol": dict(symbol_counts),
        "l2_availability_rate": round(total_l2_samples / max(total_l2_samples, 1), 4),
        "empty_bid_side_rate": round(empty_bid_count / max(total_l2, 1), 4),
        "empty_ask_side_rate": round(empty_ask_count / max(total_l2, 1), 4),
        "two_sided_book_rate": two_sided_rate,
        "spread_statistics": global_spread_stats,
        "spread_statistics_per_api_symbol": api_spread_stats,
        "depth_availability": global_depth_stats,
        "depth_availability_per_api_symbol": api_depth_stats,
        "anchor_availability_rate": anchor_stats.get("availability_rate"),
        "anchor_median_staleness_seconds": anchor_stats.get("median_staleness_seconds"),
        "anchor_availability_per_api_symbol": dict(api_anchor_counts),
        "session_counts": dict(session_counts),
        "session_counts_per_api_symbol": {k: dict(v) for k, v in api_session_counts.items()},
        "off_hours_sample_count": off_hours_samples,
        "regular_hours_sample_count": regular_sessions,
        "residual_distribution": residual_stats,
        "concentration_by_day": dict(sorted(day_counts.items())),
        "concentration_by_display_symbol": dict(sorted(symbol_counts.items())),
        "concentration_by_api_symbol": dict(sorted(api_symbol_counts.items())),
        "sufficiency_checks": checks,
        "sufficiency_assessment": sufficiency,
        "final_status": final_status,
        "forward_data_sufficient_for_phase_minus_1_tail_scout": all_pass,
    }

    summary_json = run_path / "forward_capture_summary.json"
    _write_json_atomic(summary_json, summary)

    md_lines = [
        "# Forward Capture Summary",
        "",
        f"Study: {STUDY_ID}",
        f"Run: {manifest.get('run_id', 'unknown')}",
        f"Generated: {summary['generated_at_utc']}",
        f"Resolution policy: {sym_res.get('policy', 'capture_all')}",
        "",
        "## Overview",
        f"- Total polls: {total_polls}",
        f"- Total errors: {total_errors}",
        f"- Symbols: {', '.join(manifest.get('symbols_resolved', []))}",
        f"- API symbols: {', '.join(manifest.get('api_symbols', []))}",
        f"- Status: **{final_status}**",
        "",
        "## Spread Statistics (Global)",
    ]
    if global_spread_stats:
        md_lines.extend([
            f"- Median spread: {global_spread_stats['median_bps']} bps",
            f"- P75 spread: {global_spread_stats['p75_bps']} bps",
            f"- P90 spread: {global_spread_stats['p90_bps']} bps",
            f"- Min/Max: {global_spread_stats['min_bps']} / {global_spread_stats['max_bps']} bps",
        ])
    else:
        md_lines.append("- No spread data available")

    md_lines.extend(["", "## Spread Statistics Per API Symbol"])
    for api_sym in sorted(api_spread_stats.keys()):
        ss = api_spread_stats[api_sym]
        md_lines.append(f"- **{api_sym}**: median={ss['median_bps']} bps, p75={ss['p75_bps']} bps, count={ss['count']}")

    md_lines.extend(["", "## Session Counts (Global)"])
    for session, count in sorted(session_counts.items()):
        md_lines.append(f"- {session}: {count}")

    md_lines.extend(["", "## Session Counts Per API Symbol"])
    for api_sym in sorted(api_session_counts.keys()):
        sc = api_session_counts[api_sym]
        md_lines.append(f"- **{api_sym}**: {', '.join(f'{s}={c}' for s, c in sorted(sc.items()))}")

    md_lines.extend([
        "",
        "## Anchor Availability",
        f"- Available: {anchor_available}",
        f"- Rate: {anchor_stats.get('availability_rate', 0)}",
        f"- Median staleness: {anchor_stats.get('median_staleness_seconds', 'N/A')}s",
        "",
        "## Per-API-Symbol Anchor Availability",
    ])
    for api_sym in sorted(api_anchor_counts.keys()):
        md_lines.append(f"- **{api_sym}**: {api_anchor_counts[api_sym]} anchors")

    md_lines.extend(["", "## Sufficiency Checks"])
    for check, passed in checks.items():
        md_lines.append(f"- {'PASS' if passed else 'FAIL'}: {check}")

    md_lines.extend(["", "## Per-API-Symbol Off-Hours Samples"])
    for api_sym, count in sorted(per_api_symbol_offhours.items()):
        md_lines.append(f"- {api_sym}: {count}")

    md_lines.extend(["", "## Per-Display-Symbol Off-Hours Samples (for reference)"])
    for sym, count in sorted(per_display_symbol_offhours.items()):
        md_lines.append(f"- {sym}: {count}")

    md_lines.extend(["", "## Residual Distribution (if anchor available)"])
    if residual_stats:
        md_lines.extend([
            f"- Count: {residual_stats.get('count', 0)}",
            f"- Median: {residual_stats.get('median_bps', 'N/A')} bps",
            f"- |residual| >= 25 bps: {residual_stats.get('abs_ge_25_bps', 0)}",
            f"- |residual| >= 50 bps: {residual_stats.get('abs_ge_50_bps', 0)}",
            f"- |residual| >= 100 bps: {residual_stats.get('abs_ge_100_bps', 0)}",
        ])
    else:
        md_lines.append("- No anchor data available for residuals")

    md_lines.extend([
        "",
        "## Data Sufficiency",
        f"- Days covered: {days_covered}",
        f"- Regular session samples: {regular_sessions} (required: {required_regular_sessions})",
        f"- Off-hours samples: {off_hours_samples}",
        f"- Two-sided book rate: {two_sided_rate}",
        "",
        "## Notes",
        "- Metrics are keyed by **api_symbol** (e.g., `cash:TSLA`) to avoid multi-DEX collapse.",
        "- Display-symbol counts are included for reference only.",
        "- Multi-DEX markets for the same display ticker are kept separate.",
        "- Analysis should choose canonical markets only after enough liquidity data exists.",
    ])

    md_text = "\n".join(md_lines)
    md_path = run_path / "forward_capture_summary.md"
    md_path.write_text(md_text)

    print(f"Summary written to: {summary_json}")
    print(f"Markdown written to: {md_path}")

    return summary
