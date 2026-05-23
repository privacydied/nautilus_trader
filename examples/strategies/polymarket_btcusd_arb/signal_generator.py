from __future__ import annotations
from collections import Counter
from .binance_data import align_state
from .config import tte_bucket_name
from .fair_probability import fair_probability
from .models import DivergenceSignal
def maker_fee_bps(price,maker_rebates_enabled=True):
    from nautilus_trader.adapters.polymarket.fee_model import PolymarketFeeModel
    PolymarketFeeModel(maker_rebates_enabled=maker_rebates_enabled); rate=0.072; rebate=0.20 if maker_rebates_enabled else 0.0; fee=price*rate*(1-price)*(1-rebate); return fee/max(price,1e-12)*10_000.0
def generate_signals(quotes,binance_states,meta,config):
    candidates=[]; rejections=[]; counts=Counter()
    for q in quotes:
        tte=meta.expiry_ns-q.ts_event_ns; bucket=tte_bucket_name(max(0,tte),config.tte_bucket_edges_ns)
        for lb in config.lookback_ns_grid:
          for th in config.threshold_bps_grid:
            base=dict(market_slug=meta.market_slug,side='YES',threshold_bps=th,lookback_ns=lb,tte_bucket=bucket,quoted_probability=q.quoted_probability,ts_event_ns=q.ts_event_ns,expiry_ns=meta.expiry_ns)
            def rej(reason,fp=0,raw=0,mf=0,net=0):
                s=DivergenceSignal(fair_probability=fp,raw_divergence_bps=raw,maker_fee_bps=mf,spread_bps=q.spread_bps or 0.0,latency_buffer_bps=config.latency_buffer_bps,settlement_buffer_bps=config.settlement_buffer_bps,stale_buffer_bps=config.stale_buffer_bps,net_divergence_bps=net,rejection_reason=reason,**base); rejections.append(s); counts[reason]+=1
            if tte<config.min_tte_ns: rej('too_close_to_expiry'); continue
            b=align_state(binance_states,q.ts_event_ns,config.max_binance_staleness_ns)
            if b is None: rej('stale_or_missing_binance'); continue
            fp=fair_probability(b,tte,meta.strike,config).fair_probability; raw=(fp-q.quoted_probability)*10_000.0; mf=maker_fee_bps(q.quoted_probability,config.maker_rebates_enabled); spread=q.spread_bps or 0.0; net=raw-mf-spread-config.latency_buffer_bps-config.settlement_buffer_bps
            s=DivergenceSignal(fair_probability=fp,raw_divergence_bps=raw,maker_fee_bps=mf,spread_bps=spread,latency_buffer_bps=config.latency_buffer_bps,settlement_buffer_bps=config.settlement_buffer_bps,stale_buffer_bps=0.0,net_divergence_bps=net,rejection_reason=None if net>=th else 'edge_below_threshold',**base)
            (candidates if net>=th else rejections).append(s)
            if net<th: counts['edge_below_threshold']+=1
    return tuple(candidates),tuple(rejections),dict(counts)
