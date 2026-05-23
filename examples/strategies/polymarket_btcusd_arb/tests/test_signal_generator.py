from examples.strategies.polymarket_btcusd_arb.config import PolymarketArbConfig
from examples.strategies.polymarket_btcusd_arb.models import *
from examples.strategies.polymarket_btcusd_arb.signal_generator import generate_signals
def test_candidate_when_fair_gt_quote():
 cfg=PolymarketArbConfig(threshold_bps_grid=(5,),lookback_ns_grid=(1_000_000_000,),latency_buffer_bps=0,settlement_buffer_bps=0); meta=PolymarketContractMetadata('m',100,200_000_000_000); q=(PolymarketQuoteState('m','yes','YES',0.1,1_000_000_000),); b=(BinanceReferenceState('BTCUSDT',120,1_000_000_000),); c,r,_=generate_signals(q,b,meta,cfg); assert c and not c[0].rejection_reason
def test_missing_binance_rejects():
 cfg=PolymarketArbConfig(threshold_bps_grid=(5,),lookback_ns_grid=(1_000_000_000,)); meta=PolymarketContractMetadata('m',100,200_000_000_000); c,r,counts=generate_signals((PolymarketQuoteState('m','yes','YES',0.5,1_000_000_000),),(),meta,cfg); assert not c and counts['stale_or_missing_binance']==1
