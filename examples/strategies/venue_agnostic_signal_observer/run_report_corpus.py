#!/usr/bin/env python3
"""Corpus aggregation CLI — aggregate evaluation reports across multiple captures.

Reads summary.json from each report directory, groups results by configuration
key (source_venue, target_venue, signal_type, lookback_ms, horizon_ms), and
produces a corpus aggregation showing consistency across captures.

HARD RULE: Primary sort is by consistency (number of captures where a config
appears), NOT by best result ever. A config that appears in 10 captures with
modest returns is ranked higher than one that appears once with high returns.

Usage:
    python -m examples.strategies.venue_agnostic_signal_observer.run_report_corpus \
        --report-dirs reports/dir1,reports/dir2,reports/dir3 \
        --out reports/corpus/
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifact_metadata import build_metadata


# ---------------------------------------------------------------------------
# corpus_config_key
# ---------------------------------------------------------------------------


def corpus_config_key(group: dict[str, Any]) -> tuple:
    """Return a unique configuration key for a result group.

    The key is (source_venue, target_venue, signal_type, lookback_ms, horizon_ms).
    Groups sharing the same key represent the same signal-target configuration
    across different capture windows.
    """
    return (
        str(group.get("source_venue", "")),
        str(group.get("target_venue", "")),
        str(group.get("signal_type", "")),
        int(group.get("lookback_ms", 0)),
        int(group.get("horizon_ms", 0)),
    )


# ---------------------------------------------------------------------------
# CorpusAggregation
# ---------------------------------------------------------------------------


@dataclass
class CorpusAggregation:
    """Aggregation statistics for one configuration across multiple captures."""

    config_key: tuple  # (source_venue, target_venue, signal_type, lookback_ms, horizon_ms)
    source_venue: str
    target_venue: str
    signal_type: str
    lookback_ms: int
    horizon_ms: int
    num_captures: int
    mean_net_bps_values: list[float]
    median_net_bps_values: list[float]
    win_rate_values: list[float]
    best_mean_net_bps: float
    worst_mean_net_bps: float
    positive_after_cost_captures: int
    captures: list[dict[str, Any]]
    # Derived fields
    consistency_note: str = ""

    @property
    def avg_mean_net_bps(self) -> float:
        """Average of mean_net_bps across captures (finite values only)."""
        finite = [v for v in self.mean_net_bps_values if math.isfinite(v)]
        if not finite:
            return float("nan")
        return sum(finite) / len(finite)

    @property
    def avg_win_rate(self) -> float:
        """Average win_rate across captures (finite values only)."""
        finite = [v for v in self.win_rate_values if math.isfinite(v)]
        if not finite:
            return float("nan")
        return sum(finite) / len(finite)

    @property
    def median_mean_net_bps(self) -> float:
        """Median of mean_net_bps across captures (finite values only)."""
        finite = sorted(v for v in self.mean_net_bps_values if math.isfinite(v))
        if not finite:
            return float("nan")
        n = len(finite)
        if n % 2 == 1:
            return finite[n // 2]
        return (finite[n // 2 - 1] + finite[n // 2]) / 2


# ---------------------------------------------------------------------------
# aggregate_corpus
# ---------------------------------------------------------------------------


def aggregate_corpus(report_dirs: list[Path]) -> list[CorpusAggregation]:
    """Load each report, group results by config key, and compute aggregates.

    Groups are sorted primarily by num_captures (descending), then by
    avg_mean_net_bps (descending). This enforces the HARD RULE that
    consistency matters more than peak performance.
    """
    # Map from config_key -> list of group dicts
    config_groups: dict[tuple, list[dict[str, Any]]] = {}

    for rdir in report_dirs:
        rdir = Path(rdir)
        summary_path = rdir / "summary.json"
        if not summary_path.exists():
            print(f"  [WARN] No summary.json in {rdir}, skipping.", file=sys.stderr)
            continue

        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except Exception as e:
            print(f"  [WARN] Failed to load {summary_path}: {e}", file=sys.stderr)
            continue

        groups = summary.get("results_by_group", [])
        if not groups:
            continue

        for group in groups:
            key = corpus_config_key(group)
            # Enrich with the source report dir
            group_copy = dict(group)
            group_copy["_report_dir"] = str(rdir)
            group_copy["_capture_dir"] = summary.get("capture_dir", "")

            if key not in config_groups:
                config_groups[key] = []
            config_groups[key].append(group_copy)

    # Build CorpusAggregation objects
    aggregations: list[CorpusAggregation] = []

    for key, groups in config_groups.items():
        source_venue, target_venue, signal_type, lookback_ms, horizon_ms = key

        mean_nets: list[float] = []
        median_nets: list[float] = []
        win_rates: list[float] = []
        positive_after_cost = 0

        for g in groups:
            mnb = g.get("mean_net_bps")
            if mnb is not None and math.isfinite(mnb):
                mean_nets.append(float(mnb))
            else:
                mean_nets.append(float("nan"))

            mednb = g.get("median_net_bps")
            if mednb is not None and math.isfinite(mednb):
                median_nets.append(float(mednb))
            else:
                median_nets.append(float("nan"))

            wr = g.get("win_rate")
            if wr is not None and math.isfinite(wr):
                win_rates.append(float(wr))
            else:
                win_rates.append(float("nan"))

            # Check positive after cost
            if mnb is not None and math.isfinite(mnb) and mnb > 0:
                positive_after_cost += 1

        finite_mean_nets = [v for v in mean_nets if math.isfinite(v)]
        best_mean = max(finite_mean_nets) if finite_mean_nets else float("nan")
        worst_mean = min(finite_mean_nets) if finite_mean_nets else float("nan")

        # Consistency note: fraction of captures with positive net returns
        num_captures = len(groups)
        if num_captures > 0 and positive_after_cost == num_captures:
            consistency_note = f"Consistently positive across all {num_captures} captures."
        elif num_captures > 0 and positive_after_cost == 0:
            consistency_note = f"Never positive after costs across {num_captures} captures."
        elif num_captures > 0:
            pct = (positive_after_cost / num_captures) * 100
            consistency_note = (
                f"Positive after costs in {positive_after_cost}/{num_captures} "
                f"captures ({pct:.0f}%)."
            )
        else:
            consistency_note = "No captures."

        agg = CorpusAggregation(
            config_key=key,
            source_venue=source_venue,
            target_venue=target_venue,
            signal_type=signal_type,
            lookback_ms=lookback_ms,
            horizon_ms=horizon_ms,
            num_captures=num_captures,
            mean_net_bps_values=mean_nets,
            median_net_bps_values=median_nets,
            win_rate_values=win_rates,
            best_mean_net_bps=best_mean,
            worst_mean_net_bps=worst_mean,
            positive_after_cost_captures=positive_after_cost,
            captures=groups,
            consistency_note=consistency_note,
        )
        aggregations.append(agg)

    # HARD RULE: Sort by num_captures desc, then avg_mean_net_bps desc
    aggregations.sort(
        key=lambda a: (-a.num_captures, -a.avg_mean_net_bps if math.isfinite(a.avg_mean_net_bps) else 1e9)
    )

    return aggregations


# ---------------------------------------------------------------------------
# format_corpus_report
# ---------------------------------------------------------------------------


def _fmt_bps(value: float) -> str:
    """Format bps values; N/A for non-finite."""
    if math.isfinite(value):
        return f"{value:.2f}"
    return "N/A"


def _fmt_pct(value: float) -> str:
    """Format as percentage; N/A for non-finite."""
    if math.isfinite(value):
        return f"{value * 100:.1f}%"
    return "N/A"


def format_corpus_report(aggregations: list[CorpusAggregation]) -> str:
    """Format corpus aggregation as human-readable markdown."""
    lines: list[str] = [
        "# Corpus Aggregation Report",
        "",
        "## Safety",
        "",
        "**Public data observer only. No auth. No orders. No execution.**",
        "",
        "## Methodology",
        "",
        "Results are aggregated across multiple capture windows by signal configuration. "
        "The primary sort criterion is **consistency** (number of captures where a "
        "configuration appears), NOT best-ever performance. A configuration that appears "
        "in many captures with modest returns ranks higher than one appearing once with "
        "high returns.",
        "",
    ]

    if not aggregations:
        lines.append("## Results")
        lines.append("")
        lines.append("No report directories contained valid summary.json files.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"## Summary: {len(aggregations)} unique configurations across captures")
    lines.append("")

    # Overview table
    lines.append(
        "| # | Config | Captures | Avg Mean (bps) | Median Mean (bps) | "
        "Win Rate | Best (bps) | Worst (bps) | +After Cost | Note |"
    )
    lines.append(
        "|---|--------|----------|-----------------|-------------------|"
        "----------|------------|--------------|-------------|------|"
    )

    for idx, agg in enumerate(aggregations, start=1):
        config_str = (
            f"{agg.source_venue}->{agg.target_venue} "
            f"{agg.signal_type} {agg.lookback_ms}/{agg.horizon_ms}ms"
        )
        lines.append(
            f"| {idx} | {config_str} "
            f"| {agg.num_captures} "
            f"| {_fmt_bps(agg.avg_mean_net_bps)} "
            f"| {_fmt_bps(agg.median_mean_net_bps)} "
            f"| {_fmt_pct(agg.avg_win_rate)} "
            f"| {_fmt_bps(agg.best_mean_net_bps)} "
            f"| {_fmt_bps(agg.worst_mean_net_bps)} "
            f"| {agg.positive_after_cost_captures}/{agg.num_captures} "
            f"| {agg.consistency_note} |"
        )

    lines.append("")
    lines.append("## Per-Configuration Details")
    lines.append("")

    for idx, agg in enumerate(aggregations, start=1):
        lines.append(f"### {idx}. {agg.source_venue}->{agg.target_venue} {agg.signal_type} "
                      f"{agg.lookback_ms}/{agg.horizon_ms}ms")
        lines.append("")
        lines.append(f"- **Captures**: {agg.num_captures}")
        lines.append(f"- **Avg mean net bps**: {_fmt_bps(agg.avg_mean_net_bps)}")
        lines.append(f"- **Median mean net bps**: {_fmt_bps(agg.median_mean_net_bps)}")
        lines.append(f"- **Avg win rate**: {_fmt_pct(agg.avg_win_rate)}")
        lines.append(f"- **Best mean net bps**: {_fmt_bps(agg.best_mean_net_bps)}")
        lines.append(f"- **Worst mean net bps**: {_fmt_bps(agg.worst_mean_net_bps)}")
        lines.append(
            f"- **Positive after cost**: "
            f"{agg.positive_after_cost_captures}/{agg.num_captures} captures"
        )
        lines.append(f"- **Consistency**: {agg.consistency_note}")
        lines.append("")

        # Per-capture table
        lines.append("| Capture | Mean Net (bps) | Median Net (bps) | Win Rate | Candidate |")
        lines.append("|---------|-----------------|-------------------|----------|-----------|")
        for cap in agg.captures:
            report_dir = cap.get("_report_dir", "?")
            short_dir = Path(report_dir).name if report_dir and report_dir != "?" else "?"
            mean_nb = cap.get("mean_net_bps", float("nan"))
            med_nb = cap.get("median_net_bps", float("nan"))
            wr = cap.get("win_rate", float("nan"))
            cand = cap.get("candidate", False)
            lines.append(
                f"| {short_dir} "
                f"| {_fmt_bps(mean_nb)} "
                f"| {_fmt_bps(med_nb)} "
                f"| {_fmt_pct(wr)} "
                f"| {'Yes' if cand else 'No'} |"
            )
        lines.append("")

    lines.append("## Important Notes")
    lines.append("")
    lines.append(
        "- **Consistency over performance**: Rankings prioritise configurations that "
        "appear reliably across captures, not those with single best-case results."
    )
    lines.append(
        "- **Public data observer only**: This analysis uses only publicly available "
        "market data. It does not constitute trading signals or investment advice."
    )
    lines.append(
        "- **After-cost returns**: 'Positive after cost' means mean_net_bps > 0 after "
        "the assumed all-in cost (fee + slippage + quote mismatch buffer)."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Aggregate evaluation reports across multiple captures into a corpus summary."
    )
    p.add_argument(
        "--report-dirs",
        type=str,
        required=True,
        help="Comma-separated list of report directories (each containing summary.json).",
    )
    p.add_argument(
        "--out",
        type=str,
        default="reports/corpus/",
        help="Output directory for corpus aggregation. Default: reports/corpus/.",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    report_dir_strs = [d.strip() for d in args.report_dirs.split(",") if d.strip()]
    report_dirs = [Path(d) for d in report_dir_strs]

    if not report_dirs:
        print("ERROR: No report directories specified.", file=sys.stderr)
        sys.exit(1)

    # Validate report directories
    for rd in report_dirs:
        if not rd.exists():
            print(f"ERROR: Report directory not found: {rd}", file=sys.stderr)
            sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Aggregating {len(report_dirs)} report directories...")
    for rd in report_dirs:
        print(f"  - {rd}")

    # Aggregate
    aggregations = aggregate_corpus(report_dirs)

    print(f"\nFound {len(aggregations)} unique configurations across corpus.")

    if aggregations:
        top = aggregations[0]
        print(
            f"  Top config: {top.source_venue}->{top.target_venue} "
            f"{top.signal_type} {top.lookback_ms}/{top.horizon_ms}ms "
            f"({top.num_captures} captures, avg mean: {_fmt_bps(top.avg_mean_net_bps)} bps)"
        )

    # Build metadata
    meta = build_metadata(capture_mode="", run_args=args)

    # Write JSON
    json_data: dict[str, Any] = {
        "_metadata": meta,
        "num_report_dirs": len(report_dirs),
        "report_dirs": [str(rd) for rd in report_dirs],
        "num_unique_configs": len(aggregations),
        "aggregations": [],
    }

    for agg in aggregations:
        agg_dict: dict[str, Any] = {
            "config_key": list(agg.config_key),
            "source_venue": agg.source_venue,
            "target_venue": agg.target_venue,
            "signal_type": agg.signal_type,
            "lookback_ms": agg.lookback_ms,
            "horizon_ms": agg.horizon_ms,
            "num_captures": agg.num_captures,
            "avg_mean_net_bps": agg.avg_mean_net_bps,
            "median_mean_net_bps": agg.median_mean_net_bps,
            "avg_win_rate": agg.avg_win_rate,
            "best_mean_net_bps": agg.best_mean_net_bps,
            "worst_mean_net_bps": agg.worst_mean_net_bps,
            "positive_after_cost_captures": agg.positive_after_cost_captures,
            "consistency_note": agg.consistency_note,
            "captures": agg.captures,
        }
        json_data["aggregations"].append(agg_dict)

    json_path = out_dir / "corpus_aggregation.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2, default=str)

    # Write Markdown
    md_content = format_corpus_report(aggregations)
    md_path = out_dir / "corpus_report.md"
    with open(md_path, "w") as f:
        f.write(md_content)

    print()
    print("=" * 60)
    print("CORPUS AGGREGATION COMPLETE")
    print(f"  Report dirs: {len(report_dirs)}")
    print(f"  Unique configs: {len(aggregations)}")
    print(f"  Output dir: {out_dir}")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()