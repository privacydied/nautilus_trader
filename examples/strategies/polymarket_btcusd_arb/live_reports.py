"""Phase 2 live-data reports."""
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
    top_lines=[]
    top_lines.append("# Phase 2 Observer-Only Live Data Validation Report\n")
    top_lines.append(f"Run ID: {summary.get('run_id','?')}")
    top_lines.append(f"Branch: {summary.get('branch','?')}")
    top_lines.append(f"Market slug: {summary.get('market_slug','?')}")
    top_lines.append(f"Phase: 2 observer-only, no orders, no keys, public data only\n")
    top_lines.append(f"Evaluated events: {summary.get('evaluated_event_count',0)}")
    top_lines.append(f"Candidate count: {summary.get('candidate_count',0)}")
    top_lines.append(f"Rejection count: {summary.get('rejection_total',0)}")
    # Rejection reason table
    rej_counts=summary.get("rejection_counts",{})
    if rej_counts:
        top_lines.append("\n## Rejection Reasons\n")
        top_lines.append("| Reason | Count |")
        top_lines.append("|---|---|")
        for reason,count in sorted(rej_counts.items(),key=lambda x:-x[1]):
            top_lines.append(f"| {reason} | {count} |")
    else:
        top_lines.append("\nNo rejection records (possibly no evaluable events).")
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
    (d/"report.md").write_text("\n".join(top_lines))