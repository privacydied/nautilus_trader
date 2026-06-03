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
from glob import glob
from pathlib import Path
from typing import Any

from ...artifact_metadata import build_metadata, check_schema_version, get_metadata_field
from ...quarantine import get_quarantined_run_ids, read_quarantine
from ._prog import set_legacy_prog


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively replace NaN/inf float values with None for JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# corpus_config_key
# ---------------------------------------------------------------------------


def corpus_config_key(group: dict[str, Any]) -> tuple:
    """Return a unique configuration key for a result group.

    The key is (source_venue, target_venue, signal_type, lookback_ms, horizon_ms).
    Groups sharing the same key represent the same signal-target configuration
    across different capture windows.
    """
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return (
        str(group.get("source_venue", "")),
        str(group.get("target_venue", "")),
        str(group.get("signal_type", "")),
        _safe_int(group.get("lookback_ms"), 0),
        _safe_int(group.get("horizon_ms"), 0),
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
        key=lambda a: (-a.num_captures, -a.avg_mean_net_bps if math.isfinite(a.avg_mean_net_bps) else float('inf'))
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
# discover_report_dirs
# ---------------------------------------------------------------------------


def discover_report_dirs(
    reports_root: Path,
    glob_pattern: str,
) -> list[Path]:
    """Discover report directories via glob, sorted deterministically.

    Sorting priority (higher wins):
    1. ``run_id`` from ``_metadata`` in summary.json (if available)
    2. Directory stem (path name)
    3. Modification time (mtime)

    Parameters
    ----------
    reports_root : Path
        Base directory for glob discovery.
    glob_pattern : str
        Glob pattern, e.g. ``derivatives_spot_lead_lag_v2_*``.

    Returns
    -------
    list[Path]
        Sorted list of matching directories.
    """
    reports_root = reports_root.resolve()
    matched: list[Path] = [Path(p) for p in glob(str(reports_root / glob_pattern))]
    matched = [p for p in matched if p.is_dir()]

    def _sort_key(p: Path) -> tuple:
        summary_path = p / "summary.json"
        if summary_path.exists():
            try:
                with open(summary_path) as f:
                    summary = json.load(f)
                run_id = get_metadata_field(summary, "run_id")
                if run_id:
                    return (0, str(run_id), p.stem, p.stat().st_mtime)
            except Exception:
                pass
        return (1, "", p.stem, p.stat().st_mtime)

    matched.sort(key=_sort_key)
    return matched


# ---------------------------------------------------------------------------
# filter_report_dir
# ---------------------------------------------------------------------------


def filter_report_dir(
    report_dir: Path,
    *,
    min_global_overlap_seconds: float = 600.0,
    allowed_schema_versions: set[str] | None = None,
    stream_strictness: str = "lenient",
    quarantine_run_ids: set[str] | None = None,
    include_quarantined: bool = False,
    allow_missing_git_sha: bool = False,
) -> tuple[bool, str | None]:
    """Check a report directory for quality before inclusion.

    Skip conditions (checked in order):

    1. Missing ``summary.json``.
    2. Missing ``_metadata.git_sha`` (unless ``allow_missing_git_sha``).
    3. ``capture_mode == "FAST_DIAGNOSTIC"``.
    4. ``global_overlap_duration_seconds`` below ``min_global_overlap_seconds``.
    5. ``schema_version`` not in ``allowed_schema_versions`` (if set).
    6. ``schema_version`` higher than reader supports.
    7. Failed streams for symbols being aggregated (depends on ``stream_strictness``).
    8. Quarantined ``run_id`` (unless ``include_quarantined``).

    Parameters
    ----------
    report_dir : Path
        Directory to check (must contain ``summary.json``).
    min_global_overlap_seconds : float
        Minimum acceptable overlap duration.  Default 600.
    allowed_schema_versions : set[str] | None
        If set, only these schema versions are accepted (exact match).
        ``None`` accepts all supported versions.
    stream_strictness : str
        ``"strict"`` — reject if ANY failed stream is detected.
        ``"lenient"`` — warn-only (return ok, but record reason).
        ``"off"`` — skip failed-stream check entirely.
    quarantine_run_ids : set[str] | None
        Set of run IDs to skip if ``include_quarantined`` is False.
    include_quarantined : bool
        If True, quarantined runs are not skipped.
    allow_missing_git_sha : bool
        If True, reports without ``_metadata.git_sha`` are accepted.

    Returns
    -------
    (ok, reason)
        ``ok`` is True if the report passes all filters.
        ``reason`` is a short string explaining why it was skipped,
        or ``None`` if accepted.
    """
    summary_path = report_dir / "summary.json"

    # 1. Missing summary.json
    if not summary_path.exists():
        return False, "missing_summary_json"

    try:
        with open(summary_path) as f:
            summary = json.load(f)
    except Exception as e:
        return False, f"failed_to_load_summary_json_{e!s}"

    # 2. Missing _metadata.git_sha
    if not allow_missing_git_sha:
        git_sha = get_metadata_field(summary, "git_sha")
        if not git_sha:
            return False, "missing_git_sha"

    # 3. FAST_DIAGNOSTIC capture mode
    capture_mode = get_metadata_field(summary, "capture_mode", "")
    if capture_mode == "FAST_DIAGNOSTIC":
        return False, "fast_diagnostic_mode"

    # 4. Global overlap below minimum
    overlap_info = summary.get("overlap_info", {}) or {}
    global_duration = overlap_info.get("global_overlap_duration_seconds", None)
    if global_duration is None:
        # Fallback: check top-level or _metadata block
        global_duration = get_metadata_field(summary, "global_overlap_duration_seconds", None)
    if global_duration is None:
        global_duration = 0.0
    try:
        global_duration = float(global_duration)
    except (TypeError, ValueError):
        global_duration = 0.0
    if global_duration < min_global_overlap_seconds:
        return False, f"overlap_{global_duration}s_below_min_{min_global_overlap_seconds}s"

    # 5. Schema version not in allowed set
    if allowed_schema_versions is not None:
        sv = get_metadata_field(summary, "schema_version", "")
        if sv not in allowed_schema_versions:
            return False, f"schema_version_{sv}_not_allowed"

    # 6. Schema version higher than reader supports
    sv_ok, sv_reason = check_schema_version(summary, allow_missing=True)
    if not sv_ok:
        return False, sv_reason

    # 7. Failed streams check
    if stream_strictness != "off":
        failed_streams = summary.get("failed_streams", [])
        if not failed_streams:
            # Also check inside overlap_info or _metadata
            failed_streams = overlap_info.get("failed_streams", [])
        if failed_streams:
            if stream_strictness == "strict":
                return False, f"failed_streams_{'_'.join(str(s) for s in failed_streams)}"
            # lenient: warn but accept

    # 8. Quarantined run_id
    if not include_quarantined and quarantine_run_ids:
        run_id = get_metadata_field(summary, "run_id", "")
        if not run_id:
            run_id = summary.get("run_id", "")
        if run_id and run_id in quarantine_run_ids:
            return False, f"quarantined_run_{run_id}"

    return True, None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Aggregate evaluation reports across multiple captures into a corpus summary."
    )
    set_legacy_prog(p, __name__)
    p.add_argument(
        "--report-dirs",
        type=str,
        default=None,
        help="Comma-separated list of report directories (each containing summary.json). "
        "Mutually compatible with --reports-root / --glob (both are merged).",
    )
    p.add_argument(
        "--reports-root",
        type=str,
        default="reports",
        help="Base directory for glob-based report discovery. Default: reports.",
    )
    p.add_argument(
        "--glob",
        type=str,
        default=None,
        help="Glob pattern for report discovery, e.g. derivatives_spot_lead_lag_v2_*. "
        "Used together with --reports-root.",
    )
    p.add_argument(
        "--min-global-overlap-seconds",
        type=float,
        default=600.0,
        help="Skip reports with global overlap below this threshold. Default: 600.",
    )
    p.add_argument(
        "--allowed-schema-versions",
        type=str,
        default=None,
        help="Comma-separated list of allowed schema versions (e.g. 1.0.0,v0). "
        "If unset, all supported versions are accepted.",
    )
    p.add_argument(
        "--stream-strictness",
        type=str,
        choices=["strict", "lenient", "off"],
        default="lenient",
        help="How to handle failed streams. strict=reject, lenient=warn, off=skip check. "
        "Default: lenient.",
    )
    p.add_argument(
        "--quarantine-file",
        type=str,
        default="reports/research_run_quarantine.jsonl",
        help="Path to quarantine JSONL file. Default: reports/research_run_quarantine.jsonl.",
    )
    p.add_argument(
        "--include-quarantined",
        action="store_true",
        default=False,
        help="Override to include quarantined reports.",
    )
    p.add_argument(
        "--allow-missing-git-sha",
        action="store_true",
        default=False,
        help="Allow reports without _metadata.git_sha to be included.",
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

    # --- Resolve report directories ---
    report_dirs: list[Path] = []

    # a) Explicit --report-dirs
    if args.report_dirs:
        report_dir_strs = [d.strip() for d in args.report_dirs.split(",") if d.strip()]
        report_dirs = [Path(d) for d in report_dir_strs]

    # b) Glob-based discovery via --reports-root + --glob
    if args.glob:
        discovered = discover_report_dirs(Path(args.reports_root), args.glob)
        # Merge discovered with explicit, deduplicating by resolved path
        existing_resolved = {rd.resolve() for rd in report_dirs}
        for d in discovered:
            if d.resolve() not in existing_resolved:
                report_dirs.append(d)
                existing_resolved.add(d.resolve())

    if not report_dirs:
        print(
            "ERROR: No report directories specified. Use --report-dirs or --reports-root + --glob.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate report directories
    for rd in report_dirs:
        if not rd.exists():
            print(f"ERROR: Report directory not found: {rd}", file=sys.stderr)
            sys.exit(1)

    # --- Parse filter / quarantine configuration ---
    allowed_schema_versions: set[str] | None = None
    if args.allowed_schema_versions:
        allowed_schema_versions = {
            v.strip() for v in args.allowed_schema_versions.split(",") if v.strip()
        }

    quarantine_run_ids: set[str] | None = None
    quarantine_path = Path(args.quarantine_file)
    if quarantine_path.exists():
        quarantine_run_ids = get_quarantined_run_ids(quarantine_path)
        if quarantine_run_ids:
            print(
                f"  Loaded {len(quarantine_run_ids)} quarantined run IDs from {quarantine_path}",
                file=sys.stderr,
            )

    # --- Filter reports ---
    filtered_dirs: list[Path] = []
    skipped_report_count = 0
    skipped_reasons: dict[str, list[str]] = {}

    for rd in report_dirs:
        ok, reason = filter_report_dir(
            rd,
            min_global_overlap_seconds=args.min_global_overlap_seconds,
            allowed_schema_versions=allowed_schema_versions,
            stream_strictness=args.stream_strictness,
            quarantine_run_ids=quarantine_run_ids,
            include_quarantined=args.include_quarantined,
            allow_missing_git_sha=args.allow_missing_git_sha,
        )
        if ok:
            filtered_dirs.append(rd)
        else:
            skipped_report_count += 1
            skipped_reasons.setdefault(reason or "unknown", []).append(str(rd))

    report_dirs = filtered_dirs

    if not report_dirs:
        print("ERROR: No report directories passed quality filtering.", file=sys.stderr)
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
        "skipped_report_count": skipped_report_count,
        "skipped_reasons": skipped_reasons,
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
        json.dump(_sanitize_for_json(json_data), f, indent=2, default=str)

    # Write Markdown
    md_content = format_corpus_report(aggregations)
    md_path = out_dir / "corpus_report.md"
    with open(md_path, "w") as f:
        f.write(md_content)

    print()
    print("=" * 60)
    print("CORPUS AGGREGATION COMPLETE")
    print(f"  Report dirs (included): {len(report_dirs)}")
    print(f"  Report dirs (skipped):  {skipped_report_count}")
    print(f"  Unique configs: {len(aggregations)}")
    print(f"  Output dir: {out_dir}")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    if skipped_reasons:
        print()
        print("  Skipped reports by reason:")
        for reason, dirs in sorted(skipped_reasons.items()):
            print(f"    {reason}: {len(dirs)}")
            for d in dirs:
                print(f"      - {d}")
    print("=" * 60)


if __name__ == "__main__":
    main()
