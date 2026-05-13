"""Phase 2B observer-only campaign runner.

Orchestrates multiple live observer windows using existing Phase 2 components.
Does not create orders, keys, execution clients, or on-chain calls.
"""
from __future__ import annotations
import argparse,json,subprocess,sys,time,logging
from pathlib import Path
from dataclasses import dataclass,field,asdict

log=logging.getLogger(__name__)

def discover_markets():
    """Discover active BTC 15m UpDown markets using existing Phase 2 discovery."""
    from .live_market_discovery import discover_btc_15m_updown
    return discover_btc_15m_updown()

def run_single_window(capture_dir:Path,report_dir:Path,duration_seconds:int,threshold_grid:str,lookback_grid:str|None=None,market_slug:str|None=None,max_markets:int=1)->dict:
    """Run one observer window using the existing Phase 2 CLI."""
    cmd=[sys.executable,"-m","examples.strategies.polymarket_btcusd_arb.live_public_observer",
         "--duration-seconds",str(duration_seconds),
         "--threshold-grid",threshold_grid,
         "--capture-dir",str(capture_dir),
         "--report-dir",str(report_dir),
         "--max-markets",str(max_markets)]
    if market_slug:
        cmd+=["--market-slug",market_slug]
    if lookback_grid:
        cmd+=["--lookback-grid",lookback_grid]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=duration_seconds+300)
    return {"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr}

def build_campaign_summary(campaign_id:str,windows:list,markets_observed:list,start_time:float,end_time:float,params:dict,safety:dict)->dict:
    """Aggregate campaign-level summary from window results."""
    completed=sum(1 for w in windows if w.get("completed"))
    failed=sum(1 for w in windows if not w.get("completed"))
    total_candidates=sum(w.get("candidate_count",0) for w in windows)
    total_grid_rejections=sum(w.get("grid_rejection_count",0) for w in windows)
    rej_by_reason={}
    for w in windows:
        for reason,count in w.get("rejection_counts",{}).items():
            rej_by_reason[reason]=rej_by_reason.get(reason,0)+count
    windows_with_candidates=sum(1 for w in windows if w.get("candidate_count",0)>0)
    windows_with_zero_candidates=sum(1 for w in windows if w.get("candidate_count",0)==0 and w.get("completed"))
    spread_rate=sum(w.get("grid_rejection_counts",{}).get("spread_too_wide",0) for w in windows)/max(total_grid_rejections,1) if total_grid_rejections>0 else 0.0
    stale_rate=sum(w.get("grid_rejection_counts",{}).get("stale_or_missing_binance",0) for w in windows)/max(total_grid_rejections,1) if total_grid_rejections>0 else 0.0
    all_replay_ok=all(w.get("replay_candidate_match",False) and w.get("replay_grid_rejection_match",False) for w in windows if w.get("completed"))
    # Verdict logic
    if completed==0:
        verdict="NEEDS_MORE_DATA"
        reason="no_windows_completed"
    elif windows_with_candidates>0:
        verdict="CANDIDATE_FOR_LONGER_OBSERVATION"
        reason=f"{windows_with_candidates}_of_{completed}_windows_produced_candidates"
    elif completed>=3 and windows_with_zero_candidates==completed:
        verdict="REJECTED_FOR_CURRENT_LIVE_CONDITIONS"
        reason=f"all_{completed}_windows_zero_candidates_dominant_blocker_spread_too_wide"
    else:
        verdict="NEEDS_MORE_DATA"
        reason=f"{windows_with_zero_candidates}_of_{completed}_windows_zero_candidates_insufficient_sample"
    return {
        "campaign_id":campaign_id,
        "branch":"polymarket-btcusd-arb-phase2b-observer-campaign",
        "start_time_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(start_time)),
        "end_time_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(end_time)),
        "requested_windows":params.get("requested_windows",0),
        "completed_windows":completed,
        "failed_windows":failed,
        "duration_seconds_per_window":params.get("duration_seconds",0),
        "thresholds":params.get("thresholds",""),
        "lookbacks":params.get("lookbacks",""),
        "markets_observed":markets_observed,
        "total_candidates":total_candidates,
        "total_grid_rejections":total_grid_rejections,
        "rejection_counts_by_reason":rej_by_reason,
        "spread_too_wide_rate":round(spread_rate,4),
        "stale_or_missing_binance_rate":round(stale_rate,4),
        "windows_with_candidates":windows_with_candidates,
        "windows_with_zero_candidates":windows_with_zero_candidates,
        "all_replay_checks_passed":all_replay_ok,
        "safety_check_status":safety,
        "verdict":verdict,
        "verdict_reason":reason,
        "recommendation":"Do not recommend Phase 3 or execution. Continue observer-only campaigns across more markets and volatility regimes.",
        "limitations":["Observer-only. No execution.","Single market type (BTC 15m UpDown).","Limited observation windows.","Do not call NEEDS_MORE_DATA a win.","Do not globally reject hypothesis from limited sample."],
    }

def write_campaign_report(report_dir:Path,summary:dict,windows:list)->None:
    """Write campaign report files."""
    report_dir.mkdir(parents=True,exist_ok=True)
    (report_dir/"campaign_summary.json").write_text(json.dumps(summary,indent=2,default=str))
    # window_results.csv
    import csv
    with open(report_dir/"window_results.csv","w",newline="") as f:
        if windows:
            keys=["window_idx","market_slug","completed","evaluated_events","candidate_count","grid_rejection_count","spread_too_wide","stale_or_missing_binance","replay_candidate_match","replay_grid_rejection_match","accounting_contract"]
            w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
            for i,win in enumerate(windows):
                w.writerow({"window_idx":i,"market_slug":win.get("market_slug","?"),"completed":win.get("completed",False),"evaluated_events":win.get("evaluated_event_count",0),"candidate_count":win.get("candidate_count",0),"grid_rejection_count":win.get("grid_rejection_count",0),"spread_too_wide":win.get("grid_rejection_counts",{}).get("spread_too_wide",0),"stale_or_missing_binance":win.get("grid_rejection_counts",{}).get("stale_or_missing_binance",0),"replay_candidate_match":win.get("replay_candidate_match",False),"replay_grid_rejection_match":win.get("replay_grid_rejection_match",False),"accounting_contract":win.get("accounting_contract","")})
    # campaign_report.md
    lines=["# Phase 2B Observer-Only Campaign Report\n"]
    lines.append(f"Campaign ID: {summary['campaign_id']}")
    lines.append(f"Branch: {summary['branch']}")
    lines.append(f"Phase: 2B observer-only campaign. No execution. No keys. No orders. Public data only.\n")
    lines.append(f"## Parameters")
    lines.append(f"Windows requested: {summary['requested_windows']}")
    lines.append(f"Duration per window: {summary['duration_seconds_per_window']}s")
    lines.append(f"Thresholds: {summary['thresholds']}")
    lines.append(f"Markets observed: {summary['markets_observed']}\n")
    lines.append(f"## Results")
    lines.append(f"Windows completed: {summary['completed_windows']}")
    lines.append(f"Windows failed: {summary['failed_windows']}")
    lines.append(f"Windows with candidates: {summary['windows_with_candidates']}")
    lines.append(f"Windows with zero candidates: {summary['windows_with_zero_candidates']}")
    lines.append(f"Total candidates: {summary['total_candidates']}")
    lines.append(f"Total grid rejections: {summary['total_grid_rejections']}")
    lines.append(f"Spread-too-wide rate: {summary['spread_too_wide_rate']:.1%}" if isinstance(summary['spread_too_wide_rate'],float) else f"Spread-too-wide rate: {summary['spread_too_wide_rate']}")
    lines.append(f"Stale Binance rate: {summary['stale_or_missing_binance_rate']:.1%}" if isinstance(summary['stale_or_missing_binance_rate'],float) else f"Stale Binance rate: {summary['stale_or_missing_binance_rate']}\n")
    lines.append(f"## Rejection Summary")
    lines.append("| Reason | Count |")
    lines.append("|---|---|")
    for reason,count in sorted(summary['rejection_counts_by_reason'].items(),key=lambda x:-x[1]):
        lines.append(f"| {reason} | {count} |")
    lines.append(f"\n## Replay Determinism")
    lines.append(f"All replay checks passed: {summary['all_replay_checks_passed']}")
    for i,win in enumerate(windows):
        lines.append(f"Window {i}: candidate_match={win.get('replay_candidate_match','?')}, grid_rejection_match={win.get('replay_grid_rejection_match','?')}\n")
    lines.append(f"## Dominant Blocker")
    if summary['rejection_counts_by_reason']:
        top=max(summary['rejection_counts_by_reason'],key=summary['rejection_counts_by_reason'].get)
        lines.append(f"{top} ({summary['rejection_counts_by_reason'][top]} of {summary['total_grid_rejections']} grid rejections)\n")
    lines.append(f"## Safety")
    lines.append(f"Safety check: {summary['safety_check_status']}\n")
    lines.append(f"## Verdict")
    lines.append(f"{summary['verdict']}: {summary['verdict_reason']}\n")
    lines.append(f"## Limitations")
    for lim in summary['limitations']:
        lines.append(f"- {lim}")
    lines.append(f"\n## Recommendation")
    lines.append(summary['recommendation'])
    (report_dir/"campaign_report.md").write_text("\n".join(lines))
    # safety_check.json
    (report_dir/"safety_check.json").write_text(json.dumps(summary['safety_check_status'],indent=2,default=str))

def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p=argparse.ArgumentParser(description="Phase 2B observer-only campaign runner")
    p.add_argument("--windows",type=int,default=2,help="Number of observation windows")
    p.add_argument("--duration-seconds",type=int,default=900,help="Duration per window in seconds")
    p.add_argument("--threshold-grid",type=str,default="5,10,20,40")
    p.add_argument("--lookback-grid",type=str,default=None)
    p.add_argument("--sleep-between-windows-seconds",type=int,default=5,help="Sleep between windows")
    p.add_argument("--max-markets",type=int,default=1)
    p.add_argument("--report-dir",type=str,default=str(Path("reports/polymarket_btcusd_arb/live_observer_campaign")))
    p.add_argument("--capture-dir",type=str,default=str(Path("data/polymarket_btcusd_arb/live_observer")))
    p.add_argument("--dry-run",action="store_true",help="Show plan without running")
    p.add_argument("--dry-discover",action="store_true",help="Discover markets only")
    args=p.parse_args()
    print("PHASE=2B_OBSERVER_ONLY_CAMPAIGN")
    print("NO_ORDERS=1")
    print("NO_KEYS=1")
    print("PUBLIC_DATA_ONLY=1")
    print("EXECUTION_DISABLED=1")
    print(f"BRANCH=polymarket-btcusd-arb-phase2b-observer-campaign")
    # Safety checks
    from .safety_checks import check_path
    safety=check_path(Path("examples/strategies/polymarket_btcusd_arb"))
    if not safety["ok"]:
        print(f"FAIL: safety checks failed: {safety['violations']}",file=sys.stderr); return 1
    # Discover markets
    print("Discovering BTC 15m UpDown markets...")
    try:
        markets=discover_markets()
    except Exception as e:
        print(f"Market discovery failed: {e}",file=sys.stderr)
        return 1
    print(f"Found {len(markets)} active BTC 15m UpDown markets")
    if args.dry_discover:
        for m in markets[:5]:
            print(f"  {m.slug} | {m.question} | end={m.end_ns}")
        print("--dry-discover: exiting.")
        return 0
    if not markets:
        print("No active markets. Cannot proceed.",file=sys.stderr)
        return 1
    for m in markets[:5]:
        print(f"  {m.slug} | {m.question} | end={m.end_ns}")
    if args.dry_run:
        print(f"--dry-run: would run {args.windows} windows x {args.duration_seconds}s")
        for m in markets[:args.max_markets]:
            print(f"  {m.slug}")
        return 0
    # Run campaign
    campaign_id=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
    threshold_grid=args.threshold_grid
    report_base=Path(args.report_dir)/campaign_id
    capture_base=Path(args.capture_dir)
    windows=[]
    markets_observed=[]
    start_time=time.time()
    for i in range(args.windows):
        # Select market (round-robin)
        market_idx=i%len(markets)
        market=markets[market_idx]
        print(f"\nWindow {i+1}/{args.windows}: {market.slug} for {args.duration_seconds}s...")
        result=run_single_window(
            capture_dir=capture_base,report_dir=Path(args.report_dir).parent/"live_observer",
            duration_seconds=args.duration_seconds,threshold_grid=threshold_grid,
            lookback_grid=args.lookback_grid,market_slug=market.slug,max_markets=args.max_markets,
        )
        # Parse result
        win_data={"window_idx":i,"market_slug":market.slug,"completed":result["returncode"]==0}
        if result["returncode"]==0:
            # Try to find the latest run in capture_dir
            stdout=result["stdout"]
            win_data["completed"]=True
            # Extract stats from summary
            # The observer prints stats to stdout; parse them
            for line in stdout.split("\n"):
                if line.startswith("Evaluated events:"):
                    win_data["evaluated_event_count"]=int(line.split(":")[1].strip())
                elif line.startswith("Candidate count:"):
                    win_data["candidate_count"]=int(line.split(":")[1].strip())
                elif line.startswith("Replay deterministic:"):
                    win_data["replay_deterministic"]=line.split(":")[1].strip()=="True"
            # Also look for the latest observer_summary.json
            import glob
            summaries=sorted(glob.glob(str(capture_base/"*"/"observer_summary.json")))
            if summaries:
                latest_summary_path=summaries[-1]
                try:
                    s=json.loads(Path(latest_summary_path).read_text())
                    win_data["candidate_count"]=s.get("candidate_count",win_data.get("candidate_count",0))
                    win_data["grid_rejection_count"]=s.get("grid_rejection_count",0)
                    win_data["grid_rejection_counts"]=s.get("grid_rejection_counts",{})
                    win_data["rejection_counts"]=s.get("rejection_counts",{})
                    win_data["evaluated_event_count"]=s.get("evaluated_event_count",0)
                    win_data["accounting_contract"]=s.get("accounting_contract","")
                except Exception:
                    pass
            # Try to find latest replay_check.json
            replay_checks=sorted(glob.glob(str(Path(args.report_dir).parent/"live_observer"/"*"/"replay_check.json")))
            if replay_checks:
                try:
                    r=json.loads(Path(replay_checks[-1]).read_text())
                    win_data["replay_candidate_match"]=r.get("candidate_count_match",False)
                    win_data["replay_grid_rejection_match"]=r.get("grid_rejection_count_match",False)
                    if r.get("grid_rejection_count_match") is None:
                        win_data["replay_grid_rejection_match"]="not_applicable"
                except Exception:
                    pass
        else:
            win_data["completed"]=False
            win_data["error"]=result["stderr"][:500] if result["stderr"] else "unknown error"
        markets_observed.append(market.slug)
        windows.append(win_data)
        if i<args.windows-1 and args.sleep_between_windows_seconds>0:
            print(f"Sleeping {args.sleep_between_windows_seconds}s between windows...")
            time.sleep(args.sleep_between_windows_seconds)
    end_time=time.time()
    # Build and write campaign summary
    params={"requested_windows":args.windows,"duration_seconds":args.duration_seconds,"thresholds":threshold_grid,"lookbacks":args.lookback_grid or "default"}
    summary=build_campaign_summary(campaign_id,windows,markets_observed,start_time,end_time,params,safety)
    write_campaign_report(report_base,summary,windows)
    print(f"\nCampaign complete.")
    print(f"Campaign ID: {campaign_id}")
    print(f"Completed windows: {summary['completed_windows']}/{summary['requested_windows']}")
    print(f"Total candidates: {summary['total_candidates']}")
    print(f"Total grid rejections: {summary['total_grid_rejections']}")
    print(f"All replay checks passed: {summary['all_replay_checks_passed']}")
    print(f"Verdict: {summary['verdict']} — {summary['verdict_reason']}")
    print(f"Report dir: {report_base}")
    return 0

if __name__=="__main__":
    sys.exit(main())