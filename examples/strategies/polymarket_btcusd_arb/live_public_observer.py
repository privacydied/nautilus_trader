"""Phase 2 live public observer CLI."""
from __future__ import annotations
import argparse,json,subprocess,sys,time,os,logging
from pathlib import Path

log=logging.getLogger(__name__)

def _branch_ok()->bool:
    try: b=subprocess.check_output(["git","branch","--show-current"],text=True).strip(); return b.startswith("polymarket-btcusd-arb-phase2")
    except Exception: return False

def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p=argparse.ArgumentParser(description="Phase 2 observer-only live data validation")
    p.add_argument("--duration-seconds",type=int,default=900,help="Observation duration in seconds (default 900=15min)")
    p.add_argument("--market-slug",type=str,default=None,help="Polymarket market slug; auto-discover if omitted")
    p.add_argument("--threshold-grid",type=str,default="5,10,20,40",help="Comma-separated threshold bps")
    p.add_argument("--lookback-grid",type=str,default=None,help="Comma-separated lookback ns (default from config)")
    p.add_argument("--report-dir",type=str,default=str(Path("/mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb/live_observer")))
    p.add_argument("--capture-dir",type=str,default=str(Path("/mnt/nasirjones/py/nautilus_trader/data/polymarket_btcusd_arb/live_observer")))
    p.add_argument("--random-seed",type=int,default=42)
    p.add_argument("--fail-on-lookahead",action="store_true",default=True)
    p.add_argument("--max-markets",type=int,default=1)
    p.add_argument("--dry-discover",action="store_true",help="Discover markets only, do not capture")
    args=p.parse_args()

    print("PHASE=2_OBSERVER_ONLY_LIVE_DATA_VALIDATION")
    print("NO_ORDERS=1")
    print("NO_KEYS=1")
    print("PUBLIC_DATA_ONLY=1")
    print("EXECUTION_DISABLED=1")
    print(f"BRANCH=polymarket-btcusd-arb-phase2-observer")

    branch=_branch_ok()
    if not branch:
        print("WARNING: active branch is not polymarket-btcusd-arb-phase2-observer",file=sys.stderr)
        if args.fail_on_lookahead:
            print("FAIL: wrong branch and --fail-on-lookahead is set",file=sys.stderr); return 1

    from .safety_checks import check_path
    safety=check_path(Path("examples/strategies/polymarket_btcusd_arb"))
    if not safety["ok"]:
        print(f"FAIL: safety checks failed: {safety['violations']}",file=sys.stderr); return 1

    from .live_market_discovery import discover_btc_15m_updown,fetch_market_detail,UpDownMarketInfo
    from .live_capture import BinancePublicStream,PolymarketPublicStream,run_live_capture,write_capture
    from .live_replay import replay_capture,write_replay_check
    from .live_reports import write_live_report
    from .config import PolymarketArbConfig
    from .models import DivergenceSignal

    threshold_grid=tuple(float(x) for x in args.threshold_grid.split(","))
    config=PolymarketArbConfig(threshold_bps_grid=threshold_grid,random_seed=args.random_seed,fail_on_lookahead=args.fail_on_lookahead)

    # Discover markets
    print("Discovering BTC 15m UpDown markets...")
    markets=discover_btc_15m_updown()
    print(f"Found {len(markets)} active BTC 15m UpDown markets")

    if not markets:
        print("No active BTC 15m UpDown markets found.")
        if args.dry_discover:
            print("--dry-discover: exiting with no active markets found.")
            return 0
        print("Cannot proceed with live capture: no active markets.",file=sys.stderr)
        return 1

    for m in markets[:5]:
        print(f"  {m.slug} | {m.question} | end={m.end_ns}")

    if args.dry_discover:
        print("--dry-discover: exiting after discovery.")
        return 0

    # Select market
    selected=markets[0]
    if args.market_slug:
        matches=[m for m in markets if m.slug==args.market_slug]
        if matches: selected=matches[0]
        else: print(f"WARNING: slug {args.market_slug} not in discovered markets; using first available")

    print(f"Selected market: {selected.slug}")

    # Fetch market detail for tokens and strike
    detail=fetch_market_detail(selected.slug)
    yes_token=selected.yes_token_id
    price_to_beat_source="slug_epoch"
    if detail:
        import json
        from .polymarket_data import _extract_strike,_extract_updown_tokens
        info=detail
        tokens_raw=info.get("clobTokenIds") or "[]"
        if isinstance(tokens_raw,str):
            try: tokens_raw=json.loads(tokens_raw)
            except: tokens_raw=[]
        outcomes_raw=info.get("outcomes") or "[]"
        if isinstance(outcomes_raw,str):
            try: outcomes_raw=json.loads(outcomes_raw)
            except: outcomes_raw=[]
        yes_t=no_t=None
        for i,tok in enumerate(tokens_raw):
            outcome=str(outcomes_raw[i]).upper() if i<len(outcomes_raw) else ""
            if outcome in ("YES","UP"): yes_t=tok
            if outcome in ("NO","DOWN"): no_t=tok
        if yes_t: yes_token=yes_t
        else:
            yes_token=selected.yes_token_id
        print(f"YES token: {yes_token}")
        print(f"Question: {detail.get('question','?')}")

    # Approximate strike from Binance if needed
    if selected.price_to_beat is None:
        binance_check=BinancePublicStream()
        try:
            snap=binance_check.poll_bookticker()
            if snap and snap.get("best_bid"):
                strike=float(snap["best_bid"])
                price_to_beat_source="approximation_binance_bookticker"
                print(f"Strike approximated from Binance bookTicker: {strike}")
                selected=UpDownMarketInfo(slug=selected.slug,question=selected.question,active=selected.active,closed=selected.closed,condition_id=selected.condition_id,yes_token_id=selected.yes_token_id,no_token_id=selected.no_token_id,start_ns=selected.start_ns,end_ns=selected.end_ns,series_slug=selected.series_slug,resolution_source=selected.resolution_source,price_to_beat=strike,price_to_beat_source=price_to_beat_source)
        except Exception as e:
            print(f"Binance bookTicker check failed: {e}")
            strike=100000.0
            price_to_beat_source="fallback_default"

    # Run capture
    binance=BinancePublicStream()
    poly=PolymarketPublicStream(selected.slug,yes_token)
    print(f"Starting live capture for {args.duration_seconds}s...")
    capture=run_live_capture(selected,binance,poly,args.duration_seconds,threshold_grid,config)

    # Write capture
    run_id=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())
    capture_dir=Path(args.capture_dir)/run_id
    write_capture(run_id,{**capture,"_poly_events":poly._events,"_binance_events":binance._events},selected,config)
    print(f"CAPTURE_DIR={capture_dir}")

    # Write reports
    report_dir=Path(args.report_dir)/run_id
    poly_stale_rate=capture["poly_stale"]/max(capture["poly_polls"],1)
    binance_stale_rate=capture["binance_stale"]/max(capture["binance_polls"],1)
    summary={"run_id":run_id,"branch":"polymarket-btcusd-arb-phase2-observer","market_slug":selected.slug,"market_strike_or_price_to_beat":selected.price_to_beat,"price_to_beat_source":price_to_beat_source,"expiry_ns":selected.end_ns,"duration_seconds":capture["duration_seconds"],"accounting_contract":capture.get("accounting_contract","grid_level: event × lookback × threshold"),"accounting_note":capture.get("accounting_note","Pre-grid rejections are event-level; grid rejections are event × lookback × threshold."),"event_count":capture.get("event_count",0),"evaluated_event_count":capture.get("evaluated_event_count",0),"grid_evaluation_count":capture.get("grid_evaluation_count",0),"grid_candidate_count":capture.get("grid_candidate_count",0),"grid_rejection_count":capture.get("grid_rejection_count",0),"pre_grid_rejection_count":capture.get("pre_grid_rejection_count",0),"candidate_count":len(capture["signals"]),"rejection_total":len(capture["rejections"]),"rejection_counts":capture["rejection_counts"],"pre_grid_rejection_counts":capture.get("pre_grid_rejection_counts",{}),"grid_rejection_counts":capture.get("grid_rejection_counts",{}),"binance_polls":capture["binance_polls"],"poly_polls":capture["poly_polls"],"binance_stale_rate":binance_stale_rate,"poly_stale_rate":poly_stale_rate,"missing_binance":capture["missing_binance"],"missing_poly":capture["missing_poly"],"safety_check_status":safety,"phase":"2_observer_only"}

    # Attempt replay
    try:
        replay_result=replay_capture(capture_dir,config)
        write_replay_check(run_id,replay_result,report_dir)
    except Exception as e:
        print(f"Replay check failed: {e}",file=sys.stderr)
        replay_result={"deterministic":False,"error":str(e)}

    groups=[asdict(g) if hasattr(g,"__dataclass_fields__") else g for g in capture.get("groups",[])]
    # Live captures don't use a file cache, record this explicitly
    cache_meta={"source":"live_observer","symbol_or_slug":selected.slug,"phase":"2_observer_only","cache_type":"none_live_capture"}
    write_live_report(report_dir,summary=summary,signals=capture["signals"],rejections=capture["rejections"],groups=groups,safety=safety,replay=replay_result,cache_meta=cache_meta)
    print(f"REPORT_DIR={report_dir}")
    print(f"Evaluated events: {capture.get('evaluated_event_count',0)}")
    print(f"Candidate count: {len(capture['signals'])}")
    print(f"Rejection count: {sum(capture['rejection_counts'].values()) if capture['rejection_counts'] else 0}")
    if capture["rejection_counts"]:
        for reason,count in sorted(capture["rejection_counts"].items()):
            print(f"  {reason}: {count}")
    print(f"Binance stale rate: {binance_stale_rate:.4f}")
    print(f"Polymarket stale rate: {poly_stale_rate:.4f}")
    print(f"Replay deterministic: {replay_result.get('deterministic','?')}")
    return 0

if __name__=="__main__":
    sys.exit(main())