# Polymarket Complement Arb — Final Research Status

**Status**: `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE`

**Hypothesis**: Complement arbitrage on Polymarket — buying the YES/UP token and the NO/DOWN token of the same condition as maker quotes, collecting the gross edge when the pair executes fully before timeout, net of fees, buffers, and unwind losses.

**This is not a rejection of the mathematical possibility of complement arbitrage.**
**This is not a candidate for live trading.**

---

## Campaign Summary

A narrow, repetition-based falsification campaign was conducted across 7 separated windows on crypto Up/Down duration markets (BTC and ETH, 5m/15m/1h/4h/daily durations). A later hourly addendum resolved the 1h slug discovery gap.

### Consolidated Results

| Metric | Value |
|---|---|
| Separated windows | 7 |
| Slugs tested | 8 |
| Assets tested | BTC, ETH |
| Durations tested | 5m, 15m, 1h, 4h, daily |
| Trade evidence status | **READY** (7/7 windows) |
| Missing trade data events | 0 |
| Total detected opportunities | 1565 |
| Total non-dust opportunities | 1155 |
| **Pessimistic paired fills** | **0** |
| One-leg fills | 0 |
| Paired gain | 0 |
| Unwind loss | 0 |
| Net shadow harvest | 0 |
| Expected edge per detected opportunity | 0.0 |
| **Final status** | **FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE** |

### Per-Window Breakdown

| # | Window | Slug | Detected | Non-dust | Pess Fills | Evidence |
|---|---|---|---|---|---|---|
| 1 | BTC 5m | `btc-updown-5m-1778954700` | 3 | 0 | 0 | READY |
| 2 | BTC 15m | `btc-updown-15m-1778954400` | 27 | 24 | 0 | READY |
| 3 | ETH 5m | `eth-updown-5m-1778954700` | 0 | 0 | 0 | READY |
| 4 | ETH 15m | `eth-updown-15m-1778954400` | 3 | 3 | 0 | READY |
| 5 | BTC 4h+daily | `btc-updown-4h-1778947200`, `bitcoin-up-or-down-on-may-17-2026` | 1473 | 978 | 0 | READY |
| 6 | BTC 15m (closeout) | `btc-updown-15m-1778956200` | 35 | 93 | 0 | READY |
| 7 | BTC 1h (addendum) | `bitcoin-up-or-down-may-16-2026-2pm-et` | 24 | 57 | 0 | READY |

### Structural Pincer

The failure mode is a pincer:

- **Short durations (5m)**: Have turnover but no economic depth — zero non-dust opportunities
- **Medium durations (15m)**: Detect edges (27-35 opportunities) with non-dust sizes (24-93), but zero queue turnover — no pessimistic fill evidence
- **Hourly (1h)**: Edges present (24 detections, 57 non-dust) but same zero fill result as other durations
- **Long durations (4h/daily)**: Abundant theoretical edge (1473 detections, 978 non-dust) but same zero fill result

### Fee Model

- Formula: `fee = shares * feeRate * price * (1 - price)`
- Fee source: Gamma API feeSchedule.rate per condition
- Maker fee: 0 (standard Polymarket structure, conservative assumption)
- Taker-taker breakeven derived per condition, not hardcoded

---

## Resolved Questions

* Can the Data API filter trades by market? **Yes, using `market=<conditionId>` (comma-separated)**
* Does the CLOB book endpoint work? **Yes**
* Are crypto Up/Down markets discoverable? **Yes, via event-slug resolution**
* Does the 1h hourly slug pattern exist? **Yes, e.g. `bitcoin-up-or-down-may-16-2026-2pm-et` — uses a different naming convention from 5m/15m/4h slugs**
* Is public trade evidence sufficient for pessimistic queue-fill evaluation? **Confirmed as operational — missing_trade_data_events = 0**

---

## Remaining Unaddressed

1. **Size ladder** (5/10/25/50 shares) was not implemented as a separate diagnostic run
2. **Resolution truncation** fields exist in code but runner does not fully populate them
3. **Frozen status applies to base 100-share pessimistic maker execution only**
4. **This does not prove complement arbitrage is mathematically impossible**
5. **This does not justify live trading**

---

## Files

- `FINAL_RESEARCH_STATUS.json` — machine-readable status
- `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` — precommitment document
- `examples/strategies/polymarket_complement_arb/` — strategy code
