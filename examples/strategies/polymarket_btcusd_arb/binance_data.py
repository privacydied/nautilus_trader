from __future__ import annotations
import io,zipfile,urllib.request
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
from .data_cache import read_cache,write_cache
from .models import BinanceReferenceState
def normalize_binance_df(df,symbol='BTCUSDT',source='trade_proxy'):
    df=df.copy()
    if 'ts_event_ns' not in df:
        for c in ('timestamp','T','time','transact_time'):
            if c in df:
                vals=pd.to_numeric(df[c]); max_v=vals.max(); scale=1_000_000 if max_v<10_000_000_000_000 else (1_000 if max_v<10_000_000_000_000_000 else 1); df['ts_event_ns']=(vals*scale).astype('int64'); break
    if 'price' not in df:
        for c in ('p','last_trade','close'):
            if c in df: df['price']=pd.to_numeric(df[c]); break
    if 'price' not in df or 'ts_event_ns' not in df: raise ValueError('Binance data requires price and timestamp')
    out=pd.DataFrame({'symbol':symbol,'price':pd.to_numeric(df.price),'ts_event_ns':pd.to_numeric(df.ts_event_ns).astype('int64'),'source':source}); out['last_trade']=out.price; out['best_bid']=None; out['best_ask']=None; out['spread_bps']=None; return out.sort_values('ts_event_ns').reset_index(drop=True)
def states_from_df(df): return tuple(BinanceReferenceState(str(r.symbol),float(r.price),int(r.ts_event_ns),last_trade=float(r.last_trade),source=str(r.source)) for r in df.itertuples(index=False))
def _dt(ns): return datetime.fromtimestamp(ns/1_000_000_000,tz=timezone.utc)
def _download(symbol,day):
    ymd=day.strftime('%Y-%m-%d'); url=f'https://data.binance.vision/data/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{ymd}.zip'
    payload=urllib.request.urlopen(url,timeout=60).read(); z=zipfile.ZipFile(io.BytesIO(payload)); return normalize_binance_df(pd.read_csv(z.open(z.namelist()[0]),header=None,names=['agg_trade_id','price','quantity','first_trade_id','last_trade_id','timestamp','is_buyer_maker','is_best_match']),symbol,'binance_vision_aggTrades_trade_proxy')
def load_binance_data(config,start_ns,end_ns,local_path=None):
    if local_path:
        p=Path(local_path); df=pd.read_parquet(p) if p.suffix=='.parquet' else pd.read_csv(p); return normalize_binance_df(df,config.binance_symbol,'local_trade_proxy'),None,'local'
    day=_dt(start_ns).strftime('%Y-%m-%d'); data=config.binance_cache_dir/config.binance_symbol/f'{day}.parquet'; meta=config.binance_cache_dir/config.binance_symbol/f'{day}.metadata.json'
    if config.use_warm_cache and not config.refresh_cache and data.exists() and meta.exists():
        df,cm=read_cache(data,meta)
        if len(df) and cm.start_ns<=start_ns and cm.end_ns>=min(start_ns,end_ns): return df,cm,'cache'
    df=_download(config.binance_symbol,_dt(start_ns)); df=df[(df.ts_event_ns>=start_ns)&(df.ts_event_ns<=end_ns)].reset_index(drop=True); cm=write_cache(df,data,meta,source='binance_vision_aggTrades',symbol_or_slug=config.binance_symbol,sanitize_info=False,loader_version_or_module='binance_data.py'); return df,cm,'network'
def align_state(states,ts_event_ns,max_staleness_ns):
    import bisect
    times=[s.ts_event_ns for s in states]; i=bisect.bisect_right(times,ts_event_ns)-1
    if i<0: return None
    s=states[i]; return s if ts_event_ns-s.ts_event_ns<=max_staleness_ns else None
