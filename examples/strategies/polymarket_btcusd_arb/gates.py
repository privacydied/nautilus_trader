from statistics import mean,median
from .models import CandidateGroupResult
def evaluate_candidate_group(signals,outcomes,baseline,*,lookback_ns,threshold_bps,tte_bucket,min_events=5):
    if not signals: return CandidateGroupResult(lookback_ns,threshold_bps,tte_bucket,'NEEDS_MORE_DATA','no_events_met_threshold',0)
    vals=[o.maker_fee_adjusted_edge_bps for o in outcomes if o.maker_fee_adjusted_edge_bps is not None]
    if len(vals)<min_events: return CandidateGroupResult(lookback_ns,threshold_bps,tte_bucket,'NEEDS_MORE_DATA',f'insufficient_events: {len(vals)} < {min_events}',len(signals))
    bvals=[b.maker_fee_adjusted_edge_bps for b in baseline if b.maker_fee_adjusted_edge_bps is not None and b.tte_bucket==tte_bucket]
    m=mean(vals); med=median(vals); wr=sum(v>0 for v in vals)/len(vals); bm=mean(bvals) if bvals else None
    gates={'event_count':len(vals)>=min_events,'mean_edge_positive':m>0,'median_edge':med>-5,'win_rate':wr>0.5,'beats_random_baseline':bm is not None and m>bm+1.0,'not_single_event_dominated':mean(sorted(vals)[:-1])>0 if len(vals)>1 else False,'maker_fee_survival_gate':all(s.net_divergence_bps>=s.threshold_bps for s in signals),'settlement_leakage_guard':all(o.settlement_payoff is None for o in outcomes if o.signal.ts_event_ns+o.horizon_ns<o.signal.expiry_ns)}
    verdict='CANDIDATE_FOR_LONGER_OBSERVATION' if all(gates.values()) else 'REJECTED'; reason='passed_all_gates' if verdict.startswith('CANDIDATE') else ','.join(k for k,v in gates.items() if not v)
    return CandidateGroupResult(lookback_ns,threshold_bps,tte_bucket,verdict,reason,len(signals),m,med,wr,bm,None if bm is None else m-bm,gates)
def evaluate_grid(signals,outcomes,baseline,config):
    buckets={f'{lo//1_000_000_000}s-{hi//1_000_000_000}s' for lo,hi in zip(config.tte_bucket_edges_ns,config.tte_bucket_edges_ns[1:])}|{s.tte_bucket for s in signals}
    return tuple(evaluate_candidate_group(tuple(s for s in signals if s.lookback_ns==lb and s.threshold_bps==th and s.tte_bucket==b),tuple(o for o in outcomes if o.signal.lookback_ns==lb and o.signal.threshold_bps==th and o.signal.tte_bucket==b),baseline,lookback_ns=lb,threshold_bps=th,tte_bucket=b,min_events=config.min_events) for lb in config.lookback_ns_grid for th in config.threshold_bps_grid for b in sorted(buckets))
