#!/usr/bin/env python3
"""
Live guarded runner for the Polymarket complement arb strategy.

This is the only entry point capable of submitting real orders.
Multiple safety guards must pass before any execution client is constructed.

This file is STUBBED with explicit TODOs for Phase 7.
Live execution is not implemented in V1.

REQUIRED flags:
    --live                      Confirm live intent
    --acknowledge               Explicit risk acknowledgment
    --max-order-usdc 100        Small conservative limit
    --max-session-loss-usdc 50  Session loss limit

REQUIRED env vars:
    POLYMARKET_PK
    POLYMARKET_API_KEY
    POLYMARKET_API_SECRET
    POLYMARKET_PASSPHRASE
    POLYMARKET_FUNDER
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import ComplementArbConfig

logger = logging.getLogger(__name__)


def check_guards(args: argparse.Namespace) -> bool:
    """
    Check all live guards. Returns True if all pass.
    If any guard fails, prints the reason and returns False.
    Does not construct execution client.
    """
    ok = True

    if not args.live:
        logger.error("GUARD: --live flag is required")
        ok = False

    if not args.acknowledge:
        logger.error("GUARD: --acknowledge flag is required (risk acknowledgment)")
        ok = False

    if args.max_order_usdc <= 0:
        logger.error("GUARD: --max-order-usdc must be > 0")
        ok = False

    if args.max_order_usdc > 500:
        logger.error(f"GUARD: --max-order-usdc {args.max_order_usdc} exceeds V1 hard cap of 500")
        ok = False

    if not args.max_session_loss_usdc or args.max_session_loss_usdc <= 0:
        logger.error("GUARD: --max-session-loss-usdc must be set")
        ok = False

    if not args.one_leg_timeout_ms or args.one_leg_timeout_ms <= 0:
        logger.error("GUARD: --one-leg-timeout-ms must be set")
        ok = False

    if not args.resolution_danger_window_seconds or args.resolution_danger_window_seconds <= 0:
        logger.error("GUARD: --resolution-danger-window-seconds must be set")
        ok = False

    if not args.min_net_edge_per_share or args.min_net_edge_per_share <= 0:
        logger.error("GUARD: --min-net-edge-per-share must be set")
        ok = False

    # Check required env vars (do not print their values)
    required_env = [
        "POLYMARKET_PK",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_PASSPHRASE",
        "POLYMARKET_FUNDER",
    ]
    for env_var in required_env:
        if not os.environ.get(env_var):
            logger.error(f"GUARD: {env_var} environment variable is required")
            ok = False

    return ok


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live guarded Polymarket complement arb strategy.",
    )
    parser.add_argument("--live", action="store_true", default=False, required=False)
    parser.add_argument("--acknowledge", action="store_true", default=False, required=False)
    parser.add_argument("--max-order-usdc", type=float, default=0)
    parser.add_argument("--max-total-open-usdc", type=float, default=500)
    parser.add_argument("--max-unpaired-exposure-usdc", type=float, default=100)
    parser.add_argument("--max-session-loss-usdc", type=float, default=0)
    parser.add_argument("--one-leg-timeout-ms", type=float, default=0)
    parser.add_argument("--resolution-danger-window-seconds", type=float, default=0)
    parser.add_argument("--min-net-edge-per-share", type=float, default=0)
    parser.add_argument("--max-markets", type=int, default=3)
    parser.add_argument("--event-slug", type=str, default=None)
    parser.add_argument("--market-slug", type=str, default=None)
    return parser


def live_main():
    """
    TODO (Phase 7): Implement live execution.

    - Discover markets
    - Filter to complementary pairs
    - Construct PolymarketDataClient
    - Construct PolymarketExecutionClient ONLY after all guards pass
    - Start the strategy
    - No real orders unless mode=live and dry_run=false
    """
    parser = build_parser()
    args = parser.parse_args()

    if not check_guards(args):
        sys.exit(1)

    logger.info("All guards pass. Constructing live execution...")

    # TODO (Phase 7): Build config, construct clients, run strategy.
    # config = ComplementArbConfig(
    #     mode="live",
    #     dry_run=not args.live,
    #     max_order_usdc=args.max_order_usdc,
    #     ...
    # )
    #
    # TODO (Phase 7): Use PolymarketLiveDataClientFactory and
    # PolymarketLiveExecClientFactory to construct clients.
    #
    # TODO (Phase 7): Only construct PolymarketExecutionClient after
    # all guards pass.
    #
    # TODO (Phase 7): Prefer Rust execution path if available.

    logger.info("Live guarded runner: phase 7 stub. No real orders placed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    live_main()
