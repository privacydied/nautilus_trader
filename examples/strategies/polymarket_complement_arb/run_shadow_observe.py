#!/usr/bin/env python3
"""
Observer-only shadow validation runner for Polymarket complement arb.

Collects public CLOB books plus public data-api trades, runs the existing
same-condition detector, and feeds detected opportunities into the pure shadow
execution harness. This file intentionally contains no execution-client imports,
credential handling, signing, or order submission paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Protocol

from .config import ComplementArbConfig
from .detector import detect_opportunity
from .market_filter import extract_complement_markets
from .models import BookSnapshot
from .models import ComplementBookState
from .models import ComplementMarket
from .reports import compute_config_hash
from .reports import generate_run_id
from .shadow_execution import FillAssumption
from .shadow_execution import LegQuote
from .shadow_execution import MarketTrade
from .shadow_execution import ShadowOpportunity
from .shadow_execution import ShadowOpportunityResult
from .shadow_execution import ShadowSufficiencyConfig
from .shadow_execution import TradeSide
from .shadow_execution import build_shadow_summary
from .shadow_execution import evaluate_shadow_opportunity
from .shadow_execution import write_shadow_reports


logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
USER_AGENT = "nautilus-complement-shadow-observer/1.0"
TRADE_FETCH_LIMIT = 1000  # data-api max per call

# Trade evidence status constants
TRADE_EVIDENCE_READY = "TRADE_EVIDENCE_READY"
TRADE_EVIDENCE_EMPTY = "TRADE_EVIDENCE_EMPTY"
TRADE_EVIDENCE_JOIN_FAILED = "TRADE_EVIDENCE_JOIN_FAILED"
TRADE_EVIDENCE_ENDPOINT_ERROR = "TRADE_EVIDENCE_ENDPOINT_ERROR"
TRADE_EVIDENCE_TIME_WINDOW_MISMATCH = "TRADE_EVIDENCE_TIME_WINDOW_MISMATCH"
TRADE_EVIDENCE_IDENTIFIER_MISMATCH = "TRADE_EVIDENCE_IDENTIFIER_MISMATCH"
PUBLIC_TRADE_EVIDENCE_INSUFFICIENT = "PUBLIC_TRADE_EVIDENCE_INSUFFICIENT"

# Crypto Up/Down duration market pattern
CRYPTO_UP_PATTERN = re.compile(
    r"^(btc|eth|sol|xrp|doge|hype|bnb)-up(down)?-(5m|15m|1h|4h|daily)-\d+$",
    re.IGNORECASE,
)

# Duration label extraction
DURATION_LABELS: dict[str, str] = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "daily": "daily",
}


class PublicDataClient(Protocol):
    async def discover_markets(self, limit: int) -> list[dict[str, Any]]: ...
    async def fetch_book(self, token_id: str) -> BookSnapshot | None: ...
    async def fetch_trades(
        self,
        condition_ids: str | None = None,
        after_ts: int | None = None,
        limit: int = TRADE_FETCH_LIMIT,
    ) -> list[dict[str, Any]]: ...


def _get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"unsupported public-data URL scheme: {url}")
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read())


def _levels(raw_levels: list[Any], *, reverse: bool) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for level in raw_levels:
        price = float(level["price"] if isinstance(level, dict) else level[0])
        size = float(level["size"] if isinstance(level, dict) else level[1])
        if price > 0 and size > 0:
            levels.append((price, size))
    return sorted(levels, key=lambda item: item[0], reverse=reverse)


class UrlPublicDataClient:
    """Public REST-only data client. No credentials or private endpoints."""

    async def discover_markets(self, limit: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            _get_json,
            f"{GAMMA_API_BASE}/markets",
            {"active": "true", "closed": "false", "archived": "false", "limit": limit},
            30.0,
        )

    async def fetch_book(self, token_id: str) -> BookSnapshot | None:
        try:
            data = await asyncio.to_thread(_get_json, f"{CLOB_API_BASE}/book", {"token_id": token_id}, 10.0)
        except Exception as exc:
            logger.warning("book fetch failed token=%s error=%s", token_id, exc)
            return None
        bids = _levels(data.get("bids", []), reverse=True)[:20]
        asks = _levels(data.get("asks", []), reverse=False)[:20]
        return BookSnapshot(
            instrument_id_str=f"{token_id}.POLYMARKET",
            token_id=token_id,
            bids=bids,
            asks=asks,
            timestamp_ms=time.time() * 1000,
        )

    async def fetch_trades(
        self,
        condition_ids: str | None = None,
        after_ts: int | None = None,
        limit: int = TRADE_FETCH_LIMIT,
    ) -> list[dict[str, Any]]:
        """Fetch trades from the public data-api, optionally filtered by condition IDs.

        The data-api ``/trades`` endpoint supports ``market`` as a comma-separated
        list of condition IDs for server-side filtering (e.g.
        ``?market=<cond_id1>,<cond_id2>&limit=1000``).  When ``condition_ids`` is
        set, only trades for those conditions are returned.  When omitted, the
        global feed (latest N trades across all markets) is returned.

        Use ``after_ts`` (Unix seconds) to fetch only trades newer than a known
        timestamp for incremental accumulation.
        """
        params: dict[str, Any] = {"limit": limit}
        if condition_ids:
            params["market"] = condition_ids
        if after_ts is not None:
            params["after"] = after_ts
        try:
            data = await asyncio.to_thread(_get_json, f"{DATA_API_BASE}/trades", params, 15.0)
        except Exception as exc:
            logger.warning("trade fetch failed error=%s", exc)
            return []
        if isinstance(data, dict):
            data = data.get("trades") or data.get("data") or []
        return list(data)


class InMemoryPublicDataClient:
    """Deterministic test client for shadow observe wiring."""

    def __init__(self, *, books: dict[str, list[BookSnapshot]], trades: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._books = {token: list(values) for token, values in books.items()}
        self._trades = {token: list(values) for token, values in (trades or {}).items()}
        self.book_calls: dict[str, int] = {}

    async def discover_markets(self, limit: int) -> list[dict[str, Any]]:
        return []

    async def fetch_book(self, token_id: str) -> BookSnapshot | None:
        calls = self.book_calls.get(token_id, 0)
        self.book_calls[token_id] = calls + 1
        values = self._books.get(token_id, [])
        if not values:
            return None
        return values[min(calls, len(values) - 1)]

    async def fetch_trades(
        self,
        condition_ids: str | None = None,
        after_ts: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return stored trades matching condition IDs, or all if None."""
        all_rows: list[dict[str, Any]] = []
        for rows in self._trades.values():
            all_rows.extend(rows)
        if condition_ids:
            cond_set = set(c.strip() for c in condition_ids.split(","))
            all_rows = [r for r in all_rows if r.get("conditionId") in cond_set]
        if after_ts is None:
            return all_rows
        after_ns = after_ts * 1_000_000_000
        return [row for row in all_rows if _trade_ts_ns(row) >= after_ns]


def git_sha() -> str:
    try:
        return subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _trade_ts_ns(row: dict[str, Any]) -> int:
    raw = row.get("timestamp") or row.get("match_time") or row.get("created_at") or 0
    if isinstance(raw, str) and raw.replace(".", "", 1).isdigit():
        raw = float(raw)
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1_000_000_000_000_000:
            return int(value)
        if value > 1_000_000_000_000:
            return int(value * 1_000_000)
        return int(value * 1_000_000_000)
    return 0


def _trade_side(row: dict[str, Any]) -> TradeSide:
    value = str(row.get("side") or row.get("taker_side") or "").lower()
    if value == "buy":
        return TradeSide.BUY
    if value == "sell":
        return TradeSide.SELL
    return TradeSide.UNKNOWN


def _market_trades(rows: list[dict[str, Any]]) -> list[MarketTrade]:
    trades: list[MarketTrade] = []
    for row in rows:
        try:
            size = float(row["size"]) if row.get("size") is not None else None
            trades.append(MarketTrade(timestamp_ns=_trade_ts_ns(row), price=float(row["price"]), size=size, side=_trade_side(row)))
        except (TypeError, ValueError, KeyError):
            continue
    return trades


def _depth_ahead(book: BookSnapshot, price: float) -> float:
    return sum(size for level_price, size in book.bids if level_price >= price)


def _best(levels: list[tuple[float, float]]) -> tuple[float, float] | None:
    return levels[0] if levels else None


def _resolution_danger(market: ComplementMarket, config: ComplementArbConfig, now: datetime | None = None) -> bool:
    if not market.end_date_iso:
        return False
    try:
        raw_expiry = market.end_date_iso.replace("Z", "+00:00")
        expiry = datetime.fromisoformat(raw_expiry)
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return (expiry - now).total_seconds() <= config.resolution_danger_window_seconds


def build_shadow_opportunity(
    market: ComplementMarket,
    yes_book: BookSnapshot,
    no_book: BookSnapshot,
    config: ComplementArbConfig,
    *,
    run_id: str,
    git_sha: str,
    config_hash: str,
    now_ms: float,
    ts_event_ns: int,
) -> ShadowOpportunity | None:
    book_state = ComplementBookState(market.condition_id, market.market_slug, yes_book, no_book, ts_event_ns)
    diag = detect_opportunity(book_state, market, config, now_ms)
    if diag is None:
        return None
    yes_bid = _best(yes_book.bids)
    no_bid = _best(no_book.bids)
    yes_ask = _best(yes_book.asks)
    no_ask = _best(no_book.asks)
    if yes_bid is None or no_bid is None or yes_ask is None or no_ask is None:
        return None
    max_price = max(yes_ask[0], no_ask[0], 0.01)
    max_safe_shares = min(config.max_order_usdc / max_price, yes_ask[1], no_ask[1])
    min_order_shares = max(config.min_order_usdc / max_price, market.minimum_order_size)
    qty_for_costs = max(config.min_order_usdc / 0.50, 1.0)
    fee_per_share = diag.total_taker_fee / qty_for_costs
    total_buffer_per_share = (diag.total_cost - diag.total_taker_fee) / qty_for_costs
    net_edge_per_share = diag.gross_gap - (diag.total_cost / qty_for_costs)
    return ShadowOpportunity(
        run_id=run_id,
        git_sha=git_sha,
        config_hash=config_hash,
        timestamp_ns=ts_event_ns,
        condition_id=market.condition_id,
        market_slug=market.market_slug,
        yes_token_id=market.yes_token_id,
        no_token_id=market.no_token_id,
        yes_quote=LegQuote("YES", "BUY", ts_event_ns, diag.yes_bid, max_safe_shares, _depth_ahead(yes_book, diag.yes_bid), yes_bid[0], yes_ask[0], yes_ask[0], yes_ask[0] * yes_ask[1]),
        no_quote=LegQuote("NO", "BUY", ts_event_ns, diag.no_bid, max_safe_shares, _depth_ahead(no_book, diag.no_bid), no_bid[0], no_ask[0], no_ask[0], no_ask[0] * no_ask[1]),
        sum_asks=diag.yes_ask + diag.no_ask,
        gross_edge_per_share=diag.gross_gap,
        fee_per_share=fee_per_share,
        leg_risk_buffer=total_buffer_per_share,
        net_edge_per_share=net_edge_per_share,
        max_safe_shares=max_safe_shares,
        one_leg_timeout_ms=config.one_leg_timeout_ms,
        min_order_shares=min_order_shares,
        same_condition=True,
        stale=diag.stale or yes_book.stale or no_book.stale,
        resolution_danger=_resolution_danger(market, config),
        neg_risk=market.neg_risk,
    )


def _compute_trade_evidence_status(
    *,
    trade_rows_total: int,
    missing_trade_data_events: int,
    total_opportunities: int,
    trade_fetch_success_count: int,
    trade_fetch_error_count: int,
    trade_fetch_attempt_count: int,
    non_dust_opportunity_count: int,
) -> str:
    """
    Determine trade evidence status based on fetch and join diagnostics.

    The data-api /trades endpoint is a global feed without per-asset filtering.
    Trades for specific tokens only appear if they happen to be among the latest
    N global trades. Status reflects whether the evidence path worked correctly
    even if the tokens happen to have no recent trades.
    """
    if trade_fetch_attempt_count == 0:
        return TRADE_EVIDENCE_EMPTY
    if trade_fetch_error_count > max(1, trade_fetch_attempt_count // 2):
        return TRADE_EVIDENCE_ENDPOINT_ERROR
    if trade_fetch_success_count == 0:
        return TRADE_EVIDENCE_ENDPOINT_ERROR
    if trade_rows_total == 0 and trade_fetch_success_count > 0:
        return TRADE_EVIDENCE_EMPTY
    if missing_trade_data_events >= total_opportunities > 0:
        return PUBLIC_TRADE_EVIDENCE_INSUFFICIENT
    if missing_trade_data_events > non_dust_opportunity_count:
        return TRADE_EVIDENCE_JOIN_FAILED
    return TRADE_EVIDENCE_READY


def _sufficiency_from_config(config: ComplementArbConfig) -> ShadowSufficiencyConfig:
    return ShadowSufficiencyConfig(
        min_observer_windows=config.min_observer_windows,
        min_detected_opportunities=config.min_detected_opportunities,
        min_pessimistic_paired_fills=config.min_pessimistic_paired_fills,
        min_same_condition_valid_opportunities=config.min_same_condition_valid_opportunities,
        min_non_dust_opportunities=config.min_non_dust_opportunities,
    )


def _parse_duration_from_slug(slug: str) -> str | None:
    """Extract duration label from a crypto updown slug (e.g. ``btc-updown-5m-...`` → ``5m``)."""
    m = CRYPTO_UP_PATTERN.match(slug)
    if m:
        dur = m.group(3).lower()
        return DURATION_LABELS.get(dur)
    return None


def _parse_asset_from_slug(slug: str) -> str | None:
    """Extract asset label from a crypto updown slug (e.g. ``btc-updown-5m-...`` → ``BTC``)."""
    m = CRYPTO_UP_PATTERN.match(slug)
    return m.group(1).upper() if m else None


def _is_crypto_updown_event(event: dict[str, Any]) -> bool:
    """Check if an event matches the crypto Up/Down duration market pattern."""
    slug = str(event.get("slug", "") or "")
    return bool(CRYPTO_UP_PATTERN.match(slug))


def _market_meta(event: dict[str, Any]) -> dict[str, Any]:
    """Extract asset and duration from an event's slug for crypto updown markets."""
    slug = str(event.get("slug", "") or "")
    return {
        "asset": _parse_asset_from_slug(slug),
        "duration": _parse_duration_from_slug(slug),
        "resolution_source": event.get("resolutionSource") or "",
    }


def _build_market_from_raw(
    m: dict[str, Any],
    event_slug: str,
    meta: dict[str, Any],
) -> ComplementMarket | None:
    """Build a ComplementMarket from a raw Gamma market dict.

    Handles both ``Up/Down`` and ``Yes/No`` outcome conventions, parsing
    ``clobTokenIds`` and ``outcomes`` from their JSON-string format.
    Returns None if the market is neg-risk or structurally malformed.
    """
    condition_id = str(m.get("conditionId", "") or "")
    slug = str(m.get("slug", "") or "")
    if not condition_id:
        return None

    # Parse outcomes and token IDs from JSON strings
    outcomes_raw = m.get("outcomes")
    clob_raw = m.get("clobTokenIds")
    if isinstance(outcomes_raw, str):
        import ast
        try:
            outcomes_raw = ast.literal_eval(outcomes_raw)
        except Exception:
            outcomes_raw = []
    if isinstance(clob_raw, str):
        import ast
        try:
            clob_raw = ast.literal_eval(clob_raw)
        except Exception:
            clob_raw = []
    if not isinstance(outcomes_raw, list) or not isinstance(clob_raw, list):
        return None
    if len(outcomes_raw) != 2 or len(clob_raw) != 2:
        return None

    # Map outcomes to YES/UP and NO/DOWN
    yes_token: str | None = None
    no_token: str | None = None
    for tid, outcome in zip(clob_raw, outcomes_raw):
        oc = outcome.upper().strip()
        if oc in ("YES", "UP"):
            yes_token = str(tid)
        elif oc in ("NO", "DOWN"):
            no_token = str(tid)
    if not yes_token or not no_token:
        return None

    if m.get("negRisk", False):
        return None

    yes_inst_id = f"{condition_id}-{yes_token}.POLYMARKET"
    no_inst_id = f"{condition_id}-{no_token}.POLYMARKET"
    end_date_iso = m.get("endDate") or m.get("endDateIso")
    fee = 0.0
    fs = m.get("feeSchedule")
    if isinstance(fs, dict):
        fee = float(fs.get("rate", 0.0))

    return ComplementMarket(
        condition_id=condition_id,
        market_slug=slug,
        event_slug=event_slug,
        question=str(m.get("question", "") or ""),
        yes_token_id=yes_token,
        no_token_id=no_token,
        yes_instrument_id_str=yes_inst_id,
        no_instrument_id_str=no_inst_id,
        neg_risk=False,
        active=bool(m.get("active", True)),
        closed=bool(m.get("closed", False)),
        accepting_orders=bool(m.get("acceptingOrders", True)),
        end_date_iso=str(end_date_iso) if end_date_iso else None,
        minimum_tick_size=float(m.get("orderPriceMinTickSize", 0.001) or 0.001),
        minimum_order_size=float(m.get("orderMinSize", 5.0) or 5.0),
        taker_fee_rate=fee,
        category=meta.get("category") or meta.get("asset") or "crypto-updown",
        liquidity_num=float(m.get("liquidityNum", 0) or 0),
        volume_num=float(m.get("volumeNum", 0) or 0),
    )


async def _resolve_event_slugs(
    slugs: list[str],
    *,
    gamma_base: str = GAMMA_API_BASE,
) -> list[ComplementMarket]:
    """Resolve event slugs to their CLOB ComplementMarket objects.

    Builds ComplementMarket objects directly from the resolved event data,
    bypassing the general-market closed/active filters since the user
    explicitly specified these slugs.
    """
    markets: list[ComplementMarket] = []
    for slug in slugs:
        try:
            events = await asyncio.to_thread(
                _get_json,
                f"{gamma_base}/events",
                {"slug": slug},
                15.0,
            )
            if not isinstance(events, list):
                continue
            for ev in events:
                meta = _market_meta(ev)
                raw_markets = ev.get("markets") or []
                event_slug = str(ev.get("slug", "") or "")
                for m in raw_markets:
                    cm = _build_market_from_raw(m, event_slug, meta)
                    if cm is not None:
                        markets.append(cm)
        except Exception as exc:
            logger.warning("failed to resolve event slug=%s error=%s", slug, exc)
    logger.info("event-slug resolution: %s slugs, %s markets", len(slugs), len(markets))
    return markets


async def _discover_crypto_updown(
    target_assets: set[str],
    target_durations: set[str],
    config: ComplementArbConfig,
    *,
    client: PublicDataClient,
    gamma_base: str = GAMMA_API_BASE,
) -> tuple[list[ComplementMarket], list[dict[str, Any]]]:
    """Discover crypto Up/Down duration markets from Gamma.

    Searches Gamma events for crypto Up/Down patterns matching the target
    assets and durations, then extracts CLOB markets from each matching event.
    Returns eligible ComplementMarkets and a list of market metadata dicts.
    """
    raw_events: list[dict[str, Any]] = []
    try:
        raw_events = await asyncio.to_thread(
            _get_json,
            f"{gamma_base}/events",
            {"active": "true", "closed": "false", "archived": "false", "limit": 250},
            30.0,
        )
    except Exception as exc:
        logger.warning("gamma events discovery failed error=%s", exc)
        return [], []

    matching_events = [e for e in raw_events if _is_crypto_updown_event(e)]
    logger.info(
        "crypto-updown discovery: %s events found, %s matching pattern",
        len(raw_events),
        len(matching_events),
    )

    # Filter by target assets and durations
    filtered: list[dict[str, Any]] = []
    for ev in matching_events:
        meta = _market_meta(ev)
        asset = (meta.get("asset") or "").upper()
        dur = meta.get("duration") or ""
        if asset and asset in target_assets and dur and dur in target_durations:
            filtered.append(ev)

    logger.info(
        "crypto-updown filtered: %s after asset/duration filter (assets=%s, durations=%s)",
        len(filtered),
        sorted(target_assets),
        sorted(target_durations),
    )

    markets: list[ComplementMarket] = []
    market_meta_list: list[dict[str, Any]] = []
    for ev in filtered:
        raw_markets = ev.get("markets") or []
        meta = _market_meta(ev)
        for m in raw_markets:
            condition_id = str(m.get("conditionId", "") or "")
            slug = str(m.get("slug", "") or "")
            outcomes_raw = m.get("outcomes")
            clob_raw = m.get("clobTokenIds")
            if isinstance(outcomes_raw, str):
                import ast
                try:
                    outcomes_raw = ast.literal_eval(outcomes_raw)
                except Exception:
                    outcomes_raw = []
            if isinstance(clob_raw, str):
                import ast
                try:
                    clob_raw = ast.literal_eval(clob_raw)
                except Exception:
                    clob_raw = []
            if not isinstance(outcomes_raw, list) or not isinstance(clob_raw, list):
                continue
            if len(outcomes_raw) != 2 or len(clob_raw) != 2:
                continue
            up_token: str | None = None
            down_token: str | None = None
            for tid, outcome in zip(clob_raw, outcomes_raw):
                oc = outcome.upper().strip()
                if oc == "UP":
                    up_token = str(tid)
                elif oc == "DOWN":
                    down_token = str(tid)
            if not up_token or not down_token:
                continue
            # For complement arb, UP = YES, DOWN = NO
            yes_inst_id = f"{condition_id}-{up_token}.POLYMARKET"
            no_inst_id = f"{condition_id}-{down_token}.POLYMARKET"
            neg_risk = bool(m.get("negRisk", False))
            if neg_risk:
                continue
            active = bool(m.get("active", True))
            closed = bool(m.get("closed", False))
            end_date_iso = m.get("endDate") or ev.get("endDate")
            market_entry = ComplementMarket(
                condition_id=condition_id,
                market_slug=slug,
                event_slug=str(ev.get("slug", "")),
                question=str(m.get("question", "")),
                yes_token_id=up_token,
                no_token_id=down_token,
                yes_instrument_id_str=yes_inst_id,
                no_instrument_id_str=no_inst_id,
                neg_risk=neg_risk,
                active=active,
                closed=closed,
                accepting_orders=True,
                end_date_iso=str(end_date_iso) if end_date_iso else None,
                minimum_tick_size=0.001,
                minimum_order_size=1.0,
                taker_fee_rate=float(m.get("feeSchedule", {}).get("rate", 0.0) if isinstance(m.get("feeSchedule"), dict) else 0.0),
                category="crypto-updown",
                liquidity_num=float(m.get("liquidity", 0) or 0),
                volume_num=float(m.get("volume", 0) or 0),
            )
            market_meta_list.append({**meta, "slug": slug, "condition_id": condition_id})
            markets.append(market_entry)
    return markets, market_meta_list


async def _discover_markets(
    config: ComplementArbConfig,
    client: PublicDataClient,
    *,
    event_slugs: list[str] | None = None,
    crypto_assets: set[str] | None = None,
    crypto_durations: set[str] | None = None,
    limit: int = 200,
) -> tuple[list[ComplementMarket], dict[str, Any]]:
    """Discover markets using the configured universe mode.

    Returns (markets, universe_metadata) where universe_metadata describes
    the discovery path taken.
    """
    if event_slugs:
        markets = await _resolve_event_slugs(event_slugs)
        logger.info("event-slug discovery: %s slugs, %s eligible markets", len(event_slugs), len(markets))
        return markets, {"mode": "event-slugs", "slugs": event_slugs}

    if crypto_assets and crypto_durations:
        markets, meta_list = await _discover_crypto_updown(crypto_assets, crypto_durations, config, client=client)
        logger.info("crypto-updown discovery: %s eligible markets from assets=%s durations=%s", len(markets), sorted(crypto_assets), sorted(crypto_durations))
        return markets, {"mode": "crypto-updown", "assets": sorted(crypto_assets), "durations": sorted(crypto_durations), "market_meta": meta_list}

    # Default: general Gamma discovery
    raw = await client.discover_markets(limit)
    eligible, skips = extract_complement_markets(raw, config)
    logger.info("general discovery: %s eligible markets, %s skipped", len(eligible), len(skips))
    return eligible, {"mode": "general", "raw_fetched": len(raw), "eligible": len(eligible), "skipped": len(skips)}


async def run_shadow_observe(  # noqa: C901
    *,
    markets: list[ComplementMarket],
    config: ComplementArbConfig,
    duration_secs: float,
    poll_interval_secs: float,
    client: PublicDataClient,
    output_root: str | Path = "reports/polymarket_complement_arb_shadow",
    run_id: str | None = None,
    git_sha: str | None = None,
    observer_window_count: int | None = None,
    sufficiency: ShadowSufficiencyConfig | None = None,
) -> dict[str, Any]:
    run_id = run_id or generate_run_id()
    git = git_sha or globals()["git_sha"]()
    config_hash = compute_config_hash(config)
    start = time.time()
    deadline = start + max(0.0, duration_secs)
    output_dir = Path(output_root) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "config.json").write_text(json.dumps(asdict(config), indent=2, sort_keys=True, default=str))

    opportunities: list[ShadowOpportunity] = []
    trades_by_token: dict[str, list[dict[str, Any]]] = {}
    edge_timeline_by_condition: dict[str, list[tuple[int, float]]] = {}
    windows = 0
    book_fetches = 0
    book_errors = 0
    missing_trade_data_events = 0
    trade_fetch_attempt_count = 0
    trade_fetch_success_count = 0
    trade_fetch_empty_count = 0
    trade_fetch_error_count = 0
    trade_rows_total = 0
    last_trade_ts: int | None = None  # Unix secs, for incremental after filter

    while True:
        windows += 1
        now_ms = time.time() * 1000
        ts_event_ns = time.time_ns()

        # Fetch trades — when we have condition IDs, filter server-side via
        # ?market=<comma-sep-condition-ids> for much greater efficiency.
        observed_condition_ids = ",".join(sorted({m.condition_id for m in markets}))
        trade_fetch_attempt_count += 1
        try:
            trade_rows = await client.fetch_trades(
                condition_ids=observed_condition_ids,
                after_ts=last_trade_ts,
                limit=TRADE_FETCH_LIMIT,
            )
            if isinstance(trade_rows, list):
                trade_fetch_success_count += 1
                if not trade_rows:
                    trade_fetch_empty_count += 1
                else:
                    # Track latest timestamp for incremental fetch
                    max_row_ts = max(
                        int(r.get("timestamp", 0))
                        for r in trade_rows
                        if r.get("timestamp") is not None
                    )
                    if max_row_ts > (last_trade_ts or 0):
                        last_trade_ts = max_row_ts
                    # Distribute matching trade rows to each observed token
                    for row in trade_rows:
                        asset = str(row.get("asset") or "")
                        if not asset:
                            continue
                        existing = {json.dumps(r, sort_keys=True, default=str) for r in trades_by_token.get(asset, [])}
                        key = json.dumps(row, sort_keys=True, default=str)
                        if key not in existing:
                            trades_by_token.setdefault(asset, []).append(row)
                            trade_rows_total += 1
            else:
                trade_fetch_success_count += 1  # non-list ok, just empty
        except Exception as exc:
            trade_fetch_error_count += 1
            logger.warning("trade fetch poll error=%s", exc)

        for market in markets:
            yes_book = await client.fetch_book(market.yes_token_id)
            no_book = await client.fetch_book(market.no_token_id)
            book_fetches += 2
            if yes_book is None or no_book is None:
                book_errors += 1
                continue
            opp = build_shadow_opportunity(
                market,
                yes_book,
                no_book,
                config,
                run_id=run_id,
                git_sha=git,
                config_hash=config_hash,
                now_ms=now_ms,
                ts_event_ns=ts_event_ns,
            )
            if opp is not None:
                opportunities.append(opp)
                edge_timeline_by_condition.setdefault(market.condition_id, []).append((ts_event_ns, opp.net_edge_per_share))
        if time.time() >= deadline or duration_secs <= 0:
            break
        await asyncio.sleep(max(0.0, min(poll_interval_secs, deadline - time.time())))

    results: list[ShadowOpportunityResult] = []
    for opp in opportunities:
        yes_rows = trades_by_token.get(opp.yes_token_id, [])
        no_rows = trades_by_token.get(opp.no_token_id, [])
        if not yes_rows or not no_rows:
            missing_trade_data_events += 1
        yes_trades = _market_trades(yes_rows)
        no_trades = _market_trades(no_rows)
        edge_timeline = edge_timeline_by_condition.get(opp.condition_id, [])
        unwind_price = opp.yes_quote.best_bid if yes_trades and not no_trades else opp.no_quote.best_bid
        for mode in (FillAssumption.PESSIMISTIC, FillAssumption.NEUTRAL, FillAssumption.OPTIMISTIC):
            results.append(evaluate_shadow_opportunity(opp, mode, yes_trades, no_trades, edge_timeline=edge_timeline, unwind_price=unwind_price))

    suff = sufficiency or _sufficiency_from_config(config)
    observer_windows = observer_window_count if observer_window_count is not None else windows

    # Compute trade evidence status early (before write_shadow_reports references it)
    # Use a rough non_dust estimate from results, or default to total opportunities
    rough_non_dust = sum(1 for r in results if r.opportunity.max_safe_shares >= r.opportunity.min_order_shares)
    trade_evidence_status = _compute_trade_evidence_status(
        trade_rows_total=trade_rows_total,
        missing_trade_data_events=missing_trade_data_events,
        total_opportunities=len(opportunities),
        trade_fetch_success_count=trade_fetch_success_count,
        trade_fetch_error_count=trade_fetch_error_count,
        trade_fetch_attempt_count=trade_fetch_attempt_count,
        non_dust_opportunity_count=rough_non_dust or len(opportunities),
    )

    paths = write_shadow_reports(output_dir, results, observer_window_count=observer_windows, sufficiency=suff, trade_evidence={
        "status": trade_evidence_status,
        "fetch_attempts": trade_fetch_attempt_count,
        "fetch_successes": trade_fetch_success_count,
        "fetch_empty_responses": trade_fetch_empty_count,
        "fetch_errors": trade_fetch_error_count,
        "rows_fetched_total": trade_rows_total,
        "rows_joined_to_opportunities": len(opportunities) - missing_trade_data_events,
        "missing_trade_data_events": missing_trade_data_events,
    })
    summary = build_shadow_summary(results, observer_window_count=observer_windows, sufficiency=suff)
    metadata = {
        "run_id": run_id,
        "git_sha": git,
        "config_hash": config_hash,
        "run_arguments": {"duration_secs": duration_secs, "poll_interval_secs": poll_interval_secs},
        "start_timestamp": datetime.fromtimestamp(start, UTC).isoformat(),
        "end_timestamp": datetime.now(UTC).isoformat(),
        "observer_duration_secs": time.time() - start,
        "markets_scanned": len(markets),
        "eligible_same_condition_pairs": sum(1 for m in markets if not m.neg_risk),
        "observer_window_count": observer_windows,
        "total_book_fetches": book_fetches,
        "book_fetch_errors": book_errors,
        "missing_trade_data_events": missing_trade_data_events,
        "report_paths": {key: str(path) for key, path in paths.items()},
        "trade_evidence_status": trade_evidence_status,
        "trade_fetch_attempt_count": trade_fetch_attempt_count,
        "trade_fetch_success_count": trade_fetch_success_count,
        "trade_fetch_empty_count": trade_fetch_empty_count,
        "trade_fetch_error_count": trade_fetch_error_count,
        "trade_rows_total": trade_rows_total,
        "trade_rows_joined_to_opportunity_count": len(opportunities) - missing_trade_data_events,
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str))
    summary_path = output_dir / "shadow_summary.json"
    enriched = json.loads(summary_path.read_text())
    enriched["metadata"] = metadata
    enriched["trade_evidence"] = {
        "status": trade_evidence_status,
        "fetch_attempts": trade_fetch_attempt_count,
        "fetch_successes": trade_fetch_success_count,
        "fetch_empty_responses": trade_fetch_empty_count,
        "fetch_errors": trade_fetch_error_count,
        "rows_fetched_total": trade_rows_total,
        "rows_joined_to_opportunities": len(opportunities) - missing_trade_data_events,
        "missing_trade_data_events": missing_trade_data_events,
    }
    summary_path.write_text(json.dumps(enriched, indent=2, sort_keys=True, default=str))
    return {
        "run_id": run_id,
        "report_path": str(output_dir),
        "verdict": summary["verdict"],
        "markets_scanned": len(markets),
        "eligible_same_condition_pairs": metadata["eligible_same_condition_pairs"],
        "detected_opportunities": len(opportunities),
        "non_dust_opportunities": summary["raw"]["non_dust_opportunity_count"],
        "pessimistic_paired_fills": summary["raw"]["pessimistic_paired_fill_count"],
        "one_leg_fills": summary["raw"]["one_leg_fill_count"],
        "paired_gain": summary["paired_gain"],
        "unwind_loss": summary["unwind_loss"],
        "net_shadow_harvest": summary["net_shadow_harvest"],
        "missing_trade_data_events": missing_trade_data_events,
    }


async def _discover_eligible(config: ComplementArbConfig, client: PublicDataClient, limit: int) -> list[ComplementMarket]:
    """Legacy discovery helper (kept for backcompat). Delegates to _discover_markets."""
    markets, _ = await _discover_markets(config, client, limit=limit)
    return markets


async def run_trade_evidence_debug(
    *,
    markets: list[ComplementMarket],
    config: ComplementArbConfig,
    duration_secs: float,
    poll_interval_secs: float,
    client: PublicDataClient,
    output_root: str | Path = "reports/polymarket_complement_arb_trade_debug",
) -> dict[str, Any]:
    """
    Evidence-only debug mode.

    Fetches book snapshots and public trades, attempts to join trades to
    YES/NO token IDs, and writes a compact debug report. Does NOT run
    verdict logic or shadow execution.
    """
    run_id = generate_run_id()
    output_dir = Path(output_root) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    start = time.time()
    deadline = start + max(0.0, duration_secs)

    debug_entries: list[dict[str, Any]] = []
    total_trade_rows = 0
    total_trade_fetches = 0
    total_trade_errors = 0

    while True:
        observed_condition_ids = ",".join(sorted({m.condition_id for m in markets}))
        trade_rows = await client.fetch_trades(
            condition_ids=observed_condition_ids,
            after_ts=None,
            limit=TRADE_FETCH_LIMIT,
        )
        total_trade_fetches += 1
        if not isinstance(trade_rows, list):
            total_trade_errors += 1
            trade_rows = []
        total_trade_rows = len(trade_rows)

        for market in markets:
            yes_book = await client.fetch_book(market.yes_token_id)
            no_book = await client.fetch_book(market.no_token_id)

            yes_trades_in_feed = [r for r in trade_rows if str(r.get("asset", "")) == market.yes_token_id]
            no_trades_in_feed = [r for r in trade_rows if str(r.get("asset", "")) == market.no_token_id]

            book_ts = time.time()
            trade_timestamps = [int(r.get("timestamp", 0)) for r in trade_rows if r.get("timestamp") is not None]
            trade_ts_min = min(trade_timestamps) if trade_timestamps else None
            trade_ts_max = max(trade_timestamps) if trade_timestamps else None

            entry = {
                "market_slug": market.market_slug,
                "condition_id": market.condition_id,
                "yes_token_id": market.yes_token_id,
                "no_token_id": market.no_token_id,
                "book_yes_exists": yes_book is not None,
                "book_no_exists": no_book is not None,
                "book_yes_bid_levels": len(yes_book.bids) if yes_book else 0,
                "book_no_bid_levels": len(no_book.bids) if no_book else 0,
                "book_yes_ask_levels": len(yes_book.asks) if yes_book else 0,
                "book_no_ask_levels": len(no_book.asks) if no_book else 0,
                "trade_rows_in_feed": len(trade_rows),
                "trade_rows_matched_yes": len(yes_trades_in_feed),
                "trade_rows_matched_no": len(no_trades_in_feed),
                "trade_ts_min": trade_ts_min,
                "trade_ts_max": trade_ts_max,
                "book_ts": int(book_ts),
                "mismatch_reason": None,
            }
            if not yes_trades_in_feed and not no_trades_in_feed and len(trade_rows) > 0:
                entry["mismatch_reason"] = (
                    "Global trade feed returned rows but none match this market's tokens. "
                    "Either these tokens are not actively trading (not in latest ~1000 global trades) "
                    "or there is an identifier mismatch."
                )
            elif not yes_trades_in_feed and not no_trades_in_feed:
                entry["mismatch_reason"] = "Global trade feed returned no rows at all."
            debug_entries.append(entry)

        if time.time() >= deadline or duration_secs <= 0:
            break
        await asyncio.sleep(max(0.0, min(poll_interval_secs, deadline - time.time())))

    debug_report = {
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "duration_secs": time.time() - start,
        "poll_interval_secs": poll_interval_secs,
        "markets_observed": len(markets),
        "total_trade_fetches": total_trade_fetches,
        "total_trade_errors": total_trade_errors,
        "total_trade_rows_last_fetch": total_trade_rows,
        "pairs": debug_entries,
    }

    debug_json_path = output_dir / "trade_evidence_debug.json"
    debug_json_path.write_text(json.dumps(debug_report, indent=2, sort_keys=True, default=str))

    md_lines = [
        "# Trade Evidence Debug Report",
        "",
        f"- run_id: {run_id}",
        f"- duration: {time.time() - start:.1f}s",
        f"- markets observed: {len(markets)}",
        f"- total trade fetches: {total_trade_fetches}",
        f"- total trade errors: {total_trade_errors}",
        f"- trade rows in last fetch: {total_trade_rows}",
        "",
        "## Per-Pair Evidence",
        "",
        *[
            "| market | condition_id | YES trades | NO trades | book YES bids | book NO bids | mismatch_reason |"
        ],
        *[
            "|--------|-------------|-----------|---------|--------------|-------------|-----------------|"
        ],
        *[
            (
                f"| {e['market_slug'][:40]} | {e['condition_id'][:16]}... "
                f"| {e['trade_rows_matched_yes']} | {e['trade_rows_matched_no']} "
                f"| {e['book_yes_bid_levels']} | {e['book_no_bid_levels']} "
                f"| {str(e['mismatch_reason'] or '')[:60]} |"
            )
            for e in debug_entries
        ],
        "",
        "## Notes",
        "",
        "- The data-api /trades endpoint is a global feed (latest ~1000 trades, no per-asset filtering).",
        "- YES/NO trades are matched by `asset` field (CLOB token ID).",
        "- Zero matched trades means the tokens are absent from the latest global trade window.",
        "- This is not necessarily a join failure — it may be a genuine lack of recent trading activity.",
    ]
    debug_md_path = output_dir / "trade_evidence_debug.md"
    debug_md_path.write_text("\n".join(md_lines))

    logger.info("trade evidence debug written to %s", output_dir)
    return {
        "run_id": run_id,
        "output_dir": str(output_dir),
        "json_path": str(debug_json_path),
        "md_path": str(debug_md_path),
        "markets_observed": len(markets),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run observer-only complement arb shadow validation.")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-markets", type=int, default=20)
    parser.add_argument("--market-slug", type=str)
    parser.add_argument("--event-slug", type=str)
    parser.add_argument("--output-root", type=Path, default=Path("reports/polymarket_complement_arb_shadow"))
    parser.add_argument("--debug-trade-evidence", action="store_true", help="Run evidence-only debug mode")
    parser.add_argument(
        "--universe",
        type=str,
        choices=["general", "crypto-updown"],
        default="general",
        help="Market universe to discover (general or crypto-updown)",
    )
    parser.add_argument(
        "--assets",
        type=str,
        default="",
        help="Comma-separated asset symbols for crypto-updown (e.g. BTC,ETH,SOL)",
    )
    parser.add_argument(
        "--durations",
        type=str,
        default="",
        help="Comma-separated durations for crypto-updown (e.g. 5m,15m,1h,4h,daily)",
    )
    parser.add_argument(
        "--event-slugs",
        type=str,
        default="",
        help="Comma-separated event slugs to resolve directly (e.g. btc-updown-5m-1778949900)",
    )
    args = parser.parse_args()

    config = ComplementArbConfig(
        mode="observe",
        dry_run=True,
        max_markets=args.max_markets,
        market_slug_allowlist=(args.market_slug,) if args.market_slug else (),
        event_slug_allowlist=(args.event_slug,) if args.event_slug else (),
    )
    client = UrlPublicDataClient()

    # Parse universe arguments
    event_slugs = [s.strip() for s in args.event_slugs.split(",") if s.strip()] if args.event_slugs else None
    crypto_assets: set[str] | None = None
    crypto_durations: set[str] | None = None
    if args.universe == "crypto-updown":
        assets = set(a.strip().upper() for a in args.assets.split(",") if a.strip())
        durations = set(d.strip().lower() for d in args.durations.split(",") if d.strip())
        crypto_assets = assets or {"BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB"}
        crypto_durations = durations or {"5m", "15m", "1h", "4h", "daily"}
        logger.info(
            "universe=crypto-updown assets=%s durations=%s",
            sorted(crypto_assets),
            sorted(crypto_durations),
        )

    markets, universe_meta = await _discover_markets(
        config,
        client,
        event_slugs=event_slugs,
        crypto_assets=crypto_assets,
        crypto_durations=crypto_durations,
        limit=max(args.max_markets * 20, 200),
    )
    if not markets:
        raise SystemExit("No eligible same-condition YES/NO complement markets discovered")

    if args.debug_trade_evidence:
        result = await run_trade_evidence_debug(
            markets=markets,
            config=config,
            duration_secs=args.duration,
            poll_interval_secs=args.poll_interval,
            client=client,
            output_root=args.output_root,
        )
        logger.info("trade evidence debug complete: %s", result)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return

    result = await run_shadow_observe(
        markets=markets,
        config=config,
        duration_secs=args.duration,
        poll_interval_secs=args.poll_interval,
        client=client,
        output_root=args.output_root,
        git_sha=git_sha(),
    )
    logger.info("shadow observe complete: %s", result)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
