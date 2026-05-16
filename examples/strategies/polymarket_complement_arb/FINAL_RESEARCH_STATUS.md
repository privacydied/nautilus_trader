# Polymarket Complement Arb — Final Research Status

**Status**: `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE`
**Applies to**: base 100-share pessimistic maker execution

**Hypothesis**: Complement arbitrage on Polymarket — buying the YES/UP token and the NO/DOWN token of the same condition as maker quotes, collecting the gross edge when the pair executes fully before timeout, net of fees, buffers, and unwind losses.

**This is not a rejection of the mathematical possibility of complement arbitrage.**
**This is not a candidate for live trading.**

---

## Campaign Timeline

The campaign progressed through three phases:

### 1. Original Closeout (6 windows, precommitted)

A narrow, repetition-based falsification campaign was conducted across 6 separated windows on crypto Up/Down duration markets (BTC and ETH, 5m/15m/4h/daily durations). Trade evidence READY in all windows. Zero pessimistic paired fills at base 100-share quote size. Result: FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE.

### 2. Hourly Addendum (1 window)

A 7th window resolved the 1h slug discovery gap. Confirmed the same zero-fill pattern on the hourly slug family `bitcoin-up-or-down-may-16-2026-2pm-et`. Trade evidence READY.

### 3. Size-Ladder Diagnostic Closeout (post-campaign)

A schema check (`size_ladder_schema_check.json`) confirmed that replay of fill decisions at arbitrary quote sizes from existing artifacts is impossible — the critical `cumulative_fillable_volume` field is not stored in `shadow_opportunities.jsonl`, and 100% of pessimistic rows were pre-rejected by size-invariant economic gates (NO_NET_EDGE_AFTER_COSTS or RESOLUTION_DANGER_WINDOW).

A fresh bounded diagnostic capture was conducted on the only remaining active market (`bitcoin-up-or-down-on-may-17-2026`) with ladder sizes [5, 10, 25, 50, 100]. All sizes produced zero pessimistic paired fills with all opportunities classified as dust (no quote size produced economic non-dust outcomes). The original campaign markets have expired and no new crypto Up/Down duration markets are currently active on Polymarket.

**Replay verdict: REPLAY_SCHEMA_INSUFFICIENT**
**Fresh diagnostic: ZERO_PESSIMISTIC_FILLS_AT_ALL_LADDER_SIZES**

---

## Consolidated Results

| Metric | Value |
|---|---|
| Campaign phases | 3 (original closeout, hourly addendum, size-ladder diagnostic) |
| Separated windows | 7 (original + addendum) |
| Ladder diagnostic windows | 1 |
| Slugs tested | 8 |
| Assets tested | BTC, ETH |
| Durations tested | 5m, 15m, 1h, 4h, daily |
| Trade evidence status | **READY** (7/7 windows + diagnostic) |
| Missing trade data events | 0 |
| Total detected opportunities | 1565 (+ 6 diagnostic) |
| Total non-dust opportunities | 1155 (+ 0 diagnostic) |
| **Pessimistic paired fills** | **0** (all sizes across all phases) |
| One-leg fills | 0 |
| Paired gain | 0 |
| Unwind loss | 0 |
| Net shadow harvest | 0 |
| Expected edge per detected opportunity | 0.0 |
| **Final status** | **FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE** |

### Per-Phase Breakdown

| Phase | Windows | Detected | Non-dust | Pess Fills | Evidence |
|---|---|---|---|---|---|
| Original closeout | 6 | 1532 | 1098 | 0 | READY |
| Hourly addendum | 1 | 24 | 57 | 0 | READY |
| Size-ladder diagnostic | 1 | 6 | 0 | 0 | READY |

### Size-Ladder Detail (Diagnostic)

| Quote Size | Opportunities | Non-Dust | Pess Paired | One-Leg | Dust Count |
|---|---|---|---|---|---|
| 5 | 6 | 0 | 0 | 0 | 6 |
| 10 | 6 | 0 | 0 | 0 | 6 |
| 25 | 6 | 0 | 0 | 0 | 6 |
| 50 | 6 | 0 | 0 | 0 | 6 |
| 100 | 6 | 0 | 0 | 0 | 6 |

All 6 opportunities were classified as dust (net edge below economic thresholds or insufficient notional). No quote size produced economic non-dust opportunities at the time of the diagnostic.

---

### Structural Pincer

The failure mode is a pincer:

- **Short durations (5m)**: Have turnover but no economic depth — zero non-dust opportunities
- **Medium durations (15m)**: Detect edges (27-35 opportunities) with non-dust sizes (24-93), but zero queue turnover — no pessimistic fill evidence
- **Hourly (1h)**: Edges present (24 detections, 57 non-dust) but same zero fill result as other durations
- **Long durations (4h/daily)**: Abundant theoretical edge (1473 detections, 978 non-dust) but same zero fill result
- **Size ladder (5-100 shares)**: No quote size produces pessimistic fills or economic non-dust opportunities

### Fee Model

- Formula: `fee = shares * feeRate * price * (1 - price)`
- Fee source: Gamma API feeSchedule.rate per condition
- Maker fee: 0 (standard Polymarket structure, conservative assumption)
- Taker-taker breakeven derived per condition, not hardcoded

---

## Schema Assessment Summary

**Verdict: REPLAY_SCHEMA_INSUFFICIENT**

The existing `shadow_opportunities.jsonl` artifacts do not store:
- `cumulative_fillable_volume` per leg (critical for recomputing `cumulative > depth_ahead + size` at arbitrary sizes)
- Raw trade events (time-series of price/size/side)

100% of pessimistic rows across the campaign were pre-rejected by size-invariant economic gates (sum_asks >= 1.0 or resolution danger). Replay at any size would produce identical outcomes.

See `size_ladder_schema_check.json` and `size_ladder_schema_check.md` for full diagnostic.

---

## Resolved Questions

* Can the Data API filter trades by market? **Yes, using `market=<conditionId>` (comma-separated)**
* Does the CLOB book endpoint work? **Yes**
* Are crypto Up/Down markets discoverable? **Yes, via event-slug resolution**
* Does the 1h hourly slug pattern exist? **Yes, e.g. `bitcoin-up-or-down-may-16-2026-2pm-et` — uses a different naming convention from 5m/15m/4h slugs**
* Is public trade evidence sufficient for pessimistic queue-fill evaluation? **Confirmed as operational — missing_trade_data_events = 0**
* Can the size ladder be replayed from existing artifacts? **No — REPLAY_SCHEMA_INSUFFICIENT**
* Does the freeze deepen at smaller quote sizes? **Yes — zero pessimistic fills across [5, 10, 25, 50, 100]**

---

## Remaining Unaddressed

1. ~~Size ladder (5/10/25/50 shares) was not implemented as a separate diagnostic run~~
2. ~~Resolution truncation~~ fields exist in code but runner does not fully populate them
3. **Frozen status applies to base 100-share pessimistic maker execution only**
4. **This does not prove complement arbitrage is mathematically impossible**
5. **This does not justify live trading**

---

## Files

| File | Purpose |
|---|---|
| `FINAL_RESEARCH_STATUS.json` | Machine-readable status with campaign phases |
| `FINAL_RESEARCH_STATUS.md` | This document |
| `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` | Precommitment document |
| `size_ladder_schema_check.json` | Schema replay capability check |
| `size_ladder_schema_check.md` | Schema check explanation |
| `run_size_ladder_diagnostic.py` | Diagnostic size-ladder runner |
| `config.py` | Strategy config with diagnostic thresholds |
| `examples/strategies/polymarket_complement_arb/` | Strategy code |
| `reports/polymarket_complement_arb_size_ladder_closeout/` | Diagnostic result directory |
