# Family 1 Reversion — Zero-Stress Run Archive

## Status

`TICK_BASIS_REVERSION_NEEDS_MORE_DATA_NO_PROMOTABLE_WINDOWS`

## Verdict

**Not a falsification.** The seven-date 13:00-16:00 UTC calendar-spread Family 1 reversion run produced zero promotable stress windows. This means the committed fixed afternoon windows did not produce testable stress events under the existing stress-window rules. It does not mean the Family 1 reversion hypothesis is dead.

## What was tested

- **Family:** Family 1 same-venue USD/USDT quote-basis reversion only
- **Direction:** basis_change_bps < 0 → long, basis_change_bps > 0 → short
- **Data:** Kraken public trades (XXBTZUSD, XBTUSDT), 7 dates × 2 streams = 14 files
- **Windows:** Fixed 13:00-16:00 UTC windows
- **Dates:** Feb 3 (Mon), Mar 4 (Tue), Apr 2 (Wed), May 1 (Thu), Jun 6 (Fri), Jul 5 (Sat), Aug 3 (Sun)

## What was NOT reached

- No FDR was reached
- No holdout was reached
- No native null was reached
- No train evaluation was reached
- No comparison was reached
- No threshold tuning was performed
- No live/private/order/execution/bot path was added

## Momentum

Momentum framing (basis_change_bps < 0 → short, basis_change_bps > 0 → long) remains untested and requires a separate precommitment and FDR family-size decision.

## Next steps

A cached-data diagnostic (`docs/FAMILY1_REVERSION_ZERO_STRESS_DIAGNOSTIC.md`) was performed on the same data to determine why Phase 2A produced zero stress windows. The diagnostic found that:

1. XBTUSDT is not too sparse — 600s lookback coverage is ≥98% for ≥5 trades on every window
2. The USD/USDT basis does move (peak change ~30 bps over 30-120s), but the movement is modest
3. The stress rules measure single-stream price movement (100-150 bps thresholds over 10 min), not basis divergence
4. The `tick_only_burst_placeholder` is an intentional stub that never fires

Recommendation: Expanding to full-day windows is justified based on adequate data density, but should be separately precommitted. See the diagnostic document for details.
