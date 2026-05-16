#!/usr/bin/env python3
"""
Polymarket BTC Up/Down CLOB liquidity probe — CLI runner.

Observer-only. Public data. No orders. No execution.

Captures orderbook snapshots for near-expiry BTC Up/Down binary markets
on Polymarket and classifies liquidity quality via fixed thresholds.

Usage:

    # 10-minute default capture
    python -m examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_updown_liquidity_probe

    # Custom duration and output directory
    python -m examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_updown_liquidity_probe \\
        --duration-seconds 600 \\
        --out data/polymarket_liquidity_v0

    # With Chainlink reference ticks
    python -m examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_updown_liquidity_probe \\
        --chainlink \\
        --chainlink-poll-interval 30

    # Quick discovery only (no capture)
    python -m examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_updown_liquidity_probe \\
        --discover-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .polymarket_btc_updown_liquidity_probe import (
    capture_loop,
    compute_summary,
    discover_btc_updown_markets,
    write_chainlink_ticks,
    write_discovered_markets,
    write_manifest,
    write_orderbook_samples,
    write_summary_json,
    write_summary_md,
    DEFAULT_DURATION,
    DEFAULT_POLL_INTERVAL,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Polymarket BTC Up/Down CLOB liquidity probe. "
            "Observer-only. No orders. No execution."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_updown_liquidity_probe \\\n"
            "      --duration-seconds 600 --chainlink\n"
        ),
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Capture duration in seconds (default: {DEFAULT_DURATION})",
    )
    parser.add_argument(
        "--discovery-poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=(
            "Poll interval for REST orderbook refresh in seconds "
            f"(default: {DEFAULT_POLL_INTERVAL}). "
            "CLOB WebSocket messages are recorded as received."
        ),
    )
    parser.add_argument(
        "--out",
        type=str,
        default="data/polymarket_liquidity_v0",
        help="Output directory for JSONL files and summaries",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        default=False,
        help="Discover markets only — no capture loop",
    )
    parser.add_argument(
        "--chainlink",
        action="store_true",
        default=False,
        help="Enable Chainlink BTC/USD reference tick capture",
    )
    parser.add_argument(
        "--chainlink-poll-interval",
        type=float,
        default=60.0,
        help="Chainlink price poll interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="btc",
        help="Gamma API tag filter (default: btc)",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=200,
        help="Maximum markets to query from Gamma API (default: 200)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG logging",
    )
    return parser


async def main() -> int:
    """Run the liquidity probe."""
    parser = build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Discover BTC Up/Down markets
    logger.info(
        "Discovering BTC Up/Down markets (tag=%s, max=%d)...",
        args.tag,
        args.max_markets,
    )
    markets = discover_btc_updown_markets(
        tag=args.tag,
        max_markets=args.max_markets,
    )

    btc_updown = [m for m in markets if m.reason_skipped is None]
    skipped = [m for m in markets if m.reason_skipped is not None]

    logger.info(
        "Discovered %d BTC Up/Down markets, %d skipped (total %d)",
        len(btc_updown),
        len(skipped),
        len(markets),
    )

    for m in btc_updown:
        tokens = "(" + ",".join(m.token_ids) + ")" if m.has_tokens else "(no tokens)"
        logger.info(
            "  %s %s %s expiry=%s tokens=%s",
            m.direction.upper(),
            m.market_slug,
            m.question[:60],
            m.expiry or "?",
            tokens,
        )

    # Write discovered markets
    discovered_path = out_dir / "discovered_markets.jsonl"
    write_discovered_markets(markets, discovered_path)
    logger.info("Wrote discovered markets to %s", discovered_path)

    if args.discover_only:
        logger.info("--discover-only set. Skipping capture loop.")
        return 0

    actionable = [m for m in btc_updown if m.has_tokens]
    if not actionable:
        logger.warning(
            "No actionable markets (with token IDs) found. "
            "Skipping capture loop."
        )

    # Step 2: Capture loop
    logger.info(
        "Starting capture: %d markets, %ds duration, %.1fs poll interval",
        len(actionable),
        args.duration_seconds,
        args.discovery_poll_interval_seconds,
    )

    markets_out, samples, cl_ticks, manifest = await capture_loop(
        markets=markets,
        duration_seconds=args.duration_seconds,
        poll_interval=args.poll_interval,
        enable_chainlink=args.chainlink,
        chainlink_poll_interval=args.chainlink_poll_interval,
    )

    logger.info(
        "Capture complete: %d samples, %d Chainlink ticks",
        len(samples),
        len(cl_ticks),
    )

    # Step 3: Write outputs
    samples_path = out_dir / "clob_orderbook_samples.jsonl"
    write_orderbook_samples(samples, samples_path)
    logger.info("Wrote %d samples to %s", len(samples), samples_path)

    if cl_ticks:
        cl_path = out_dir / "chainlink_reference_ticks.jsonl"
        write_chainlink_ticks(cl_ticks, cl_path)
        logger.info("Wrote %d Chainlink ticks to %s", len(cl_ticks), cl_path)
    else:
        logger.info("No Chainlink ticks captured (--chainlink not set)")

    manifest_path = out_dir / "capture_manifest.json"
    write_manifest(manifest, manifest_path)
    logger.info("Wrote manifest to %s", manifest_path)

    # Step 4: Compute summary
    summary = compute_summary(markets, samples)

    summary_path = out_dir / "liquidity_probe_summary.json"
    write_summary_json(summary, summary_path)
    logger.info("Wrote summary JSON to %s", summary_path)

    summary_md_path = out_dir / "liquidity_probe_summary.md"
    write_summary_md(summary, manifest, summary_md_path)
    logger.info("Wrote summary MD to %s", summary_md_path)

    # Print diagnostic
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC CLASSIFICATION: %s", summary.diagnostic_classification)
    logger.info("=" * 60)
    logger.info("Median spread: %s cents", _fmt(summary.median_spread_cents))
    logger.info("P75 spread:    %s cents", _fmt(summary.p75_spread_cents))
    logger.info("P95 spread:    %s cents", _fmt(summary.p95_spread_cents))
    logger.info("Valid samples: %d / %d", summary.valid_samples, summary.samples_collected)
    logger.info("Two-sided:     %.1f%%", summary.percent_two_sided)
    logger.info("Missing:       %.1f%%", summary.percent_missing)

    if args.chainlink:
        cl_valid = [t for t in cl_ticks if t.price is not None]
        if cl_valid:
            prices = [t.price for t in cl_valid if t.price is not None]
            avg_price = sum(prices) / len(prices) if prices else 0
            logger.info("Chainlink BTC/USD: %d ticks, avg ~$%.2f", len(cl_valid), avg_price)

    logger.info("Done. Output directory: %s", out_dir.resolve())
    return 0


def _fmt(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:.4f}"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
