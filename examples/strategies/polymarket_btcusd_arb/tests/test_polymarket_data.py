import pandas as pd
from examples.strategies.polymarket_btcusd_arb.polymarket_data import PARTIAL_WARNING_RE,load_polymarket_quotes_from_df,_extract_updown_expiry_ns,_extract_updown_tokens,_extract_strike
from examples.strategies.polymarket_btcusd_arb.models import PolymarketContractMetadata
def test_partial_warning_regex(): assert PARTIAL_WARNING_RE.search('returning partial results due to offset ceiling')
def test_yes_quotes_selected():
 q=load_polymarket_quotes_from_df(pd.DataFrame([{'price':0.4,'size':1,'ts_event_ns':1,'ts_recv_ns':1,'token_id':'yes'}]),PolymarketContractMetadata('m',100,1000,has_no_token=True)); assert q[0].outcome=='YES'

def test_updown_slug_expiry_uses_15m_window_end():
 assert _extract_updown_expiry_ns({'market_slug':'btc-updown-15m-1767107700'},0)==1767108600000000000

def test_updown_tokens_map_up_as_yes_down_as_no():
 yes,no,has_no=_extract_updown_tokens([{'token_id':'up-token','outcome':'Up'},{'token_id':'down-token','outcome':'Down'}])
 assert yes=='up-token'
 assert no=='down-token'
 assert has_no is True

def test_updown_slug_epoch_is_not_misread_as_strike():
 assert _extract_strike({'market_slug':'btc-updown-15m-1767107700','question':'Bitcoin Up or Down - December 30, 10:15AM-10:30AM ET'})==100000.0
