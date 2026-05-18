# Family 1 Reversion Zero-Stress Diagnostic

## Status

Cached-data diagnostic only. No new data fetch. No pipeline run.

## Inputs inspected

### Cached data files
- 14 CSV files under `data/kraken_trades/` (7 dates x 2 pairs):
  - `XXBTZUSD__family1_reversion_2025_*_*.csv` (BTC/USD)
  - `XBTUSDT__family1_reversion_2025_*_*.csv` (BTC/USDT)

### Source config
- `examples/strategies/venue_agnostic_signal_observer/offline_sources_multi_date.json`
- `scripts/fetch_kraken_trades.py`
- `scripts/build_kraken_replay_source_config.py`

### Precommitment
- `OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json`

### Code files
- `offline_stress_windows.py` — stress-window indexer including stress rule implementations
- `run_offline_stress_window_index.py` — default stress rule definitions
- `offline_historical_sources.py` — `parse_kraken_trades` parser

### Prior run artifacts
- `reports/venue_agnostic_signal_observer/offline_historical_prepare/offline_prepare_20260518_035158/offline_prepare_manifest.json`
- `reports/venue_agnostic_signal_observer/offline_stress_windows/offline_stress_window_index_20260518_035223/stress_window_manifest.json`

## Prior run outcome

Phase 2A returned `NO_STRESS_WINDOWS` with 0 windows, 0 promotable windows, 0 suppressed triggers. All three stress rules (`rolling_range_bps`, `rolling_absolute_return_bps`, `tick_only_burst_placeholder`) rejected every stream for every date.

Archive label: `TICK_BASIS_REVERSION_NEEDS_MORE_DATA_NO_PROMOTABLE_WINDOWS`
Archive file: `reports/FAMILY1_REVERSION_ZERO_STRESS_ARCHIVE.md`

## Q1 — XBTUSDT sparsity and stress-rule computability

### Evidence

For each window and pair, the diagnostic computed:
- Total trade count in the 3-hour window
- Inter-trade gap percentiles
- 600s lookback coverage (what fraction of trades have at least N other trades within 600s)

#### BTC/USD (XXBTZUSD) — all windows

| Date | Trades | Median gap | P90 gap | P99 gap | ≥5 trades in 600s |
|------|--------|------------|---------|---------|-------------------|
| Feb 3 Mon | 12,089 | 0.009s | 2.9s | 9.7s | 100% |
| Mar 4 Tue | 11,386 | 0.021s | 3.0s | 10.5s | 100% |
| Apr 2 Wed | 11,267 | 0.000s | 3.0s | 12.6s | 100% |
| May 1 Thu | 8,351 | 0.000s | 4.3s | 14.5s | 100% |
| Jun 6 Fri | 6,317 | 0.003s | 5.8s | 17.3s | 99.9% |
| Jul 5 Sat | 2,179 | 2.104s | 14.1s | 30.8s | 99.8% |
| Aug 3 Sun | 2,981 | 0.885s | 11.2s | 26.7s | 99.9% |

#### BTC/USDT (XBTUSDT) — all windows

| Date | Trades | Median gap | P90 gap | P99 gap | ≥5 trades in 600s |
|------|--------|------------|---------|---------|-------------------|
| Feb 3 Mon | 2,162 | 0.012s | 15.7s | 60.1s | 99.8% |
| Mar 4 Tue | 2,163 | 0.167s | 13.4s | 65.3s | 99.8% |
| Apr 2 Wed | 944 | 0.167s | 33.4s | 154.3s | 99.6% |
| May 1 Thu | 869 | 0.363s | 39.7s | 147.0s | 99.5% |
| Jun 6 Fri | 486 | 2.164s | 68.6s | 179.8s | 99.2% |
| Jul 5 Sat | 312 | 4.220s | 124.8s | 253.3s | 98.1% |
| Aug 3 Sun | 343 | 5.361s | 97.6s | 206.8s | 98.8% |

### Finding

**XBTUSDT is moderately sparse but adequate for the stress rules.** Even on the thinnest window (Jul 5 Sat: 312 trades in 3 hours), 98.1% of trade points have ≥5 other trades within a 600-second window. The stress rules require only `min_required_points=2` — this threshold is met by 99.7+% of points across all windows.

Sparsity is NOT the primary cause of the zero-stress result. The data density is sufficient for the configured stress rules to compute rolling return/range metrics.

Degenerate periods: The P99 gap for XBTUSDT reaches 253s (Jul 5 Sat) and the max gap hits 416s (May 1 Thu). These are isolated gaps of 4-7 minutes, but a 600-second lookback window easily bridges them. The stress rules would still find most of the window computable.

## Q2 — USD/USDT basis movement

### Evidence

Basis was computed for each window as:
```
basis_bps(t) = 10000 * (P_usdt(t) - P_usd(t)) / P_usd(t)
```
using first-tick-at-or-after matching.

| Date | Samples | Median bps | Range bps | P5/P95 bps | Max Δ 30s bps | Max Δ 60s bps | Max Δ 120s bps |
|------|---------|------------|-----------|-------------|----------------|----------------|-----------------|
| Feb 3 Mon | 2,162 | -10.71 | 55.37 | -20.2 / -1.6 | 27.10 | 25.95 | 30.02 |
| Mar 4 Tue | 2,163 | 7.28 | 32.45 | -1.1 / 14.5 | 24.30 | 18.78 | 24.15 |
| Apr 2 Wed | 944 | 1.16 | 18.88 | -4.3 / 6.2 | 11.34 | 12.82 | 11.33 |
| May 1 Thu | 869 | -3.43 | 18.63 | -9.7 / 4.5 | 15.90 | 12.87 | 14.20 |
| Jun 6 Fri | 486 | -6.55 | 17.91 | -11.8 / -2.0 | 9.99 | 12.84 | 12.31 |
| Jul 5 Sat | 312 | 0.20 | 12.68 | -4.6 / 2.1 | 5.74 | 5.74 | 6.15 |
| Aug 3 Sun | 343 | 3.30 | 15.73 | -2.6 / 8.9 | 8.94 | 8.94 | 9.90 |

### Finding

**The basis does move, but the movement is modest.** Key observations:

1. **Basis level drifts by window:** Median basis varies from -10.7 bps (USDT premium) to +7.3 bps (USD premium) depending on the date. This suggests regime-dependent basis levels but the samples per window are small.

2. **Basis range within a 3-hour window:** 12-55 bps. Largest window is Feb 3 (55 bps range), smallest is Jul 5 (12.7 bps). Weekday windows generally have wider ranges than weekend windows.

3. **Maximum basis change over Family 1 lookbacks:** Peak change over 30-120s is 5.7-30.0 bps depending on window. Weekdays: 10-30 bps. Weekends: 6-10 bps.

4. **Interpretation for Family 1 reversion:** The reversion signal is `basis_change_bps` over the lookback window. The largest observable basis changes (10-30 bps) are small but non-trivial. Whether this is enough to produce a positive signal-to-noise ratio after costs depends on the forward return distribution.

**The basis movement is NOT a direct candidate for why Phase 2A failed** because Phase 2A stress rules operate on individual stream prices, not on the basis. The basis is only evaluated later in the pipeline (Phases 2B+). The question of whether Family 1 reversion produces an edge is separate from whether the stress rules fire.

### Cost implication

For a Family 1 reversion trade on Kraken spot, the cost is roughly 0.16% (16 bps) per leg (maker-taker on two legs, or two market orders). With peak basis changes of 5-30 bps over the lookback window, the signal magnitude is often smaller than the round-trip cost. This means most reversion events would need to be large (tail events) to produce net positive forward returns.

## Q3 — Stress-rule implementation audit

### Evidence

Three stress rules are defined in `run_offline_stress_window_index.py` (lines 19-53) and evaluated in `offline_stress_windows.py` (lines 356-361):

#### Rule 1: `rolling_range_bps`
- **Lookback:** 600 seconds (10 minutes)
- **Threshold:** 150 bps (1.5%)
- **Trigger metric:** `(max_high - min_low) / min_low * 10000` over the lookback window
- **Resolution support:** trade, agg_trade, bar
- **Status:** Real implementation. Computes the range (high-low) within the lookback window.

#### Rule 2: `rolling_absolute_return_bps`
- **Lookback:** 600 seconds (10 minutes)
- **Threshold:** 100 bps (1.0%)
- **Trigger metric:** `abs(end_price - start_price) / start_price * 10000`
- **Resolution support:** trade, agg_trade, bar
- **Status:** Real implementation. Computes absolute return across the lookback window.

#### Rule 3: `tick_only_burst_placeholder`
- **Lookback:** 60 seconds
- **Threshold:** 1.0 bps
- **Trigger metric:** Always returns 0.0
- **Resolution support:** trade, agg_trade, bar
- **Status:** **Stub.** The trigger value is hardcoded to 0.0 in `offline_stress_windows.py` line 361. Since threshold_bps=1.0, the condition `trigger_value (0.0) < threshold_bps (1.0)` always fails, so this rule never emits stress windows.

### Finding

**The zero-window result was primarily caused by the real stress rules (rules 1 and 2) rejecting on these particular windows, not by the placeholder rule.**

Both `rolling_range_bps` and `rolling_absolute_return_bps` with their 10-minute lookbacks and 100-150 bps thresholds are well above the typical afternoon price movement in these windows. A spot Bitcoin price move of 100-150 bps (1.0-1.5%) in 10 minutes is a significant stress event — it would occur perhaps a few times per day during active periods. During fixed 13:00-16:00 UTC windows (which cover the end of the European afternoon but miss the London open and precede the NY open), these thresholds would rarely be triggered on calm days.

The `tick_only_burst_placeholder` stub contributed rejected entries to the manifest but did not cause the zero-window result — it was always a diagnostic-only rule that cannot fire by design.

**Important distinction:** The Phase 2A stress rules measure single-stream price movement. They are NOT measuring basis divergence. They select "stressful" windows where BTC/USD or BTC/USDT individually exhibited large price moves. The basis signal evaluation happens downstream. The zero-stress result means: "in these specific afternoon windows, BTC prices were calm enough that neither stream had a 100+ bps move in 10 minutes."

## Q4 — Full-day rerun recommendation

### Recommendation

`FULL_DAY_PRECOMMITMENT_JUSTIFIED`

### Rationale

1. **Data density is adequate.** Both streams have sufficient trade count for the stress rules to compute 10-minute lookback metrics (>98% coverage on the thinnest weekend windows).

2. **The 3-hour afternoon window is structurally calm.** The 100-150 bps stress thresholds are designed to fire during volatile periods. Fixed 13:00-16:00 UTC windows miss the London open, the NY open, and the daily rollover/volatility clustering periods.

3. **Full-day windows would expose the stress rules to the full daily volatility profile.** The same 100-150 bps thresholds over 10-minute lookbacks would fire during the daily volatility peaks that fall outside 13:00-16:00 UTC.

4. **The basis does move.** Peak basis changes of 5-30 bps over 30-120s are detectable. Whether they produce a net edge after costs is the empirical question — and that can only be answered if the pipeline reaches the evaluation phases.

**Caveat:** The basis movement is modest (5-30 bps peak changes). Even with full-day windows, the signal magnitude is small compared to typical execution costs. A full-day run may produce train survivors that fail on holdout due to cost. That would be a valid result either way — the point is to run the full pipeline to find out.

## Reproducibility boundary

Two helper scripts were committed as part of this diagnostic task:

- `scripts/fetch_kraken_trades.py` — Fetches Kraken public trades for all windows defined in the precommitment's `multi_date_windows` field. Reads the precommitment directly (no hardcoded dates).
- `scripts/build_kraken_replay_source_config.py` — Generates an `offline_sources.json` from the precommitment's `multi_date_windows` and `data_config.streams`. Output preserves labels, dates, and UTC window boundaries.

Both scripts are public-API-only (Kraken Trades endpoint, no auth). Tests at `scripts/test_reproducibility_helpers.py`.

**The source config is now fully derivable from the committed precommitment.** The pipeline runner reads dates from the source config (not from the precommitment's `multi_date_windows` directly), so the reproducibility chain is: precommitment `multi_date_windows` → `build_kraken_replay_source_config.py` → `offline_sources.json` → pipeline `--source-config`.

## Explicit non-actions

- No data was fetched (diagnostic used cached CSVs)
- No pipeline phase was run (Phase 1, 2A, 2B, or later)
- No threshold tuning or FDR tuning was performed
- No momentum framing was introduced
- No execution path was added
- No evaluator code was modified
- No precommitment full-day windows were added
