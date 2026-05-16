#!/usr/bin/env python3
"""
Observe-mode runner for the Polymarket complement arb strategy.

Discovers active binary markets, subscribes to live data, runs opportunity
detection and passive fill estimation. No execution client. No orders.

Usage:
    python -m examples.strategies.polymarket_complement_arb.run_observe \\
        --duration 300 --max-markets 10

    python -m examples.strategies.polymarket_complement_arb.run_observe \\
        --event-slug will-bitcoin-reach-200k-by-june-2026 \\
        --duration 600
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from nautilus_trader.core.nautilus_pyo3 import HttpClient

from .config import ComplementArbConfig
from .detector import detect_opportunity
from .models import ComplementBookState
from .ledger import Ledger
from .market_filter import extract_complement_markets
from .models import BookSnapshot, ComplementMarket
from .passive_fill_estimator import PassiveFillEstimator
from .reports import compute_config_hash, generate_run_id, generate_run_summary

logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


async def discover_active_markets(
    http_client: HttpClient,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Fetch active binary markets from Gamma API."""
    params = {
        "active": "true",
        "closed": "false",
        "archived": "false",
        "limit": limit,
    }
    import msgspec

    resp = await http_client.get(
        f"{GAMMA_API_BASE}/markets",
        params=params,
        timeout_secs=30,
    )
    if resp.status != 200:
        raise RuntimeError(f"Gamma API error: {resp.status}")
    return msgspec.json.decode(resp.body)


async def fetch_book(
    http_client: HttpClient,
    token_id: str,
) -> BookSnapshot | None:
    """Fetch current order book for a token from the CLOB API."""
    import msgspec

    try:
        resp = await http_client.get(
            f"https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout_secs=10,
        )
        if resp.status != 200:
            return None

        data = msgspec.json.decode(resp.body)

        bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

        return BookSnapshot(
            instrument_id_str="",
            token_id=token_id,
            bids=bids[:5],
            asks=asks[:5],
            timestamp_ms=time.time() * 1000,
        )
    except Exception as e:
        logger.warning(f"Failed to fetch book for {token_id}: {e}")
        return None


async def observe_loop(
    markets: list[ComplementMarket],
    config: ComplementArbConfig,
    duration_secs: float,
    http_client: HttpClient,
    run_id: str,
    git_sha: str = "unknown",
) -> dict[str, Any]:
    """
    Observe active binary markets, detecting opportunities and recording
    passive fill estimates.
    """
    ledger = Ledger(run_id)
    ledger.write_config(vars(config))

    estimator = PassiveFillEstimator()
    opportunities_detected = 0
    maker_gate_passes = 0
    total_book_fetches = 0
    book_fetch_errors = 0
    quote_count = 0

    start_time = time.time()
    deadline = start_time + duration_secs

    logger.info(
        f"Observing {len(markets)} markets for {duration_secs}s "
        f"(run_id={run_id}, config_hash={compute_config_hash(config)})",
    )

    while time.time() < deadline:
        iteration_start = time.time()

        for market in markets:
            # Fetch book for YES token
            yes_book = await fetch_book(http_client, market.yes_token_id)
            total_book_fetches += 1
            if yes_book is None:
                book_fetch_errors += 1
                continue

            # Fetch book for NO token
            no_book = await fetch_book(http_client, market.no_token_id)
            total_book_fetches += 1
            if no_book is None:
                book_fetch_errors += 1
                continue

            book_state = ComplementBookState(
                condition_id=market.condition_id,
                market_slug=market.market_slug,
                yes_book=yes_book,
                no_book=no_book,
                ts_event_ns=time.time_ns(),
            )

            now_ms = time.time() * 1000
            diag = detect_opportunity(book_state, market, config, now_ms)

            if diag is not None:
                opportunities_detected += 1
                ledger.write_opportunity(vars(diag))

                if diag.maker_gate_pass:
                    maker_gate_passes += 1
                    quote_count += 1
                    estimator.record_quote(
                        condition_id=diag.condition_id,
                        market_slug=diag.market_slug,
                        side="YES",
                        quote_price=diag.yes_bid,
                        quote_size=diag.yes_top_bid_size,
                    )

            # Check touch/cross via book movement
            if yes_book:
                estimator.on_book_snapshot(yes_book)

        # Sleep until next iteration
        elapsed = time.time() - iteration_start
        sleep_time = max(0.1, 5.0 - elapsed)
        if time.time() + sleep_time < deadline:
            await asyncio.sleep(sleep_time)

    # Finalize
    pass_summary = estimator.finalize(quote_count)
    run_duration = time.time() - start_time

    # Generate summary
    summary = generate_run_summary(
        run_id=run_id,
        git_sha=git_sha,
        mode="observe",
        config=config,
        markets_discovered=markets,
        skipped_reasons=[],
        opportunities=[],
        rejected_opportunities=[],
        passive_estimates=estimator.estimates,
        passive_summary=pass_summary,
        adapter_implementation="python (no Rust adapter available)",
        depth_mode="top_of_book_only",
        passive_estimate_source="book_movement_only",
        final_resolution_detection="unavailable",
        run_duration_secs=run_duration,
        rebate_excluded_count=0,
    )

    ledger.write_run_summary_json({
        "run_id": run_id,
        "duration_secs": run_duration,
        "markets_observed": len(markets),
        "total_book_fetches": total_book_fetches,
        "book_fetch_errors": book_fetch_errors,
        "opportunities_detected": opportunities_detected,
        "maker_gate_passes": maker_gate_passes,
        "passive_summary": pass_summary,
    })

    ledger.write_run_summary_md(summary)

    logger.info(
        f"Observe complete: {opportunities_detected} opportunities, "
        f"{maker_gate_passes} maker passes, "
        f"touch_rate={pass_summary.get('touch_rate_pct', 0):.1f}%, "
        f"report={ledger.base_dir}",
    )

    return {
        "run_id": run_id,
        "markets_observed": len(markets),
        "opportunities_detected": opportunities_detected,
        "maker_gate_passes": maker_gate_passes,
        "touch_rate_pct": pass_summary.get("touch_rate_pct", 0),
        "report_path": str(ledger.base_dir),
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Observe Polymarket complement arb opportunities.",
    )
    parser.add_argument("--duration", type=int, default=300, help="Duration in seconds")
    parser.add_argument("--max-markets", type=int, default=10, help="Max markets to observe")
    parser.add_argument("--event-slug", type=str, help="Single event slug to observe")
    parser.add_argument("--market-slug", type=str, help="Single market slug to observe")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Poll interval in seconds")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry run (no orders)")
    args = parser.parse_args()

    config = ComplementArbConfig(
        mode="observe",
        dry_run=args.dry_run,
        max_markets=args.max_markets,
        market_slug_allowlist=(args.market_slug,) if args.market_slug else (),
        event_slug_allowlist=(args.event_slug,) if args.event_slug else (),
    )

    http_client = HttpClient(timeout_secs=30)

    # Discover markets
    logger.info("Discovering active Polymarket markets...")
    raw_markets = await discover_active_markets(http_client, limit=200)
    logger.info(f"Found {len(raw_markets)} active markets")

    eligible, skips = extract_complement_markets(raw_markets, config)
    logger.info(f"Eligible markets: {len(eligible)}, skipped: {len(skips)}")

    if not eligible:
        logger.warning("No eligible markets found")
        return

    run_id = generate_run_id()

    await observe_loop(
        markets=eligible,
        config=config,
        duration_secs=float(args.duration),
        http_client=http_client,
        run_id=run_id,
        git_sha="unknown",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
