"""
Latency diagnostics for cross-venue tick streams.

Pure functions module. No network, no live imports.

Analyses tick-stream timing to estimate inter-venue latency and
recommend viable sub-second signal horizons. Uses inter-tick gap
statistics, cross-correlation of aligned price series, and overlap
durations to produce a human-readable diagnostic report.

Dataclasses
-----------
StreamStats     – per-(venue, symbol) tick-stream timing statistics
OverlapResult   – pairwise cross-venue overlap and lead-lag summary
LatencyDiagnosticReport – top-level report with all pairwise results

All computations use only public market data and produce no execution signals.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from .artifact_metadata import build_metadata
from .tick_store import load_trades_jsonl


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StreamStats:
    """Per-stream tick timing statistics."""

    venue: str
    symbol: str
    tick_count: int
    first_ts_ns: int
    last_ts_ns: int
    median_inter_tick_gap_ns: float
    p95_inter_tick_gap_ns: float

    @property
    def duration_s(self) -> float:
        """Total stream duration in seconds."""
        return (self.last_ts_ns - self.first_ts_ns) / 1e9

    @property
    def median_gap_s(self) -> float:
        """Median inter-tick gap in seconds."""
        return self.median_inter_tick_gap_ns / 1e9

    @property
    def p95_gap_s(self) -> float:
        """P95 inter-tick gap in seconds."""
        return self.p95_inter_tick_gap_ns / 1e9


@dataclass
class OverlapResult:
    """Pairwise cross-venue overlap and lead-lag summary."""

    venue_a: str
    symbol_a: str
    venue_b: str
    symbol_b: str
    overlap_seconds: float
    a_lead_b_seconds: float | None
    sparse_warning_a: bool
    sparse_warning_b: bool
    misaligned_warning: bool
    sub_second_confidence: str  # one of 'high', 'medium', 'low', 'unsafe'


@dataclass
class LatencyDiagnosticReport:
    """Top-level latency diagnostic report."""

    streams: dict[str, StreamStats] = field(default_factory=dict)
    pairwise: list[OverlapResult] = field(default_factory=list)
    sub_second_horizon_recommendation: str = ""
    binance_perp_leads: bool | None = None
    coinbase_leads_kraken: bool | None = None
    sparse_streams: list[str] = field(default_factory=list)
    misaligned_streams: list[str] = field(default_factory=list)
    summary_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SPARSE_GAP_THRESHOLD_S = 10.0  # median gap > 10s considered sparse
_MISALIGNED_OVERLAP_S = 5.0     # overlap < 5s triggers misaligned warning
_MIN_TICKS_FOR_CORRELATION = 50
_MIN_BUCKETS_FOR_CORRELATION = 20


# ---------------------------------------------------------------------------
# load_stream_stats
# ---------------------------------------------------------------------------


def load_stream_stats(capture_dir: Path) -> dict[str, StreamStats]:
    """
    Load all trades JSONL files and compute per-(venue, symbol) statistics.

    Returns a dict keyed by ``"venue__symbol"`` strings.
    """
    capture_dir = Path(capture_dir)
    if not capture_dir.is_dir():
        return {}

    # Discover trades_*.jsonl files
    trades_files: list[Path] = sorted(
        f for f in capture_dir.iterdir()
        if f.name.startswith("trades_") and f.name.endswith(".jsonl")
    )

    # Group raw ticks by (venue, symbol)
    from .tick_models import TradeTickLite

    raw_ticks: dict[tuple[str, str], list[TradeTickLite]] = {}
    for fpath in trades_files:
        try:
            ticks = load_trades_jsonl(str(fpath))
        except Exception:
            continue
        for t in ticks:
            key = (t.venue, t.symbol)
            if key not in raw_ticks:
                raw_ticks[key] = []
            raw_ticks[key].append(t)

    # Merge and sort per key, then compute stats
    result: dict[str, StreamStats] = {}
    for (venue, symbol), ticks in raw_ticks.items():
        ticks.sort(key=lambda t: t.ts_event)

        n = len(ticks)
        if n == 0:
            continue

        first_ts = ticks[0].ts_event
        last_ts = ticks[-1].ts_event

        # Compute inter-tick gaps
        gaps_ns: list[float] = []
        for i in range(1, n):
            gap = ticks[i].ts_event - ticks[i - 1].ts_event
            if gap > 0:
                gaps_ns.append(float(gap))

        if gaps_ns:
            median_gap = statistics.median(gaps_ns)
            sorted_gaps = sorted(gaps_ns)
            p95_gap = _percentile(sorted_gaps, 95.0)
        else:
            median_gap = 0.0
            p95_gap = 0.0

        key_str = f"{venue}__{symbol}"
        result[key_str] = StreamStats(
            venue=venue,
            symbol=symbol,
            tick_count=n,
            first_ts_ns=first_ts,
            last_ts_ns=last_ts,
            median_inter_tick_gap_ns=median_gap,
            p95_inter_tick_gap_ns=p95_gap,
        )

    return result


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute percentile from a sorted list using linear interpolation."""
    if not sorted_values:
        return float("nan")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = p / 100.0 * (n - 1)
    lower = math.floor(rank)
    upper = lower + 1
    if upper >= n:
        return sorted_values[-1]
    frac = rank - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])


# ---------------------------------------------------------------------------
# compute_cross_correlation
# ---------------------------------------------------------------------------


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation coefficient between two equal-length lists."""
    n = len(xs)
    if n < 2:
        return float("nan")
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys, strict=False))
    var_x = sum((xi - mean_x) ** 2 for xi in xs)
    var_y = sum((yi - mean_y) ** 2 for yi in ys)
    denom = math.sqrt(var_x * var_y)
    if denom < 1e-30:
        return float("nan")
    return cov / denom


def compute_cross_correlation(
    ts_a: list[int],
    prices_a: list[float],
    ts_b: list[int],
    prices_b: list[float],
    bucket_ms: int,
) -> float | None:
    """
    Align two price series into time buckets and compute cross-correlation.

    Buckets both series at ``bucket_ms`` resolution, then computes Pearson
    correlation at lags from -5 to +5 buckets. Returns the lag **in seconds**
    at which the absolute correlation is maximised, or ``None`` if insufficient
    data.

    A positive lag means series A leads series B (A's price movement precedes
    B's).

    Parameters
    ----------
    ts_a, prices_a : sorted timestamps (ns) and prices for stream A
    ts_b, prices_b : sorted timestamps (ns) and prices for stream B
    bucket_ms : bucket width in milliseconds

    Returns
    -------
    float | None
        Lead of A over B in seconds, or None.
    """
    if len(ts_a) < _MIN_TICKS_FOR_CORRELATION or len(ts_b) < _MIN_TICKS_FOR_CORRELATION:
        return None
    if len(ts_a) != len(prices_a) or len(ts_b) != len(prices_b):
        return None
    if bucket_ms <= 0:
        return None

    bucket_ns = bucket_ms * 1_000_000  # ms to ns

    # Determine common time range
    start_ns = max(ts_a[0], ts_b[0])
    end_ns = min(ts_a[-1], ts_b[-1])
    if end_ns <= start_ns:
        return None

    # Create aligned bucket series
    def _bucket_series(ts: list[int], prices: list[float]) -> dict[int, float]:
        """Create bucket index -> last-seen-price mapping."""
        buckets: dict[int, float] = {}
        for t, p in zip(ts, prices, strict=False):
            if t < start_ns or t > end_ns:
                continue
            idx = (t - start_ns) // bucket_ns
            buckets[idx] = p
        return buckets

    buckets_a = _bucket_series(ts_a, prices_a)
    buckets_b = _bucket_series(ts_b, prices_b)

    if not buckets_a or not buckets_b:
        return None

    # Determine shared bucket index range
    all_indices = set(buckets_a.keys()) | set(buckets_b.keys())
    if not all_indices:
        return None

    max_idx = max(all_indices)
    min_idx = min(all_indices)

    # Create filled arrays: forward-fill within range
    def _fill(buckets: dict[int, float], max_bucket: int, min_bucket: int) -> list[float | None]:
        result: list[float | None] = []
        last_price: float | None = None
        for idx in range(min_bucket, max_bucket + 1):
            if idx in buckets:
                last_price = buckets[idx]
            result.append(last_price)
        return result

    series_a_maybe = _fill(buckets_a, max_idx, min_idx)
    series_b_maybe = _fill(buckets_b, max_idx, min_idx)

    # Find contiguous range where both are non-None
    start_idx = None
    end_idx = None
    for i, (a, b) in enumerate(zip(series_a_maybe, series_b_maybe, strict=False)):
        if a is not None and b is not None:
            if start_idx is None:
                start_idx = i
            end_idx = i

    if start_idx is None:
        return None

    series_a = [series_a_maybe[i] for i in range(start_idx, end_idx + 1)]  # type: ignore
    series_b = [series_b_maybe[i] for i in range(start_idx, end_idx + 1)]  # type: ignore

    if len(series_a) < _MIN_BUCKETS_FOR_CORRELATION:
        return None

    # Compute cross-correlation at lags -5 to +5
    best_corr = -2.0
    best_lag = 0

    for lag in range(-5, 6):
        if lag >= 0:
            a_seg = series_a[:len(series_a) - lag] if lag < len(series_a) else []
            b_seg = series_b[lag:] if lag < len(series_b) else []
        else:
            a_seg = series_a[-lag:] if -lag < len(series_a) else []
            b_seg = series_b[:len(series_b) + lag] if -lag < len(series_b) else []

        min_len = min(len(a_seg), len(b_seg))
        if min_len < _MIN_BUCKETS_FOR_CORRELATION:
            continue
        a_seg = a_seg[:min_len]
        b_seg = b_seg[:min_len]

        r = _pearson_r(a_seg, b_seg)
        if math.isfinite(r) and abs(r) > abs(best_corr):
            best_corr = r
            best_lag = lag

    if best_corr == -2.0:
        return None

    # Convert lag (in buckets) to seconds
    return best_lag * (bucket_ms / 1000.0)


# ---------------------------------------------------------------------------
# compute_overlap_stats
# ---------------------------------------------------------------------------


def compute_overlap_stats(
    stats_a: StreamStats,
    stats_b: StreamStats,
) -> OverlapResult:
    """Compute overlap and cross-venue lead-lag statistics for two streams."""
    # Compute overlap duration
    overlap_start = max(stats_a.first_ts_ns, stats_b.first_ts_ns)
    overlap_end = min(stats_a.last_ts_ns, stats_b.last_ts_ns)
    overlap_ns = max(overlap_end - overlap_start, 0)
    overlap_seconds = overlap_ns / 1e9

    # Detect sparse streams
    sparse_a = stats_a.median_gap_s > _SPARSE_GAP_THRESHOLD_S
    sparse_b = stats_b.median_gap_s > _SPARSE_GAP_THRESHOLD_S

    # Misalignment warning
    misaligned = overlap_seconds < _MISALIGNED_OVERLAP_S

    # Cross-correlation at multiple bucket sizes
    # Load the underlying tick data for correlation computation
    # We'll compute at 250ms, 1000ms, 5000ms buckets and use the best result
    a_lead_b: float | None = None

    # We can't load ticks here (pure function), so we leave a_lead_b as None
    # and let compute_latency_diagnostics fill it in with actual tick data.
    # For now, compute sub_second_confidence based on stream density.

    # Determine sub_second_confidence based on stream quality
    if misaligned:
        sub_second_confidence = "unsafe"
    elif sparse_a or sparse_b:
        sub_second_confidence = "low"
    elif (
        stats_a.median_gap_s < 0.5
        and stats_b.median_gap_s < 0.5
        and overlap_seconds > 300
    ):
        sub_second_confidence = "high"
    elif (
        stats_a.median_gap_s < 2.0
        and stats_b.median_gap_s < 2.0
        and overlap_seconds > 60
    ):
        sub_second_confidence = "medium"
    else:
        sub_second_confidence = "low"


    return OverlapResult(
        venue_a=stats_a.venue,
        symbol_a=stats_a.symbol,
        venue_b=stats_b.venue,
        symbol_b=stats_b.symbol,
        overlap_seconds=round(overlap_seconds, 2),
        a_lead_b_seconds=a_lead_b,
        sparse_warning_a=sparse_a,
        sparse_warning_b=sparse_b,
        misaligned_warning=misaligned,
        sub_second_confidence=sub_second_confidence,
    )


# ---------------------------------------------------------------------------
# compute_latency_diagnostics
# ---------------------------------------------------------------------------


def compute_latency_diagnostics(
    capture_dir: Path,
) -> LatencyDiagnosticReport:
    """Load all stream stats, compute pairwise diagnostics for BTC/ETH perp+spot pairs."""
    all_stats = load_stream_stats(capture_dir)
    if not all_stats:
        return LatencyDiagnosticReport(
            streams=all_stats,
            summary_notes=["No trade streams found in capture directory."],
        )

    # Load raw ticks for cross-correlation computation
    from .tick_models import TradeTickLite

    capture_dir = Path(capture_dir)
    trades_files: list[Path] = sorted(
        f for f in capture_dir.iterdir()
        if f.name.startswith("trades_") and f.name.endswith(".jsonl")
    )

    raw_ticks: dict[str, list[TradeTickLite]] = {}
    for fpath in trades_files:
        try:
            ticks = load_trades_jsonl(str(fpath))
        except Exception:
            continue
        for t in ticks:
            key = f"{t.venue}__{t.symbol}"
            if key not in raw_ticks:
                raw_ticks[key] = []
            raw_ticks[key].append(t)

    # Sort all tick streams by timestamp
    for key in raw_ticks:
        raw_ticks[key].sort(key=lambda t: t.ts_event)

    report = LatencyDiagnosticReport(streams=all_stats)
    pairwise: list[OverlapResult] = []
    binance_perp_leads: bool | None = None
    coinbase_leads_kraken: bool | None = None

    # Identify BTC and ETH perp+spot pairs
    # Pattern: look for (binance_perp, spot) pairs sharing the same asset
    perp_keys: list[str] = [k for k in all_stats if "binance_perp" in k.lower() or "perp" in all_stats[k].venue.lower()]
    spot_keys: list[str] = [k for k in all_stats if k not in perp_keys]

    for pk in perp_keys:
        ps = all_stats[pk]
        # Find matching spot streams with same asset
        asset = ps.symbol.split("/")[0].upper() if "/" in ps.symbol else ps.symbol.upper()
        for sk in spot_keys:
            ss = all_stats[sk]
            spot_asset = ss.symbol.split("/")[0].upper() if "/" in ss.symbol else ss.symbol.upper()
            if asset != spot_asset:
                continue

            overlap = compute_overlap_stats(ps, ss)

            # Compute cross-correlation at 1000ms
            pk_ticks = raw_ticks.get(pk, [])
            sk_ticks = raw_ticks.get(sk, [])
            if pk_ticks and sk_ticks:
                ts_a = [t.ts_event for t in pk_ticks]
                pr_a = [t.price for t in pk_ticks]
                ts_b = [t.ts_event for t in sk_ticks]
                pr_b = [t.price for t in sk_ticks]
                lead = compute_cross_correlation(ts_a, pr_a, ts_b, pr_b, bucket_ms=1000)
                overlap.a_lead_b_seconds = lead

            pairwise.append(overlap)

            # Track specific leads
            if ps.venue == "binance_perp" and overlap.a_lead_b_seconds is not None:
                binance_perp_leads = overlap.a_lead_b_seconds > 0

            if ps.venue == "coinbase" and ss.venue == "kraken" and overlap.a_lead_b_seconds is not None:
                coinbase_leads_kraken = overlap.a_lead_b_seconds > 0
            elif ss.venue == "coinbase" and ps.venue == "kraken" and overlap.a_lead_b_seconds is not None:
                # swap: B is coinbase, A is kraken → coinbase leads if a_lead_b < 0
                coinbase_leads_kraken = overlap.a_lead_b_seconds < 0

    # Also check coinbase vs kraken direct pairs (both spot)
    cb_keys = [k for k in spot_keys if all_stats[k].venue.lower() == "coinbase"]
    kr_keys = [k for k in spot_keys if all_stats[k].venue.lower() == "kraken"]

    for ck in cb_keys:
        cs = all_stats[ck]
        asset_c = cs.symbol.split("/")[0].upper() if "/" in cs.symbol else cs.symbol.upper()
        for kk in kr_keys:
            ks = all_stats[kk]
            asset_k = ks.symbol.split("/")[0].upper() if "/" in ks.symbol else ks.symbol.upper()
            if asset_c != asset_k:
                continue

            overlap = compute_overlap_stats(cs, ks)

            # Compute correlation at 1000ms
            ck_ticks = raw_ticks.get(ck, [])
            kk_ticks = raw_ticks.get(kk, [])
            if ck_ticks and kk_ticks:
                ts_c = [t.ts_event for t in ck_ticks]
                pr_c = [t.price for t in ck_ticks]
                ts_k = [t.ts_event for t in kk_ticks]
                pr_k = [t.price for t in kk_ticks]
                lead = compute_cross_correlation(ts_c, pr_c, ts_k, pr_k, bucket_ms=1000)
                overlap.a_lead_b_seconds = lead

                if lead is not None:
                    coinbase_leads_kraken = lead > 0

            pairwise.append(overlap)

    report.pairwise = pairwise
    report.binance_perp_leads = binance_perp_leads
    report.coinbase_leads_kraken = coinbase_leads_kraken

    # Identify sparse and misaligned streams
    sparse: list[str] = []
    misaligned: list[str] = []
    for key, ss in all_stats.items():
        if ss.median_gap_s > _SPARSE_GAP_THRESHOLD_S:
            sparse.append(key)
    for pw in pairwise:
        if pw.misaligned_warning:
            pair_key = f"{pw.venue_a}__{pw.symbol_a} vs {pw.venue_b}__{pw.symbol_b}"
            misaligned.append(pair_key)

    report.sparse_streams = sparse
    report.misaligned_streams = misaligned

    # Sub-second horizon recommendation
    sub_second_recs: list[str] = []
    if pairwise:
        best_confidence = min(pw.sub_second_confidence for pw in pairwise)
        confidence_order = {"high": 0, "medium": 1, "low": 2, "unsafe": 3}
        worst_rank = confidence_order.get(best_confidence, 3)
        if worst_rank <= 0:
            report.sub_second_horizon_recommendation = "sub_second_viable"
            sub_second_recs.append("All pairwise overlaps support sub-second horizons.")
        elif worst_rank == 1:
            report.sub_second_horizon_recommendation = "sub_second_possible_with_caveats"
            sub_second_recs.append("Some pairwise overlaps have medium confidence for sub-second horizons.")
        elif worst_rank == 2:
            report.sub_second_horizon_recommendation = "sub_second_not_recommended"
            sub_second_recs.append("Sparse or low-confidence streams — avoid sub-second horizons.")
        else:
            report.sub_second_horizon_recommendation = "insufficient_data"
            sub_second_recs.append("Severely misaligned or sparse data — cannot recommend sub-second horizons.")
    else:
        report.sub_second_horizon_recommendation = "no_pairs"
        sub_second_recs.append("No cross-venue pairs available for latency diagnostics.")

    notes: list[str] = []
    if binance_perp_leads is True:
        notes.append("Binance perp leads spot (perp price discovery confirmed).")
    elif binance_perp_leads is False:
        notes.append("Binance perp does NOT lead spot — unusual, investigate data quality.")
    if coinbase_leads_kraken is True:
        notes.append("Coinbase leads Kraken for same-asset pairs.")
    elif coinbase_leads_kraken is False:
        notes.append("Kraken leads Coinbase for same-asset pairs.")
    if sparse:
        notes.append(f"Sparse streams (median gap > {_SPARSE_GAP_THRESHOLD_S}s): {', '.join(sparse)}")
    if misaligned:
        notes.append(f"Misaligned overlaps (< {_MISALIGNED_OVERLAP_S}s): {', '.join(misaligned)}")
    notes.extend(sub_second_recs)

    report.summary_notes = notes
    return report


# ---------------------------------------------------------------------------
# format_latency_report
# ---------------------------------------------------------------------------


def format_latency_report(report: LatencyDiagnosticReport) -> str:
    """Format a latency diagnostic report as human-readable markdown."""
    lines: list[str] = [
        "# Latency Diagnostics Report",
        "",
        "## Safety",
        "",
        "**Public data observer only. No auth. No orders. No execution.**",
        "",
        "## Stream Statistics",
        "",
    ]

    if not report.streams:
        lines.append("No trade streams found.")
    else:
        lines.append("| Stream | Venue | Symbol | Ticks | Duration (s) | Median Gap (s) | P95 Gap (s) |")
        lines.append("|--------|--------|--------|-------|---------------|----------------|--------------|")
        for key, ss in sorted(report.streams.items()):
            lines.append(
                f"| {key} | {ss.venue} | {ss.symbol} | {ss.tick_count} "
                f"| {ss.duration_s:.1f} | {ss.median_gap_s:.3f} | {ss.p95_gap_s:.3f} |"
            )

    lines.append("")
    lines.append("## Pairwise Overlap & Lead-Lag")
    lines.append("")

    if not report.pairwise:
        lines.append("No cross-venue pairs found.")
    else:
        lines.append(
            "| Pair A | Pair B | Overlap (s) | A Leads B (s) | Sparse A | Sparse B "
            "| Misaligned | Sub-s Confidence |"
        )
        lines.append(
            "|--------|--------|-------------|----------------|----------|----------"
            "|-----------|-------------------|"
        )
        for pw in report.pairwise:
            lead_str = f"{pw.a_lead_b_seconds:.3f}" if pw.a_lead_b_seconds is not None else "N/A"
            lines.append(
                f"| {pw.venue_a}/{pw.symbol_a} | {pw.venue_b}/{pw.symbol_b} "
                f"| {pw.overlap_seconds:.1f} | {lead_str} "
                f"| {'Yes' if pw.sparse_warning_a else 'No'} "
                f"| {'Yes' if pw.sparse_warning_b else 'No'} "
                f"| {'Yes' if pw.misaligned_warning else 'No'} "
                f"| {pw.sub_second_confidence} |"
            )

    lines.append("")
    lines.append("## Sub-Second Horizon Recommendation")
    lines.append("")
    lines.append(f"**{report.sub_second_horizon_recommendation or 'N/A'}**")
    lines.append("")

    if report.binance_perp_leads is not None:
        lines.append(
            f"- Binance perp leads spot: **{'Yes' if report.binance_perp_leads else 'No'}**"
        )
    if report.coinbase_leads_kraken is not None:
        lines.append(
            f"- Coinbase leads Kraken: **{'Yes' if report.coinbase_leads_kraken else 'No'}**"
        )

    lines.append("")
    lines.append("## Summary Notes")
    lines.append("")
    for note in report.summary_notes:
        lines.append(f"- {note}")

    # Sparse and misaligned streams
    if report.sparse_streams:
        lines.append("")
        lines.append("### Sparse Streams")
        for s in report.sparse_streams:
            lines.append(f"- {s}")
    if report.misaligned_streams:
        lines.append("")
        lines.append("### Misaligned Overlaps")
        for s in report.misaligned_streams:
            lines.append(f"- {s}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# write_latency_diagnostics
# ---------------------------------------------------------------------------


def write_latency_diagnostics(
    capture_dir: Path,
    out_dir: Path,
) -> Path:
    """
    Run latency diagnostics and write JSON + markdown report.

    Parameters
    ----------
    capture_dir : Path
        Directory containing trades_*.jsonl files.
    out_dir : Path
        Directory to write outputs (created if missing).

    Returns
    -------
    Path
        Path to the output directory.
    """
    capture_dir = Path(capture_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = compute_latency_diagnostics(capture_dir)

    # Build metadata
    meta = build_metadata(capture_mode="", run_args=None)

    # Write JSON
    json_data: dict[str, Any] = {
        "_metadata": meta,
        "streams": {k: _stream_stats_to_dict(v) for k, v in report.streams.items()},
        "pairwise": [_overlap_to_dict(pw) for pw in report.pairwise],
        "sub_second_horizon_recommendation": report.sub_second_horizon_recommendation,
        "binance_perp_leads": report.binance_perp_leads,
        "coinbase_leads_kraken": report.coinbase_leads_kraken,
        "sparse_streams": report.sparse_streams,
        "misaligned_streams": report.misaligned_streams,
        "summary_notes": report.summary_notes,
    }

    json_path = out_dir / "latency_diagnostics.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2, default=str)

    # Write Markdown
    md_path = out_dir / "latency_diagnostics_report.md"
    md_content = format_latency_report(report)
    with open(md_path, "w") as f:
        f.write(md_content)

    return out_dir


def _stream_stats_to_dict(ss: StreamStats) -> dict[str, Any]:
    """Convert StreamStats to a plain dict for JSON serialisation."""
    return {
        "venue": ss.venue,
        "symbol": ss.symbol,
        "tick_count": ss.tick_count,
        "first_ts_ns": ss.first_ts_ns,
        "last_ts_ns": ss.last_ts_ns,
        "median_inter_tick_gap_ns": ss.median_inter_tick_gap_ns,
        "p95_inter_tick_gap_ns": ss.p95_inter_tick_gap_ns,
        "duration_s": ss.duration_s,
        "median_gap_s": ss.median_gap_s,
        "p95_gap_s": ss.p95_gap_s,
    }


def _overlap_to_dict(pw: OverlapResult) -> dict[str, Any]:
    """Convert OverlapResult to a plain dict for JSON serialisation."""
    return {
        "venue_a": pw.venue_a,
        "symbol_a": pw.symbol_a,
        "venue_b": pw.venue_b,
        "symbol_b": pw.symbol_b,
        "overlap_seconds": pw.overlap_seconds,
        "a_lead_b_seconds": pw.a_lead_b_seconds,
        "sparse_warning_a": pw.sparse_warning_a,
        "sparse_warning_b": pw.sparse_warning_b,
        "misaligned_warning": pw.misaligned_warning,
        "sub_second_confidence": pw.sub_second_confidence,
    }
