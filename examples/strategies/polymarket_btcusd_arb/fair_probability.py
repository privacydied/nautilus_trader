from __future__ import annotations
import math
from .models import BinanceReferenceState,FairProbabilityResult
from .config import PolymarketArbConfig
SECONDS_PER_YEAR=365.0*24*60*60
def binary_call_probability(spot:float,strike:float,time_to_expiry_years:float,sigma:float,risk_free_rate:float=0.0)->float:
    if not all(math.isfinite(v) for v in (spot,strike,time_to_expiry_years,sigma,risk_free_rate)): return 0.5
    if strike<=0 or spot<=0: return 0.5
    if time_to_expiry_years<=0 or sigma<=0: return 1.0 if spot>strike else 0.0
    denom=sigma*math.sqrt(time_to_expiry_years)
    if denom<=0: return 1.0 if spot>strike else 0.0
    d2=(math.log(spot/strike)+(risk_free_rate-0.5*sigma*sigma)*time_to_expiry_years)/denom
    return max(0.0,min(1.0,0.5*(1.0+math.erf(d2/math.sqrt(2.0)))))
def fair_probability(binance_state:BinanceReferenceState,time_to_expiry_ns:int,strike:float,config:PolymarketArbConfig)->FairProbabilityResult:
    if binance_state is None or binance_state.price<=0 or strike<=0 or time_to_expiry_ns<0: raise ValueError('invalid fair_probability inputs')
    t=(time_to_expiry_ns/1_000_000_000)/SECONDS_PER_YEAR; sigma=0.70
    return FairProbabilityResult(binary_call_probability(binance_state.price,strike,t,sigma,0.05),sigma,t,expiry_configured=True)
