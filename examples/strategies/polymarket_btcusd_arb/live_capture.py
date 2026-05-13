"""Live public data capture for Phase 2 observer."""
from __future__ import annotations
import asyncio,json,time,logging,os
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
        from datetime import datetime,timezone
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

    Emits rejection records for every evaluated event that does not become a candidate,
    tracking rejection reasons explicitly.
    """
    from .fair_probability import fair_probability
    from .signal_generator import maker_fee_bps
    from .binance_data import align_state

    t0=time.time(); end_time=t0+duration_seconds
    poll_interval=5
    signals=[]; rejections=[]; rejection_counts:dict[str,int]={}
    binance_polls=0; poly_polls=0; binance_stale=0; poly_stale=0
    missing_bn=0; missing_poly=0; evaluated_events=0

    while time.time()<end_time:
        now_ns=int(time.time()*1_000_000_000)
        # Compute time-to-expiry
        if not market_info.end_ns or now_ns<market_info.end_ns:
            tte=market_info.end_ns-now_ns if market_info.end_ns else 10_000_000_000_000
        else:
            tte=0  # Market has expired

        # Poll Binance
        bn_states=binance.poll_aggtrades(now_ns-5_000_000_000,now_ns)
        bn=binance.poll_bookticker()
        # Poll Polymarket
        poly_q=poly.poll_orderbook()
        binance_polls+=1; poly_polls+=1

        # If Polymarket quote is missing, skip evaluation but count
        if poly_q is None:
            missing_poly+=1
            time.sleep(poll_interval)
            continue

        # If too close to expiry, record rejection
        if tte<=0:
            r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=0,lookback_ns=0,tte_bucket="expired",fair_probability=0.0,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=0.0,maker_fee_bps=0.0,spread_bps=poly_q.spread_bps or 0.0,latency_buffer_bps=0.0,settlement_buffer_bps=0.0,stale_buffer_bps=0.0,net_divergence_bps=0.0,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="market_expired")
            rejections.append(r); rejection_counts["market_expired"]=rejection_counts.get("market_expired",0)+1
            time.sleep(poll_interval)
            continue

        # If TTE too small, record rejection
        if tte<config.min_tte_ns:
            bucket=tte_bucket_name(max(0,tte),config.tte_bucket_edges_ns)
            r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=0,lookback_ns=0,tte_bucket=bucket,fair_probability=0.0,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=0.0,maker_fee_bps=0.0,spread_bps=poly_q.spread_bps or 0.0,latency_buffer_bps=0.0,settlement_buffer_bps=0.0,stale_buffer_bps=0.0,net_divergence_bps=0.0,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="too_close_to_expiry")
            rejections.append(r); rejection_counts["too_close_to_expiry"]=rejection_counts.get("too_close_to_expiry",0)+1
            time.sleep(poll_interval)
            continue

        # Try to align Binance state
        binance_states=binance._states[-2000:] if binance._states else []
        aligned=align_state(binance_states,poly_q.ts_event_ns,config.max_binance_staleness_ns)

        if aligned is None:
            missing_bn+=1
            r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=0,lookback_ns=0,tte_bucket=tte_bucket_name(max(0,tte),config.tte_bucket_edges_ns),fair_probability=0.0,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=0.0,maker_fee_bps=0.0,spread_bps=poly_q.spread_bps or 0.0,latency_buffer_bps=0.0,settlement_buffer_bps=0.0,stale_buffer_bps=0.0,net_divergence_bps=0.0,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="stale_or_missing_binance")
            rejections.append(r); rejection_counts["stale_or_missing_binance"]=rejection_counts.get("stale_or_missing_binance",0)+1
            time.sleep(poll_interval)
            continue

        # We have a valid Polymarket quote and aligned Binance state - evaluate
        evaluated_events+=1
        tte_now=market_info.end_ns-poly_q.ts_event_ns if market_info.end_ns else 10_000_000_000_000
        bucket=tte_bucket_name(max(0,tte_now),config.tte_bucket_edges_ns)
        fp_result=fair_probability(aligned,tte_now,market_info.price_to_beat or 100000.0,config)
        fp=fp_result.fair_probability
        raw=(fp-poly_q.quoted_probability)*10_000.0
        mf=maker_fee_bps(poly_q.quoted_probability,config.maker_rebates_enabled)
        spread=poly_q.spread_bps or 0.0
        net=raw-mf-spread-config.latency_buffer_bps-config.settlement_buffer_bps-config.stale_buffer_bps

        # Check for spread too wide
        max_spread=getattr(config,"max_spread_bps",200.0)
        if spread>max_spread:
            r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=0,lookback_ns=0,tte_bucket=bucket,fair_probability=fp,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=raw,maker_fee_bps=mf,spread_bps=spread,latency_buffer_bps=config.latency_buffer_bps,settlement_buffer_bps=config.settlement_buffer_bps,stale_buffer_bps=config.stale_buffer_bps,net_divergence_bps=net,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="spread_too_wide")
            rejections.append(r); rejection_counts["spread_too_wide"]=rejection_counts.get("spread_too_wide",0)+1
            time.sleep(poll_interval)
            continue

        # Evaluate against each threshold
        for th in threshold_grid:
            if net>=th:
                s=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=th,lookback_ns=0,tte_bucket=bucket,fair_probability=fp,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=raw,maker_fee_bps=mf,spread_bps=spread,latency_buffer_bps=config.latency_buffer_bps,settlement_buffer_bps=config.settlement_buffer_bps,stale_buffer_bps=config.stale_buffer_bps,net_divergence_bps=net,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0)
                signals.append(s)
            else:
                r=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=th,lookback_ns=0,tte_bucket=bucket,fair_probability=fp,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=raw,maker_fee_bps=mf,spread_bps=spread,latency_buffer_bps=config.latency_buffer_bps,settlement_buffer_bps=config.settlement_buffer_bps,stale_buffer_bps=config.stale_buffer_bps,net_divergence_bps=net,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0,rejection_reason="edge_below_threshold")
                rejections.append(r); rejection_counts["edge_below_threshold"]=rejection_counts.get("edge_below_threshold",0)+1

        time.sleep(poll_interval)

    elapsed=time.time()-t0
    return {"signals":signals,"rejections":rejections,"rejection_counts":rejection_counts,"evaluated_event_count":evaluated_events,"binance_polls":binance_polls,"poly_polls":poly_polls,"binance_stale":binance_stale,"poly_stale":poly_stale,"missing_binance":missing_bn,"missing_poly":missing_poly,"duration_seconds":elapsed}

def write_capture(run_id:str,capture:dict,market_info:UpDownMarketInfo,config)->Path:
    """Write capture data to JSONL files."""
    d=LIVE_DATA_ROOT/run_id; d.mkdir(parents=True,exist_ok=True)
    (d/"metadata.json").write_text(json.dumps({"run_id":run_id,"market_slug":market_info.slug,"start_ns":market_info.start_ns,"end_ns":market_info.end_ns,"yes_token_id":market_info.yes_token_id,"no_token_id":market_info.no_token_id,"series_slug":market_info.series_slug,"threshold_grid":list(config.threshold_bps_grid if hasattr(config,'threshold_bps_grid') else threshold_grid),"lookback_grid":list(config.lookback_ns_grid),"duration_seconds":capture["duration_seconds"],"evaluated_event_count":capture.get("evaluated_event_count",0),"phase":"2_observer_only"},indent=2))
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
    (d/"observer_summary.json").write_text(json.dumps({"candidate_count":len(capture["signals"]),"rejection_count":len(capture["rejections"]),"rejection_counts":capture["rejection_counts"],"evaluated_event_count":capture.get("evaluated_event_count",0),"binance_polls":capture["binance_polls"],"poly_polls":capture["poly_polls"],"binance_stale":capture["binance_stale"],"poly_stale":capture["poly_stale"],"missing_binance":capture["missing_binance"],"missing_poly":capture["missing_poly"],"duration_seconds":capture["duration_seconds"]},indent=2))
    return d