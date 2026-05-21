r"""
CLI runner for the Polymarket BTC Price Target liquidity probe.

Observer-only. No orders. No wallet. No auth.

Usage:
    python -m examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_price_target_liquidity_probe \\
        --duration-seconds 1800 \\
        --poll-interval-seconds 5 \\
        --out reports/polymarket_btc_price_target_liquidity_probe_v0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path

import httpx

from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    FAMILY_BTC_PRICE_TARGET,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    FAMILY_BTC_UPDOWN,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    STRIKE_EXTRACTED,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    AdapterInspectionResult,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    BtcProxyTick,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    LiquiditySample,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    MarketMetadata,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    OrderbookSnapshot,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    classify_distance_bucket,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    classify_tte_bucket,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    compute_distance_bps,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    compute_liquidity_verdict,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    compute_tte_seconds,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    discover_btc_price_target_markets,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    inspect_nautilus_polymarket_adapter,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    is_convex_danger_zone,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    poll_btc_binance_proxy,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    poll_clob_book,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    write_adapter_inspection_json,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    write_liquidity_grid_csv,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    write_markets_json,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    write_report_md,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    write_samples_jsonl,
)
from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    write_summary_json,
)


logger = logging.getLogger(__name__)

DEFAULT_DURATION_SECONDS: int = 1800
DEFAULT_POLL_INTERVAL: int = 5
DEFAULT_OUT: str = "reports/polymarket_btc_price_target_liquidity_probe_v0"
DEFAULT_BTC_VENUE: str = "binance"
DEFAULT_MAX_MARKETS: int = 25
DEFAULT_REQUEST_TIMEOUT: int = 10
DEFAULT_MAX_CONSECUTIVE_ERRORS: int = 5


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket BTC Price Target liquidity probe v0",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help=f"Capture duration in seconds (default: {DEFAULT_DURATION_SECONDS})",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Seconds between orderbook polls (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--btc-proxy-venue",
        type=str,
        default=DEFAULT_BTC_VENUE,
        help=f"BTC price proxy venue (default: {DEFAULT_BTC_VENUE})",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=DEFAULT_MAX_MARKETS,
        help=f"Maximum BTC Price Target markets to track (default: {DEFAULT_MAX_MARKETS})",
    )
    parser.add_argument(
        "--include-raw-payloads",
        action="store_true",
        default=False,
        help="Include raw CLOB payloads (default: False)",
    )
    parser.add_argument(
        "--max-raw-payloads",
        type=int,
        default=100,
        help="Max raw payloads to store (default: 100)",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=f"HTTP request timeout (default: {DEFAULT_REQUEST_TIMEOUT})",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        help=f"Max consecutive errors before abort (default: {DEFAULT_MAX_CONSECUTIVE_ERRORS})",
    )
    parser.add_argument(
        "--data-path",
        type=str,
        default="auto",
        choices=["auto", "nautilus", "mixed", "public-rest"],
        help="Data path to use (default: auto)",
    )
    parser.add_argument(
        "--adapter-inspection-only",
        action="store_true",
        default=False,
        help="Only inspect Nautilus adapter, do not run capture (default: False)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Verbose logging",
    )
    return parser.parse_args()


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _build_data_path(
    args_path: str,
    inspection: AdapterInspectionResult,
) -> str:
    """Determine final data path based on arg and inspection."""
    if args_path == "nautilus":
        if not inspection.nautilus_core_importable:
            raise RuntimeError(
                f"Forced nautilus data path but Nautilus core not importable: "
                f"{inspection.nautilus_core_import_error}"
            )
        return "NAUTILUS_POLYMARKET_DATA_ADAPTER_USED"

    if args_path == "mixed":
        return "MIXED_NAUTILUS_AND_PUBLIC_REST_USED"

    if args_path == "public-rest":
        return "PUBLIC_REST_FALLBACK_USED"

    # auto: use what inspection determined
    return inspection.selected_data_path


async def _run_capture(
    http: httpx.AsyncClient,
    markets: list[MarketMetadata],
    duration_seconds: int,
    poll_interval: int,
    request_timeout: int,
    max_consecutive_errors: int,
    data_path: str,
    raw_payloads: list[str] | None,
    max_raw_payloads: int,
) -> list[LiquiditySample]:
    """Run the main capture loop."""
    samples: list[LiquiditySample] = []
    start_time = time.time()
    end_time = start_time + duration_seconds

    # Filter to BTC_PRICE_TARGET with strike extracted
    target_markets = [
        m for m in markets
        if m.market_family == FAMILY_BTC_PRICE_TARGET
        and m.strike_extraction_status == STRIKE_EXTRACTED
    ]

    logger.info(
        f"Starting capture: {len(target_markets)} Price Target markets, "
        f"{duration_seconds}s duration, {poll_interval}s interval"
    )

    while time.time() < end_time:
        poll_start = time.time()

        # Poll BTC proxy
        btc_tick = await poll_btc_binance_proxy(http)

        for market in target_markets:
            # Poll YES token orderbook
            if market.yes_token_id:
                yes_book = await poll_clob_book(
                    http, market.yes_token_id, market.market_id,
                    side_label="yes", timeout=request_timeout,
                )
                samples.append(_make_sample(
                    market, yes_book, btc_tick, time.time(),
                    side_label="yes",
                ))

            # Poll NO token orderbook
            if market.no_token_id:
                no_book = await poll_clob_book(
                    http, market.no_token_id, market.market_id,
                    side_label="no", timeout=request_timeout,
                )
                samples.append(_make_sample(
                    market, no_book, btc_tick, time.time(),
                    side_label="no",
                ))

        poll_duration = time.time() - poll_start
        sleep_time = max(0, poll_interval - poll_duration)
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    return samples


def _make_sample(
    market: MarketMetadata,
    book: OrderbookSnapshot,
    btc_tick: BtcProxyTick,
    ts: float,
    side_label: str,
) -> LiquiditySample:
    """Create a LiquiditySample from market metadata, orderbook, and BTC tick."""
    tte = compute_tte_seconds(market.end_date_iso)
    tte_bucket = classify_tte_bucket(tte)
    distance = compute_distance_bps(btc_tick.price, market.strike_price)
    dist_bucket = classify_distance_bucket(distance)
    danger_zone = is_convex_danger_zone(tte, distance)

    return LiquiditySample(
        ts_event=ts,
        market_id=market.market_id,
        token_id=book.token_id,
        slug=market.slug,
        question=market.question,
        strike_price=market.strike_price,
        strike_extraction_status=market.strike_extraction_status,
        market_family=market.market_family,
        side_label=side_label,
        yes_token_id=market.yes_token_id,
        no_token_id=market.no_token_id,
        expiry=market.expiry,
        end_date_iso=market.end_date_iso,
        tte_seconds=tte,
        tte_bucket=tte_bucket,
        btc_proxy_price=btc_tick.price,
        distance_to_strike_bps=distance,
        distance_bucket=dist_bucket,
        convex_danger_zone=danger_zone,
        book_status=book.book_status,
        best_bid_price=book.best_bid_price,
        best_ask_price=book.best_ask_price,
        executable_spread_price_units=book.executable_spread_price_units,
        executable_spread_cents=book.executable_spread_cents,
        top_bid_depth_usd=book.top_bid_depth_usd,
        top_ask_depth_usd=book.top_ask_depth_usd,
        top_of_book_depth_usd=book.top_of_book_depth_usd,
        bid_count=book.bid_count,
        ask_count=book.ask_count,
        error=btc_tick.error,
    )


async def _main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)

    logger.info("=== Polymarket BTC Price Target Liquidity Probe V0 ===")

    # --- Adapter inspection ---
    logger.info("Inspecting Nautilus Polymarket integration...")
    inspection = inspect_nautilus_polymarket_adapter()
    logger.info(f"Nautilus core importable: {inspection.nautilus_core_importable}")
    logger.info(f"Selected data path: {inspection.selected_data_path}")

    # Determine final data path
    data_path = _build_data_path(args.data_path, inspection)
    logger.info(f"Effective data path: {data_path}")

    if args.adapter_inspection_only:
        logger.info("Adapter inspection only mode — exiting")
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_adapter_inspection_json(inspection, out_dir / "adapter_inspection.json")
        logger.info(f"Adapter inspection written to {out_dir / 'adapter_inspection.json'}")
        return 0

    # --- Market discovery ---
    run_id = (
        f"polymarket_btc_price_target_liquidity_probe_v0_"
        f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S')}_"
        f"{uuid.uuid4().hex[:8]}"
    )
    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    logger.info("Discovering BTC Price Target markets...")
    async with httpx.AsyncClient(timeout=httpx.Timeout(args.request_timeout_seconds)) as http:
        markets = await discover_btc_price_target_markets(
            http, max_markets=args.max_markets, timeout=args.request_timeout_seconds,
        )

        price_target_count = sum(1 for m in markets if m.market_family == FAMILY_BTC_PRICE_TARGET)
        updown_count = sum(1 for m in markets if m.market_family == FAMILY_BTC_UPDOWN)
        logger.info(
            f"Discovered {len(markets)} BTC markets: "
            f"{price_target_count} Price Target, "
            f"{updown_count} Up/Down (excluded), "
            f"{len(markets) - price_target_count - updown_count} other"
        )

        stripped_markets = [
            m for m in markets
            if m.market_family == FAMILY_BTC_PRICE_TARGET
            and m.strike_extraction_status == STRIKE_EXTRACTED
        ]
        logger.info(f"Markets with strike extracted: {len(stripped_markets)}")

        if stripped_markets:
            logger.info(f"Starting capture loop ({args.duration_seconds}s)...")
            samples = await _run_capture(
                http=http,
                markets=markets,
                duration_seconds=args.duration_seconds,
                poll_interval=args.poll_interval_seconds,
                request_timeout=args.request_timeout_seconds,
                max_consecutive_errors=args.max_consecutive_errors,
                data_path=data_path,
                raw_payloads=None,
                max_raw_payloads=args.max_raw_payloads,
            )
        else:
            samples = []
            logger.warning("No BTC Price Target markets with strikes — skipping capture")
    # end httpx context

    end_time = time.time()

    # --- Compute verdict ---
    verdict, verdict_stats = compute_liquidity_verdict(samples)
    logger.info(f"Phase 0 verdict: {verdict}")

    # --- Write outputs ---
    write_adapter_inspection_json(inspection, out_dir / "adapter_inspection.json")
    write_markets_json(markets, out_dir / "markets.json")
    write_samples_jsonl(samples, out_dir / "samples.jsonl")
    write_liquidity_grid_csv(samples, out_dir / "liquidity_grid.csv")

    summary = write_summary_json(
        run_id=run_id,
        inspection=inspection,
        markets=markets,
        samples=samples,
        verdict=verdict,
        verdict_stats=verdict_stats,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=args.duration_seconds,
        poll_interval=args.poll_interval_seconds,
        btc_venue=args.btc_proxy_venue,
        out_dir=out_dir,
        include_raw=args.include_raw_payloads,
        max_markets=args.max_markets,
        data_path=data_path,
    )
    write_report_md(summary, inspection, samples, out_dir)

    logger.info(f"Report directory: {out_dir}")
    logger.info(f"Summary: {json.dumps(summary, indent=2, default=str)}")
    return 0


def main() -> None:
    """CLI entry point."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
