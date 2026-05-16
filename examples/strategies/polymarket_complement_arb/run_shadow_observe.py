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


class PublicDataClient(Protocol):
    async def discover_markets(self, limit: int) -> list[dict[str, Any]]: ...
    async def fetch_book(self, token_id: str) -> BookSnapshot | None: ...
    async def fetch_trades(self, token_id: str, after_ts: int | None = None, limit: int = 500) -> list[dict[str, Any]]: ...


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

    async def fetch_trades(self, token_id: str, after_ts: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        # data-api currently tolerates unknown filters, so filter client-side too.
        if after_ts is not None:
            params["after"] = after_ts
        try:
            data = await asyncio.to_thread(_get_json, f"{DATA_API_BASE}/trades", params, 15.0)
        except Exception as exc:
            logger.warning("trade fetch failed token=%s error=%s", token_id, exc)
            return []
        if isinstance(data, dict):
            data = data.get("trades") or data.get("data") or []
        return [row for row in data if str(row.get("asset") or row.get("token_id") or row.get("market") or "") == str(token_id)]


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

    async def fetch_trades(self, token_id: str, after_ts: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._trades.get(token_id, [])
        if after_ts is None:
            return list(rows)
        return [row for row in rows if _trade_ts_ns(row) >= after_ts * 1_000_000_000]


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


def _sufficiency_from_config(config: ComplementArbConfig) -> ShadowSufficiencyConfig:
    return ShadowSufficiencyConfig(
        min_observer_windows=config.min_observer_windows,
        min_detected_opportunities=config.min_detected_opportunities,
        min_pessimistic_paired_fills=config.min_pessimistic_paired_fills,
        min_same_condition_valid_opportunities=config.min_same_condition_valid_opportunities,
        min_non_dust_opportunities=config.min_non_dust_opportunities,
    )


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

    while True:
        windows += 1
        now_ms = time.time() * 1000
        ts_event_ns = time.time_ns()
        for market in markets:
            yes_book = await client.fetch_book(market.yes_token_id)
            no_book = await client.fetch_book(market.no_token_id)
            book_fetches += 2
            if yes_book is None or no_book is None:
                book_errors += 1
                continue
            for token_id in (market.yes_token_id, market.no_token_id):
                rows = await client.fetch_trades(token_id, limit=500)
                existing = {json.dumps(row, sort_keys=True, default=str) for row in trades_by_token.get(token_id, [])}
                merged = trades_by_token.setdefault(token_id, [])
                for row in rows:
                    key = json.dumps(row, sort_keys=True, default=str)
                    if key not in existing:
                        merged.append(row)
                        existing.add(key)
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
    paths = write_shadow_reports(output_dir, results, observer_window_count=observer_windows, sufficiency=suff)
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
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str))
    summary_path = output_dir / "shadow_summary.json"
    enriched = json.loads(summary_path.read_text())
    enriched["metadata"] = metadata
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
    raw = await client.discover_markets(limit)
    eligible, skips = extract_complement_markets(raw, config)
    logger.info("eligible markets=%s skipped=%s", len(eligible), len(skips))
    return eligible


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run observer-only complement arb shadow validation.")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-markets", type=int, default=20)
    parser.add_argument("--market-slug", type=str)
    parser.add_argument("--event-slug", type=str)
    parser.add_argument("--output-root", type=Path, default=Path("reports/polymarket_complement_arb_shadow"))
    args = parser.parse_args()

    config = ComplementArbConfig(
        mode="observe",
        dry_run=True,
        max_markets=args.max_markets,
        market_slug_allowlist=(args.market_slug,) if args.market_slug else (),
        event_slug_allowlist=(args.event_slug,) if args.event_slug else (),
    )
    client = UrlPublicDataClient()
    markets = await _discover_eligible(config, client, limit=max(args.max_markets * 20, 200))
    if not markets:
        raise SystemExit("No eligible same-condition YES/NO complement markets discovered")
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
