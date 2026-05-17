# DEX-CEX Spot Dislocation Report

> **This is an observer-only research report.**
> No orders, no execution, no private keys, no live trading.
> **This is not a trading recommendation.**

## Study Parameters

- **DEX source:** data/dex_cex_spot_dislocation_v1/dex_snapshots.jsonl
- **Target CEX venues:** kraken,coinbase
- **Asset universe:** LINK
- **Horizons (ms):** 30000,60000,300000,900000,3600000
- **Fee (bps):** 40.0
- **Slippage (bps):** 5.0
- **Stale data buffer (bps):** 10.0
- **Quote mismatch buffer (bps):** 5.0
- **Total cost (bps):** 60.0
- **Min pool liquidity (USD):** 500,000
- **Min 1h volume (USD):** 100,000

## Results by Group

| Venue | Asset | Horizon(ms) | Events | Valid | Mean Net(bps) | Win Rate | Candidate |
|-------|-------|-------------|--------|-------|---------------|----------|-----------|

## Summary Stats

- Total DEX snapshots: 18
- Total dislocation events: 0
- Valid forward returns: 0
- Rejected: 0
- Candidate groups: 0
- Warnings: 1
  - No dislocation events generated

## Final Verdict

**NEEDS_MORE_DATA**
Insufficient events to evaluate. Collect more DEX data and re-run.
