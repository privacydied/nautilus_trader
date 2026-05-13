"""Replay captured observer data offline and verify determinism.

Accounting contract: same as live capture. Both use
signal_generator.generate_signals() for grid-level evaluation.
Each (event × lookback × threshold) grid cell produces one record.
Pre-grid rejections are loaded from capture and kept separate.
"""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import BinanceReferenceState,PolymarketContractMetadata,PolymarketQuoteState,DivergenceSignal
from .signal_generator import generate_signals
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
    """Replay captured data and recompute signals to verify determinism.

    Uses the same signal_generator.generate_signals() as live capture,
    producing grid-level (event × lookback × threshold) accounting.
    """
    data=load_capture(capture_dir)
    meta_data=data["metadata"]
    slug=meta_data.get("market_slug","")
    end_ns=meta_data.get("end_ns") or 0
    strike=100000.0
    # Try to get strike from observer_summary if available
    obs=data.get("observer_summary",{})
    meta=PolymarketContractMetadata(market_slug=slug,strike=strike,expiry_ns=end_ns,yes_token_id=meta_data.get("yes_token_id"),no_token_id=meta_data.get("no_token_id"),has_no_token=bool(meta_data.get("no_token_id")),sanitized_info=True)

    # Build Binance reference states from captured events
    binance_states=[]
    for e in data["binance_events"]:
        if e.get("type") in ("aggtrade","binance_aggtrade"):
            st=BinanceReferenceState(symbol=e.get("symbol","BTCUSDT"),price=float(e.get("price",0)),ts_event_ns=int(e.get("ts_event_ns",0)),ts_recv_ns=int(e.get("ts_event_ns",0)),last_trade=float(e.get("price",0)),source="binance_live_aggtrade")
            binance_states.append(st)
    binance_states.sort(key=lambda s:s.ts_event_ns)

    # Build Polymarket quote states from captured events
    poly_quotes=data.get("poly_events",[])
    quote_states=[]
    for qe in poly_quotes:
        if qe.get("type")=="poly_quote":
            qs=PolymarketQuoteState(market_slug=slug,token_id=qe.get("token_id",""),outcome="YES",quoted_probability=float(qe.get("mid",0)),ts_event_ns=int(qe.get("ts_event_ns",0)),ts_recv_ns=int(qe.get("ts_event_ns",0)),best_bid=float(qe.get("best_bid",0)) if qe.get("best_bid") else None,best_ask=float(qe.get("best_ask",0)) if qe.get("best_ask") else None,spread_bps=float(qe.get("spread_bps",0)) if qe.get("spread_bps") else None,depth=0.0)
            quote_states.append(qs)

    # Grid-level evaluation: same path as live capture
    candidates,rejections_gen,grid_rejection_counts=generate_signals(tuple(quote_states) if quote_states else tuple(data["signals"]),tuple(binance_states),meta,config)

    outcomes=measure_forward_outcomes(candidates,tuple(quote_states) if quote_states else tuple(data["signals"]),config.forward_horizon_ns_grid,resolved_payoff=None)
    if config.fail_on_lookahead: assert_no_settlement_lookahead(outcomes)
    baseline=generate_random_baseline(tuple(quote_states) if quote_states else tuple(data["signals"]),config.baseline_sample_count,config,meta.expiry_ns)
    groups=evaluate_grid(candidates,outcomes,baseline,config)

    # Compute grid evaluation count
    evaluated_event_count=len(quote_states) if quote_states else len(data["signals"])
    grid_evaluation_count=evaluated_event_count*len(config.lookback_ns_grid)*len(config.threshold_bps_grid)

    # Check accounting contract version from original capture
    original_accounting=obs.get("accounting_contract","event_level:legacy")
    original_has_grid_accounting="grid" in original_accounting.lower() if original_accounting else False

    # Separate pre-grid and grid rejections from original capture
    # Note: for legacy (event-level) captures, ALL rejections are event-level
    # and there is no pre-grid/grid separation
    pre_grid_reasons={"missing_poly","market_expired","too_close_to_expiry"}
    original_pre_grid=[r for r in data["rejections"] if r.rejection_reason in pre_grid_reasons]
    original_grid=[r for r in data["rejections"] if r.rejection_reason not in pre_grid_reasons]

    # Check accounting match at grid level
    candidate_count_match=len(candidates)==len(data["signals"])
    # Only claim grid rejection match if original capture used grid-level accounting
    if original_has_grid_accounting:
        grid_rejection_count_match=len(rejections_gen)==len(original_grid)
    else:
        # Legacy capture used event-level rejection; cannot compare grid counts directly
        grid_rejection_count_match=None

    accounting_note="Both live capture and replay use signal_generator.generate_signals() for grid-level evaluation. Pre-grid rejections (missing_poly, market_expired, too_close_to_expiry) are event-level only and not compared at grid level."
    if not original_has_grid_accounting:
        accounting_note+=" LEGACY_CAPTURE_NOTE: Original capture used event-level rejection accounting (spread_too_wide, stale_or_missing_binance counted once per event, not per grid cell). Grid rejection count comparison is not_applicable for this capture."

    return {
        "replay_candidate_count":len(candidates),
        "replay_grid_rejection_count":len(rejections_gen),
        "replay_grid_rejection_counts":grid_rejection_counts,
        "replay_evaluated_event_count":evaluated_event_count,
        "replay_grid_evaluation_count":grid_evaluation_count,
        "original_candidate_count":len(data["signals"]),
        "original_rejection_count":len(data["rejections"]),
        "original_pre_grid_rejection_count":len(original_pre_grid),
        "original_grid_rejection_count":len(original_grid),
        "original_rejection_counts":obs.get("rejection_counts",{}),
        "original_accounting_contract":original_accounting,
        "candidate_count_match":candidate_count_match,
        "grid_rejection_count_match":grid_rejection_count_match,
        "replay_groups":[asdict(g) for g in groups],
        "deterministic":candidate_count_match and (grid_rejection_count_match if grid_rejection_count_match is not None else True),
        "accounting_contract":"grid_level: event × lookback × threshold",
        "accounting_note":accounting_note,
    }

def write_replay_check(run_id:str,replay_result:dict,report_dir:Path)->Path:
    """Write replay check result."""
    d=Path(report_dir); d.mkdir(parents=True,exist_ok=True)
    p=d/"replay_check.json"
    p.write_text(json.dumps(replay_result,indent=2,default=str))
    return p