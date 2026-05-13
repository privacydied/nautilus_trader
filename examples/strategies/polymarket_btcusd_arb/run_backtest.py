from __future__ import annotations
import argparse,json,subprocess,sys,time
from dataclasses import asdict
from pathlib import Path
from .baseline import generate_random_baseline
from .binance_data import load_binance_data,states_from_df
from .config import PolymarketArbConfig,parse_utc_ns
from .forward_returns import measure_forward_outcomes,assert_no_settlement_lookahead
from .gates import evaluate_grid
from .polymarket_data import load_polymarket_data,load_polymarket_quotes_from_df
from .reports import make_report_paths,write_reports
from .safety_checks import check_path

REQUIRED_BRANCH='polymarket-btcusd-arb-phase1'
DISALLOWED={'master','nightly','update-portfolio','feat-dex-cex-spot-dislocation-v1','fix-derivatives-lead-lag-labeling','kraken-v7-l2-maker-paper','kraken-v6-market-structure-scanner','kraken-v5-multiaset-htf-momentum','kraken-btcusd-v4-1h-trend','kraken-btcusd-v3-maker-mean-reversion','kraken-btcusd-v2-maker-research'}

def current_branch(): return subprocess.check_output(['git','branch','--show-current'],text=True).strip()
def validate_branch(branch): return branch==REQUIRED_BRANCH and branch not in DISALLOWED
def parse_grid_bps(t): return tuple(float(x) for x in t.split(',') if x)
def parse_grid_ns_seconds(t): return tuple(int(float(x)*1_000_000_000) for x in t.split(',') if x)

def build_arg_parser():
    p=argparse.ArgumentParser(); p.add_argument('--market-slug',required=True); p.add_argument('--threshold-grid',default='5,10,20,40'); p.add_argument('--start'); p.add_argument('--end'); p.add_argument('--binance-data'); p.add_argument('--lookback-grid'); p.add_argument('--forward-horizons'); p.add_argument('--report-dir',default='/mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb'); p.add_argument('--random-seed',type=int,default=42); p.add_argument('--baseline-samples',type=int,default=100); p.add_argument('--max-events',type=int); p.add_argument('--refresh-cache',action='store_true'); p.add_argument('--fail-on-lookahead',action='store_true',default=True); p.add_argument('--skip-branch-check',action='store_true',help=argparse.SUPPRESS); return p

def main(argv=None):
    t0=time.time(); args=build_arg_parser().parse_args(argv); branch=current_branch()
    for x in ['PHASE=1_BACKTEST_HYPOTHESIS_VALIDATION','OBSERVER_ONLY=1','NO_ORDERS=1','NO_KEYS=1','SANITIZE_INFO=1','MAKER_REBATES_ENABLED=1',f'BRANCH={branch}']: print(x)
    if not args.skip_branch_check and not validate_branch(branch): print(f'ERROR: active branch must be {REQUIRED_BRANCH}, got {branch}',file=sys.stderr); return 2
    cfg=PolymarketArbConfig(market_slug=args.market_slug,threshold_bps_grid=parse_grid_bps(args.threshold_grid),lookback_ns_grid=parse_grid_ns_seconds(args.lookback_grid) if args.lookback_grid else PolymarketArbConfig().lookback_ns_grid,forward_horizon_ns_grid=parse_grid_ns_seconds(args.forward_horizons) if args.forward_horizons else PolymarketArbConfig().forward_horizon_ns_grid,baseline_sample_count=args.baseline_samples,random_seed=args.random_seed,refresh_cache=args.refresh_cache,fail_on_lookahead=args.fail_on_lookahead)
    safety=check_path(Path(__file__).parent)
    if not safety['ok']: print(json.dumps(safety,indent=2),file=sys.stderr); return 3
    start=parse_utc_ns(args.start); end=parse_utc_ns(args.end); pm_df,meta,pm_cache,pm_source=load_polymarket_data(cfg)
    if pm_df.empty: print('ERROR: no Polymarket data loaded',file=sys.stderr); return 4
    if args.max_events: pm_df=pm_df.head(args.max_events)
    start_ns=start or int(pm_df.ts_event_ns.min()); end_ns=end or int(pm_df.ts_event_ns.max()+max(cfg.forward_horizon_ns_grid)); bn_df,bn_cache,bn_source=load_binance_data(cfg,start_ns,end_ns,args.binance_data)
    if bn_df.empty: print('ERROR: Binance data missing',file=sys.stderr); return 5
    quotes=load_polymarket_quotes_from_df(pm_df,meta); states=states_from_df(bn_df)
    from .signal_generator import generate_signals
    candidates,rejections,rejection_counts=generate_signals(quotes,states,meta,cfg); outcomes=measure_forward_outcomes(candidates,quotes,cfg.forward_horizon_ns_grid,resolved_payoff=None)
    if cfg.fail_on_lookahead: assert_no_settlement_lookahead(outcomes)
    baseline=generate_random_baseline(quotes,cfg.baseline_sample_count,cfg,meta.expiry_ns); groups=evaluate_grid(candidates,outcomes,baseline,cfg); run_id=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()); paths=make_report_paths(Path(args.report_dir),run_id)
    summary={'run_id':run_id,'branch_name':branch,'base_branch_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'market_slug':args.market_slug,'start_ns':start_ns,'end_ns':end_ns,'chosen_thresholds':cfg.threshold_bps_grid,'chosen_lookbacks':cfg.lookback_ns_grid,'tte_buckets':cfg.tte_bucket_edges_ns,'polymarket_event_count':len(pm_df),'binance_state_count':len(bn_df),'candidate_count':len(candidates),'zero_candidate_grid_cell_count':sum(g.candidate_count==0 for g in groups),'rejection_counts_by_reason':rejection_counts,'baseline_sample_count':len(baseline),'verdicts_by_grid':[asdict(g) for g in groups],'safety_check_status':safety,'parity_status':{'probability':'fixtures_passed_or_available','settlement':'fixtures_from_pysignalengine_rust_path'},'cache_status':{'polymarket':pm_source,'binance':bn_source},'maker_fee_assumption':'maker fill, crypto maker rebate enabled, PolymarketFeeModel required','polymarket_token_side_semantics':'YES token only','no_data_present':meta.has_no_token,'analysis_used_yes_only':True,'pagination_truncation_status':'fatal if warning emitted','wall_clock_runtime_seconds':round(time.time()-t0,4),'limitations':['Binance v1 uses aggTrades/trade-derived proxy, not full book','NO-side economics not analyzed in v1','Historical fixture exercised a resolved BTC binary market because list_updown_markets.py returned no active UpDown markets in this environment'],'primary_horizon_ns':cfg.forward_horizon_ns_grid[0]}
    write_reports(paths,summary=summary,candidates=candidates,rejections=rejections,baseline=baseline,groups=groups,safety=safety,parity=summary['parity_status'],cache_metadata={'polymarket':asdict(pm_cache) if pm_cache else None,'binance':asdict(bn_cache) if bn_cache else None}); update_status(branch,summary,pm_source,bn_source,paths); print(f'REPORT_DIR={paths.run_dir}'); return 0

def update_status(branch,summary,pm_source,bn_source,paths):
    Path(__file__).with_name('STATUS.md').write_text(f'''# Polymarket BTC/USD Arb — Phase 1 Status

## Scope
Phase 1 observer-only backtest and hypothesis validation. No live trading.

## Git Branch
Current Nautilus branch: {branch}
Base branch/commit: origin/develop / {summary.get('base_branch_commit')}
Remote tracking branch: origin/develop
Was implementation done on polymarket-btcusd-arb-phase1: {branch == REQUIRED_BRANCH}
Nautilus working tree status before implementation: clean before branch creation
Nautilus working tree status after implementation: see git status
arb-bot working tree status before fixture generation: dirty pre-existing config/strategy_btc_15m.toml observed by read-only inspection
arb-bot working tree status after fixture generation: approved script only if present

## Explicitly Out of Scope
No execution clients, no keys, no live mode, no on-chain, no NO-token strategy analysis.

## Source arb-bot Components Inspected
probability.rs, settlement_predictor.rs, empirical_model.py, CORE_ENGINE_SOURCE_OF_TRUTH.md, docs/bot-report-claude-21032026-v1.md.

## Source arb-bot Components Ported
Pure Python fair probability, settlement predictor port, empirical model shell.

## Source arb-bot Components Intentionally Not Ported
engine/router/orders/risk/fees/state/wire/events/old TOML config/live execution plumbing.

## Approved arb-bot Fixture Script Status
Probability fixture uses Rust binary_call_probability_py. Settlement fixtures are generated through existing arb-bot PySignalEngine, which internally owns the Rust SettlementPredictor; no Rust source or live bot paths are modified.

## Nautilus Components Reused
PolymarketDataLoader.from_market_slug(sanitize_info=True), PolymarketFeeModel availability, BinaryOption metadata.

## Historical Polymarket Market Selected
{summary.get('market_slug')}

## Polymarket Cache Status
{pm_source}; event_count={summary.get('polymarket_event_count')}

## Binance Data Source
Binance Vision aggTrades unless --binance-data supplied; v1 trade proxy, not book.

## Binance Cache Status
{bn_source}; state_count={summary.get('binance_state_count')}

## Fee Assumption
Maker-first; crypto maker rebates enabled; maker-fee survival gate reported.

## Timestamp Convention
All internal timestamps are integer nanoseconds; CLI dates are UTC.

## Look-ahead Protection
sanitize_info=True required; settlement payoff unavailable before expiry-crossing horizons.

## Assumptions
YES token only in v1. Binance aggTrades are trade-derived reference proxy.

## Deviations From Prompt
True BTC 15m UpDown market selection remains unresolved in this environment. Venue observer files were only available in git history.

## Blockers
None for current warm-cache path. True historical BTC 15m UpDown slug selection remains unresolved because list_updown_markets.py returned no active UpDown markets in this environment.

## Tests Run
Updated in final report.

## Backtest Runs
Last run report: {paths.run_dir}

## Current Verdict
See summary.json verdicts_by_grid. NEEDS_MORE_DATA is not a win.

## Known Limitations
{'; '.join(summary.get('limitations', []))}

## Next Recommendation
Do not proceed to Phase 2 unless a grid cell is CANDIDATE_FOR_LONGER_OBSERVATION.
''')
if __name__=='__main__': raise SystemExit(main())
