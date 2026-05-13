"""Live public data capture for Phase 2 observer."""
from __future__ import annotations
import asyncio,json,time,logging,os
from dataclasses import asdict
from pathlib import Path
from typing import Any
import pandas as pd
from .models import BinanceReferenceState,PolymarketContractMetadata,PolymarketQuoteState
from .live_market_discovery import UpDownMarketInfo

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
    """Run a live observer capture for a fixed duration."""
    from .fair_probability import fair_probability
    from .signal_generator import generate_signals,maker_fee_bps
    from .binance_data import align_state
    from .config import tte_bucket_name
    t0=time.time(); end_time=t0+duration_seconds
    poll_interval=5
    signals=[]; rejections=[]; rejection_counts={}
    binance_polls=0; poly_polls=0; binance_stale=0; poly_stale=0
    missing_bn=0; missing_poly=0
    while time.time()<end_time:
        now_ns=int(time.time()*1_000_000_000)
        if not market_info.end_ns or now_ns<market_info.end_ns:
            tte=market_info.end_ns-now_ns if market_info.end_ns else 10_000_000_000_000
        else:
            tte=0
        bn_states=binance.poll_aggtrades(now_ns-5_000_000_000,now_ns)
        bn=binance.poll_bookticker()
        poly_q=poly.poll_orderbook()
        binance_polls+=1; poly_polls+=1
        if poly_q and bn and tte>config.min_tte_ns:
            binance_states=binance._states[-2000:] if binance._states else []
            aligned=align_state(binance_states,poly_q.ts_event_ns,config.max_binance_staleness_ns)
            if aligned is None:
                binance_stale+=1; missing_bn+=1
            else:
                tte_now=market_info.end_ns-poly_q.ts_event_ns if market_info.end_ns else 10_000_000_000_000
                bucket=tte_bucket_name(max(0,tte_now),config.tte_bucket_edges_ns)
                fp_result=fair_probability(aligned,tte_now,market_info.price_to_beat or 100000.0,config)
                fp=fp_result.fair_probability
                raw=(fp-poly_q.quoted_probability)*10_000.0
                mf=maker_fee_bps(poly_q.quoted_probability,config.maker_rebates_enabled)
                net=raw-mf-config.latency_buffer_bps-config.settlement_buffer_bps-config.stale_buffer_bps
                for th in threshold_grid:
                    if net>=th and tte_now>=config.min_tte_ns:
                        from .models import DivergenceSignal
                        s=DivergenceSignal(market_slug=market_info.slug,side="YES",threshold_bps=th,lookback_ns=0,tte_bucket=bucket,fair_probability=fp,quoted_probability=poly_q.quoted_probability,raw_divergence_bps=raw,maker_fee_bps=mf,spread_bps=poly_q.spread_bps or 0.0,latency_buffer_bps=config.latency_buffer_bps,settlement_buffer_bps=config.settlement_buffer_bps,stale_buffer_bps=config.stale_buffer_bps,net_divergence_bps=net,ts_event_ns=poly_q.ts_event_ns,expiry_ns=market_info.end_ns or 0)
                        signals.append(s)
        time.sleep(poll_interval)
    elapsed=time.time()-t0
    return {"signals":signals,"rejections":rejections,"rejection_counts":rejection_counts,"binance_polls":binance_polls,"poly_polls":poly_polls,"binance_stale":binance_stale,"poly_stale":poly_stale,"missing_binance":missing_bn,"missing_poly":missing_poly,"duration_seconds":elapsed}

def write_capture(run_id:str,capture:dict,market_info:UpDownMarketInfo,config)->Path:
    """Write capture data to JSONL files."""
    d=LIVE_DATA_ROOT/run_id; d.mkdir(parents=True,exist_ok=True)
    (d/"metadata.json").write_text(json.dumps({"run_id":run_id,"market_slug":market_info.slug,"start_ns":market_info.start_ns,"end_ns":market_info.end_ns,"yes_token_id":market_info.yes_token_id,"no_token_id":market_info.no_token_id,"series_slug":market_info.series_slug,"threshold_grid":config.threshold_bps_grid,"lookback_grid":config.lookback_ns_grid,"duration_seconds":capture["duration_seconds"],"phase":"2_observer_only"},indent=2))
    with open(d/"polymarket_events.jsonl","w") as f:
        for e in capture.get("_poly_events",( )):
            f.write(json.dumps(e)+"\n")
    with open(d/"binance_events.jsonl","w") as f:
        for e in capture.get("_binance_events",( )):
            f.write(json.dumps(e)+"\n")
    with open(d/"signals.jsonl","w") as f:
        for s in capture["signals"]:
            f.write(json.dumps(asdict(s) if hasattr(s,"__dataclass_fields__") else s)+"\n")
    with open(d/"rejections.jsonl","w") as f:
        for s in capture["rejections"]:
            f.write(json.dumps(asdict(s) if hasattr(s,"__dataclass_fields__") else s)+"\n")
    (d/"heartbeat.jsonl").write_text(json.dumps({"ts_ns":int(time.time()*1e9),"status":"completed"})+"\n")
    (d/"observer_summary.json").write_text(json.dumps({"candidate_count":len(capture["signals"]),"rejection_counts":capture["rejection_counts"],"binance_polls":capture["binance_polls"],"poly_polls":capture["poly_polls"],"binance_stale":capture["binance_stale"],"poly_stale":capture["poly_stale"],"duration_seconds":capture["duration_seconds"]},indent=2))
    return d