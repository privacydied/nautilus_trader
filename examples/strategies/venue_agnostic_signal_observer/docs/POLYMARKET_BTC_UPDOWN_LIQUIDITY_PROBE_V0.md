# Polymarket BTC Up/Down CLOB Liquidity Probe v0

**Observer-only. Public data. No orders. No execution.**

## Status

| Field | Value |
|---|---|
| Name | `polymarket_btc_updown_clob_liquidity_probe_v0` |
| Category | Liquidity/actionability diagnostic |
| Data sources | Polymarket Gamma API, Polymarket CLOB public REST |
| Settlement reference | Chainlink BTC/USD (optional in v0) |
| Verdict authority | None — this probe cannot produce CANDIDATE, REJECTED, CANDIDATE_FOR_LONGER_OBSERVATION, EXECUTION_READY, or TRADE_READY |
| Allowed outputs | GREEN_LIQUIDITY_DIAGNOSTIC, YELLOW_LIQUIDITY_DIAGNOSTIC, RED_LIQUIDITY_DIAGNOSTIC, NEEDS_MORE_DATA, CAPTURE_UNUSABLE |

## Day-One Question

> Are near-expiry BTC Up/Down Polymarket CLOB books tight and deep enough to justify further Chainlink-lag research?

v0 does not test whether a signal is profitable. It only answers: Is the book tight/deep enough to justify building the next diagnostic?

## What This Probe Does

1. Discovers active BTC Up/Down binary markets via the Polymarket Gamma API.
2. Captures CLOB orderbook snapshots for YES and NO tokens via public REST polling.
3. Computes descriptive spread/depth statistics against fixed pre-capture thresholds.
4. Classifies liquidity into GREEN / YELLOW / RED diagnostic buckets.

## What This Probe Does Not Do

- Fair-value model calculation.
- Trading strategy definition or execution.
- Signal evaluation or forward-return measurement.
- Wallet, signing, authentication, or order submission.
- Stage 2 precommitment / MCPT / permutation pipeline wiring.
- Any derivative of CANDIDATE, REJECTED, EXECUTION_READY, or TRADE_READY verdicts.
- Lag analysis beyond optional descriptive timestamp alignment.
- Any import from or modification of `../polymarket_btcusd_arb/`.

## Diagnostic Thresholds

Thresholds are fixed before capture. No peeking.

| Threshold | Token price units | Human meaning |
|---|---|---|
| `GREEN_MAX_SPREAD` | 0.03 | 3 cents |
| `YELLOW_MAX_SPREAD` | 0.06 | 6 cents |
| `RED_P95_SPREAD` | 0.10 | 10 cents |
| `MIN_NON_DUST_DEPTH_USD` | 100 | $100 top-of-book depth |

### Classification Rules

| Classification | Rule |
|---|---|
| **GREEN_LIQUIDITY_DIAGNOSTIC** | Median executable spread <= 0.03 token price units AND median top-of-book depth >= $100 when depth data is available |
| **YELLOW_LIQUIDITY_DIAGNOSTIC** | Median executable spread > 0.03 and <= 0.06 token price units. Only proceed later if future lag diagnostics show moves clearly larger than spread. |
| **RED_LIQUIDITY_DIAGNOSTIC** | Median executable spread > 0.06 token price units OR p95 executable spread > 0.10 token price units OR top-of-book depth is consistently dust (< $100 when depth data is available). Stop further model work unless a human explicitly reviews and overrides with written rationale. |
| **NEEDS_MORE_DATA** | Fewer than 5 valid orderbook samples |
| **CAPTURE_UNUSABLE** | Zero valid orderbook samples |

## Field Naming Convention

| Field | Meaning |
|---|---|
| `spread_price_units` | Bid-ask spread in token price units (e.g. 0.02) |
| `spread_cents` | Same spread in cents (e.g. 2.0 = 2 cents) |
| `depth_usd` | Top-of-book depth in USD |

## Lag Note

Lag (Chainlink BTC/USD vs CLOB price alignment) is not being tested in v0. If Chainlink reference ticks are collected, they are stored as `chainlink_reference_ticks.jsonl` for optional future timestamp-aligned analysis. No lag metric, no lead-lag model, no edge computation.

## Project Separation

This probe lives inside `examples/strategies/venue_agnostic_signal_observer/` as a standalone Nautilus diagnostic runner. The existing derivatives v2 / arb-bot work at `../polymarket_btcusd_arb/` remains separate. This probe does not import from it, write to it, modify it, depend on it, or assume its runtime layout.

## RED Does Not Modify REJECTED_RESEARCH.md

RED means stop further model work unless a human explicitly reviews and overrides with written rationale. It does not automatically update REJECTED_RESEARCH.md.

## Files

| File | Purpose |
|---|---|
| `POLYMARKET_BTC_UPDOWN_LIQUIDITY_PROBE_V0.md` | This document |
| `polymarket_btc_updown_liquidity_probe.py` | Core probe module |
| `run_polymarket_btc_updown_liquidity_probe.py` | CLI entrypoint |
| `tests/test_polymarket_btc_updown_liquidity_probe.py` | Safety and functional tests (61 tests) |
