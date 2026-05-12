"""V6: Cross-venue spread calculator.
Only calculates if quote currencies match exactly.
"""
from dataclasses import dataclass
from typing import Optional
from .venues import Ticker

@dataclass(frozen=True)
class Opportunity:
    timestamp_ms: int
    symbol: str
    buy_venue: str
    sell_venue: str
    buy_ask: float
    sell_bid: float
    gross_edge_usd: float
    gross_edge_bps: float
    taker_fee_buy_bps: float
    taker_fee_sell_bps: float
    estimated_fees_bps: float
    latency_buffer_bps: float
    net_edge_bps: float
    is_profitable: bool

def calculate_opportunity(t1, t2, fee_a_bps=40.0, fee_b_bps=40.0, latency_buffer_bps=10.0):
    if t1.quote != t2.quote:
        return None
    if t1.venue == t2.venue:
        return None
    if t1.bid is None or t1.ask is None or t2.bid is None or t2.ask is None:
        return None
    if t1.bid <= 0 or t2.bid <= 0 or t1.ask <= 0 or t2.ask <= 0:
        return None

    # Direction 1: buy on t1, sell on t2
    edge12 = t2.bid - t1.ask
    mid12 = (t1.ask + t2.bid) / 2.0
    edge12_bps = (edge12 / mid12) * 10000

    # Direction 2: buy on t2, sell on t1
    edge21 = t1.bid - t2.ask
    mid21 = (t2.ask + t1.bid) / 2.0
    edge21_bps = (edge21 / mid21) * 10000

    if edge12_bps > edge21_bps:
        buy_venue, sell_venue = t1.venue, t2.venue
        buy_ask, sell_bid = t1.ask, t2.bid
        edge_usd, edge_bps = edge12, edge12_bps
        fee_buy, fee_sell = fee_a_bps, fee_b_bps
    else:
        buy_venue, sell_venue = t2.venue, t1.venue
        buy_ask, sell_bid = t2.ask, t1.bid
        edge_usd, edge_bps = edge21, edge21_bps
        fee_buy, fee_sell = fee_b_bps, fee_a_bps

    total_fees = fee_buy + fee_sell
    net = edge_bps - total_fees - latency_buffer_bps
    return Opportunity(
        timestamp_ms=t1.ts_recv_ms,
        symbol=t1.symbol,
        buy_venue=buy_venue,
        sell_venue=sell_venue,
        buy_ask=round(buy_ask, 4),
        sell_bid=round(sell_bid, 4),
        gross_edge_usd=round(edge_usd, 4),
        gross_edge_bps=round(edge_bps, 4),
        taker_fee_buy_bps=fee_buy,
        taker_fee_sell_bps=fee_sell,
        estimated_fees_bps=round(total_fees, 2),
        latency_buffer_bps=latency_buffer_bps,
        net_edge_bps=round(net, 4),
        is_profitable=net > 0,
    )
