# /// script
# dependencies = [
#     "httpx>=0.27,<1",
#     "websockets>=12,<14",
# ]
# ///
"""
Polymarket BTC Up/Down CLOB liquidity probe — observer-only, no orders.

Captures orderbook snapshots for near-expiry BTC Up/Down binary markets
on Polymarket and classifies liquidity quality via fixed thresholds.

**This is not a trading strategy.**
- No wallet, signing, authentication, or order submission.
- No fair-value model, signal evaluation, or forward-return measurement.
- No CANDIDATE, REJECTED, EXECUTION_READY, or TRADE_READY verdicts.

Outputs under the run output directory:
    discovered_markets.jsonl
    clob_orderbook_samples.jsonl
    chainlink_reference_ticks.jsonl   (optional)
    capture_manifest.json
    liquidity_probe_summary.json
    liquidity_probe_summary.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_WS_BASE = "wss://ws-subscriptions-clob.polymarket.com/ws/l3"
CHAINLINK_PRICE_URL = f"{CLOB_API_BASE}/price/btc"

# Diagnostic thresholds (fixed before capture)
SPREAD_GREEN_MAX = 0.03  # $0.03
SPREAD_YELLOW_MAX = 0.06  # $0.06
SPREAD_RED_P95 = 0.10  # $0.10 p95
MIN_NON_DUST_DEPTH_USD = 100.0  # $100 minimum top-of-book depth
MAX_STALE_SECONDS = 30.0  # orderbook is stale if older than this
DEFAULT_POLL_INTERVAL = 5.0  # seconds between book polls
DEFAULT_DURATION = 600  # 10 minutes default capture

# Allowed diagnostic classifications
GREEN_DIAG = "GREEN_LIQUIDITY_DIAGNOSTIC"
YELLOW_DIAG = "YELLOW_LIQUIDITY_DIAGNOSTIC"
RED_DIAG = "RED_LIQUIDITY_DIAGNOSTIC"
NEEDS_MORE_DATA = "NEEDS_MORE_DATA"
CAPTURE_UNUSABLE = "CAPTURE_UNUSABLE"

# Forbidden verdicts (must never appear in output)
FORBIDDEN_VERDICTS = [
    "REJECTED",
    "CANDIDATE",
    "CANDIDATE_FOR_LONGER_OBSERVATION",
    "CANDIDATE_FOR_LIVE",
    "EXECUTION_READY",
    "TRADE_READY",
]

ALLOWED_DIAGNOSTICS = {
    GREEN_DIAG,
    YELLOW_DIAG,
    RED_DIAG,
    NEEDS_MORE_DATA,
    CAPTURE_UNUSABLE,
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class BTCMarket:
    """A discovered BTC Up or Down binary market on Polymarket."""

    market_slug: str
    market_id: str
    condition_id: str
    question: str
    outcomes: list[str]
    yes_token_id: str | None
    no_token_id: str | None
    expiry: str | None
    price_to_beat: float | None
    is_active: bool
    discovered_ts: float
    direction: str  # "up" or "down"
    source_url: str = ""
    reason_skipped: str | None = None

    @property
    def token_ids(self) -> list[str]:
        return [t for t in [self.yes_token_id, self.no_token_id] if t is not None]

    @property
    def has_tokens(self) -> bool:
        return len(self.token_ids) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_slug": self.market_slug,
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "question": self.question,
            "outcomes": self.outcomes,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "expiry": self.expiry,
            "price_to_beat": self.price_to_beat,
            "is_active": self.is_active,
            "direction": self.direction,
            "discovered_ts": self.discovered_ts,
            "source_url": self.source_url,
            "reason_skipped": self.reason_skipped,
        }


@dataclass
class OrderbookSample:
    """One orderbook snapshot for one token at one point in time."""

    ts_event: float  # when we observed this sample
    market_slug: str
    token_id: str
    side: str | None  # "yes" or "no" token label
    expiry: str | None
    price_to_beat: float | None
    best_bid: float | None
    best_ask: float | None
    spread_price_units: float | None  # (best_ask - best_bid) in token price units
    spread_cents: float | None  # same value in cents for human readability
    top_bid_size: float | None
    top_ask_size: float | None
    estimated_top_bid_depth_usd: float | None
    estimated_top_ask_depth_usd: float | None
    is_two_sided: bool
    is_stale: bool
    is_crossed: bool
    is_missing: bool
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_event": self.ts_event,
            "market_slug": self.market_slug,
            "token_id": self.token_id,
            "side": self.side,
            "expiry": self.expiry,
            "price_to_beat": self.price_to_beat,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread_price_units": self.spread_price_units,
            "spread_cents": self.spread_cents,
            "top_bid_size": self.top_bid_size,
            "top_ask_size": self.top_ask_size,
            "estimated_top_bid_depth_usd": self.estimated_top_bid_depth_usd,
            "estimated_top_ask_depth_usd": self.estimated_top_ask_depth_usd,
            "is_two_sided": self.is_two_sided,
            "is_stale": self.is_stale,
            "is_crossed": self.is_crossed,
            "is_missing": self.is_missing,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class ChainlinkTick:
    """Chainlink BTC/USD reference price snapshot."""

    ts_event: float
    price: float | None
    source_url: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_event": self.ts_event,
            "price": self.price,
            "source_url": self.source_url,
            "error": self.error,
        }


@dataclass
class FeedStats:
    """Capture statistics for one feed/token."""

    name: str
    sample_count: int = 0
    reconnect_count: int = 0
    error_count: int = 0
    first_sample_ts: float | None = None
    last_sample_ts: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sample_count": self.sample_count,
            "reconnect_count": self.reconnect_count,
            "error_count": self.error_count,
            "first_sample_ts": self.first_sample_ts,
            "last_sample_ts": self.last_sample_ts,
        }


@dataclass
class CaptureManifest:
    """Run metadata describing what was captured."""

    run_id: str
    git_sha: str
    started_at: str
    ended_at: str
    requested_duration_seconds: int
    actual_duration_seconds: float
    feeds_requested: list[str]
    feeds_connected: list[str]
    feed_stats: dict[str, FeedStats]
    market_slugs: list[str]
    token_ids: list[str]
    errors: list[str]
    overlap_duration_seconds: float
    no_auth_no_orders_statement: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "git_sha": self.git_sha,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "requested_duration_seconds": self.requested_duration_seconds,
            "actual_duration_seconds": round(self.actual_duration_seconds, 2),
            "feeds_requested": self.feeds_requested,
            "feeds_connected": self.feeds_connected,
            "feed_stats": {
                k: v.to_dict() for k, v in self.feed_stats.items()
            },
            "market_slugs": self.market_slugs,
            "token_ids": self.token_ids,
            "errors": self.errors,
            "overlap_duration_seconds": round(self.overlap_duration_seconds, 2),
            "no_auth_no_orders_statement": self.no_auth_no_orders_statement,
        }


@dataclass
class ProbeSummary:
    """Computed liquidity statistics and diagnostic classification."""

    markets_discovered: int
    markets_with_tokens: int
    samples_collected: int
    valid_samples: int
    median_spread_price_units: float | None
    median_spread_cents: float | None
    p75_spread_cents: float | None
    p95_spread_cents: float | None
    median_top_bid_depth_usd: float | None
    median_top_ask_depth_usd: float | None
    percent_two_sided: float
    percent_stale: float
    percent_crossed: float
    percent_missing: float
    diagnostic_classification: str
    verdict_statement: str
    spread_list_sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "markets_discovered": self.markets_discovered,
            "markets_with_tokens": self.markets_with_tokens,
            "samples_collected": self.samples_collected,
            "valid_samples": self.valid_samples,
            "median_spread_price_units": self.median_spread_price_units,
            "median_spread_cents": self.median_spread_cents,
            "p75_spread_cents": self.p75_spread_cents,
            "p95_spread_cents": self.p95_spread_cents,
            "median_top_bid_depth_usd": self.median_top_bid_depth_usd,
            "median_top_ask_depth_usd": self.median_top_ask_depth_usd,
            "percent_two_sided": round(self.percent_two_sided, 2),
            "percent_stale": round(self.percent_stale, 2),
            "percent_crossed": round(self.percent_crossed, 2),
            "percent_missing": round(self.percent_missing, 2),
            "diagnostic_classification": self.diagnostic_classification,
            "verdict_statement": self.verdict_statement,
            "spread_list_sample_count": self.spread_list_sample_count,
        }


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------


def _get_git_sha() -> str:
    """Get the current git SHA, or 'unknown' if unavailable."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _is_btc_updown(question: str) -> str | None:
    """Check if a market question is BTC Up or Down.

    Returns 'up', 'down', or None.
    """
    q = question.lower()
    if "btc" not in q and "bitcoin" not in q:
        return None
    if " up " in q or q.startswith("up ") or q.endswith(" up"):
        return "up"
    if " down " in q or q.startswith("down ") or q.endswith(" down"):
        return "down"
    if q.startswith("will btc be"):
        # Sometimes phrased as "Will BTC be above..." or "Will BTC be below..."
        if "above" in q:
            return "up"
        if "below" in q:
            return "down"
        return "up"  # generic will question
    if "higher" in q or "above" in q or "increase" in q:
        return "up"
    if "lower" in q or "below" in q or "decrease" in q:
        return "down"
    return None


def _extract_price_to_beat(question: str) -> float | None:
    """Try to extract the strike/target price from the market question.

    E.g. 'Will BTC be above $105,000?' -> 105000.0
         'Will BTC be above $100K?'   -> 100000.0
    """
    import re

    def _parse_amount(raw: str) -> float | None:
        """Parse a dollar-amount string, handling K/M/B suffixes."""
        raw = raw.replace(",", "").strip()
        suffix_mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
        suffix = raw[-1].upper() if raw else ""
        if suffix in suffix_mult:
            try:
                return float(raw[:-1]) * suffix_mult[suffix]
            except (ValueError, IndexError):
                return None
        try:
            return float(raw)
        except ValueError:
            return None

    amounts = []
    # Match $100,000 or $100K or $100M
    for match in re.finditer(r'\$([0-9,]+\.?[0-9]*[KkMmBb]?)', question):
        val = _parse_amount(match.group(1))
        if val is not None:
            amounts.append(val)
    # Also match standalone 100K (without $ sign)
    for match in re.finditer(r'(?<!\$)(\d+\.?\d*)\s*([KkMmBb])', question):
        raw = match.group(1) + match.group(2)
        val = _parse_amount(raw)
        if val is not None:
            amounts.append(val)

    if not amounts:
        return None

    # If a value is > 10000 it's likely a BTC price
    btc_prices = [v for v in amounts if v > 10000]
    if btc_prices:
        return min(btc_prices)  # smallest likely price target
    # Otherwise take the largest
    return max(amounts)


def discover_btc_updown_markets(
    client: httpx.Client | None = None,
    tag: str = "btc",
    max_markets: int = 200,
) -> list[BTCMarket]:
    """Discover active BTC Up/Down markets via the Gamma API.

    Args:
        client: Optional httpx.Client (created fresh if None).
        tag: Gamma tag filter (default: 'btc').
        max_markets: Maximum markets to query.

    Returns:
        List of BTCMarket objects, including those that were skipped
        (with reason_skipped populated).
    """
    close_own = client is None
    if client is None:
        client = httpx.Client(timeout=15)

    discovered_ts = time.time()
    markets: list[BTCMarket] = []

    try:
        url = f"{GAMMA_API_BASE}/markets"
        params: dict[str, Any] = {
            "tag": tag,
            "closed": "false",
            "limit": min(max_markets, 500),
        }
        resp = client.get(url, params=params)
        resp.raise_for_status()
        raw_markets = resp.json()
    except Exception as exc:
        logger.error("Gamma API discovery failed: %s", exc)
        return markets
    finally:
        if close_own:
            client.close()

    if not isinstance(raw_markets, list):
        logger.warning("Gamma API returned non-list: %s", type(raw_markets))
        return markets

    for raw in raw_markets:
        slug = raw.get("slug", "") or raw.get("id", "")
        question = raw.get("question", "") or ""
        condition_id = raw.get("conditionId", "") or ""
        market_id = str(raw.get("id", ""))
        end_date = raw.get("endDate") or raw.get("end_date")
        is_closed = raw.get("closed", False)
        outcomes = raw.get("outcomes", [])

        # Check if this is a BTC Up/Down market
        direction = _is_btc_updown(question)
        if direction is None:
            markets.append(BTCMarket(
                market_slug=slug,
                market_id=market_id,
                condition_id=condition_id,
                question=question[:200],
                outcomes=outcomes,
                yes_token_id=None,
                no_token_id=None,
                expiry=end_date,
                price_to_beat=None,
                is_active=not is_closed,
                discovered_ts=discovered_ts,
                direction="",
                reason_skipped="not_btc_updown",
            ))
            continue

        price_to_beat = _extract_price_to_beat(question)

        # Extract CLOB token IDs
        clob_tokens_raw = raw.get("clobTokenIds") or raw.get("clobTokenIds", "")
        token_ids_list: list[str] = []
        if isinstance(clob_tokens_raw, str) and clob_tokens_raw.strip():
            parts = [t.strip() for t in clob_tokens_raw.split(",") if t.strip()]
            token_ids_list = parts

        yes_token_id = token_ids_list[0] if len(token_ids_list) > 0 else None
        no_token_id = token_ids_list[1] if len(token_ids_list) > 1 else None

        if not token_ids_list:
            # May need to get tokens from a nested field
            tokens_raw = raw.get("tokens", [])
            if isinstance(tokens_raw, list):
                for t in tokens_raw:
                    tid = t.get("tokenId") if isinstance(t, dict) else None
                    if tid:
                        token_ids_list.append(str(tid))
                yes_token_id = token_ids_list[0] if len(token_ids_list) > 0 else None
                no_token_id = token_ids_list[1] if len(token_ids_list) > 1 else None

        markets.append(BTCMarket(
            market_slug=slug,
            market_id=market_id,
            condition_id=condition_id,
            question=question[:200],
            outcomes=outcomes,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            expiry=end_date,
            price_to_beat=price_to_beat,
            is_active=not is_closed,
            discovered_ts=discovered_ts,
            direction=direction or "",
            source_url=f"{GAMMA_API_BASE}/markets/{slug}" if slug else "",
            reason_skipped=None if (token_ids_list and direction) else (
                "no_token_ids" if not token_ids_list else None
            ),
        ))

    return markets


# ---------------------------------------------------------------------------
# Orderbook fetching
# ---------------------------------------------------------------------------


def fetch_orderbook(token_id: str, client: httpx.Client) -> dict[str, Any] | None:
    """Fetch the orderbook for a token via the CLOB REST API.

    Returns parsed JSON dict, or None on failure.
    """
    try:
        url = f"{CLOB_API_BASE}/orderbook"
        resp = client.get(url, params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.debug("No orderbook for token %s (404)", token_id)
            return None
        logger.warning("HTTP error fetching orderbook %s: %s", token_id, exc)
        return None
    except Exception as exc:
        logger.warning("Error fetching orderbook %s: %s", token_id, exc)
        return None


def parse_orderbook_sample(
    raw: dict[str, Any] | None,
    market: BTCMarket,
    token_id: str,
    ts_event: float,
) -> OrderbookSample:
    """Parse a raw orderbook response into an OrderbookSample.

    Handles missing, empty, crossed, and stale books.
    """
    if raw is None:
        return OrderbookSample(
            ts_event=ts_event,
            market_slug=market.market_slug,
            token_id=token_id,
            side=_token_side(market, token_id),
            expiry=market.expiry,
            price_to_beat=market.price_to_beat,
            best_bid=None,
            best_ask=None,
            spread_price_units=None,
            spread_cents=None,
            top_bid_size=None,
            top_ask_size=None,
            estimated_top_bid_depth_usd=None,
            estimated_top_ask_depth_usd=None,
            is_two_sided=False,
            is_stale=False,
            is_crossed=False,
            is_missing=True,
            rejection_reason="no_data",
        )

    bids_raw = raw.get("bids", [])
    asks_raw = raw.get("asks", [])

    # Normalize: could be list of [price, size] or list of {"price": ..., "size": ...}
    bids = _normalize_levels(bids_raw)
    asks = _normalize_levels(asks_raw)
    assert bids is not None and asks is not None

    best_bid = float(bids[0]["price"]) if bids else None
    best_ask = float(asks[0]["price"]) if asks else None
    top_bid_size = float(bids[0]["size"]) if bids else None
    top_ask_size = float(asks[0]["size"]) if asks else None

    is_missing_flag = best_bid is None and best_ask is None
    is_one_sided = (best_bid is not None) != (best_ask is not None)

    spread_price_units: float | None = None
    if best_bid is not None and best_ask is not None:
        spread_price_units = best_ask - best_bid
        if spread_price_units < 0:
            spread_price_units = None  # crossed book

    spread_cents: float | None = (
        round(spread_price_units * 100, 2)
        if spread_price_units is not None
        else None
    )

    is_crossed_flag = (
        best_bid is not None
        and best_ask is not None
        and best_bid > best_ask
    )

    is_two_sided_flag = (
        best_bid is not None
        and best_ask is not None
        and not is_crossed_flag
    )

    estimated_bid_depth = (
        round(top_bid_size * best_bid, 2)
        if top_bid_size is not None and best_bid is not None
        else None
    )
    estimated_ask_depth = (
        round(top_ask_size * best_ask, 2)
        if top_ask_size is not None and best_ask is not None
        else None
    )

    rejection_reason: str | None = None
    if is_missing_flag:
        rejection_reason = "empty_book"
    elif is_crossed_flag:
        rejection_reason = "crossed_book"
    elif is_one_sided:
        rejection_reason = "one_sided_book"

    return OrderbookSample(
        ts_event=ts_event,
        market_slug=market.market_slug,
        token_id=token_id,
        side=_token_side(market, token_id),
        expiry=market.expiry,
        price_to_beat=market.price_to_beat,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_price_units=spread_price_units,
        spread_cents=spread_cents,
        top_bid_size=top_bid_size,
        top_ask_size=top_ask_size,
        estimated_top_bid_depth_usd=estimated_bid_depth,
        estimated_top_ask_depth_usd=estimated_ask_depth,
        is_two_sided=is_two_sided_flag,
        is_stale=False,  # REST polls are always "fresh" relative to ts_event
        is_crossed=is_crossed_flag,
        is_missing=is_missing_flag,
        rejection_reason=rejection_reason,
    )


def _token_side(market: BTCMarket, token_id: str) -> str | None:
    """Determine if a token_id is YES or NO."""
    if market.yes_token_id and token_id == market.yes_token_id:
        return "yes"
    if market.no_token_id and token_id == market.no_token_id:
        return "no"
    return None


def _normalize_levels(
    levels: list[Any],
) -> list[dict[str, float]]:
    """Normalize orderbook levels to [{'price': ..., 'size': ...}, ...].

    Handles both [price, size] arrays and {'price': ..., 'size': ...} dicts.
    """
    result: list[dict[str, float]] = []
    for level in levels:
        if isinstance(level, dict):
            price = level.get("price")
            size = level.get("size")
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = level[0]
            size = level[1]
        else:
            continue

        try:
            result.append({
                "price": float(price),
                "size": float(size),
            })
        except (ValueError, TypeError):
            continue
    return result


# ---------------------------------------------------------------------------
# Chainlink reference tick
# ---------------------------------------------------------------------------


def fetch_chainlink_btc_price(client: httpx.Client) -> ChainlinkTick:
    """Fetch the Chainlink BTC/USD reference price used by Polymarket."""
    ts = time.time()
    try:
        resp = client.get(CHAINLINK_PRICE_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Response is typically {"price": "12345.67"} or {"price": 12345.67}
        if isinstance(data, dict):
            price_raw = data.get("price")
        elif isinstance(data, (int, float)):
            price_raw = data
        else:
            price_raw = None

        price = float(price_raw) if price_raw is not None else None
        return ChainlinkTick(
            ts_event=ts,
            price=price,
            source_url=CHAINLINK_PRICE_URL,
            error=None if price is not None else "unparseable_response",
        )
    except Exception as exc:
        logger.debug("Chainlink price fetch failed: %s", exc)
        return ChainlinkTick(
            ts_event=ts,
            price=None,
            source_url=CHAINLINK_PRICE_URL,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Capture loop
# ---------------------------------------------------------------------------


async def capture_loop(
    markets: list[BTCMarket],
    duration_seconds: int = DEFAULT_DURATION,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    enable_chainlink: bool = False,
    chainlink_poll_interval: float = 60.0,
) -> tuple[
    list[BTCMarket],
    list[OrderbookSample],
    list[ChainlinkTick],
    CaptureManifest,
]:
    """Run the capture loop: poll orderbooks and optionally Chainlink prices.

    Returns (markets, samples, chainlink_ticks, manifest).

    This is an async function but uses synchronous httpx for REST polling
    inside a thread executor since each poll is independent.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    started_ts = time.time()

    feeds_requested: list[str] = []
    for m in markets:
        if m.has_tokens:
            for tid in m.token_ids:
                feeds_requested.append(f"clob:{m.market_slug}:{tid}")
    if enable_chainlink:
        feeds_requested.append("chainlink:btcusd")

    feeds_connected: list[str] = list(feeds_requested)  # assume all connect
    feed_stats: dict[str, FeedStats] = {}
    for fname in feeds_requested:
        feed_stats[fname] = FeedStats(name=fname)

    all_samples: list[OrderbookSample] = []
    all_cl_ticks: list[ChainlinkTick] = []
    errors: list[str] = []

    actionable_markets = [m for m in markets if m.has_tokens]
    token_ids_captured: set[str] = set()

    deadline = time.time() + duration_seconds

    with httpx.Client(timeout=15) as client:
        while time.time() < deadline:
            loop_start = time.time()

            # Fetch orderbooks for all markets
            for market in actionable_markets:
                for tid in market.token_ids:
                    raw = fetch_orderbook(tid, client)
                    sample = parse_orderbook_sample(raw, market, tid, time.time())
                    all_samples.append(sample)

                    fname = f"clob:{market.market_slug}:{tid}"
                    if fname in feed_stats:
                        feed_stats[fname].sample_count += 1
                        if feed_stats[fname].first_sample_ts is None:
                            feed_stats[fname].first_sample_ts = sample.ts_event
                        feed_stats[fname].last_sample_ts = sample.ts_event

                    if sample.rejection_reason:
                        if fname not in errors:
                            errors.append(
                                f"{fname}: {sample.rejection_reason} "
                                f"(ts={sample.ts_event})"
                            )

                    token_ids_captured.add(tid)

            # Optional Chainlink price
            if enable_chainlink:
                cl_tick = fetch_chainlink_btc_price(client)
                all_cl_ticks.append(cl_tick)
                fs = feed_stats.get("chainlink:btcusd")
                if fs:
                    fs.sample_count += 1
                    if fs.first_sample_ts is None:
                        fs.first_sample_ts = cl_tick.ts_event
                    fs.last_sample_ts = cl_tick.ts_event
                if cl_tick.error:
                    errors.append(f"chainlink:btcusd: {cl_tick.error}")

            elapsed = time.time() - loop_start
            sleep_needed = max(0, poll_interval - elapsed)
            await asyncio.sleep(sleep_needed)

    ended_at = datetime.now(timezone.utc).isoformat()
    actual_duration = time.time() - started_ts

    # Compute overlap between CLOB and Chainlink feeds
    clob_times = [s.ts_event for s in all_samples]
    cl_times = [t.ts_event for t in all_cl_ticks]
    overlap = 0.0
    if clob_times and cl_times:
        overlap_start = max(min(clob_times), min(cl_times))
        overlap_end = min(max(clob_times), max(cl_times))
        overlap = max(0.0, overlap_end - overlap_start)

    manifest = CaptureManifest(
        run_id=f"polymarket_liquidity_probe_{int(started_ts)}",
        git_sha=_get_git_sha(),
        started_at=started_at,
        ended_at=ended_at,
        requested_duration_seconds=duration_seconds,
        actual_duration_seconds=actual_duration,
        feeds_requested=feeds_requested,
        feeds_connected=feeds_connected,
        feed_stats=feed_stats,
        market_slugs=[m.market_slug for m in markets if m.has_tokens],
        token_ids=list(token_ids_captured),
        errors=errors[:50],  # cap at 50
        overlap_duration_seconds=overlap,
        no_auth_no_orders_statement=(
            "This capture used public Polymarket Gamma and CLOB APIs only. "
            "No authentication, no API keys, no wallet, no signing, "
            "no order submission. Observer-only."
        ),
    )

    return markets, all_samples, all_cl_ticks, manifest


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def compute_summary(
    markets: list[BTCMarket],
    samples: list[OrderbookSample],
) -> ProbeSummary:
    """Compute liquidity statistics and diagnostic classification."""
    markets_discovered = len([m for m in markets if m.reason_skipped is None])
    all_markets = len(markets)
    markets_with_tokens = len([m for m in markets if m.has_tokens])
    samples_collected = len(samples)

    # Filter to valid samples (not missing, not crossed)
    valid = [
        s
        for s in samples
        if not s.is_missing
        and not s.is_crossed
        and s.best_bid is not None
        and s.best_ask is not None
        and s.spread_price_units is not None
        and s.spread_price_units >= 0
    ]

    valid_samples = len(valid)

    # Extract spreads in price units (e.g., 0.02 = 2 cents)
    spreads_price_units = [s.spread_price_units for s in valid if s.spread_price_units is not None]
    spreads_price_units_sorted = sorted(spreads_price_units)

    # Depth
    bid_depths = [
        s.estimated_top_bid_depth_usd
        for s in valid
        if s.estimated_top_bid_depth_usd is not None
    ]
    ask_depths = [
        s.estimated_top_ask_depth_usd
        for s in valid
        if s.estimated_top_ask_depth_usd is not None
    ]

    # Percentages
    total = len(samples)
    two_sided_count = sum(1 for s in samples if s.is_two_sided)
    stale_count = sum(1 for s in samples if s.is_stale)
    crossed_count = sum(1 for s in samples if s.is_crossed)
    missing_count = sum(1 for s in samples if s.is_missing)

    pct_two_sided = (two_sided_count / total * 100) if total > 0 else 0.0
    pct_stale = (stale_count / total * 100) if total > 0 else 0.0
    pct_crossed = (crossed_count / total * 100) if total > 0 else 0.0
    pct_missing = (missing_count / total * 100) if total > 0 else 0.0

    # Compute percentiles
    def percentile(data: list[float], p: float) -> float | None:
        if not data:
            return None
        idx = max(0, int(len(data) * p / 100))
        return data[min(idx, len(data) - 1)]

    median_spread_price_units = percentile(spreads_price_units_sorted, 50)
    p75_spread_price_units = percentile(spreads_price_units_sorted, 75)
    p95_spread_price_units = percentile(spreads_price_units_sorted, 95)
    median_spread_cents = (
        round(median_spread_price_units * 100, 2)
        if median_spread_price_units is not None
        else None
    )
    p75_spread_cents = (
        round(p75_spread_price_units * 100, 2)
        if p75_spread_price_units is not None
        else None
    )
    p95_spread_cents = (
        round(p95_spread_price_units * 100, 2)
        if p95_spread_price_units is not None
        else None
    )
    median_bid_depth = percentile(sorted(bid_depths), 50) if bid_depths else None
    median_ask_depth = percentile(sorted(ask_depths), 50) if ask_depths else None

    # Classification
    diagnostic = _classify_liquidity(
        spreads=spreads_price_units_sorted,
        bid_depths=bid_depths,
        ask_depths=ask_depths,
        total_valid=valid_samples,
    )

    verdict = (
        "No trade candidate emitted. "
        "This is a liquidity/actionability diagnostic only. "
        "It cannot produce CANDIDATE, REJECTED, EXECUTION_READY, or TRADE_READY verdicts."
    )

    return ProbeSummary(
        markets_discovered=markets_discovered,
        markets_with_tokens=markets_with_tokens,
        samples_collected=samples_collected,
        valid_samples=valid_samples,
        median_spread_price_units=median_spread_price_units,
        median_spread_cents=median_spread_cents,
        p75_spread_cents=p75_spread_cents,
        p95_spread_cents=p95_spread_cents,
        median_top_bid_depth_usd=median_bid_depth,
        median_top_ask_depth_usd=median_ask_depth,
        percent_two_sided=pct_two_sided,
        percent_stale=pct_stale,
        percent_crossed=pct_crossed,
        percent_missing=pct_missing,
        diagnostic_classification=diagnostic,
        verdict_statement=verdict,
        spread_list_sample_count=len(spreads_price_units),
    )


def _classify_liquidity(
    spreads: list[float],
    bid_depths: list[float],
    ask_depths: list[float],
    total_valid: int,
) -> str:
    """Classify liquidity into GREEN/YELLOW/RED/NEEDS_MORE_DATA/CAPTURE_UNUSABLE.

    Rules (fixed before capture):
    - GREEN: median spread <= 3c AND depth is non-dust (bid+ask >= $100)
    - YELLOW: median > 3c and <= 6c
    - RED: median > 6c, OR p95 > 10c, OR depth consistently dust
    - NEEDS_MORE_DATA: < 5 valid samples
    - CAPTURE_UNUSABLE: 0 valid samples
    """
    if total_valid == 0:
        return CAPTURE_UNUSABLE
    if total_valid < 5:
        return NEEDS_MORE_DATA

    if not spreads:
        return CAPTURE_UNUSABLE

    sorted_spreads = sorted(spreads)
    n = len(sorted_spreads)
    median_idx = n // 2
    p95_idx = min(int(n * 0.95), n - 1)

    median_spread = sorted_spreads[median_idx]
    p95_spread = sorted_spreads[p95_idx]

    # Compute median total depth
    all_depths = bid_depths + ask_depths
    median_depth = sorted(all_depths)[len(all_depths) // 2] if all_depths else 0.0

    is_dust = median_depth < MIN_NON_DUST_DEPTH_USD

    if median_spread > SPREAD_YELLOW_MAX or p95_spread > SPREAD_RED_P95 or is_dust:
        return RED_DIAG
    if median_spread > SPREAD_GREEN_MAX:
        return YELLOW_DIAG
    # median_spread <= SPREAD_GREEN_MAX
    return GREEN_DIAG


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    """Atomically append one JSON line to a file."""
    with open(path, "a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def write_discovered_markets(markets: list[BTCMarket], path: Path) -> None:
    """Write discovered markets to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for m in markets:
        _append_jsonl(path, m.to_dict())


def write_orderbook_samples(samples: list[OrderbookSample], path: Path) -> None:
    """Write orderbook samples to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for s in samples:
        _append_jsonl(path, s.to_dict())


def write_chainlink_ticks(ticks: list[ChainlinkTick], path: Path) -> None:
    """Write Chainlink reference ticks to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for t in ticks:
        _append_jsonl(path, t.to_dict())


def write_manifest(manifest: CaptureManifest, path: Path) -> None:
    """Write capture manifest to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest.to_dict(), f, indent=2, default=str)
        f.write("\n")


def write_summary_json(summary: ProbeSummary, path: Path) -> None:
    """Write probe summary to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary.to_dict(), f, indent=2, default=str)
        f.write("\n")


def write_summary_md(
    summary: ProbeSummary,
    manifest: CaptureManifest,
    path: Path,
) -> None:
    """Write probe summary to Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Polymarket BTC Up/Down CLOB Liquidity Probe Summary",
        "",
        f"**Run ID:** {manifest.run_id}",
        f"**Started:** {manifest.started_at}",
        f"**Duration:** {manifest.actual_duration_seconds:.1f}s "
        f"(requested {manifest.requested_duration_seconds}s)",
        "",
        "---",
        "",
        "## Markets",
        "",
        f"- Markets discovered: {summary.markets_discovered}",
        f"- Markets with token IDs: {summary.markets_with_tokens}",
        f"- Market slugs: {', '.join(manifest.market_slugs) if manifest.market_slugs else '(none)'}",
        "",
        "## Samples",
        "",
        f"- Samples collected: {summary.samples_collected}",
        f"- Valid samples: {summary.valid_samples}",
        "",
        "## Spread Statistics",
        "",
        f"- Median spread (cents): {_fmt(summary.median_spread_cents)}",
        f"- P75 spread (cents): {_fmt(summary.p75_spread_cents)}",
        f"- P95 spread (cents): {_fmt(summary.p95_spread_cents)}",
        f"- Spread sample count: {summary.spread_list_sample_count}",
        "",
        "## Depth Statistics",
        "",
        f"- Median top bid depth (USD): {_fmt(summary.median_top_bid_depth_usd)}",
        f"- Median top ask depth (USD): {_fmt(summary.median_top_ask_depth_usd)}",
        "",
        "## Book Quality",
        "",
        f"- Percent two-sided: {summary.percent_two_sided:.1f}%",
        f"- Percent stale: {summary.percent_stale:.1f}%",
        f"- Percent crossed: {summary.percent_crossed:.1f}%",
        f"- Percent missing: {summary.percent_missing:.1f}%",
        "",
        "## Diagnostic Classification",
        "",
        f"**{summary.diagnostic_classification}**",
        "",
    ]

    def diag_detail(d: str) -> str:
        details = {
            GREEN_DIAG: (
                "Median spread <= $0.03 and top-of-book depth >= $100. "
                "Books are actionable."
            ),
            YELLOW_DIAG: (
                "Median spread > $0.03 and <= $0.06. "
                "Only proceed if future lag diagnostics show "
                "moves clearly larger than spread."
            ),
            RED_DIAG: (
                "Median spread > $0.06, or p95 spread > $0.10, "
                "or top-of-book depth is consistently below $100. "
                "Stop unless a human reviews and overrides."
            ),
            NEEDS_MORE_DATA: (
                "Insufficient valid samples. "
                "Cannot reach a diagnostic."
            ),
            CAPTURE_UNUSABLE: (
                "No actionable snapshots captured. "
                "Feed may be down, markets may have no book."
            ),
        }
        return details.get(d, "Unknown classification.")

    lines.append(f"> {diag_detail(summary.diagnostic_classification)}")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"> {summary.verdict_statement}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*This summary was produced by a liquidity/actionability probe.*")
    lines.append("*It is not a trading signal or strategy recommendation.*")

    path.write_text("\n".join(lines) + "\n")


def _fmt(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) < 0.01:
        return f"{val:.6f}"
    if abs(val) < 100:
        return f"{val:.4f}"
    return f"{val:.2f}"
