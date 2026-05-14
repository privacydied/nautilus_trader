"""Spread regime report generation.

Produces CSV and Markdown reports from spread regime analysis.
No orders. No keys. No execution.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from examples.strategies.polymarket_btcusd_arb.spread_regime import (
    SpreadEvent,
    SpreadRegimeSummary,
    SpreadBucketCounts,
    TTE_BUCKET_LABELS,
    SPREAD_THRESHOLDS_BPS,
    assign_tte_bucket,
    assign_time_of_day_bucket,
    assign_volatility_bucket,
    compute_spread_bucket_counts,
    compute_percentile,
    classify_verdict,
)


def write_summary_json(summary: SpreadRegimeSummary, output_dir: Path) -> None:
    """Write summary.json to the output directory."""
    summary_dict = asdict(summary)
    # Ensure None values are written as null
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary_dict, f, indent=2, default=str)


def write_spread_events_csv(events: list[SpreadEvent], output_dir: Path) -> None:
    """Write raw spread events to CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "market_slug", "ts_event_ns", "time_to_expiry_ns",
        "best_bid", "best_ask", "mid", "spread_abs", "spread_bps",
        "book_depth_bid", "book_depth_ask",
        "binance_price", "binance_spread_bps", "binance_short_window_vol_bps",
        "polymarket_stale", "binance_stale",
    ]
    with open(output_dir / "spread_events.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for e in events:
            row = {
                k: (getattr(e, k) if getattr(e, k) is not None else "")
                for k in fieldnames
            }
            writer.writerow(row)


def write_spread_by_tte_csv(events: list[SpreadEvent], output_dir: Path) -> None:
    """Write aggregate spread statistics by TTE bucket."""
    output_dir.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[float]] = {}
    for e in events:
        if e.spread_bps is None or e.polymarket_stale:
            continue
        tte = assign_tte_bucket(e.time_to_expiry_ns)
        buckets.setdefault(tte.bucket_label, []).append(e.spread_bps)

    with open(output_dir / "spread_by_tte.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "tte_bucket", "count", "median_bps", "p25_bps", "p75_bps",
            "p90_bps", "p95_bps", "pct_below_100bps", "pct_below_200bps",
        ])
        writer.writeheader()
        for label in TTE_BUCKET_LABELS:
            spreads = sorted(buckets.get(label, []))
            if not spreads:
                writer.writerow({"tte_bucket": label, "count": 0})
                continue
            pct = compute_spread_bucket_counts(spreads)
            writer.writerow({
                "tte_bucket": label,
                "count": len(spreads),
                "median_bps": compute_percentile(spreads, 0.50),
                "p25_bps": compute_percentile(spreads, 0.25),
                "p75_bps": compute_percentile(spreads, 0.75),
                "p90_bps": compute_percentile(spreads, 0.90),
                "p95_bps": compute_percentile(spreads, 0.95),
                "pct_below_100bps": pct.to_pct(pct.below_100bps),
                "pct_below_200bps": pct.to_pct(pct.below_200bps),
            })


def write_spread_by_market_csv(events: list[SpreadEvent], output_dir: Path) -> None:
    """Write aggregate spread statistics by market slug."""
    output_dir.mkdir(parents=True, exist_ok=True)
    markets: dict[str, list[float]] = {}
    for e in events:
        if e.spread_bps is None or e.polymarket_stale:
            continue
        markets.setdefault(e.market_slug, []).append(e.spread_bps)

    with open(output_dir / "spread_by_market.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "market_slug", "count", "median_bps", "p25_bps", "p75_bps",
            "p90_bps", "p95_bps", "pct_below_100bps", "pct_below_200bps",
        ])
        writer.writeheader()
        for slug, spreads in sorted(markets.items()):
            s = sorted(spreads)
            pct = compute_spread_bucket_counts(s)
            writer.writerow({
                "market_slug": slug,
                "count": len(s),
                "median_bps": compute_percentile(s, 0.50),
                "p25_bps": compute_percentile(s, 0.25),
                "p75_bps": compute_percentile(s, 0.75),
                "p90_bps": compute_percentile(s, 0.90),
                "p95_bps": compute_percentile(s, 0.95),
                "pct_below_100bps": pct.to_pct(pct.below_100bps),
                "pct_below_200bps": pct.to_pct(pct.below_200bps),
            })


def write_spread_by_time_of_day_csv(events: list[SpreadEvent], output_dir: Path) -> None:
    """Write aggregate spread statistics by time of day UTC."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tod: dict[str, list[float]] = {}
    for e in events:
        if e.spread_bps is None or e.polymarket_stale:
            continue
        bucket = assign_time_of_day_bucket(e.ts_event_ns)
        tod.setdefault(bucket, []).append(e.spread_bps)

    with open(output_dir / "spread_by_time_of_day.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "time_of_day_utc", "count", "median_bps", "p25_bps", "p75_bps",
            "p90_bps", "p95_bps", "pct_below_100bps", "pct_below_200bps",
        ])
        writer.writeheader()
        for label in ["00-06UTC", "06-12UTC", "12-18UTC", "18-24UTC"]:
            spreads = sorted(tod.get(label, []))
            if not spreads:
                writer.writerow({"time_of_day_utc": label, "count": 0})
                continue
            pct = compute_spread_bucket_counts(spreads)
            writer.writerow({
                "time_of_day_utc": label,
                "count": len(spreads),
                "median_bps": compute_percentile(spreads, 0.50),
                "p25_bps": compute_percentile(spreads, 0.25),
                "p75_bps": compute_percentile(spreads, 0.75),
                "p90_bps": compute_percentile(spreads, 0.90),
                "p95_bps": compute_percentile(spreads, 0.95),
                "pct_below_100bps": pct.to_pct(pct.below_100bps),
                "pct_below_200bps": pct.to_pct(pct.below_200bps),
            })


def write_spread_by_volatility_csv(events: list[SpreadEvent], output_dir: Path) -> None:
    """Write aggregate spread statistics by Binance volatility bucket."""
    output_dir.mkdir(parents=True, exist_ok=True)
    vol_buckets: dict[str, list[float]] = {}
    for e in events:
        if e.spread_bps is None or e.polymarket_stale:
            continue
        bucket = assign_volatility_bucket(e.binance_short_window_vol_bps)
        vol_buckets.setdefault(bucket, []).append(e.spread_bps)

    with open(output_dir / "spread_by_volatility.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "volatility_bucket", "count", "median_bps", "p25_bps", "p75_bps",
            "p90_bps", "p95_bps", "pct_below_100bps", "pct_below_200bps",
        ])
        writer.writeheader()
        for label in ["0-5bps", "5-20bps", "20-50bps", "50-100bps", "100+bps", "unknown"]:
            spreads = sorted(vol_buckets.get(label, []))
            if not spreads:
                writer.writerow({"volatility_bucket": label, "count": 0})
                continue
            pct = compute_spread_bucket_counts(spreads)
            writer.writerow({
                "volatility_bucket": label,
                "count": len(spreads),
                "median_bps": compute_percentile(spreads, 0.50),
                "p25_bps": compute_percentile(spreads, 0.25),
                "p75_bps": compute_percentile(spreads, 0.75),
                "p90_bps": compute_percentile(spreads, 0.90),
                "p95_bps": compute_percentile(spreads, 0.95),
                "pct_below_100bps": pct.to_pct(pct.below_100bps),
                "pct_below_200bps": pct.to_pct(pct.below_200bps),
            })


def write_report_md(
    summary: SpreadRegimeSummary,
    events: list[SpreadEvent],
    output_dir: Path,
) -> None:
    """Write report.md with spread regime analysis summary."""
    verdict = classify_verdict(summary)
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Polymarket BTC UpDown Spread Regime Study",
        "",
        "## Hypothesis",
        "",
        "Polymarket BTC UpDown markets may be structurally untradeable for maker",
        "fair-probability strategies because quoted spreads are usually wider than",
        "plausible fair-probability edge.",
        "",
        "This is a market-structure study. Not a trading strategy. Not Phase 3.",
        "",
        "## Data Used",
        "",
        f"- Input capture dirs: {', '.join(summary.input_capture_dirs) or 'none'}",
        f"- Markets: {summary.market_count}",
        f"- Total events: {summary.event_count}",
        f"- Valid spread observations: {summary.valid_spread_count}",
        "",
        "## Safety Statement",
        "",
        "- No orders submitted",
        "- No private keys loaded",
        "- No execution client imports",
        "- No on-chain calls",
        f"- Safety status: {summary.safety_status}",
        "",
        "## Spread Distribution Summary",
        "",
        f"- Median spread: {summary.median_spread_bps:.1f} bps" if summary.median_spread_bps else "- Median spread: N/A",
        f"- P25 spread: {summary.p25_spread_bps:.1f} bps" if summary.p25_spread_bps else "- P25 spread: N/A",
        f"- P75 spread: {summary.p75_spread_bps:.1f} bps" if summary.p75_spread_bps else "- P75 spread: N/A",
        f"- P90 spread: {summary.p90_spread_bps:.1f} bps" if summary.p90_spread_bps else "- P90 spread: N/A",
        f"- P95 spread: {summary.p95_spread_bps:.1f} bps" if summary.p95_spread_bps else "- P95 spread: N/A",
        "",
        "## Spread Below Thresholds",
        "",
        f"- Below 20 bps: {summary.pct_spread_lte_20bps:.1f}%",
        f"- Below 40 bps: {summary.pct_spread_lte_40bps:.1f}%",
        f"- Below 80 bps: {summary.pct_spread_lte_80bps:.1f}%",
        f"- Below 100 bps: {summary.pct_spread_lte_100bps:.1f}%",
        f"- Below 200 bps: {summary.pct_spread_lte_200bps:.1f}%",
        f"- Below 500 bps: {summary.pct_spread_lte_500bps:.1f}%",
        "",
        "## TTE Bucket Analysis",
        "",
        f"- Dominant TTE bucket: {summary.dominant_tte_bucket}",
        f"- Tightest TTE bucket: {summary.tightest_tte_bucket}",
        f"- Widest TTE bucket: {summary.widest_tte_bucket}",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
        "## Recommendation",
        "",
        summary.recommendation,
        "",
        "## Limitations",
        "",
        "- Observer-only. No execution.",
        "- Spread data from live capture only (no reconstructed book).",
        "- Binance reference is aggTrade/bookTicker proxy, not full depth.",
        "- Single session may not represent all regimes.",
        "- Do not recommend execution from this study.",
    ]

    with open(output_dir / "report.md", "w") as f:
        f.write("\n".join(lines) + "\n")


def write_safety_check_json(output_dir: Path, status: str = "PASS") -> None:
    """Write safety_check.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safety = {
        "no_orders": True,
        "no_private_keys": True,
        "no_execution_client_imports": True,
        "no_on_chain_calls": True,
        "status": status,
    }
    with open(output_dir / "safety_check.json", "w") as f:
        json.dump(safety, f, indent=2)


def write_all_reports(
    summary: SpreadRegimeSummary,
    events: list[SpreadEvent],
    output_dir: Path,
) -> None:
    """Write all spread regime reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    write_summary_json(summary, output_dir)
    write_spread_events_csv(events, output_dir)
    write_spread_by_tte_csv(events, output_dir)
    write_spread_by_market_csv(events, output_dir)
    write_spread_by_time_of_day_csv(events, output_dir)
    write_spread_by_volatility_csv(events, output_dir)
    write_report_md(summary, events, output_dir)
    write_safety_check_json(output_dir)