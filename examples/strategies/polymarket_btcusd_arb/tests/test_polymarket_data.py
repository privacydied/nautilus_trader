import pandas as pd
from examples.strategies.polymarket_btcusd_arb.polymarket_data import PARTIAL_WARNING_RE,load_polymarket_quotes_from_df
from examples.strategies.polymarket_btcusd_arb.models import PolymarketContractMetadata
def test_partial_warning_regex(): assert PARTIAL_WARNING_RE.search('returning partial results due to offset ceiling')
def test_yes_quotes_selected():
 q=load_polymarket_quotes_from_df(pd.DataFrame([{'price':0.4,'size':1,'ts_event_ns':1,'ts_recv_ns':1,'token_id':'yes'}]),PolymarketContractMetadata('m',100,1000,has_no_token=True)); assert q[0].outcome=='YES'
