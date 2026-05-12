#!/usr/bin/env python3
"""V7: L2 Maker Paper Simulator — Entry Point."""
import sys
import asyncio
import logging
import time
from pathlib import Path

# Import from V7 package
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples.strategies.kraken_l2_maker_paper.config import V7Config
from examples.strategies.kraken_l2_maker_paper.simulator import MakerPaperSimulator
from examples.strategies.kraken_l2_maker_paper.kraken_ws import KrakenBookWS
from examples.strategies.kraken_l2_maker_paper.book_models import BookSnapshot, OrderBook
from examples.strategies.kraken_l2_maker_paper.reports import write_event, write_summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description="V7 L2 Maker Paper Simulator")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD", "ETH/USD"])
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--quote-lifetime", type=float, default=5.0)
    parser.add_argument("--fill-model", default="pessimistic", choices=["pessimistic", "neutral", "optimistic"])
    parser.add_argument("--maker-fee-bps", type=float, default=3.0)
    parser.add_argument("--out", default="reports/v7_l2_maker_paper")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg = V7Config(
        symbols=args.symbols,
        duration_seconds=args.duration_seconds,
        quote_lifetime_seconds=args.quote_lifetime,
        fill_model=args.fill_model,
        maker_fee_bps=args.maker_fee_bps,
        output_dir=args.out,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"V7: L2 Maker Paper Simulator")
    print(f"  Symbols: {', '.join(args.symbols)}")
    print(f"  Duration: {args.duration_seconds}s")
    print(f"  Fill model: {args.fill_model}")
    print(f"  Quote lifetime: {args.quote_lifetime}s")
    print(f"  Maker fee: {args.maker_fee_bps}bps + {cfg.fill_penalty_bps}bps fill penalty")
    print(f"  No orders. No private keys. Observer-only.")
    print()

    asyncio.run(run(cfg, out_dir))


async def run(cfg: V7Config, out_dir: Path):
    # Create WS client
    ws = KrakenBookWS(cfg.ws_url)

    # Create simulator and share book dict reference
    sim = MakerPaperSimulator(cfg)
    sim.books = ws.books  # Same object — WS client populates it

    start = time.time()
    event_log = out_dir / "events.jsonl"

    with open(event_log, "w") as fh:
        def on_event(event):
            if event.event_type in ("snapshot", "update"):
                sym = event.symbol
                if sym and sym in sim.books:
                    sim.process_book_update(sym)
                    # Log summary every ~200 updates
                    if sim.stats["book_updates"] % 200 == 0:
                        book = sim.books[sym]
                        snap = BookSnapshot.from_book(book, cfg.stale_book_max_age_seconds)
                        write_event({
                            "type": "book_update_summary",
                            "timestamp": time.time(),
                            "symbol": sym,
                            "best_bid": round(float(snap.best_bid or 0), 2),
                            "best_ask": round(float(snap.best_ask or 0), 2),
                            "midprice": round(float(snap.midprice or 0), 2),
                            "spread_bps": round(snap.spread_bps or 0, 4),
                            "imbalance": round(snap.imbalance, 4),
                            "is_stale": snap.is_stale,
                        }, fh)
            elif event.event_type == "trade":
                if event.trades:
                    for t in event.trades:
                        sim.process_trade(event.symbol, t["price"], t["qty"])

        await ws.connect_and_subscribe(
            cfg.symbols,
            on_book_update=on_event,
            duration=cfg.duration_seconds,
        )

    # Close all quotes
    for engine in sim.quote_engines.values():
        for state in list(engine.active_quotes):
            state.cancel("run_end")
            engine.history.append(state)
        engine.active_quotes = []

    end = time.time()
    summary = sim.get_summary_stats(start, end)
    write_summary(summary, out_dir)

    net = summary["net_paper_pnl_bps"]
    print(f"\nV7 Summary:")
    print(f"  Runtime: {summary['runtime_seconds']:.0f}s")
    print(f"  Book updates: {summary['book_updates']}")
    print(f"  Quotes placed: {summary['quotes_placed']}")
    print(f"  Quotes cancelled: {summary['quotes_cancelled']}")
    print(f"  Paper fills: {summary['paper_fills']}")
    print(f"  Fill rate: {summary['fill_rate']*100:.4f}%")
    print(f"  Avg spread: {summary['average_spread_bps']:.4f} bps")
    print(f"  Avg quote lifetime: {summary['average_quote_lifetime_seconds']:.2f}s")
    print(f"  Gross paper PnL: {summary['gross_paper_pnl_bps']:.4f} bps")
    print(f"  Fees (maker+penalty): {summary['fees_bps']:.4f} bps")
    print(f"  Net paper PnL: {net:.4f} bps")
    if net > 0:
        print("  → POSITIVE net paper PnL ✓")
    else:
        print("  → NEGATIVE net paper PnL ✗")


if __name__ == "__main__":
    main()
