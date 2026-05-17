# NOTE: Runner Patched — Previous Verdict Is Stale

**Date:** 2026-05-13

## What happened

The files in this directory (`cross_asset_summary.json`, `cross_asset_report.md`, `cross_asset_summary.csv`, etc.) were generated **before** a verdict bug was patched in the cross-asset impulse runner (`cross_asset_impulse.py`).

## The bug

The `PairResult.has_sufficient_range()` method used a **hardcoded 1 bps floor** to decide whether a pair had sufficient price movement for a meaningful test. This meant the runner treated all 50 overlapping pairs as having sufficient range even though actual BTC/ETH source movement during the capture was only about **9–17 bps**.

The correct source/target range thresholds should have come from CLI arguments:

- `--min-source-range-bps 30`
- `--min-target-range-bps 30`

With the bug in place, the runner saw "sufficient range" (because even 10 bps > 1 bps) and proceeded to evaluate pairs against the cost wall — returning a full **REJECTED** verdict.

## The fix

`has_sufficient_range()` is now a method accepting `min_source_range_bps` and `min_target_range_bps` as parameters. The verdict gate at line 556 of `cross_asset_impulse.py` checks if `pairs_with_sufficient == 0` and returns `NEEDS_MORE_DATA` when no pair meets the configured thresholds. Tests 21–25 verify this behavior, including the critical case where high event count cannot override quiet source movement.

## What this means for this capture

- Actual BTC/ETH source movement during capture: **~9–17 bps**
- Required source movement threshold: **30 bps**
- Required target movement threshold: **30 bps**
- Correct verdict: **MARKET_MODERATE_DIAGNOSTIC**
- Registry verdict: **MARKET_MODERATE_DIAGNOSTIC** (authoritative)
- Old verdict in these files: **REJECTED** (stale, do not trust)

## Summary

The REJECTED verdict in these old report files is **wrong**. The correct verdict for this quiet/moderate capture is **MARKET_MODERATE_DIAGNOSTIC**. The registry is the authoritative source. This capture is **diagnostic only**, not a structural rejection. Rerunning the evaluator with patched logic on this same data should produce a `NEEDS_MORE_DATA` / `MARKET_MODERATE_DIAGNOSTIC` verdict, not a full REJECTED.

**Do not delete this directory.** The raw data files (events, forward returns) are still valid. Only the verdict string was incorrect.
