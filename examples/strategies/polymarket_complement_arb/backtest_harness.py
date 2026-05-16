"""
Thin backtest/replay diagnostic harness.

Loads trade data for eligible markets, runs detector and edge diagnostics,
and emits reports. This is NOT a fill simulator. It validates detector/edge/
sizing behavior only.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .config import ComplementArbConfig
from .detector import detect_opportunity
from .edge_model import evaluate_maker_gate
from .ledger import Ledger
from .market_filter import extract_complement_markets
from .models import BookSnapshot, ComplementBookState, ComplementMarket, MarketSkipReason, OpportunityDiagnostic
from .passive_fill_estimator import PassiveFillEstimator
from .reports import compute_config_hash, generate_run_id, generate_run_summary
from .sizing import compute_quote_size

from decimal import Decimal


async def run_backtest_diagnostics_async(
    markets: list[ComplementMarket],
    config: ComplementArbConfig,
    run_id: str | None = None,
    git_sha: str = "unknown",
) -> dict[str, Any]:
    """
    Run backtest diagnostics for the given markets.

    Parameters
    ----------
    markets : list[ComplementMarket]
        Eligible markets to analyze.
    config : ComplementArbConfig
        Strategy configuration.
    run_id : str, optional
        Run identifier (generated if not provided).
    git_sha : str
        Git SHA for the run.

    Returns
    -------
    dict[str, Any]
        Diagnostics summary.
    """
    run_id = run_id or generate_run_id()
    ledger = Ledger(run_id)
    start_time = time.time()

    # Write config
    ledger.write_config(vars(config))

    t0 = time.time()

    opportunities: list[OpportunityDiagnostic] = []
    rejected: list[OpportunityDiagnostic] = []
    pass_estimates = PassiveFillEstimator()
    quote_count = 0

    for market in markets:
        # Try to load trades for this market
        try:
            loader = await _create_loader_for_market(market)
            trades = await loader.load_trades()
        except Exception as e:
            ledger.write_skipped_market(
                market.condition_id, market.market_slug, f"trade_load_failed: {e}",
            )
            continue

        if not trades:
            continue

        # For each trade, build synthetic book state and check for opportunities
        # This is a rough diagnostic - actual backtest would use BacktestEngine
        for i in range(0, len(trades), 10):
            trade = trades[i]

            # Create synthetic book snapshot from trade
            trade_price = float(trade.price)
            trade_size = float(trade.size)

            # Create a diagnostic from the trade's price as the ask on both sides
            yes_book = BookSnapshot(
                instrument_id_str=market.yes_instrument_id_str,
                token_id=market.yes_token_id,
                bids=[(trade_price * 0.98, trade_size * 10)],
                asks=[(trade_price * 1.02, trade_size * 10)],
                timestamp_ms=float(trade.ts_event) / 1_000_000,
            )
            no_book = BookSnapshot(
                instrument_id_str=market.no_instrument_id_str,
                token_id=market.no_token_id,
                bids=[((1 - trade_price) * 0.98, trade_size * 10)],
                asks=[((1 - trade_price) * 1.02, trade_size * 10)],
                timestamp_ms=float(trade.ts_event) / 1_000_000,
            )

            from .detector import detect_opportunity
            book_state = ComplementBookState(
                condition_id=market.condition_id,
                market_slug=market.market_slug,
                yes_book=yes_book,
                no_book=no_book,
                ts_event_ns=float(trade.ts_event),
            )

            diag = detect_opportunity(book_state, market, config, now_ms=time.time() * 1000)
            if diag is not None:
                opportunities.append(diag)
                if diag.maker_gate_pass:
                    quote_count += 1
                    # Record passive fill estimate
                    pass_estimate = pass_estimates.record_quote(
                        condition_id=diag.condition_id,
                        market_slug=diag.market_slug,
                        side="YES",
                        quote_price=diag.yes_bid,
                        quote_size=diag.yes_top_bid_size,
                    )
                ledger.write_opportunity(vars(diag))
            else:
                # Create a minimal diagnostic for rejection tracking
                if i % 50 == 0:
                    reject_diag = OpportunityDiagnostic(
                        condition_id=market.condition_id,
                        market_slug=market.market_slug,
                        question=market.question,
                        yes_bid=trade_price * 0.98,
                        yes_ask=trade_price * 1.02,
                        no_bid=(1 - trade_price) * 0.98,
                        no_ask=(1 - trade_price) * 1.02,
                        yes_top_bid_size=0,
                        yes_top_ask_size=0,
                        no_top_bid_size=0,
                        no_top_ask_size=0,
                        gross_gap=0,
                        taker_fee_rate=market.taker_fee_rate,
                        taker_fee_yes=0,
                        taker_fee_no=0,
                        total_taker_fee=0,
                        leg_risk_buffer=0,
                        signing_latency_buffer=0,
                        gas_redeem_buffer=0,
                        total_cost=float("inf"),
                        net_edge=float("-inf"),
                        maker_gate_pass=False,
                        taker_diagnostic_pass=False,
                        depth_ok=False,
                        stale=False,
                        ts_event_ns=float(trade.ts_event),
                    )
                    rejected.append(reject_diag)

        # Save per-market progress
        ledger.write_skipped_market(
            market.condition_id, market.market_slug, "processed",
        )

    # Finalize passive estimates
    pass_summary = pass_estimates.finalize(quote_count)

    run_duration = time.time() - start_time

    summary = generate_run_summary(
        run_id=run_id,
        git_sha=git_sha,
        mode="backtest",
        config=config,
        markets_discovered=markets,
        skipped_reasons=[],
        opportunities=opportunities,
        rejected_opportunities=rejected,
        passive_estimates=pass_estimates.estimates,
        passive_summary=pass_summary,
        adapter_implementation="python (no Rust adapter available)",
        depth_mode="top_of_book_only",
        passive_estimate_source="book_movement_only",
        final_resolution_detection="unavailable",
        run_duration_secs=run_duration,
    )

    ledger.write_run_summary_json({
        "run_id": run_id,
        "git_sha": git_sha,
        "mode": "backtest",
        "duration_secs": run_duration,
        "markets_seen": len(markets),
        "opportunities_detected": len(opportunities),
        "maker_pass": sum(1 for o in opportunities if o.maker_gate_pass),
        "taker_diagnostic_pass": sum(1 for o in opportunities if o.taker_diagnostic_pass),
        "passive_fill_estimates": pass_summary,
    })

    ledger.write_run_summary_md(summary)

    return {
        "run_id": run_id,
        "markets_seen": len(markets),
        "opportunities_detected": len(opportunities),
        "maker_pass": sum(1 for o in opportunities if o.maker_gate_pass),
        "taker_diagnostic_pass": sum(1 for o in opportunities if o.taker_diagnostic_pass),
        "passive_summary": pass_summary,
        "report_path": str(ledger.base_dir),
    }


async def _create_loader_for_market(market: ComplementMarket):
    """Create a PolymarketDataLoader for the given market."""
    from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader
    from nautilus_trader.adapters.polymarket.common.parsing import parse_polymarket_instrument

    # We need a token instrument. Use from_market_slug for simplicity.
    try:
        loader = await PolymarketDataLoader.from_market_slug(
            slug=market.market_slug,
            token_index=0,  # YES
            sanitize_info=True,
        )
        return loader
    except Exception:
        # Fall back to event slug
        if market.event_slug:
            loaders = await PolymarketDataLoader.from_event_slug(
                slug=market.event_slug,
                token_index=0,
                sanitize_info=True,
            )
            for l in loaders:
                if l.condition_id == market.condition_id:
                    return l
        raise


def run_backtest_diagnostics(
    markets: list[ComplementMarket],
    config: ComplementArbConfig,
    run_id: str | None = None,
    git_sha: str = "unknown",
) -> dict[str, Any]:
    """Synchronous wrapper for run_backtest_diagnostics_async."""
    return asyncio.run(run_backtest_diagnostics_async(
        markets=markets,
        config=config,
        run_id=run_id,
        git_sha=git_sha,
    ))
