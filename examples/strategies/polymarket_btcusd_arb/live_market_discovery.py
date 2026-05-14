"""Discover active BTC 15m UpDown markets from Polymarket Gamma API."""
from __future__ import annotations
import json,urllib.request
from dataclasses import dataclass
from typing import Any

GAMMA_BASE="https://gamma-api.polymarket.com"

@dataclass(frozen=True)
class UpDownMarketInfo:
    slug:str; question:str; active:bool; closed:bool; condition_id:str; yes_token_id:str|None; no_token_id:str|None; start_ns:int|None; end_ns:int|None; series_slug:str|None; resolution_source:str|None; price_to_beat:float|None=None; price_to_beat_source:str|None=None

def _ts(v:Any)->int|None:
    if v is None: return None
    s=str(v)[:26].replace('Z','').replace('+00:00','')
    try:
        from datetime import datetime,timezone
        return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()*1_000_000_000)
    except Exception: return None

def _gamma_get(path:str,params:dict[str,str]|None=None,timeout:int=30)->list[dict]:
    qs="&".join(f"{k}={v}" for k,v in (params or {}).items())
    url=f"{GAMMA_BASE}/{path}?{qs}" if params else f"{GAMMA_BASE}/{path}"
    req=urllib.request.Request(url,headers={"User-Agent":"NautilusTrader/Phase2Observer"})
    resp=urllib.request.urlopen(req,timeout=timeout)
    return json.loads(resp.read())

def _parse_tokens(market:dict)->tuple[str|None,str|None]:
    raw=market.get("clobTokenIds") or "[]"
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception: raw=[]
    outcomes=market.get("outcomes") or "[]"
    if isinstance(outcomes,str):
        try: outcomes=json.loads(outcomes)
        except Exception: outcomes=[]
    tokens=raw if isinstance(raw,list) else []
    yes_token=None; no_token=None
    for i,tok in enumerate(tokens):
        outcome=str(outcomes[i]).upper() if i<len(outcomes) else ""
        if outcome in ("YES","UP"): yes_token=str(tok)
        if outcome in ("NO","DOWN"): no_token=str(tok)
    return yes_token,no_token

def discover_btc_15m_updown()->list[UpDownMarketInfo]:
    """Discover currently active/open BTC 15m UpDown markets."""
    markets=_gamma_get("markets",{"active":"true","closed":"false","limit":"200","order":"startDate","ascending":"false"})
    btc_15m=[]
    for m in markets:
        slug=str(m.get("slug","")).lower()
        question=str(m.get("question","")).lower()
        if not ("updown" in slug or "up/down" in question or "up or down" in question): continue
        if not ("btc" in slug or "bitcoin" in question): continue
        if "-15m-" not in slug: continue
        yes,no=_parse_tokens(m)
        info=UpDownMarketInfo(slug=m.get("slug",""),question=m.get("question",""),active=bool(m.get("active",False)),closed=bool(m.get("closed",True)),condition_id=m.get("conditionId",""),yes_token_id=yes,no_token_id=no,start_ns=_ts(m.get("startDate") or m.get("eventStartTime")),end_ns=_ts(m.get("endDate")),series_slug=m.get("seriesSlug"),resolution_source=m.get("resolutionSource"),price_to_beat=None,price_to_beat_source=None)
        btc_15m.append(info)
    return btc_15m

def fetch_market_detail(slug:str)->dict|None:
    """Fetch market detail from Gamma API by slug."""
    try:
        events=_gamma_get("events",{"slug":slug})
        if events:
            ev=events[0]
            markets=ev.get("markets",[])
            if markets:
                m=markets[0]
                m["event_title"]=ev.get("title","")
                m["series_slug"]=ev.get("series",{}).get("slug","") if isinstance(ev.get("series"),list) else ""
                return m
    except Exception: pass
    try:
        markets=_gamma_get("markets",{"slug":slug})
        if markets: return markets[0]
    except Exception: pass
    return None