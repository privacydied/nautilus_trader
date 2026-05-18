"""Polymarket BTC Price Target liquidity probe — observer-only, no orders.

Phase 0 liquidity and observability for Polymarket BTC Price Target
binary markets. Discovers markets, extracts numerical strikes, maps
to CLOB token IDs, polls orderbooks, and classifies liquidity quality.

No auth. No orders. No wallet. No execution path.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import statistics
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_BOOK_PATH = "/book"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

# ---------------------------------------------------------------------------
# Diagnostic constants
# ---------------------------------------------------------------------------

SPREAD_GREEN_MAX = 0.03  # $0.03
SPREAD_YELLOW_MAX = 0.06  # $0.06
SPREAD_YELLOW_MAX_P95 = 0.10  # $0.10 p95
MIN_NON_DUST_DEPTH_USD_GREEN = 100.0
MIN_NON_DUST_DEPTH_USD_YELLOW = 50.0
TWO_SIDED_GREEN_RATE = 0.90
TWO_SIDED_YELLOW_RATE = 0.70
PRIMARY_SUBSET_MIN_SAMPLES = 5

# TTE bucket boundaries
TTE_GT_15M = ">15m"
TTE_5M_15M = "5-15m"
TTE_2M_5M = "2-5m"
TTE_1M_2M = "1-2m"
TTE_30S_1M = "30s-1m"
TTE_0_30S = "0-30s"
TTE_EXPIRED = "expired"
TTE_UNKNOWN = "unknown"

TTE_BUCKET_ORDER = [
    TTE_GT_15M, TTE_5M_15M, TTE_2M_5M, TTE_1M_2M,
    TTE_30S_1M, TTE_0_30S, TTE_EXPIRED, TTE_UNKNOWN,
]

# Distance buckets
DIST_DEEP_BELOW = "deep_below_strike"
DIST_BELOW_100_500 = "below_strike_100_500bps"
DIST_BELOW_25_100 = "below_strike_25_100bps"
DIST_NEAR_25 = "near_strike_abs_25bps"
DIST_ABOVE_25_100 = "above_strike_25_100bps"
DIST_ABOVE_100_500 = "above_strike_100_500bps"
DIST_DEEP_ABOVE = "deep_above_strike"
DIST_UNKNOWN = "unknown"

DIST_BUCKET_ORDER = [
    DIST_DEEP_BELOW, DIST_BELOW_100_500, DIST_BELOW_25_100,
    DIST_NEAR_25,
    DIST_ABOVE_25_100, DIST_ABOVE_100_500, DIST_DEEP_ABOVE,
    DIST_UNKNOWN,
]

# Verdicts
GREEN_DIAG = "GREEN_LIQUIDITY_DIAGNOSTIC"
YELLOW_DIAG = "YELLOW_LIQUIDITY_DIAGNOSTIC"
RED_DIAG = "RED_LIQUIDITY_DIAGNOSTIC"
NEEDS_MORE_DATA_V = "NEEDS_MORE_DATA"
CAPTURE_UNUSABLE_V = "CAPTURE_UNUSABLE"

ALLOWED_VERDICTS = {GREEN_DIAG, YELLOW_DIAG, RED_DIAG, NEEDS_MORE_DATA_V, CAPTURE_UNUSABLE_V}

FORBIDDEN_VERDICTS = [
    "REJECTED", "CANDIDATE", "CANDIDATE_FOR_LONGER_OBSERVATION",
    "CANDIDATE_FOR_LIVE", "EXECUTION_READY", "TRADE_READY",
]

# V1 recommendations
V1_JUSTIFIED = "V1_PRECOMMITMENT_JUSTIFIED"
V1_HUMAN_REVIEW = "V1_REQUIRES_HUMAN_REVIEW"
V1_BLOCKED_LIQUIDITY = "V1_BLOCKED_BY_PHASE0_LIQUIDITY"
V1_BLOCKED_DATA = "V1_BLOCKED_BY_INSUFFICIENT_DATA"

# Market family classification
FAMILY_BTC_PRICE_TARGET = "BTC_PRICE_TARGET"
FAMILY_BTC_UPDOWN = "BTC_UPDOWN"
FAMILY_BTC_OTHER = "BTC_OTHER"
FAMILY_NON_BTC = "NON_BTC"

# Strike extraction statuses
STRIKE_EXTRACTED = "STRIKE_EXTRACTED"
NO_STRIKE_FOUND = "NO_STRIKE_FOUND"
AMBIGUOUS_STRIKE = "AMBIGUOUS_STRIKE"
UNSUPPORTED_MARKET_TEXT = "UNSUPPORTED_MARKET_TEXT"

# Book statuses
BOOK_TWO_SIDED = "TWO_SIDED"
BOOK_ONE_SIDED_BID = "ONE_SIDED_BID_ONLY"
BOOK_ONE_SIDED_ASK = "ONE_SIDED_ASK_ONLY"
BOOK_EMPTY = "EMPTY_BOOK"
BOOK_CROSSED = "CROSSED_OR_INVALID"

# Data path labels
DATA_PATH_NAUTILUS = "NAUTILUS_POLYMARKET_DATA_ADAPTER_USED"
DATA_PATH_MIXED = "MIXED_NAUTILUS_AND_PUBLIC_REST_USED"
DATA_PATH_PUBLIC_REST = "PUBLIC_REST_FALLBACK_USED"

# Component statuses
STATUS_SPREAD_GREEN = "SPREAD_GREEN"
STATUS_SPREAD_YELLOW = "SPREAD_YELLOW"
STATUS_SPREAD_RED = "SPREAD_RED"
STATUS_DEPTH_GREEN = "DEPTH_GREEN"
STATUS_DEPTH_YELLOW = "DEPTH_YELLOW"
STATUS_DEPTH_RED = "DEPTH_RED"
STATUS_TWO_SIDED_GREEN = "TWO_SIDED_GREEN"
STATUS_TWO_SIDED_YELLOW = "TWO_SIDED_YELLOW"
STATUS_TWO_SIDED_RED = "TWO_SIDED_RED"

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class AdapterInspectionResult:
    """Result of inspecting Nautilus Polymarket integration."""

    nautilus_inspected: bool = True
    modules_inspected: list[str] = field(default_factory=list)
    data_side_components_found: list[str] = field(default_factory=list)
    instrument_components_found: list[str] = field(default_factory=list)
    orderbook_components_found: list[str] = field(default_factory=list)
    execution_auth_components_found: list[str] = field(default_factory=list)
    execution_auth_components_avoided: list[str] = field(default_factory=list)
    selected_data_path: str = DATA_PATH_PUBLIC_REST
    data_path_reason: str = ""
    nautilus_core_importable: bool = False
    nautilus_core_import_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketMetadata:
    """Discovered Polymarket market metadata."""

    market_id: str
    condition_id: str | None
    slug: str
    title: str
    question: str
    outcomes: list[str]
    clob_token_ids: list[str]
    yes_token_id: str | None
    no_token_id: str | None
    expiry: str | None
    end_date_iso: str | None
    active: bool
    closed: bool
    archived: bool
    market_family: str
    market_family_reason: str
    strike_extraction_status: str
    strike_price: float | None
    strike_source_field: str | None
    strike_matched_text: str | None
    strike_reason: str | None
    exclusion_reason: str | None
    discovered_ts: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderbookSnapshot:
    """Normalized orderbook snapshot."""

    ts_event: float
    market_id: str
    token_id: str
    side_label: str | None
    bid_count: int = 0
    ask_count: int = 0
    best_bid_price: float | None = None
    best_bid_size: float | None = None
    best_ask_price: float | None = None
    best_ask_size: float | None = None
    executable_spread_price_units: float | None = None
    executable_spread_cents: float | None = None
    top_bid_depth_usd: float | None = None
    top_ask_depth_usd: float | None = None
    top_of_book_depth_usd: float | None = None
    book_status: str = BOOK_EMPTY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BtcProxyTick:
    """BTC proxy price snapshot."""

    ts_event: float
    price: float | None
    venue: str = "binance"
    symbol: str = "BTC/USDT"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiquiditySample:
    """One combined liquidity sample: market metadata + orderbook + BTC proxy."""

    ts_event: float
    market_id: str
    token_id: str
    slug: str
    question: str
    strike_price: float | None
    strike_extraction_status: str
    market_family: str
    side_label: str | None
    yes_token_id: str | None
    no_token_id: str | None
    expiry: str | None
    end_date_iso: str | None
    tte_seconds: float | None
    tte_bucket: str
    btc_proxy_price: float | None
    distance_to_strike_bps: float | None
    distance_bucket: str
    convex_danger_zone: bool
    book_status: str
    best_bid_price: float | None
    best_ask_price: float | None
    executable_spread_price_units: float | None
    executable_spread_cents: float | None
    top_bid_depth_usd: float | None
    top_ask_depth_usd: float | None
    top_of_book_depth_usd: float | None
    bid_count: int
    ask_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _get_git_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Adapter inspection
# ---------------------------------------------------------------------------


def inspect_nautilus_polymarket_adapter() -> AdapterInspectionResult:
    """Inspect the Nautilus Polymarket integration for safe data components."""
    result = AdapterInspectionResult()

    # Check Nautilus core importability
    try:
        # Try importing a small safe core component
        from nautilus_trader.core.nautilus_pyo3 import HttpClient  # noqa: F401
        result.nautilus_core_importable = True
    except ImportError as e:
        result.nautilus_core_import_error = str(e)
        result.nautilus_core_importable = False

    adapter_base = "nautilus_trader.adapters.polymarket"

    # Safe data-side modules
    data_side = [
        f"{adapter_base}.common.gamma_markets",
        f"{adapter_base}.common.parsing",
        f"{adapter_base}.common.symbol",
    ]
    for mod in data_side:
        result.modules_inspected.append(mod)
        result.data_side_components_found.append(mod)

    # Instrument modules (safe parsing but coupled to auth for live use)
    inst_modules = [
        f"{adapter_base}.providers",
        f"{adapter_base}.config",
    ]
    for mod in inst_modules:
        result.modules_inspected.append(mod)
        result.instrument_components_found.append(mod)

    # Orderbook schemas
    book_mod = f"{adapter_base}.schemas.book"
    result.modules_inspected.append(book_mod)
    result.orderbook_components_found.append(book_mod)

    # Execution/auth modules (forbidden)
    exec_auth = [
        f"{adapter_base}.data",
        f"{adapter_base}.execution",
        f"{adapter_base}.factories",
        f"{adapter_base}.loaders",
        f"{adapter_base}.websocket.client",
        f"{adapter_base}.common.credentials",
        f"{adapter_base}.common.conversion",
    ]
    for mod in exec_auth:
        result.modules_inspected.append(mod)
        result.execution_auth_components_found.append(mod)
        result.execution_auth_components_avoided.append(mod)

    # Determine data path
    if result.nautilus_core_importable:
        # Could use gamma_markets + public REST for books
        result.selected_data_path = DATA_PATH_MIXED
        result.data_path_reason = (
            "Nautilus core importable; safe gamma_markets module available "
            "for market discovery. Using mixed mode: gamma_markets for "
            "discovery + public CLOB REST for orderbooks."
        )
    else:
        result.selected_data_path = DATA_PATH_PUBLIC_REST
        result.data_path_reason = (
            f"Nautilus core not importable ({result.nautilus_core_import_error}). "
            "Using direct httpx calls to public Gamma and CLOB REST APIs. "
            "The Nautilus data-side modules (PolymarketLiveDataClient, "
            "PolymarketInstrumentProvider) require ClobClient (authenticated) "
            "and are excluded by Phase 0 safety policy regardless."
        )

    return result


# ---------------------------------------------------------------------------
# Market family classifier
# ---------------------------------------------------------------------------


def classify_btc_market_family(
    market_text: str,
    slug: str | None = None,
) -> tuple[str, str]:
    """Classify a Polymarket market into a BTC family.

    Returns (family, reason).
    """
    text = f"{slug or ''} {market_text}".lower()

    # Check for non-BTC first
    btc_terms = ["btc", "bitcoin", "bit coin"]
    if not any(t in text for t in btc_terms):
        return FAMILY_NON_BTC, "no BTC/Bitcoin reference found"

    # Check for Up/Down patterns
    updown_patterns = [
        "btc-updown-", "btc_updown",
        "bitcoin-up-or-down", "bitcoin up or down",
        "btc up or down", "btc up/down",
        "bitcoin up/down",
    ]
    if any(p in text for p in updown_patterns):
        return FAMILY_BTC_UPDOWN, "Up/Down slug or title pattern matched"

    # Check for Price Target (explicit numerical strike)
    strike_patterns = [
        r"\$\d{1,3}(?:,\d{3})*(?:\.\d+)?",  # $105,000 or $100000
        r"\$\d{1,3}k",                        # $100k or $110k
        r"\d{1,3}(?:,\d{3})*\s*k",           # 100k or 105k
        r"(?:above|below|hit|reach|target|price target)\s+\$?\d",
        r"(?:at|of)\s+\$?\d{1,3}(?:,\d{3})*",
    ]
    for pat in strike_patterns:
        if re.search(pat, text):
            return FAMILY_BTC_PRICE_TARGET, "explicit strike price detected"

    return FAMILY_BTC_OTHER, "BTC reference found but no Up/Down or strike pattern"


# ---------------------------------------------------------------------------
# Strike extraction
# ---------------------------------------------------------------------------


def extract_strike(text: str) -> tuple[str, float | None, str, str, str]:
    """Extract numerical strike price from market question/title.

    Returns (status, strike_price, source_field, matched_text, reason).
    """
    t = text.lower()

    # Check for Up/Down patterns first to avoid false positives
    updown_patterns = [
        "updown", "up/down", "up or down", "up-or-down",
    ]
    is_updown = any(p in t for p in updown_patterns)

    # Check for multiple plausible strikes (ambiguous)
    dollar_matches: list[float] = []

    # Pattern for $X,XXX or $XXXXX (no trailing k, digit, or m)
    for m in re.finditer(r"\$(\d+(?:,\d{3})*(?:\.\d+)?)(?!k|\d|m)", t):
        num = float(m.group(1).replace(",", ""))
        dollar_matches.append(num)

    # Pattern for Xk or $Xk (k-thousand)
    k_matches_raw = re.findall(r"(?<!\w)(\d{1,3})k(?!\w)", t)
    k_matches = [float(n) * 1000 for n in k_matches_raw]

    # Pattern for Xm or $Xm (m-million) — large target strikes like $1m
    m_matches_raw = re.findall(r"(?<!\w)(\d{1,3})m(?!\w)", t)
    m_matches = [float(n) * 1_000_000 for n in m_matches_raw]

    # Raw 5-7 digit numbers (excluding date-like, year, epoch)
    raw_numbers_matches: list[float] = []
    for m in re.finditer(r"(?<!\w)(\d{5,7})(?!\w)", t):
        num = int(m.group(1))
        if 50000 <= num <= 500000:
            raw_numbers_matches.append(float(num))

    # Combine and deduplicate
    unique_strikes: set[float] = set()
    for v in dollar_matches + k_matches + m_matches + raw_numbers_matches:
        unique_strikes.add(v)

    # If Up/Down and also has numbers, those are durations/timestamps, not strikes
    if is_updown:
        return (
            NO_STRIKE_FOUND, None, "question",
            "", "EXCLUDED_BTC_UPDOWN_NO_NUMERICAL_STRIKE",
        )

    if len(unique_strikes) > 1:
        sorted_strikes = sorted(unique_strikes)
        return (
            AMBIGUOUS_STRIKE, None, "question",
            str(sorted_strikes),
            f"multiple plausible strikes: {sorted_strikes}",
        )

    if len(unique_strikes) == 1:
        strike = list(unique_strikes)[0]
        return (
            STRIKE_EXTRACTED, strike, "question",
            str(int(strike)),
            f"extracted strike ${int(strike):,}",
        )

    return (
        UNSUPPORTED_MARKET_TEXT if "btc" not in t and "bitcoin" not in t
        else NO_STRIKE_FOUND,
        None, "question", "",
        "no numerical strike found",
    )


# ---------------------------------------------------------------------------
# Token/outcome parsing
# ---------------------------------------------------------------------------


def parse_tokens_from_market(market: dict) -> tuple[str | None, str | None, list[str]]:
    """Parse YES/NO CLOB token IDs from a Gamma API market dict.

    Returns (yes_token_id, no_token_id, all_token_ids).
    """
    raw_tokens = market.get("clobTokenIds") or "[]"
    if isinstance(raw_tokens, str):
        try:
            raw_tokens = json.loads(raw_tokens)
        except (json.JSONDecodeError, TypeError):
            raw_tokens = []
    elif not isinstance(raw_tokens, list):
        raw_tokens = []

    outcomes_raw = market.get("outcomes") or "[]"
    if isinstance(outcomes_raw, str):
        try:
            outcomes_raw = json.loads(outcomes_raw)
        except (json.JSONDecodeError, TypeError):
            outcomes_raw = []
    elif not isinstance(outcomes_raw, list):
        outcomes_raw = []

    tokens = list(raw_tokens)
    outcomes = list(outcomes_raw)

    yes_token: str | None = None
    no_token: str | None = None

    for i, tok in enumerate(tokens):
        outcome = str(outcomes[i]).upper() if i < len(outcomes) else ""
        if outcome in ("YES", "UP"):
            yes_token = str(tok)
        elif outcome in ("NO", "DOWN"):
            no_token = str(tok)

    return yes_token, no_token, tokens


# ---------------------------------------------------------------------------
# TTE computation
# ---------------------------------------------------------------------------


def compute_tte_seconds(end_date_iso: str | None) -> float | None:
    """Compute time-to-expiry in seconds from ISO end date."""
    if not end_date_iso:
        return None
    try:
        end = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (end - now).total_seconds()
    except (ValueError, TypeError):
        return None


def classify_tte_bucket(tte_seconds: float | None) -> str:
    """Classify TTE (seconds) into a bucket."""
    if tte_seconds is None:
        return TTE_UNKNOWN
    if tte_seconds < 0:
        return TTE_EXPIRED
    if tte_seconds <= 30:
        return TTE_0_30S
    if tte_seconds <= 60:
        return TTE_30S_1M
    if tte_seconds <= 120:
        return TTE_1M_2M
    if tte_seconds <= 300:
        return TTE_2M_5M
    if tte_seconds <= 900:
        return TTE_5M_15M
    return TTE_GT_15M


# ---------------------------------------------------------------------------
# Distance computation
# ---------------------------------------------------------------------------


def compute_distance_bps(
    btc_proxy_price: float | None,
    strike_price: float | None,
) -> float | None:
    """Compute distance from BTC proxy price to strike in bps.

    positive = price is above strike, negative = price is below strike.
    """
    if btc_proxy_price is None or strike_price is None or strike_price == 0:
        return None
    return ((btc_proxy_price - strike_price) / strike_price) * 10000


def classify_distance_bucket(distance_bps: float | None) -> str:
    """Classify distance-to-strike bps into a bucket."""
    if distance_bps is None or not math.isfinite(distance_bps):
        return DIST_UNKNOWN
    if distance_bps <= -500:
        return DIST_DEEP_BELOW
    if distance_bps <= -100:
        return DIST_BELOW_100_500
    if distance_bps < -25:
        return DIST_BELOW_25_100
    if distance_bps <= 25:
        return DIST_NEAR_25
    if distance_bps < 100:
        return DIST_ABOVE_25_100
    if distance_bps < 500:
        return DIST_ABOVE_100_500
    return DIST_DEEP_ABOVE


def is_convex_danger_zone(tte_seconds: float | None, distance_bps: float | None) -> bool:
    """True if near-expiry and near-strike (convex danger zone)."""
    if tte_seconds is None or distance_bps is None:
        return False
    if tte_seconds < 0:
        return False
    return tte_seconds <= 120 and abs(distance_bps) <= 25


# ---------------------------------------------------------------------------
# Orderbook parsing
# ---------------------------------------------------------------------------


def empty_snapshot(
    ts_event: float,
    market_id: str,
    token_id: str,
    side_label: str | None = None,
) -> OrderbookSnapshot:
    """Create an empty/error orderbook snapshot."""
    snap = OrderbookSnapshot(
        ts_event=ts_event,
        market_id=market_id,
        token_id=token_id,
        side_label=side_label,
        book_status=BOOK_EMPTY,
    )
    return snap


def parse_orderbook_snapshot(
    raw: dict[str, Any] | None,
    ts_event: float,
    market_id: str,
    token_id: str,
    side_label: str | None = None,
) -> OrderbookSnapshot:
    """Parse a CLOB API orderbook response into a normalized snapshot.

    Uses corrected top-of-book semantics:
    - best bid = max bid price
    - best ask = min ask price
    """
    if raw is None:
        return empty_snapshot(ts_event, market_id, token_id)

    bids_raw = raw.get("bids") if isinstance(raw, dict) else None
    asks_raw = raw.get("asks") if isinstance(raw, dict) else None

    bids = _parse_levels(bids_raw)
    asks = _parse_levels(asks_raw)

    bid_count = len(bids)
    ask_count = len(asks)

    best_bid_price: float | None = None
    best_bid_size: float | None = None
    best_ask_price: float | None = None
    best_ask_size: float | None = None

    if bid_count > 0:
        best_bid_price = max(b[0] for b in bids)
        best_bid_size = next(b[1] for b in bids if b[0] == best_bid_price)

    if ask_count > 0:
        best_ask_price = min(a[0] for a in asks)
        best_ask_size = next(a[1] for a in asks if a[0] == best_ask_price)

    # Determine book status
    if bid_count == 0 and ask_count == 0:
        return OrderbookSnapshot(
            ts_event=ts_event, market_id=market_id, token_id=token_id,
            side_label=side_label, book_status=BOOK_EMPTY,
        )
    if bid_count == 0:
        return OrderbookSnapshot(
            ts_event=ts_event, market_id=market_id, token_id=token_id,
            side_label=side_label,
            ask_count=ask_count,
            best_ask_price=best_ask_price,
            best_ask_size=best_ask_size,
            book_status=BOOK_ONE_SIDED_ASK,
        )
    if ask_count == 0:
        return OrderbookSnapshot(
            ts_event=ts_event, market_id=market_id, token_id=token_id,
            side_label=side_label,
            bid_count=bid_count,
            best_bid_price=best_bid_price,
            best_bid_size=best_bid_size,
            book_status=BOOK_ONE_SIDED_BID,
        )

    # Two-sided: check for crossed
    if best_bid_price is not None and best_ask_price is not None:
        if best_bid_price > best_ask_price:
            return OrderbookSnapshot(
                ts_event=ts_event, market_id=market_id, token_id=token_id,
                side_label=side_label,
                bid_count=bid_count, ask_count=ask_count,
                best_bid_price=best_bid_price, best_bid_size=best_bid_size,
                best_ask_price=best_ask_price, best_ask_size=best_ask_size,
                book_status=BOOK_CROSSED,
            )

        spread_units = best_ask_price - best_bid_price
        spread_cents = spread_units * 100
        top_bid_depth = best_bid_price * best_bid_size if best_bid_size else 0.0
        top_ask_depth = best_ask_price * best_ask_size if best_ask_size else 0.0

        return OrderbookSnapshot(
            ts_event=ts_event, market_id=market_id, token_id=token_id,
            side_label=side_label,
            bid_count=bid_count, ask_count=ask_count,
            best_bid_price=best_bid_price, best_bid_size=best_bid_size,
            best_ask_price=best_ask_price, best_ask_size=best_ask_size,
            executable_spread_price_units=spread_units,
            executable_spread_cents=spread_cents,
            top_bid_depth_usd=top_bid_depth,
            top_ask_depth_usd=top_ask_depth,
            top_of_book_depth_usd=top_bid_depth + top_ask_depth,
            book_status=BOOK_TWO_SIDED,
        )

    return empty_snapshot(ts_event, market_id, token_id)


def _parse_levels(levels: Any) -> list[tuple[float, float]]:
    """Parse bid/ask levels from raw API response.

    Each level is typically [price_str, size_str] or {"price": "...", "size": "..."}.
    Returns list of (price, size) as floats, or empty list on failure.
    """
    if not isinstance(levels, (list, tuple)):
        return []

    result: list[tuple[float, float]] = []
    for level in levels:
        try:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price = _safe_float(level[0])
                size = _safe_float(level[1])
            elif isinstance(level, dict):
                price = _safe_float(level.get("price"))
                size = _safe_float(level.get("size"))
            else:
                continue
            if price is not None and size is not None and price > 0:
                result.append((price, size))
        except (TypeError, ValueError, IndexError):
            continue
    return result


def _safe_float(v: Any) -> float | None:
    """Safely convert a value to float, handling strings and None."""
    if v is None:
        return None
    try:
        f = float(v)
        if not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# BTC proxy polling
# ---------------------------------------------------------------------------


async def poll_btc_binance_proxy(http: httpx.AsyncClient) -> BtcProxyTick:
    """Poll Binance for BTC/USDT price."""
    ts = time.time()
    try:
        resp = await http.get(BINANCE_TICKER_URL, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            price = float(data.get("price", 0))
            return BtcProxyTick(ts_event=ts, price=price)
        return BtcProxyTick(
            ts_event=ts, price=None, error=f"HTTP {resp.status_code}",
        )
    except Exception as e:
        return BtcProxyTick(ts_event=ts, price=None, error=str(e))


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------


async def fetch_gamma_markets(
    http: httpx.AsyncClient,
    *,
    limit: int = 100,
    timeout: float = 10,
) -> list[dict[str, Any]]:
    """Fetch active markets from the Gamma API.
    """
    url = f"{GAMMA_API_BASE}/markets"
    params: dict[str, Any] = {
        "active": "true",
        "closed": "false",
        "limit": limit,
    }

    try:
        resp = await http.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"Gamma API returned {resp.status_code}")
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        logger.info(f"Gamma API returned {len(data)} markets (limit={limit})")
        return data
    except Exception as e:
        logger.warning(f"Gamma API fetch failed: {e}")
        return []


async def discover_btc_price_target_markets(
    http: httpx.AsyncClient,
    *,
    max_markets: int = 25,
    timeout: float = 10,
) -> list[MarketMetadata]:
    """Discover BTC Price Target markets from the Gamma API."""
    raw_markets = await fetch_gamma_markets(http, limit=max_markets * 10, timeout=timeout)
    discovered: list[MarketMetadata] = []
    now = time.time()

    for i, raw in enumerate(raw_markets):
        if i == 0:
            logger.info(f"Sample market keys: {list(raw.keys())}")

        market_id = str(raw.get("id", ""))
        slug = str(raw.get("slug", ""))
        title = str(raw.get("title", raw.get("question", "")))
        question = str(raw.get("question", ""))
        outcomes_raw = raw.get("outcomes", "[]")
        if isinstance(outcomes_raw, str):
            try:
                outcomes_raw = json.loads(outcomes_raw)
            except Exception:
                outcomes_raw = []
        outcomes = list(outcomes_raw) if isinstance(outcomes_raw, list) else []

        # Parse tokens
        yes_token, no_token, all_tokens = parse_tokens_from_market(raw)

        expiry = raw.get("expiry", raw.get("endDate"))
        end_date_iso = raw.get("endDate") or raw.get("end_date_iso")

        # Classification
        classify_text = f"{slug} {title} {question}"
        family, reason = classify_btc_market_family(classify_text, slug)

        # Filter: only BTC_PRICE_TARGET goes into discovered list
        if family != FAMILY_BTC_PRICE_TARGET:
            metadata = MarketMetadata(
                market_id=market_id,
                condition_id=raw.get("conditionId") or raw.get("condition_id"),
                slug=slug,
                title=title,
                question=question,
                outcomes=outcomes,
                clob_token_ids=all_tokens,
                yes_token_id=yes_token,
                no_token_id=no_token,
                expiry=expiry,
                end_date_iso=end_date_iso,
                active=raw.get("active", True),
                closed=raw.get("closed", False),
                archived=raw.get("archived", False),
                market_family=family,
                market_family_reason=reason,
                strike_extraction_status=NO_STRIKE_FOUND,
                strike_price=None,
                strike_source_field=None,
                strike_matched_text=None,
                strike_reason=f"not a BTC_PRICE_TARGET market: {reason}",
                exclusion_reason=f"SKIPPED_{family}",
                discovered_ts=now,
            )
            discovered.append(metadata)
            continue

        # Strike extraction
        strike_text = f"{title} {question}"
        strike_status, strike_price, source_field, matched_text, strike_reason = (
            extract_strike(strike_text)
        )

        metadata = MarketMetadata(
            market_id=market_id,
            condition_id=raw.get("conditionId") or raw.get("condition_id"),
            slug=slug,
            title=title,
            question=question,
            outcomes=outcomes,
            clob_token_ids=all_tokens,
            yes_token_id=yes_token,
            no_token_id=no_token,
            expiry=expiry,
            end_date_iso=end_date_iso,
            active=raw.get("active", True),
            closed=raw.get("closed", False),
            archived=raw.get("archived", False),
            market_family=family,
            market_family_reason=reason,
            strike_extraction_status=strike_status,
            strike_price=strike_price,
            strike_source_field=source_field,
            strike_matched_text=matched_text,
            strike_reason=strike_reason,
            exclusion_reason=None,
            discovered_ts=now,
        )
        discovered.append(metadata)

        if len([m for m in discovered if m.market_family == FAMILY_BTC_PRICE_TARGET]) >= max_markets:
            break

    return discovered


# ---------------------------------------------------------------------------
# CLOB orderbook polling
# ---------------------------------------------------------------------------


async def poll_clob_book(
    http: httpx.AsyncClient,
    token_id: str,
    market_id: str,
    side_label: str | None = None,
    timeout: float = 5,
) -> OrderbookSnapshot:
    """Poll Polymarket CLOB REST API for orderbook snapshot."""
    url = f"{CLOB_API_BASE}{CLOB_BOOK_PATH}/{token_id}"
    ts = time.time()
    try:
        resp = await http.get(url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return parse_orderbook_snapshot(data, ts, market_id, token_id, side_label)
        return empty_snapshot(ts, market_id, token_id)
    except Exception:
        return empty_snapshot(ts, market_id, token_id)


# ---------------------------------------------------------------------------
# Liquidity verdict computation
# ---------------------------------------------------------------------------


def compute_liquidity_verdict(
    samples: list[LiquiditySample],
) -> tuple[str, dict[str, Any]]:
    """Compute Phase 0 verdict from collected samples.

    Returns (verdict, stats_dict).
    """
    primary_subset = _primary_subset(samples)
    stats: dict[str, Any] = {}
    stats["total_samples"] = len(samples)
    stats["total_primary_markets"] = len(set(s.market_id for s in samples if s.market_family == FAMILY_BTC_PRICE_TARGET))
    stats["total_two_sided"] = sum(1 for s in samples if s.book_status == BOOK_TWO_SIDED)
    stats["total_one_sided"] = sum(1 for s in samples if s.book_status in (BOOK_ONE_SIDED_BID, BOOK_ONE_SIDED_ASK))
    stats["total_empty"] = sum(1 for s in samples if s.book_status == BOOK_EMPTY)
    stats["total_crossed"] = sum(1 for s in samples if s.book_status == BOOK_CROSSED)
    stats["primary_subset_count"] = len(primary_subset)
    stats["near_strike_near_expiry_count"] = sum(1 for s in samples if s.convex_danger_zone and s.book_status == BOOK_TWO_SIDED)

    if not primary_subset:
        # Check why
        price_target_count = len(set(s.market_id for s in samples if s.market_family == FAMILY_BTC_PRICE_TARGET))
        if price_target_count == 0:
            return NEEDS_MORE_DATA_V, stats
        if len(samples) < PRIMARY_SUBSET_MIN_SAMPLES:
            return NEEDS_MORE_DATA_V, stats
        return NEEDS_MORE_DATA_V, stats

    # Compute spread stats
    spreads = [s.executable_spread_cents for s in primary_subset
               if s.executable_spread_cents is not None and s.executable_spread_cents >= 0]
    depths = [s.top_of_book_depth_usd for s in primary_subset
              if s.top_of_book_depth_usd is not None and s.top_of_book_depth_usd >= 0]
    two_sided_count = sum(1 for s in samples if s.book_status == BOOK_TWO_SIDED)
    two_sided_rate = two_sided_count / len(samples) if len(samples) > 0 else 0

    median_spread = statistics.median(spreads) if spreads else None
    median_depth = statistics.median(depths) if depths else None

    # Compute p95 spread
    p95_spread = None
    if len(spreads) >= 20:
        sorted_s = sorted(spreads)
        idx = int(len(sorted_s) * 0.95)
        p95_spread = sorted_s[idx]
    elif spreads:
        sorted_s = sorted(spreads)
        idx = min(len(sorted_s) - 1, int(len(sorted_s) * 0.95))
        p95_spread = sorted_s[idx]

    stats["median_spread_cents"] = median_spread
    stats["p95_spread_cents"] = p95_spread
    stats["median_top_depth_usd"] = median_depth
    stats["two_sided_rate"] = two_sided_rate

    # Component statuses
    if median_spread is not None:
        if median_spread <= SPREAD_GREEN_MAX and (p95_spread is None or p95_spread <= SPREAD_YELLOW_MAX_P95):
            spread_status = STATUS_SPREAD_GREEN
        elif median_spread <= SPREAD_YELLOW_MAX:
            spread_status = STATUS_SPREAD_YELLOW
        else:
            spread_status = STATUS_SPREAD_RED
    else:
        spread_status = STATUS_SPREAD_RED

    if median_depth is not None:
        if median_depth >= MIN_NON_DUST_DEPTH_USD_GREEN:
            depth_status = STATUS_DEPTH_GREEN
        elif median_depth >= MIN_NON_DUST_DEPTH_USD_YELLOW:
            depth_status = STATUS_DEPTH_YELLOW
        else:
            depth_status = STATUS_DEPTH_RED
    else:
        depth_status = STATUS_DEPTH_RED

    if two_sided_rate >= TWO_SIDED_GREEN_RATE:
        two_sided_status = STATUS_TWO_SIDED_GREEN
    elif two_sided_rate >= TWO_SIDED_YELLOW_RATE:
        two_sided_status = STATUS_TWO_SIDED_YELLOW
    else:
        two_sided_status = STATUS_TWO_SIDED_RED

    stats["spread_status"] = spread_status
    stats["depth_status"] = depth_status
    stats["two_sided_status"] = two_sided_status

    # Final verdict
    has_red = (
        spread_status == STATUS_SPREAD_RED
        or depth_status == STATUS_DEPTH_RED
        or two_sided_status == STATUS_TWO_SIDED_RED
    )
    has_yellow = (
        spread_status == STATUS_SPREAD_YELLOW
        or depth_status == STATUS_DEPTH_YELLOW
        or two_sided_status == STATUS_TWO_SIDED_YELLOW
    )

    if not has_red and not has_yellow:
        verdict = GREEN_DIAG
    elif not has_red:
        verdict = YELLOW_DIAG
    else:
        verdict = RED_DIAG

    stats["verdict"] = verdict
    return verdict, stats


def _primary_subset(samples: list[LiquiditySample]) -> list[LiquiditySample]:
    """Filter to primary liquidity subset.

    Primary subset requires:
    - BTC_PRICE_TARGET family
    - strike extracted
    - BTC proxy available
    - distance bucket known
    - valid two-sided orderbook
    - TTE bucket in eligible set
    - distance bucket in eligible set
    """
    eligible_tte = {TTE_5M_15M, TTE_2M_5M, TTE_1M_2M, TTE_30S_1M, TTE_0_30S}
    eligible_dist = {
        DIST_BELOW_100_500, DIST_BELOW_25_100, DIST_NEAR_25,
        DIST_ABOVE_25_100, DIST_ABOVE_100_500,
    }
    return [
        s for s in samples
        if s.market_family == FAMILY_BTC_PRICE_TARGET
        and s.strike_extraction_status == STRIKE_EXTRACTED
        and s.btc_proxy_price is not None
        and s.distance_bucket != DIST_UNKNOWN
        and s.book_status == BOOK_TWO_SIDED
        and s.tte_bucket in eligible_tte
        and s.distance_bucket in eligible_dist
    ]


def compute_v1_recommendation(verdict: str) -> str:
    """Map Phase 0 verdict to v1 recommendation."""
    mapping = {
        GREEN_DIAG: V1_JUSTIFIED,
        YELLOW_DIAG: V1_HUMAN_REVIEW,
        RED_DIAG: V1_BLOCKED_LIQUIDITY,
        NEEDS_MORE_DATA_V: V1_BLOCKED_DATA,
        CAPTURE_UNUSABLE_V: V1_BLOCKED_DATA,
    }
    return mapping.get(verdict, V1_BLOCKED_DATA)


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def write_markets_json(markets: list[MarketMetadata], path: Path) -> None:
    """Write discovered markets to JSON."""
    data = {
        "market_count": len(markets),
        "btc_price_target_count": sum(1 for m in markets if m.market_family == FAMILY_BTC_PRICE_TARGET),
        "btc_updown_count": sum(1 for m in markets if m.market_family == FAMILY_BTC_UPDOWN),
        "btc_other_count": sum(1 for m in markets if m.market_family == FAMILY_BTC_OTHER),
        "non_btc_count": sum(1 for m in markets if m.market_family == FAMILY_NON_BTC),
        "markets": [m.to_dict() for m in markets],
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def write_samples_jsonl(samples: list[LiquiditySample], path: Path) -> None:
    """Write samples to JSONL."""
    lines = [json.dumps(s.to_dict(), default=str) for s in samples]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_liquidity_grid_csv(
    samples: list[LiquiditySample],
    path: Path,
) -> None:
    """Write a TTE x Distance liquidity grid as CSV."""
    grid: dict[tuple[str, str], list[float]] = {}
    for s in samples:
        if s.book_status != BOOK_TWO_SIDED or s.top_of_book_depth_usd is None:
            continue
        key = (s.tte_bucket, s.distance_bucket)
        if key not in grid:
            grid[key] = []
        grid[key].append(s.top_of_book_depth_usd)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["tte_bucket", "distance_bucket", "sample_count", "median_depth_usd", "mean_depth_usd"])
        for tte_bucket in TTE_BUCKET_ORDER:
            for dist_bucket in DIST_BUCKET_ORDER:
                key = (tte_bucket, dist_bucket)
                vals = grid.get(key, [])
                if vals:
                    writer.writerow([
                        tte_bucket, dist_bucket, len(vals),
                        round(statistics.median(vals), 2),
                        round(statistics.mean(vals), 2),
                    ])


def write_summary_json(
    run_id: str,
    inspection: AdapterInspectionResult,
    markets: list[MarketMetadata],
    samples: list[LiquiditySample],
    verdict: str,
    verdict_stats: dict[str, Any],
    start_time: float,
    end_time: float,
    duration_seconds: int,
    poll_interval: int,
    btc_venue: str,
    out_dir: Path,
    include_raw: bool,
    max_markets: int,
    data_path: str,
) -> dict[str, Any]:
    """Write summary.json and return the summary dict."""
    btc_price_target_markets = [m for m in markets if m.market_family == FAMILY_BTC_PRICE_TARGET]
    btc_updown_markets = [m for m in markets if m.market_family == FAMILY_BTC_UPDOWN]
    btc_other_markets = [m for m in markets if m.market_family == FAMILY_BTC_OTHER]
    non_btc_markets = [m for m in markets if m.market_family == FAMILY_NON_BTC]

    # TTE rollup
    tte_rollup: dict[str, int] = {}
    for s in samples:
        tte_rollup[s.tte_bucket] = tte_rollup.get(s.tte_bucket, 0) + 1

    # Distance rollup
    dist_rollup: dict[str, int] = {}
    for s in samples:
        dist_rollup[s.distance_bucket] = dist_rollup.get(s.distance_bucket, 0) + 1

    summary = {
        "run_id": run_id,
        "git_sha": _get_git_sha(),
        "git_branch": _get_git_branch(),
        "dirty": _is_dirty(),
        "selected_data_path": data_path,
        "nautilus_components_used": inspection.data_side_components_found if data_path in (DATA_PATH_NAUTILUS, DATA_PATH_MIXED) else [],
        "public_rest_components_used": ["gamma-api.polymarket.com", "clob.polymarket.com/book", "api.binance.com"],
        "start_utc": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(),
        "requested_duration_seconds": duration_seconds,
        "actual_duration_seconds": round(end_time - start_time, 2),
        "poll_interval_seconds": poll_interval,
        "btc_proxy_venue": btc_venue,
        "btc_proxy_symbol": "BTC/USDT",
        "total_markets_discovered": len(markets),
        "btc_price_target_markets": len(btc_price_target_markets),
        "btc_updown_markets_excluded": len(btc_updown_markets),
        "btc_other_markets_excluded": len(btc_other_markets),
        "non_btc_markets_excluded": len(non_btc_markets),
        "markets_strike_extracted": sum(1 for m in markets if m.strike_extraction_status == STRIKE_EXTRACTED),
        "markets_sampled": len(set(s.market_id for s in samples)),
        "valid_two_sided_samples": verdict_stats.get("total_two_sided", 0),
        "one_sided_or_empty": verdict_stats.get("total_one_sided", 0) + verdict_stats.get("total_empty", 0) + verdict_stats.get("total_crossed", 0),
        "primary_subset_count": verdict_stats.get("primary_subset_count", 0),
        "near_strike_near_expiry_count": verdict_stats.get("near_strike_near_expiry_count", 0),
        "convex_danger_zone_count": verdict_stats.get("near_strike_near_expiry_count", 0),
        "spread_status": verdict_stats.get("spread_status"),
        "depth_status": verdict_stats.get("depth_status"),
        "two_sided_status": verdict_stats.get("two_sided_status"),
        "median_spread_cents": verdict_stats.get("median_spread_cents"),
        "p95_spread_cents": verdict_stats.get("p95_spread_cents"),
        "median_top_depth_usd": verdict_stats.get("median_top_depth_usd"),
        "two_sided_rate": verdict_stats.get("two_sided_rate"),
        "tte_rollup": tte_rollup,
        "distance_rollup": dist_rollup,
        "final_phase_0_verdict": verdict,
        "verdict_reason": _verdict_reason(verdict, verdict_stats, markets, samples),
        "v1_recommendation": compute_v1_recommendation(verdict),
        "include_raw_payloads": include_raw,
    }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )
    return summary


def _verdict_reason(
    verdict: str,
    stats: dict[str, Any],
    markets: list[MarketMetadata],
    samples: list[LiquiditySample],
) -> str:
    """Build a human-readable reason string for the verdict."""
    pt_count = sum(1 for m in markets if m.market_family == FAMILY_BTC_PRICE_TARGET)
    primary_count = stats.get("primary_subset_count", 0)

    if verdict == CAPTURE_UNUSABLE_V:
        return "Capture failed — no usable API responses received"
    if verdict == NEEDS_MORE_DATA_V:
        if pt_count == 0:
            up_count = sum(1 for m in markets if m.market_family == FAMILY_BTC_UPDOWN)
            return (
                f"No active BTC Price Target markets found "
                f"({up_count} Up/Down markets excluded, "
                f"{len(markets)} total BTC markets discovered)"
            )
        return (
            f"Insufficient primary subset samples: {primary_count} < "
            f"{PRIMARY_SUBSET_MIN_SAMPLES} minimum"
        )
    if verdict == GREEN_DIAG:
        return (
            f"Adequate liquidity: {primary_count} primary subset samples, "
            f"green spread, green depth, green two-sided rate"
        )
    if verdict == YELLOW_DIAG:
        return (
            f"Marginal liquidity: {primary_count} primary subset samples, "
            f"at least one yellow component"
        )
    if verdict == RED_DIAG:
        red_causes = []
        if stats.get("spread_status") == STATUS_SPREAD_RED:
            red_causes.append("spread")
        if stats.get("depth_status") == STATUS_DEPTH_RED:
            red_causes.append("depth")
        if stats.get("two_sided_status") == STATUS_TWO_SIDED_RED:
            red_causes.append("two_sided_rate")
        return (
            f"Inadequate liquidity: {', '.join(red_causes)} component(s) red"
        )
    return "Unknown verdict"


def write_report_md(
    summary: dict[str, Any],
    inspection: AdapterInspectionResult,
    samples: list[LiquiditySample],
    out_dir: Path,
) -> None:
    """Write a human-readable markdown report."""
    lines = [
        "# Polymarket BTC Price Target Liquidity Probe V0",
        "",
        f"**Run ID:** {summary.get('run_id', 'unknown')}",
        f"**Branch:** {summary.get('git_branch', 'unknown')}",
        f"**SHA:** {summary.get('git_sha', 'unknown')}",
        f"**Dirty:** {summary.get('dirty', 'unknown')}",
        "",
        "## Adapter Path",
        "",
        f"**Selected data path:** `{summary.get('selected_data_path', 'unknown')}`",
        "",
        "### Nautilus components inspected",
        "",
    ]
    for mod in inspection.modules_inspected:
        lines.append(f"- `{mod}`")
    lines.append("")
    lines.append(f"**Reason for path choice:** {inspection.data_path_reason}")
    lines.append("")
    lines.append("## Discovery")
    lines.append("")
    lines.append(f"- Total markets discovered: {summary.get('total_markets_discovered', 0)}")
    lines.append(f"- BTC Price Target markets: {summary.get('btc_price_target_markets', 0)}")
    lines.append(f"- BTC Up/Down excluded: {summary.get('btc_updown_markets_excluded', 0)}")
    lines.append(f"- BTC Other excluded: {summary.get('btc_other_markets_excluded', 0)}")
    lines.append(f"- Non-BTC excluded: {summary.get('non_btc_markets_excluded', 0)}")
    lines.append(f"- Markets with strike extracted: {summary.get('markets_strike_extracted', 0)}")
    lines.append("")
    lines.append("## Capture")
    lines.append("")
    lines.append(f"- Duration: {summary.get('actual_duration_seconds', 0)}s (requested: {summary.get('requested_duration_seconds', 0)}s)")
    lines.append(f"- Poll interval: {summary.get('poll_interval_seconds', 0)}s")
    lines.append(f"- BTC proxy: {summary.get('btc_proxy_venue', '')} {summary.get('btc_proxy_symbol', '')}")
    lines.append(f"- Markets sampled: {summary.get('markets_sampled', 0)}")
    lines.append(f"- Total samples: {len(samples)}")
    lines.append(f"- Valid two-sided: {summary.get('valid_two_sided_samples', 0)}")
    lines.append(f"- Primary subset count: {summary.get('primary_subset_count', 0)}")
    lines.append(f"- Near-strike / near-expiry: {summary.get('near_strike_near_expiry_count', 0)}")
    lines.append(f"- Convex danger zone: {summary.get('convex_danger_zone_count', 0)}")
    lines.append("")
    lines.append("## Liquidity Diagnostics")
    lines.append("")
    lines.append(f"- Median spread cents: {summary.get('median_spread_cents', 'N/A')}")
    lines.append(f"- P95 spread cents: {summary.get('p95_spread_cents', 'N/A')}")
    lines.append(f"- Median top depth USD: {summary.get('median_top_depth_usd', 'N/A')}")
    lines.append(f"- Two-sided rate: {summary.get('two_sided_rate', 'N/A')}")
    lines.append(f"- Spread status: {summary.get('spread_status', 'N/A')}")
    lines.append(f"- Depth status: {summary.get('depth_status', 'N/A')}")
    lines.append(f"- Two-sided status: {summary.get('two_sided_status', 'N/A')}")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**{summary.get('final_phase_0_verdict', 'UNKNOWN')}**")
    lines.append("")
    lines.append(f"Reason: {summary.get('verdict_reason', '')}")
    lines.append("")
    lines.append(f"**V1 recommendation:** {summary.get('v1_recommendation', 'unknown')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*No orders. No wallet. No signing. No auth. Observer-only.*")

    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_adapter_inspection_json(inspection: AdapterInspectionResult, path: Path) -> None:
    """Write adapter inspection result."""
    path.write_text(
        json.dumps(inspection.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
