#!/usr/bin/env python3
"""Research report miner.

Walks all reports/ directories, extracts study results, and produces a
unified status table and bps-gate table.

**Research discipline only. No live trading. No orders. No execution.**
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StudyResult:
    study: str
    signal_type: str
    symbol: str
    source_venue: str
    target_venue: str
    horizon_ms: str
    total_signals: int = 0
    valid_events: int = 0
    rejected_events: int = 0
    gross_bps: float | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    latency_bps: float | None = None
    total_cost_bps: float | None = None
    net_bps: float | None = None
    median_bps: float | None = None
    win_rate: float | None = None
    baseline_net_bps: float | None = None
    beats_baseline: bool | None = None
    candidate: bool | None = None
    verdict: str = "UNKNOWN"
    rejection_reasons: list[str] = field(default_factory=list)
    source_file: str = ""
    sample_days: float | None = None


_PATH_PROJECT_MAP = [
    (re.compile(r"kraken_btcusd_research", re.I), "kraken_btcusd_v1_v4"),
    (re.compile(r"trade_flow_impulse", re.I), "trade_flow_impulse"),
    (re.compile(r"tick_lead_lag", re.I), "tick_lead_lag"),
    (re.compile(r"lead_lag_v1", re.I), "lead_lag_ohlcv_v1"),
    (re.compile(r"l2_maker_paper", re.I), "l2_maker_paper_v7"),
    (re.compile(r"v6_market_structure", re.I), "v6_funding_basis"),
    (re.compile(r"v7_l2_maker", re.I), "l2_maker_paper_v7"),
]


def _infer_project(path: str) -> str:
    for pat, name in _PATH_PROJECT_MAP:
        if pat.search(path):
            return name
    return "unknown"


def _parse_val(val, target: str = "float"):
    if val is None:
        return None
    if isinstance(val, bool):
        return val if target == "bool" else None
    if isinstance(val, int):
        return val if target == "float" else int(val)
    try:
        return float(val) if target == "float" else int(float(val))
    except (ValueError, TypeError):
        return None


def _get(d, keys, default=None):
    """Try multiple key names including dot-notation in a nested dict."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        parts = k.split(".")
        cur = d
        ok = True
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return default


def _normalize_verdict(text):
    if not text:
        return "UNKNOWN"
    t = text.upper()
    if "REJECT" in t:
        return "REJECTED"
    if "CANDIDATE" in t or "PASS" in t or "VIABLE" in t:
        return "CANDIDATE"
    if "NEED" in t or "MORE DATA" in t:
        return "NEEDS_MORE_DATA"
    return "UNKNOWN"


def _extract_study_from_path(path: str) -> str:
    p = Path(path)
    for i, part in enumerate(p.parts):
        if part == "reports" and i + 1 < len(p.parts):
            return p.parts[i + 1]
    return p.stem


def _make_result(r: StudyResult) -> StudyResult:
    """Apply verdict inference if not explicitly set."""
    if r.verdict == "UNKNOWN":
        if r.candidate is True:
            r.verdict = "CANDIDATE"
        elif r.net_bps is not None:
            r.verdict = "REJECTED"
        elif r.rejection_reasons:
            r.verdict = "REJECTED"
        elif r.total_signals == 0:
            r.verdict = "NEEDS_MORE_DATA"
    return r


def _process_json_report(path: str) -> list[StudyResult]:
    results: list[StudyResult] = []
    try:
        data = json.loads(Path(path).read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return results

    study = _extract_study_from_path(path)
    project = _infer_project(path)

    summary = None
    # Handle top-level "summary" or a flat summary dict
    if isinstance(data, dict):
        if "summary" in data and isinstance(data["summary"], dict):
            summary = data["summary"]
        elif "total_signals" in data or "valid_evaluations" in data or "results_by_group" in data:
            summary = data

    if summary is None or not isinstance(summary, dict):
        return results

    total_signals = _parse_val(summary.get("total_signals", summary.get("total_events")), "int") or 0
    valid_events = _parse_val(summary.get("valid_evaluations", summary.get("valid_events")), "int") or 0
    fee_bps = _parse_val(_get(summary, ["fee_bps"]))
    slippage_bps = _parse_val(_get(summary, ["slippage_bps"]))
    latency_bps = _parse_val(summary.get("quote_mismatch_buffer_bps"))

    total_cost = None
    if fee_bps is not None or slippage_bps is not None:
        total_cost = (fee_bps or 0) + (slippage_bps or 0) + (latency_bps or 0)

    baseline = data.get("baseline_results") if isinstance(data, dict) else None
    baseline_net = _parse_val(_get(baseline, ["mean_net_return_bps", "mean_net_bps"])) if baseline else None

    groups = []
    if isinstance(summary.get("results_by_group"), list):
        groups = summary["results_by_group"]
    elif isinstance(data.get("results_by_group"), list):
        groups = data["results_by_group"]

    candidate_groups = []
    if isinstance(summary.get("candidate_groups"), list):
        candidate_groups = summary["candidate_groups"]
    elif isinstance(data.get("candidate_groups"), list):
        candidate_groups = data["candidate_groups"]
    has_candidates = len(candidate_groups) > 0

    verdict_raw = ""
    if isinstance(data, dict):
        vr = summary.get("verdict", data.get("verdict", ""))
        verdict_raw = str(vr) if vr else ""

    verdict = _normalize_verdict(verdict_raw) if verdict_raw else ""

    if not groups and total_signals == 0 and not baseline:
        return results

    if groups:
        for g in groups:
            if not isinstance(g, dict):
                continue
            signal_type = g.get("signal_type", "")
            if not signal_type:
                sl = study.lower()
                if "lead_lag" in sl:
                    signal_type = "tick_lead_lag"
                elif "trade_flow" in sl:
                    signal_type = "trade_flow_impulse"
                elif "l2_maker" in sl:
                    signal_type = "l2_maker"
                elif "ohlcv" in sl or project == "lead_lag_ohlcv_v1":
                    signal_type = "ohlcv_lead_lag"

            gate = g.get("gate", {})
            if not isinstance(gate, dict):
                gate = {}

            net_val = g.get("mean_net_return_bps")
            v = verdict or ("CANDIDATE" if has_candidates else ("REJECTED" if _parse_val(net_val) is not None and _parse_val(net_val) < 0 else "UNKNOWN"))

            rr = gate.get("rejection_reasons", [])
            if not isinstance(rr, list):
                rr = []

            r = StudyResult(
                study=study,
                signal_type=signal_type,
                symbol=str(g.get("symbol", g.get("asset", g.get("base_asset", "")))),
                source_venue=str(g.get("source_venue", "")),
                target_venue=str(g.get("target_venue", "")),
                horizon_ms=str(g.get("horizon_ms", g.get("lookback_ms", ""))),
                total_signals=_parse_val(g.get("total_signals"), "int") or 0,
                valid_events=_parse_val(g.get("valid_events"), "int") or 0,
                rejected_events=_parse_val(g.get("rejected_events"), "int") or 0,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                latency_bps=latency_bps,
                total_cost_bps=total_cost,
                net_bps=_parse_val(net_val),
                median_bps=_parse_val(g.get("median_net_return_bps")),
                win_rate=_parse_val(g.get("win_rate")),
                baseline_net_bps=baseline_net,
                beats_baseline=_parse_val(gate.get("beats_baseline", g.get("beats_baseline"))),
                candidate=gate.get("candidate", g.get("candidate", has_candidates)),
                verdict=v,
                rejection_reasons=rr,
                source_file=path,
            )
            results.append(_make_result(r))
    else:
        # No groups — produce a single summary result
        r = StudyResult(
            study=study,
            signal_type=project if project != "unknown" else "unknown",
            symbol=str(_get(data, ["symbol", "asset"])),
            source_venue=str(summary.get("source_venue", "")),
            target_venue=str(summary.get("target_venue", "")),
            horizon_ms="",
            total_signals=total_signals,
            valid_events=valid_events,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            latency_bps=latency_bps,
            total_cost_bps=total_cost,
            net_bps=_parse_val(summary.get("mean_net_return_bps")),
            baseline_net_bps=baseline_net,
            verdict=verdict or ("NEEDS_MORE_DATA" if total_signals == 0 else "UNKNOWN"),
            source_file=path,
        )
        if r.total_signals > 0 or r.net_bps is not None:
            results.append(_make_result(r))

    return results


def _process_jsonl_file(path: str) -> list[StudyResult]:
    results: list[StudyResult] = []
    rows: list[dict] = []
    try:
        for line in Path(path).read_text().strip().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return results

    if not rows:
        return results

    study = _extract_study_from_path(path)
    project = _infer_project(path)
    sample = rows[0]
    symbol = str(sample.get("symbol", sample.get("asset", "")))
    source_venue = str(sample.get("source_venue", sample.get("venue", "")))
    target_venue = str(sample.get("target_venue", ""))

    net_values = []
    for row in rows:
        nv = _parse_val(
            row.get("net_return_bps")
            or row.get("net_edge_bps")
            or row.get("estimated_net_edge_bps")
        )
        if nv is not None:
            net_values.append(nv)

    if net_values:
        r = StudyResult(
            study=study,
            signal_type=project,
            symbol=symbol,
            source_venue=source_venue,
            target_venue=target_venue,
            horizon_ms="",
            total_signals=len(rows),
            valid_events=len(rows),
            net_bps=statistics.mean(net_values),
            median_bps=statistics.median(net_values),
            source_file=path,
        )
        results.append(_make_result(r))

    return results


def mine_reports(reports_dir: str = "reports") -> list[StudyResult]:
    results: list[StudyResult] = []
    reports_path = Path(reports_dir)
    if not reports_path.exists():
        return results

    for fpath in sorted(reports_path.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.name.startswith("research_status_summary"):
            continue
        suffix = fpath.suffix.lower()
        str_path = str(fpath)
        if suffix == ".json":
            results.extend(_process_json_report(str_path))
        elif suffix == ".jsonl":
            results.extend(_process_jsonl_file(str_path))

    # Deduplicate by (study, signal_type, symbol, source_venue, target_venue)
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[StudyResult] = []
    for r in results:
        key = (r.study, r.signal_type, r.symbol, r.source_venue, r.target_venue)
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


def format_rejected_md(results: list[StudyResult]) -> str:
    lines: list[str] = []
    lines.append("# Research Status Report (Mined)")
    lines.append("")
    lines.append("> Generated by research_report_miner.py. Observer-only, no execution.")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Study | Signal Type | Symbol | Venues | Signals | Valid | Net BPS | Win Rate | Verdict |")
    lines.append("|-------|-------------|--------|--------|---------|-------|---------|----------|---------|")

    verdict_order = {"CANDIDATE": 0, "UNKNOWN": 1, "NEEDS_MORE_DATA": 2, "REJECTED": 3}
    results_sorted = sorted(results, key=lambda r: (verdict_order.get(r.verdict, 99), r.study, r.signal_type))

    for r in results_sorted:
        venues = ""
        if r.source_venue and r.target_venue:
            venues = f"{r.source_venue}->{r.target_venue}"
        elif r.source_venue:
            venues = r.source_venue
        net_str = f"{r.net_bps:.2f}" if r.net_bps is not None else "-"
        win_str = f"{r.win_rate:.1%}" if r.win_rate is not None else "-"
        lines.append(
            f"| {r.study} | {r.signal_type} | {r.symbol or '-'} "
            f"| {venues or '-'} | {r.total_signals or '-'} "
            f"| {r.valid_events or '-'} | {net_str} | {win_str} | "
            f"{r.verdict} |"
        )

    lines.append("")
    lines.append("## Verdict Counts")
    lines.append("")
    verdicts: dict[str, int] = {}
    for r in results:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
    for v in ["REJECTED", "CANDIDATE", "NEEDS_MORE_DATA", "UNKNOWN"]:
        count = verdicts.get(v, 0)
        if count > 0:
            lines.append(f"- **{v}**: {count} study groups")

    lines.append("")
    lines.append("## Locked Gates")
    lines.append("")
    lines.append("1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4.")
    lines.append("2. **Same-asset cross-venue tick lead-lag** (CB<->KRK BTC/ETH). HFT-dominated. Rejected.")
    lines.append("3. **Naive top-of-book L2 maker** on BTC/ETH at current fee tier. Rejected.")
    lines.append("4. **Direct cash-and-carry** under tested Kraken spot + Binance/Bybit perp cost model. Rejected.")

    lines.append("")
    lines.append("## Rejection Reasons")
    lines.append("")
    all_reasons: dict[str, int] = {}
    for r in results:
        for reason in r.rejection_reasons:
            key = reason.split(":")[0].strip().lower() if ":" in reason else reason.lower()
            all_reasons[key] = all_reasons.get(key, 0) + 1
    for reason, count in sorted(all_reasons.items(), key=lambda x: -x[1]):
        lines.append(f"- **{reason}**: {count} groups")

    lines.append("")
    return "\n".join(lines)


def format_bps_gate_table(results: list[StudyResult]) -> str:
    lines: list[str] = []
    lines.append("# BPS Gate Table")
    lines.append("")
    lines.append("| Study | Signal | Symbol | Venues | Gross | Fee | Slip | Net | Baseline | Win% | Events | Cand? |")
    lines.append("|-------|--------|--------|--------|-------|-----|------|-----|----------|------|--------|-------|")

    for r in sorted(results, key=lambda x: x.net_bps if x.net_bps is not None else -999, reverse=True):
        venues = f"{r.source_venue}->{r.target_venue}" if r.source_venue and r.target_venue else ""
        gross = f"{r.gross_bps:.2f}" if r.gross_bps is not None else "-"
        fee = f"{r.fee_bps:.1f}" if r.fee_bps is not None else "-"
        slip = f"{r.slippage_bps:.1f}" if r.slippage_bps is not None else "-"
        net = f"{r.net_bps:.2f}" if r.net_bps is not None else "-"
        baseline = f"{r.baseline_net_bps:.2f}" if r.baseline_net_bps is not None else "-"
        win = f"{r.win_rate:.1%}" if r.win_rate is not None else "-"
        cand = "YES" if r.candidate else "NO" if r.candidate is not None else "-"
        lines.append(
            f"| {r.study} | {r.signal_type} | {r.symbol or '-'} | {venues or '-'} "
            f"| {gross} | {fee} | {slip} | {net} | {baseline} | {win} "
            f"| {r.valid_events or '-'} | {cand} |"
        )

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Mine research reports")
    parser.add_argument("--reports", default="reports", help="Reports dir")
    parser.add_argument("--output", default="reports/research_status", help="Output prefix")
    args = parser.parse_args()

    results = mine_reports(args.reports)
    if not results:
        print("No study results found.")
        return

    gate_md = format_bps_gate_table(results)
    Path(f"{args.output}_bps_gate.md").write_text(gate_md)
    print(f"Written: {args.output}_bps_gate.md")

    status_md = format_rejected_md(results)
    Path(f"{args.output}_status.md").write_text(status_md)
    print(f"Written: {args.output}_status.md")

    csv_path = Path(f"{args.output}_table.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "study", "signal_type", "symbol", "source_venue", "target_venue",
            "total_signals", "valid_events", "net_bps", "win_rate", "verdict",
            "rejection_reasons"
        ])
        for r in sorted(results, key=lambda x: (x.verdict != "CANDIDATE", x.study)):
            writer.writerow([
                r.study, r.signal_type, r.symbol, r.source_venue, r.target_venue,
                r.total_signals, r.valid_events,
                r.net_bps if r.net_bps is not None else "",
                r.win_rate if r.win_rate is not None else "",
                r.verdict,
                "; ".join(r.rejection_reasons[:5])
            ])
    print(f"Written: {args.output}_table.csv")

    print(f"\nMined {len(results)} unique study groups from {args.reports}/")
    verdicts: dict[str, int] = {}
    for r in results:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
    for v in ["REJECTED", "CANDIDATE", "NEEDS_MORE_DATA", "UNKNOWN"]:
        if v in verdicts:
            print(f"  {v}: {verdicts[v]}")


if __name__ == "__main__":
    main()
