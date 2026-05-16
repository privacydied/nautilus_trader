from .models import ForwardOutcome
def measure_forward_outcomes(signals,quotes,horizons_ns,resolved_payoff=None):
    qs=sorted(quotes,key=lambda q:q.ts_event_ns); out=[]
    for s in signals:
      for h in horizons_ns:
        target=s.ts_event_ns+h; f=next((q for q in qs if q.ts_event_ns>=target),None); payoff=resolved_payoff if target>=s.expiry_ns else None
        if f is None: out.append(ForwardOutcome(s,h,None,None,None,None,None,payoff,None,None,'no_forward_probability')); continue
        move=(f.quoted_probability-s.quoted_probability)*10_000.0; out.append(ForwardOutcome(s,h,f.quoted_probability,move,move,move-s.maker_fee_bps,move-s.spread_bps,payoff,move>0,move<0))
    return tuple(out)
def assert_no_settlement_lookahead(outcomes):
    if any(o.signal.ts_event_ns+o.horizon_ns<o.signal.expiry_ns and o.settlement_payoff is not None for o in outcomes): raise RuntimeError('settlement_payoff populated before expiry-crossing horizon')
