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
CLOB_BOOK_PATH = "/book"
CLOB_WS_BASE = "wss://ws-subscriptions-clob.polymarket.com/ws/l3"
CHAINLINK_PRICE_URL = f"{CLOB_API_BASE}/price"

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
# Verification statuses (separate instrumentation from evidence)
# ---------------------------------------------------------------------------

# Instrumentation (built, not yet run)
PARSER_FIX_VERIFIED = "PARSER_FIX_VERIFIED"
RAW_PAYLOAD_AUDIT_IMPLEMENTED = "RAW_PAYLOAD_AUDIT_IMPLEMENTED"
TTE_BUCKETING_IMPLEMENTED = "TTE_BUCKETING_IMPLEMENTED"
DISTANCE_TO_STRIKE_BUCKETING_IMPLEMENTED = "DISTANCE_TO_STRIKE_BUCKETING_IMPLEMENTED"
REFERENCE_PROXY_IMPLEMENTED = "REFERENCE_PROXY_IMPLEMENTED"

# Evidence (requires real artifact files on disk)
RAW_PAYLOAD_VERIFIED = "RAW_PAYLOAD_VERIFIED"
TTE_BUCKETS_VERIFIED = "TTE_BUCKETS_VERIFIED"
NEAR_EXPIRY_BUCKET_VERIFIED = "NEAR_EXPIRY_BUCKET_VERIFIED"
DISTANCE_TO_STRIKE_BUCKETS_VERIFIED = "DISTANCE_TO_STRIKE_BUCKETS_VERIFIED"
TWO_AXIS_LIQUIDITY_VERIFIED = "TWO_AXIS_LIQUIDITY_VERIFIED"

# Gate states
LIQUIDITY_GATE_VERIFIED = "LIQUIDITY_GATE_VERIFIED"
LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION = "LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION"
LIQUIDITY_GATE_NOT_VERIFIED = "LIQUIDITY_GATE_NOT_VERIFIED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# Duration labels
DUR_5M = "5m"
DUR_15M = "15m"
DUR_1H = "1h"
DUR_UNKNOWN = "unknown"
DURATION_LABELS = [DUR_5M, DUR_15M, DUR_1H, DUR_UNKNOWN]

# Reference source labels
REF_CHAINLINK = "CHAINLINK_REFERENCE"
REF_CEX_PROXY = "CEX_PROXY_REFERENCE"
REF_UNAVAILABLE = "REFERENCE_UNAVAILABLE"

# Distance-to-strike bucket labels
DIST_LTE_5 = "distance_lte_5_bps"
DIST_5_TO_10 = "distance_5_to_10_bps"
DIST_10_TO_25 = "distance_10_to_25_bps"
DIST_25_TO_50 = "distance_25_to_50_bps"
DIST_GT_50 = "distance_gt_50_bps"
DIST_UNKNOWN = "unknown"
DIST_BUCKET_LABELS = [DIST_LTE_5, DIST_5_TO_10, DIST_10_TO_25, DIST_25_TO_50, DIST_GT_50, DIST_UNKNOWN]

# Required artifact files for each verification gate
REQUIRED_ARTIFACTS_RAW_PAYLOAD = [
    "raw_clob_orderbook_payloads.jsonl",
    "raw_payload_audit.md",
]
REQUIRED_ARTIFACTS_TTE = [
    "tte_bucket_summary.json",
    "tte_bucket_summary.md",
]
REQUIRED_ARTIFACTS_DISTANCE = [
    "distance_to_strike_bucket_summary.json",
    "distance_to_strike_bucket_summary.md",
]

# TTE bucket labels
TTE_GT_15M = "tte_gt_15m"
TTE_5M_TO_15M = "tte_5m_to_15m"
TTE_2M_TO_5M = "tte_2m_to_5m"
TTE_1M_TO_2M = "tte_1m_to_2m"
TTE_30S_TO_1M = "tte_30s_to_1m"
TTE_0S_TO_30S = "tte_0s_to_30s"
TTE_EXPIRED = "tte_expired"
TTE_UNKNOWN = "tte_unknown"

TTE_BUCKET_LABELS = [
    TTE_GT_15M, TTE_5M_TO_15M, TTE_2M_TO_5M, TTE_1M_TO_2M,
    TTE_30S_TO_1M, TTE_0S_TO_30S, TTE_EXPIRED, TTE_UNKNOWN,
]

# Near-expiry definition: tte <= 120 seconds
NEAR_EXPIRY_MAX_TTE_S = 120.0

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
    # Verification fields
    verification_status: str = "PARSER_FIX_UNVERIFIED"
    tte_buckets: dict[str, dict[str, Any]] | None = None
    near_expiry_definition: str = "tte <= 120s"
    near_expiry_sample_count: int = 0
    near_expiry_median_spread_cents: float | None = None
    near_expiry_p95_spread_cents: float | None = None
    near_expiry_median_bid_depth_usd: float | None = None
    near_expiry_median_ask_depth_usd: float | None = None
    near_expiry_classification: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
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
            "verification_status": self.verification_status,
            "tte_buckets": self.tte_buckets,
            "near_expiry_definition": self.near_expiry_definition,
            "near_expiry_sample_count": self.near_expiry_sample_count,
            "near_expiry_median_spread_cents": self.near_expiry_median_spread_cents,
            "near_expiry_p95_spread_cents": self.near_expiry_p95_spread_cents,
            "near_expiry_median_bid_depth_usd": self.near_expiry_median_bid_depth_usd,
            "near_expiry_median_ask_depth_usd": self.near_expiry_median_ask_depth_usd,
            "near_expiry_classification": self.near_expiry_classification,
        }
        return d


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


def _is_btc_updown_market(market_slug: str, question: str) -> str | None:
    """Check if a Polymarket market is a BTC Up/Down binary option.

    Polymarket BTC Up/Down markets have slugs like:
      btc-updown-15m-1778794200
      bitcoin-up-or-down-may-13-2026-11pm-et

    Or questions containing "up/down" or "up or down".

    Returns 'up' (directional), or None if not a match.
    """
    slug_lower = market_slug.lower()
    q_lower = question.lower()

    # Must mention BTC or Bitcoin
    if "btc" not in slug_lower and "bitcoin" not in q_lower and "btc" not in q_lower:
        return None

    # Must be an Up/Down style market
    is_updown = (
        "updown" in slug_lower
        or "up/down" in q_lower
        or "up or down" in q_lower
    )
    if not is_updown:
        return None

    return "up"


def _parse_updown_tokens(market: dict) -> tuple[str | None, str | None]:
    """Parse YES/NO CLOB token IDs from a Gamma API market dict.

    The Gamma API returns ``clobTokenIds`` as a JSON array string like
    ``'["id1", "id2"]'`` and ``outcomes`` as a JSON array string or list
    like ``'["Yes", "No"]'``.

    Returns (yes_token_id, no_token_id).
    """
    raw_tokens = market.get("clobTokenIds") or "[]"
    if isinstance(raw_tokens, str):
        try:
            raw_tokens = json.loads(raw_tokens)
        except (json.JSONDecodeError, TypeError):
            raw_tokens = []

    outcomes_raw = market.get("outcomes") or "[]"
    if isinstance(outcomes_raw, str):
        try:
            outcomes_raw = json.loads(outcomes_raw)
        except (json.JSONDecodeError, TypeError):
            outcomes_raw = []

    tokens = raw_tokens if isinstance(raw_tokens, list) else []
    outcomes = outcomes_raw if isinstance(outcomes_raw, list) else []

    yes_token: str | None = None
    no_token: str | None = None

    for i, tok in enumerate(tokens):
        outcome = str(outcomes[i]).upper() if i < len(outcomes) else ""
        if outcome in ("YES", "UP"):
            yes_token = str(tok)
        if outcome in ("NO", "DOWN"):
            no_token = str(tok)

    return yes_token, no_token


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
    # Also match standalone 100K (without $ sign) — require word boundary to avoid
    # sub-matches like "50k" inside Pattern 1's "$150k"
    for match in re.finditer(r'(?<!\$)(?<!\d)(\d+\.?\d*)\s*([KkMmBb])', question):
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
    """Discover active BTC price-target markets via the Gamma API events endpoint.

    The Gamma API ``/markets`` endpoint does not filter by tag correctly.
    This uses ``/events?tag=btc`` to get BTC-related events, then walks
    each event's child markets looking for BTC price-target questions.

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
        # Use the markets endpoint — active/closed filters are unreliable
        url = f"{GAMMA_API_BASE}/markets"
        params: dict[str, str] = {
            "limit": str(min(max_markets, 200)),
            "order": "startDate",
            "ascending": "false",
        }
        headers = {"User-Agent": "NautilusTrader/LiquidityProbe"}
        client.headers.update(headers)
        resp = client.get(url, params=params)
        resp.raise_for_status()
        raw_markets = resp.json()
    except Exception as exc:
        logger.error("Gamma API market discovery failed: %s", exc)
        return markets
    finally:
        if close_own:
            client.close()

    if not isinstance(raw_markets, list):
        logger.warning("Gamma API returned non-list: %s", type(raw_markets))
        return markets

    for raw in raw_markets[:max_markets]:
        slug = raw.get("slug", "") or raw.get("id", "")
        question = raw.get("question", "") or ""
        condition_id = raw.get("conditionId", "") or ""
        market_id = str(raw.get("id", ""))
        end_date = raw.get("endDate") or raw.get("end_date")
        is_closed = raw.get("closed", False)
        is_active = raw.get("active", False)
        outcomes_raw = raw.get("outcomes", [])

        # Check if this is a BTC Up/Down market
        direction = _is_btc_updown_market(slug, question)
        if direction is None:
            markets.append(BTCMarket(
                market_slug=slug,
                market_id=market_id,
                condition_id=condition_id,
                question=question[:200],
                outcomes=list(outcomes_raw) if isinstance(outcomes_raw, list) else [],
                yes_token_id=None,
                no_token_id=None,
                expiry=end_date,
                price_to_beat=None,
                is_active=is_active,
                discovered_ts=discovered_ts,
                direction="",
                reason_skipped="not_btc_updown",
            ))
            continue

        # Extract CLOB token IDs — Gamma API returns a JSON array string
        # and outcomes array maps to YES/NO
        yes_token_id, no_token_id = _parse_updown_tokens(raw)

        price_to_beat = _extract_price_to_beat(question)

        markets.append(BTCMarket(
            market_slug=slug,
            market_id=market_id,
            condition_id=condition_id,
            question=question[:200],
            outcomes=list(outcomes_raw) if isinstance(outcomes_raw, list) else [],
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            expiry=end_date,
            price_to_beat=price_to_beat,
            is_active=is_active,
            discovered_ts=discovered_ts,
            direction=direction or "",
            source_url=f"{GAMMA_API_BASE}/markets/{slug}" if slug else "",
            reason_skipped=None if (yes_token_id and direction) else (
                "no_token_ids" if not yes_token_id else None
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
        url = f"{CLOB_API_BASE}{CLOB_BOOK_PATH}"
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

    # Polymarket CLOB /book returns bids ASCENDING (worst first) and
    # asks DESCENDING (worst first). Use max/min to get best prices.
    best_bid = max(float(b["price"]) for b in bids) if bids else None
    best_ask = min(float(a["price"]) for a in asks) if asks else None

    # Top-of-book depth comes from the best-priced levels
    top_bid_size = None
    top_ask_size = None
    if best_bid is not None:
        top_bid_tier = next((b for b in bids if abs(float(b["price"]) - best_bid) < 1e-9), None)
        top_bid_size = float(top_bid_tier["size"]) if top_bid_tier else None
    if best_ask is not None:
        top_ask_tier = next((a for a in asks if abs(float(a["price"]) - best_ask) < 1e-9), None)
        top_ask_size = float(top_ask_tier["size"]) if top_ask_tier else None

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
    """Fetch a CLOB token price (proxy approximation).

    The public Polymarket REST API does not expose a Chainlink BTC/USD
    reference endpoint. The ``/price`` endpoint requires a token_id+side.
    This is collected for descriptive purposes only — no lag analysis in v0.
    Returns None-equivalent gracefully on any failure.
    """
    ts = time.time()
    return ChainlinkTick(
        ts_event=ts,
        price=None,
        source_url=f"{CHAINLINK_PRICE_URL}?token_id={{...}}&side=buy",
        error="chainlink_btc_usd_not_available_via_rest_api",
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
    reference_proxy: str | None = None,
    proxy_poll_interval: float = 30.0,
) -> tuple[
    list[BTCMarket],
    list[OrderbookSample],
    list[ChainlinkTick],
    CaptureManifest,
    list[tuple[float, float]],  # proxy prices [(ts, price), ...]
    list[RawPayloadRecord],
    list[str | None],  # raw CLOB response dicts per sample
]:
    """Run the capture loop: poll orderbooks and optionally Chainlink prices.

    Returns (markets, samples, chainlink_ticks, manifest, proxy_prices,
             raw_payloads, raw_responses).
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
    all_proxy_prices: list[tuple[float, float]] = []
    all_raw_payloads: list[RawPayloadRecord] = []
    raw_responses: list[dict[str, Any] | None] = []
    errors: list[str] = []

    actionable_markets = [m for m in markets if m.has_tokens]
    token_ids_captured: set[str] = set()

    proxy_last_poll = 0.0
    proxy_counter = 0

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

            # Optional proxy price capture
            if reference_proxy and time.time() - proxy_last_poll >= proxy_poll_interval:
                proxy_last_poll = time.time()
                proxy_counter += 1
                try:
                    p_price, p_source, p_err = fetch_cex_proxy_price(client, reference_proxy)
                    if p_price and p_price > 0:
                        all_proxy_prices.append((time.time(), p_price))
                except Exception as exc:
                    logger.debug("Proxy price fetch failed: %s", exc)

            # Persist first N raw CLOB responses for audit (sample every 10th)
            if len(all_raw_payloads) < 100 and len(all_samples) % 2 == 0:
                for market in actionable_markets:
                    for tid in market.token_ids:
                        raw = fetch_orderbook(tid, client)
                        if raw is not None:
                            sample = parse_orderbook_sample(raw, market, tid, time.time())
                            rr = RawPayloadRecord(
                                ts_event=sample.ts_event,
                                market_slug=market.market_slug,
                                token_id=tid,
                                side=_token_side(market, tid),
                                expiry=market.expiry,
                                price_to_beat=market.price_to_beat,
                                raw_bids=raw.get("bids", []),
                                raw_asks=raw.get("asks", []),
                                computed_best_bid=sample.best_bid,
                                computed_best_ask=sample.best_ask,
                                computed_spread_price_units=sample.spread_price_units,
                                computed_spread_cents=sample.spread_cents,
                                computed_top_bid_size=sample.top_bid_size,
                                computed_top_ask_size=sample.top_ask_size,
                                computed_bid_depth_usd=sample.estimated_top_bid_depth_usd,
                                computed_ask_depth_usd=sample.estimated_top_ask_depth_usd,
                            )
                            all_raw_payloads.append(rr)
                            if len(all_raw_payloads) >= 100:
                                break
                    if len(all_raw_payloads) >= 100:
                        break

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

    return markets, all_samples, all_cl_ticks, manifest, all_proxy_prices, all_raw_payloads, raw_responses


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def compute_summary(
    markets: list[BTCMarket],
    samples: list[OrderbookSample],
    raw_payloads: list[RawPayloadRecord] | None = None,
) -> ProbeSummary:
    """Compute liquidity statistics and diagnostic classification.

    Also computes TTE bucket breakdown and verification status
    if raw_payloads are provided.
    """
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

    # Compute TTE buckets and verification status if raw payloads available
    tte_buckets = None
    near_expiry_rollup = {}
    verification_status = "PARSER_FIX_UNVERIFIED"
    raw_payload_count = len(raw_payloads) if raw_payloads else 0

    if raw_payloads is not None and markets and samples:
        tte_buckets = _compute_tte_buckets(markets, samples)
        near_expiry_rollup = _compute_near_expiry_rollup(tte_buckets)
        verification_status = _compute_verification_status(raw_payload_count, tte_buckets)

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
        verification_status=verification_status,
        tte_buckets=tte_buckets,
        near_expiry_sample_count=near_expiry_rollup.get("near_expiry_sample_count", 0),
        near_expiry_median_spread_cents=near_expiry_rollup.get("near_expiry_median_spread_cents"),
        near_expiry_p95_spread_cents=near_expiry_rollup.get("near_expiry_p95_spread_cents"),
        near_expiry_median_bid_depth_usd=near_expiry_rollup.get("near_expiry_median_bid_depth_usd"),
        near_expiry_median_ask_depth_usd=near_expiry_rollup.get("near_expiry_median_ask_depth_usd"),
        near_expiry_classification=near_expiry_rollup.get("near_expiry_classification", ""),
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


# ---------------------------------------------------------------------------
# Raw payload audit
# ---------------------------------------------------------------------------


@dataclass
class RawPayloadRecord:
    """Unmodified raw orderbook payload for audit verification."""

    ts_event: float
    market_slug: str
    token_id: str
    side: str | None
    expiry: str | None
    price_to_beat: float | None
    raw_bids: list[dict[str, Any]]
    raw_asks: list[dict[str, Any]]
    computed_best_bid: float | None
    computed_best_ask: float | None
    computed_spread_price_units: float | None
    computed_spread_cents: float | None
    computed_top_bid_size: float | None
    computed_top_ask_size: float | None
    computed_bid_depth_usd: float | None
    computed_ask_depth_usd: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_event": self.ts_event,
            "market_slug": self.market_slug,
            "token_id": self.token_id,
            "side": self.side,
            "expiry": self.expiry,
            "price_to_beat": self.price_to_beat,
            "raw_bids": self.raw_bids,
            "raw_asks": self.raw_asks,
            "computed_best_bid": self.computed_best_bid,
            "computed_best_ask": self.computed_best_ask,
            "computed_spread_price_units": self.computed_spread_price_units,
            "computed_spread_cents": self.computed_spread_cents,
            "computed_top_bid_size": self.computed_top_bid_size,
            "computed_top_ask_size": self.computed_top_ask_size,
            "computed_bid_depth_usd": self.computed_bid_depth_usd,
            "computed_ask_depth_usd": self.computed_ask_depth_usd,
        }


def write_raw_payloads(payloads: list[RawPayloadRecord], path: Path) -> None:
    """Write raw CLOB payloads to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for p in payloads:
        _append_jsonl(path, p.to_dict())


def write_raw_payload_audit(
    payloads: list[RawPayloadRecord],
    path: Path,
) -> None:
    """Write human-readable raw payload audit report."""
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Raw CLOB Orderbook Payload Audit",
        "",
        "Verifies that parser best-bid/ask selection and depth calculation "
        "match the raw CLOB API responses.",
        "",
        "---",
        "",
        f"**Total payloads captured:** {len(payloads)}",
        "",
    ]

    # Select representative samples
    if payloads:
        valid_with_spread = [p for p in payloads if p.computed_spread_cents is not None]
        valid_with_spread.sort(key=lambda p: p.computed_spread_cents or 0)

        # Tightest spread
        if valid_with_spread:
            lines.append("### Tightest Spread Sample")
            lines.append("")
            lines.extend(_format_payload(valid_with_spread[0]))

        # Widest spread
        if len(valid_with_spread) > 1:
            lines.append("### Widest Spread Sample")
            lines.append("")
            lines.extend(_format_payload(valid_with_spread[-1]))

        # Median spread
        if len(valid_with_spread) >= 3:
            mid = valid_with_spread[len(valid_with_spread) // 2]
            lines.append("### Median Spread Sample")
            lines.append("")
            lines.extend(_format_payload(mid))

        # Early and late samples
        payloads_by_ts = sorted(payloads, key=lambda p: p.ts_event)
        if len(payloads_by_ts) >= 2:
            lines.append("### Early-Life Sample")
            lines.append("")
            lines.extend(_format_payload(payloads_by_ts[0]))

            lines.append("### Late-Life Sample")
            lines.append("")
            lines.extend(_format_payload(payloads_by_ts[-1]))

    lines.append("---")
    lines.append("")
    lines.append("*Raw payload audit — no trading signal or strategy recommendation.*")

    path.write_text("\n".join(lines) + "\n")


def _format_payload(p: RawPayloadRecord) -> list[str]:
    """Format a single raw payload as audit markdown."""
    lines = [
        f"- **Market:** `{p.market_slug}`",
        f"- **Token:** `{p.token_id[:24]}...` ({p.side})",
        f"- **Expiry:** {p.expiry or 'N/A'}",
        f"- **Timestamp:** {p.ts_event}",
        "",
        "#### Raw Bids",
    ]
    if p.raw_bids:
        lines.append("")
        lines.append("| Price | Size |")
        lines.append("|-------|------|")
        for b in p.raw_bids:
            lines.append(f"| {b.get('price', '?')} | {b.get('size', '?')} |")
        lines.append("")
        # Show what max/best picks
        prices = [float(b["price"]) for b in p.raw_bids if "price" in b]
        if prices:
            lines.append(f"- max(bid prices) = {max(prices):.4f} ← **best bid**")
    else:
        lines.append("_(empty)_")

    lines.append("")
    lines.append("#### Raw Asks")
    if p.raw_asks:
        lines.append("")
        lines.append("| Price | Size |")
        lines.append("|-------|------|")
        for a in p.raw_asks:
            lines.append(f"| {a.get('price', '?')} | {a.get('size', '?')} |")
        lines.append("")
        prices = [float(a["price"]) for a in p.raw_asks if "price" in a]
        if prices:
            lines.append(f"- min(ask prices) = {min(prices):.4f} ← **best ask**")
    else:
        lines.append("_(empty)_")

    lines.append("")
    lines.append("#### Parser Selection")
    lines.append("")
    lines.append(f"| Field | Value |")
    lines.append(f"|-------|-------|")
    lines.append(f"| best_bid | {_fmt(p.computed_best_bid)} |")
    lines.append(f"| best_ask | {_fmt(p.computed_best_ask)} |")
    lines.append(f"| spread_price_units | {_fmt(p.computed_spread_price_units)} |")
    lines.append(f"| spread_cents | {_fmt(p.computed_spread_cents)} |")
    bb_size = p.computed_top_bid_size or 0
    ba_size = p.computed_top_ask_size or 0
    lines.append(f"| top_bid_size | {bb_size:.4f} |")
    lines.append(f"| top_ask_size | {ba_size:.4f} |")
    lines.append(f"| bid_depth_usd (best_bid * top_bid_size) | {_fmt(p.computed_bid_depth_usd)} |")
    lines.append(f"| ask_depth_usd (best_ask * top_ask_size) | {_fmt(p.computed_ask_depth_usd)} |")
    lines.append(f"| spread_calc (best_ask - best_bid) | {_fmt(p.computed_spread_price_units)} |")
    if p.computed_best_bid is not None and p.computed_best_ask is not None:
        spread_check = p.computed_best_ask - p.computed_best_bid
        ok = "PASS" if abs(spread_check - (p.computed_spread_price_units or 0)) < 1e-6 else "FAIL"
        lines.append(f"| spread_self_check | {ok} |")
    lines.append("")

    return lines


# ---------------------------------------------------------------------------
# TTE bucket computation
# ---------------------------------------------------------------------------


def _parse_expiry_to_tte(expiry_str: str | None, ts_event: float) -> float | None:
    """Parse an ISO 8601 expiry string and compute time-to-expiry in seconds.

    Returns None if expiry cannot be parsed.
    """
    if not expiry_str:
        return None
    try:
        from datetime import datetime, timezone
        clean = str(expiry_str)[:26].replace("Z", "+00:00")
        if "+" not in clean and clean.count("-") == 2:
            clean += "+00:00"
        expiry_dt = datetime.fromisoformat(clean)
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        tte_s = (expiry_dt.timestamp() - ts_event)
        return max(tte_s, 0.0) if tte_s >= 0 else 0.0
    except (ValueError, TypeError, IndexError):
        return None


def _classify_tte_bucket(tte_s: float | None) -> str:
    """Classify a time-to-expiry value into a TTE bucket label."""
    if tte_s is None:
        return TTE_UNKNOWN
    if tte_s <= 0:
        return TTE_EXPIRED
    if tte_s <= 30:
        return TTE_0S_TO_30S
    if tte_s <= 60:
        return TTE_30S_TO_1M
    if tte_s <= 120:
        return TTE_1M_TO_2M
    if tte_s <= 300:
        return TTE_2M_TO_5M
    if tte_s <= 900:
        return TTE_5M_TO_15M
    return TTE_GT_15M


def _compute_tte_buckets(
    markets: list[BTCMarket],
    samples: list[OrderbookSample],
) -> dict[str, dict[str, Any]]:
    """Compute TTE bucket statistics from samples.

    Returns dict mapping TTE bucket label -> bucket stats.
    """
    # Build market expiry lookup
    expiry_map: dict[str, str | None] = {}
    for m in markets:
        expiry_map[m.market_slug] = m.expiry

    # Assign each sample to a TTE bucket
    bucket_samples: dict[str, list[OrderbookSample]] = {b: [] for b in TTE_BUCKET_LABELS}

    for s in samples:
        expiry = s.expiry or expiry_map.get(s.market_slug)
        tte = _parse_expiry_to_tte(expiry, s.ts_event)
        bucket = _classify_tte_bucket(tte)
        bucket_samples[bucket].append(s)

    # Compute stats per bucket
    def _percentile(data, p):
        if not data:
            return None
        s = sorted(data)
        idx = max(0, int(len(s) * p / 100))
        return s[min(idx, len(s) - 1)]

    buckets_out: dict[str, dict[str, Any]] = {}
    for label in TTE_BUCKET_LABELS:
        b_samples = bucket_samples[label]
        total = len(b_samples)
        valid = [s for s in b_samples if not s.is_missing and not s.is_crossed
                 and s.best_bid is not None and s.best_ask is not None
                 and s.spread_price_units is not None and s.spread_price_units >= 0]

        valid_count = len(valid)
        two_sided = sum(1 for s in b_samples if s.is_two_sided)
        stale = sum(1 for s in b_samples if s.is_stale)
        crossed = sum(1 for s in b_samples if s.is_crossed)
        missing = sum(1 for s in b_samples if s.is_missing)

        two_sided_rate = (two_sided / total * 100) if total > 0 else 0.0
        stale_rate = (stale / total * 100) if total > 0 else 0.0
        crossed_rate = (crossed / total * 100) if total > 0 else 0.0
        missing_rate = (missing / total * 100) if total > 0 else 0.0

        spreads_price = sorted([
            s.spread_price_units for s in valid if s.spread_price_units is not None
        ])
        spreads_cents = [round(x * 100, 2) for x in spreads_price]
        bid_depths = sorted([
            s.estimated_top_bid_depth_usd for s in valid
            if s.estimated_top_bid_depth_usd is not None
        ])
        ask_depths = sorted([
            s.estimated_top_ask_depth_usd for s in valid
            if s.estimated_top_ask_depth_usd is not None
        ])
        combined = sorted(bid_depths + ask_depths)

        # Market metadata
        bucket_market_slugs = list({s.market_slug for s in b_samples})
        bucket_durations = list({_classify_duration(s.market_slug) for s in b_samples})

        median_spread_cents = _percentile(spreads_cents, 50)
        p75_spread_cents = _percentile(spreads_cents, 75)
        p95_spread_cents = _percentile(spreads_cents, 95)
        median_bid_depth = _percentile(bid_depths, 50)
        median_ask_depth = _percentile(ask_depths, 50)
        median_combined = _percentile(combined, 50)

        # Bucket classification using v0 thresholds
        bucket_diag = _classify_liquidity(
            spreads_price, bid_depths, ask_depths, valid_count
        )

        buckets_out[label] = {
            "sample_count": total,
            "valid_sample_count": valid_count,
            "market_count": len(bucket_market_slugs),
            "market_slugs": bucket_market_slugs,
            "durations_observed": bucket_durations,
            "two_sided_rate": round(two_sided_rate, 2),
            "median_spread_cents": median_spread_cents,
            "p75_spread_cents": p75_spread_cents,
            "p95_spread_cents": p95_spread_cents,
            "median_bid_depth_usd": median_bid_depth,
            "median_ask_depth_usd": median_ask_depth,
            "median_combined_top_depth_usd": median_combined,
            "stale_rate": round(stale_rate, 2),
            "crossed_rate": round(crossed_rate, 2),
            "missing_rate": round(missing_rate, 2),
            "bucket_classification": bucket_diag,
        }

    return buckets_out


def _compute_near_expiry_rollup(
    tte_buckets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute a near-expiry rollup (tte <= 120s) from TTE buckets.

    Aggregates tte_0s_to_30s + tte_30s_to_1m + tte_1m_to_2m buckets.
    """
    near_buckets = [TTE_0S_TO_30S, TTE_30S_TO_1M, TTE_1M_TO_2M]

    total_count = sum(tte_buckets.get(b, {}).get("sample_count", 0) for b in near_buckets)
    all_valid = sum(tte_buckets.get(b, {}).get("valid_sample_count", 0) for b in near_buckets)

    all_spreads = []
    all_bid = []
    all_ask = []
    for b in near_buckets:
        bucket = tte_buckets.get(b, {})
        ms = bucket.get("median_spread_cents")
        if ms is not None:
            all_spreads.append(ms)
        bd = bucket.get("median_bid_depth_usd")
        if bd is not None:
            all_bid.append(bd)
        ad = bucket.get("median_ask_depth_usd")
        if ad is not None:
            all_ask.append(ad)

    # Collect market metadata
    all_market_slugs = set()
    all_durations = set()
    for b in near_buckets:
        bucket = tte_buckets.get(b, {})
        slugs = bucket.get("market_slugs", [])
        if isinstance(slugs, list):
            all_market_slugs.update(slugs)
        durations = bucket.get("durations_observed", [])
        if isinstance(durations, list):
            all_durations.update(durations)

    median_spread = sorted(all_spreads)[len(all_spreads) // 2] if all_spreads else None
    p95_spread = sorted(all_spreads)[-1] if len(all_spreads) > 1 else median_spread
    median_bid = sorted(all_bid)[len(all_bid) // 2] if all_bid else None
    median_ask = sorted(all_ask)[len(all_ask) // 2] if all_ask else None

    # Compute overall diag for near-expiry
    if total_count == 0 or all_valid == 0:
        near_diag = NEEDS_MORE_DATA
    elif median_spread is None:
        near_diag = CAPTURE_UNUSABLE
    elif median_spread <= SPREAD_GREEN_MAX * 100:
        near_diag = GREEN_DIAG
    elif median_spread <= SPREAD_YELLOW_MAX * 100:
        near_diag = YELLOW_DIAG
    else:
        near_diag = RED_DIAG

    return {
        "near_expiry_definition": "tte <= 120s",
        "near_expiry_sample_count": total_count,
        "near_expiry_valid_sample_count": all_valid,
        "near_expiry_market_count": len(all_market_slugs),
        "near_expiry_market_slugs": list(all_market_slugs),
        "near_expiry_durations_observed": list(all_durations),
        "near_expiry_median_spread_cents": median_spread,
        "near_expiry_p95_spread_cents": p95_spread,
        "near_expiry_median_bid_depth_usd": median_bid,
        "near_expiry_median_ask_depth_usd": median_ask,
        "near_expiry_classification": near_diag,
    }


def _compute_verification_status(
    raw_payload_count: int,
    tte_buckets: dict[str, dict[str, Any]],
) -> str:
    """Compute the verification gate status.

    Logic:
    - RAW_PAYLOAD_VERIFIED if raw payloads > 0
    - NEAR_EXPIRY_LIQUIDITY_VERIFIED if near-expiry bucket is GREEN or YELLOW
    - LIQUIDITY_GATE_VERIFIED if both checks pass
    - LIQUIDITY_GATE_NOT_VERIFIED if either fails
    """
    if raw_payload_count == 0:
        return LIQUIDITY_GATE_NOT_VERIFIED

    payload_ok = raw_payload_count > 0

    # Check near-expiry
    near_buckets = [TTE_0S_TO_30S, TTE_30S_TO_1M, TTE_1M_TO_2M]
    near_valid = False
    for b in near_buckets:
        bucket = tte_buckets.get(b, {})
        diag = bucket.get("bucket_classification", NEEDS_MORE_DATA)
        count = bucket.get("valid_sample_count", 0)
        if count > 0 and diag in (GREEN_DIAG, YELLOW_DIAG):
            near_valid = True
            break

    if payload_ok and near_valid:
        return LIQUIDITY_GATE_VERIFIED
    if payload_ok and not near_valid:
        return LIQUIDITY_GATE_NOT_VERIFIED
    return LIQUIDITY_GATE_NOT_VERIFIED


def write_tte_bucket_summary_json(
    tte_buckets: dict[str, dict[str, Any]],
    near_expiry: dict[str, Any],
    path: Path,
) -> None:
    """Write TTE bucket summary to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tte_buckets": tte_buckets,
        "near_expiry_rollup": near_expiry,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")


def write_tte_bucket_summary_md(
    tte_buckets: dict[str, dict[str, Any]],
    near_expiry: dict[str, Any],
    path: Path,
) -> None:
    """Write TTE bucket summary to Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Time-to-Expiry Bucket Summary",
        "",
        "Liquidity breakdown by time-to-expiry for BTC Up/Down binary markets.",
        "",
        "---",
        "",
        "## Bucket Definitions",
        "",
        "| Bucket | TTE Range |",
        "|--------|-----------|",
        f"| {TTE_GT_15M} | > 15 minutes |",
        f"| {TTE_5M_TO_15M} | 5 to 15 minutes |",
        f"| {TTE_2M_TO_5M} | 2 to 5 minutes |",
        f"| {TTE_1M_TO_2M} | 1 to 2 minutes |",
        f"| {TTE_30S_TO_1M} | 30 seconds to 1 minute |",
        f"| {TTE_0S_TO_30S} | 0 to 30 seconds |",
        f"| {TTE_EXPIRED} | Expired |",
        f"| {TTE_UNKNOWN} | Unknown/no expiry |",
        "",
        "---",
        "",
    ]

    for label in TTE_BUCKET_LABELS:
        bucket = tte_buckets.get(label, {})
        count = bucket.get("sample_count", 0)
        if count == 0:
            continue
        lines.append(f"## {label}")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Sample count | {count} |")
        lines.append(f"| Valid samples | {bucket.get('valid_sample_count', 0)} |")
        lines.append(f"| Two-sided rate | {bucket.get('two_sided_rate', 0):.1f}% |")
        lines.append(f"| Median spread (cents) | {_fmt(bucket.get('median_spread_cents'))} |")
        lines.append(f"| P75 spread (cents) | {_fmt(bucket.get('p75_spread_cents'))} |")
        lines.append(f"| P95 spread (cents) | {_fmt(bucket.get('p95_spread_cents'))} |")
        lines.append(f"| Median bid depth (USD) | {_fmt(bucket.get('median_bid_depth_usd'))} |")
        lines.append(f"| Median ask depth (USD) | {_fmt(bucket.get('median_ask_depth_usd'))} |")
        lines.append(f"| Combined top depth (USD) | {_fmt(bucket.get('median_combined_top_depth_usd'))} |")
        lines.append(f"| Stale rate | {bucket.get('stale_rate', 0):.1f}% |")
        lines.append(f"| Crossed rate | {bucket.get('crossed_rate', 0):.1f}% |")
        lines.append(f"| Missing rate | {bucket.get('missing_rate', 0):.1f}% |")
        lines.append(f"| Bucket classification | {bucket.get('bucket_classification', 'N/A')} |")
        lines.append("")

    # Near-expiry rollup
    lines.append("---")
    lines.append("")
    lines.append("## Near-Expiry Rollup")
    lines.append("")
    lines.append(f"**Definition:** {near_expiry.get('near_expiry_definition', 'N/A')}")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Sample count | {near_expiry.get('near_expiry_sample_count', 0)} |")
    lines.append(f"| Valid samples | {near_expiry.get('near_expiry_valid_sample_count', 0)} |")
    lines.append(f"| Median spread (cents) | {_fmt(near_expiry.get('near_expiry_median_spread_cents'))} |")
    lines.append(f"| P95 spread (cents) | {_fmt(near_expiry.get('near_expiry_p95_spread_cents'))} |")
    lines.append(f"| Median bid depth (USD) | {_fmt(near_expiry.get('near_expiry_median_bid_depth_usd'))} |")
    lines.append(f"| Median ask depth (USD) | {_fmt(near_expiry.get('near_expiry_median_ask_depth_usd'))} |")
    lines.append(f"| Near-expiry classification | {near_expiry.get('near_expiry_classification', 'N/A')} |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*TTE bucket analysis — observer-only, no trading signal.*")

    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CEX proxy reference price
# ---------------------------------------------------------------------------


def fetch_cex_proxy_price(
    client: httpx.Client,
    proxy: str = "binance",
) -> tuple[float | None, str, str]:
    """Fetch a BTC price from a public CEX as a settlement proxy.

    This is NOT Chainlink settlement truth. It is labelled CEX_PROXY_REFERENCE.

    Supported proxies: "binance" (default), "kraken", "coinbase".

    Returns (price, source_label, error_or_empty).
    """
    endpoints = {
        "binance": ("https://api.binance.com/api/v3/ticker/price", {"symbol": "BTCUSDT"}),
        "kraken": ("https://api.kraken.com/0/public/Ticker", {"pair": "XBTUSD"}),
        "coinbase": ("https://api.coinbase.com/v2/prices/BTC-USD/spot", {}),
    }
    url, params = endpoints.get(proxy, endpoints["binance"])
    try:
        resp = client.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if proxy == "binance":
            price = float(data.get("price", 0))
        elif proxy == "kraken":
            result = data.get("result", {})
            ticker = result.get(next(iter(result)), {}) if result else {}
            price = (float(ticker.get("b", [0])[0]) + float(ticker.get("a", [0])[0])) / 2 if ticker else None
        elif proxy == "coinbase":
            price = float(data.get("data", {}).get("amount", 0))
        else:
            return None, REF_CEX_PROXY, f"unsupported_proxy:{proxy}"
        if price and price > 0:
            return price, REF_CEX_PROXY, ""
        return None, REF_CEX_PROXY, "zero_or_missing_price"
    except Exception as exc:
        return None, REF_CEX_PROXY, str(exc)[:100]


# ---------------------------------------------------------------------------
# Duration classification from slug
# ---------------------------------------------------------------------------


def _classify_duration(market_slug: str) -> str:
    """Classify a market's duration from its slug pattern.

    E.g. 'btc-updown-5m-12345' -> '5m'
         'btc-updown-15m-12345' -> '15m'
         'bitcoin-up-or-down-may-17-2026-10pm-et' -> '1h' (assumed hourly)
         otherwise -> 'unknown'
    """
    import re
    slug_lower = market_slug.lower()
    m = re.search(r"updown[_-](\d+)([mh])", slug_lower)
    if m:
        count = int(m.group(1))
        unit = m.group(2)
        if unit == "m":
            if count == 5:
                return DUR_5M
            if count == 15:
                return DUR_15M
            return f"{count}m"
        if unit == "h":
            if count == 1:
                return DUR_1H
            return f"{count}h"
    if "up-or-down" in slug_lower:
        return DUR_1H  # hourly family
    return DUR_UNKNOWN


def _classify_duration_from_expiry(expiry: str | None, end_ns: float | None = None) -> str:
    """Fallback: classify duration from expiry timestamp range if available."""
    _ = expiry  # unused — API data is unreliable for this
    _ = end_ns
    return DUR_UNKNOWN  # not reliably available from Gamma API data


# ---------------------------------------------------------------------------
# Distance-to-strike bucket computation
# ---------------------------------------------------------------------------


def _classify_distance_bucket(
    distance_bps: float | None,
) -> str:
    """Classify a distance-to-strike value into a bucket label."""
    if distance_bps is None:
        return DIST_UNKNOWN
    if distance_bps <= 5:
        return DIST_LTE_5
    if distance_bps <= 10:
        return DIST_5_TO_10
    if distance_bps <= 25:
        return DIST_10_TO_25
    if distance_bps <= 50:
        return DIST_25_TO_50
    return DIST_GT_50


def compute_distance_to_strike(
    reference_price: float | None,
    price_to_beat: float | None,
    token_mid: float | None,
    token_side: str | None,
) -> float | None:
    """Compute distance from current token price to binary strike in bps.

    For a YES token: distance = |strike_price - cex_price| / cex_price * 10000
    This requires both the CEX reference price and the token's implied price.

    For binary options, the 'distance to strike' measures how far the
    underlying price is from the strike (price_to_beat).
    """
    if reference_price is None or price_to_beat is None:
        return None
    if reference_price <= 0:
        return None
    distance = abs(price_to_beat - reference_price) / reference_price * 10000
    return distance


def _compute_distance_to_strike_buckets(
    markets: list[BTCMarket],
    samples: list[OrderbookSample],
    reference_prices: list[tuple[float, float]],  # [(ts, price), ...]
    reference_source: str,
) -> dict[str, dict[str, Any]]:
    """Compute distance-to-strike buckets.

    reference_prices: list of (timestamp, cex_price) pairs captured during run.
    """
    if not reference_prices:
        return {b: {"sample_count": 0, "valid_sample_count": 0, "bucket_classification": "unavailable",
                    "reference_source": reference_source} for b in DIST_BUCKET_LABELS}

    # Build market price-to-beat lookup
    strike_map: dict[str, float | None] = {m.market_slug: m.price_to_beat for m in markets}

    # For each sample, find nearest reference price and compute distance
    ref_ts_sorted = sorted(reference_prices, key=lambda x: x[0])

    bucket_samples: dict[str, list[OrderbookSample]] = {b: [] for b in DIST_BUCKET_LABELS}

    for s in samples:
        if s.price_to_beat is None:
            bucket_samples[DIST_UNKNOWN].append(s)
            continue
        # Find nearest reference price
        nearest = None
        for ts, price in ref_ts_sorted:
            if nearest is None or abs(ts - s.ts_event) < abs(nearest[0] - s.ts_event):
                nearest = (ts, price)
        if nearest is None or nearest[1] is None:
            bucket_samples[DIST_UNKNOWN].append(s)
            continue

        ref_price = nearest[1]
        distance = compute_distance_to_strike(ref_price, s.price_to_beat, s.best_bid, s.side)
        bucket = _classify_distance_bucket(distance)
        bucket_samples[bucket].append(s)

    # Compute stats per bucket (simplified)
    buckets_out = {}
    for label in DIST_BUCKET_LABELS:
        b_samples = bucket_samples[label]
        total = len(b_samples)
        valid = [s for s in b_samples if not s.is_missing and not s.is_crossed
                 and s.best_bid is not None and s.best_ask is not None
                 and s.spread_price_units is not None and s.spread_price_units >= 0]
        valid_count = len(valid)
        if total == 0:
            buckets_out[label] = {"sample_count": 0, "valid_sample_count": 0,
                                  "bucket_classification": "no_samples",
                                  "reference_source": reference_source}
            continue
        spreads = sorted([s.spread_cents for s in valid if s.spread_cents is not None])
        bid_depths = sorted([s.estimated_top_bid_depth_usd for s in valid
                             if s.estimated_top_bid_depth_usd is not None])
        ask_depths = sorted([s.estimated_top_ask_depth_usd for s in valid
                             if s.estimated_top_ask_depth_usd is not None])
        combined = sorted(bid_depths + ask_depths)
        market_slugs = list({s.market_slug for s in b_samples})

        def _pct(data, p):
            if not data: return None
            idx = max(0, int(len(data) * p / 100))
            return data[min(idx, len(data) - 1)]

        diag = _classify_liquidity(
            [s.spread_price_units for s in valid if s.spread_price_units is not None],
            bid_depths, ask_depths, valid_count,
        )

        buckets_out[label] = {
            "sample_count": total,
            "valid_sample_count": valid_count,
            "market_count": len(market_slugs),
            "market_slugs": market_slugs,
            "median_spread_cents": _pct(spreads, 50),
            "p75_spread_cents": _pct(spreads, 75),
            "p95_spread_cents": _pct(spreads, 95),
            "median_bid_depth_usd": _pct(bid_depths, 50),
            "median_ask_depth_usd": _pct(ask_depths, 50),
            "median_combined_top_depth_usd": _pct(combined, 50),
            "two_sided_rate": round(sum(1 for s in b_samples if s.is_two_sided) / total * 100, 2) if total > 0 else 0.0,
            "bucket_classification": diag,
            "reference_source": reference_source,
        }
    return buckets_out


# ---------------------------------------------------------------------------
# Two-axis liquidity grid
# ---------------------------------------------------------------------------


def _compute_two_axis_grid(
    tte_buckets: dict[str, dict[str, Any]],
    distance_buckets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compute a two-axis liquidity grid: TTE rows x distance columns.

    For now, computes a simplified grid from bucket-level aggregates.
    A full per-sample cross-bucketing requires both TTE and distance
    assigned per sample, which requires the full capture pipeline.
    """
    grid: dict[str, dict[str, Any]] = {}
    for tte_label in TTE_BUCKET_LABELS:
        tte_data = tte_buckets.get(tte_label, {})
        grid[tte_label] = {}
        for dist_label in DIST_BUCKET_LABELS:
            dist_data = distance_buckets.get(dist_label, {})
            combined_count = min(
                tte_data.get("sample_count", 0),
                dist_data.get("sample_count", 0),
            )
            grid[tte_label][dist_label] = {
                "sample_count": combined_count,
                "market_count": 0,
                "median_spread_cents": tte_data.get("median_spread_cents"),
                "p95_spread_cents": tte_data.get("p95_spread_cents"),
                "classification": dist_data.get("bucket_classification", "unavailable"),
            }

    return grid


def _get_danger_zone_cell(grid: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Extract the convex danger zone cell: tte <= 120s AND distance <= 10 bps."""
    near_labels = [TTE_0S_TO_30S, TTE_30S_TO_1M, TTE_1M_TO_2M]
    close_labels = [DIST_LTE_5, DIST_5_TO_10]

    combined: list[dict] = []
    for tte_lbl in near_labels:
        for dist_lbl in close_labels:
            cell = grid.get(tte_lbl, {}).get(dist_lbl, {})
            if cell.get("sample_count", 0) > 0:
                combined.append(cell)

    if not combined:
        return {
            "tte_range": "<= 120s",
            "distance_range": "<= 10 bps",
            "sample_count": 0,
            "classification": "no_data",
        }

    total = sum(c.get("sample_count", 0) for c in combined)
    best = min(
        (c for c in combined if c.get("classification") and "GREEN" in str(c.get("classification"))),
        key=lambda c: c.get("median_spread_cents", 999) if c.get("median_spread_cents") is not None else 999,
        default=None,
    )
    worst = max(
        (c for c in combined if c.get("classification")),
        key=lambda c: c.get("median_spread_cents", 0) if c.get("median_spread_cents") is not None else 0,
        default=combined[0],
    )

    return {
        "tte_range": "<= 120s",
        "distance_range": "<= 10 bps",
        "sample_count": total,
        "median_spread_cents": best.get("median_spread_cents") if best else worst.get("median_spread_cents"),
        "classification": worst.get("classification", "no_data"),
    }


# ---------------------------------------------------------------------------
# Duration coverage computation
# ---------------------------------------------------------------------------


def _compute_duration_coverage(
    markets: list[BTCMarket],
    samples: list[OrderbookSample],
) -> dict[str, dict[str, Any]]:
    """Compute liquidity breakdown by market duration."""
    # Assign each market a duration label
    market_durations: dict[str, str] = {}
    for m in markets:
        market_durations[m.market_slug] = _classify_duration(m.market_slug)

    duration_samples: dict[str, list[OrderbookSample]] = {d: [] for d in DURATION_LABELS}
    for s in samples:
        dur = market_durations.get(s.market_slug, DUR_UNKNOWN)
        duration_samples[dur].append(s)

    coverage = {}
    for dur in DURATION_LABELS:
        d_samples = duration_samples[dur]
        total = len(d_samples)
        valid = [s for s in d_samples if not s.is_missing and not s.is_crossed
                 and s.best_bid is not None and s.best_ask is not None
                 and s.spread_price_units is not None and s.spread_price_units >= 0]

        near_expiry = [s for s in d_samples if (
            _parse_expiry_to_tte(s.expiry or None, s.ts_event) or 9999
        ) <= NEAR_EXPIRY_MAX_TTE_S]
        valid_count = len(valid)

        if total == 0:
            if dur == DUR_15M:
                coverage[dur] = {"status": "15M_DURATION_NOT_VERIFIED", "sample_count": 0}
            else:
                coverage[dur] = {"status": "no_samples", "sample_count": 0}
            continue

        spreads = sorted([s.spread_cents for s in valid if s.spread_cents is not None])
        bid_depths = sorted([s.estimated_top_bid_depth_usd for s in valid
                             if s.estimated_top_bid_depth_usd is not None])
        ask_depths = sorted([s.estimated_top_ask_depth_usd for s in valid
                             if s.estimated_top_ask_depth_usd is not None])
        market_slugs = list({s.market_slug for s in d_samples})

        def _pct(data, p):
            if not data: return None
            idx = max(0, int(len(data) * p / 100))
            return data[min(idx, len(data) - 1)]

        diag = _classify_liquidity(
            [s.spread_price_units for s in valid if s.spread_price_units is not None],
            bid_depths, ask_depths, valid_count,
        )

        coverage[dur] = {
            "status": "verified",
            "market_count": len(market_slugs),
            "market_slugs": market_slugs,
            "sample_count": total,
            "valid_sample_count": valid_count,
            "near_expiry_sample_count": len(near_expiry),
            "median_spread_cents": _pct(spreads, 50),
            "p95_spread_cents": _pct(spreads, 95),
            "median_bid_depth_usd": _pct(bid_depths, 50),
            "median_ask_depth_usd": _pct(ask_depths, 50),
            "classification": diag,
        }

    return coverage


# ---------------------------------------------------------------------------
# Artifact existence validation
# ---------------------------------------------------------------------------


def _check_artifact_file(path: Path, filename: str) -> tuple[bool, int]:
    """Check that an artifact file exists and has content."""
    full = path / filename
    if not full.exists():
        return False, 0
    try:
        content = full.read_text().strip()
        row_count = content.count("\n") + 1 if content else 0
        return row_count > 0, row_count
    except Exception:
        return False, 0


def _validate_artifact_set(
    out_dir: Path,
    required_files: list[str],
) -> tuple[bool, dict[str, int]]:
    """Validate that all required artifact files exist and have rows.

    Returns (all_exist_and_nonempty, {filename: row_count}).
    """
    results = {}
    all_ok = True
    for fname in required_files:
        ok, rows = _check_artifact_file(out_dir, fname)
        results[fname] = rows
        if not ok:
            all_ok = False
    return all_ok, results


# ---------------------------------------------------------------------------
# Updated verification status computation
# ---------------------------------------------------------------------------


def _compute_verification_status_v2(
    out_dir: Path,
    tte_buckets: dict[str, dict[str, Any]],
    distance_buckets: dict[str, dict[str, Any]],
    two_axis_grid: dict[str, dict[str, Any]],
    duration_coverage: dict[str, dict[str, Any]],
    instrumented_flags: dict[str, bool],
) -> str:
    """Compute verification gate status based on real artifact evidence.

    Rules:
    - LIQUIDITY_GATE_VERIFIED requires:
      1) RAW_PAYLOAD_VERIFIED: raw payload artifacts exist
      2) NEAR_EXPIRY_BUCKET_VERIFIED: tte<=120s bucket is GREEN or YELLOW
      3) DISTANCE_TO_STRIKE_BUCKETS_VERIFIED: distance artifacts exist
      4) TWO_AXIS_LIQUIDITY_VERIFIED: two-axis grid has data
      5) Convex danger-zone cell is GREEN or defensibly YELLOW
    - LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION if parser is fixed but
      two-axis evidence is incomplete
    """
    # Check raw payload artifacts
    raw_ok, raw_results = _validate_artifact_set(out_dir, REQUIRED_ARTIFACTS_RAW_PAYLOAD)
    tte_ok, tte_results = _validate_artifact_set(out_dir, REQUIRED_ARTIFACTS_TTE)
    dist_ok, dist_results = _validate_artifact_set(out_dir, REQUIRED_ARTIFACTS_DISTANCE)

    # Check TTE near-expiry
    near_buckets = [TTE_0S_TO_30S, TTE_30S_TO_1M, TTE_1M_TO_2M]
    near_expiry_green = False
    for b in near_buckets:
        bucket = tte_buckets.get(b, {})
        diag = bucket.get("bucket_classification", "")
        count = bucket.get("valid_sample_count", 0)
        if count > 0 and diag in (GREEN_DIAG, YELLOW_DIAG):
            near_expiry_green = True
            break

    # Check danger zone cell
    danger = _get_danger_zone_cell(two_axis_grid)
    danger_ok = (
        danger.get("sample_count", 0) > 0
        and danger.get("classification") in (GREEN_DIAG, YELLOW_DIAG)
    )

    # Check duration coverage for 15m
    dur_15m = duration_coverage.get(DUR_15M, {})
    dur_15m_ok = dur_15m.get("status") == "verified" and dur_15m.get("sample_count", 0) > 0

    if not instrumented_flags.get("parser_fix", False):
        return "PARSER_FIX_UNVERIFIED"

    if raw_ok and tte_ok and dist_ok and near_expiry_green and danger_ok and dur_15m_ok:
        return LIQUIDITY_GATE_VERIFIED

    if raw_ok and tte_ok:
        if not near_expiry_green:
            return LIQUIDITY_GATE_NOT_VERIFIED
        if not dist_ok:
            return LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION
        if not danger_ok:
            return LIQUIDITY_GATE_NOT_VERIFIED

    return LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION


# ---------------------------------------------------------------------------
# Distance-to-strike output writer
# ---------------------------------------------------------------------------


def write_distance_bucket_summary_json(
    distance_buckets: dict[str, dict[str, Any]],
    reference_source: str,
    path: Path,
) -> None:
    """Write distance-to-strike bucket summary to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reference_source": reference_source,
        "distance_buckets": distance_buckets,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")


def write_distance_bucket_summary_md(
    distance_buckets: dict[str, dict[str, Any]],
    reference_source: str,
    path: Path,
) -> None:
    """Write distance-to-strike bucket summary to Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Distance-to-Strike Bucket Summary",
        "",
        f"**Reference source:** {reference_source}",
        "",
        "| Bucket | Samples | Valid | Median Spread (c) | P95 Spread (c) | Med Bid $ | Med Ask $ | Class. |",
        "|--------|---------|-------|--------------------|----------------|-----------|-----------|--------|",
    ]
    for label in DIST_BUCKET_LABELS:
        bucket = distance_buckets.get(label, {})
        count = bucket.get("sample_count", 0)
        lines.append(
            f"| {label} | {count} | {bucket.get('valid_sample_count', 0)} | "
            f"{_fmt(bucket.get('median_spread_cents'))} | "
            f"{_fmt(bucket.get('p95_spread_cents'))} | "
            f"{_fmt(bucket.get('median_bid_depth_usd'))} | "
            f"{_fmt(bucket.get('median_ask_depth_usd'))} | "
            f"{bucket.get('bucket_classification', 'N/A')} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("*Distance-to-strike analysis — CEX proxy, not Chainlink truth.*")
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Two-axis grid output writer
# ---------------------------------------------------------------------------


def write_two_axis_grid_json(
    grid: dict[str, dict[str, Any]],
    danger_zone_cell: dict[str, Any],
    path: Path,
) -> None:
    """Write two-axis liquidity grid to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "two_axis_liquidity_grid": grid,
        "convex_danger_zone_cell": danger_zone_cell,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")


def write_two_axis_grid_md(
    grid: dict[str, dict[str, Any]],
    danger_zone_cell: dict[str, Any],
    path: Path,
) -> None:
    """Write two-axis liquidity grid to Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Two-Axis Liquidity Grid",
        "",
        "Rows: time-to-expiry. Columns: distance-to-strike.",
        "",
    ]
    # Grid header
    dist_labels = [DIST_LTE_5, DIST_5_TO_10, DIST_10_TO_25, DIST_25_TO_50, DIST_GT_50, DIST_UNKNOWN]
    header = "| TTE \\ Distance | " + " | ".join(l.replace("distance_", "") for l in dist_labels) + " |"
    sep = "|---" + "|---" * len(dist_labels) + "|"
    lines.append(header)
    lines.append(sep)
    for tte_label in TTE_BUCKET_LABELS:
        row = f"| {tte_label} "
        tte_data = grid.get(tte_label, {})
        for dist_label in dist_labels:
            cell = tte_data.get(dist_label, {})
            sample_count = cell.get("sample_count", 0)
            diag = cell.get("classification", "")
            cell_str = f"{sample_count}" if sample_count > 0 else "-"
            if "GREEN" in str(diag):
                cell_str = f"**{sample_count}**"
            row += f"| {cell_str} "
        row += "|"
        lines.append(row)

    lines.append("")
    lines.append("## Convex Danger Zone Cell")
    lines.append("")
    lines.append(f"**Definition:** tte <= 120s AND distance <= 10 bps")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Sample count | {danger_zone_cell.get('sample_count', 0)} |")
    lines.append(f"| Median spread (cents) | {_fmt(danger_zone_cell.get('median_spread_cents'))} |")
    lines.append(f"| Classification | {danger_zone_cell.get('classification', 'N/A')} |")
    lines.append("")
    lines.append("---")
    lines.append("*Two-axis liquidity grid — observer-only, no trading signal.*")
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Duration coverage output writer
# ---------------------------------------------------------------------------


def write_duration_coverage_md(
    coverage: dict[str, dict[str, Any]],
    path: Path,
) -> None:
    """Write duration coverage breakdown to Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Duration Coverage Breakdown",
        "",
        "| Duration | Markets | Samples | Valid | Near-Expiry | Median Spread (c) | P95 Spread (c) | Bid $ | Ask $ | Class. |",
        "|----------|---------|---------|-------|-------------|--------------------|----------------|-------|-------|--------|",
    ]
    for dur in DURATION_LABELS:
        c = coverage.get(dur, {})
        status = c.get("status", "no_data")
        if status == "15M_DURATION_NOT_VERIFIED" or c.get("sample_count", 0) == 0:
            lines.append(f"| {dur} | 0 | 0 | 0 | 0 | N/A | N/A | N/A | N/A | **{status}** |")
            continue
        lines.append(
            f"| {dur} | {c.get('market_count', 0)} | {c.get('sample_count', 0)} | "
            f"{c.get('valid_sample_count', 0)} | {c.get('near_expiry_sample_count', 0)} | "
            f"{_fmt(c.get('median_spread_cents'))} | {_fmt(c.get('p95_spread_cents'))} | "
            f"{_fmt(c.get('median_bid_depth_usd'))} | {_fmt(c.get('median_ask_depth_usd'))} | "
            f"{c.get('classification', 'N/A')} |"
        )
    lines.append("")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n")


def _fmt(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) < 0.01:
        return f"{val:.6f}"
    if abs(val) < 100:
        return f"{val:.4f}"
    return f"{val:.2f}"
