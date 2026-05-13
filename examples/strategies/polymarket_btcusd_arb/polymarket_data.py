from __future__ import annotations
import asyncio,warnings,json,re
from dataclasses import asdict
import pandas as pd
from .data_cache import safe_name,write_cache,read_cache
from .models import PolymarketContractMetadata,PolymarketQuoteState
PARTIAL_WARNING_RE=re.compile(r'partial|truncat|offset ceiling|pagination|max historical activity offset',re.I)
def _extract_strike(info):
    text=' '.join(str(info.get(k,'')) for k in ('question','description','title','slug'))
    m=re.search(r'\$?([0-9]{2,3}(?:,[0-9]{3})+(?:\.\d+)?)',text) or re.search(r'\$?([0-9]{4,6}(?:\.\d+)?)',text)
    return float(m.group(1).replace(',','')) if m else 100000.0
def _row(t,slug): return {'market_slug':slug,'token_id':str(getattr(t.instrument_id,'symbol','')),'outcome':'YES','price':float(t.price),'size':float(t.size),'ts_event_ns':int(t.ts_event),'ts_recv_ns':int(t.ts_init)}
async def fetch_polymarket_trades(market_slug,*,sanitize_info=True,start=None,end=None):
    if not sanitize_info: raise ValueError('sanitize_info must be True')
    from nautilus_trader.adapters.polymarket import PolymarketDataLoader
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always',RuntimeWarning); loader=await PolymarketDataLoader.from_market_slug(market_slug,token_index=0,sanitize_info=True); trades=await loader.load_trades(start=start,end=end)
    bad=[str(w.message) for w in caught if PARTIAL_WARNING_RE.search(str(w.message))]
    if bad: raise RuntimeError('Polymarket partial/truncated data warning: '+'; '.join(bad))
    info=getattr(loader.instrument,'info',{}) or {}; tokens=info.get('tokens') or []; yes=no=None
    for tok in tokens:
        if isinstance(tok,dict) and str(tok.get('outcome','')).upper()=='YES': yes=str(tok.get('token_id') or tok.get('asset_id') or '')
        if isinstance(tok,dict) and str(tok.get('outcome','')).upper()=='NO': no=str(tok.get('token_id') or tok.get('asset_id') or '')
    meta=PolymarketContractMetadata(market_slug,_extract_strike(info),int(loader.instrument.expiration_ns),str(info.get('condition_id') or info.get('conditionId') or ''),yes,no,has_no_token=bool(no),sanitized_info=True,resolution_metadata=getattr(loader,'resolution_metadata',{}) or {})
    df=pd.DataFrame([_row(t,market_slug) for t in trades]);
    if not df.empty: df=df.sort_values(['ts_event_ns','price','size']).reset_index(drop=True)
    return df,meta
def load_polymarket_quotes_from_df(df,meta):
    return tuple(PolymarketQuoteState(meta.market_slug,str(r.get('token_id','')),'YES',float(r.get('price')),int(r.get('ts_event_ns')),int(r.get('ts_recv_ns',r.get('ts_event_ns'))),last_trade=float(r.get('price')),depth=float(r.get('size',0.0))) for r in df.to_dict('records'))
def load_polymarket_data(config,start=None,end=None):
    slug=safe_name(config.market_slug); data=config.polymarket_cache_dir/f'{slug}.parquet'; mp=config.polymarket_cache_dir/f'{slug}.metadata.json'; cp=config.polymarket_cache_dir/f'{slug}.contract.json'
    if config.use_warm_cache and not config.refresh_cache and data.exists() and mp.exists():
        df,cm=read_cache(data,mp); meta=PolymarketContractMetadata(**json.loads(cp.read_text())) if cp.exists() else PolymarketContractMetadata(config.market_slug,100000.0,int(df.ts_event_ns.max()+900_000_000_000) if len(df) else 0); return df,meta,cm,'cache'
    df,meta=asyncio.run(fetch_polymarket_trades(config.market_slug,sanitize_info=True,start=start,end=end)); cm=write_cache(df,data,mp,source='polymarket_data_api',symbol_or_slug=config.market_slug,sanitize_info=True,loader_version_or_module='PolymarketDataLoader.from_market_slug'); cp.write_text(json.dumps(asdict(meta),indent=2,sort_keys=True)); return df,meta,cm,'network'
