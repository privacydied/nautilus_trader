# Family 2 Funding Crowding Reversal — Approved Bugfix Rerun

- **Branch:** feat/edge-miner-offline-discovery-runner
- **Starting SHA:** `54d3f5257292efb4270fbfe4f3773fbb53cd5220`
- **Run ID:** funding_crowding_reversal_20260518T180157_168230_8d622b
- **Rerun reason:** BUGFIX_RERUN_EVENTS_PRESERVED
- **Approved window:** 2020-06-29 00:00:00+00:00 → 2026-04-30 16:00:00+00:00
- **Seed:** 42
- **Primary cost:** 50.0 bps
- **Diagnostic cost:** 6.0 bps
- **Data source:** Binance Vision archive (cached; no re-fetch)

## Headline

- **Total cells evaluated:** 60

- **FDR_BLOCKED_DIAGNOSTIC:** 1
- **NEEDS_MORE_DATA:** 25
- **NULL_REJECTED_DIAGNOSTIC:** 23
- **REJECTED:** 11

### Cells earning CANDIDATE_FOR_LONGER_OBSERVATION
- None

## Full 60-Cell Verdict Table

| # | Cell ID | Valid | MeanNet | MedNet | WinRate | WorstD10 | BaseΔ | Null? | HoldSS? | FDR? | Verdict |
|---|---------|-------|---------|--------|---------|----------|-------|-------|---------|------|---------|
| 1 | BTC/abs_funding_ge_5bp/h4/positive_funding_extreme | 272 | -50.14 | -57.41 | 0.3419 | -259.20 | -40.93 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 2 | BTC/abs_funding_ge_5bp/h4/negative_funding_extreme | 5 | 234.18 | 230.59 | 0.6000 | -261.63 | 168.56 | ? | ? | ? | NEEDS_MORE_DATA |
| 3 | BTC/abs_funding_ge_5bp/h8/positive_funding_extreme | 272 | -57.11 | -40.51 | 0.4007 | -338.82 | -44.29 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 4 | BTC/abs_funding_ge_5bp/h8/negative_funding_extreme | 5 | 267.76 | 428.72 | 0.6000 | -218.40 | 143.59 | ? | ? | ? | NEEDS_MORE_DATA |
| 5 | BTC/abs_funding_ge_5bp/h12/positive_funding_extreme | 272 | -43.35 | -46.29 | 0.4228 | -404.78 | -26.11 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 6 | BTC/abs_funding_ge_5bp/h12/negative_funding_extreme | 5 | 295.12 | 248.16 | 0.8000 | -190.61 | 134.05 | ? | ? | ? | NEEDS_MORE_DATA |
| 7 | BTC/abs_funding_ge_5bp/h24/positive_funding_extreme | 272 | -66.62 | -42.49 | 0.4485 | -642.94 | -34.85 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 8 | BTC/abs_funding_ge_5bp/h24/negative_funding_extreme | 5 | 668.98 | 474.74 | 1.0000 | 339.19 | 479.20 | ? | ? | ? | NEEDS_MORE_DATA |
| 9 | BTC/abs_funding_ge_5bp/h48/positive_funding_extreme | 272 | -68.15 | -88.58 | 0.4118 | -803.87 | -14.47 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 10 | BTC/abs_funding_ge_5bp/h48/negative_funding_extreme | 5 | 398.96 | 420.54 | 0.6000 | -190.40 | 8.96 | ? | ? | ? | NEEDS_MORE_DATA |
| 11 | BTC/abs_funding_ge_10bp/h4/positive_funding_extreme | 81 | -28.87 | -60.07 | 0.3704 | -236.33 | -6.51 | ? | ? | ? | NEEDS_MORE_DATA |
| 12 | BTC/abs_funding_ge_10bp/h4/negative_funding_extreme | 2 | -15.52 | -15.52 | 0.5000 | -261.63 | -108.20 | ? | ? | ? | NEEDS_MORE_DATA |
| 13 | BTC/abs_funding_ge_10bp/h8/positive_funding_extreme | 81 | -42.85 | -18.22 | 0.4074 | -296.88 | -13.27 | ? | ? | ? | NEEDS_MORE_DATA |
| 14 | BTC/abs_funding_ge_10bp/h8/negative_funding_extreme | 2 | 449.77 | 449.77 | 1.0000 | 428.72 | 343.71 | ? | ? | ? | NEEDS_MORE_DATA |
| 15 | BTC/abs_funding_ge_10bp/h12/positive_funding_extreme | 81 | -27.18 | -23.28 | 0.4444 | -392.14 | 7.87 | ? | ? | ? | NEEDS_MORE_DATA |
| 16 | BTC/abs_funding_ge_10bp/h12/negative_funding_extreme | 2 | 309.06 | 309.06 | 1.0000 | 248.16 | 44.73 | ? | ? | ? | NEEDS_MORE_DATA |
| 17 | BTC/abs_funding_ge_10bp/h24/positive_funding_extreme | 81 | -40.36 | 24.44 | 0.5309 | -667.01 | 14.08 | ? | ? | ? | NEEDS_MORE_DATA |
| 18 | BTC/abs_funding_ge_10bp/h24/negative_funding_extreme | 2 | 671.62 | 671.62 | 1.0000 | 339.19 | 334.85 | ? | ? | ? | NEEDS_MORE_DATA |
| 19 | BTC/abs_funding_ge_10bp/h48/positive_funding_extreme | 81 | -80.22 | -105.16 | 0.4198 | -863.11 | 0.40 | ? | ? | ? | NEEDS_MORE_DATA |
| 20 | BTC/abs_funding_ge_10bp/h48/negative_funding_extreme | 2 | 331.20 | 331.20 | 0.5000 | -8.26 | -265.69 | ? | ? | ? | NEEDS_MORE_DATA |
| 21 | BTC/abs_funding_ge_25bp/h4/positive_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 22 | BTC/abs_funding_ge_25bp/h4/negative_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 23 | BTC/abs_funding_ge_25bp/h8/positive_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 24 | BTC/abs_funding_ge_25bp/h8/negative_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 25 | BTC/abs_funding_ge_25bp/h12/positive_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 26 | BTC/abs_funding_ge_25bp/h12/negative_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 27 | BTC/abs_funding_ge_25bp/h24/positive_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 28 | BTC/abs_funding_ge_25bp/h24/negative_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 29 | BTC/abs_funding_ge_25bp/h48/positive_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 30 | BTC/abs_funding_ge_25bp/h48/negative_funding_extreme | 0 | -- | -- | -- | -- | -- | ? | ? | ? | NEEDS_MORE_DATA |
| 31 | BTC/pct_funding_top_bottom_5pct/h4/positive_funding_extreme | 180 | -46.80 | -54.95 | 0.2389 | -182.36 | -36.57 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 32 | BTC/pct_funding_top_bottom_5pct/h4/negative_funding_extreme | 633 | -42.11 | -48.13 | 0.2844 | -166.39 | -47.02 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 33 | BTC/pct_funding_top_bottom_5pct/h8/positive_funding_extreme | 180 | -58.24 | -51.76 | 0.2667 | -264.10 | -46.93 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 34 | BTC/pct_funding_top_bottom_5pct/h8/negative_funding_extreme | 633 | -33.12 | -42.95 | 0.3412 | -225.62 | -36.36 | Y | ? | N | FDR_BLOCKED_DIAGNOSTIC |
| 35 | BTC/pct_funding_top_bottom_5pct/h12/positive_funding_extreme | 180 | -45.86 | -49.47 | 0.3167 | -328.49 | -36.04 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 36 | BTC/pct_funding_top_bottom_5pct/h12/negative_funding_extreme | 633 | -21.45 | -44.53 | 0.3665 | -254.64 | -20.60 | Y | ? | Y | REJECTED |
| 37 | BTC/pct_funding_top_bottom_5pct/h24/positive_funding_extreme | 180 | -85.04 | -62.43 | 0.3556 | -526.94 | -52.18 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 38 | BTC/pct_funding_top_bottom_5pct/h24/negative_funding_extreme | 633 | 4.42 | -26.72 | 0.4408 | -323.79 | -9.67 | Y | ? | Y | REJECTED |
| 39 | BTC/pct_funding_top_bottom_5pct/h48/positive_funding_extreme | 180 | -145.98 | -104.50 | 0.3111 | -786.68 | -94.55 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 40 | BTC/pct_funding_top_bottom_5pct/h48/negative_funding_extreme | 633 | 33.98 | -9.51 | 0.4866 | -399.35 | -4.42 | Y | ? | Y | REJECTED |
| 41 | BTC/pct_funding_top_bottom_2.5pct/h4/positive_funding_extreme | 126 | -46.89 | -52.88 | 0.1667 | -143.99 | -23.82 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 42 | BTC/pct_funding_top_bottom_2.5pct/h4/negative_funding_extreme | 739 | -44.67 | -49.60 | 0.2733 | -168.71 | -50.51 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 43 | BTC/pct_funding_top_bottom_2.5pct/h8/positive_funding_extreme | 126 | -62.31 | -48.65 | 0.2381 | -242.78 | -34.44 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 44 | BTC/pct_funding_top_bottom_2.5pct/h8/negative_funding_extreme | 739 | -30.74 | -42.53 | 0.3464 | -224.21 | -35.59 | Y | ? | Y | REJECTED |
| 45 | BTC/pct_funding_top_bottom_2.5pct/h12/positive_funding_extreme | 126 | -60.19 | -50.43 | 0.2540 | -328.49 | -31.82 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 46 | BTC/pct_funding_top_bottom_2.5pct/h12/negative_funding_extreme | 739 | -19.98 | -42.19 | 0.3775 | -253.40 | -19.83 | Y | ? | Y | REJECTED |
| 47 | BTC/pct_funding_top_bottom_2.5pct/h24/positive_funding_extreme | 126 | -81.31 | -62.43 | 0.3413 | -434.27 | -33.99 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 48 | BTC/pct_funding_top_bottom_2.5pct/h24/negative_funding_extreme | 739 | -1.09 | -28.50 | 0.4357 | -325.07 | -20.16 | Y | ? | Y | REJECTED |
| 49 | BTC/pct_funding_top_bottom_2.5pct/h48/positive_funding_extreme | 126 | -144.89 | -101.08 | 0.2937 | -803.87 | -52.31 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 50 | BTC/pct_funding_top_bottom_2.5pct/h48/negative_funding_extreme | 739 | 29.02 | -14.84 | 0.4831 | -407.75 | -9.39 | Y | ? | Y | REJECTED |
| 51 | BTC/pct_funding_top_bottom_1pct/h4/positive_funding_extreme | 109 | -44.45 | -50.95 | 0.1376 | -134.38 | -37.06 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 52 | BTC/pct_funding_top_bottom_1pct/h4/negative_funding_extreme | 810 | -44.45 | -49.21 | 0.2679 | -166.39 | -50.20 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 53 | BTC/pct_funding_top_bottom_1pct/h8/positive_funding_extreme | 109 | -61.98 | -51.72 | 0.2110 | -218.66 | -54.22 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 54 | BTC/pct_funding_top_bottom_1pct/h8/negative_funding_extreme | 810 | -32.04 | -42.59 | 0.3407 | -218.40 | -36.94 | Y | ? | Y | REJECTED |
| 55 | BTC/pct_funding_top_bottom_1pct/h12/positive_funding_extreme | 109 | -75.03 | -56.76 | 0.2110 | -328.80 | -61.74 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 56 | BTC/pct_funding_top_bottom_1pct/h12/negative_funding_extreme | 810 | -22.69 | -44.24 | 0.3691 | -254.64 | -23.73 | Y | ? | Y | REJECTED |
| 57 | BTC/pct_funding_top_bottom_1pct/h24/positive_funding_extreme | 109 | -98.47 | -73.17 | 0.3119 | -504.29 | -52.41 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 58 | BTC/pct_funding_top_bottom_1pct/h24/negative_funding_extreme | 810 | -0.63 | -30.25 | 0.4346 | -322.43 | -21.05 | Y | ? | Y | REJECTED |
| 59 | BTC/pct_funding_top_bottom_1pct/h48/positive_funding_extreme | 109 | -170.96 | -103.84 | 0.2569 | -863.11 | -87.66 | N | ? | ? | NULL_REJECTED_DIAGNOSTIC |
| 60 | BTC/pct_funding_top_bottom_1pct/h48/negative_funding_extreme | 810 | 34.60 | -8.36 | 0.4901 | -404.41 | -10.15 | Y | ? | Y | REJECTED |

## Diagnostic Sensitivity (6 bps)

Cells where optimistic_mean > 0 but gate verdict not CANDIDATE:
- BTC/abs_funding_ge_5bp/h4/negative_funding_extreme: opt_mean=278.18, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h8/negative_funding_extreme: opt_mean=311.76, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h12/positive_funding_extreme: opt_mean=0.65, actual_verdict=NULL_REJECTED_DIAGNOSTIC
- BTC/abs_funding_ge_5bp/h12/negative_funding_extreme: opt_mean=339.12, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h24/negative_funding_extreme: opt_mean=712.98, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_5bp/h48/negative_funding_extreme: opt_mean=442.96, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h4/positive_funding_extreme: opt_mean=15.13, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h4/negative_funding_extreme: opt_mean=28.48, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h8/positive_funding_extreme: opt_mean=1.15, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h8/negative_funding_extreme: opt_mean=493.77, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h12/positive_funding_extreme: opt_mean=16.82, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h12/negative_funding_extreme: opt_mean=353.06, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h24/positive_funding_extreme: opt_mean=3.64, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h24/negative_funding_extreme: opt_mean=715.62, actual_verdict=NEEDS_MORE_DATA
- BTC/abs_funding_ge_10bp/h48/negative_funding_extreme: opt_mean=375.20, actual_verdict=NEEDS_MORE_DATA
- BTC/pct_funding_top_bottom_5pct/h4/negative_funding_extreme: opt_mean=1.89, actual_verdict=NULL_REJECTED_DIAGNOSTIC
- BTC/pct_funding_top_bottom_5pct/h8/negative_funding_extreme: opt_mean=10.88, actual_verdict=FDR_BLOCKED_DIAGNOSTIC
- BTC/pct_funding_top_bottom_5pct/h12/negative_funding_extreme: opt_mean=22.55, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_5pct/h24/negative_funding_extreme: opt_mean=48.42, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_5pct/h48/negative_funding_extreme: opt_mean=77.98, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_2.5pct/h8/negative_funding_extreme: opt_mean=13.26, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_2.5pct/h12/negative_funding_extreme: opt_mean=24.02, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_2.5pct/h24/negative_funding_extreme: opt_mean=42.91, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_2.5pct/h48/negative_funding_extreme: opt_mean=73.02, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_1pct/h8/negative_funding_extreme: opt_mean=11.96, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_1pct/h12/negative_funding_extreme: opt_mean=21.31, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_1pct/h24/negative_funding_extreme: opt_mean=43.37, actual_verdict=REJECTED
- BTC/pct_funding_top_bottom_1pct/h48/negative_funding_extreme: opt_mean=78.60, actual_verdict=REJECTED

## Verifications
- **BY_FDR_AVAILABLE:** yes
- **TIMESTAMP_SHUFFLE_NULL_AVAILABLE:** yes
- **No re-fetch**
- **Run executed once:** seed=42, no re-window, no re-tune
- **No private key / order / execution path**