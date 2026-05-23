from __future__ import annotations
import csv,json
from dataclasses import asdict,is_dataclass
from pathlib import Path
from .models import ReportPaths
def _d(o):
    if is_dataclass(o): return asdict(o)
    if isinstance(o,Path): return str(o)
    return str(o)
def make_report_paths(root:Path,run_id:str):
    d=root/run_id; d.mkdir(parents=True,exist_ok=True); return ReportPaths(d,d/'summary.json',d/'candidates.jsonl',d/'rejections.jsonl',d/'baseline.jsonl',d/'candidate_groups.csv',d/'report.md',d/'safety_check.json',d/'parity_check.json',d/'cache_metadata.json')
def write_reports(paths,*,summary,candidates,rejections,baseline,groups,safety,parity,cache_metadata):
    paths.summary_json.write_text(json.dumps(summary,indent=2,sort_keys=True,default=_d))
    for p,rows in [(paths.candidates_jsonl,candidates),(paths.rejections_jsonl,rejections),(paths.baseline_jsonl,baseline)]:
        with p.open('w') as f:
            for r in rows: f.write(json.dumps(r,default=_d,sort_keys=True)+'\n')
    fields=['lookback_ns','threshold_bps','tte_bucket','verdict','reason','candidate_count','mean_edge_bps','median_edge_bps','win_rate','baseline_mean_edge_bps','beats_baseline_bps']
    with paths.candidate_groups_csv.open('w',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader(); [wr.writerow({k:asdict(g).get(k) for k in fields}) for g in groups]
    paths.safety_check_json.write_text(json.dumps(safety,indent=2,sort_keys=True)); paths.parity_check_json.write_text(json.dumps(parity,indent=2,sort_keys=True)); paths.cache_metadata_json.write_text(json.dumps(cache_metadata,indent=2,sort_keys=True,default=_d)); paths.report_md.write_text(render_markdown(summary,groups))
def render_markdown(summary,groups):
    g=groups[0] if groups else None
    top=(f'Across 0 candidate events at threshold {g.threshold_bps:g} bps in tte-bucket {g.tte_bucket}, no events met the threshold. Verdict: NEEDS_MORE_DATA.' if g and g.candidate_count==0 else (f'Across {g.candidate_count} candidate events at threshold {g.threshold_bps:g} bps in tte-bucket {g.tte_bucket}, the mean maker-fee-adjusted forward edge at horizon {summary.get("primary_horizon_ns",0)} was {(g.mean_edge_bps or 0):.4f} bps, beating random baseline by {(g.beats_baseline_bps or 0):.4f} bps. Verdict: {g.verdict}.' if g else 'Across 0 candidate events at threshold 0 bps in tte-bucket none, no events met the threshold. Verdict: NEEDS_MORE_DATA.'))
    names=['Phase statement','Git branch statement','Hypothesis','What was ported from arb-bot','What was intentionally discarded','Data sources','Cache status','Look-ahead protection statement','Fee model statement','YES-token-only v1 statement','Pagination/truncation statement','Candidate summary table','Zero-candidate grid cell summary','Rejection reason table','Forward outcome table','Random baseline comparison','Verdict table','Limitations','Next Step']
    out=[top,'']
    for n in names: out += [f'## {n}', section(n,summary,groups),'']
    return '\n'.join(out)
def section(n,summary,groups):
    if n=='Git branch statement': return f"Current branch: {summary.get('branch_name')}"
    if n=='YES-token-only v1 statement': return 'V1 evaluates YES token only; NO token availability is documented but NO-side economics are not analyzed.'
    if n=='Fee model statement': return 'Maker-first verdict uses PolymarketFeeModel availability and maker-fee survival gate with crypto maker rebates enabled.'
    if n=='Look-ahead protection statement': return 'sanitize_info=True is required; settlement payoff is unavailable before expiry-crossing horizons.'
    if n=='Verdict table': return '\n'.join(f'- {g.lookback_ns} × {g.threshold_bps} × {g.tte_bucket}: {g.verdict} ({g.reason})' for g in groups)
    return json.dumps({k:summary.get(k) for k in ('market_slug','candidate_count','zero_candidate_grid_cell_count','limitations')},default=str)
