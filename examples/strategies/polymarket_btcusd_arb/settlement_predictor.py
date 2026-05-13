from __future__ import annotations
import math
from .models import BinanceReferenceState,SettlementPredictionResult
def predict_settlement(state:BinanceReferenceState|None,strike:float|None,expiry_ns:int|None,now_ns:int|None=None)->SettlementPredictionResult:
    if state is None or strike is None or expiry_ns is None or strike<=0 or state.price<=0: raise ValueError('missing strike/expiry/state blocks settlement prediction')
    score=max(-2.5,min(2.5,((state.price-strike)/strike*10_000.0)/100.0)); p=1/(1+math.exp(-2.4*score)); conf=max(0,min(1,abs(p-0.5)*2)); direction='UP' if p>0.55 else 'DOWN' if p<0.45 else 'NEUTRAL'
    return SettlementPredictionResult(p,conf,direction,score)
