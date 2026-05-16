"""
Market filter for Polymarket complement arb strategy.

Filters active binary markets to find complementary YES+NO pairs from the
same condition_id. Logs skip reasons for every rejected market.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ComplementArbConfig
from .models import ComplementMarket, MarketSkipReason


def extract_complement_markets(
    gamma_markets: list[dict[str, Any]],
    config: ComplementArbConfig,
) -> tuple[list[ComplementMarket], list[MarketSkipReason]]:
    """
    Extract complementary YES+NO binary markets from raw Gamma API market data.

    Parameters
    ----------
    gamma_markets : list[dict]
        Raw market dicts from Gamma API (active=true, closed=false).
    config : ComplementArbConfig
        Strategy configuration for filtering.

    Returns
    -------
    tuple[list[ComplementMarket], list[MarketSkipReason]]
        Eligible markets and skip reasons.
    """
    eligible: list[ComplementMarket] = []
    skips: list[MarketSkipReason] = []
    seen_condition_ids: set[str] = set()

    for market in gamma_markets:
        condition_id = market.get("conditionId") or market.get("condition_id")
        slug = market.get("slug", market.get("market_slug", "")) or ""
        question = market.get("question", "")
        active = market.get("active", False)
        closed = market.get("closed", False)

        # Basic structure
        if not condition_id:
            skips.append(MarketSkipReason("", slug, "missing condition_id"))
            continue

        if not slug:
            skips.append(MarketSkipReason(condition_id, "", "missing slug"))
            continue

        # Only process each condition_id once
        if condition_id in seen_condition_ids:
            continue

        # Active/open check
        if not active:
            skips.append(MarketSkipReason(condition_id, slug, f"not active (active={active})"))
            continue

        if closed:
            skips.append(MarketSkipReason(condition_id, slug, f"closed (closed={closed})"))
            continue

        # --- Token extraction ---
        clob_token_ids_raw = market.get("clobTokenIds", [])
        if isinstance(clob_token_ids_raw, str):
            import ast
            try:
                clob_token_ids_raw = ast.literal_eval(clob_token_ids_raw)
            except Exception:
                clob_token_ids_raw = []
        if not isinstance(clob_token_ids_raw, list):
            clob_token_ids_raw = []

        outcomes_raw = market.get("outcomes", [])
        if isinstance(outcomes_raw, str):
            import ast
            try:
                outcomes_raw = ast.literal_eval(outcomes_raw)
            except Exception:
                outcomes_raw = []
        if not isinstance(outcomes_raw, list):
            outcomes_raw = []

        # Must have exactly 2 outcomes
        if len(clob_token_ids_raw) != 2 or len(outcomes_raw) != 2:
            skips.append(
                MarketSkipReason(
                    condition_id, slug,
                    f"not binary: {len(clob_token_ids_raw)} tokens, {len(outcomes_raw)} outcomes",
                ),
            )
            continue

        # Identify YES and NO tokens
        yes_token_id: str | None = None
        no_token_id: str | None = None
        for tid, outcome in zip(clob_token_ids_raw, outcomes_raw, strict=False):
            outcome_upper = outcome.upper().strip()
            if outcome_upper == "YES":
                yes_token_id = str(tid)
            elif outcome_upper == "NO":
                no_token_id = str(tid)

        if not yes_token_id or not no_token_id:
            skips.append(
                MarketSkipReason(
                    condition_id, slug,
                    f"missing YES/NO tokens: yes={yes_token_id}, no={no_token_id}",
                ),
            )
            continue

        # NegRisk check
        neg_risk = market.get("negRisk", False)
        if isinstance(neg_risk, str):
            neg_risk = neg_risk.lower() in ("true", "1")
        if neg_risk:
            skips.append(MarketSkipReason(condition_id, slug, "negRisk market (V1 exclusion)"))
            continue

        # Accepting orders
        accepting_orders = market.get("acceptingOrders") or market.get("accepting_orders", True)
        if isinstance(accepting_orders, str):
            accepting_orders = accepting_orders.lower() in ("true", "1")
        if not accepting_orders:
            skips.append(
                MarketSkipReason(condition_id, slug, f"not accepting orders ({accepting_orders})"),
            )
            continue

        # --- Allowlist filtering ---
        event_slug = ""
        events_data = market.get("events") or []
        if events_data and isinstance(events_data, list) and len(events_data) > 0:
            event_slug = events_data[0].get("slug", "")

        if config.event_slug_allowlist:
            if event_slug not in config.event_slug_allowlist:
                skips.append(
                    MarketSkipReason(condition_id, slug, f"event_slug {event_slug} not in allowlist"),
                )
                continue

        if config.market_slug_allowlist:
            if slug not in config.market_slug_allowlist:
                skips.append(
                    MarketSkipReason(condition_id, slug, f"market_slug {slug} not in allowlist"),
                )
                continue

        # --- Timestamp/freshness ---
        end_date_iso = market.get("endDateIso") or market.get("end_date_iso")

        # --- Tick/precision ---
        min_tick = float(market.get("orderPriceMinTickSize") or market.get("minimum_tick_size", 0.001))
        min_order_size = float(market.get("orderMinSize") or market.get("minimum_order_size", 5))

        # --- Fee rate ---
        taker_fee_rate = 0.0
        fee_schedule = market.get("feeSchedule")
        if fee_schedule and isinstance(fee_schedule, dict):
            taker_fee_rate = float(fee_schedule.get("rate", 0.0))
        else:
            # Check in the normalized structure
            gamma_original = market.get("_gamma_original", {})
            fee_schedule = gamma_original.get("feeSchedule")
            if fee_schedule and isinstance(fee_schedule, dict):
                taker_fee_rate = float(fee_schedule.get("rate", 0.0))

        # --- Category ---
        category = None
        tags = market.get("tags") or market.get("categories") or []
        if isinstance(tags, str):
            tags = [tags]
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    label = tag.get("label") or tag.get("slug") or tag.get("name")
                    if label:
                        category = str(label).lower()
                        break
                elif isinstance(tag, str):
                    category = tag.lower()
                    break

        if not category:
            category_raw = market.get("category") or market.get("category_slug")
            if category_raw:
                category = str(category_raw).lower()

        # Category allowlist
        if config.category_allowlist:
            if category not in config.category_allowlist:
                skips.append(
                    MarketSkipReason(condition_id, slug, f"category '{category}' not in allowlist"),
                )
                continue

        # Liquidity
        liquidity_num = market.get("liquidityNum") or market.get("liquidity_num")
        if liquidity_num is not None:
            try:
                liquidity_num = float(liquidity_num)
            except (ValueError, TypeError):
                liquidity_num = None

        volume_num = market.get("volumeNum") or market.get("volume_num")
        if volume_num is not None:
            try:
                volume_num = float(volume_num)
            except (ValueError, TypeError):
                volume_num = None

        # Build instrument IDs (format: {condition_id}-{token_id}.POLYMARKET)
        yes_inst_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        no_inst_id = f"{condition_id}-{no_token_id}.POLYMARKET"

        market_entry = ComplementMarket(
            condition_id=condition_id,
            market_slug=slug,
            event_slug=event_slug,
            question=question,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            yes_instrument_id_str=yes_inst_id,
            no_instrument_id_str=no_inst_id,
            neg_risk=False,
            active=active,
            closed=closed,
            accepting_orders=(accepting_orders is True),
            end_date_iso=end_date_iso,
            minimum_tick_size=min_tick,
            minimum_order_size=min_order_size,
            taker_fee_rate=taker_fee_rate,
            category=category,
            liquidity_num=liquidity_num,
            volume_num=volume_num,
        )
        eligible.append(market_entry)
        seen_condition_ids.add(condition_id)

        # Respect max_markets
        if len(eligible) >= config.max_markets:
            break

    # Deduplicate skips (ignore different first pass issues for same condition_id)
    seen_skip_conditions: set[str] = set()
    unique_skips: list[MarketSkipReason] = []
    for s in skips:
        if s.condition_id not in seen_skip_conditions:
            unique_skips.append(s)
            seen_skip_conditions.add(s.condition_id)

    return eligible, unique_skips
