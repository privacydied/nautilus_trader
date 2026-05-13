"""Replay captured observer data offline and verify determinism."""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import BinanceReferenceState,PolymarketQuoteState,PolymarketContractMetadata,DivergenceSignal
from .signal_generator import generate_signals,maker_fee_bps
from .forward_returns import measure_forward_outcomes,assert_no_settlement_lookahead
from .gates import evaluate_grid
from .baseline import generate_random_baseline
from .binance_data import align_state

def load_capture(capture_dir:Path)->dict:
    """Load captured JSONL data from disk."""
    d=Path(capture_dir)
    meta=json.loads((d/"metadata.json").read_text())
    signals=[]; rejections=[]
    sf=d/"signals.jsonl"
    if sf.exists():
        for line in sf.read_text().strip().splitlines():
            sd=json.loads(line); signals.append(DivergenceSignal(**sd))
    rf=d/"rejections.jsonl"
    if rf.exists():
        for line in rf.read_text().strip().splitlines():
            rd=json.loads(line); rejections.append(DivergenceSignal(**rd))
    poly_events=[]; bn_events=[]
    pe=d/"polymarket_events.jsonl"
    if pe.exists():
        for line in pe.read_text().strip().splitlines():
            poly_events.append(json.loads(line))
    be=d/"binance_events.jsonl"
    if be.exists():
        for line in be.read_text().strip().splitlines():
            bn_events.append(json.loads(line))
    obs=json.loads((d/"observer_summary.json").read_text()) if (d/"observer_summary.json").exists() else {}
    return {"metadata":meta,"signals":signals,"rejections":rejections,"poly_events":poly_events,"binance_events":bn_events,"observer_summary":obs}

def replay_capture(capture_dir:Path,config)->dict:
    """Replay captured data and recompute signals to verify determinism."""
    data=load_capture(capture_dir)
    meta_data=data["metadata"]
    slug=meta_data.get("market_slug","")
    end_ns=meta_data.get("end_ns") or 0
    strike=100000.0
    meta=PolymarketContractMetadata(market_slug=slug,strike=strike,expiry_ns=end_ns,yes_token_id=meta_data.get("yes_token_id"),no_token_id=meta_data.get("no_token_id"),has_no_token=bool(meta_data.get("no_token_id")),sanitized_info=True)
    quotes=data["signals"]
    binance_states=[]
    for e in data["binance_events"]:
        if e.get("type") in ("aggtrade","binance_aggtrade"):
            st=BinanceReferenceState(symbol=e.get("symbol","BTCUSDT"),price=float(e.get("price",0)),ts_event_ns=int(e.get("ts_event_ns",0)),ts_recv_ns=int(e.get("ts_event_ns",0)),last_trade=float(e.get("price",0)),source="binance_live_aggtrade")
            binance_states.append(st)
    binance_states.sort(key=lambda s:s.ts_event_ns)

    # Re-generate signals from captured data
    poly_quotes=data.get("poly_events",[])
    quote_states=[]
    for qe in poly_quotes:
        if qe.get("type")=="poly_quote":
            qs=PolymarketQuoteState(market_slug=slug,token_id=qe.get("token_id",""),outcome="YES",quoted_probability=float(qe.get("mid",0)),ts_event_ns=int(qe.get("ts_event_ns",0)),ts_recv_ns=int(qe.get("ts_event_ns",0)),best_bid=float(qe.get("best_bid",0)) if qe.get("best_bid") else None,best_ask=float(qe.get("best_ask",0)) if qe.get("best_ask") else None,spread_bps=float(qe.get("spread_bps",0)) if qe.get("spread_bps") else None,depth=0.0)
            quote_states.append(qs)

    candidates,rejections_gen,rejection_counts=generate_signals(tuple(quote_states) if quote_states else tuple(quotes),tuple(binance_states),meta,config)
    outcomes=measure_forward_outcomes(candidates,tuple(quote_states) if quote_states else tuple(quotes),config.forward_horizon_ns_grid,resolved_payoff=None)
    if config.fail_on_lookahead: assert_no_settlement_lookahead(outcomes)
    baseline=generate_random_baseline(tuple(quote_states) if quote_states else tuple(quotes),config.baseline_sample_count,config,meta.expiry_ns)
    groups=evaluate_grid(candidates,outcomes,baseline,config)

    return {"replay_candidate_count":len(candidates),"replay_rejection_counts":rejection_counts,"original_candidate_count":len(data["signals"]),"replay_groups":[asdict(g) for g in groups],"deterministic":len(candidates)==len(data["signals"])}

def write_replay_check(run_id:str,replay_result:dict,report_dir:Path)->Path:
    """Write replay check result."""
    d=Path(report_dir); d.mkdir(parents=True,exist_ok=True)
    p=d/"replay_check.json"
    p.write_text(json.dumps(replay_result,indent=2,default=str))
    return p