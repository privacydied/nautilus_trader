from examples.strategies.polymarket_btcusd_arb.models import *
from examples.strategies.polymarket_btcusd_arb.forward_returns import measure_forward_outcomes,assert_no_settlement_lookahead
def sig(expiry=100): return DivergenceSignal('m','YES',5,1,'b',0.6,0.5,1000,1,0,0,0,0,999,10,expiry)
def test_settlement_none_before_expiry(): assert measure_forward_outcomes((sig(100),),(PolymarketQuoteState('m','y','YES',0.6,20),),(10,),resolved_payoff=1.0)[0].settlement_payoff is None
def test_settlement_after_expiry(): assert measure_forward_outcomes((sig(15),),(PolymarketQuoteState('m','y','YES',0.6,20),),(10,),resolved_payoff=1.0)[0].settlement_payoff==1.0
def test_lookahead_guard_catches():
 import pytest
 with pytest.raises(RuntimeError): assert_no_settlement_lookahead((ForwardOutcome(sig(100),10,None,None,None,None,None,1.0,None,None),))
