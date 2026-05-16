# Polymarket BTC Up/Down CLOB Liquidity Probe v0

**Observer-only. Public data. No orders. No execution.**

## Status

| Field | Value |
|---|---|
| Name | `polymarket_btc_updown_clob_liquidity_probe_v0` |
| Category | Liquidity/actionability diagnostic |
| Data sources | Polymarket Gamma API, Polymarket CLOB public REST/WS |
| Settlement reference | Chainlink BTC/USD (optional in v0) |
| Verdict authority | None — this probe cannot produce CANDIDATE, REJECTED, CANDIDATE_FOR_LONGER_OBSERVATION, CANDIDATE_FOR_LIVE, EXECUTION_READY, or TRADE_READY |
| Allowed outputs | GREEN_LIQUIDITY_DIAGNOSTIC, YELLOW_LIQUIDITY_DIAGNOSTIC, RED_LIQUIDITY_DIAGNOSTIC, NEEDS_MORE_DATA, CAPTURE_UNUSABLE |

## Day-One Question

> Are near-expiry BTC Up/Down Polymarket CLOB books tight and deep enough to justify further Chainlink-lag research?

The spread-only liquidity probe cannot answer the lag/edge question. Its job is to determine whether the next question (lag/edge) is worth asking.

## What This Probe Does

1. Discovers active BTC Up/Down binary markets via the Polymarket Gamma API.
2. Captures CLOB orderbook snapshots for YES and NO tokens via public REST or WebSocket.
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

## Diagnostic Thresholds

Thresholds are fixed before capture. No peeking.

| Classification | Rule |
|---|---|
| **GREEN_LIQUIDITY_DIAGNOSTIC** | Median YES/NO executable spread <= $0.03 AND median top-of-book depth >= $100 |
| **YELLOW_LIQUIDITY_DIAGNOSTIC** | Median spread > $0.03 and <= $0.06. Only proceed later if future lag diagnostics show moves clearly larger than spread. |
| **RED_LIQUIDITY_DIAGNOSTIC** | Median spread > $0.06 OR p95 spread > $0.10 OR top-of-book depth is consistently below $100 |
| **NEEDS_MORE_DATA** | Insufficient samples collected to reach a diagnostic (e.g. < 10 valid snapshots) |
| **CAPTURE_UNUSABLE** | Capture produced zero actionable snapshots across all discovered markets |

**RED does not automatically update REJECTED_RESEARCH.md.** It means stop further model work unless a human explicitly reviews and overrides with written rationale.

## Lag Note

Lag (Chainlink BTC/USD vs CLOB price alignment) is not being tested in v0. If Chainlink reference ticks are collected, they are stored as `chainlink_reference_ticks.jsonl` for optional future timestamp-aligned analysis. No lag metric, no lead-lag model, no edge computation.

## Files

| File | Purpose |
|---|---|
| `POLYMARKET_BTC_UPDOWN_LIQUIDITY_PROBE_V0.md` | This document |
| `run_polymarket_btc_updown_liquidity_probe.py` | CLI entrypoint |
| `tests/test_polymarket_btc_updown_liquidity_probe.py` | Safety and functional tests |
