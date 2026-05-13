"""Phase 2 live-data reports.

Reports include explicit accounting contract declaration and separate
pre-grid and grid-level rejection counts.
"""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

def write_live_report(report_dir:Path,*,summary:dict,signals:list,rejections:list,groups:list,safety:dict,replay:dict,cache_meta:dict|None=None)->None:
    d=Path(report_dir); d.mkdir(parents=True,exist_ok=True)
    (d/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    with open(d/"signals.jsonl","w") as f:
        for s in signals: f.write(json.dumps(asdict(s) if hasattr(s,"__dataclass_fields__") else s,default=str)+"\n")
    with open(d/"rejections.jsonl","w") as f:
        for s in rejections: f.write(json.dumps(asdict(s) if hasattr(s,"__dataclass_fields__") else s,default=str)+"\n")
    import csv
    with open(d/"candidate_groups.csv","w",newline="") as f:
        if groups:
            w=csv.DictWriter(f,fieldnames=list(groups[0].keys())); w.writeheader(); w.writerows(groups)
    (d/"safety_check.json").write_text(json.dumps(safety,indent=2,default=str))
    (d/"replay_check.json").write_text(json.dumps(replay,indent=2,default=str))
    if cache_meta: (d/"cache_metadata.json").write_text(json.dumps(cache_meta,indent=2,default=str))
    # Determine top rejection reason
    grid_rej=summary.get("grid_rejection_counts") or {}
    pre_grid=summary.get("pre_grid_rejection_counts") or {}
    legacy_rej=summary.get("rejection_counts") or {}
    all_rej={**grid_rej,**pre_grid} if (grid_rej or pre_grid) else legacy_rej
    top_rejection_reason=max(all_rej,key=all_rej.get) if all_rej else "none"
    top_lines=[]
    top_lines.append("# Phase 2 Observer-Only Live Data Validation Report\n")
    top_lines.append(f"Run ID: {summary.get('run_id','?')}")
    top_lines.append(f"Branch: {summary.get('branch','?')}")
    top_lines.append(f"Market slug: {summary.get('market_slug','?')}")
    top_lines.append(f"Phase: 2 observer-only, no orders, no keys, public data only\n")
    top_lines.append(f"Accounting contract: {summary.get('accounting_contract','not specified')}")
    top_lines.append(f"Accounting note: {summary.get('accounting_note','')}\n")
    top_lines.append(f"Event count (poll attempts): {summary.get('event_count',0)}")
    top_lines.append(f"Evaluated event count (passed to grid): {summary.get('evaluated_event_count',0)}")
    top_lines.append(f"Grid evaluation count (event × lookback × threshold): {summary.get('grid_evaluation_count',0)}")
    top_lines.append(f"Grid candidate count: {summary.get('grid_candidate_count',0)}")
    top_lines.append(f"Grid rejection count: {summary.get('grid_rejection_count',0)}")
    top_lines.append(f"Pre-grid rejection count: {summary.get('pre_grid_rejection_count',0)}")
    top_lines.append(f"Total rejection count: {summary.get('rejection_total',0)}\n")
    # Pre-grid rejection reasons
    if pre_grid:
        top_lines.append("## Pre-grid Rejection Reasons (event-level, not grid-evaluated)\n")
        top_lines.append("| Reason | Count |")
        top_lines.append("|---|---|")
        for reason,count in sorted(pre_grid.items(),key=lambda x:-x[1]):
            top_lines.append(f"| {reason} | {count} |")
    # Grid-level rejection reasons
    if grid_rej:
        top_lines.append("\n## Grid-level Rejection Reasons (event × lookback × threshold)\n")
        top_lines.append("| Reason | Count |")
        top_lines.append("|---|---|")
        for reason,count in sorted(grid_rej.items(),key=lambda x:-x[1]):
            top_lines.append(f"| {reason} | {count} |")
    for g in groups:
        tte=g.get("tte_bucket","?")
        th=g.get("threshold_bps","?")
        lb=g.get("lookback_ns","?")
        count=g.get("candidate_count",0)
        verdict=g.get("verdict","?")
        reason=g.get("reason","?")
        mean_edge=g.get("mean_edge_bps")
        top_lines.append(f"\nLookback {lb}ns, threshold {th}bps, TTE {tte}: {count} candidates, verdict={verdict}, reason={reason}, mean_edge={mean_edge}")
    top_lines.append(f"\nBinance stale rate: {summary.get('binance_stale_rate','N/A')}")
    top_lines.append(f"Polymarket stale rate: {summary.get('poly_stale_rate','N/A')}")
    top_lines.append(f"Safety check: {safety.get('ok',False)}")
    top_lines.append(f"Replay deterministic: {replay.get('deterministic','?')}")
    if replay.get("accounting_contract"):
        top_lines.append(f"Replay accounting contract: {replay.get('accounting_contract')}")
    if replay.get("candidate_count_match") is not None:
        top_lines.append(f"Replay candidate count match: {replay.get('candidate_count_match')}")
    if replay.get("grid_rejection_count_match") is not None and replay.get("grid_rejection_count_match") is not None:
        top_lines.append(f"Replay grid rejection count match: {replay.get('grid_rejection_count_match')}")
    elif replay.get("grid_rejection_count_match") is None:
        top_lines.append("Replay grid rejection count match: not_applicable (legacy capture used event-level accounting)")
    # Verdict
    total_candidates=summary.get("grid_candidate_count",0)
    if total_candidates>0:
        top_lines.append(f"\n## Verdict\nCANDIDATE_FOR_LONGER_OBSERVATION — {total_candidates} candidates found in live observation window.")
    elif summary.get("evaluated_event_count",0)==0:
        top_lines.append("\n## Verdict\nNEEDS_MORE_DATA — no evaluable events in live observation window.")
    else:
        top_lines.append(f"\n## Verdict\nNEEDS_MORE_DATA — {summary.get('evaluated_event_count',0)} events evaluated but 0 candidates in this live observation window. Dominant blocker: {top_rejection_reason}. The Phase 1 backtest signal did not produce candidates in this one 900s live observation window. This is one observation, not a global rejection of the hypothesis. Do not proceed to Phase 3.")
    top_lines.append("\n## Conclusion\nDo not call NEEDS_MORE_DATA a win. Do not recommend Phase 3 or execution from one 900s live window. Observe more markets and volatility regimes before making a global reject/continue decision.")
    # Legacy capture note
    original_accounting=replay.get("original_accounting_contract","")
    if original_accounting and "legacy" in original_accounting.lower():
        top_lines.append(f"\n## Accounting Legacy Note\nOriginal capture used `{original_accounting}` accounting. Grid rejection counts from replay cannot be directly compared to original event-level rejection counts. Future captures use grid-level accounting for full determinism.")
    (d/"report.md").write_text("\n".join(top_lines))