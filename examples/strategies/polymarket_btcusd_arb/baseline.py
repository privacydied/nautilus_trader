import random
from .models import BaselineResult
from .config import tte_bucket_name
def generate_random_baseline(quotes,sample_count,config,expiry_ns):
    if not quotes or sample_count<=0: return ()
    rng=random.Random(config.random_seed); qs=sorted(quotes,key=lambda q:q.ts_event_ns); out=[]
    for _ in range(sample_count):
        q=rng.choice(qs); h=rng.choice(config.forward_horizon_ns_grid); f=next((x for x in qs if x.ts_event_ns>=q.ts_event_ns+h),None); edge=None if f is None else (f.quoted_probability-q.quoted_probability)*10_000.0; out.append(BaselineResult(q.ts_event_ns,tte_bucket_name(max(0,expiry_ns-q.ts_event_ns),config.tte_bucket_edges_ns),h,edge))
    return tuple(out)
