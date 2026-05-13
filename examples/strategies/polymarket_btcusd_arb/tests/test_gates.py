from examples.strategies.polymarket_btcusd_arb.gates import evaluate_candidate_group
from examples.strategies.polymarket_btcusd_arb.models import *
def mk(edge):
 s=DivergenceSignal('m','YES',5,1,'b',.6,.5,1000,1,0,0,0,0,999,1,100); return ForwardOutcome(s,1,.6,edge,edge,edge,edge,None,edge>0,edge<0)
def test_zero_candidate_group():
 g=evaluate_candidate_group((),(),(),lookback_ns=1,threshold_bps=5,tte_bucket='b'); assert g.verdict=='NEEDS_MORE_DATA' and g.reason=='no_events_met_threshold'
def test_losing_rejected():
 o=mk(-1); g=evaluate_candidate_group((o.signal,),(o,)*5,(),lookback_ns=1,threshold_bps=5,tte_bucket='b',min_events=1); assert g.verdict=='REJECTED'
