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
import time
from pathlib import Path

from .polymarket_btc_updown_liquidity_probe import (
    BTCMarket,
    CaptureManifest,
    OrderbookSample,
    ProbeSummary,
    capture_loop,
    compute_summary,
    discover_btc_updown_markets,
    fetch_cex_proxy_price,
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
    parser.add_argument(
        "--reference-proxy",
        type=str,
        default=None,
        choices=["binance", "kraken", "coinbase"],
        help="CEX proxy price source for distance-to-strike bucketing (default: disabled)",
    )
    parser.add_argument(
        "--proxy-poll-interval",
        type=float,
        default=30.0,
        help="CEX proxy price poll interval in seconds (default: 30)",
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

    markets_out, samples, cl_ticks, manifest, _proxy_prices, _raw_payloads, _raw_resp = await capture_loop(
        markets=markets,
        duration_seconds=args.duration_seconds,
        poll_interval=args.discovery_poll_interval_seconds,
        enable_chainlink=args.chainlink,
        chainlink_poll_interval=args.chainlink_poll_interval,
        reference_proxy=args.reference_proxy,
        proxy_poll_interval=args.proxy_poll_interval,
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

    # Step 5: Post-capture verification (TTE, distance, two-axis grid)
    verification_status = None
    if markets and samples:
        verification_status = _run_verification(out_dir, markets, samples, args.reference_proxy,
                                                summary, manifest,
                                                _proxy_prices=_proxy_prices,
                                                _raw_payloads=_raw_payloads)

    # Re-write summary JSON and MD with updated verification status
    if verification_status:
        summary.verification_status = verification_status
        summary_path = out_dir / "liquidity_probe_summary.json"
        write_summary_json(summary, summary_path)
        logger.info("Re-wrote summary JSON with verification status: %s", verification_status)

        summary_md_path = out_dir / "liquidity_probe_summary.md"
        write_summary_md(summary, manifest, summary_md_path)
        logger.info("Re-wrote summary MD with verification status")

    return 0


def _run_verification(
    out_dir: Path,
    markets: list[BTCMarket],
    samples: list[OrderbookSample],
    reference_proxy: str | None,
    summary: ProbeSummary,
    manifest: CaptureManifest,
    _proxy_prices: list | None = None,
    _raw_payloads: list | None = None,
) -> str | None:
    """Run post-capture verification: TTE, distance, two-axis grid, duration.

    Returns verification status string, or None if not computed.
    """
    from .polymarket_btc_updown_liquidity_probe import (
        _compute_tte_buckets,
        _compute_near_expiry_rollup,
        _compute_distance_to_strike_buckets,
        _compute_two_axis_grid,
        _get_danger_zone_cell,
        _compute_duration_coverage,
        _compute_verification_status_v2,
        write_tte_bucket_summary_json,
        write_tte_bucket_summary_md,
        write_distance_bucket_summary_json,
        write_distance_bucket_summary_md,
        write_two_axis_grid_json,
        write_two_axis_grid_md,
        write_duration_coverage_md,
        write_raw_payloads,
        write_raw_payload_audit,
        REF_UNAVAILABLE,
    )

    # Collect reference prices
    proxy_prices: list[tuple[float, float]] = []
    reference_source = REF_UNAVAILABLE

    # Use captured proxy prices from capture_loop if available
    if _proxy_prices:
        logger.info("Using %d captured proxy prices from capture loop", len(_proxy_prices))
        proxy_prices = list(_proxy_prices)
        reference_source = str(reference_proxy).upper() if reference_proxy else REF_UNAVAILABLE

    if reference_proxy and not proxy_prices:
        try:
            import httpx
            with httpx.Client(timeout=10) as cl:
                price, source, err = _fetch_proxy_price(cl, reference_proxy)
                if price and price > 0:
                    proxy_prices.append((time.time(), price))
                    reference_source = source
                    logger.info("Reference proxy price: $%.2f (%s)", price, source)
                else:
                    logger.warning("Proxy price failed: %s", err)
        except Exception as exc:
            logger.warning("Proxy price fetch failed: %s", exc)

    # TTE buckets
    logger.info("Computing TTE buckets...")
    tte_buckets = _compute_tte_buckets(markets, samples)
    near_expiry = _compute_near_expiry_rollup(tte_buckets)

    tte_json = out_dir / "tte_bucket_summary.json"
    write_tte_bucket_summary_json(tte_buckets, near_expiry, tte_json)
    logger.info("Wrote TTE bucket summary to %s", tte_json)

    tte_md = out_dir / "tte_bucket_summary.md"
    write_tte_bucket_summary_md(tte_buckets, near_expiry, tte_md)
    logger.info("Wrote TTE bucket MD to %s", tte_md)

    # Distance-to-strike buckets
    logger.info("Computing distance-to-strike buckets...")
    distance_buckets = _compute_distance_to_strike_buckets(
        markets, samples, proxy_prices, reference_source,
    )

    dist_json = out_dir / "distance_to_strike_bucket_summary.json"
    write_distance_bucket_summary_json(distance_buckets, reference_source, dist_json)
    logger.info("Wrote distance bucket summary to %s", dist_json)

    dist_md = out_dir / "distance_to_strike_bucket_summary.md"
    write_distance_bucket_summary_md(distance_buckets, reference_source, dist_md)
    logger.info("Wrote distance bucket MD to %s", dist_md)

    # Two-axis grid
    logger.info("Computing two-axis liquidity grid...")
    two_axis_grid = _compute_two_axis_grid(tte_buckets, distance_buckets)
    danger_cell = _get_danger_zone_cell(two_axis_grid)

    grid_json = out_dir / "two_axis_liquidity_grid.json"
    write_two_axis_grid_json(two_axis_grid, danger_cell, grid_json)
    logger.info("Wrote two-axis grid to %s", grid_json)

    grid_md = out_dir / "two_axis_liquidity_grid.md"
    write_two_axis_grid_md(two_axis_grid, danger_cell, grid_md)
    logger.info("Wrote two-axis grid MD to %s", grid_md)

    # Duration coverage
    logger.info("Computing duration coverage...")
    duration_coverage = _compute_duration_coverage(markets, samples)

    dur_md = out_dir / "duration_coverage.md"
    write_duration_coverage_md(duration_coverage, dur_md)
    logger.info("Wrote duration coverage to %s", dur_md)

    # Raw payload artifacts (captured inline if capture_loop had raw payloads)
    if _raw_payloads:
        raw_json = out_dir / "raw_clob_orderbook_payloads.jsonl"
        write_raw_payloads(_raw_payloads, raw_json)
        logger.info("Wrote %d raw payloads to %s", len(_raw_payloads), raw_json)

        raw_md = out_dir / "raw_payload_audit.md"
        write_raw_payload_audit(_raw_payloads, raw_md)
        logger.info("Wrote raw payload audit to %s", raw_md)

    # Check artifact existence for raw payload
    raw_payload_files_exist = (
        (out_dir / "raw_clob_orderbook_payloads.jsonl").exists()
        and (out_dir / "raw_payload_audit.md").exists()
    )

    # Verification status
    instrumented_flags = {
        "parser_fix": True,
    }
    verification_status = _compute_verification_status_v2(
        out_dir=out_dir,
        tte_buckets=tte_buckets,
        distance_buckets=distance_buckets,
        two_axis_grid=two_axis_grid,
        duration_coverage=duration_coverage,
        instrumented_flags=instrumented_flags,
    )

    logger.info("=" * 60)
    logger.info("VERIFICATION STATUS: %s", verification_status)
    logger.info("=" * 60)
    logger.info("Near-expiry samples: %d", near_expiry.get("near_expiry_sample_count", 0))
    logger.info("Near-expiry classification: %s", near_expiry.get("near_expiry_classification", "N/A"))
    logger.info("Danger zone samples: %d", danger_cell.get("sample_count", 0))
    logger.info("Danger zone classification: %s", danger_cell.get("classification", "N/A"))
    logger.info("15m duration: %s", duration_coverage.get("15m", {}).get("status", "no_data"))
    logger.info("Proxy prices: %d", len(proxy_prices))
    logger.info("Reference source: %s", reference_source)
    logger.info("Raw payload files: %s", "FOUND" if raw_payload_files_exist else "MISSING")

    # Update summary with verification status
    summary.verification_status = verification_status

    return verification_status


def _fetch_proxy_price(client, proxy: str) -> tuple:
    """Fetch a single proxy price."""
    return fetch_cex_proxy_price(client, proxy)


def _fmt(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:.4f}"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
