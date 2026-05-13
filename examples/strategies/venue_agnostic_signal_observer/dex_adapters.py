"""DEX data adapters for public REST APIs.

Sources:
- DEX Screener (api.dexscreener.com) — pair/profile data, no API key
- GeckoTerminal / CoinGecko — DEX pool OHLCV (documented free endpoints)

**Observer-only. No execution, no orders, no keys.**
"""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import httpx

from .dex_models import DexPoolSnapshot

_TIMEOUT = 15  # seconds
_RATE_LIMIT_DELAY = 1.0  # seconds between calls to avoid 429s


# ---------------------------------------------------------------------------
# DEX Screener adapter
# ---------------------------------------------------------------------------

DEXSCREENER_BASE = "https://api.dexscreener.com/latest"


def parse_dexscreener_pair(raw_pair: dict, ts_recv_ns: int) -> DexPoolSnapshot | None:
    """Parse one pair object from DEX Screener /latest/dex/pairs/{chain}/{address}.

    Returns None if the payload cannot be parsed into a valid snapshot.
    """
    try:
        # Nested structure varies; handle the documented shape
        base = raw_pair.get("baseToken", {})
        quote = raw_pair.get("quoteToken", {})
        info = raw_pair.get("info", {})
        liquidity = info.get("liquidity", {})
        txn_5m = info.get("txn", {}).get("m5", {})
        volume_5m = raw_pair.get("volume", {}).get("m5", 0)
        volume_24h = raw_pair.get("volume", {}).get("h24", 0)
        vol_1h = raw_pair.get("volume", {}).get("h1", 0)
        price_usd = raw_pair.get("priceUsd")
        price_change_5m_pct = info.get("priceChange", {}).get("m5")
        price_change_1h_pct = info.get("priceChange", {}).get("h1")
        dex_id = raw_pair.get("dexId", "")
        chain_id = raw_pair.get("chainId", "")
        pair_address = raw_pair.get("pairAddress", "")

        # Required fields
        if not pair_address or not base or not quote:
            return None

        # Liquidity can be deep or flat
        liq_usd = liquidity.get("usd")
        if liq_usd is None:
            # GeckoTerminal-style flat key inside info
            liq_usd = info.get("usd")

        buys = txn_5m.get("buys")
        sells = txn_5m.get("sells")

        # Use ts_event from pair update time if available, else wall clock
        pair_create_ns = raw_pair.get("pairCreatedAt")
        if pair_create_ns is not None:
            # DEX Screener pairCreatedAt is milliseconds
            ts_event = int(pair_create_ns) * 1_000_000
        else:
            ts_event = ts_recv_ns

        return DexPoolSnapshot(
            source="dexscreener",
            chain=chain_id,
            dex=dex_id,
            pair_address=pair_address,
            base_symbol=base.get("symbol", ""),
            quote_symbol=quote.get("symbol", ""),
            base_address=base.get("address", ""),
            quote_address=quote.get("address", ""),
            price_usd=float(price_usd) if price_usd is not None else None,
            liquidity_usd=float(liq_usd) if liq_usd is not None else None,
            volume_5m_usd=float(volume_5m) if volume_5m is not None else None,
            volume_1h_usd=float(vol_1h) if vol_1h is not None else None,
            volume_24h_usd=float(volume_24h) if volume_24h is not None else None,
            txns_5m_buys=int(buys) if buys is not None else None,
            txns_5m_sells=int(sells) if sells is not None else None,
            price_change_5m_pct=float(price_change_5m_pct) if price_change_5m_pct is not None else None,
            price_change_1h_pct=float(price_change_1h_pct) if price_change_1h_pct is not None else None,
            ts_event=ts_event,
            ts_recv=ts_recv_ns,
            raw=raw_pair,
        )
    except Exception as exc:
        warnings.warn(f"DEX Screener pair parse failed: {exc}")
        return None


def fetch_dexscreener_pair(
    chain: str,
    pair_address: str,
    client: httpx.Client | None = None,
) -> tuple[DexPoolSnapshot | None, str | None]:
    """Fetch a single pair from DEX Screener public API.

    Returns (snapshot, error_string).  snapshot is None on failure.
    """
    url = f"{DEXSCREENER_BASE}/dex/pairs/{chain}/{pair_address}"
    close_client = False
    try:
        if client is None:
            client = httpx.Client(timeout=_TIMEOUT)
            close_client = True
        ts_recv_ns = int(time.time() * 1_000_000_000)
        resp = client.get(url)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code} from {url}"

        data = resp.json()
        pairs = data.get("pairs", [])
        if not pairs:
            return None, f"No pairs returned for {chain}/{pair_address}"

        snap = parse_dexscreener_pair(pairs[0], ts_recv_ns)
        return snap, None
    except httpx.TimeoutException:
        return None, f"Timeout fetching {url}"
    except httpx.RequestError as exc:
        return None, f"Request error: {exc}"
    except (json.JSONDecodeError, KeyError) as exc:
        return None, f"Parse error: {exc}"
    finally:
        if close_client and client is not None:
            client.close()


def fetch_dexscreener_pairs_by_address(
    addresses: list[str],
    client: httpx.Client | None = None,
) -> tuple[list[DexPoolSnapshot], list[str]]:
    """Fetch multiple addresses.  Rate-limited with built-in delays."""
    close_client = False
    if client is None:
        client = httpx.Client(timeout=_TIMEOUT)
        close_client = True

    snaps: list[DexPoolSnapshot] = []
    errs: list[str] = []
    for addr in addresses:
        snap, err = fetch_dexscreener_pair(addr, addr, client=client)
        # For address-first fetch, chain defaults to 'multiple'
        if snap is not None:
            snaps.append(snap)
        if err:
            errs.append(f"{addr}: {err}")
        time.sleep(_RATE_LIMIT_DELAY)

    if close_client:
        client.close()
    return snaps, errs


# ---------------------------------------------------------------------------
# DEX Screener multi-search by token symbol
# ---------------------------------------------------------------------------

def search_dexscreener_by_symbols(
    symbols: list[str],
    client: httpx.Client | None = None,
) -> tuple[list[DexPoolSnapshot], list[str]]:
    """Search for pairs matching asset symbols on DEX Screener.

    Uses /latest/dex/search endpoint with multiple queries.
    Each query is the asset symbol (e.g. 'sol', 'link').
    Filters results to pairs with decent liquidity.
    """
    close_client = False
    if client is None:
        client = httpx.Client(timeout=_TIMEOUT)
        close_client = True

    snaps: list[DexPoolSnapshot] = []
    errs: list[str] = []

    url = f"{DEXSCREENER_BASE}/dex/search"
    for sym in symbols:
        try:
            ts_recv_ns = int(time.time() * 1_000_000_000)
            resp = client.get(url, params={"q": sym.lower()})
            if resp.status_code != 200:
                errs.append(f"{sym}: HTTP {resp.status_code}")
                continue

            data = resp.json()
            pairs = data.get("pairs", [])
            for p in pairs:
                snap = parse_dexscreener_pair(p, ts_recv_ns)
                if snap is not None:
                    snaps.append(snap)
        except Exception as exc:
            errs.append(f"{sym}: {exc}")
        time.sleep(_RATE_LIMIT_DELAY)

    if close_client:
        client.close()
    return snaps, errs


# ---------------------------------------------------------------------------
# GeckoTerminal adapter
# ---------------------------------------------------------------------------

GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"


def fetch_geckoterminal_pool_ohlcv(
    network: str,
    pool_address: str,
    timeframe: str = "minute",
    limit: int = 60,
    client: httpx.Client | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch OHLCV for a GeckoTerminal pool.

    Timeframe: minute, hour, day.
    Returns list of OHLCV dicts with keys: timestamp, open, high, low, close, volume.
    """
    url = f"{GECKOTERMINAL_BASE}/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}"
    close_client = False
    try:
        if client is None:
            client = httpx.Client(timeout=_TIMEOUT)
            close_client = True
        params: dict = {"limit": limit, "token_address": pool_address}
        resp = client.get(url, params=params)
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code} from {url}"

        data = resp.json()
        attributes = data.get("data", {}).get("attributes", {})
        ohlcv_list = attributes.get("ohlcv_list", [])
        result = []
        for o in ohlcv_list:
            result.append({
                "timestamp": int(o.get("timestamp", 0)),
                "open": float(o.get("open", 0)),
                "high": float(o.get("high", 0)),
                "low": float(o.get("low", 0)),
                "close": float(o.get("close", 0)),
                "volume": float(o.get("volume", 0)),
            })
        return result, None
    except Exception as exc:
        return [], str(exc)
    finally:
        if close_client and client is not None:
            client.close()


# ---------------------------------------------------------------------------
# Local fixture loader — for testing without network
# ---------------------------------------------------------------------------

def load_dex_snapshots_from_jsonl(path: str) -> list[DexPoolSnapshot]:
    """Load DexPoolSnapshot from a JSONL file."""
    p = Path(path)
    if not p.exists():
        return []
    snaps: list[DexPoolSnapshot] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            snap = DexPoolSnapshot.from_dict(d)
            snaps.append(snap)
        except Exception:
            continue
    return snaps
