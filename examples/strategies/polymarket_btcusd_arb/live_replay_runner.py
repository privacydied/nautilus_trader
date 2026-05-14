"""Replay CLI for Phase 2 captured data."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description="Replay Phase 2 captured observer data offline")
    p.add_argument("--capture-dir",type=str,required=True,help="Path to capture directory")
    p.add_argument("--report-dir",type=str,default=None,help="Path to report directory (defaults to reports/polymarket_btcusd_arb/live_observer/<run_id>)")
    args=p.parse_args()
    from .live_replay import replay_capture,write_replay_check
    from .config import PolymarketArbConfig
    config=PolymarketArbConfig()
    capture_dir=Path(args.capture_dir)
    result=replay_capture(capture_dir,config)
    # Determine report dir
    if args.report_dir:
        report_dir=Path(args.report_dir)
    else:
        # Derive run_id from capture dir name
        run_id=capture_dir.name
        report_dir=Path("reports/polymarket_btcusd_arb/live_observer")/run_id
    # Write updated replay check to report dir
    write_replay_check(run_id=capture_dir.name,replay_result=result,report_dir=report_dir)
    # Also update summary.json with accounting fields
    summary_path=report_dir/"summary.json"
    if summary_path.exists():
        summary=json.loads(summary_path.read_text())
        summary["accounting_contract"]=result.get("accounting_contract","grid_level: event × lookback × threshold")
        summary["accounting_note"]=result.get("accounting_note","")
        summary["replay_accounting_contract"]=result.get("accounting_contract")
        summary["replay_candidate_count"]=result.get("replay_candidate_count")
        summary["replay_grid_rejection_count"]=result.get("replay_grid_rejection_count")
        summary["replay_grid_rejection_counts"]=result.get("replay_grid_rejection_counts")
        summary["replay_evaluated_event_count"]=result.get("replay_evaluated_event_count")
        summary["replay_grid_evaluation_count"]=result.get("replay_grid_evaluation_count")
        summary["original_accounting_contract"]=result.get("original_accounting_contract")
        summary["grid_rejection_count_match"]=result.get("grid_rejection_count_match")
        summary_path.write_text(json.dumps(summary,indent=2,default=str))
    print(f"Replay written to {report_dir/'replay_check.json'}")
    print(f"Summary updated at {summary_path}")
    print(f"Candidate count match: {result.get('candidate_count_match')}")
    print(f"Grid rejection count match: {result.get('grid_rejection_count_match')}")
    print(f"Deterministic: {result.get('deterministic')}")
    if not result.get("deterministic",False):
        print("FAIL: replay is not deterministic",file=sys.stderr); return 1
    return 0

if __name__=="__main__":
    sys.exit(main())