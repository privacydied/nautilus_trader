"""Discover BTC UpDown markets across multiple durations from Polymarket Gamma API.

Extends live_market_discovery to support 5m, 15m, 1h, and 4h durations.
Supports both slug families: btc-updown-* and bitcoin-up-or-down-*.
Classifies reference sources: Binance vs Chainlink.
No orders. No keys. No execution. No on-chain calls.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from .live_market_discovery import UpDownMarketInfo, _ts, _parse_tokens, _gamma_get

# Duration classification labels
DURATION_5M = "5m"
DURATION_15M = "15m"
DURATION_1H = "1h"
DURATION_4H = "4h"
DURATION_UNKNOWN = "unknown"

ALL_DURATIONS = (DURATION_5M, DURATION_15M, DURATION_1H, DURATION_4H, DURATION_UNKNOWN)

# Duration in seconds for classification
DURATION_SECONDS_MAP = {
    DURATION_5M: 300,
    DURATION_15M: 900,
    DURATION_1H: 3600,
    DURATION_4H: 14400,
}

# Regex patterns for slug-based duration detection
# Pattern 1: btc-updown-15m-1778794200, btc-updown-1h-1778794200, btc-updown-4h-1778794200
_SLUG_DURATION_RE = re.compile(r"updown-(\d+[mh])-(\d{10})", re.IGNORECASE)

# Pattern 2: bitcoin-up-or-down-may-13-2026-11pm-et (hourly family)
_SLUG_BITCOIN_UP_OR_DOWN_RE = re.compile(r"bitcoin-up-or-down-", re.IGNORECASE)

# Tolerance for duration classification from start/end timestamps (seconds)
DURATION_TOLERANCE_S = 60  # 1-minute tolerance

# Reference source classification
REF_SOURCE_BINANCE = "BINANCE_BTCUSDT"
REF_SOURCE_CHAINLINK = "CHAINLINK_BTCUSD"
REF_SOURCE_UNKNOWN = "UNKNOWN"

# Duration-level verdict labels (superseded taxonomy)
DV_EXISTS_NEEDS_QUOTE = "DURATION_MARKET_EXISTS_NEEDS_QUOTE_OBSERVATION"
DV_ACTIVE_NO_USABLE = "DURATION_ACTIVE_MARKET_NO_USABLE_BOOK"
DV_ACTIVE_HAS_ACTIONABLE = "DURATION_ACTIVE_MARKET_HAS_ACTIONABLE_TWO_SIDED_BOOK"
DV_NO_ACTIVE_NOW = "DURATION_NO_ACTIVE_MARKET_NOW"
DV_DISCOVERY_FAILED = "DURATION_DISCOVERY_FAILED"
DV_UNSUPPORTED_REF = "DURATION_UNSUPPORTED_REFERENCE_SOURCE"

# Overall verdict labels (superseded taxonomy)
PV_SUPERSEDES_PRIOR = "DURATION_PROBE_SUPERSEDES_PRIOR_NONEXISTENCE_FINDING"
PV_NEEDS_MORE_DATA = "DURATION_PROBE_NEEDS_MORE_LIVE_DATA"
PV_NO_USABLE = "DURATION_PROBE_NO_USABLE_BOOKS_IN_OBSERVED_ACTIVE_MARKETS"
PV_FOUND_ACTIONABLE = "DURATION_PROBE_FOUND_ACTIONABLE_BOOKS_REQUIRES_PHASE1_BACKTEST"


@dataclass(frozen=True)
class DurationMarketInfo:
    """Market info with duration and reference source classification."""
    market: UpDownMarketInfo
    duration_label: str
    duration_seconds: int | None
    classification_source: str  # "slug", "start_end", "title", "known_slug", "unknown"
    resolution_source_kind: str  # BINANCE_BTCUSDT, CHAINLINK_BTCUSD, UNKNOWN
    is_known_slug: bool = False


def classify_duration_from_slug(slug: str) -> tuple[str, int | None]:
    """Classify duration from slug pattern.

    Supports both:
    - btc-updown-15m-1778794200 (standard 5m/15m/1h/4h)
    - bitcoin-up-or-down-may-13-2026-11pm-et (hourly family)

    Returns (duration_label, duration_seconds).
    """
    slug_lower = slug.lower()

    # Pattern 1: btc-updown-{dur}-{timestamp}
    m = _SLUG_DURATION_RE.search(slug_lower)
    if m:
        dur_str = m.group(1)
        if dur_str.endswith("m"):
            try:
                minutes = int(dur_str[:-1])
                return f"{minutes}m", minutes * 60
            except ValueError:
                return DURATION_UNKNOWN, None
        elif dur_str.endswith("h"):
            try:
                hours = int(dur_str[:-1])
                return f"{hours}h", hours * 3600
            except ValueError:
                return DURATION_UNKNOWN, None

    # Pattern 2: bitcoin-up-or-down-* (hourly family)
    if _SLUG_BITCOIN_UP_OR_DOWN_RE.search(slug_lower):
        return DURATION_1H, 3600

    return DURATION_UNKNOWN, None


def classify_duration_from_start_end(
    start_ns: int | None,
    end_ns: int | None,
) -> tuple[str, int | None]:
    """Classify duration from start and end timestamps.

    Returns (duration_label, duration_seconds).
    Uses 1-minute tolerance for classification.
    """
    if start_ns is None or end_ns is None or start_ns <= 0 or end_ns <= 0:
        return DURATION_UNKNOWN, None

    delta_s = (end_ns - start_ns) / 1_000_000_000

    for label, expected_s in DURATION_SECONDS_MAP.items():
        if label == DURATION_UNKNOWN:
            continue
        if abs(delta_s - expected_s) <= DURATION_TOLERANCE_S:
            return label, int(delta_s)

    if abs(delta_s - 300) <= DURATION_TOLERANCE_S:
        return DURATION_5M, int(delta_s)
    if abs(delta_s - 900) <= DURATION_TOLERANCE_S:
        return DURATION_15M, int(delta_s)
    if abs(delta_s - 3600) <= DURATION_TOLERANCE_S:
        return DURATION_1H, int(delta_s)
    if abs(delta_s - 14400) <= DURATION_TOLERANCE_S:
        return DURATION_4H, int(delta_s)

    return DURATION_UNKNOWN, None


def classify_duration_from_title(question: str) -> tuple[str, int | None]:
    """Classify duration from question/title text.

    Looks for patterns like:
    - '15 minute', '15 minutes', '15 min'
    - '1 hour', '1 hours', '1 hr', '1hr'
    - 'Hourly' (1h)
    - '8:00PM-12:00AM', '8PM-12AM' style (4h block)

    Returns (duration_label, duration_seconds).
    """
    q = question.lower()

    # Check for "X minute" or "X minutes" or "X min" patterns
    m_min = re.search(r"(\d+)\s*(?:minutes?|mins?)\b", q)
    if m_min:
        try:
            minutes = int(m_min.group(1))
            if minutes == 15:
                return DURATION_15M, 900
            if minutes == 5:
                return DURATION_5M, 300
            return f"{minutes}m", minutes * 60
        except ValueError:
            pass

    # Check for "X hour" or "X hours" or "X hr" or "X hrs" patterns
    m_hr = re.search(r"(\d+)\s*(?:hours?|hrs?)\b", q)
    if m_hr:
        try:
            hours = int(m_hr.group(1))
            if hours == 1:
                return DURATION_1H, 3600
            if hours == 4:
                return DURATION_4H, 14400
            return f"{hours}h", hours * 3600
        except ValueError:
            pass

    # Check for "Hourly" (e.g. "BTC Up or Down Hourly")
    if "hourly" in q:
        return DURATION_1H, 3600

    # Check for time-block patterns like "8:00PM-12:00AM" or "8PM-12AM"
    # These typically indicate 4-hour blocks
    m_block = re.search(r"(\d+):?\d*(?:PM|AM)\s*-\s*(\d+):?\d*(?:PM|AM)", q, re.IGNORECASE)
    if m_block:
        # If it mentions a 4h block, classify as 4h
        # Also check for "4h" in the title
        if "4h" in q or "4-hours" in q.replace(" ", ""):
            return DURATION_4H, 14400

    return DURATION_UNKNOWN, None


def classify_reference_source(market: UpDownMarketInfo) -> str:
    """Classify the resolution source of a BTC UpDown market.

    Detects:
    - Binance BTC/USDT (common for 5m/15m/1h)
    - Chainlink BTC/USD (common for 4h)
    - Unknown

    Args:
        market: The market info with resolution_source field.

    Returns:
        One of BINANCE_BTCUSDT, CHAINLINK_BTCUSD, UNKNOWN.
    """
    question = (market.question or "").lower()
    resolution_source = (market.resolution_source or "").lower()
    slug = (market.slug or "").lower()

    # Check resolution_source field first
    combined = f"{resolution_source} {question}"

    if "chainlink" in combined:
        return REF_SOURCE_CHAINLINK
    if "binance" in combined:
        return REF_SOURCE_BINANCE

    # Heuristic: 5m/15m markets use Binance (these are the standard BTC UpDown products)
    if "btc-updown-5m" in slug or "btc-updown-15m" in slug or "btc-updown-1h" in slug:
        return REF_SOURCE_BINANCE

    # Heuristic: 4h markets tend to use Chainlink
    if "btc-updown-4h" in slug:
        return REF_SOURCE_CHAINLINK

    return REF_SOURCE_UNKNOWN


def classify_duration(market: UpDownMarketInfo) -> DurationMarketInfo:
    """Classify market duration using multiple sources with priority.

    Priority: slug pattern > start/end delta > title text > unknown.
    """
    # 1. Try slug classification
    label, dur_s = classify_duration_from_slug(market.slug)
    if label != DURATION_UNKNOWN:
        return DurationMarketInfo(
            market=market,
            duration_label=label,
            duration_seconds=dur_s,
            classification_source="slug",
            resolution_source_kind=classify_reference_source(market),
        )

    # 2. Try start/end timestamps
    label, dur_s = classify_duration_from_start_end(market.start_ns, market.end_ns)
    if label != DURATION_UNKNOWN:
        return DurationMarketInfo(
            market=market,
            duration_label=label,
            duration_seconds=dur_s,
            classification_source="start_end",
            resolution_source_kind=classify_reference_source(market),
        )

    # 3. Try title/question text
    label, dur_s = classify_duration_from_title(market.question)
    if label != DURATION_UNKNOWN:
        return DurationMarketInfo(
            market=market,
            duration_label=label,
            duration_seconds=dur_s,
            classification_source="title",
            resolution_source_kind=classify_reference_source(market),
        )

    # 4. Unknown
    return DurationMarketInfo(
        market=market,
        duration_label=DURATION_UNKNOWN,
        duration_seconds=None,
        classification_source="unknown",
        resolution_source_kind=classify_reference_source(market),
    )


def _build_updown_info(m: dict) -> UpDownMarketInfo:
    """Build an UpDownMarketInfo from a Gamma API market dict."""
    yes, no = _parse_tokens(m)
    return UpDownMarketInfo(
        slug=m.get("slug", ""),
        question=m.get("question", ""),
        active=bool(m.get("active", False)),
        closed=bool(m.get("closed", True)),
        condition_id=m.get("conditionId", ""),
        yes_token_id=yes,
        no_token_id=no,
        start_ns=_ts(m.get("startDate") or m.get("eventStartTime")),
        end_ns=_ts(m.get("endDate")),
        series_slug=m.get("seriesSlug"),
        resolution_source=m.get("resolutionSource"),
        price_to_beat=None,
        price_to_beat_source=None,
    )


def _updown_filter(slug: str, question: str, asset_filter: str) -> bool:
    """Check if a market is an UpDown market for the given asset.

    Accepts:
    - btc-updown-* slugs
    - bitcoin-up-or-down-* slugs
    - "up/down" or "up or down" in questions
    """
    slug_lower = slug.lower()
    question_lower = question.lower()

    # Must be an UpDown market - check multiple patterns
    is_updown = (
        "updown" in slug_lower
        or "up-or-down" in slug_lower
        or "up/down" in question_lower
        or "up or down" in question_lower
        or "up ordown" in slug_lower  # edge case
    )
    if not is_updown:
        return False

    # Filter by asset
    if asset_filter:
        asset_map = {"btc": "bitcoin", "eth": "ethereum"}
        full_name = asset_map.get(asset_filter.lower(), "")
        if asset_filter in slug_lower or asset_filter in question_lower:
            return True
        if full_name and full_name in question_lower:
            return True
        return False

    return True


def discover_updown_markets(
    asset_filter: str = "btc",
    durations: tuple[str, ...] | None = None,
    include_active: bool = True,
    include_closed: bool = False,
    max_markets: int = 20,
) -> list[DurationMarketInfo]:
    """Discover UpDown markets filtered by asset and duration.

    Args:
        asset_filter: Asset to filter for (e.g. "btc", "eth"). Empty string for all.
        durations: Duration labels to include. None means all durations.
        include_active: Include active markets.
        include_closed: Include closed/resolved markets.
        max_markets: Maximum number of markets to return.

    Returns:
        List of DurationMarketInfo with duration classification.
    """
    if durations is None:
        durations = ALL_DURATIONS

    # Build API params
    params: dict[str, str] = {
        "limit": "200",
        "order": "startDate",
        "ascending": "false",
    }

    # Set active/closed filters correctly
    if include_active and not include_closed:
        params["active"] = "true"
        params["closed"] = "false"
    elif include_closed and not include_active:
        params["active"] = "false"
        params["closed"] = "true"
    elif include_active and include_closed:
        # Both: no filtering - API returns all
        pass
    # else: neither - no filtering

    # Fetch markets from Gamma API
    markets: list[dict] = []
    try:
        raw_markets = _gamma_get("markets", params)
        markets = raw_markets if isinstance(raw_markets, list) else []
    except Exception:
        # If API fails, try events endpoint as fallback
        try:
            raw_events = _gamma_get("events", {
                "limit": "100",
                "order": "startDate",
                "ascending": "false",
                "tag": "bitcoin",
            })
            for ev in (raw_events if isinstance(raw_events, list) else []):
                ev_markets = ev.get("markets", [])
                if isinstance(ev_markets, list):
                    for m in ev_markets:
                        if "slug" not in m:
                            m["slug"] = m.get("slug", ev.get("slug", ""))
                        markets.append(m)
        except Exception:
            return []

    results: list[DurationMarketInfo] = []

    for m in markets:
        slug = str(m.get("slug", ""))
        question = str(m.get("question", ""))

        # Check UpDown filter
        if not _updown_filter(slug, question, asset_filter):
            continue

        # Build UpDownMarketInfo
        info = _build_updown_info(m)

        # Classify duration
        dur_info = classify_duration(info)

        # Filter by requested durations
        if dur_info.duration_label not in durations:
            continue

        results.append(dur_info)

        if len(results) >= max_markets:
            break

    return results


def discover_duration_markets(
    durations: tuple[str, ...] | None = None,
    max_markets: int = 4,
) -> list[DurationMarketInfo]:
    """Convenience function to discover BTC UpDown markets by duration.

    Args:
        durations: Duration labels to search for (e.g. ("1h", "4h")).
        max_markets: Maximum markets to return.

    Returns:
        List of DurationMarketInfo sorted by duration then start time.
    """
    if durations is None:
        durations = (DURATION_1H, DURATION_4H)

    all_results = discover_updown_markets(
        asset_filter="btc",
        durations=durations,
        include_active=True,
        include_closed=False,
        max_markets=max_markets * 2,  # over-fetch then trim
    )

    # Also try closed/resolved markets if not enough active
    if len(all_results) < max_markets:
        closed_results = discover_updown_markets(
            asset_filter="btc",
            durations=durations,
            include_active=False,
            include_closed=True,
            max_markets=max_markets * 2,
        )
        seen_slugs = {r.market.slug for r in all_results}
        for r in closed_results:
            if r.market.slug not in seen_slugs:
                all_results.append(r)
                seen_slugs.add(r.market.slug)

    # Also try both active+closed to get everything
    if len(all_results) < max_markets:
        both_results = discover_updown_markets(
            asset_filter="btc",
            durations=durations,
            include_active=True,
            include_closed=True,
            max_markets=max_markets * 2,
        )
        seen_slugs = {r.market.slug for r in all_results}
        for r in both_results:
            if r.market.slug not in seen_slugs:
                all_results.append(r)
                seen_slugs.add(r.market.slug)

    # Sort: active first, then by duration label, then by start time descending
    duration_order = {DURATION_5M: 0, DURATION_15M: 1, DURATION_1H: 2, DURATION_4H: 3, DURATION_UNKNOWN: 4}
    all_results.sort(key=lambda r: (
        not r.market.active,  # active first
        duration_order.get(r.duration_label, 99),
        -(r.market.start_ns or 0),  # most recent first
    ))

    return all_results[:max_markets]


def validate_known_slug(slug: str) -> DurationMarketInfo | None:
    """Validate a user-supplied slug by fetching it from Polymarket API.

    Attempts both the events and markets endpoints.
    Classifies duration and reference source.
    Returns None if the slug cannot be validated.

    Args:
        slug: The slug to validate (e.g. "bitcoin-up-or-down-may-13-2026-11pm-et").

    Returns:
        DurationMarketInfo with classification, or None if not found.
    """
    # Try events endpoint first (for /event/ URLs)
    try:
        events = _gamma_get("events", {"slug": slug})
        if events and isinstance(events, list) and len(events) > 0:
            ev = events[0]
            ev_markets = ev.get("markets", [])
            # Get the first market from the event
            if isinstance(ev_markets, list) and ev_markets:
                m = ev_markets[0]
                # Merge event-level fields
                m["slug"] = slug
                m["question"] = ev.get("title", m.get("question", ""))
                info = _build_updown_info(m)
                dur = classify_duration(info)
                return DurationMarketInfo(
                    market=info,
                    duration_label=dur.duration_label,
                    duration_seconds=dur.duration_seconds,
                    classification_source="known_slug",
                    resolution_source_kind=classify_reference_source(info),
                    is_known_slug=True,
                )
    except Exception:
        pass

    # Try markets endpoint
    try:
        markets = _gamma_get("markets", {"slug": slug})
        if markets and isinstance(markets, list) and len(markets) > 0:
            m = markets[0]
            info = _build_updown_info(m)
            dur = classify_duration(info)
            return DurationMarketInfo(
                market=info,
                duration_label=dur.duration_label,
                duration_seconds=dur.duration_seconds,
                classification_source="known_slug",
                resolution_source_kind=classify_reference_source(info),
                is_known_slug=True,
            )
    except Exception:
        pass

    return None


def poll_quote_for_market(
    market_info: UpDownMarketInfo,
) -> dict | None:
    """Poll a single quote snapshot from Polymarket CLOB for a market.

    Returns dict with best_bid, best_ask, mid, spread_bps, depth, ts_event_ns,
    or None on failure.
    """
    token_id = market_info.yes_token_id
    if not token_id:
        return None

    url = f"https://clob.polymarket.com/book?token_id={token_id}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "NautilusTrader/DurationDiscoveryFix"}
    )
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception:
        return None

    bids = data.get("bids", [])
    asks = data.get("asks", [])
    best_bid = float(bids[0]["price"]) if bids else None
    best_ask = float(asks[0]["price"]) if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread = ((best_ask - best_bid) / mid * 10_000) if (best_bid is not None and best_ask is not None and mid) else None
    depth_bid = sum(float(b.get("size", 0)) for b in bids[:5])
    depth_ask = sum(float(a.get("size", 0)) for a in asks[:5])

    return {
        "market_slug": market_info.slug,
        "token_id": token_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread,
        "depth_bid": depth_bid,
        "depth_ask": depth_ask,
        "ts_event_ns": int(time.time() * 1_000_000_000),
    }
