#!/usr/bin/env python3
"""Research report miner.

Walks the repo's reports/ directory, reads JSON/JSONL/CSV/MD files, and
produces a unified bps-gated research-status table.

**No live trading. No orders. No API keys. Observer only.**
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclass
class ResearchRecord:
    source_file: str = ""
    project: str = ""
    study_name: str = ""
    symbol: str = ""
    source_venue: str = ""
    target_venue: str = ""
    signal_type: str = ""
    horizon: str = ""
    events_count: int | None = None
    gross_bps: float | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    latency_buffer_bps: float | None = None
    quote_mismatch_bps: float | None = None
    net_bps: float | None = None
    win_rate: float | None = None
    random_baseline_bps: float | None = None
    beats_baseline: bool | None = None
    verdict: str = ""
    rejection_reason: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_KEYWORDS = [
    ("signal_observer", "venue_agnostic_signal_observer"),
    ("trade_flow_impulse", "venue_agnostic_signal_observer"),
    ("tick_lead_lag", "venue_agnostic_signal_observer"),
    ("lead_lag_v1", "venue_agnostic_signal_observer"),
    ("v7_l2_maker_paper", "kraken_l2_maker_paper"),
    ("v6_market_structure", "kraken_market_structure_scanner"),
    ("funding_basis", "kraken_market_structure_scanner"),
    ("kraken_btcusd", "kraken_btcusd_research"),
]

_BPS_KEYS = [
    "mean_net_return_bps", "net_return_bps", "mean_net_bps",
    "net_edge_bps", "gross_edge_bps", "gross_bps",
    "avg_mean_net_return_bps", "median_net_return_bps",
    "entry_spread_bps", "exit_spread_bps", "fee_bps", "slippage_bps",
    "latency_buffer_bps", "quote_mismatch_buffer_bps",
    "conservative_net_edge_bps", "mixed_net_edge_bps", "optimistic_net_edge_bps",
    "max_net_return_bps", "total_cost_bps",
]

_EVENT_KEYS = [
    "events_count", "event_count", "total_signals", "valid_events",
    "total_events",
]

_VERDICT_KEYS = ["verdict", "candidate", "is_candidate", "is_profitable"]

_WIN_RATE_KEYS = [
    "win_rate", "win_rate_percent",
]

_VERDICT_NORMALIZE = [
    (r"CANDIDATE[_\s]*FOR[_\s]*LONGER", "CANDIDATE_FOR_LONGER_OBSERVATION"),
    (r"NEEDS[_\s]*MORE[_\s]*DATA", "NEEDS_MORE_DATA"),
    (r"REJECTED", "REJECTED"),
    (r"PASS", "PASS"),
    (r"VIABLE", "VIABLE"),
]


def _detect_project(filepath: str) -> str:
    fp = filepath.lower()
    for keyword, proj in _PROJECT_KEYWORDS:
        if keyword in fp:
            return proj
    return "unknown"


def _study_name_from_path(filepath: str) -> str:
    """Infer study name from path components."""
    p = Path(filepath)
    # e.g. reports/signal_observer_tick_lead_lag_v3/...
    parts = p.parts
    if len(parts) >= 2:
        # The directory under reports/ is usually the study name
        reports_idx = next((i for i, p in enumerate(parts) if p == "reports"), -1)
        if reports_idx >= 0 and reports_idx + 1 < len(parts):
            return p.parts[reports_idx + 1]
    return p.stem


def _parse_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _normalize_verdict(text: str) -> str:
    if not text:
        return "UNKNOWN"
    up = text.upper()
    for pattern, verdict in _VERDICT_NORMALIZE:
        if re.search(pattern, up):
            return verdict
    return "UNKNOWN"


def _pick(obj: dict | list, keys: list[str]):
    """Return the value of the first key found in obj, or None."""
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj:
            return obj[k]
    return None


def _record_from_dict(
    row: dict,
    source_file: str,
    project: str,
    study_name: str,
) -> ResearchRecord:
    """Extract known fields from a flat or nested dict."""
    r = ResearchRecord(
        source_file=source_file,
        project=project,
        study_name=study_name,
    )
    # Venue / symbol / type
    r.symbol = row.get("symbol", row.get("asset", ""))
    r.source_venue = row.get("source_venue", row.get("venue", ""))
    r.target_venue = row.get("target_venue", "")
    r.signal_type = row.get("signal_type", row.get("flow_signal_type", ""))

    # If signal_type is empty, try to infer from study_name
    if not r.signal_type:
        sn = study_name.lower()
        if "lead_lag" in sn:
            r.signal_type = "tick_lead_lag"
        elif "trade_flow" in sn:
            r.signal_type = "trade_flow_impulse"
        elif "l2_maker" in sn:
            r.signal_type = "l2_maker"
        elif "funding" in sn or "basis" in sn:
            r.signal_type = "funding_basis"
        elif "spread" in sn or "scanner" in sn:
            r.signal_type = "spread_scanner"
        elif "btcusd" in sn or "backtest" in sn:
            r.signal_type = "ohlc_signal"

    # Horizon
    r.horizon = str(row.get("horizon_ms", row.get("horizon", "")))
    if r.horizon == "0" or r.horizon == "":
        r.horizon = str(row.get("lookback_ms", ""))

    # Numeric fields — first pass through row top-level
    r.events_count = _parse_int(_pick(row, _EVENT_KEYS))
    r.gross_bps = _parse_float(row.get("gross_bps",))
    gp = _pick(row, _BPS_KEYS)
    if r.gross_bps is None and gp:
        r.gross_bps = _parse_float(gp)
    r.net_bps = _parse_float(row.get("net_bps", row.get("net_return_bps",)))
    r.fee_bps = _parse_float(row.get("fee_bps",))
    r.slippage_bps = _parse_float(row.get("slippage_bps",))
    r.latency_buffer_bps = _parse_float(row.get("latency_buffer_bps",))
    r.quote_mismatch_bps = _parse_float(row.get("quote_mismatch_buffer_bps",))
    r.win_rate = _parse_float(_pick(row, _WIN_RATE_KEYS))
    if r.win_rate is not None and r.win_rate > 1.0:
        r.win_rate = r.win_rate / 100.0  # percent -> fraction

    # Random baseline
    r.random_baseline_bps = _parse_float(row.get("baseline_mean_net_bps",
        row.get("random_baseline_bps", row.get("baseline_mean_net_return_bps",))))

    r.beats_baseline = row.get("beats_baseline", row.get("candidate", None))
    if r.beats_baseline is not None:
        r.beats_baseline = bool(r.beats_baseline)

    # Verdict
    verdict_raw = _pick(row, _VERDICT_KEYS)
    if verdict_raw is not None:
        r.verdict = _normalize_verdict(str(verdict_raw))
    else:
        # Try to derive from other fields
        if r.net_bps is not None and r.events_count is not None and r.events_count >= 50:
            # This is not enough — we don't declare verdict on data alone
            pass
        elif row.get("candidate") is True:
            r.verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"

    # Rejection reason
    rr = row.get("rejection_reason", row.get("reason_if_rejected", ""))
    if isinstance(rr, list):
        rr = "; ".join(rr)
    r.rejection_reason = str(rr)

    return r


# ---------------------------------------------------------------------------
# File parsers
# ---------------------------------------------------------------------------

def _parse_json_file(
    filepath: str,
    records: list[ResearchRecord],
    warnings: list[str],
) -> None:
    project = _detect_project(filepath)
    study = _study_name_from_path(filepath)

    try:
        data = json.loads(Path(filepath).read_text())
    except json.JSONDecodeError as e:
        warnings.append(f"{filepath}: JSON parse error: {e}")
        return

    _extract_records(data, filepath, project, study, records, warnings, depth=0)


def _extract_records(
    obj,
    filepath: str,
    project: str,
    study: str,
    records: list[ResearchRecord],
    warnings: list[str],
    depth: int,
    max_depth: int = 6,
) -> None:
    """Recursively walk JSON to find result-bearing dicts."""
    if depth > max_depth:
        return

    if isinstance(obj, dict):
        # This dict is a candidate if it has BPS keys or event keys
        keys_present = set(obj.keys())
        has_bps = any(k in keys_present for k in _BPS_KEYS)
        has_events = any(k in keys_present for k in _EVENT_KEYS)
        has_verdict = any(k in keys_present for k in _VERDICT_KEYS)
        has_source = "source_venue" in keys_present or "target_venue" in keys_present

        if has_bps or has_events or has_verdict or has_source:
            rec = _record_from_dict(dict(obj), filepath, project, study)
            if rec.verdict or rec.net_bps is not None or rec.events_count is not None:
                records.append(rec)
            else:
                # Also recurse into child dicts for nested summaries
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        _extract_records(v, filepath, project, study, records, warnings, depth + 1)

        else:
            # Recurse into values
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    _extract_records(v, filepath, project, study, records, warnings, depth + 1)

    elif isinstance(obj, list):
        for item in obj:
            _extract_records(item, filepath, project, study, records, warnings, depth + 1)


def _parse_jsonl_file(
    filepath: str,
    records: list[ResearchRecord],
    warnings: list[str],
) -> None:
    project = _detect_project(filepath)
    study = _study_name_from_path(filepath)

    try:
        text = Path(filepath).read_text()
    except Exception as e:
        warnings.append(f"{filepath}: read error: {e}")
        return

    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"{filepath}:{lineno}: JSON parse error")
            continue
        if isinstance(row, dict):
            rec = _record_from_dict(row, filepath, project, study)
            if rec.net_bps is not None or rec.events_count is not None:
                records.append(rec)


def _parse_csv_file(
    filepath: str,
    records: list[ResearchRecord],
    warnings: list[str],
) -> None:
    project = _detect_project(filepath)
    study = _study_name_from_path(filepath)

    try:
        text = Path(filepath).read_text()
    except Exception as e:
        warnings.append(f"{filepath}: read error: {e}")
        return

    reader = csv.DictReader(text.splitlines())
    for row in reader:
        rec = _record_from_dict(dict(row), filepath, project, study)
        if rec.net_bps is not None or rec.events_count is not None:
            records.append(rec)


def _parse_md_file(
    filepath: str,
    records: list[ResearchRecord],
    warnings: list[str],
) -> None:
    """Extract verdict info from markdown reports (simple regex only)."""
    project = _detect_project(filepath)
    study = _study_name_from_path(filepath)

    try:
        text = Path(filepath).read_text()
    except Exception as e:
        warnings.append(f"{filepath}: read error: {e}")
        return

    # Look for verdict
    verdict = "UNKNOWN"
    for m in re.finditer(r"(?i)\b(REJECTED|NEEDS_MORE_DATA|CANDIDATE[_\s]*FOR[_\s]*LONGER)\b", text):
        verdict = _normalize_verdict(m.group(1))
        break

    # Try to find net return figures
    net_match = re.search(r"(-?\d+\.?\d*)\s*bps", text)
    net_val = _parse_float(net_match.group(1)) if net_match else None

    rec = ResearchRecord(
        source_file=filepath,
        project=project,
        study_name=study,
        verdict=verdict,
        net_bps=net_val,
    )
    records.append(rec)


# ---------------------------------------------------------------------------
# Miner entry point
# ---------------------------------------------------------------------------

SUPPORTED_EXTS = {".json", ".jsonl", ".csv", ".md"}
# Skip summary files we produce ourselves to avoid double-counting
_SKIP_NAMES = {"research_status_summary.json", "research_status_summary.csv", "research_status_summary.md"}


def mine_reports(reports_dir: str) -> tuple[list[ResearchRecord], list[str]]:
    """Walk reports_dir and extract ResearchRecords from supported files.

    Returns (records, warnings).
    """
    records: list[ResearchRecord] = []
    warnings: list[str] = []

    base = Path(reports_dir)
    if not base.is_dir():
        warnings.append(f"Reports directory does not exist: {reports_dir}")
        return records, warnings

    for fpath in sorted(base.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.name in _SKIP_NAMES:
            continue
        ext = fpath.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue

        sfp = str(fpath)
        if ext == ".json":
            _parse_json_file(sfp, records, warnings)
        elif ext == ".jsonl":
            _parse_jsonl_file(sfp, records, warnings)
        elif ext == ".csv":
            _parse_csv_file(sfp, records, warnings)
        elif ext == ".md":
            _parse_md_file(sfp, records, warnings)

    # De-duplicate by (study, symbol, source_venue, target_venue, signal_type, net_bps)
    seen: set[tuple] = set()
    deduped: list[ResearchRecord] = []
    for r in records:
        key = (r.study_name, r.symbol, r.source_venue, r.target_venue,
               r.signal_type, r.horizon,
               round(r.net_bps or 0, 4), r.events_count or 0)
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped, warnings


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "source_file", "project", "study_name", "symbol",
    "source_venue", "target_venue", "signal_type", "horizon",
    "events_count", "gross_bps", "fee_bps", "slippage_bps",
    "latency_buffer_bps", "quote_mismatch_bps", "net_bps",
    "win_rate", "random_baseline_bps", "beats_baseline",
    "verdict", "rejection_reason", "warnings",
]


def _write_json(records: list[ResearchRecord], warnings: list[str], out_dir: Path) -> None:
    out = out_dir / "research_status_summary.json"
    obj = {
        "records": [asdict(r) for r in records],
        "warnings": warnings,
        "record_count": len(records),
    }
    with open(out, "w") as f:
        json.dump(obj, f, indent=2, default=str)


def _write_csv(records: list[ResearchRecord], out_dir: Path) -> None:
    out = out_dir / "research_status_summary.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in records:
            row = asdict(r)
            row["warnings"] = "; ".join(r.warnings) if r.warnings else ""
            writer.writerow(row)


def _write_md(records: list[ResearchRecord], warnings: list[str], out_dir: Path) -> None:
    out = out_dir / "research_status_summary.md"
    lines: list[str] = [
        "# Research Status Summary",
        "",
        "> **This is a research-status report, not a trading recommendation.**",
        "> Verdicts reflect past study outputs, not current market conditions.",
        "> Do not trade based on this document.",
        "",
        f"**Records mined:** {len(records)}  ",
        f"**Warnings:** {len(warnings)}  ",
        "",
    ]

    # Group by verdict
    by_verdict: dict[str, list[ResearchRecord]] = {}
    for r in records:
        v = r.verdict or "UNKNOWN"
        by_verdict.setdefault(v, []).append(r)

    for verdict_key in ["CANDIDATE_FOR_LONGER_OBSERVATION", "REJECTED", "NEEDS_MORE_DATA", "PASS", "UNKNOWN"]:
        group = by_verdict.get(verdict_key, [])
        if not group:
            continue
        lines.append(f"## {verdict_key} ({len(group)})")
        lines.append("")
        lines.append("| Study | Symbol | Signal | Venues | Net bps | Events | Win Rate |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in group:
            venues = f"{r.source_venue}→{r.target_venue}" if r.source_venue and r.target_venue else ""
            lines.append(
                f"| {r.study_name} | {r.symbol or ''} | {r.signal_type or ''} "
                f"| {venues} | {r.net_bps if r.net_bps is not None else '—'} "
                f"| {r.events_count or '—'} | {r.win_rate if r.win_rate is not None else '—'} |"
            )
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings[:50]:
            lines.append(f"- {w}")
        lines.append("")

    with open(out, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine research reports and produce a unified status table."
    )
    parser.add_argument(
        "--reports", type=str, default="reports",
        help="Path to reports directory (default: reports)",
    )
    parser.add_argument(
        "--out-dir", type=str, default=None,
        help="Output directory (default: same as --reports)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.reports)
    out_dir.mkdir(parents=True, exist_ok=True)

    records, warnings = mine_reports(args.reports)

    _write_json(records, warnings, out_dir)
    _write_csv(records, out_dir)
    _write_md(records, warnings, out_dir)

    print(f"Mined {len(records)} records, {len(warnings)} warnings.")
    print(f"Output: {out_dir}/research_status_summary.json/csv/md")


if __name__ == "__main__":
    main()
