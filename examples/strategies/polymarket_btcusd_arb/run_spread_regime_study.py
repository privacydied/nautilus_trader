"""Run spread regime study on captured Polymarket BTC UpDown observer data.

Reuses existing live observer capture data from:
  data/polymarket_btcusd_arb/live_observer/

Also supports fresh public observer capture if active markets exist.

No orders. No keys. No execution. No on-chain calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from examples.strategies.polymarket_btcusd_arb.spread_regime import (
    SpreadEvent,
    SpreadRegimeSummary,
    compute_spread_bps,
    compute_spread_abs,
    compute_summary_from_events,
    classify_verdict,
)
from examples.strategies.polymarket_btcusd_arb.spread_regime_reports import (
    write_all_reports,
)


BRANCH = "polymarket-btc-updown-spread-regime-v1"


def load_observer_captures(data_dir: Path) -> list[SpreadEvent]:
    """Load spread events from existing live observer capture data.

    Reads metadata.json for market slug and expiry info,
    polymarket_events.jsonl for per-event quote data.
    """
    events: list[SpreadEvent] = []
    observer_dir = data_dir / "polymarket_btcusd_arb" / "live_observer"
    if not observer_dir.exists():
        return events

    for capture_dir in sorted(observer_dir.iterdir()):
        if not capture_dir.is_dir():
            continue

        metadata_path = capture_dir / "metadata.json"
        if not metadata_path.exists():
            continue

        try:
            metadata = json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        market_slug = metadata.get("market_slug", "unknown")
        expiry_ns = metadata.get("end_ns", 0)
        if isinstance(expiry_ns, str):
            try:
                expiry_ns = int(expiry_ns)
            except (ValueError, TypeError):
                expiry_ns = 0

        poly_events_path = capture_dir / "polymarket_events.jsonl"
        if not poly_events_path.exists():
            continue

        try:
            for line in poly_events_path.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("type") != "poly_quote":
                    continue

                ts_event_ns = rec.get("ts_event_ns", 0)
                tte_ns = expiry_ns - ts_event_ns if expiry_ns and expiry_ns > 0 else  0

                best_bid = rec.get("best_bid")
                best_ask = rec.get("best_ask")
                mid = rec.get("mid")
                spread_bps = rec.get("spread_bps")
                if spread_bps is None and best_bid is not None and best_ask is not None:
                    spread_bps = compute_spread_bps(best_bid, best_ask)

                events.append(SpreadEvent(
                    market_slug=market_slug,
                    ts_event_ns=ts_event_ns,
                    time_to_expiry_ns=max(tte_ns, 0),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    mid=mid,
                    spread_abs=compute_spread_abs(best_bid, best_ask),
                    spread_bps=spread_bps,
                    book_depth_bid=rec.get("depth_bid"),
                    book_depth_ask=rec.get("depth_ask"),
                    binance_price=rec.get("binance_price"),
                    binance_spread_bps=rec.get("binance_spread_bps"),
                    binance_short_window_vol_bps=rec.get("binance_short_window_vol_bps"),
                    polymarket_stale=rec.get("polymarket_stale", False),
                    binance_stale=rec.get("binance_stale", False),
                ))
        except (json.JSONDecodeError, OSError):
            continue

    return events


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket BTC UpDown spread regime study",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Root data directory containing live_observer captures",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/polymarket_btcusd_arb/spread_regime"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--skip-branch-check",
        action="store_true",
        default=False,
        help="Skip git branch check",
    )
    argv = parser.parse_args()

    # Phase banner
    print(f"PHASE=SPREAD_REGIME_STUDY")
    print(f"OBSERVER_ONLY=1")
    print(f"NO_ORDERS=1")
    print(f"NO_KEYS=1")
    print(f"BRANCH={BRANCH}")

    # Branch check
    if not argv.skip_branch_check:
        import subprocess
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[4],
        )
        current_branch = result.stdout.strip()
        if not current_branch.startswith("polymarket-btc-updown-spread-regime"):
            print(f"WARNING: Active branch is '{current_branch}', expected '{BRANCH}'")
            print("Use --skip-branch-check to bypass.")

    # Load data
    print(f"Loading observer captures from {argv.data_dir}...")
    events = load_observer_captures(argv.data_dir)
    print(f"Loaded {len(events)} spread events.")

    if not events:
        print("No spread events found. Exiting.")
        print("Ensure observer capture data exists in data/polymarket_btcusd_arb/live_observer/")
        sys.exit(1)

    # Compute summary
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    input_dirs = [str(argv.data_dir)]

    summary = compute_summary_from_events(events, run_id, input_dirs)
    verdict = classify_verdict(summary)

    print(f"\n=== Spread Regime Summary ===")
    print(f"Markets: {summary.market_count}")
    print(f"Total events: {summary.event_count}")
    print(f"Valid spread observations: {summary.valid_spread_count}")
    if summary.median_spread_bps is not None:
        print(f"Median spread: {summary.median_spread_bps:.1f} bps")
    if summary.p95_spread_bps is not None:
        print(f"P95 spread: {summary.p95_spread_bps:.1f} bps")
    print(f"Below 80 bps: {summary.pct_spread_lte_80bps:.1f}%")
    print(f"Below 200 bps: {summary.pct_spread_lte_200bps:.1f}%")
    print(f"\nVerdict: {verdict}")

    # Write reports
    output_dir = argv.report_dir / run_id
    print(f"\nWriting reports to {output_dir}...")
    write_all_reports(summary, events, output_dir)

    print(f"Report written to {output_dir}")
    print(f"\nVerdict: {verdict}")
    print(f"Recommendation: {summary.recommendation}")


if __name__ == "__main__":
    main()