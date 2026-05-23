from examples.strategies.polymarket_btcusd_arb.baseline import generate_random_baseline
from examples.strategies.polymarket_btcusd_arb.config import PolymarketArbConfig
from examples.strategies.polymarket_btcusd_arb.models import PolymarketQuoteState
def test_baseline_deterministic():
 qs=(PolymarketQuoteState('m','y','YES',0.5,1),PolymarketQuoteState('m','y','YES',0.6,2_000_000_000)); cfg=PolymarketArbConfig(baseline_sample_count=3,random_seed=7); assert generate_random_baseline(qs,3,cfg,10)==generate_random_baseline(qs,3,cfg,10)
