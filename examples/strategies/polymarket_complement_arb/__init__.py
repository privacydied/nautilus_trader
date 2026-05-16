"""
Polymarket Global complementary YES+NO arbitrage strategy.

Same-condition binary YES+NO pairs on Polymarket Global may occasionally be
quoteable or executable below $1 after fees, spread/depth, stale-book filtering,
signing latency, cancel/replace latency, gas/redeem estimate, resolution timing
risk, and leg-risk buffer.

This is a maker-default mispricing harvester with bounded leg risk.
It is not a free-money machine.
Most opportunities should not fill.
Low fill rate is expected.
"""