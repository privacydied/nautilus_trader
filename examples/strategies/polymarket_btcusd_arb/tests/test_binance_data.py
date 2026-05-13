import pandas as pd
from examples.strategies.polymarket_btcusd_arb.binance_data import normalize_binance_df,states_from_df,align_state
def test_normalize_trade_proxy():
 df=normalize_binance_df(pd.DataFrame([{'timestamp':1000,'price':'100.0'}])); assert df.spread_bps.iloc[0] is None; assert df.ts_event_ns.iloc[0]==1000000000
def test_align_state_staleness():
 st=states_from_df(normalize_binance_df(pd.DataFrame([{'ts_event_ns':10,'price':100.0}]))); assert align_state(st,11,10) is not None; assert align_state(st,30,10) is None
