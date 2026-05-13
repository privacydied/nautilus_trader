from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
CACHE_ROOT=Path('/mnt/nasirjones/py/nautilus_trader/cache/polymarket_btcusd_arb')
DEFAULT_DOCS={'lookback_ns_grid':'nanoseconds; hypothesis parameter; required 1s/5s/15s/30s/60s windows.','threshold_bps_grid':'bps; hypothesis parameter.','max_binance_staleness_ns':'nanoseconds; conservative safety parameter.','min_tte_ns':'nanoseconds; conservative safety parameter.'}
@dataclass(frozen=True)
class PolymarketArbConfig:
    market_slug:str='btc-updown-development-fixture'; binance_symbol:str='BTCUSDT'; threshold_bps_grid:tuple[float,...]=(5.0,10.0,20.0,40.0); lookback_ns_grid:tuple[int,...]=(1_000_000_000,5_000_000_000,15_000_000_000,30_000_000_000,60_000_000_000); forward_horizon_ns_grid:tuple[int,...]=(1_000_000_000,5_000_000_000,15_000_000_000,30_000_000_000,60_000_000_000); tte_bucket_edges_ns:tuple[int,...]=(0,60_000_000_000,180_000_000_000,480_000_000_000,10_000_000_000_000); max_binance_staleness_ns:int=2_000_000_000; max_polymarket_staleness_ns:int=2_000_000_000; min_tte_ns:int=30_000_000_000; max_spread_bps:float=200.0; min_depth:float=0.0; latency_buffer_bps:float=2.0; settlement_buffer_bps:float=2.0; stale_buffer_bps:float=1.0; min_events:int=5; baseline_sample_count:int=100; random_seed:int=42; sanitize_info:bool=True; maker_rebates_enabled:bool=True; cache_dir:Path=CACHE_ROOT; polymarket_cache_dir:Path=CACHE_ROOT/'polymarket'; binance_cache_dir:Path=CACHE_ROOT/'binance'; use_warm_cache:bool=True; refresh_cache:bool=False; fail_on_lookahead:bool=True; config_docs:dict[str,str]=field(default_factory=lambda:dict(DEFAULT_DOCS))
    def __post_init__(self):
        if not self.sanitize_info: raise ValueError('sanitize_info must be True in Phase 1')
        if any(x<=0 for x in self.threshold_bps_grid): raise ValueError('thresholds must be positive')
        if any(x<=0 for x in self.lookback_ns_grid+self.forward_horizon_ns_grid): raise ValueError('time grids must be positive ns')
        if tuple(sorted(self.tte_bucket_edges_ns))!=self.tte_bucket_edges_ns or len(self.tte_bucket_edges_ns)<2: raise ValueError('bad TTE buckets')
        base=self.cache_dir.resolve()
        for p in (self.polymarket_cache_dir.resolve(),self.binance_cache_dir.resolve()):
            if base not in (p,*p.parents): raise ValueError('cache paths must resolve under cache_dir')
def parse_utc_ns(value:str|None)->int|None:
    if value is None: return None
    text=value.strip();
    if 'T' not in text and len(text)==10: text+='T00:00:00Z'
    if text.endswith('Z'): text=text[:-1]+'+00:00'
    dt=datetime.fromisoformat(text)
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp()*1_000_000_000)
def tte_bucket_name(tte_ns:int, edges:tuple[int,...])->str:
    for lo,hi in zip(edges,edges[1:]):
        if lo<=tte_ns<hi: return f'{lo//1_000_000_000}s-{hi//1_000_000_000}s'
    return f'>={edges[-1]//1_000_000_000}s'
