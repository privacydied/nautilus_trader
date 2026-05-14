"""Discover BTC UpDown markets across multiple durations from Polymarket Gamma API.

Extends live_market_discovery to support 1h and 4h durations, not just 15m.
No orders. No keys. No execution. No on-chain calls.
"""
from __future__ import annotations

import json
import re
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
# Pattern: btc-updown-15m-1778794200, btc-updown-1h-1778794200, btc-updown-4h-1778794200
_SLUG_DURATION_RE = re.compile(r"updown-(\d+[mh])-(\d{10})", re.IGNORECASE)

# Tolerance for duration classification from start/end timestamps (seconds)
DURATION_TOLERANCE_S = 60  # 1-minute tolerance


@dataclass(frozen=True)
class DurationMarketInfo:
    """Market info with duration classification."""
    market: UpDownMarketInfo
    duration_label: str
    duration_seconds: int | None
    classification_source: str  # "slug", "start_end", "title", "unknown"


def classify_duration_from_slug(slug: str) -> tuple[str, int | None]:
    """Classify duration from slug pattern like 'btc-updown-15m-1778794200'.

    Returns (duration_label, duration_seconds).
    """
    m = _SLUG_DURATION_RE.search(slug.lower())
    if not m:
        return DURATION_UNKNOWN, None

    dur_str = m.group(1)
    # Parse duration string like "15m", "1h", "4h"
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

    # Check if it's close to any known duration
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

    Looks for patterns like '15 minute', '15 minutes', '1 hour', '4 hours', '1hr', '4hr'.
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

    return DURATION_UNKNOWN, None


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
        )

    # 2. Try start/end timestamps
    label, dur_s = classify_duration_from_start_end(market.start_ns, market.end_ns)
    if label != DURATION_UNKNOWN:
        return DurationMarketInfo(
            market=market,
            duration_label=label,
            duration_seconds=dur_s,
            classification_source="start_end",
        )

    # 3. Try title/question text
    label, dur_s = classify_duration_from_title(market.question)
    if label != DURATION_UNKNOWN:
        return DurationMarketInfo(
            market=market,
            duration_label=label,
            duration_seconds=dur_s,
            classification_source="title",
        )

    # 4. Unknown
    return DurationMarketInfo(
        market=market,
        duration_label=DURATION_UNKNOWN,
        duration_seconds=None,
        classification_source="unknown",
    )


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

    # Fetch markets from Gamma API
    markets: list[dict] = []
    try:
        params: dict[str, str] = {
            "limit": "200",
            "order": "startDate",
            "ascending": "false",
        }
        if include_active and not include_closed:
            params["active"] = "true"
            params["closed"] = "false"

        raw_markets = _gamma_get("markets", params)
        markets = raw_markets if isinstance(raw_markets, list) else []
    except Exception:
        # If API fails, return empty
        return []

    results: list[DurationMarketInfo] = []

    for m in markets:
        slug = str(m.get("slug", "")).lower()
        question = str(m.get("question", "")).lower()

        # Must be an UpDown market
        if not ("updown" in slug or "up/down" in question or "up or down" in question):
            continue

        # Filter by asset
        if asset_filter:
            if not (asset_filter in slug or asset_filter in question):
                # Also check full names
                asset_map = {"btc": "bitcoin", "eth": "ethereum"}
                full_name = asset_map.get(asset_filter.lower(), "")
                if not (full_name and full_name in question):
                    continue

        # Build UpDownMarketInfo
        yes, no = _parse_tokens(m)
        info = UpDownMarketInfo(
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
        # Add only results not already present
        seen_slugs = {r.market.slug for r in all_results}
        for r in closed_results:
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


def poll_quote_for_market(
    market_info: UpDownMarketInfo,
) -> dict | None:
    """Poll a single quote snapshot from Polymarket CLOB for a market.

    Returns dict with best_bid, best_ask, mid, spread_bps, depth, ts_event_ns,
    or None on failure.
    """
    import time

    token_id = market_info.yes_token_id
    if not token_id:
        return None

    url = f"https://clob.polymarket.com/book?token_id={token_id}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "NautilusTrader/DurationSpreadProbe"}
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