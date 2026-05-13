import pandas as pd
from examples.strategies.polymarket_btcusd_arb.binance_data import normalize_binance_df,states_from_df,align_state,load_binance_data
from examples.strategies.polymarket_btcusd_arb.config import PolymarketArbConfig
from examples.strategies.polymarket_btcusd_arb.data_cache import write_cache
def test_normalize_trade_proxy():
 df=normalize_binance_df(pd.DataFrame([{'timestamp':1000,'price':'100.0'}])); assert df.spread_bps.iloc[0] is None; assert df.ts_event_ns.iloc[0]==1000000000

def test_normalize_binance_microsecond_timestamps():
 df=normalize_binance_df(pd.DataFrame([{'timestamp':1767108407000000,'price':'100.0'}]))
 assert df.ts_event_ns.iloc[0]==1767108407000000000
def test_align_state_staleness():
 st=states_from_df(normalize_binance_df(pd.DataFrame([{'ts_event_ns':10,'price':100.0}]))); assert align_state(st,11,10) is not None; assert align_state(st,30,10) is None

def test_load_binance_refreshes_empty_warm_cache(tmp_path,monkeypatch):
 cfg=PolymarketArbConfig(cache_dir=tmp_path,binance_cache_dir=tmp_path/'binance',polymarket_cache_dir=tmp_path/'polymarket')
 data=cfg.binance_cache_dir/cfg.binance_symbol/'2025-12-30.parquet'; meta=cfg.binance_cache_dir/cfg.binance_symbol/'2025-12-30.metadata.json'
 write_cache(pd.DataFrame({'ts_event_ns':pd.Series(dtype='int64'),'price':pd.Series(dtype='float64')}),data,meta,source='binance_vision_aggTrades',symbol_or_slug=cfg.binance_symbol,sanitize_info=False,loader_version_or_module='test')
 def fake_download(symbol,day):
  return normalize_binance_df(pd.DataFrame([{'timestamp':1767108407000000,'price':'100.0'}]),symbol,'fake')
 monkeypatch.setattr('examples.strategies.polymarket_btcusd_arb.binance_data._download',fake_download)
 df,cm,source=load_binance_data(cfg,1767108407000000000,1767108407000000000)
 assert source=='network'
 assert len(df)==1
 assert cm.row_count==1
