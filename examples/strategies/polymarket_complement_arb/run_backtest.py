#!/usr/bin/env python3
"""
Backtest the Polymarket complement arb strategy.

Loads trade data for specified market(s), runs detector/edge/sizing diagnostics,
and writes reports. This is NOT a fill simulator.

Usage:
    python -m examples.strategies.polymarket_complement_arb.run_backtest \\
        --market-slug will-some-binary-meltdown \\
        --duration 86400

    python -m examples.strategies.polymarket_complement_arb.run_backtest \\
        --discover-only --max-markets 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from typing import Any

from nautilus_trader.core.nautilus_pyo3 import HttpClient

from .backtest_harness import run_backtest_diagnostics_async, _create_loader_for_market
from .config import ComplementArbConfig
from .market_filter import extract_complement_markets
from .reports import generate_run_id

logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


async def discover_markets(config: ComplementArbConfig) -> tuple[list[Any], list[Any]]:
    """Discover markets from Gamma API and filter."""
    import msgspec
    http_client = HttpClient(timeout_secs=30)

    params = {"active": "true", "closed": "false", "archived": "false", "limit": 200}
    resp = await http_client.get(f"{GAMMA_API_BASE}/markets", params=params, timeout_secs=30)
    data = msgspec.json.decode(resp.body)

    eligible, skips = extract_complement_markets(data, config)

    if eligible:
        logger.info(f"Eligible: {len(eligible)}, skipped: {len(skips)}")
        for m in eligible[:5]:
            logger.info(f"  {m.market_slug} ({m.question[:60]}) fee={m.taker_fee_rate}")
    else:
        logger.warning(f"No eligible markets found, skipped: {len(skips)}")

    return eligible, skips


async def main():
    parser = argparse.ArgumentParser(
        description="Backtest Polymarket complement arb strategy.",
    )
    parser.add_argument("--market-slug", type=str, help="Single market slug to backtest")
    parser.add_argument("--event-slug", type=str, help="Event slug to backtest")
    parser.add_argument("--max-markets", type=int, default=5, help="Max markets")
    parser.add_argument("--duration", type=float, default=86400, help="Backtest duration window in seconds")
    parser.add_argument("--discover-only", action="store_true", help="Only discover markets, don't run")
    args = parser.parse_args()

    config = ComplementArbConfig(
        mode="backtest",
        dry_run=True,
        max_markets=args.max_markets,
        market_slug_allowlist=(args.market_slug,) if args.market_slug else (),
        event_slug_allowlist=(args.event_slug,) if args.event_slug else (),
    )

    eligible, skips = await discover_markets(config)

    if args.discover_only or not eligible:
        return

    run_id = generate_run_id()

    result = await run_backtest_diagnostics_async(
        markets=eligible,
        config=config,
        run_id=run_id,
        git_sha="unknown",
    )

    logger.info(f"Backtest complete: report at {result['report_path']}")
    logger.info(f"Opportunities detected: {result['opportunities_detected']}")
    logger.info(f"Maker gate passes: {result['maker_pass']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
