"""MCPT export adapter for venue_agnostic_signal_observer.

Pure functions only. No network, no capture, no live imports.

This module decides whether MCPT is warranted for a given report,
selects candidate groups, and exports their event return series as
clean CSV files ready for Monte Carlo Permutation Testing.

MCPT is a falsification tool: it tests whether a signal's observed
return distribution could have arisen by chance. It is only meaningful
when a candidate or near-candidate group exists to falsify.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .artifact_metadata import build_metadata, get_metadata, get_metadata_field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MIN_EVENTS = 30
DEFAULT_COST_FLOOR_BPS = 50.0
DEFAULT_MAX_GROUPS = 3


# ---------------------------------------------------------------------------
# is_mcpt_worthy_group
# ---------------------------------------------------------------------------

def is_mcpt_worthy_group(
    *,
    mean_net_bps: float | None = None,
    median_net_bps: float | None = None,
    win_rate: float | None = None,
    baseline_mean_net_bps: float | None = None,
    baseline_win_rate: float | None = None,
    valid_count: int | None = None,
    candidate: bool | None = None,
    rejection_reasons: list[str] | None = None,
    cost_floor_bps: float = DEFAULT_COST_FLOOR_BPS,
    min_events: int = DEFAULT_MIN_EVENTS,
) -> tuple[bool, str]:
    """Decide whether MCPT is worth running on a group.

    Returns (worthy, reason).

    Note: FAST_DIAGNOSTIC captures cannot produce final REJECTED verdicts
    by design. This function does not enforce that here — it is a pure
    statistical gate. The capture-mode verdict remapping happens upstream
    in run_derivatives_spot_lead_lag.py.
    """
    # --- Hard skip: too few events ---
    count = valid_count if valid_count is not None else 0
    if count < min_events:
        return False, f"valid_count={count} < min_events={min_events}"

    # --- Hard skip: NaN/inf in key stats ---
    if mean_net_bps is not None and not math.isfinite(mean_net_bps):
        return False, "mean_net_bps is not finite"
    if median_net_bps is not None and not math.isfinite(median_net_bps):
        return False, "median_net_bps is not finite"

    # --- Candidate groups are always MCPT-worthy ---
    if candidate is True:
        return True, "candidate_group"

    # --- Verdict-based fast path ---
    # If rejection reasons indicate clear structural failure, skip.
    if rejection_reasons:
        reasons_str = "; ".join(rejection_reasons)
        # Groups rejected for fundamentally negative net returns near cost floor
        if "mean_net_return_not_positive" in reasons_str and mean_net_bps is not None:
            if mean_net_bps <= -(cost_floor_bps * 0.5):
                return False, f"mean_net={mean_net_bps:.2f} bps, deeply negative (near cost floor)"

    # --- Net bps gate ---
    mnb = mean_net_bps if mean_net_bps is not None else 0.0

    # Positive net returns after costs -> MCPT-worthy
    if mnb > 0:
        return True, f"positive_net_bps={mnb:.2f}"

    # Near-breakeven groups that beat baseline -> MCPT-worthy
    if baseline_mean_net_bps is not None and math.isfinite(baseline_mean_net_bps):
        # Allow groups within 10 bps of breakeven that clearly beat baseline
        if mnb > -(cost_floor_bps * 0.2) and mnb > baseline_mean_net_bps + 5.0:
            return True, f"near_breakeven_but_beats_baseline: net={mnb:.2f} vs baseline={baseline_mean_net_bps:.2f}"

    # Everything else is cost-floor dust (covers mnb <= 0 OR baseline check not passed)
    return False, f"non_positive_net_bps={mnb:.2f}, below cost floor"


# ---------------------------------------------------------------------------
# select_mcpt_candidate_groups
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupKey:
    """Unique identifier for a result group to avoid duplicate variants."""
    source_venue: str
    target_venue: str
    signal_type: str
    lookback_ms: int
    horizon_ms: int


def select_mcpt_candidate_groups(
    summary_rows: list[dict[str, Any]],
    *,
    max_groups: int = DEFAULT_MAX_GROUPS,
    cost_floor_bps: float = DEFAULT_COST_FLOOR_BPS,
    min_events: int = DEFAULT_MIN_EVENTS,
) -> list[dict[str, Any]]:
    """Select at most *max_groups* best candidate/near-candidate groups.

    Sort priority: candidate=True first, then by mean_net_bps desc,
    then by win_rate desc, then by valid_count desc.

    After sorting, deduplicate by (signal_type, lookback_ms) so we
    don't select 3 horizons of the same signal variant.
    """
    worthy: list[dict[str, Any]] = []
    for row in summary_rows:
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=row.get("mean_net_bps"),
            median_net_bps=row.get("median_net_bps"),
            win_rate=row.get("win_rate"),
            baseline_mean_net_bps=row.get("baseline_mean_net_bps"),
            baseline_win_rate=row.get("baseline_win_rate"),
            valid_count=row.get("valid_count"),
            candidate=row.get("candidate"),
            rejection_reasons=row.get("rejection_reasons"),
            cost_floor_bps=cost_floor_bps,
            min_events=min_events,
        )
        if ok:
            row_copy = dict(row)
            row_copy["_mcpt_reason"] = reason
            worthy.append(row_copy)

    # Sort: candidate groups first, then by mean_net_bps desc, win_rate desc, count desc
    def _sort_key(r: dict) -> tuple:
        is_cand = 0 if r.get("candidate") is True else 1
        mnb_raw = r.get("mean_net_bps")
        mnb = mnb_raw if mnb_raw is not None else 0.0
        if not math.isfinite(mnb):
            mnb = -1e9
        wr_raw = r.get("win_rate")
        wr = wr_raw if wr_raw is not None else 0.0
        if not math.isfinite(wr):
            wr = 0.0
        vc_raw = r.get("valid_count")
        vc = vc_raw if vc_raw is not None else 0
        return (is_cand, -mnb, -wr, -vc)

    worthy.sort(key=_sort_key)

    # Deduplicate by (signal_type, lookback_ms) — keep best horizon per variant
    seen_variants: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []
    for row in worthy:
        sig_type = str(row.get("signal_type", ""))
        lb = int(row.get("lookback_ms", 0))
        variant = (sig_type, lb)
        if variant in seen_variants:
            continue
        seen_variants.add(variant)
        result.append(row)
        if len(result) >= max_groups:
            break

    return result


# ---------------------------------------------------------------------------
# export_mcpt_candidate_series
# ---------------------------------------------------------------------------

_MCPT_CSV_FIELDS = [
    "event_timestamp_ns",
    "source_venue",
    "source_symbol",
    "target_venue",
    "target_symbol",
    "asset",
    "signal_type",
    "flow_signal_type",
    "lookback_ms",
    "horizon_ms",
    "oi_bucket",
    "direction",
    "raw_return_bps",
    "net_return_bps",
    "strength",
    "source_move_bps",
    "fee_bps",
    "slippage_bps",
    "quote_mismatch_buffer_bps",
    "valid",
    "signal_id",
]


def _load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of dicts."""
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_summary_json(path: Path) -> dict:
    """Load summary.json."""
    with open(path) as f:
        return json.load(f)


def _slugify(text: str) -> str:
    """Simple slugifier for file paths."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def export_mcpt_candidate_series(
    *,
    report_dir: Path,
    candidate_group: dict[str, Any],
    out_dir: Path | None = None,
) -> Path:
    """Export one candidate group's event return series as a CSV file.

    Given the report directory (containing summary.json, signals.jsonl,
    forward_returns.jsonl) and a selected candidate group dict, writes a
    clean CSV with the fields in _MCPT_CSV_FIELDS.

    Returns the output file path.
    """
    report_dir = Path(report_dir)

    summary = _load_summary_json(report_dir / "summary.json")
    capture_slug = _slugify(Path(summary.get("capture_dir", "unknown")).name)

    # Build group-identifying fields from the candidate dict
    src_v = candidate_group.get("source_venue", "unknown")
    tgt_v = candidate_group.get("target_venue", "unknown")
    sig_type = candidate_group.get("signal_type", "unknown")
    lb = candidate_group.get("lookback_ms", 0)
    hz = candidate_group.get("horizon_ms", 0)

    group_slug = f"{src_v}_{tgt_v}_{sig_type}_{lb}ms_{hz}ms"
    candidate_slug = _slugify(group_slug)

    if out_dir is None:
        out_dir = report_dir / "mcpt_inputs"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{candidate_slug}.csv"

    # Load signals and forward returns
    signals = _load_jsonl(report_dir / "signals.jsonl")
    fwd_returns = _load_jsonl(report_dir / "forward_returns.jsonl")

    # Index signals by signal_id for fast lookups
    signals_by_id: dict[str, dict] = {}
    for sig in signals:
        sid = sig.get("signal_id", "")
        if sid:
            signals_by_id[sid] = sig

    # Filter forward returns to this group: matching signal_type, lookback, horizon
    # AND valid=True AND net_return_bps is finite
    matched_rows: list[dict] = []
    for fr in fwd_returns:
        if not fr.get("valid", False):
            continue
        net_bps = fr.get("net_return_bps")
        if net_bps is None or not math.isfinite(net_bps):
            continue
        if fr.get("horizon_ms") != hz:
            continue

        sid = fr.get("signal_id", "")
        sig = signals_by_id.get(sid, {})

        # Match on flow_signal_type (in metadata) matching our signal_type
        meta = sig.get("metadata") or {}
        flow_type = meta.get("flow_signal_type", sig.get("signal_type", ""))
        sig_lb = meta.get("lookback_ms", sig.get("lookback_ms", 0))

        if flow_type != sig_type:
            continue
        if int(sig_lb) != int(lb):
            continue

        row = {
            "event_timestamp_ns": sig.get("ts_event", fr.get("signal_ts", "")),
            "source_venue": sig.get("source_venue", src_v),
            "source_symbol": sig.get("source_symbol", ""),
            "target_venue": fr.get("target_venue", tgt_v),
            "target_symbol": fr.get("target_symbol", ""),
            "asset": sig.get("asset", ""),
            "signal_type": sig.get("signal_type", sig_type),
            "flow_signal_type": flow_type,
            "lookback_ms": sig_lb,
            "horizon_ms": fr.get("horizon_ms", hz),
            "oi_bucket": meta.get("oi_bucket", ""),
            "direction": sig.get("direction", ""),
            "raw_return_bps": fr.get("raw_return_bps", ""),
            "net_return_bps": net_bps,
            "strength": sig.get("strength", ""),
            "source_move_bps": sig.get("source_move_bps", ""),
            "fee_bps": fr.get("fee_bps", ""),
            "slippage_bps": fr.get("slippage_bps", ""),
            "quote_mismatch_buffer_bps": fr.get("quote_mismatch_buffer_bps", ""),
            "valid": fr.get("valid", True),
            "signal_id": sid,
        }
        matched_rows.append(row)

    # Write CSV
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_MCPT_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in matched_rows:
            w.writerow(row)

    return out_path


def export_mcpt_summary(
    *,
    report_dir: Path,
    selected_groups: list[dict[str, Any]],
    all_groups: list[dict[str, Any]],
    skipped_reason: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Write mcpt_export_summary.json.

    Records whether MCPT was skipped, why, and which groups were selected.
    """
    report_dir = Path(report_dir)
    if out_dir is None:
        out_dir = report_dir / "mcpt_inputs"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load source summary to propagate capture_mode and metadata
    source_meta: dict[str, Any] = {}
    source_summary: dict[str, Any] = {}
    source_summary_path = report_dir / "summary.json"
    if source_summary_path.exists():
        try:
            with open(source_summary_path) as f:
                source_summary = json.load(f)
            source_meta = get_metadata(source_summary)
        except Exception:
            pass  # Backward compat: old summaries without metadata are fine

    capture_mode = get_metadata_field(source_summary, "capture_mode", "")
    if not capture_mode:
        # Fallback: read capture_mode from top-level summary dict (pre-metadata format)
        capture_mode = source_summary.get("capture_mode", "")

    meta = build_metadata(
        capture_mode=capture_mode,
        run_args=None,
    )

    summary_data: dict[str, Any] = {
        "_metadata": meta,
        "report_dir": str(report_dir),
        "mcpt_skipped": skipped_reason is not None,
        "skip_reason": skipped_reason,
        "total_groups": len(all_groups),
        "mcpt_worthy_count": len(selected_groups),
        "selected_groups": [],
    }

    for g in selected_groups:
        entry = {
            "source_venue": g.get("source_venue", ""),
            "target_venue": g.get("target_venue", ""),
            "signal_type": g.get("signal_type", ""),
            "lookback_ms": g.get("lookback_ms", 0),
            "horizon_ms": g.get("horizon_ms", 0),
            "valid_count": g.get("valid_count", 0),
            "mean_net_bps": g.get("mean_net_bps"),
            "median_net_bps": g.get("median_net_bps"),
            "win_rate": g.get("win_rate"),
            "candidate": g.get("candidate", False),
            "_mcpt_reason": g.get("_mcpt_reason", ""),
        }
        summary_data["selected_groups"].append(entry)

    out_path = out_dir / "mcpt_export_summary.json"
    with open(out_path, "w") as f:
        json.dump(summary_data, f, indent=2, default=str)

    return out_path