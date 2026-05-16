# Polymarket Complement Arb — Final Research Status

**Base 100-share status**: `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE`
**Size ladder status**: `SIZE_LADDER_INCONCLUSIVE`

**Hypothesis**: Complement arbitrage on Polymarket — buying the YES/UP token and the NO/DOWN token of the same condition as maker quotes, collecting the gross edge when the pair executes fully before timeout, net of fees, buffers, and unwind losses.

**This is not a rejection of the mathematical possibility of complement arbitrage.**
**This is not a candidate for live trading.**

---

## Campaign Phases

### Phase 1: Base Closeout (6 windows, precommitted)

A narrow, repetition-based falsification campaign was conducted across 6 separated windows on crypto Up/Down duration markets (BTC and ETH, 5m/15m/4h/daily durations). Trade evidence READY in all windows. Zero pessimistic paired fills at base 100-share quote size.

**Result**: `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE` — applies to base 100-share pessimistic maker execution.

The base 100-share pessimistic maker-execution hypothesis is frozen after repeated public-data windows with trade evidence READY, non-dust theoretical opportunities, and zero pessimistic paired fills. This is a queue-position/execution failure under current assumptions, not a mathematical rejection of complement arbitrage.

### Phase 2: Hourly Addendum (1 window, confirmatory)

A 7th window resolved the 1h slug discovery gap. Confirmed the same zero-fill pattern on the hourly slug family `bitcoin-up-or-down-may-16-2026-2pm-et`. Trade evidence READY. Confirmatory only — does not change the base verdict.

### Phase 3: Schema Check (replay assessment)

Inspected existing `shadow_opportunities.jsonl` for raw queue evidence at each ladder size. Found that `cumulative_fillable_volume` per leg is not stored — only the final fill status (no_fill/partial/full) is persisted. Without the cumulative volume, the threshold comparison `cumulative > depth_ahead + quote_size` cannot be recomputed at an arbitrary ladder size.

**Result**: `REPLAY_SCHEMA_INSUFFICIENT` — replay over original campaign is arithmetically impossible.

### Phase 4: Size-Ladder Stub (fallback fresh capture)

A 60-second fallback diagnostic capture was conducted on the only remaining active market (`bitcoin-up-or-down-on-may-17-2026`). The original crypto Up/Down duration markets had expired and no equivalent active duration universe was available.

- 6 opportunities detected
- All 6 classified as dust at every ladder size [5, 10, 25, 50, 100]
- 0 pessimistic paired fills
- Sample is below the campaign's own sufficiency gates

**Result**: `BELOW_SUFFICIENCY_GATES` — does not settle the size-ladder question.

---

## Status Summary

| Dimension | Status |
|---|---|
| Base 100-share freeze | `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE` |
| Size ladder | `SIZE_LADDER_INCONCLUSIVE` |
| Schema replay | `REPLAY_SCHEMA_INSUFFICIENT` |
| Fresh capture sufficiency | `BELOW_SUFFICIENCY_GATES` |
| Can trade live | No |
| Is mathematical rejection | No |

The size-ladder question remains formally open. Replay over the original campaign was blocked by missing raw cumulative fillable-volume fields, and fresh capture was blocked by market availability. The 60-second fallback run is recorded as a diagnostic stub, not as sufficient ladder evidence. The distinction between no fills at any size and edge only at dust size remains formally open.

### Per-Phase Breakdown

| Phase | Windows | Detected | Non-dust | Pess Fills | Evidence |
|---|---|---|---|---|---|
| Base closeout | 6 | 1532 | 1098 | 0 | READY |
| Hourly addendum | 1 | 24 | 57 | 0 | READY |
| Size-ladder stub | 1 | 6 | 0 | 0 | READY |

## Phases (do not merge)

| # | Phase | Type | Key Result |
|---|---|---|---|
| 1 | `base_closeout` | repeated-window campaign, 100 shares | `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE` |
| 2 | `hourly_addendum` | confirmatory addendum | same zero-fill pattern |
| 3 | `schema_check` | replay assessment | `REPLAY_SCHEMA_INSUFFICIENT` |
| 4 | `size_ladder_stub` | fallback fresh capture, insufficient | `BELOW_SUFFICIENCY_GATES` |

### Structural Pincer

The base failure mode is a pincer:

- **Short durations (5m)**: Have turnover but no economic depth — zero non-dust opportunities
- **Medium durations (15m)**: Detect edges (27-35 opportunities) with non-dust sizes (24-93), but zero queue turnover — no pessimistic fill evidence
- **Hourly (1h)**: Edges present (24 detections, 57 non-dust) but same zero fill result as other durations
- **Long durations (4h/daily)**: Abundant theoretical edge (1473 detections, 978 non-dust) but same zero fill result
- **Size ladder**: Not tested on original campaign markets — inconclusive

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

See `size_ladder_schema_check.json` and `size_ladder_schema_check.md` for full diagnostic.

The observer schema has been patched to store `cumulative_fillable_volume` (and related replay fields) in future runs — see Task 7 in the size-ladder closeout.

---

## Resolved Questions

* Can the Data API filter trades by market? **Yes, using `market=<conditionId>` (comma-separated)**
* Does the CLOB book endpoint work? **Yes**
* Are crypto Up/Down markets discoverable? **Yes, via event-slug resolution**
* Does the 1h hourly slug pattern exist? **Yes, e.g. `bitcoin-up-or-down-may-16-2026-2pm-et` — uses a different naming convention from 5m/15m/4h slugs**
* Is public trade evidence sufficient for pessimistic queue-fill evaluation? **Confirmed as operational — missing_trade_data_events = 0**
* Can the size ladder be replayed from existing artifacts? **No — REPLAY_SCHEMA_INSUFFICIENT**
* Does the size-ladder closeout settle the question? **No — SIZE_LADDER_INCONCLUSIVE**

---

## Remaining Unaddressed

1. The size-ladder question remains formally open — replay was blocked by schema, fresh capture was blocked by market availability
2. The distinction between no fills at any tested size and edge only at dust size remains open
3. **Frozen status applies to base 100-share pessimistic maker execution only**
4. **This does not prove complement arbitrage is mathematically impossible**
5. **This does not justify live trading**

---

## Files

| File | Purpose |
|---|---|
| `FINAL_RESEARCH_STATUS.json` | Machine-readable status with separate base/size-ladder fields |
| `FINAL_RESEARCH_STATUS.md` | This document |
| `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` | Precommitment document |
| `size_ladder_schema_check.json` | Schema replay capability check (REPLAY_SCHEMA_INSUFFICIENT) |
| `size_ladder_schema_check.md` | Schema check explanation |
| `run_size_ladder_diagnostic.py` | Diagnostic size-ladder runner |
| `config.py` | Strategy config with diagnostic thresholds |
| `shadow_execution.py` | Shadow fill/execution model (patched to store cumulative_fillable_volume) |
| `examples/strategies/polymarket_complement_arb/` | Strategy code |
| `reports/polymarket_complement_arb_size_ladder_closeout/` | Diagnostic result directory |
