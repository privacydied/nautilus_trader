"""Live public data capture for Phase 2 observer.

Accounting contract: both live capture and replay use the same grid-level
evaluation path via signal_generator.generate_signals(). Each evaluated
(event × lookback × threshold) grid cell produces exactly one record:
either a candidate or a rejection. Event-level rejections (where the event
cannot be evaluated at all) are tracked separately as pre-grid rejections
and are not comparable to grid-level rejections.
"""
from __future__ import annotations
import json,time,logging
from dataclasses import asdict
from pathlib import Path
from typing import Any
import pandas as pd
from .models import BinanceReferenceState,PolymarketContractMetadata,PolymarketQuoteState,DivergenceSignal
from .live_market_discovery import UpDownMarketInfo
from .config import tte_bucket_name

log=logging.getLogger(__name__)

LIVE_DATA_ROOT=Path("/mnt/nasirjones/py/nautilus_trader/data/polymarket_btcusd_arb/live_observer")

def _ts_ns(v:Any)->int|None:
    if v is None: return None
    s=str(v)[:26].replace("Z","").replace("+00:00","")
    try:
        from datetime import datetime,timezone
        return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()*1_000_000_000)
    except Exception: return None

class BinancePublicStream:
    """Capture public Binance BTCUSDT aggTrades and bookTicker via REST polling."""
    def __init__(self,symbol:str="BTCUSDT")->None:
        self.symbol=symbol; self._events:list[dict]=[]; self._states:list[BinanceReferenceState]=[]
    def poll_aggtrades(self,start_ns:int,end_ns:int)->list[BinanceReferenceState]:
        """Fetch aggTrades for a time window from Binance public API."""
        import urllib.request
        start_ms=max(0,start_ns//1_000_000); end_ms=end_ns//1_000_000
        url=f"https://api.binance.com/api/v3/aggTrades?symbol={self.symbol}&startTime={start_ms}&endTime={end_ms}&limit=1000"
        req=urllib.request.Request(url,headers={"User-Agent":"NautilusTrader/Phase2Observer"})
        try:
            data=json.loads(urllib.request.urlopen(req,timeout=15).read())
        except Exception as e:
            log.warning("Binance aggTrades poll failed: %s",e); return []
        states=[]
        for t in data:
            ts_ns=int(t["T"])*1_000_000
            price=float(t["p"])
            st=BinanceReferenceState(symbol=self.symbol,price=price,ts_event_ns=ts_ns,ts_recv_ns=ts_ns,last_trade=price,source="binance_live_aggtrade")
            states.append(st)
            self._events.append({"type":"aggtrade","symbol":self.symbol,"price":price,"qty":float(t["q"]),"ts_event_ns":ts_ns,"is_buyer_maker":t["m"]})
        self._states.extend(states)
        return states
    def poll_bookticker(self)->dict|None:
        """Fetch current best bid/ask from Binance bookTicker."""
        import urllib.request
        url=f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={self.symbol}"
        req=urllib.request.Request(url,headers={"User-Agent":"NautilusTrader/Phase2Observer"})
        try:
            data=json.loads(urllib.request.urlopen(req,timeout=10).read())
            ts_ns=int(time.time()*1_000_000_000)
            event={"type":"bookticker","symbol":self.symbol,"best_bid":float(data["bidPrice"]),"best_ask":float(data["askPrice"]),"bid_qty":float(data["bidQty"]),"ask_qty":float(data["askQty"]),"ts_event_ns":ts_ns}
            self._events.append(event)
            return event
        except Exception as e:
            log.warning("Binance bookTicker poll failed: %s",e); return None

class PolymarketPublicStream:
    """Capture public Polymarket market data via REST."""
    def __init__(self,market_slug:str,token_id:str|None=None)->None:
        self.market_slug=market_slug; self.token_id=token_id; self._events:list[dict]=[]; self._quotes:list[PolymarketQuoteState]=[]
    def poll_orderbook(self)->PolymarketQuoteState|None:
        """Poll current best bid/ask from Polymarket CLOB."""
        import urllib.request
        if not self.token_id: return None
        url=f"https://clob.polymarket.com/book?token_id={self.token_id}"
        req=urllib.request.Request(url,headers={"User-Agent":"NautilusTrader/Phase2Observer"})
        try:
            data=json.loads(urllib.request.urlopen(req,timeout=10).read())
            ts_ns=int(time.time()*1_000_000_000)
            bids=data.get("bids",[]); asks=data.get("asks",[])
            best_bid=float(bids[0]["price"]) if bids else None
            best_ask=float(asks[0]["price"]) if asks else None
            mid=(best_bid+best_ask)/2 if best_bid and best_ask else None
            spread=((best_ask-best_bid)/mid*10_000) if best_bid and best_ask and mid else None
            q=PolymarketQuoteState(market_slug=self.market_slug,token_id=self.token_id,outcome="YES",quoted_probability=mid if mid else 0.0,ts_event_ns=ts_ns,ts_recv_ns=ts_ns,best_bid=best_bid,best_ask=best_ask,last_trade=mid,spread_bps=spread,depth=float(sum(float(b.get("size",0)) for b in bids[:5])) if bids else 0.0)
            self._quotes.append(q)
            self._events.append({"type":"poly_quote","market_slug":self.market_slug,"token_id":self.token_id,"best_bid":best_bid,"best_ask":best_ask,"mid":mid,"spread_bps":spread,"ts_event_ns":ts_ns})
            return q
        except Exception as e:
            log.warning("Polymarket CLOB poll failed: %s",e); return None
    def poll_trades(self,start_ms:int,end_ms:int)->list[PolymarketQuoteState]:
        """Poll recent trades from Polymarket CLOB."""
        import urllib.request
        if not self.token_id: return []
        url=f"https://clob.polymarket.com/trades?token_id={self.token_id}&after={start_ms}&before={end_ms}&limit=500"
        req=urllib.request.Request(url,headers={"User-Agent":"NautilusTrader/Phase2Observer"})
        try:
            data=json.loads(urllib.request.urlopen(req,timeout=15).read())
            if isinstance(data,dict): data=data.get("trades",data.get("data",[]))
            quotes=[]
            for t in data:
                ts_ms=int(t.get("timestamp",t.get("match_time",0)))
                ts_ns=ts_ms*1_000_000
                price=float(t.get("price",0))
                q=PolymarketQuoteState(market_slug=self.market_slug,token_id=self.token_id,outcome="YES",quoted_probability=price,ts_event_ns=ts_ns,ts_recv_ns=ts_ns,last_trade=price,depth=float(t.get("size",0)))
                quotes.append(q)
                self._events.append({"type":"poly_trade","market_slug":self.market_slug,"token_id":self.token_id,"price":price,"size":float(t.get("size",0)),"ts_event_ns":ts_ns})
            self._quotes.extend(quotes)
            return quotes
        except Exception as e:
            log.warning("Polymarket trade poll failed: %s",e); return []


def run_live_capture(market_info:UpDownMarketInfo,binance:BinancePublicStream,poly:PolymarketPublicStream,duration_seconds:int,threshold_grid:tuple[float,...],config)->dict:
    """Run a live observer capture for a fixed duration.

    Accounting contract: grid-level evaluation using signal_generator.
    Each (quote × lookback × threshold) grid cell produces one record:
    either a candidate or a grid-level rejection.

    Pre-grid event-level rejections (missing_poly, market_expired, too_close_to_expiry,
    stale_or_missing_binance, spread_too_wide) are tracked separately because
    those events were never evaluated at grid level.
    """
    from .signal_generator import generate_signals

    t0=time.time(); end_time=t0+duration_seconds
    poll_interval=5

    # Pre-grid tracking: events that could not be evaluated at all
    pre_grid_rejections:list[DivergenceSignal]=[]
    pre_grid_rejection_counts:dict[str,int]={}
    pre_grid_events=0  # events that passed to grid evaluation
    missing_poly=0
    market_expired=0

    # Quote collection for grid evaluation
    collected_quotes:list[PolymarketQuoteState]=[]
    binance_polls=0; poly_polls=0

    while time.time()<end_time:
        now_ns=int(time.time()*1_000_000_000)

        # Compute time-to-expiry
        if not market_info.end_ns or now_ns<market_info.end_ns:
            tte=market_info.end_ns-now_ns if market_info.end_ns else 10_000_000_000_000
        else:
            tte=0

        # Poll Binance
        binance.poll_aggtrades(now_ns-5_000_000_000,now_ns)
        binance.poll_bookticker()
        # Poll Polymarket
        poly_q=poly.poll_orderbook()
        binance_polls+=1; poly_polls+=1

        # If Polymarket quote is missing, skip evaluation but count
        if poly_q is None:
            missing_poly+=1
            time.sleep(poll_interval)
            continue

        # If market expired, record pre-grid rejection
        if tte<=0:
            r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=0,lookback_ns=0,tte_bucket="expired",fair_probability=0.0,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=0.0,maker_fee_bps=0.0,spread_bps=poly_q.spread_bps or 0.0,latency_buffer_bps=0.0,settlement_buffer_bps=0.0,stale_buffer_bps=0.0,net_divergence_bps=0.0,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="market_expired")
            pre_grid_rejections.append(r); pre_grid_rejection_counts["market_expired"]=pre_grid_rejection_counts.get("market_expired",0)+1
            time.sleep(poll_interval)
            continue

        # If TTE too small for evaluation, record pre-grid rejection
        if tte<config.min_tte_ns:
            bucket=tte_bucket_name(max(0,tte),config.tte_bucket_edges_ns)
            r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=0,lookback_ns=0,tte_bucket=bucket,fair_probability=0.0,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=0.0,maker_fee_bps=0.0,spread_bps=poly_q.spread_bps or 0.0,latency_buffer_bps=0.0,settlement_buffer_bps=0.0,stale_buffer_bps=0.0,net_divergence_bps=0.0,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="too_close_to_expiry")
            pre_grid_rejections.append(r); pre_grid_rejection_counts["too_close_to_expiry"]=pre_grid_rejection_counts.get("too_close_to_expiry",0)+1
            time.sleep(poll_interval)
            continue

        # Collect valid quote for grid evaluation
        collected_quotes.append(poly_q)
        pre_grid_events+=1

        time.sleep(poll_interval)

    # Build contract metadata
    meta=PolymarketContractMetadata(
        market_slug=market_info.slug,
        strike=market_info.price_to_beat or 100000.0,
        expiry_ns=market_info.end_ns or 0,
        yes_token_id=market_info.yes_token_id,
        no_token_id=market_info.no_token_id,
        has_no_token=bool(market_info.no_token_id),
        sanitized_info=True
    )

    # Grid-level evaluation using the same signal_generator as replay
    binance_states=tuple(binance._states[-5000:]) if binance._states else ()
    grid_candidates,grid_rejections,grid_rejection_counts=generate_signals(
        tuple(collected_quotes),binance_states,meta,config
    )

    # Combine: pre-grid rejections + grid-level results
    all_signals=list(grid_candidates)
    all_rejections=pre_grid_rejections+list(grid_rejections)
    all_rejection_counts:dict[str,int]={}
    for k,v in pre_grid_rejection_counts.items():
        all_rejection_counts[k]=all_rejection_counts.get(k,0)+v
    for k,v in grid_rejection_counts.items():
        all_rejection_counts[k]=all_rejection_counts.get(k,0)+v

    # Compute derived stats
    binance_stale=0  # tracked by align_state internally
    poly_stale=0

    elapsed=time.time()-t0

    # Compute accounting metrics
    event_count=poly_polls  # total poll attempts that could produce events
    evaluated_event_count=pre_grid_events  # events that passed to grid evaluation
    grid_evaluation_count=len(collected_quotes)*len(config.lookback_ns_grid)*len(config.threshold_bps_grid)
    grid_rejection_total=len(grid_rejections)
    grid_candidate_total=len(grid_candidates)

    return {
        "signals":all_signals,
        "rejections":all_rejections,
        "rejection_counts":all_rejection_counts,
        "evaluated_event_count":evaluated_event_count,
        "event_count":event_count,
        "grid_evaluation_count":grid_evaluation_count,
        "grid_candidate_count":grid_candidate_total,
        "grid_rejection_count":grid_rejection_total,
        # Pre-grid rejections separately
        "pre_grid_rejection_counts":pre_grid_rejection_counts,
        "pre_grid_rejection_count":len(pre_grid_rejections),
        # Grid-level rejections separately
        "grid_rejection_counts":grid_rejection_counts,
        # Accounting contract
        "accounting_contract":"grid_level: event × lookback × threshold",
        "accounting_note":"Live capture and replay both use signal_generator.generate_signals() for grid-level evaluation. Pre-grid rejections (missing_poly, market_expired, too_close_to_expiry) are event-level only and not comparable to grid-level counts.",
        # Poll counts
        "binance_polls":binance_polls,
        "poly_polls":poly_polls,
        "binance_stale":binance_stale,
        "poly_stale":poly_stale,
        "missing_binance":grid_rejection_counts.get("stale_or_missing_binance",0),
        "missing_poly":missing_poly,
        "duration_seconds":elapsed,
    }


def write_capture(run_id:str,capture:dict,market_info:UpDownMarketInfo,config)->Path:
    """Write capture data to JSONL files."""
    d=LIVE_DATA_ROOT/run_id; d.mkdir(parents=True,exist_ok=True)
    (d/"metadata.json").write_text(json.dumps({"run_id":run_id,"market_slug":market_info.slug,"start_ns":market_info.start_ns,"end_ns":market_info.end_ns,"yes_token_id":market_info.yes_token_id,"no_token_id":market_info.no_token_id,"series_slug":market_info.series_slug,"accounting_contract":capture.get("accounting_contract","grid_level: event × lookback × threshold"),"threshold_grid":list(config.threshold_bps_grid),"lookback_grid":list(config.lookback_ns_grid),"duration_seconds":capture["duration_seconds"],"evaluated_event_count":capture.get("evaluated_event_count",0),"event_count":capture.get("event_count",0),"grid_evaluation_count":capture.get("grid_evaluation_count",0),"phase":"2_observer_only"},indent=2))
    with open(d/"polymarket_events.jsonl","w") as f:
        for e in capture.get("_poly_events",()):
            f.write(json.dumps(e)+"\n")
    with open(d/"binance_events.jsonl","w") as f:
        for e in capture.get("_binance_events",()):
            f.write(json.dumps(e)+"\n")
    with open(d/"signals.jsonl","w") as f:
        for s in capture["signals"]:
            f.write(json.dumps(asdict(s) if hasattr(s,"__dataclass_fields__") else s)+"\n")
    with open(d/"rejections.jsonl","w") as f:
        for s in capture["rejections"]:
            f.write(json.dumps(asdict(s) if hasattr(s,"__dataclass_fields__") else s)+"\n")
    (d/"heartbeat.jsonl").write_text(json.dumps({"ts_ns":int(time.time()*1e9),"status":"completed"})+"\n")
    (d/"observer_summary.json").write_text(json.dumps({
        "candidate_count":len(capture["signals"]),
        "rejection_count":len(capture["rejections"]),
        "rejection_counts":capture["rejection_counts"],
        "evaluated_event_count":capture.get("evaluated_event_count",0),
        "event_count":capture.get("event_count",0),
        "grid_evaluation_count":capture.get("grid_evaluation_count",0),
        "grid_candidate_count":capture.get("grid_candidate_count",0),
        "grid_rejection_count":capture.get("grid_rejection_count",0),
        "pre_grid_rejection_counts":capture.get("pre_grid_rejection_counts",{}),
        "pre_grid_rejection_count":capture.get("pre_grid_rejection_count",0),
        "grid_rejection_counts":capture.get("grid_rejection_counts",{}),
        "accounting_contract":capture.get("accounting_contract","grid_level: event × lookback × threshold"),
        "binance_polls":capture["binance_polls"],
        "poly_polls":capture["poly_polls"],
        "binance_stale":capture["binance_stale"],
        "poly_stale":capture["poly_stale"],
        "missing_binance":capture["missing_binance"],
        "missing_poly":capture["missing_poly"],
        "duration_seconds":capture["duration_seconds"],
    },indent=2))
    return d